"""Task 5B.1 — chat file-attachment flow (multipart endpoint + 4th context source).

Integration tests over the real /v1/chat/completions path with the conftest
stubs (stub_llm, stub_pii, stub_analyze). They prove the user-sharpenings:

  * file text is a PROVIDER-ONLY 4th context source (history + RAG + FILE + new),
  * file PII feeds the sovereign routing_text (file-only PII -> EU reroute),
  * strict coreference: same value -> one consistent placeholder across file+question,
  * re-injection EVERY turn via load_attachments (turn 2 with no upload still injects),
  * provider-only: the file text is NEVER a messages row; it lives in chat_attachments,
  * audit: the transited file text is in audit_events.request_text,
  * the extract cap is enforced BEFORE conversation-create/insert and BEFORE the LLM,
  * the JSON (no-file) path is byte-identical to before.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import text

from app.config import get_settings
from app.main import app
from tests._helpers import seed_org_with_owner
from tests.test_pii_routing import (
    CHOSEN,
    SOVEREIGN,
    _auth,
    _seed_model,
    _seed_sovereign,
)

USER = "11111111-1111-1111-1111-111111111111"
IBAN = "DE89370400440532013000"


@pytest.fixture
def settings_override():
    """Override get_settings for the chat path (e.g. shrink max_extracted_chars)."""
    base = get_settings()

    def _apply(**overrides):
        app.dependency_overrides[get_settings] = lambda: base.model_copy(update=overrides)

    yield _apply
    app.dependency_overrides.pop(get_settings, None)


def _payload(model, content="hi", **extra):
    return {"model": model, "messages": [{"role": "user", "content": content}], **extra}


def _multipart(model, file_bytes, *, filename="doc.txt", content_type="text/plain", **extra):
    """httpx multipart: JSON body as the `payload` form field + a `file` upload."""
    return {
        "files": {"file": (filename, file_bytes, content_type)},
        "data": {"payload": json.dumps(_payload(model, **extra))},
    }


def _provider_messages(stub_llm):
    return stub_llm.calls[-1]["messages"]


def _provider_blob(stub_llm):
    return "\n".join(m["content"] for m in _provider_messages(stub_llm))


# --- 6. multipart happy path (DoD) -------------------------------------


@pytest.mark.asyncio
async def test_multipart_happy_path_file_in_context(
    client, db, make_token, stub_pii, stub_llm, settings_override
):
    settings_override()
    _org, ws = seed_org_with_owner(db, USER, "a@firma.de")
    _seed_model(db, CHOSEN)
    _seed_sovereign(db)
    token = make_token(sub=USER, email="a@firma.de")

    r = await client.post(
        "/v1/chat/completions",
        headers=_auth(token),
        **_multipart(CHOSEN, b"Quartalsbericht Q3 Umsatz 5 Mio.", content="Fasse zusammen"),
    )
    assert r.status_code == 200, r.text
    # file text reached the provider as a system (provider-only) message
    sys = [m for m in _provider_messages(stub_llm) if m["role"] == "system"]
    assert sys and "Quartalsbericht Q3" in sys[0]["content"]
    assert "[Datei: doc.txt]" in sys[0]["content"]
    # a conversation + a chat_attachments row were created
    cid = r.json()["decyra"].get("conversation_id") or r.json().get("conversation_id")
    assert cid
    att = db.execute(
        text("SELECT filename, extracted_text FROM chat_attachments "
             "WHERE conversation_id = :c"),
        {"c": cid},
    ).one()
    assert att.filename == "doc.txt"
    assert "Quartalsbericht Q3" in att.extracted_text


# --- 1. file PII -> sovereign reroute ----------------------------------


@pytest.mark.asyncio
async def test_file_pii_forces_eu_reroute_sovereign(
    client, db, make_token, stub_pii, stub_llm, settings_override
):
    settings_override()
    _org, ws = seed_org_with_owner(db, USER, "a@firma.de")
    _seed_model(db, CHOSEN)  # US/cloud, not sovereign-eligible
    _seed_sovereign(db)
    stub_pii.state["needle"] = "__FILEPII__"  # detected iff the token is in scanned text
    token = make_token(sub=USER, email="a@firma.de")

    # WITHOUT file: clean user text -> no reroute, US model used.
    r1 = await client.post(
        "/v1/chat/completions",
        headers=_auth(token),
        json=_payload(CHOSEN, content="Fasse zusammen"),
    )
    assert r1.json()["decyra"]["effective_model"] == CHOSEN

    # WITH file containing PII (user text benign) -> file_text in routing_text -> reroute.
    r2 = await client.post(
        "/v1/chat/completions",
        headers=_auth(token),
        **_multipart(CHOSEN, b"Kunde __FILEPII__ im Vertrag.", content="Fasse zusammen"),
    )
    assert r2.status_code == 200, r2.text
    assert r2.json()["decyra"]["effective_model"] == SOVEREIGN
    assert r2.json()["decyra"]["routed_to"] == "mistral"


# --- 2. strict coreference: one placeholder across file + question -----


@pytest.mark.asyncio
async def test_strict_coreference_file_and_question_same_placeholder(
    client, db, make_token, stub_pii, stub_analyze, stub_llm, settings_override
):
    settings_override()
    _org, ws = seed_org_with_owner(db, USER, "a@firma.de")
    db.execute(
        text("UPDATE workspaces SET settings='{\"pii_mode\":\"strict\"}'::jsonb WHERE id=:w"),
        {"w": ws},
    )
    _seed_model(db, CHOSEN)
    _seed_sovereign(db)
    stub_pii.state["force"] = "detected"          # strict enters the anonymise branch
    stub_analyze.state["spans_for"] = {IBAN: "IBAN"}
    token = make_token(sub=USER, email="a@firma.de")

    r = await client.post(
        "/v1/chat/completions",
        headers=_auth(token),
        **_multipart(
            CHOSEN, f"Überweisung an {IBAN} ausgeführt.".encode(),
            content=f"Stimmt die {IBAN}?",
        ),
    )
    assert r.status_code == 200, r.text
    msgs = _provider_messages(stub_llm)
    blob = "\n".join(m["content"] for m in msgs)
    assert IBAN not in blob                        # raw IBAN never left the house
    sys = next(m for m in msgs if m["role"] == "system")
    usr = next(m for m in msgs if m["role"] == "user")
    assert "[[DCY_IBAN_0]]" in sys["content"]       # same placeholder in BOTH sources
    assert "[[DCY_IBAN_0]]" in usr["content"]


# --- 3. re-injection on turn 2 (no new upload) -------------------------


@pytest.mark.asyncio
async def test_reinjection_turn2_no_new_file_sovereign(
    client, db, make_token, stub_pii, stub_llm, settings_override
):
    settings_override()
    _org, ws = seed_org_with_owner(db, USER, "a@firma.de")
    _seed_model(db, CHOSEN)
    _seed_sovereign(db)
    stub_pii.state["needle"] = "__FILEPII__"
    token = make_token(sub=USER, email="a@firma.de")

    # Turn 1: attach a PII file -> reroute, capture the conversation id.
    r1 = await client.post(
        "/v1/chat/completions",
        headers=_auth(token),
        **_multipart(CHOSEN, b"Kunde __FILEPII__ im Vertrag.", content="Hallo"),
    )
    assert r1.status_code == 200, r1.text
    assert r1.json()["decyra"]["effective_model"] == SOVEREIGN
    cid = r1.json().get("conversation_id")
    assert cid

    # Turn 2: SAME conversation, NO file, benign question -> file STILL re-injected,
    # PII protection STILL fires (reroute persists).
    r2 = await client.post(
        "/v1/chat/completions",
        headers=_auth(token),
        json=_payload(CHOSEN, content="Und nun?", conversation_id=cid),
    )
    assert r2.status_code == 200, r2.text
    assert "__FILEPII__" in _provider_blob(stub_llm)        # re-injected from storage
    assert r2.json()["decyra"]["effective_model"] == SOVEREIGN  # protection still fires


# --- 4. provider-only: not a messages row, lives in chat_attachments ---


@pytest.mark.asyncio
async def test_file_text_provider_only_not_persisted_as_message(
    client, db, make_token, stub_pii, stub_llm, settings_override
):
    settings_override()
    _org, ws = seed_org_with_owner(db, USER, "a@firma.de")
    _seed_model(db, CHOSEN)
    _seed_sovereign(db)
    token = make_token(sub=USER, email="a@firma.de")

    r = await client.post(
        "/v1/chat/completions",
        headers=_auth(token),
        **_multipart(CHOSEN, b"FILEONLY-MARKER Inhalt.", filename="vertrag.txt",
                     content="frage"),
    )
    assert r.status_code == 200, r.text
    assert "FILEONLY-MARKER" in _provider_blob(stub_llm)  # provider saw it
    rows = db.execute(text("SELECT role, content FROM messages")).all()
    assert all("FILEONLY-MARKER" not in (row.content or "") for row in rows)
    assert all(row.role != "system" for row in rows)
    cid = r.json()["conversation_id"]
    att = db.execute(
        text("SELECT filename, extracted_text FROM chat_attachments WHERE conversation_id=:c"),
        {"c": cid},
    ).one()
    assert att.filename == "vertrag.txt"
    assert "FILEONLY-MARKER" in att.extracted_text


# --- 5. audit: transited file text is in request_text ------------------


@pytest.mark.asyncio
async def test_audit_contains_transited_file_text_sovereign(
    client, db, make_token, stub_pii, stub_llm, settings_override
):
    settings_override()
    _org, ws = seed_org_with_owner(db, USER, "a@firma.de")
    _seed_model(db, CHOSEN)
    _seed_sovereign(db)
    token = make_token(sub=USER, email="a@firma.de")

    await client.post(
        "/v1/chat/completions",
        headers=_auth(token),
        **_multipart(CHOSEN, b"AUDIT-FILE-MARKER Vertragstext.", content="frage"),
    )
    audit_req = db.execute(
        text("SELECT request_text FROM audit_events ORDER BY timestamp DESC LIMIT 1")
    ).scalar_one()
    assert "AUDIT-FILE-MARKER" in audit_req  # cleartext in sovereign


@pytest.mark.asyncio
async def test_audit_contains_anonymised_file_text_strict(
    client, db, make_token, stub_pii, stub_analyze, stub_llm, settings_override
):
    settings_override()
    _org, ws = seed_org_with_owner(db, USER, "a@firma.de")
    db.execute(
        text("UPDATE workspaces SET settings='{\"pii_mode\":\"strict\"}'::jsonb WHERE id=:w"),
        {"w": ws},
    )
    _seed_model(db, CHOSEN)
    _seed_sovereign(db)
    stub_pii.state["force"] = "detected"
    stub_analyze.state["spans_for"] = {IBAN: "IBAN"}
    token = make_token(sub=USER, email="a@firma.de")

    await client.post(
        "/v1/chat/completions",
        headers=_auth(token),
        **_multipart(CHOSEN, f"Datei mit {IBAN}.".encode(), content="frage"),
    )
    audit_req = db.execute(
        text("SELECT request_text FROM audit_events ORDER BY timestamp DESC LIMIT 1")
    ).scalar_one()
    assert IBAN not in audit_req                  # raw PII never in the chain
    assert "[[DCY_IBAN_0]]" in audit_req          # anonymised file text WAS audited


# --- 7. extract cap enforced BEFORE conversation-create / insert / LLM --


@pytest.mark.asyncio
async def test_oversize_extract_rejected_before_any_write(
    client, db, make_token, stub_pii, stub_llm, settings_override
):
    settings_override(max_extracted_chars=5)
    _org, ws = seed_org_with_owner(db, USER, "a@firma.de")
    _seed_model(db, CHOSEN)
    _seed_sovereign(db)
    token = make_token(sub=USER, email="a@firma.de")

    r = await client.post(
        "/v1/chat/completions",
        headers=_auth(token),
        **_multipart(CHOSEN, b"This extract is way over the tiny cap.", content="frage"),
    )
    assert r.status_code == 413, r.text
    # no conversation, no attachment row, no LLM call
    assert db.execute(text("SELECT count(*) FROM conversations")).scalar_one() == 0
    assert db.execute(text("SELECT count(*) FROM chat_attachments")).scalar_one() == 0
    assert stub_llm.calls == []


# --- 8. JSON path unchanged --------------------------------------------


@pytest.mark.asyncio
async def test_json_path_unchanged_no_file(
    client, db, make_token, stub_pii, stub_llm, settings_override
):
    settings_override()
    _org, ws = seed_org_with_owner(db, USER, "a@firma.de")
    _seed_model(db, CHOSEN)
    _seed_sovereign(db)
    token = make_token(sub=USER, email="a@firma.de")

    r = await client.post(
        "/v1/chat/completions",
        headers=_auth(token),
        json=_payload(CHOSEN, content="Hallo"),
    )
    assert r.status_code == 200, r.text
    # no file context system message injected
    assert all(m["role"] != "system" for m in _provider_messages(stub_llm))
    assert db.execute(text("SELECT count(*) FROM chat_attachments")).scalar_one() == 0


# --- streaming multipart case ------------------------------------------


@pytest.mark.asyncio
async def test_multipart_streaming_file_in_context(
    client, db, make_token, stub_pii, stub_llm, settings_override
):
    settings_override()
    _org, ws = seed_org_with_owner(db, USER, "a@firma.de")
    _seed_model(db, CHOSEN)
    _seed_sovereign(db)
    token = make_token(sub=USER, email="a@firma.de")

    r = await client.post(
        "/v1/chat/completions",
        headers=_auth(token),
        **_multipart(CHOSEN, b"STREAM-FILE-MARKER Inhalt.", content="frage", stream=True),
    )
    assert r.status_code == 200, r.text
    assert "STREAM-FILE-MARKER" in _provider_blob(stub_llm)
