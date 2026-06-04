"""Task 4.3 — chat proxy logic: cost, title, audit text, OpenAI response
shape, and the conversation/message persistence helpers.

The actual litellm.completion call lives in main.py (so tests patch
``litellm.completion`` directly). This module is pure helpers + SQL.

Invariants:
- messages.workspace_id is ALWAYS derived from the parent conversation's
  workspace (passed in by the caller), never from the request — that is
  what keeps the denormalised column consistent.
- Every chat call persists messages AND writes an audit event in the same
  transaction; there is no path that skips the audit.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Connection

# Roles we accept inbound and store.
_PERSISTABLE_ROLES = {"system", "user", "assistant"}


def compute_cost(
    prompt_tokens: int,
    completion_tokens: int,
    cost_input: float,
    cost_output: float,
) -> float:
    """USD. Prices in the models table are per 1M tokens."""
    return (
        prompt_tokens / 1_000_000 * float(cost_input)
        + completion_tokens / 1_000_000 * float(cost_output)
    )


def derive_title(messages: list[dict]) -> str:
    """First user message, first non-empty line, truncated to 60 chars.
    Robust fallback so the conversation list never looks broken."""
    for m in messages:
        if m.get("role") == "user":
            first_line = (m.get("content") or "").strip().splitlines()
            text_line = first_line[0].strip() if first_line else ""
            if text_line:
                return text_line[:60]
    return "Neue Unterhaltung"


def audit_request_text(messages: list[dict]) -> str:
    """The NEW user messages of this turn, concatenated. NOT the loaded
    history (already captured in earlier audit events) — but the full new
    input, never a partial slice (that would be a forensic gap)."""
    parts = [
        (m.get("content") or "")
        for m in messages
        if m.get("role") == "user"
    ]
    return "\n\n".join(parts)


def build_openai_response(
    resp, model: str, conversation_id: str
) -> dict:
    """Shape the litellm response as an OpenAI chat.completion, plus our
    conversation_id extension."""
    choice = resp.choices[0]
    return {
        "id": resp.id,
        "object": "chat.completion",
        "created": resp.created,
        "model": resp.model or model,
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": choice.message.role,
                    "content": choice.message.content,
                },
                "finish_reason": choice.finish_reason,
            }
        ],
        "usage": {
            "prompt_tokens": resp.usage.prompt_tokens,
            "completion_tokens": resp.usage.completion_tokens,
            "total_tokens": resp.usage.total_tokens,
        },
        "conversation_id": conversation_id,
    }


# --- DB helpers ---------------------------------------------------------


def load_conversation_owner(
    db: Connection, conversation_id: str
) -> str | None:
    """Owner user_id of a conversation, RLS-scoped to the current
    workspace. None if it doesn't exist in this workspace."""
    row = db.execute(
        text("SELECT user_id FROM conversations WHERE id = :c"),
        {"c": conversation_id},
    ).one_or_none()
    return None if row is None else str(row.user_id)


def load_history(db: Connection, conversation_id: str) -> list[dict]:
    rows = db.execute(
        text(
            "SELECT role, content FROM messages "
            "WHERE conversation_id = :c ORDER BY created_at ASC, id ASC"
        ),
        {"c": conversation_id},
    ).all()
    return [{"role": r.role, "content": r.content} for r in rows]


def create_conversation(
    db: Connection, workspace_id: str, user_id: str, title: str
) -> str:
    return str(
        db.execute(
            text(
                "INSERT INTO conversations (workspace_id, user_id, title) "
                "VALUES (:w, :u, :t) RETURNING id"
            ),
            {"w": workspace_id, "u": user_id, "t": title},
        ).scalar_one()
    )


def insert_message(
    db: Connection,
    conversation_id: str,
    workspace_id: str,
    role: str,
    content: str,
    *,
    model: str | None = None,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
    cost: float | None = None,
) -> None:
    """Insert a message. workspace_id MUST be the parent conversation's
    workspace (passed by the caller) — never request-derived."""
    db.execute(
        text(
            "INSERT INTO messages (conversation_id, workspace_id, role, "
            "content, model, prompt_tokens, completion_tokens, cost) "
            "VALUES (:c, :w, :r, :content, :m, :pt, :ct, :cost)"
        ),
        {
            "c": conversation_id,
            "w": workspace_id,
            "r": role,
            "content": content,
            "m": model,
            "pt": prompt_tokens,
            "ct": completion_tokens,
            "cost": cost,
        },
    )


def touch_conversation(db: Connection, conversation_id: str) -> None:
    db.execute(
        text("UPDATE conversations SET updated_at = now() WHERE id = :c"),
        {"c": conversation_id},
    )


def insert_audit_event(
    db: Connection,
    workspace_id: str,
    user_id: str,
    model: str,
    request_text: str,
    response_text: str,
    routed_to: str,
) -> None:
    """Append an audit event. The BEFORE INSERT trigger (3.1) chains the
    hashes. Same template as tests/_helpers.insert_event."""
    db.execute(
        text(
            "INSERT INTO audit_events (workspace_id, user_id, model, "
            "request_text, response_text, routed_to) "
            "VALUES (:w, :u, :m, :req, :res, :routed)"
        ),
        {
            "w": workspace_id,
            "u": user_id,
            "m": model,
            "req": request_text,
            "res": response_text,
            "routed": routed_to,
        },
    )


def persistable_messages(messages: list[dict]) -> list[dict]:
    return [m for m in messages if m.get("role") in _PERSISTABLE_ROLES]
