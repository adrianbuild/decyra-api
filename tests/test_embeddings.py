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
