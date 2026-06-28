# tests/test_rag.py
"""Task 5.3 — RAG chat integration: retrieval + context injection + PII safety."""
from __future__ import annotations

import pytest
from sqlalchemy import text

from tests._helpers import seed_org_with_owner
from tests.test_pii_routing import (
    CHOSEN, SOVEREIGN, _auth, _body, _seed_model, _seed_sovereign,
)

USER = "11111111-1111-1111-1111-111111111111"
IBAN = "DE89370400440532013000"


def _unit_vec(idx: int, dim: int = 1024) -> list[float]:
    v = [0.0] * dim
    v[idx] = 1.0
    return v


def _lit(v):
    return "[" + ",".join(repr(float(x)) for x in v) + "]"


def _seed_doc(db, ws, filename="doc.pdf"):
    return str(db.execute(
        text("INSERT INTO documents (workspace_id, filename, uploaded_by, "
             "storage_key, mime_type, size_bytes, extracted_text, "
             "extraction_status, embedding_status) VALUES "
             "(:w,:fn,:u,'k','text/plain',0,'','ok','done') RETURNING id"),
        {"w": ws, "fn": filename, "u": USER}).scalar_one())


def _chunk(db, doc, ws, content, idx, vec):
    db.execute(
        text("INSERT INTO document_chunks (document_id, workspace_id, content, "
             "chunk_index, embedding) VALUES (:d,:w,:c,:i,(:e)::vector)"),
        {"d": doc, "w": ws, "c": content, "i": idx, "e": _lit(vec)})


def _provider_messages(stub_llm):
    """The messages dict the (stubbed) provider was called with last."""
    return stub_llm.calls[-1]["messages"]


@pytest.mark.asyncio
async def test_rag_answer_uses_context_sovereign(
    client, db, make_token, stub_pii, stub_embed, stub_llm
):
    _org, ws = seed_org_with_owner(db, USER, "a@firma.de")
    _seed_model(db, CHOSEN)
    _seed_sovereign(db)
    doc = _seed_doc(db, ws, "handbuch.pdf")
    _chunk(db, doc, ws, "Die Urlaubsregelung erlaubt 30 Tage.", 0, _unit_vec(0))
    stub_embed.state["vectors"] = [_unit_vec(0)]
    token = make_token(sub=USER, email="a@firma.de")

    r = await client.post(
        "/v1/chat/completions", headers=_auth(token),
        json=_body(token, CHOSEN, content="Wie viel Urlaub?", use_company_knowledge=True),
    )
    assert r.status_code == 200, r.text
    msgs = _provider_messages(stub_llm)
    sys = [m for m in msgs if m["role"] == "system"]
    assert sys and "[Quelle: handbuch.pdf #0]" in sys[0]["content"]
    assert "30 Tage" in sys[0]["content"]


@pytest.mark.asyncio
async def test_rag_context_is_provider_only_not_persisted(
    client, db, make_token, stub_pii, stub_embed, stub_llm
):
    _org, ws = seed_org_with_owner(db, USER, "a@firma.de")
    _seed_model(db, CHOSEN)
    _seed_sovereign(db)
    doc = _seed_doc(db, ws)
    _chunk(db, doc, ws, "PROVIDERONLY-MARKER Inhalt.", 0, _unit_vec(0))
    stub_embed.state["vectors"] = [_unit_vec(0)]
    token = make_token(sub=USER, email="a@firma.de")

    r = await client.post(
        "/v1/chat/completions", headers=_auth(token),
        json=_body(token, CHOSEN, content="frage", use_company_knowledge=True),
    )
    assert r.status_code == 200, r.text
    # provider SAW the context...
    assert any(m["role"] == "system" and "PROVIDERONLY-MARKER" in m["content"]
               for m in _provider_messages(stub_llm))
    # ...but it was NEVER persisted as a message and there is NO system message.
    rows = db.execute(text("SELECT role, content FROM messages")).all()
    assert all(r.role != "system" for r in rows)
    assert all("PROVIDERONLY-MARKER" not in (r.content or "") for r in rows)


