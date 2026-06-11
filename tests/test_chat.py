"""Task 4.3 — chat proxy: OpenAI format, persistence, multi-turn,
cost, audit chain, and the (critical) per-user privacy guard."""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection

from app.audit import verify_workspace_chain
from tests._helpers import add_member, seed_org_with_owner

USER_A = "11111111-1111-1111-1111-111111111111"
USER_B = "22222222-2222-2222-2222-222222222222"
NOBODY = "99999999-9999-9999-9999-999999999999"
MODEL = "test-model"


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _seed_model(
    db: Connection,
    name: str = MODEL,
    cost_input: float = 5.0,
    cost_output: float = 30.0,
    enabled: bool = True,
) -> None:
    db.execute(
        text(
            "INSERT INTO models (name, provider, cost_input, cost_output, "
            "eu_hosted, sovereign_eligible, tier_min, enabled) "
            "VALUES (:n, 'openai', :ci, :co, false, false, 'free', :en)"
        ),
        {"n": name, "ci": cost_input, "co": cost_output, "en": enabled},
    )


@pytest.mark.asyncio
async def test_chat_returns_openai_format(client, db, make_token) -> None:
    seed_org_with_owner(db, USER_A, "a@firma.de")
    _seed_model(db)
    token = make_token(sub=USER_A, email="a@firma.de")
    r = await client.post(
        "/v1/chat/completions",
        headers=_auth(token),
        json={"model": MODEL, "messages": [{"role": "user", "content": "Hallo"}]},
    )
    assert r.status_code == 200
    b = r.json()
    assert b["object"] == "chat.completion"
    assert b["model"] == MODEL
    assert b["choices"][0]["message"]["role"] == "assistant"
    assert b["choices"][0]["message"]["content"] == "Hallo! Wie kann ich helfen?"
    assert b["choices"][0]["finish_reason"] == "stop"
    assert b["usage"] == {
        "prompt_tokens": 10,
        "completion_tokens": 5,
        "total_tokens": 15,
    }
    assert b["conversation_id"]


@pytest.mark.asyncio
async def test_chat_persists_conversation_and_messages(
    client, db, make_token
) -> None:
    seed_org_with_owner(db, USER_A, "a@firma.de")
    _seed_model(db)
    token = make_token(sub=USER_A, email="a@firma.de")
    await client.post(
        "/v1/chat/completions",
        headers=_auth(token),
        json={"model": MODEL, "messages": [{"role": "user", "content": "Hallo"}]},
    )
    assert (
        db.execute(
            text("SELECT count(*) FROM conversations WHERE user_id = :u"),
            {"u": USER_A},
        ).scalar_one()
        == 1
    )
    rows = db.execute(
        text("SELECT role, content FROM messages ORDER BY created_at ASC, id ASC")
    ).all()
    assert [(r.role, r.content) for r in rows] == [
        ("user", "Hallo"),
        ("assistant", "Hallo! Wie kann ich helfen?"),
    ]


@pytest.mark.asyncio
async def test_chat_multi_turn_loads_history(
    client, db, make_token, stub_llm
) -> None:
    seed_org_with_owner(db, USER_A, "a@firma.de")
    _seed_model(db)
    token = make_token(sub=USER_A, email="a@firma.de")

    r1 = await client.post(
        "/v1/chat/completions",
        headers=_auth(token),
        json={"model": MODEL, "messages": [{"role": "user", "content": "Erste"}]},
    )
    cid = r1.json()["conversation_id"]

    r2 = await client.post(
        "/v1/chat/completions",
        headers=_auth(token),
        json={
            "model": MODEL,
            "conversation_id": cid,
            "messages": [{"role": "user", "content": "Zweite"}],
        },
    )
    assert r2.status_code == 200
    # The model saw the stored history (Erste + its answer) plus the new turn.
    sent = [m["content"] for m in stub_llm.calls[-1]["messages"]]
    assert sent == ["Erste", "Hallo! Wie kann ich helfen?", "Zweite"]


@pytest.mark.asyncio
async def test_chat_writes_audit_and_chain_verifies(
    client, db, make_token
) -> None:
    _, ws = seed_org_with_owner(db, USER_A, "a@firma.de")
    _seed_model(db)
    token = make_token(sub=USER_A, email="a@firma.de")
    body = {"model": MODEL, "messages": [{"role": "user", "content": "x"}]}

    await client.post("/v1/chat/completions", headers=_auth(token), json=body)
    res = verify_workspace_chain(db, ws)
    assert res.valid is True and res.event_count == 1

    await client.post("/v1/chat/completions", headers=_auth(token), json=body)
    res = verify_workspace_chain(db, ws)
    assert res.valid is True and res.event_count == 2


@pytest.mark.asyncio
async def test_chat_cost_calculation(client, db, make_token, stub_llm) -> None:
    seed_org_with_owner(db, USER_A, "a@firma.de")
    _seed_model(db, cost_input=5.0, cost_output=30.0)
    stub_llm.state["prompt_tokens"] = 1_000_000
    stub_llm.state["completion_tokens"] = 500_000
    token = make_token(sub=USER_A, email="a@firma.de")
    await client.post(
        "/v1/chat/completions",
        headers=_auth(token),
        json={"model": MODEL, "messages": [{"role": "user", "content": "x"}]},
    )
    cost = db.execute(
        text("SELECT cost FROM messages WHERE role = 'assistant'")
    ).scalar_one()
    # 1M/1M*5 + 0.5M/1M*30 = 5 + 15 = 20
    assert float(cost) == 20.0


