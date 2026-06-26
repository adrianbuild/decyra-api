"""Task 5.1 — document upload, extraction, listing, deletion.

Security + tenant-isolation guards:
- content-based type sniffing (a binary renamed .pdf/.txt, or a bare zip renamed
  .docx, is rejected),
- server-side size cap enforced by COUNTING streamed bytes (not Content-Length),
- cross-workspace isolation under the real decyra_app RLS role,
- hard delete removes file + text but leaves an immutable tombstone.

Real tiny fixtures: TXT bytes, a DOCX built with python-docx, and minimal PDFs
(with text / without text) built inline.
"""

from __future__ import annotations

import io
import zipfile

import pytest
from docx import Document as DocxDocument
from sqlalchemy import text

from app.config import get_settings
from app.main import app
from tests._helpers import seed_org_with_owner

USER_A = "11111111-1111-1111-1111-111111111111"
USER_B = "22222222-2222-2222-2222-222222222222"
NOBODY = "33333333-3333-3333-3333-333333333333"


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# --- tiny real fixtures ------------------------------------------------


def make_pdf(text_content: str | None) -> bytes:
    """Minimal single-page PDF. text_content=None -> a page with no content
    stream (nothing extractable -> 'no_text')."""
    objs: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
    ]
    if text_content is None:
        objs.append(b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] >>")
    else:
        stream = b"BT /F1 24 Tf 72 700 Td (" + text_content.encode("latin-1") + b") Tj ET"
        objs.append(
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>"
        )
        objs.append(b"<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream))
        objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for i, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n%s\nendobj\n" % (i, body)
    xref_pos = len(out)
    n = len(objs) + 1
    out += b"xref\n0 %d\n0000000000 65535 f \n" % n
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF" % (n, xref_pos)
    return bytes(out)


def make_docx(text_content: str) -> bytes:
    buf = io.BytesIO()
    doc = DocxDocument()
    doc.add_paragraph(text_content)
    doc.save(buf)
    return buf.getvalue()


def make_bare_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("hello.txt", "not a docx")
    return buf.getvalue()


PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64  # valid PNG signature + junk


# --- fixtures ----------------------------------------------------------


@pytest.fixture
def settings_override(tmp_path):
    """Point document storage at a per-test tmp dir (and optionally tweak other
    settings) via the app's get_settings dependency. Always cleaned up."""
    base = get_settings()

    def _apply(**overrides):
        merged = {"document_storage_dir": str(tmp_path), **overrides}
        app.dependency_overrides[get_settings] = lambda: base.model_copy(update=merged)
        return tmp_path

    yield _apply
    app.dependency_overrides.pop(get_settings, None)


def _files(name: str, data: bytes, content_type: str):
    return {"file": (name, data, content_type)}


def _all_files(base_dir) -> list:
    return [p for p in base_dir.rglob("*") if p.is_file()]


# --- happy paths -------------------------------------------------------


@pytest.mark.asyncio
async def test_upload_txt_extracts_and_lists(client, db, make_token, settings_override):
    settings_override()
    seed_org_with_owner(db, USER_A, "a@firma.de")
    token = make_token(sub=USER_A, email="a@firma.de")

    r = await client.post(
        "/documents", headers=_auth(token),
        files=_files("notes.txt", b"Hallo Decyra Welt", "text/plain"),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["mime_type"] == "text/plain"
    assert body["extraction_status"] == "ok"

    lst = await client.get("/documents", headers=_auth(token))
    assert lst.status_code == 200
    assert [d["filename"] for d in lst.json()] == ["notes.txt"]

    stored = db.execute(text("SELECT extracted_text FROM documents")).scalar_one()
    assert stored == "Hallo Decyra Welt"


@pytest.mark.asyncio
async def test_upload_pdf_extracts(client, db, make_token, settings_override):
    settings_override()
    seed_org_with_owner(db, USER_A, "a@firma.de")
    token = make_token(sub=USER_A, email="a@firma.de")

    r = await client.post(
        "/documents", headers=_auth(token),
        files=_files("doc.pdf", make_pdf("Hello Decyra"), "application/pdf"),
    )
    assert r.status_code == 200, r.text
    assert r.json()["mime_type"] == "application/pdf"
    assert r.json()["extraction_status"] == "ok"
    assert "Hello Decyra" in db.execute(
        text("SELECT extracted_text FROM documents")
    ).scalar_one()


@pytest.mark.asyncio
async def test_upload_docx_extracts(client, db, make_token, settings_override):
    settings_override()
    seed_org_with_owner(db, USER_A, "a@firma.de")
    token = make_token(sub=USER_A, email="a@firma.de")

    r = await client.post(
        "/documents", headers=_auth(token),
        files=_files("report.docx", make_docx("Quartalsbericht Q3"),
                     "application/octet-stream"),  # wrong declared type on purpose
    )
    assert r.status_code == 200, r.text
    assert r.json()["mime_type"].endswith("wordprocessingml.document")
    assert "Quartalsbericht Q3" in db.execute(
        text("SELECT extracted_text FROM documents")
    ).scalar_one()


@pytest.mark.asyncio
async def test_upload_requires_auth(client, settings_override):
    settings_override()
    r = await client.post(
        "/documents", files=_files("x.txt", b"hi", "text/plain")
    )
    assert r.status_code == 401


# --- security: content-based type validation ---------------------------


@pytest.mark.asyncio
async def test_upload_rejects_binary_with_pdf_extension(
    client, db, make_token, settings_override
):
    tmp = settings_override()
    seed_org_with_owner(db, USER_A, "a@firma.de")
    token = make_token(sub=USER_A, email="a@firma.de")

    r = await client.post(
        "/documents", headers=_auth(token),
        files=_files("evil.pdf", PNG_BYTES, "application/pdf"),
    )
    assert r.status_code == 415, r.text
    # Nothing persisted, nothing written to disk.
    assert db.execute(text("SELECT count(*) FROM documents")).scalar_one() == 0
    assert _all_files(tmp) == []


@pytest.mark.asyncio
async def test_upload_rejects_binary_with_txt_extension(
    client, db, make_token, settings_override
):
    settings_override()
    seed_org_with_owner(db, USER_A, "a@firma.de")
    token = make_token(sub=USER_A, email="a@firma.de")

    r = await client.post(
        "/documents", headers=_auth(token),
        files=_files("evil.txt", b"\xff\xfe\x00\x01\x02binary", "text/plain"),
    )
    assert r.status_code == 415, r.text
    assert db.execute(text("SELECT count(*) FROM documents")).scalar_one() == 0


@pytest.mark.asyncio
async def test_upload_rejects_bare_zip_as_docx(
    client, db, make_token, settings_override
):
    """DOCX sharpening: a bare ZIP renamed .docx (no word/document.xml +
    wordprocessingml content type) is rejected — not accepted as 'a zip'."""
    settings_override()
    seed_org_with_owner(db, USER_A, "a@firma.de")
    token = make_token(sub=USER_A, email="a@firma.de")

    r = await client.post(
        "/documents", headers=_auth(token),
        files=_files("fake.docx", make_bare_zip(),
                     "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
    )
    assert r.status_code == 415, r.text
    assert db.execute(text("SELECT count(*) FROM documents")).scalar_one() == 0


# --- security: server-side size limit (streamed byte count) ------------


@pytest.mark.asyncio
async def test_upload_size_limit_enforced(client, db, make_token, settings_override):
    # 1 KiB cap; send ~5 KiB. The abort comes from COUNTING the streamed bytes
    # server-side (the code never reads Content-Length), satisfying the guard.
    tmp = settings_override(max_upload_bytes=1024)
    seed_org_with_owner(db, USER_A, "a@firma.de")
    token = make_token(sub=USER_A, email="a@firma.de")

    r = await client.post(
        "/documents", headers=_auth(token),
        files=_files("big.txt", b"A" * 5000, "text/plain"),
    )
    assert r.status_code == 413, r.text
    assert db.execute(text("SELECT count(*) FROM documents")).scalar_one() == 0
    assert _all_files(tmp) == []


# --- no_text (scanned PDF) is stored, not rejected ---------------------


@pytest.mark.asyncio
async def test_upload_scanned_pdf_no_text(client, db, make_token, settings_override):
    settings_override()
    seed_org_with_owner(db, USER_A, "a@firma.de")
    token = make_token(sub=USER_A, email="a@firma.de")

    r = await client.post(
        "/documents", headers=_auth(token),
        files=_files("scan.pdf", make_pdf(None), "application/pdf"),
    )
    assert r.status_code == 200, r.text
    assert r.json()["extraction_status"] == "no_text"
    row = db.execute(
        text("SELECT extracted_text, extraction_status FROM documents")
    ).one()
    assert row.extraction_status == "no_text"
    assert row.extracted_text == ""


@pytest.mark.asyncio
async def test_upload_corrupt_pdf_rejected(client, db, make_token, settings_override):
    settings_override()
    seed_org_with_owner(db, USER_A, "a@firma.de")
    token = make_token(sub=USER_A, email="a@firma.de")

    # Sniffs as PDF (has the %PDF- header) but is not parseable -> 422.
    r = await client.post(
        "/documents", headers=_auth(token),
        files=_files("broken.pdf", b"%PDF-1.4\nthis is not a real pdf", "application/pdf"),
    )
    assert r.status_code == 422, r.text
    assert db.execute(text("SELECT count(*) FROM documents")).scalar_one() == 0


# --- filename sanitisation / storage key safety ------------------------


@pytest.mark.asyncio
async def test_filename_sanitized_storage_key_not_raw(
    client, db, make_token, settings_override
):
    tmp = settings_override()
    _, ws = seed_org_with_owner(db, USER_A, "a@firma.de")
    token = make_token(sub=USER_A, email="a@firma.de")

    r = await client.post(
        "/documents", headers=_auth(token),
        files=_files("../../etc/passwd.txt", b"x", "text/plain"),
    )
    assert r.status_code == 200, r.text
    assert r.json()["filename"] == "passwd.txt"  # basename, no traversal

    fn, key = db.execute(
        text("SELECT filename, storage_key FROM documents")
    ).one()
    assert fn == "passwd.txt"
    assert key.startswith(f"{ws}/") and key.endswith(".txt")
    assert ".." not in key
    # File landed under the base dir, nowhere else.
    files = _all_files(tmp)
    assert len(files) == 1
    assert str(files[0]).startswith(str(tmp))


# --- tenant isolation (under the real decyra_app RLS role) -------------


@pytest.mark.asyncio
async def test_list_only_own_workspace(
    client_decyra_app, db, make_token, settings_override
):
    settings_override()
    seed_org_with_owner(db, USER_A, "a@firma.de")
    seed_org_with_owner(db, USER_B, "b@firma.de")  # separate org + workspace
    token_a = make_token(sub=USER_A, email="a@firma.de")
    token_b = make_token(sub=USER_B, email="b@firma.de")

    up = await client_decyra_app.post(
        "/documents", headers=_auth(token_a),
        files=_files("a-secret.txt", b"A geheim", "text/plain"),
    )
    assert up.status_code == 200, up.text

    # B (other workspace) must not see A's document — RLS is the boundary.
    lst = await client_decyra_app.get("/documents", headers=_auth(token_b))
    assert lst.status_code == 200
    assert lst.json() == []


@pytest.mark.asyncio
async def test_delete_cross_workspace_forbidden(
    client_decyra_app, db, make_token, settings_override
):
    settings_override()
    seed_org_with_owner(db, USER_A, "a@firma.de")
    seed_org_with_owner(db, USER_B, "b@firma.de")
    token_a = make_token(sub=USER_A, email="a@firma.de")
    token_b = make_token(sub=USER_B, email="b@firma.de")

    up = await client_decyra_app.post(
        "/documents", headers=_auth(token_a),
        files=_files("a.txt", b"A geheim", "text/plain"),
    )
    doc_id = up.json()["id"]

    # B tries to delete A's document -> invisible under RLS -> 404.
    dele = await client_decyra_app.delete(
        f"/documents/{doc_id}", headers=_auth(token_b)
    )
    assert dele.status_code == 404, dele.text
    # A's document survived. Verify via A's OWN list: the shared per-test
    # connection is left in B's RLS context (decyra_app + wsB), so a raw query
    # here would be RLS-hidden — A's GET re-sets the context to wsA.
    lst_a = await client_decyra_app.get("/documents", headers=_auth(token_a))
    assert [d["id"] for d in lst_a.json()] == [doc_id]


# --- delete removes file + text, keeps an immutable tombstone ----------


@pytest.mark.asyncio
async def test_delete_removes_file_and_text_keeps_tombstone(
    client, db, make_token, settings_override
):
    tmp = settings_override()
    seed_org_with_owner(db, USER_A, "a@firma.de")
    token = make_token(sub=USER_A, email="a@firma.de")

    up = await client.post(
        "/documents", headers=_auth(token),
        files=_files("del.txt", b"loesch mich", "text/plain"),
    )
    doc_id = up.json()["id"]
    assert len(_all_files(tmp)) == 1  # file on disk

    dele = await client.delete(f"/documents/{doc_id}", headers=_auth(token))
    assert dele.status_code == 204, dele.text

    # Row + text gone, file gone.
    assert db.execute(text("SELECT count(*) FROM documents")).scalar_one() == 0
    assert _all_files(tmp) == []
    # Immutable tombstone remains: filename + who + when, no content column.
    ev = db.execute(
        text("SELECT document_id, filename, event_type, actor_user_id "
             "FROM document_events")
    ).one()
    assert str(ev.document_id) == doc_id
    assert ev.filename == "del.txt"
    assert ev.event_type == "deleted"
    assert str(ev.actor_user_id) == USER_A
    cols = {
        c[0]
        for c in db.execute(
            text("SELECT column_name FROM information_schema.columns "
                 "WHERE table_name = 'document_events'")
        )
    }
    assert "content" not in cols and "extracted_text" not in cols


# --- 5.2: upload triggers chunking + embedding -------------------------


@pytest.mark.asyncio
async def test_upload_embeds_document(client, db, make_token, settings_override, stub_embed):
    settings_override()
    seed_org_with_owner(db, USER_A, "a@firma.de")
    token = make_token(sub=USER_A, email="a@firma.de")

    r = await client.post(
        "/documents", headers=_auth(token),
        files=_files("notes.txt", b"Hallo Decyra Welt aus dem Dokument", "text/plain"),
    )
    assert r.status_code == 200, r.text
    assert r.json()["embedding_status"] == "done"
    assert stub_embed.calls  # text was actually sent to (stubbed) Mistral

    n = db.execute(text("SELECT count(*) FROM document_chunks")).scalar_one()
    assert n >= 1
    dims_ok = db.execute(
        text("SELECT bool_and(vector_dims(embedding) = 1024) FROM document_chunks")
    ).scalar_one()
    assert dims_ok is True


@pytest.mark.asyncio
async def test_upload_no_text_skips_embedding(client, db, make_token, settings_override, stub_embed):
    settings_override()
    seed_org_with_owner(db, USER_A, "a@firma.de")
    token = make_token(sub=USER_A, email="a@firma.de")

    r = await client.post(
        "/documents", headers=_auth(token),
        files=_files("scan.pdf", make_pdf(None), "application/pdf"),
    )
    assert r.status_code == 200, r.text
    assert r.json()["extraction_status"] == "no_text"
    assert r.json()["embedding_status"] == "skipped"
    assert stub_embed.calls == []
    assert db.execute(text("SELECT count(*) FROM document_chunks")).scalar_one() == 0


@pytest.mark.asyncio
async def test_upload_embed_failure_still_succeeds(client, db, make_token, settings_override, stub_embed):
    settings_override()
    seed_org_with_owner(db, USER_A, "a@firma.de")
    token = make_token(sub=USER_A, email="a@firma.de")
    stub_embed.state["fail"] = RuntimeError("mistral down")

    r = await client.post(
        "/documents", headers=_auth(token),
        files=_files("notes.txt", b"Hallo Decyra Welt", "text/plain"),
    )
    assert r.status_code == 200, r.text  # upload NOT crashed by provider outage
    assert r.json()["embedding_status"] == "failed"
    assert db.execute(text("SELECT count(*) FROM document_chunks")).scalar_one() == 0
    assert db.execute(text("SELECT count(*) FROM documents")).scalar_one() == 1


@pytest.mark.asyncio
async def test_list_documents_returns_embedding_status(client, db, make_token, settings_override, stub_embed):
    settings_override()
    seed_org_with_owner(db, USER_A, "a@firma.de")
    token = make_token(sub=USER_A, email="a@firma.de")
    await client.post(
        "/documents", headers=_auth(token),
        files=_files("notes.txt", b"Hallo Decyra Welt", "text/plain"),
    )
    lst = await client.get("/documents", headers=_auth(token))
    assert lst.status_code == 200
    assert lst.json()[0]["embedding_status"] == "done"
