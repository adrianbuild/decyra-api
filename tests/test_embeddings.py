# tests/test_embeddings.py
"""Task 5.2 — embedding service + idempotent orchestrator tests."""
from __future__ import annotations

import logging
from contextlib import contextmanager

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection

from app import embeddings
from app.config import get_settings

_LOG_CTX = {"workspace_id": "ws-test", "user_id": "u-test"}


def _settings():
    return get_settings()


@contextmanager
def _decyra_errors_visible():
    """alembic's fileConfig (conftest session setup) disables existing loggers,
    so decyra.errors drops records and caplog can't see them — a test-only
    artifact (alembic runs as its own process in prod, so the API's error
    logger stays live). Re-enable for the duration of the assertion."""
    lg = logging.getLogger("decyra.errors")
    prev = lg.disabled
    lg.disabled = False
    try:
        yield
    finally:
        lg.disabled = prev


# --- embed_texts -------------------------------------------------------

def test_embed_texts_returns_1024_dim(stub_embed):
    vecs = embeddings.embed_texts(["a", "b"], _settings(), log_ctx=_LOG_CTX)
    assert len(vecs) == 2
    assert all(len(v) == 1024 for v in vecs)


def test_embed_texts_batches_over_max_batch(stub_embed):
    inputs = [f"chunk-{i}" for i in range(embeddings.MAX_BATCH + 5)]
    vecs = embeddings.embed_texts(inputs, _settings(), log_ctx=_LOG_CTX)
    assert len(vecs) == embeddings.MAX_BATCH + 5
    # two calls: a full batch then the remainder (Invariant: batched, not 1/call)
    assert [len(b) for b in stub_embed.calls] == [embeddings.MAX_BATCH, 5]


def test_embed_texts_empty_returns_empty(stub_embed):
    assert embeddings.embed_texts([], _settings(), log_ctx=_LOG_CTX) == []
    assert stub_embed.calls == []  # no provider call on empty input


def test_embed_texts_provider_failure_raises(stub_embed, caplog):
    stub_embed.state["fail"] = RuntimeError("mistral down")
    with _decyra_errors_visible(), caplog.at_level(logging.ERROR, logger="decyra.errors"):
        with pytest.raises(embeddings.EmbeddingError):
            embeddings.embed_texts(["a"], _settings(), log_ctx=_LOG_CTX)
    assert any(
        "embedding failed" in r.getMessage() and "transient=False" in r.getMessage()
        for r in caplog.records
    )


# --- migration constraint ---------------------------------------------

def test_embedding_status_check_constraint(db: Connection):
    org = db.execute(
        text("INSERT INTO organizations (name) VALUES ('O') RETURNING id")
    ).scalar_one()
    ws = db.execute(
        text("INSERT INTO workspaces (organization_id, name) VALUES (:o,'W') "
             "RETURNING id"), {"o": org},
    ).scalar_one()
    user = db.execute(
        text("INSERT INTO users (email) VALUES ('e@x.de') RETURNING id")
    ).scalar_one()
    with pytest.raises(Exception) as exc:
        with db.begin_nested():
            db.execute(
                text(
                    "INSERT INTO documents (workspace_id, filename, uploaded_by, "
                    "storage_key, mime_type, size_bytes, extracted_text, "
                    "extraction_status, embedding_status) VALUES "
                    "(:w,'f',:u,'k','text/plain',0,'','ok','bogus')"
                ),
                {"w": ws, "u": user},
            )
    assert "embedding_status" in str(exc.value).lower() or "check" in str(exc.value).lower()


# --- embed_document (idempotent orchestrator) --------------------------

def _seed_doc(db: Connection, *, status: str = "ok", body: str = "") -> tuple[str, str]:
    """As postgres: org + workspace + user + one document. Returns (ws, doc_id)."""
    org = db.execute(
        text("INSERT INTO organizations (name) VALUES ('O') RETURNING id")
    ).scalar_one()
    ws = db.execute(
        text("INSERT INTO workspaces (organization_id, name) VALUES (:o,'W') "
             "RETURNING id"), {"o": org},
    ).scalar_one()
    user = db.execute(
        text("INSERT INTO users (email) VALUES ('e@x.de') RETURNING id")
    ).scalar_one()
    doc = db.execute(
        text(
            "INSERT INTO documents (workspace_id, filename, uploaded_by, "
            "storage_key, mime_type, size_bytes, extracted_text, "
            "extraction_status) VALUES (:w,'f.txt',:u,'k','text/plain',:n,:t,:s) "
            "RETURNING id"
        ),
        {"w": ws, "u": user, "n": len(body), "t": body, "s": status},
    ).scalar_one()
    return str(ws), str(doc)


def _txn_factory(db: Connection):
    @contextmanager
    def _open():
        yield db
    return _open


