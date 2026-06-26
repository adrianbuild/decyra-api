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
import math
from typing import Callable, ContextManager

import litellm
from sqlalchemy import text
from sqlalchemy.engine import Connection

from app.chunking import chunk_text
from app.config import Settings
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


def embed_texts(
    inputs: list[str], settings: Settings, *, log_ctx: dict
) -> list[list[float]]:
    """Embed a list of strings → list of 1024-dim vectors, batched over the
    Mistral `input` array (<= MAX_BATCH per call). Empty input → [] with no
    provider call. Any provider failure (or a non-finite component returned by
    the provider) is logged to decyra.errors and re-raised as EmbeddingError.

    The non-finite guard lives HERE (not in _vec_literal): the orchestrator's
    INSERT loop runs outside the EmbeddingError-handled region, so validating
    at the provider boundary keeps a NaN/Inf quirk attributed to embedding
    (document marked 'failed') instead of surfacing as an opaque pgvector
    INSERT error later."""
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
        for d in resp.data:
            vec = d["embedding"]
            if not all(math.isfinite(x) for x in vec):
                errors_logger.error(
                    "embedding non-finite model=%s workspace_id=%s",
                    EMBED_MODEL, log_ctx.get("workspace_id"),
                )
                raise EmbeddingError("embedding contained a non-finite component")
            vectors.append(vec)
    return vectors


def _set_status(open_txn: OpenTxn, workspace_id, document_id, status: str) -> None:
    with open_txn() as db:
        _set_workspace(db, workspace_id)
        db.execute(
            text("UPDATE documents SET embedding_status = :s WHERE id = :d"),
            {"s": status, "d": document_id},
        )


def embed_document(
    open_txn: OpenTxn,
    *,
    workspace_id,
    document_id,
    extracted_text: str,
    extraction_status: str,
    settings,
    log_ctx,
) -> str:
    """Idempotently embed one document into document_chunks and set its
    embedding_status. Returns the final status.

    - no_text (or blank text) -> 'skipped' (nothing to embed; Invariant 4).
    - provider failure -> 'failed' (logged, NOT raised: the upload stands;
      Invariant 2). The document can be re-embedded later.
    - success -> 'done'.

    Idempotent: existing chunks for the document are DELETEd before insert, so a
    re-trigger or retry never duplicates (Invariant 4). Every chunk inherits the
    document's workspace_id (Invariant 1)."""
    if extraction_status == "no_text" or not extracted_text.strip():
        _set_status(open_txn, workspace_id, document_id, "skipped")
        return "skipped"

    chunks = chunk_text(extracted_text)
    try:
        vectors = embed_texts(chunks, settings, log_ctx=log_ctx)
    except EmbeddingError:
        _set_status(open_txn, workspace_id, document_id, "failed")
        return "failed"

    with open_txn() as db:
        _set_workspace(db, workspace_id)
        db.execute(
            text("DELETE FROM document_chunks WHERE document_id = :d"),
            {"d": document_id},
        )
        for idx, (content, vec) in enumerate(zip(chunks, vectors)):
            db.execute(
                text(
                    "INSERT INTO document_chunks "
                    "(document_id, workspace_id, content, chunk_index, embedding) "
                    "VALUES (:d, :w, :c, :i, (:e)::vector)"
                ),
                {"d": document_id, "w": workspace_id, "c": content,
                 "i": idx, "e": _vec_literal(vec)},
            )
        db.execute(
            text("UPDATE documents SET embedding_status = 'done' WHERE id = :d"),
            {"d": document_id},
        )
    return "done"
