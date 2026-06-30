"""Task 5B.2 Sub-Task 5 — chart-in-chat-response envelope, with the THREE
invariants proven through the REAL endpoint (sandbox + LLM stubbed).

The codegen+sandbox loop and the failure-status handling already have endpoint
proofs in ``tests/test_analysis_codegen.py``. This file FORMALISES Sub-Task 5 by
proving the three contract invariants of the chart return:

  1. THE CHART NEVER GOES BACK TO THE LLM. After a SUCCESSFUL analysis no entry
     in ``stub_llm.calls`` carries the chart — not the base64 string, not the
     raw bytes, not a vision/image content block, not a "describe"/"beschreibe"
     prompt — and the number of LLM calls equals the number of codegen attempts
     (1 on first-try success). Control-flow reason: the analysis branch returns
     EARLY right after ``analysis.generate_and_run`` and makes NO further LLM
     call; the only ``complete_fn`` calls are the codegen attempts, each built
     from the SCHEMA (not the rendered chart). The chart bytes come OUT of the
     sandbox and go straight into the HTTP body.

  2. NO RAW CHART AT REST / IN AUDIT. The chart bytes live ONLY in the HTTP
     response. After a successful analysis a SELECT over ``messages``,
     ``chat_attachments`` and ``audit_events`` finds none of them carry the
     chart base64, and ``chat_attachments`` stays empty (the branch persists
     nothing).

  3. FRIENDLY FINAL-FAILURE MESSAGE. After the bounded retries are exhausted the
     user gets a clear German message with NO raw box output and NO traceback:
     the serialized response body contains neither the raw ``last_output``
     sentinel we fed via ``stub_sandbox.output`` nor a "Traceback" line.
"""

from __future__ import annotations

import base64
import json

import pytest
from sqlalchemy import text

from app.main import _ANALYSIS_FAILED_MSG
from tests._helpers import seed_org_with_owner
from tests.test_pii_routing import (
    CHOSEN,
    _auth,
    _seed_model,
    _seed_sovereign,
)

USER_A = "11111111-1111-1111-1111-111111111111"

# A chart payload starting with the real PNG magic bytes.
_PNG = b"\x89PNG\r\n\x1a\n_decyra_chart_pixels"
# A unique sentinel placed in the sandbox raw output that must NEVER reach the
# user-facing response on the failure path.
_RAW_LEAK = "RAW_TRACEBACK_LEAK_Mueller_GmbH_12345"


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


async def _post_success(client, token, *, chart=_PNG):
    return await client.post(
        "/v1/chat/completions",
        headers=_auth(token),
        **_multipart(b"q,umsatz\nQ1,10\nQ2,20\n", content="Umsatz als Balken"),
    )


# --- Invariant: chart returned in the response ------------------------------


@pytest.mark.asyncio
async def test_chart_returned_in_response(
    client, db, make_token, stub_pii, stub_llm, stub_sandbox
):
    """Successful analysis -> 200, decyra.chart_png_b64 decodes to bytes that
    start with the PNG magic, and the assistant message is present."""
    _seed(db)
    token = make_token(sub=USER_A, email="a@firma.de")
    stub_sandbox.state["status"] = "ok"
    stub_sandbox.state["chart_png"] = _PNG
    stub_llm.state["content"] = "df.plot()\nplt.savefig(CHART_PATH)\n"

    r = await _post_success(client, token)
    assert r.status_code == 200, r.text
    body = r.json()

    assert body["decyra"]["analysis_status"] == "ok"
    raw = base64.b64decode(body["decyra"]["chart_png_b64"])
    assert raw == _PNG
    assert raw.startswith(b"\x89PNG\r\n\x1a\n")  # PNG magic

    # Assistant message present and friendly (no internals).
    msg = body["choices"][0]["message"]
    assert msg["role"] == "assistant"
    assert msg["content"] == "Hier ist dein Diagramm."


# --- Invariant 1: the chart never goes back to the LLM ----------------------


def _vision_block_present(call: dict) -> bool:
    """True if any message in this LLM call carries an image/vision content
    block (OpenAI multimodal shape) rather than plain string content."""
    for m in call.get("messages") or []:
        content = m.get("content")
        if isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") in (
                    "image_url",
                    "image",
                    "input_image",
                ):
                    return True
    return False