@pytest.mark.asyncio
async def test_rag_strict_coreference_same_placeholder_across_question_and_chunk(
    client, db, make_token, stub_pii, stub_analyze, stub_embed, stub_llm
):
    _org, ws = seed_org_with_owner(db, USER, "a@firma.de")
    db.execute(text("UPDATE workspaces SET settings='{\"pii_mode\":\"strict\"}'::jsonb "
                    "WHERE id=:w"), {"w": ws})
    _seed_model(db, CHOSEN)
    _seed_sovereign(db)
    doc = _seed_doc(db, ws)
    _chunk(db, doc, ws, f"Überweisung an {IBAN} ausgeführt.", 0, _unit_vec(0))
    stub_embed.state["vectors"] = [_unit_vec(0)]
    stub_pii.state["force"] = "detected"            # strict enters anonymise branch
    stub_analyze.state["spans_for"] = {IBAN: "IBAN"}  # the IBAN is PII in any text
    token = make_token(sub=USER, email="a@firma.de")

    r = await client.post(
        "/v1/chat/completions", headers=_auth(token),
        json=_body(token, CHOSEN, content=f"Stimmt die {IBAN}?", use_company_knowledge=True),
    )
    assert r.status_code == 200, r.text
    msgs = _provider_messages(stub_llm)
    blob = "\n".join(m["content"] for m in msgs)
    assert IBAN not in blob                          # raw IBAN never left the house
    sys = next(m for m in msgs if m["role"] == "system")
    usr = next(m for m in msgs if m["role"] == "user")
    assert "[[DCY_IBAN_0]]" in sys["content"]         # same placeholder in BOTH sources
    assert "[[DCY_IBAN_0]]" in usr["content"]
    # I5: the IMMUTABLE audit chain holds the ANONYMISED context (read AFTER
    # anonymisation), never raw chunk PII — exactly what transited to the cloud.
    audit_req = db.execute(
        text("SELECT request_text FROM audit_events ORDER BY timestamp DESC LIMIT 1")
    ).scalar_one()
    assert IBAN not in audit_req                       # raw chunk PII never in the chain
    assert "[[DCY_IBAN_0]]" in audit_req               # anonymised context WAS audited


@pytest.mark.asyncio
async def test_rag_chunk_pii_forces_eu_reroute_sovereign(
    client, db, make_token, stub_pii, stub_embed, stub_llm
):
    _org, ws = seed_org_with_owner(db, USER, "a@firma.de")
    _seed_model(db, CHOSEN)            # US model: eu_hosted=False, sovereign_eligible=False
    _seed_sovereign(db)
    doc = _seed_doc(db, ws)
    _chunk(db, doc, ws, "Kunde __CHUNKPII__ im Vertrag.", 0, _unit_vec(0))
    stub_embed.state["vectors"] = [_unit_vec(0)]
    stub_pii.state["needle"] = "__CHUNKPII__"   # detected iff this token is in the scanned text
    token = make_token(sub=USER, email="a@firma.de")

    # WITHOUT RAG: clean user text -> no reroute, US model used.
    r1 = await client.post(
        "/v1/chat/completions", headers=_auth(token),
        json=_body(token, CHOSEN, content="Fasse zusammen"),
    )
    assert r1.json()["decyra"]["effective_model"] == CHOSEN

    # WITH RAG: chunk PII enters routing_text -> reroute to the EU sovereign model.
    r2 = await client.post(
        "/v1/chat/completions", headers=_auth(token),
        json=_body(token, CHOSEN, content="Fasse zusammen", use_company_knowledge=True),
    )
    assert r2.json()["decyra"]["effective_model"] == SOVEREIGN
    assert r2.json()["decyra"]["routed_to"] == "mistral"


@pytest.mark.asyncio
async def test_rag_below_threshold_no_context(
    client, db, make_token, stub_pii, stub_embed, stub_llm
):
    _org, ws = seed_org_with_owner(db, USER, "a@firma.de")
    _seed_model(db, CHOSEN)
    _seed_sovereign(db)
    doc = _seed_doc(db, ws)
    _chunk(db, doc, ws, "Irrelevant.", 0, _unit_vec(1))   # orthogonal -> sim 0
    stub_embed.state["vectors"] = [_unit_vec(0)]
    token = make_token(sub=USER, email="a@firma.de")

    r = await client.post(
        "/v1/chat/completions", headers=_auth(token),
        json=_body(token, CHOSEN, content="frage", use_company_knowledge=True),
    )
    assert r.status_code == 200, r.text
    assert all(m["role"] != "system" for m in _provider_messages(stub_llm))  # no context


@pytest.mark.asyncio
async def test_rag_no_chunks_classic_path_no_embed(
    client, db, make_token, stub_pii, stub_embed, stub_llm
):
    _org, ws = seed_org_with_owner(db, USER, "a@firma.de")  # no documents
    _seed_model(db, CHOSEN)
    _seed_sovereign(db)
    token = make_token(sub=USER, email="a@firma.de")

    r = await client.post(
        "/v1/chat/completions", headers=_auth(token),
        json=_body(token, CHOSEN, content="frage", use_company_knowledge=True),
    )
    assert r.status_code == 200, r.text
    assert stub_embed.calls == []  # empty workspace short-circuits, no query embed
    assert all(m["role"] != "system" for m in _provider_messages(stub_llm))
