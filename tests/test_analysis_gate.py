"""Task 5B.2 Sub-Task 3 — schema info through the PII gate (S6, part 2).

The core invariant: the schema block sent to the LLM (column names + dtypes +
row count, NO cell values) is the FIFTH content source and MUST traverse the
SAME chokepoint as user text, history, RAG chunks and file text. It is wired
exactly like the 5B.1 ``file_context_msgs`` / ``file_text`` pair:

* strict  -> the schema block is a message inside ``llm_input``, so
  ``pii.anonymize_messages`` masks PII in column names automatically AND in the
  SAME shared map (coreference with the user question -> one placeholder);
* sovereign -> the schema text is appended to ``routing_text``, so PII in a
  column name triggers the EU reroute just like the other real sources.

These tests prove that wiring through the real endpoint (the sandbox/LLM are
stubbed; the routing/anonymisation path is the real ``app.main`` code).
"""

from __future__ import annotations

import json

import pytest

from tests._helpers import seed_org_with_owner
from tests.test_pii_routing import (
    CHOSEN,
    SOVEREIGN,
    _auth,
    _seed_model,
    _seed_sovereign,
)

USER_A = "11111111-1111-1111-1111-111111111111"


def _seed(db):
    seed_org_with_owner(db, USER_A, "a@firma.de")
    _seed_model(db, CHOSEN)
    _seed_sovereign(db)


def _multipart(file_bytes, *, content, filename="u.csv", model=CHOSEN, **extra):
    payload = {
        "model": model,
        "use_code_interpreter": True,
        "messages": [{"role": "user", "content": content}],
        **extra,
    }
    return {
        "files": {"file": (filename, file_bytes, "text/csv")},
        "data": {"payload": json.dumps(payload)},
    }


def _provider_messages_blob(stub_llm) -> str:
    """Join the messages the provider actually received on the last LLM call."""
    msgs = stub_llm.calls[-1]["messages"]
    return "\n".join(m.get("content") or "" for m in msgs)


# --- 3. No cell value reaches the LLM; column names DO ------------------


@pytest.mark.asyncio
async def test_no_cell_values_reach_llm(
    client, db, make_token, stub_pii, stub_llm, stub_sandbox
):
    """The provider gets the column names (schema) but NEVER any cell value."""
    _seed(db)
    token = make_token(sub=USER_A, email="a@firma.de")
    csv = b"kunde,umsatz\nMueller GmbH,1000\nSchmidt AG,2000\n"

    r = await client.post(
        "/v1/chat/completions",
        headers=_auth(token),
        **_multipart(csv, content="Umsatz pro Kunde als Balken"),
    )
    assert r.status_code == 200, r.text

    blob = _provider_messages_blob(stub_llm)
    # Column names (the schema) are present...
    assert "kunde" in blob and "umsatz" in blob
    # ...but no cell VALUE ever transited to the provider.
    assert "Mueller" not in blob
    assert "Schmidt" not in blob
    assert "1000" not in blob
    assert "2000" not in blob


# --- 4. Sovereign reroute when PII is ONLY in a column name -------------


@pytest.mark.asyncio
async def test_column_names_routed_sovereign(
    client, db, make_token, stub_pii, stub_llm, stub_sandbox
):
    """PII trigger is present ONLY in a column name, never in the user text.
    The needle ``geheimspalte`` is a substring of the column header but absent
    from the question. With the schema text appended to ``routing_text``,
    ``contains_pii`` fires and the request reroutes to the EU model.

    WHY this proves S6: the user text is clean and there is no history/RAG/file
    source, so ``routing_text`` would be entirely clean WITHOUT the schema. The
    reroute can ONLY happen because the schema text (carrying the column name)
    is part of ``routing_text``. Remove that line and the needle vanishes from
    the scanned text -> no detection -> no reroute. The chosen model is a
    non-sovereign US model (test-model); the asserted effective model is the EU
    sovereign one."""
    _seed(db)
    token = make_token(sub=USER_A, email="a@firma.de")
    # Needle lives ONLY inside the column header, NOT in the user question.
    stub_pii.state["needle"] = "geheimspalte"
    csv = b"geheimspalte,umsatz\nA,1\n"

    r = await client.post(
        "/v1/chat/completions",
        headers=_auth(token),
        # sovereign is the secure default mode (no pii_mode override here).
        **_multipart(csv, content="Bitte ein Diagramm erstellen", model=CHOSEN),
    )
    assert r.status_code == 200, r.text
    # Reroute happened => the schema column name reached routing_text.
    assert r.json()["decyra"]["effective_model"] == SOVEREIGN
    # Sanity: the user text alone carries no needle (so only the schema could
    # have triggered it).
    assert "geheimspalte" not in "Bitte ein Diagramm erstellen"