def test_embed_document_persists_chunks_and_marks_done(db, stub_embed):
    body = " ".join(f"w{i}" for i in range(1500))  # multiple chunks
    ws, doc = _seed_doc(db, status="ok", body=body)

    result = embeddings.embed_document(
        _txn_factory(db), workspace_id=ws, document_id=doc,
        extracted_text=body, extraction_status="ok",
        settings=_settings(), log_ctx=_LOG_CTX,
    )
    assert result == "done"

    rows = db.execute(
        text("SELECT workspace_id, chunk_index, embedding IS NOT NULL AS has_vec "
             "FROM document_chunks WHERE document_id = :d ORDER BY chunk_index"),
        {"d": doc},
    ).all()
    from app.chunking import chunk_text
    assert len(rows) == len(chunk_text(body))
    assert all(str(r.workspace_id) == ws and r.has_vec for r in rows)
    assert [r.chunk_index for r in rows] == list(range(len(rows)))

    status = db.execute(
        text("SELECT embedding_status FROM documents WHERE id = :d"), {"d": doc}
    ).scalar_one()
    assert status == "done"


def test_embed_document_idempotent_no_duplicate_chunks(db, stub_embed):
    body = " ".join(f"w{i}" for i in range(1500))
    ws, doc = _seed_doc(db, status="ok", body=body)
    kw = dict(workspace_id=ws, document_id=doc, extracted_text=body,
              extraction_status="ok", settings=_settings(), log_ctx=_LOG_CTX)

    embeddings.embed_document(_txn_factory(db), **kw)
    first = db.execute(
        text("SELECT count(*) FROM document_chunks WHERE document_id=:d"), {"d": doc}
    ).scalar_one()
    embeddings.embed_document(_txn_factory(db), **kw)  # re-trigger / retry
    second = db.execute(
        text("SELECT count(*) FROM document_chunks WHERE document_id=:d"), {"d": doc}
    ).scalar_one()
    assert first == second and first > 0


def test_embed_document_no_text_skipped(db, stub_embed):
    ws, doc = _seed_doc(db, status="no_text", body="")
    result = embeddings.embed_document(
        _txn_factory(db), workspace_id=ws, document_id=doc,
        extracted_text="", extraction_status="no_text",
        settings=_settings(), log_ctx=_LOG_CTX,
    )
    assert result == "skipped"
    assert stub_embed.calls == []  # no_text → nothing sent to Mistral
    n = db.execute(
        text("SELECT count(*) FROM document_chunks WHERE document_id=:d"), {"d": doc}
    ).scalar_one()
    assert n == 0
    status = db.execute(
        text("SELECT embedding_status FROM documents WHERE id=:d"), {"d": doc}
    ).scalar_one()
    assert status == "skipped"


def test_embed_document_provider_failure_marks_failed_no_crash(db, stub_embed, caplog):
    body = "Hallo Decyra Welt"
    ws, doc = _seed_doc(db, status="ok", body=body)
    stub_embed.state["fail"] = RuntimeError("mistral down")

    with _decyra_errors_visible(), caplog.at_level(logging.ERROR, logger="decyra.errors"):
        result = embeddings.embed_document(  # must NOT raise
            _txn_factory(db), workspace_id=ws, document_id=doc,
            extracted_text=body, extraction_status="ok",
            settings=_settings(), log_ctx=_LOG_CTX,
        )
    assert result == "failed"
    n = db.execute(
        text("SELECT count(*) FROM document_chunks WHERE document_id=:d"), {"d": doc}
    ).scalar_one()
    assert n == 0
    status = db.execute(
        text("SELECT embedding_status FROM documents WHERE id=:d"), {"d": doc}
    ).scalar_one()
    assert status == "failed"
    assert any("embedding failed" in r.getMessage() for r in caplog.records)


def test_embed_document_recovers_after_failed_attempt(db, stub_embed):
    body = " ".join(f"w{i}" for i in range(800))
    ws, doc = _seed_doc(db, status="ok", body=body)
    kw = dict(workspace_id=ws, document_id=doc, extracted_text=body,
              extraction_status="ok", settings=_settings(), log_ctx=_LOG_CTX)

    stub_embed.state["fail"] = RuntimeError("mistral down")
    assert embeddings.embed_document(_txn_factory(db), **kw) == "failed"
    assert db.execute(
        text("SELECT count(*) FROM document_chunks WHERE document_id=:d"), {"d": doc}
    ).scalar_one() == 0

    stub_embed.state["fail"] = None  # provider recovers
    assert embeddings.embed_document(_txn_factory(db), **kw) == "done"
    from app.chunking import chunk_text
    n = db.execute(
        text("SELECT count(*) FROM document_chunks WHERE document_id=:d"), {"d": doc}
    ).scalar_one()
    assert n == len(chunk_text(body)) and n > 0
    assert db.execute(
        text("SELECT embedding_status FROM documents WHERE id=:d"), {"d": doc}
    ).scalar_one() == "done"