@pytest.mark.asyncio
async def test_chat_unknown_and_disabled_model_rejected(
    client, db, make_token
) -> None:
    seed_org_with_owner(db, USER_A, "a@firma.de")
    _seed_model(db, name="disabled-model", enabled=False)
    token = make_token(sub=USER_A, email="a@firma.de")

    r1 = await client.post(
        "/v1/chat/completions",
        headers=_auth(token),
        json={"model": "does-not-exist", "messages": [{"role": "user", "content": "x"}]},
    )
    assert r1.status_code == 400

    r2 = await client.post(
        "/v1/chat/completions",
        headers=_auth(token),
        json={"model": "disabled-model", "messages": [{"role": "user", "content": "x"}]},
    )
    assert r2.status_code == 400


@pytest.mark.asyncio
async def test_chat_stream_now_supported(client, db, make_token) -> None:
    """Task 4.4 replaced the 4.3 stream=400 guard with the real streaming
    path: stream=true now returns an OpenAI-compatible SSE stream.
    (Full streaming behaviour is covered in test_chat_stream.py.)"""
    seed_org_with_owner(db, USER_A, "a@firma.de")
    _seed_model(db)
    token = make_token(sub=USER_A, email="a@firma.de")
    r = await client.post(
        "/v1/chat/completions",
        headers=_auth(token),
        json={
            "model": MODEL,
            "stream": True,
            "messages": [{"role": "user", "content": "x"}],
        },
    )
    assert r.status_code == 200
    assert "text/event-stream" in r.headers["content-type"]
    assert "data: [DONE]" in r.text


@pytest.mark.asyncio
async def test_chat_no_membership_403(client, make_token) -> None:
    token = make_token(sub=NOBODY, email="nobody@firma.de")
    r = await client.post(
        "/v1/chat/completions",
        headers=_auth(token),
        json={"model": MODEL, "messages": [{"role": "user", "content": "x"}]},
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_get_conversation_with_messages(client, db, make_token) -> None:
    seed_org_with_owner(db, USER_A, "a@firma.de")
    _seed_model(db)
    token = make_token(sub=USER_A, email="a@firma.de")
    created = await client.post(
        "/v1/chat/completions",
        headers=_auth(token),
        json={"model": MODEL, "messages": [{"role": "user", "content": "Frage"}]},
    )
    cid = created.json()["conversation_id"]
    r = await client.get(f"/conversations/{cid}", headers=_auth(token))
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == cid
    assert [m["role"] for m in body["messages"]] == ["user", "assistant"]


@pytest.mark.asyncio
async def test_list_conversations_only_own(client, db, make_token) -> None:
    _, ws = seed_org_with_owner(db, USER_A, "a@firma.de")
    _seed_model(db)
    db.execute(
        text("INSERT INTO users (id, email) VALUES (:i, 'b@firma.de')"),
        {"i": USER_B},
    )
    add_member(db, ws, USER_B, "user")
    # A and B each create a conversation in the same workspace.
    for sub, email, content in (
        (USER_A, "a@firma.de", "A-Chat"),
        (USER_B, "b@firma.de", "B-Chat"),
    ):
        resp = await client.post(
            "/v1/chat/completions",
            headers=_auth(make_token(sub=sub, email=email)),
            json={"model": MODEL, "messages": [{"role": "user", "content": content}]},
        )
        assert resp.status_code == 200

    r = await client.get(
        "/conversations", headers=_auth(make_token(sub=USER_A, email="a@firma.de"))
    )
    titles = [c["title"] for c in r.json()]
    assert titles == ["A-Chat"]


@pytest.mark.asyncio
async def test_conversation_privacy_user_b_cannot_read_as_decyra_app(
    client_decyra_app, db, make_token
) -> None:
    """PROMINENT privacy guard, run as decyra_app (is_superuser='off' is
    asserted by the fixture). "Private" is only a user_id query filter —
    RLS does NOT protect it within a workspace — so this test is the
    watchdog: User B must not see User A's conversation."""
    _, ws = seed_org_with_owner(db, USER_A, "a@firma.de")
    db.execute(
        text("INSERT INTO users (id, email) VALUES (:i, 'b@firma.de')"),
        {"i": USER_B},
    )
    add_member(db, ws, USER_B, "user")
    # A's private conversation, seeded directly (postgres bypasses RLS).
    cid = str(
        db.execute(
            text(
                "INSERT INTO conversations (workspace_id, user_id, title) "
                "VALUES (:w, :u, 'A secret') RETURNING id"
            ),
            {"w": ws, "u": USER_A},
        ).scalar_one()
    )

    token_b = make_token(sub=USER_B, email="b@firma.de")
    r = await client_decyra_app.get(
        f"/conversations/{cid}", headers=_auth(token_b)
    )
    assert r.status_code == 404  # B cannot load A's conversation

    r2 = await client_decyra_app.get("/conversations", headers=_auth(token_b))
    assert r2.status_code == 200
    assert r2.json() == []  # B's list is empty, A's chat not leaked
