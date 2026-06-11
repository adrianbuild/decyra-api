"""Task 4.4 — streaming (SSE). The compliance guarantee under streaming
(full answer in the hash chain), the abort cases, cost/token, multi-turn,
and the conversation_id vehicle. LLM + stream_chunk_builder are stubbed
(see conftest.stub_llm); the abort is a stub generator that raises after
N chunks."""

from __future__ import annotations

import json
import types

import pytest
from sqlalchemy import text

from app import chat
from app.audit import verify_workspace_chain
from tests._helpers import seed_org_with_owner

USER_A = "11111111-1111-1111-1111-111111111111"
MODEL = "test-model"


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _seed_model(
    db, name: str = MODEL, cost_input: float = 5.0, cost_output: float = 30.0
) -> None:
    db.execute(
        text(
            "INSERT INTO models (name, provider, cost_input, cost_output, "
            "eu_hosted, sovereign_eligible, tier_min, enabled) "
            "VALUES (:n, 'openai', :ci, :co, false, false, 'free', true)"
        ),
        {"n": name, "ci": cost_input, "co": cost_output},
    )


def _events(body: str) -> list[str]:
    """The payload of each `data: ` SSE line."""
    out = []
    for block in body.split("\n\n"):
        block = block.strip()
        if block.startswith("data: "):
            out.append(block[len("data: ") :])
    return out


def _content(events: list[str]) -> str:
    out = ""
    for e in events:
        if e == "[DONE]":
            continue
        for ch in json.loads(e).get("choices", []):
            out += ch.get("delta", {}).get("content") or ""
    return out


def _conversation_id(events: list[str]) -> str | None:
    cid = None
    for e in events:
        if e == "[DONE]":
            continue
        obj = json.loads(e)
        if obj.get("conversation_id"):
            cid = obj["conversation_id"]
    return cid


async def _post_stream(client, token, body):
    return await client.post(
        "/v1/chat/completions", headers=_auth(token), json={**body, "stream": True}
    )


@pytest.mark.asyncio
async def test_stream_returns_openai_sse_format(client, db, make_token) -> None:
    seed_org_with_owner(db, USER_A, "a@firma.de")
    _seed_model(db)
    token = make_token(sub=USER_A, email="a@firma.de")

    r = await _post_stream(
        client, token, {"model": MODEL, "messages": [{"role": "user", "content": "Hi"}]}
    )
    assert r.status_code == 200
    assert "text/event-stream" in r.headers["content-type"]

    events = _events(r.text)
    assert events[-1] == "[DONE]"
    # Every non-DONE event is an OpenAI chat.completion.chunk.
    for e in events[:-1]:
        assert json.loads(e).get("object") == "chat.completion.chunk"
    assert _content(events) == "Hallo! Wie kann ich helfen?"


@pytest.mark.asyncio
async def test_stream_persists_and_audits(client, db, make_token) -> None:
    """The compliance guarantee under streaming: the full streamed answer
    lands in the hash chain and the chain verifies."""
    _org, ws = seed_org_with_owner(db, USER_A, "a@firma.de")
    _seed_model(db)
    token = make_token(sub=USER_A, email="a@firma.de")

    r = await _post_stream(
        client, token, {"model": MODEL, "messages": [{"role": "user", "content": "Hi"}]}
    )
    assert r.status_code == 200
    assert _content(_events(r.text)) == "Hallo! Wie kann ich helfen?"

    row = db.execute(
        text(
            "SELECT content, model FROM messages WHERE role='assistant' "
            "ORDER BY created_at DESC LIMIT 1"
        )
    ).one()
    assert row.content == "Hallo! Wie kann ich helfen?"
    assert row.model == MODEL

    result = verify_workspace_chain(db, ws)
    assert result.valid is True
    assert result.event_count == 1


@pytest.mark.asyncio
async def test_stream_tracks_cost(client, db, make_token) -> None:
    seed_org_with_owner(db, USER_A, "a@firma.de")
    _seed_model(db, cost_input=5.0, cost_output=30.0)
    token = make_token(sub=USER_A, email="a@firma.de")

    await _post_stream(
        client, token, {"model": MODEL, "messages": [{"role": "user", "content": "Hi"}]}
    )
    row = db.execute(
        text(
            "SELECT prompt_tokens, completion_tokens, cost FROM messages "
            "WHERE role='assistant' ORDER BY created_at DESC LIMIT 1"
        )
    ).one()
    assert row.prompt_tokens == 10 and row.completion_tokens == 5
    # 10/1e6*5 + 5/1e6*30 = 0.0002
    assert abs(float(row.cost) - 0.0002) < 1e-9


