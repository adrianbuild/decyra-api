# app/embeddings.py
"""Task 5.2 — Mistral embeddings for RAG.

mistral-embed (1024-dim) over the EU-DEFAULT endpoint (api.mistral.ai;
sovereign per Decyra's EU-residency requirement — NEVER set a US
MISTRAL_API_BASE). Same external-service discipline as 4.6: litellm handles
per-call timeout + transient retry; any remaining failure is classified
against the 4.6 taxonomy, logged to `decyra.errors` (NEVER the audit chain),
and surfaced as EmbeddingError. There is no sovereign embedding fallback model,
so the caller (embed_document) marks the document 'failed' and does NOT crash
the upload — the document row already committed.
"""
from __future__ import annotations

import logging
from typing import Callable, ContextManager

import litellm
from sqlalchemy import text
from sqlalchemy.engine import Connection

from app.chunking import chunk_text
from app.llm_call import FALLBACK_ERRORS

errors_logger = logging.getLogger("decyra.errors")

EMBED_MODEL = "mistral/mistral-embed"
EMBED_DIM = 1024
MAX_BATCH = 64  # chunks per litellm.embedding call (Mistral `input` array)

OpenTxn = Callable[[], ContextManager[Connection]]


class EmbeddingError(Exception):
    """The embedding provider failed (after litellm's transient retries).
    embed_document marks the document embedding_status='failed' and does NOT
    crash the request."""


def _vec_literal(vec: list[float]) -> str:
    """pgvector text literal '[a,b,...]' for the `::vector` cast (no pgvector
    Python dependency)."""
    return "[" + ",".join(repr(float(x)) for x in vec) + "]"


def _set_workspace(db: Connection, workspace_id: object) -> None:
    """Transaction-local RLS GUC (bound param, never an f-string). Inlined here
    to avoid a circular import with app.main."""
    db.execute(
        text("SELECT set_config('app.current_workspace_id', :ws, true)"),
        {"ws": str(workspace_id)},
    )


def embed_texts(inputs, settings, *, log_ctx) -> list[list[float]]:
    """Embed a list of strings → list of 1024-dim vectors, batched over the
    Mistral `input` array (<= MAX_BATCH per call). Any provider failure is
    logged to decyra.errors and re-raised as EmbeddingError."""
    vectors: list[list[float]] = []
    for start in range(0, len(inputs), MAX_BATCH):
        batch = inputs[start:start + MAX_BATCH]
        try:
            resp = litellm.embedding(
                model=EMBED_MODEL,
                input=batch,
                timeout=settings.request_timeout_seconds,
                num_retries=settings.num_retries,
            )
        except Exception as e:  # noqa: BLE001 — never crash on a provider quirk
            transient = isinstance(e, FALLBACK_ERRORS)
            errors_logger.error(
                "embedding failed model=%s error=%s transient=%s workspace_id=%s",
                EMBED_MODEL, type(e).__name__, transient,
                log_ctx.get("workspace_id"),
            )
            raise EmbeddingError(str(e)) from e
        vectors.extend(d["embedding"] for d in resp.data)
    return vectors
