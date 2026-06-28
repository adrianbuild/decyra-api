"""Task 5B.1 — chat attachment storage helpers.

The extracted TEXT of files attached to chat messages is stored here,
NOT in the RAG/documents system. It is re-injected into the LLM context
on every turn by the chat layer (a later task).

Invariant: workspace_id is ALWAYS passed in by the caller (derived from
the parent conversation), never request-derived — same rule as chat.py.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.engine import Connection


@dataclass
class Attachment:
    id: str
    filename: str
    mime_type: str
    size_bytes: int
    extracted_text: str
    created_at: datetime


def insert_attachment(
    db: Connection,
    conversation_id: str,
    workspace_id: str,
    filename: str,
    mime_type: str,
    size_bytes: int,
    extracted_text: str,
) -> str:
    """Insert a chat attachment row and return its new UUID as a string.

    workspace_id MUST be the parent conversation's workspace (passed by
    the caller) — never request-derived.
    """
    new_id = str(
        db.execute(
            text(
                "INSERT INTO chat_attachments "
                "(workspace_id, conversation_id, filename, mime_type, "
                "size_bytes, extracted_text) "
                "VALUES (:w, :c, :fn, :mt, :sb, :et) "
                "RETURNING id"
            ),
            {
                "w": workspace_id,
                "c": conversation_id,
                "fn": filename,
                "mt": mime_type,
                "sb": size_bytes,
                "et": extracted_text,
            },
        ).scalar_one()
    )
    return new_id


def load_attachments(db: Connection, conversation_id: str) -> list[Attachment]:
    """Return all attachments for a conversation, ordered by created_at ASC, id ASC.

    RLS is enforced by the session's app.current_workspace_id setting; the
    caller must have set it before calling this function.
    """
    rows = db.execute(
        text(
            "SELECT id, filename, mime_type, size_bytes, extracted_text, created_at "
            "FROM chat_attachments "
            "WHERE conversation_id = :c "
            "ORDER BY created_at ASC, id ASC"
        ),
        {"c": conversation_id},
    ).all()
    return [
        Attachment(
            id=str(r.id),
            filename=r.filename,
            mime_type=r.mime_type,
            size_bytes=r.size_bytes,
            extracted_text=r.extracted_text,
            created_at=r.created_at,
        )
        for r in rows
    ]
