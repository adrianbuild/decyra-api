# app/retrieval.py
"""Task 5.3 — RLS-scoped vector retrieval over document_chunks for RAG.

Embeds the query via mistral-embed (EU; same EU-residency boundary as 5.2 — in
strict mode the raw query still transits to Mistral-EU for embedding, an open
architecture point documented in PROGRESS) and runs an EXACT cosine search
(`<=>`, NO ivfflat/hnsw index — exact = no recall loss, fine at MVP scale; an
index is a later migration-only add). Tenant isolation is by RLS
(document_chunks_isolation) AND an explicit WHERE workspace_id
(defense-in-depth, CLAUDE.md). The retrieved context is PROVIDER-ONLY: the
caller sends + audits it but never persists it as a message.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.engine import Connection

from app.config import Settings
from app.embeddings import embed_texts, vec_literal

_CONTEXT_HEADER = (
    "Kontext aus den Unternehmensdokumenten des Nutzers. Beantworte die Frage, "
    "wenn möglich, auf Basis dieses Kontexts und nenne die Quelle(n) in Klammern. "
    "Wenn der Kontext die Frage nicht abdeckt, sage das offen."
)


@dataclass(frozen=True)
class RetrievedChunk:
    content: str
    filename: str
    chunk_index: int
    similarity: float


def latest_user_query(new_messages: list[dict]) -> str:
    """The current turn's question = the last user-role message content ("" if none)."""
    for m in reversed(new_messages):
        if m.get("role") == "user" and m.get("content"):
            return m["content"]
    return ""


def retrieve_chunks(
    db: Connection, workspace_id: object, query: str, settings: Settings, *, log_ctx: dict
) -> list[RetrievedChunk]:
    """Top-k cosine search for `query` over the workspace's chunks. Returns up
    to settings.rag_top_k chunks with similarity >= settings.rag_similarity_threshold,
    most similar first. Short-circuits to [] (NO embed call) for a blank query or
    a workspace with no embedded chunks. The caller MUST have run
    set_workspace_context(db, workspace_id) so RLS scopes the search. Raises
    EmbeddingError if the query embedding fails (the caller decides to degrade)."""
    if not query.strip():
        return []
    has_chunks = db.execute(
        text(
            "SELECT 1 FROM document_chunks "
            "WHERE workspace_id = :w AND embedding IS NOT NULL LIMIT 1"
        ),
        {"w": str(workspace_id)},
    ).first()
    if has_chunks is None:
        return []

    qvec = vec_literal(embed_texts([query], settings, log_ctx=log_ctx)[0])
    rows = db.execute(
        text(
            "SELECT dc.content, d.filename, dc.chunk_index, "
            "1 - (dc.embedding <=> (:q)::vector) AS similarity "
            "FROM document_chunks dc "
            "JOIN documents d ON d.id = dc.document_id "
            "WHERE dc.workspace_id = :w AND dc.embedding IS NOT NULL "
            "ORDER BY dc.embedding <=> (:q)::vector "
            "LIMIT :k"
        ),
        {"q": qvec, "w": str(workspace_id), "k": settings.rag_top_k},
    ).all()
    return [
        RetrievedChunk(r.content, r.filename, r.chunk_index, float(r.similarity))
        for r in rows
        if float(r.similarity) >= settings.rag_similarity_threshold
    ]


def chunk_join(chunks: list[RetrievedChunk]) -> str:
    """Raw chunk text for the sovereign routing decision (Invariant 3)."""
    return "\n".join(c.content for c in chunks)


def build_context_message(chunks: list[RetrievedChunk]) -> dict:
    """A single PROVIDER-ONLY system message: header + source-tagged chunks. Caller must only invoke with a non-empty chunks list (skip the context message when retrieval returns [])."""
    parts = [f"[Quelle: {c.filename} #{c.chunk_index}]\n{c.content}" for c in chunks]
    return {"role": "system", "content": _CONTEXT_HEADER + "\n\n" + "\n\n".join(parts)}