@pytest.mark.asyncio
async def test_stream_provider_abort_persists_partial(
    client, db, make_token, stub_llm
) -> None:
    """Provider breaks mid-stream: the partial answer was SEEN, so it is
    audited; the client gets an error event (no [DONE]); chain stays valid."""
    _org, ws = seed_org_with_owner(db, USER_A, "a@firma.de")
    _seed_model(db)
    stub_llm.state["content"] = "eins zwei drei vier"
    stub_llm.state["raise_after"] = 2
    token = make_token(sub=USER_A, email="a@firma.de")

    r = await _post_stream(
        client, token, {"model": MODEL, "messages": [{"role": "user", "content": "x"}]}
    )
    assert r.status_code == 200
    events = _events(r.text)
    assert "[DONE]" not in events
    assert any(json.loads(e).get("error") for e in events)

    row = db.execute(
        text(
            "SELECT content FROM messages WHERE role='assistant' "
            "ORDER BY created_at DESC LIMIT 1"
        )
    ).one()
    assert row.content == "eins zwei"  # the 2 chunks collected before the abort

    result = verify_workspace_chain(db, ws)
    assert result.valid is True and result.event_count == 1


@pytest.mark.asyncio
async def test_stream_null_content_abort_persists_nothing(
    client, db, make_token, stub_llm
) -> None:
    """Provider errors BEFORE any chunk: nothing seen, nothing persisted,
    no orphan conversation (matches 4.3 error-before-write)."""
    _org, ws = seed_org_with_owner(db, USER_A, "a@firma.de")
    _seed_model(db)
    stub_llm.state["raise_after"] = 0
    token = make_token(sub=USER_A, email="a@firma.de")

    r = await _post_stream(
        client, token, {"model": MODEL, "messages": [{"role": "user", "content": "x"}]}
    )
    assert r.status_code == 200
    events = _events(r.text)
    assert "[DONE]" not in events
    assert any(json.loads(e).get("error") for e in events)

    assert db.execute(text("SELECT count(*) FROM messages")).scalar_one() == 0
    assert db.execute(text("SELECT count(*) FROM conversations")).scalar_one() == 0
    assert (
        db.execute(
            text("SELECT count(*) FROM audit_events WHERE workspace_id=:w"),
            {"w": ws},
        ).scalar_one()
        == 0
    )


@pytest.mark.asyncio
async def test_stream_multi_turn(client, db, make_token, stub_llm) -> None:
    seed_org_with_owner(db, USER_A, "a@firma.de")
    _seed_model(db)
    token = make_token(sub=USER_A, email="a@firma.de")

    r1 = await _post_stream(
        client, token, {"model": MODEL, "messages": [{"role": "user", "content": "erste"}]}
    )
    cid = _conversation_id(_events(r1.text))
    assert cid

    stub_llm.calls.clear()
    r2 = await _post_stream(
        client,
        token,
        {"model": MODEL, "messages": [{"role": "user", "content": "zweite"}], "conversation_id": cid},
    )
    assert r2.status_code == 200
    # The stub saw the loaded history + the new message.
    sent = [m["content"] for m in stub_llm.calls[-1]["messages"]]
    assert "erste" in sent and "zweite" in sent
    # Both turns in the SAME conversation, and the same id comes back.
    assert _conversation_id(_events(r2.text)) == cid
    assert (
        db.execute(
            text("SELECT count(*) FROM conversations WHERE user_id=:u"),
            {"u": USER_A},
        ).scalar_one()
        == 1
    )


@pytest.mark.asyncio
async def test_stream_delivers_conversation_id_in_final_chunk(
    client, db, make_token
) -> None:
    seed_org_with_owner(db, USER_A, "a@firma.de")
    _seed_model(db)
    token = make_token(sub=USER_A, email="a@firma.de")

    r = await _post_stream(
        client, token, {"model": MODEL, "messages": [{"role": "user", "content": "Hi"}]}
    )
    events = _events(r.text)
    cid = _conversation_id(events)
    assert cid
    assert (
        db.execute(
            text("SELECT id FROM conversations WHERE id=:c"), {"c": cid}
        ).one_or_none()
        is not None
    )
    # The final chunk (right before [DONE]) carries the authoritative id.
    final = json.loads(events[-2])
    assert final["conversation_id"] == cid
    assert final["choices"][0]["finish_reason"] == "stop"


def test_persist_stream_turn_partial_keeps_chain_valid(db) -> None:
    """Unit test of the persist path that the CLIENT-abort branch
    (GeneratorExit) uses — a real client disconnect can't be simulated
    deterministically over HTTP, so the shared persist function is tested
    directly with partial chunks."""
    _org, ws = seed_org_with_owner(db, USER_A, "a@firma.de")

    def _chunk(content):
        delta = types.SimpleNamespace(role=None, content=content)
        return types.SimpleNamespace(
            id="x",
            created=0,
            model=MODEL,
            choices=[types.SimpleNamespace(index=0, delta=delta, finish_reason=None)],
        )

    chunks = [_chunk("Teil"), _chunk(" antwort")]
    new_messages = [{"role": "user", "content": "frage"}]
    db.execute(
        text("SELECT set_config('app.current_workspace_id', :w, true)"), {"w": ws}
    )

    cid = chat.persist_stream_turn(
        db,
        ws,
        USER_A,
        None,
        new_messages,
        chunks,
        new_messages,
        model=MODEL,
        provider="openai",
        cost_input=5.0,
        cost_output=30.0,
    )
    assert cid
    row = db.execute(
        text("SELECT content FROM messages WHERE role='assistant'")
    ).one()
    assert row.content == "Teil antwort"

    result = verify_workspace_chain(db, ws)
    assert result.valid is True and result.event_count == 1