@pytest.mark.asyncio
async def test_chart_never_sent_to_llm(
    client, db, make_token, stub_pii, stub_llm, stub_sandbox
):
    """After a successful analysis NO LLM call carries the chart (base64, raw
    bytes, a vision block, or a describe-prompt), and len(calls) == codegen
    attempts (1 on first-try success)."""
    _seed(db)
    token = make_token(sub=USER_A, email="a@firma.de")
    stub_sandbox.state["status"] = "ok"
    stub_sandbox.state["chart_png"] = _PNG
    stub_llm.state["content"] = "df.plot()\nplt.savefig(CHART_PATH)\n"

    r = await _post_success(client, token)
    assert r.status_code == 200, r.text
    body = r.json()
    # The chart WAS delivered to the user...
    chart_b64 = body["decyra"]["chart_png_b64"]
    assert base64.b64decode(chart_b64) == _PNG

    # ...but exactly ONE LLM call happened (the single codegen attempt), and the
    # chart is in NONE of the LLM calls.
    assert len(stub_llm.calls) == 1, "exactly one codegen attempt on first-try success"

    for call in stub_llm.calls:
        serialized = json.dumps(call.get("messages"), default=str)
        # Not the base64 string.
        assert chart_b64 not in serialized
        # Not the PNG magic / raw bytes (latin-1 view of the bytes).
        assert _PNG.decode("latin-1") not in serialized
        assert "\\u0089PNG" not in serialized and "PNG\\r\\n" not in serialized
        # Not a vision/image content block.
        assert not _vision_block_present(call)
        # Not a describe-the-chart round-trip.
        low = serialized.lower()
        assert "describe" not in low
        assert "beschreib" not in low


# --- Invariant 2: no raw chart at rest / in audit ---------------------------


@pytest.mark.asyncio
async def test_chart_not_persisted_at_rest(
    client, db, make_token, stub_pii, stub_llm, stub_sandbox
):
    """After a successful analysis the chart base64 appears in NONE of
    messages / chat_attachments / audit_events, and chat_attachments stays
    empty (the branch persists nothing)."""
    _seed(db)
    token = make_token(sub=USER_A, email="a@firma.de")
    stub_sandbox.state["status"] = "ok"
    stub_sandbox.state["chart_png"] = _PNG
    stub_llm.state["content"] = "df.plot()\nplt.savefig(CHART_PATH)\n"

    r = await _post_success(client, token)
    assert r.status_code == 200, r.text
    chart_b64 = r.json()["decyra"]["chart_png_b64"]
    assert chart_b64  # delivered in the response

    png_magic_text = _PNG.decode("latin-1")

    # messages.content carries no chart.
    msg_contents = [
        row[0] for row in db.execute(text("SELECT content FROM messages")).fetchall()
    ]
    for c in msg_contents:
        assert chart_b64 not in c
        assert png_magic_text not in c

    # chat_attachments is empty AND its extracted_text carries no chart.
    att = db.execute(
        text("SELECT COUNT(*), COALESCE(string_agg(extracted_text, '||'), '') "
             "FROM chat_attachments")
    ).fetchone()
    assert att[0] == 0, "analysis branch persists no chat_attachments"
    assert chart_b64 not in att[1]
    assert png_magic_text not in att[1]

    # audit_events request/response text carries no chart.
    audit_rows = db.execute(
        text("SELECT request_text, response_text FROM audit_events")
    ).fetchall()
    for req, resp in audit_rows:
        for field in (req or "", resp or ""):
            assert chart_b64 not in field
            assert png_magic_text not in field


# --- Invariant 3: friendly final-failure message ----------------------------


def test_final_failure_message_constant_is_friendly():
    """_ANALYSIS_FAILED_MSG is a clear German user message that leaks no
    internals (no traceback, no box markers, no raw-output token)."""
    m = _ANALYSIS_FAILED_MSG
    assert "Analyse" in m  # German, talks about the analysis
    assert m.endswith(".")  # a sentence, not a dump
    low = m.lower()
    for forbidden in (
        "traceback",
        "exception",
        "<generated>",
        "decyra_status",
        "stderr",
        "stdout",
        "last_output",
    ):
        assert forbidden not in low


@pytest.mark.asyncio
async def test_final_failure_message_is_clean(
    client, db, make_token, stub_pii, stub_llm, stub_sandbox
):
    """All-error path: after the bounded retries the user gets the friendly
    error at HTTP 200, and the serialized body contains NEITHER the raw
    last_output sentinel we fed via stub_sandbox.output NOR a traceback."""
    _seed(db)
    token = make_token(sub=USER_A, email="a@firma.de")
    stub_sandbox.state["status"] = "error"
    stub_sandbox.state["output"] = (
        "Traceback (most recent call last):\n"
        '  File "<generated>", line 3, in <module>\n'
        f"ValueError: {_RAW_LEAK}\n"
    )
    stub_llm.state["content"] = "df.plot()\nplt.savefig(CHART_PATH)\n"

    r = await client.post(
        "/v1/chat/completions",
        headers=_auth(token),
        **_multipart(b"kunde,umsatz\nMueller GmbH,1000\n", content="Umsatz pro Kunde"),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    serialized = json.dumps(body)

    # Friendly error surfaced to the user.
    assert body["decyra"]["error"] == _ANALYSIS_FAILED_MSG
    assert body["decyra"]["analysis_status"] == "error"
    assert "chart_png_b64" not in body["decyra"]
    assert body["choices"][0]["message"]["content"] == _ANALYSIS_FAILED_MSG

    # NO raw box output / traceback / cell value in the user-facing body.
    assert _RAW_LEAK not in serialized
    assert "Traceback" not in serialized
    assert "<generated>" not in serialized
    assert "Mueller GmbH" not in serialized
