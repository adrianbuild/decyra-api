"""Task 4.5a — PII sovereign routing. PII is stubbed (conftest.stub_pii);
the DoD invariants 1-3 are tested here. The local regex layer is tested
in test_pii.py."""

from __future__ import annotations

import json

import pytest
from sqlalchemy import text

from app.audit import verify_workspace_chain
from tests._helpers import seed_org_with_owner

USER_A = "11111111-1111-1111-1111-111111111111"
CHOSEN = "test-model"  # non-sovereign
SOVEREIGN = "mistral/mistral-large-latest"  # = Settings.sovereign_model default


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _seed_model(
    db,
    name: str,
    *,
    provider: str = "openai",
    cost_input: float = 5.0,
    cost_output: float = 30.0,
    eu_hosted: bool = False,
    sovereign_eligible: bool = False,
) -> None:
    db.execute(
        text(
            "INSERT INTO models (name, provider, cost_input, cost_output, "
            "eu_hosted, sovereign_eligible, tier_min, enabled) "
            "VALUES (:n, :p, :ci, :co, :eu, :sov, 'free', true)"
        ),
        {
            "n": name, "p": provider, "ci": cost_input, "co": cost_output,
            "eu": eu_hosted, "sov": sovereign_eligible,
        },
    )


def _seed_sovereign(db, cost_input: float = 2.0, cost_output: float = 6.0) -> None:
    _seed_model(
        db, SOVEREIGN, provider="mistral", cost_input=cost_input,
        cost_output=cost_output, eu_hosted=True, sovereign_eligible=True,
    )


def _events(body: str) -> list[str]:
    return [
        b.strip()[len("data: ") :]
        for b in body.split("\n\n")
        if b.strip().startswith("data: ")
    ]


def _body(token, model, content="hi", **extra):
    return {"model": model, "messages": [{"role": "user", "content": content}], **extra}


@pytest.mark.asyncio
async def test_pii_reroutes_to_sovereign_nonstream(
    client, db, make_token, stub_pii
) -> None:
    _org, ws = seed_org_with_owner(db, USER_A, "a@firma.de")
    _seed_model(db, CHOSEN)
    _seed_sovereign(db)
    stub_pii.state["force"] = "detected"
    token = make_token(sub=USER_A, email="a@firma.de")

    r = await client.post(
        "/v1/chat/completions", headers=_auth(token), json=_body(token, CHOSEN)
    )
    assert r.status_code == 200
    d = r.json()["decyra"]
    assert d["pii_detected"] is True
    assert d["pii_check"] == "detected"
    assert d["effective_model"] == SOVEREIGN
    assert d["routed_to"] == "mistral"
    assert r.json()["model"] == SOVEREIGN

    msg = db.execute(
        text("SELECT model FROM messages WHERE role='assistant' "
             "ORDER BY created_at DESC LIMIT 1")
    ).one()
    assert msg.model == SOVEREIGN
    ev = db.execute(
        text("SELECT model, routed_to, pii_detected FROM audit_events "
             "ORDER BY timestamp DESC LIMIT 1")
    ).one()
    assert ev.model == SOVEREIGN and ev.routed_to == "mistral"
    assert ev.pii_detected is True
    # Invariant 3: chain still valid after a rerouted call.
    assert verify_workspace_chain(db, ws).valid is True


@pytest.mark.asyncio
async def test_pii_reroutes_streaming(client, db, make_token, stub_pii) -> None:
    _org, ws = seed_org_with_owner(db, USER_A, "a@firma.de")
    _seed_model(db, CHOSEN)
    _seed_sovereign(db)
    stub_pii.state["force"] = "detected"
    token = make_token(sub=USER_A, email="a@firma.de")

    r = await client.post(
        "/v1/chat/completions", headers=_auth(token),
        json=_body(token, CHOSEN, stream=True),
    )
    assert r.status_code == 200
    events = _events(r.text)
    first = json.loads(events[0])
    assert first["decyra"]["effective_model"] == SOVEREIGN
    assert first["decyra"]["pii_detected"] is True

    msg = db.execute(
        text("SELECT model FROM messages WHERE role='assistant' "
             "ORDER BY created_at DESC LIMIT 1")
    ).one()
    assert msg.model == SOVEREIGN
    assert verify_workspace_chain(db, ws).valid is True  # Invariant 3 (stream)


@pytest.mark.asyncio
async def test_no_pii_keeps_chosen_model(client, db, make_token) -> None:
    seed_org_with_owner(db, USER_A, "a@firma.de")
    _seed_model(db, CHOSEN)
    _seed_sovereign(db)
    token = make_token(sub=USER_A, email="a@firma.de")  # stub default: clean

    r = await client.post(
        "/v1/chat/completions", headers=_auth(token), json=_body(token, CHOSEN)
    )
    d = r.json()["decyra"]
    assert d["pii_detected"] is False and d["pii_check"] == "clean"
    assert d["effective_model"] == CHOSEN


