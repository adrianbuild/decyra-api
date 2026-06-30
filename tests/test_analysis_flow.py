"""Task 5B.2 Sub-Task 2 — transient raw-byte intake for the code-interpreter
analysis path (NO persistence).

These tests cover the analysis branch of /v1/chat/completions when
``use_code_interpreter`` is true. The key invariant proven here is
TRANSIENCE: the uploaded bytes are validated + held in memory for the request
and then discarded — they are NEVER written to chat_attachments (the 5B.1
persistence path is deliberately bypassed for analysis uploads).

Type allow-list (content-sniffed, not extension): only XLSX and TXT/CSV are
accepted for analysis; PDF/DOCX -> 415. The existing streaming byte cap
(settings.max_upload_bytes) still applies (-> 413). Auth is still required
(-> 401).

Later sub-tasks (3-5) extend the analysis response with the chart; this task
only proves intake + validation + a minimal "accepted for analysis" response.
"""

from __future__ import annotations

import io
import json

import openpyxl
import pytest
from sqlalchemy import text

from app.config import get_settings
from app.main import app
from tests._helpers import seed_org_with_owner
from tests.test_documents import make_pdf
from tests.test_pii_routing import CHOSEN, _auth, _seed_model, _seed_sovereign

USER_A = "11111111-1111-1111-1111-111111111111"


@pytest.fixture
def settings_override():
    """Override get_settings for the analysis path (e.g. shrink max_upload_bytes)."""
    base = get_settings()

    def _apply(**overrides):
        app.dependency_overrides[get_settings] = lambda: base.model_copy(update=overrides)

    yield _apply
    app.dependency_overrides.pop(get_settings, None)


def _payload(content="Umsatz pro Quartal als Balken", **extra):
    return {
        "model": CHOSEN,
        "use_code_interpreter": True,
        "messages": [{"role": "user", "content": content}],
        **extra,
    }


def _multipart(file_bytes, *, filename="u.csv", content_type="text/csv", **extra):
    return {
        "files": {"file": (filename, file_bytes, content_type)},
        "data": {"payload": json.dumps(_payload(**extra))},
    }


def make_xlsx(rows: list[list]) -> bytes:
    buf = io.BytesIO()
    wb = openpyxl.Workbook()
    ws = wb.active
    for row in rows:
        ws.append(row)
    wb.save(buf)
    return buf.getvalue()


def _seed(db):
    seed_org_with_owner(db, USER_A, "a@firma.de")
    _seed_model(db, CHOSEN)
    _seed_sovereign(db)


# --- 1. transient: a CSV analysis upload is NOT persisted --------------


@pytest.mark.asyncio
async def test_analysis_does_not_persist_attachment(
    client, db, make_token, stub_pii, stub_llm, stub_sandbox
):
    _seed(db)
    token = make_token(sub=USER_A, email="a@firma.de")

    r = await client.post(
        "/v1/chat/completions",
        headers=_auth(token),
        **_multipart(b"q,umsatz\nQ1,10\nQ2,20\n"),
    )
    assert r.status_code == 200, r.text
    # Transient: nothing persisted to chat_attachments.
    rows = db.execute(text("SELECT count(*) FROM chat_attachments")).scalar_one()
    assert rows == 0


# --- 2. accepts CSV: schema flows to the LLM, file stays transient -----
# Sub-Task 3 replaced the Sub-Task 2 "accepted for analysis" placeholder
# envelope: the request now flows through the normal chat-completion path with
# the schema as the fifth (provider-only) context source. The load-bearing
# invariants of this test are unchanged — CSV is accepted (200) and the upload
# is NEVER persisted — plus the new schema-only guarantee (columns to the LLM,
# no cell values).


@pytest.mark.asyncio
async def test_analysis_accepts_csv(
    client, db, make_token, stub_pii, stub_llm, stub_sandbox
):
    _seed(db)
    token = make_token(sub=USER_A, email="a@firma.de")
    csv = b"q,umsatz\nQ1,10\nQ2,20\n"

    r = await client.post(
        "/v1/chat/completions",
        headers=_auth(token),
        **_multipart(csv, filename="u.csv", content_type="text/csv"),
    )
    assert r.status_code == 200, r.text
    # Still transient: nothing persisted to chat_attachments.
    assert db.execute(text("SELECT count(*) FROM chat_attachments")).scalar_one() == 0
    # Schema (column names) reached the provider; cell values did NOT.
    blob = "\n".join(m.get("content") or "" for m in stub_llm.calls[-1]["messages"])
    assert "q" in blob and "umsatz" in blob
    assert "Q1" not in blob and "Q2" not in blob
    assert "10" not in blob and "20" not in blob


# --- 3. accepts XLSX ---------------------------------------------------


@pytest.mark.asyncio
async def test_analysis_accepts_xlsx(
    client, db, make_token, stub_pii, stub_llm, stub_sandbox
):
    _seed(db)
    token = make_token(sub=USER_A, email="a@firma.de")
    xlsx = make_xlsx([["q", "umsatz"], ["Q1", 10], ["Q2", 20]])

    r = await client.post(
        "/v1/chat/completions",
        headers=_auth(token),
        **_multipart(
            xlsx,
            filename="u.xlsx",
            content_type=(
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            ),
        ),
    )
    assert r.status_code == 200, r.text
    # still transient
    assert db.execute(text("SELECT count(*) FROM chat_attachments")).scalar_one() == 0
    # Schema columns reached the provider; cell values did NOT.
    blob = "\n".join(m.get("content") or "" for m in stub_llm.calls[-1]["messages"])
    assert "q" in blob and "umsatz" in blob
    assert "Q1" not in blob and "Q2" not in blob


# --- 4. rejects PDF with 415 (type allow-list) -------------------------


@pytest.mark.asyncio
async def test_analysis_rejects_pdf_415(
    client, db, make_token, stub_pii, stub_llm, stub_sandbox
):
    _seed(db)
    token = make_token(sub=USER_A, email="a@firma.de")

    r = await client.post(
        "/v1/chat/completions",
        headers=_auth(token),
        **_multipart(
            make_pdf("Vertrag"), filename="u.pdf", content_type="application/pdf"
        ),
    )
    assert r.status_code == 415, r.text
    # nothing persisted, no LLM call, no sandbox call
    assert db.execute(text("SELECT count(*) FROM chat_attachments")).scalar_one() == 0
    assert stub_llm.calls == []
    assert stub_sandbox.calls == []


# --- 5. rejects oversize with 413 (reuses max_upload_bytes cap) --------


@pytest.mark.asyncio
async def test_analysis_rejects_oversize_413(
    client, db, make_token, stub_pii, stub_llm, stub_sandbox, settings_override
):
    settings_override(max_upload_bytes=50)  # send ~200 bytes
    _seed(db)
    token = make_token(sub=USER_A, email="a@firma.de")

    r = await client.post(
        "/v1/chat/completions",
        headers=_auth(token),
        **_multipart(b"q,umsatz\n" + b"1," * 200, filename="big.csv"),
    )
    assert r.status_code == 413, r.text
    assert db.execute(text("SELECT count(*) FROM chat_attachments")).scalar_one() == 0
    assert stub_llm.calls == []
    assert stub_sandbox.calls == []


# --- 6. unauthenticated -> 401 -----------------------------------------


@pytest.mark.asyncio
async def test_analysis_requires_auth_401(client, db, stub_pii, stub_llm, stub_sandbox):
    _seed(db)

    r = await client.post(
        "/v1/chat/completions",
        **_multipart(b"q,umsatz\nQ1,10\n"),
    )
    assert r.status_code == 401, r.text
