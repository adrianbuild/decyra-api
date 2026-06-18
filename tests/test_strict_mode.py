"""Task 4.5b — strict mode end-to-end: anonymise PII before the cloud call,
de-anonymise the answer, track the mode in the (v2) hash chain.

PII is stubbed at two seams: `stub_pii` (detection boolean) and `stub_analyze`
(the spans the anonymiser masks). `stub_llm.calls` records exactly what reached
the provider — the DoD guard that ONLY placeholders left the house.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import text

from app.audit import verify_workspace_chain
from tests._helpers import seed_org_with_owner

USER_A = "11111111-1111-1111-1111-111111111111"
CHOSEN = "test-model"  # non-sovereign (provider 'openai')
SOVEREIGN = "mistral/mistral-large-latest"
PII = "Max Mustermann"


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _seed_model(db, name, *, provider="openai", sovereign=False) -> None:
    db.execute(
        text(
            "INSERT INTO models (name, provider, cost_input, cost_output, "
            "eu_hosted, sovereign_eligible, tier_min, enabled) "
            "VALUES (:n, :p, 5.0, 30.0, :eu, :sov, 'free', true)"
        ),
        {"n": name, "p": provider, "eu": sovereign, "sov": sovereign},
    )


def _seed_sovereign(db) -> None:
    _seed_model(db, SOVEREIGN, provider="mistral", sovereign=True)


def _body(model, content="hi", **extra):
    return {
        "model": model,
        "messages": [{"role": "user", "content": content}],
        **extra,
    }


def _provider_input(stub_llm) -> str:
    """All content that reached the (stubbed) provider on the last call."""
    msgs = stub_llm.calls[-1]["messages"]
    return "\n".join(m["content"] for m in msgs)


def _events(body: str) -> list[str]:
    return [
        b.strip()[len("data: ") :]
        for b in body.split("\n\n")
        if b.strip().startswith("data: ")
    ]


def _setup_strict_pii(db, stub_pii, stub_analyze) -> str:
    _org, ws = seed_org_with_owner(db, USER_A, "a@firma.de")
    _seed_model(db, CHOSEN)
    _seed_sovereign(db)
    stub_pii.state["force"] = "detected"
    stub_analyze.state["spans_for"] = {PII: "PERSON"}
    return ws


# --- Non-streaming -----------------------------------------------------


@pytest.mark.asyncio
async def test_strict_only_placeholders_reach_provider_nonstream(
    client, db, make_token, stub_pii, stub_analyze, stub_llm
) -> None:
    ws = _setup_strict_pii(db, stub_pii, stub_analyze)
    token = make_token(sub=USER_A, email="a@firma.de")

    r = await client.post(
        "/v1/chat/completions", headers=_auth(token),
        json=_body(CHOSEN, content=f"Bitte hilf {PII} weiter", pii_mode="strict"),
    )
    assert r.status_code == 200

    # 1) The provider saw ONLY the placeholder, never the real PII.
    sent = _provider_input(stub_llm)
    assert PII not in sent
    assert "[[DCY_PERSON_0]]" in sent

    # 2) Status: anonymised, PII detected, NO reroute (chosen model kept).
    d = r.json()["decyra"]
    assert d["anonymized"] is True
    assert d["pii_detected"] is True  # refinement 1: detected, just handled differently
    assert d["pii_mode"] == "strict"
    assert d["effective_model"] == CHOSEN and d["routed_to"] == "openai"

    # 3) messages keep the REAL text (tenant data, deletable).
    user_msg = db.execute(
        text("SELECT content FROM messages WHERE role='user' "
             "ORDER BY created_at DESC LIMIT 1")
    ).scalar_one()
    assert PII in user_msg

    # 4) Audit stores the ANONYMISED request (what really went to the cloud),
    #    with pii_mode='strict', anonymized=true, pii_detected=true.
    ev = db.execute(
        text("SELECT request_text, pii_mode, anonymized, pii_detected "
             "FROM audit_events ORDER BY timestamp DESC LIMIT 1")
    ).one()
    assert PII not in ev.request_text
    assert "[[DCY_PERSON_0]]" in ev.request_text
    assert ev.pii_mode == "strict" and ev.anonymized is True
    assert ev.pii_detected is True

    # 5) Chain still valid (v2).
    assert verify_workspace_chain(db, ws).valid is True


@pytest.mark.asyncio
async def test_strict_response_is_deanonymized_nonstream(
    client, db, make_token, stub_pii, stub_analyze, stub_llm
) -> None:
    _setup_strict_pii(db, stub_pii, stub_analyze)
    # The model echoes the placeholder; the client must see the real value.
    stub_llm.state["content"] = "Klar, ich helfe [[DCY_PERSON_0]] gern."
    token = make_token(sub=USER_A, email="a@firma.de")

    r = await client.post(
        "/v1/chat/completions", headers=_auth(token),
        json=_body(CHOSEN, content=f"Hilf {PII}", pii_mode="strict"),
    )
    answer = r.json()["choices"][0]["message"]["content"]
    assert answer == f"Klar, ich helfe {PII} gern."
    assert "[[DCY_" not in answer

    # The stored assistant message is the real (de-anonymised) one...
    stored = db.execute(
        text("SELECT content FROM messages WHERE role='assistant' "
             "ORDER BY created_at DESC LIMIT 1")
    ).scalar_one()
    assert stored == f"Klar, ich helfe {PII} gern."
    # ...but the audit response is the anonymised text that actually transited.
    resp_text = db.execute(
        text("SELECT response_text FROM audit_events ORDER BY timestamp DESC LIMIT 1")
    ).scalar_one()
    assert resp_text == "Klar, ich helfe [[DCY_PERSON_0]] gern."


@pytest.mark.asyncio
async def test_strict_ratchet_anonymizes_history_too(
    client, db, make_token, stub_pii, stub_analyze, stub_llm
) -> None:
    """Refinement 2: the FULL provider input (history + new) is anonymised, so
    PII sitting only in stored history cannot leak on a later clean turn."""
    ws = _setup_strict_pii(db, stub_pii, stub_analyze)
    cid = db.execute(
        text("INSERT INTO conversations (workspace_id, user_id, title, pii_mode) "
             "VALUES (:w, :u, 't', 'strict') RETURNING id"),
        {"w": ws, "u": USER_A},
    ).scalar_one()
    db.execute(
        text("INSERT INTO messages (conversation_id, workspace_id, role, content) "
             f"VALUES (:c, :w, 'user', 'Frühere Nachricht über {PII}')"),
        {"c": cid, "w": ws},
    )
    token = make_token(sub=USER_A, email="a@firma.de")

    r = await client.post(
        "/v1/chat/completions", headers=_auth(token),
        json=_body(CHOSEN, content="harmlose Folgefrage",
                   conversation_id=str(cid), pii_mode="strict"),
    )
    assert r.status_code == 200
    sent = _provider_input(stub_llm)
    assert PII not in sent  # the history PII is masked too
    assert "[[DCY_PERSON_0]]" in sent


@pytest.mark.asyncio
async def test_strict_anonymizes_assistant_pii_no_leak(
    client, db, make_token, stub_pii, stub_analyze, stub_llm
) -> None:
    """Guard: strict ANONYMISATION covers the FULL input (incl. assistant
    history), even though the sovereign reroute decision is user-text-only. PII
    sitting only in a prior assistant answer must NOT leak un-masked to the
    cloud. Detection is needle-based (not force), so this also proves strict
    scans the full input: the needle sits only in the assistant message; a
    user-text-only scan would miss it and the PII would leak (test fails)."""
    _org, ws = seed_org_with_owner(db, USER_A, "a@firma.de")
    _seed_model(db, CHOSEN)
    _seed_sovereign(db)
    stub_pii.state["needle"] = PII
    stub_analyze.state["spans_for"] = {PII: "PERSON"}
    cid = db.execute(
        text("INSERT INTO conversations (workspace_id, user_id, title, pii_mode) "
             "VALUES (:w, :u, 't', 'strict') RETURNING id"),
        {"w": ws, "u": USER_A},
    ).scalar_one()
    db.execute(
        text("INSERT INTO messages (conversation_id, workspace_id, role, content) "
             f"VALUES (:c, :w, 'assistant', 'Aus dem Bericht: {PII} ist Kunde.')"),
        {"c": cid, "w": ws},
    )
    token = make_token(sub=USER_A, email="a@firma.de")

    r = await client.post(
        "/v1/chat/completions", headers=_auth(token),
        json=_body(CHOSEN, content="harmlose Folgefrage",
                   conversation_id=str(cid), pii_mode="strict"),
    )
    assert r.status_code == 200
    d = r.json()["decyra"]
    assert d["anonymized"] is True
    assert d["effective_model"] == CHOSEN  # strict masks in place, no reroute
    sent = _provider_input(stub_llm)
    assert PII not in sent  # assistant-originated PII masked -> no leak
    assert "[[DCY_PERSON_0]]" in sent


@pytest.mark.asyncio
async def test_strict_without_pii_sends_original_no_anonymize(
    client, db, make_token, stub_pii, stub_llm
) -> None:
    seed_org_with_owner(db, USER_A, "a@firma.de")
    _seed_model(db, CHOSEN)
    _seed_sovereign(db)  # default stub_pii = clean; no spans
    token = make_token(sub=USER_A, email="a@firma.de")

    r = await client.post(
        "/v1/chat/completions", headers=_auth(token),
        json=_body(CHOSEN, content="Wie wird das Wetter?", pii_mode="strict"),
    )
    d = r.json()["decyra"]
    assert d["anonymized"] is False
    assert d["pii_detected"] is False
    assert d["effective_model"] == CHOSEN  # no reroute
    assert "Wie wird das Wetter?" in _provider_input(stub_llm)


@pytest.mark.asyncio
async def test_strict_presidio_down_failsafe_reroutes_sovereign(
    client, db, make_token, stub_pii, stub_analyze, stub_llm
) -> None:
    """Strict + PII but the analyzer is unavailable: we cannot guarantee a clean
    anonymisation, so we fail-safe to a sovereign reroute (never send real PII)."""
    _setup_strict_pii(db, stub_pii, stub_analyze)
    stub_analyze.state["raise"] = True  # Presidio down during anonymisation
    token = make_token(sub=USER_A, email="a@firma.de")

    r = await client.post(
        "/v1/chat/completions", headers=_auth(token),
        json=_body(CHOSEN, content=f"Hilf {PII}", pii_mode="strict"),
    )
    d = r.json()["decyra"]
    assert d["anonymized"] is False
    assert d["effective_model"] == SOVEREIGN and d["routed_to"] == "mistral"


# --- Streaming ---------------------------------------------------------


@pytest.mark.asyncio
async def test_strict_stream_no_partial_placeholder_and_deanonymized(
    client, db, make_token, stub_pii, stub_analyze, stub_llm
) -> None:
    _setup_strict_pii(db, stub_pii, stub_analyze)
    # Split the placeholder across a chunk boundary on the wire.
    stub_llm.state["chunks"] = ["Hallo [[DCY_", "PERSON_0]] tschüss"]
    token = make_token(sub=USER_A, email="a@firma.de")

    r = await client.post(
        "/v1/chat/completions", headers=_auth(token),
        json=_body(CHOSEN, content=f"Hilf {PII}", stream=True, pii_mode="strict"),
    )
    assert r.status_code == 200
    events = [json.loads(e) for e in _events(r.text) if e != "[DONE]"]

    # First event carries the status: anonymised + detected.
    assert events[0]["decyra"]["anonymized"] is True
    assert events[0]["decyra"]["pii_detected"] is True

    # No streamed delta may contain a partial placeholder fragment.
    deltas = [
        e["choices"][0]["delta"].get("content", "")
        for e in events
        if e.get("choices")
    ]
    for piece in deltas:
        assert "[[DCY" not in piece

    # The assembled visible answer is fully de-anonymised.
    assembled = "".join(deltas)
    assert assembled == f"Hallo {PII} tschüss"


# --- Mode resolution ---------------------------------------------------


@pytest.mark.asyncio
async def test_request_mode_persists_onto_new_conversation(
    client, db, make_token, stub_pii, stub_analyze, stub_llm
) -> None:
    _setup_strict_pii(db, stub_pii, stub_analyze)
    token = make_token(sub=USER_A, email="a@firma.de")

    r = await client.post(
        "/v1/chat/completions", headers=_auth(token),
        json=_body(CHOSEN, content=f"Hilf {PII}", pii_mode="strict"),
    )
    cid = r.json()["conversation_id"]
    stored = db.execute(
        text("SELECT pii_mode FROM conversations WHERE id = :c"), {"c": cid}
    ).scalar_one()
    assert stored == "strict"


@pytest.mark.asyncio
async def test_conversation_mode_overrides_workspace_default(
    client, db, make_token, stub_pii, stub_analyze, stub_llm
) -> None:
    """Workspace default is sovereign; the conversation is strict; a request
    that omits pii_mode must be governed by the conversation's strict mode."""
    ws = _setup_strict_pii(db, stub_pii, stub_analyze)
    cid = db.execute(
        text("INSERT INTO conversations (workspace_id, user_id, title, pii_mode) "
             "VALUES (:w, :u, 't', 'strict') RETURNING id"),
        {"w": ws, "u": USER_A},
    ).scalar_one()
    token = make_token(sub=USER_A, email="a@firma.de")

    r = await client.post(
        "/v1/chat/completions", headers=_auth(token),
        json=_body(CHOSEN, content=f"Hilf {PII}", conversation_id=str(cid)),
    )  # NB: no pii_mode in the request
    d = r.json()["decyra"]
    assert d["anonymized"] is True  # strict governed via the conversation
    assert PII not in _provider_input(stub_llm)


@pytest.mark.asyncio
async def test_default_mode_is_sovereign(
    client, db, make_token, stub_pii, stub_analyze, stub_llm
) -> None:
    """No request mode, no conversation mode, workspace default -> sovereign:
    PII reroutes (4.5a behaviour) and the audit records pii_mode='sovereign'."""
    seed_org_with_owner(db, USER_A, "a@firma.de")
    _seed_model(db, CHOSEN)
    _seed_sovereign(db)
    stub_pii.state["force"] = "detected"
    stub_analyze.state["spans_for"] = {PII: "PERSON"}
    token = make_token(sub=USER_A, email="a@firma.de")

    r = await client.post(
        "/v1/chat/completions", headers=_auth(token),
        json=_body(CHOSEN, content=f"Hilf {PII}"),
    )
    d = r.json()["decyra"]
    assert d["anonymized"] is False
    assert d["effective_model"] == SOVEREIGN  # rerouted, not anonymised
    ev = db.execute(
        text("SELECT pii_mode FROM audit_events ORDER BY timestamp DESC LIMIT 1")
    ).scalar_one()
    assert ev == "sovereign"
