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

import json
import time

import litellm
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
    resp, model: str, conversation_id: str, status: dict | None = None
) -> dict:
    """Shape the litellm response as an OpenAI chat.completion, plus our
    conversation_id and (4.5a) the Decyra PII/routing status block."""
    choice = resp.choices[0]
    out = {
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
    if status is not None:
        out["decyra"] = status
    return out


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
    db: Connection,
    workspace_id: str,
    user_id: str,
    title: str,
    pii_mode: str | None = None,
) -> str:
    """Create a conversation. ``pii_mode`` is the per-chat override (Task 4.5b);
    None means inherit the workspace default."""
    return str(
        db.execute(
            text(
                "INSERT INTO conversations (workspace_id, user_id, title, "
                "pii_mode) VALUES (:w, :u, :t, :pm) RETURNING id"
            ),
            {"w": workspace_id, "u": user_id, "t": title, "pm": pii_mode},
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


def set_conversation_mode(
    db: Connection, conversation_id: str, pii_mode: str
) -> None:
    """Persist a per-chat PII-mode toggle onto an existing conversation
    (Task 4.5b). New conversations get it at creation instead."""
    db.execute(
        text("UPDATE conversations SET pii_mode = :pm WHERE id = :c"),
        {"pm": pii_mode, "c": conversation_id},
    )


def insert_audit_event(
    db: Connection,
    workspace_id: str,
    user_id: str,
    model: str,
    request_text: str,
    response_text: str,
    routed_to: str,
    pii_detected: bool = False,
    *,
    pii_mode: str = "sovereign",
    anonymized: bool = False,
) -> None:
    """Append an audit event. The BEFORE INSERT trigger (3.1/4.5b) chains the
    hashes and binds ``pii_mode`` + ``anonymized`` into the canonical v2 string.
    ``model``/``routed_to`` are the EFFECTIVE model/provider (after any sovereign
    reroute). ``pii_detected`` is true whenever PII was actually detected —
    including strict mode, where it was anonymised rather than rerouted (a
    degraded Presidio-down reroute still leaves it false). ``request_text`` /
    ``response_text`` are what ACTUALLY transited to the provider: the original
    in sovereign mode (real text went to the EU model), the anonymised text in
    strict mode (only placeholders left the house) — the chain attests both."""
    db.execute(
        text(
            "INSERT INTO audit_events (workspace_id, user_id, model, "
            "request_text, response_text, routed_to, pii_detected, "
            "pii_mode, anonymized) "
            "VALUES (:w, :u, :m, :req, :res, :routed, :pii, :mode, :anon)"
        ),
        {
            "w": workspace_id,
            "u": user_id,
            "m": model,
            "req": request_text,
            "res": response_text,
            "routed": routed_to,
            "pii": pii_detected,
            "mode": pii_mode,
            "anon": anonymized,
        },
    )


def persistable_messages(messages: list[dict]) -> list[dict]:
    return [m for m in messages if m.get("role") in _PERSISTABLE_ROLES]


# --- Streaming (Task 4.4) ----------------------------------------------
#
# The streaming path collects the provider chunks, then rebuilds the SAME
# ModelResponse shape the non-streaming path uses (content + usage) via
# litellm.stream_chunk_builder, so the entire persist+audit block is
# reused — the 4.3 compliance guarantee is inherited, not rebuilt.


def rebuild_stream_response(
    chunks: list, llm_input: list[dict]
) -> tuple[str, int, int] | None:
    """Reconstruct (content, prompt_tokens, completion_tokens) from the
    collected streaming chunks. Returns None when nothing usable was
    collected (no chunks, or empty content) — the caller then persists
    nothing (matches 4.3, where a provider error before the write block
    writes nothing). Token counts come from litellm's calculate_usage
    (tokenizer-based when the provider omits them in the stream)."""
    if not chunks:
        return None
    resp = litellm.stream_chunk_builder(chunks, messages=llm_input)
    if resp is None:
        return None
    content = resp.choices[0].message.content or ""
    if not content:
        return None
    usage = resp.usage
    return content, usage.prompt_tokens, usage.completion_tokens


def persist_stream_turn(
    db_write: Connection,
    workspace_id: str,
    user_id: str,
    existing_cid: str | None,
    new_messages: list[dict],
    chunks: list,
    llm_input: list[dict],
    *,
    model: str,
    provider: str,
    cost_input: float,
    cost_output: float,
    pii_detected: bool = False,
    pii_mode: str = "sovereign",
    anonymized: bool = False,
    anonymizer=None,
    audit_request: str | None = None,
    conv_pii_mode: str | None = None,
) -> str | None:
    """Persist a streamed turn + write the audit event — the same write block as
    the 4.3 non-streaming path, sourcing the assistant content/usage from the
    collected chunks. ``model``/``provider``/prices are the EFFECTIVE model.

    Strict-mode divergence (Task 4.5b): the collected chunks carry the provider
    (anonymised) content. ``messages`` stores the REAL, de-anonymised text
    (tenant data); the AUDIT stores what actually transited — the anonymised
    request (``audit_request``) and the anonymised provider response. ``pii_mode``
    is the governing mode; ``conv_pii_mode`` is the per-chat override persisted
    on a freshly-created conversation. ``anonymizer`` (or None) de-anonymises the
    stored assistant content. Returns the conversation id, or None if nothing
    was collected (skip)."""
    rebuilt = rebuild_stream_response(chunks, llm_input)
    if rebuilt is None:
        return None
    provider_content, prompt_tokens, completion_tokens = rebuilt
    stored_content = (
        anonymizer.deanonymize(provider_content)
        if anonymizer is not None
        else provider_content
    )
    cost = compute_cost(prompt_tokens, completion_tokens, cost_input, cost_output)

    if existing_cid is None:
        cid = create_conversation(
            db_write, workspace_id, user_id, derive_title(new_messages), conv_pii_mode
        )
    else:
        cid = existing_cid
        if conv_pii_mode is not None:
            set_conversation_mode(db_write, cid, conv_pii_mode)
    for m in persistable_messages(new_messages):
        insert_message(db_write, cid, workspace_id, m["role"], m["content"])
    insert_message(
        db_write,
        cid,
        workspace_id,
        "assistant",
        stored_content,
        model=model,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        cost=cost,
    )
    touch_conversation(db_write, cid)
    insert_audit_event(
        db_write,
        workspace_id,
        user_id,
        model,
        audit_request if audit_request is not None else audit_request_text(new_messages),
        provider_content,
        provider,
        pii_detected,
        pii_mode=pii_mode,
        anonymized=anonymized,
    )
    return cid


# --- OpenAI-compatible SSE serialisation --------------------------------


def _chunk_dict(chunk, conversation_id: str | None = None) -> dict:
    """Shape one streaming chunk as an OpenAI chat.completion.chunk. Pure
    attribute access, so it works for real litellm chunks AND the test
    stub's SimpleNamespace chunks. conversation_id rides as an extra
    top-level field (OpenAI clients ignore unknown fields)."""
    choice = chunk.choices[0]
    delta = getattr(choice, "delta", None)
    content = getattr(delta, "content", None) if delta is not None else None
    role = getattr(delta, "role", None) if delta is not None else None
    out_delta: dict = {}
    if role is not None:
        out_delta["role"] = role
    if content is not None:
        out_delta["content"] = content
    d: dict = {
        "id": getattr(chunk, "id", None),
        "object": "chat.completion.chunk",
        "created": getattr(chunk, "created", None),
        "model": getattr(chunk, "model", None),
        "choices": [
            {
                "index": getattr(choice, "index", 0),
                "delta": out_delta,
                "finish_reason": getattr(choice, "finish_reason", None),
            }
        ],
    }
    if conversation_id is not None:
        d["conversation_id"] = conversation_id
    return d


def sse_chunk(chunk, conversation_id: str | None = None) -> str:
    return f"data: {json.dumps(_chunk_dict(chunk, conversation_id))}\n\n"


def sse_final(conversation_id: str | None, model: str) -> str:
    """Terminal chunk: empty delta + finish_reason stop, carrying the
    authoritative conversation_id (the only place a NEW conversation's id
    is known — it is created during persist, after the provider stream)."""
    d = {
        "id": "chatcmpl-decyra",
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
        "conversation_id": conversation_id,
    }
    return f"data: {json.dumps(d)}\n\n"


def sse_content(text_piece: str, conversation_id: str | None = None) -> str:
    """A content-only OpenAI chunk. Used by the strict streaming path to emit
    DE-ANONYMISED text (the provider's raw placeholder chunks are not forwarded;
    they pass through the StreamDeanonymizer first)."""
    d: dict = {
        "id": "chatcmpl-decyra",
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": None,
        "choices": [
            {"index": 0, "delta": {"content": text_piece}, "finish_reason": None}
        ],
    }
    if conversation_id is not None:
        d["conversation_id"] = conversation_id
    return f"data: {json.dumps(d)}\n\n"


def sse_error(message: str, conversation_id: str | None = None) -> str:
    d: dict = {"error": {"message": message, "type": "stream_error"}}
    if conversation_id is not None:
        d["conversation_id"] = conversation_id
    return f"data: {json.dumps(d)}\n\n"


def sse_done() -> str:
    return "data: [DONE]\n\n"


def pii_status(
    *,
    pii_detected: bool,
    pii_check: str,
    routed_to: str,
    effective_model: str,
    anonymized: bool = False,
    pii_mode: str = "sovereign",
) -> dict:
    """The Decyra PII/routing status block. ``anonymized`` is true only when
    strict mode actually masked PII before the cloud call (Task 4.5b);
    ``pii_mode`` is the mode that governed this request."""
    return {
        "pii_detected": pii_detected,
        "pii_check": pii_check,
        "routed_to": routed_to,
        "effective_model": effective_model,
        "anonymized": anonymized,
        "pii_mode": pii_mode,
    }


def sse_status(status: dict) -> str:
    """First SSE event: an OpenAI-shaped chunk with an empty delta carrying
    the Decyra status. Emitted BEFORE the provider chunks (the PII/routing
    decision is known pre-stream) so the client can show the notice at once."""
    d = {
        "id": "chatcmpl-decyra",
        "object": "chat.completion.chunk",
        "choices": [{"index": 0, "delta": {}, "finish_reason": None}],
        "decyra": status,
    }
    return f"data: {json.dumps(d)}\n\n"