@pytest.mark.asyncio
async def test_invariant1_pii_only_in_history_still_reroutes(
    client, db, make_token, stub_pii
) -> None:
    """The full llm_input (history + new) is scanned: PII in stored history
    forces the reroute even when the new message is clean (the ratchet)."""
    _org, ws = seed_org_with_owner(db, USER_A, "a@firma.de")
    _seed_model(db, CHOSEN)
    _seed_sovereign(db)
    cid = db.execute(
        text("INSERT INTO conversations (workspace_id, user_id, title) "
             "VALUES (:w, :u, 't') RETURNING id"),
        {"w": ws, "u": USER_A},
    ).scalar_one()
    db.execute(
        text("INSERT INTO messages (conversation_id, workspace_id, role, content) "
             "VALUES (:c, :w, 'user', 'Frühere Daten: GEHEIM')"),
        {"c": cid, "w": ws},
    )
    stub_pii.state["needle"] = "GEHEIM"  # force stays None -> substring match
    token = make_token(sub=USER_A, email="a@firma.de")

    r = await client.post(
        "/v1/chat/completions", headers=_auth(token),
        json=_body(token, CHOSEN, content="harmlose Folgefrage",
                   conversation_id=str(cid)),
    )
    assert r.status_code == 200
    assert r.json()["decyra"]["effective_model"] == SOVEREIGN


@pytest.mark.asyncio
async def test_invariant2_presidio_unavailable_failsafe_reroutes(
    client, db, make_token, stub_pii
) -> None:
    _org, ws = seed_org_with_owner(db, USER_A, "a@firma.de")
    _seed_model(db, CHOSEN)
    _seed_sovereign(db)
    stub_pii.state["force"] = "unavailable"
    token = make_token(sub=USER_A, email="a@firma.de")

    r = await client.post(
        "/v1/chat/completions", headers=_auth(token), json=_body(token, CHOSEN)
    )
    d = r.json()["decyra"]
    assert d["pii_check"] == "unavailable"
    assert d["pii_detected"] is False  # degraded != real detection
    assert d["effective_model"] == SOVEREIGN and d["routed_to"] == "mistral"
    # Degraded reroute leaves audit pii_detected false (distinguishable).
    ev = db.execute(
        text("SELECT routed_to, pii_detected FROM audit_events "
             "ORDER BY timestamp DESC LIMIT 1")
    ).one()
    assert ev.routed_to == "mistral" and ev.pii_detected is False


@pytest.mark.asyncio
async def test_already_sovereign_plus_pii_no_reroute(
    client, db, make_token, stub_pii
) -> None:
    _org, ws = seed_org_with_owner(db, USER_A, "a@firma.de")
    _seed_sovereign(db)  # chosen model IS the sovereign one
    stub_pii.state["force"] = "detected"
    token = make_token(sub=USER_A, email="a@firma.de")

    r = await client.post(
        "/v1/chat/completions", headers=_auth(token), json=_body(token, SOVEREIGN)
    )
    d = r.json()["decyra"]
    assert d["effective_model"] == SOVEREIGN  # no reroute (already sovereign)
    assert d["pii_detected"] is True  # still recorded
    ev = db.execute(
        text("SELECT pii_detected FROM audit_events ORDER BY timestamp DESC LIMIT 1")
    ).one()
    assert ev.pii_detected is True


@pytest.mark.asyncio
async def test_no_sovereign_target_plus_pii_blocks_503(
    client, db, make_token, stub_pii
) -> None:
    seed_org_with_owner(db, USER_A, "a@firma.de")
    _seed_model(db, CHOSEN)  # NO sovereign target seeded
    stub_pii.state["force"] = "detected"
    token = make_token(sub=USER_A, email="a@firma.de")

    r = await client.post(
        "/v1/chat/completions", headers=_auth(token), json=_body(token, CHOSEN)
    )
    assert r.status_code == 503


@pytest.mark.asyncio
async def test_workspace_strict_default_anonymizes_not_reroutes(
    client, db, make_token, stub_pii, stub_analyze
) -> None:
    """4.5b: the WORKSPACE-level pii_mode='strict' default now ANONYMISES (keeps
    the chosen model, sends only placeholders) instead of rerouting as in 4.5a.
    No request/conversation override is given — the workspace default governs."""
    _org, ws = seed_org_with_owner(db, USER_A, "a@firma.de")
    _seed_model(db, CHOSEN)
    _seed_sovereign(db)
    db.execute(
        text("UPDATE workspaces SET settings = '{\"pii_mode\":\"strict\"}'::jsonb "
             "WHERE id = :w"),
        {"w": ws},
    )
    stub_pii.state["force"] = "detected"
    stub_analyze.state["spans_for"] = {"Max Mustermann": "PERSON"}
    token = make_token(sub=USER_A, email="a@firma.de")

    r = await client.post(
        "/v1/chat/completions", headers=_auth(token),
        json=_body(token, CHOSEN, content="Hilf Max Mustermann"),
    )
    d = r.json()["decyra"]
    assert d["effective_model"] == CHOSEN  # no reroute
    assert d["anonymized"] is True
    assert d["pii_mode"] == "strict"


@pytest.mark.asyncio
async def test_cost_uses_effective_model_prices(
    client, db, make_token, stub_pii
) -> None:
    seed_org_with_owner(db, USER_A, "a@firma.de")
    _seed_model(db, CHOSEN, cost_input=5.0, cost_output=30.0)
    _seed_sovereign(db, cost_input=2.0, cost_output=6.0)
    stub_pii.state["force"] = "detected"
    token = make_token(sub=USER_A, email="a@firma.de")

    await client.post(
        "/v1/chat/completions", headers=_auth(token), json=_body(token, CHOSEN)
    )
    # stub usage: pt=10, ct=5; sovereign prices 2/6 -> 10/1e6*2 + 5/1e6*6 = 5e-5
    cost = db.execute(
        text("SELECT cost FROM messages WHERE role='assistant' "
             "ORDER BY created_at DESC LIMIT 1")
    ).scalar_one()
    assert abs(float(cost) - 0.00005) < 1e-9