# --- 5. Strict anonymisation + coreference across schema and question ---


@pytest.mark.asyncio
async def test_column_names_anonymized_strict(
    client, db, make_token, stub_pii, stub_analyze, stub_llm, stub_sandbox
):
    """Strict mode. A PERSON token (``Mustermann``) is BOTH a column name AND
    present in the user question. Driving ``stub_analyze`` to tag that token as
    PERSON, the strict path runs ``anonymize_messages`` over the WHOLE
    ``llm_input`` (which includes the schema block as a message). The provider
    therefore sees a placeholder in place of the column name, and — because one
    shared Anonymizer map is used — it is the SAME placeholder as in the user
    message (coreference). The raw token never reaches the provider.

    WHY this proves S6: the schema block is a real message inside ``llm_input``,
    so it is anonymised by the identical primitive as user/history/RAG/file
    text. The shared placeholder proves the schema goes through the same single
    map, not a separate path."""
    _seed(db)
    token = make_token(sub=USER_A, email="a@firma.de")
    stub_pii.state["force"] = "detected"  # strict: full-input detection fires
    stub_analyze.state["spans_for"] = {"Mustermann": "PERSON"}
    csv = b"Mustermann,umsatz\nA,1\n"

    r = await client.post(
        "/v1/chat/completions",
        headers=_auth(token),
        **_multipart(
            csv,
            content="Analysiere die Daten von Mustermann",
            model=CHOSEN,
            pii_mode="strict",
        ),
    )
    assert r.status_code == 200, r.text
    d = r.json()["decyra"]
    assert d["pii_mode"] == "strict"
    assert d["anonymized"] is True
    # Strict keeps the chosen model and sends placeholders (no reroute).
    assert d["effective_model"] == CHOSEN

    msgs = stub_llm.calls[-1]["messages"]
    schema_msg = next(
        m for m in msgs if "Spalten:" in (m.get("content") or "")
    )
    user_msg = next(
        m
        for m in msgs
        if m.get("role") == "user" and "Analysiere" in (m.get("content") or "")
    )
    # Raw PERSON token is gone from BOTH the schema block and the user message.
    assert "Mustermann" not in schema_msg["content"]
    assert "Mustermann" not in user_msg["content"]
    # The placeholder used for the column name is the SAME one used in the user
    # message (shared map / coreference).
    assert "[[DCY_PERSON_0]]" in schema_msg["content"]
    assert "[[DCY_PERSON_0]]" in user_msg["content"]


# --- 6. Clean schema + clean user text + US model -> no reroute --------


@pytest.mark.asyncio
async def test_schema_gate_clean_no_reroute(
    client, db, make_token, stub_pii, stub_llm, stub_sandbox
):
    """A clean schema and clean user text keep the chosen (US) model: the gate
    does not reroute when there is no PII to protect."""
    _seed(db)
    token = make_token(sub=USER_A, email="a@firma.de")
    # Default stub_pii needle is "__PII__", absent everywhere here -> clean.
    csv = b"quartal,umsatz\nQ1,10\nQ2,20\n"

    r = await client.post(
        "/v1/chat/completions",
        headers=_auth(token),
        **_multipart(csv, content="Umsatz pro Quartal", model=CHOSEN),
    )
    assert r.status_code == 200, r.text
    d = r.json()["decyra"]
    assert d["pii_detected"] is False
    assert d["effective_model"] == CHOSEN  # no reroute
