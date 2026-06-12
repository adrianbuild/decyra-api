"""Task 4.6 — error handling + sovereignty-aware fallback. Provider failures
are stubbed via conftest.stub_llm `fail_models` (model -> litellm exception)."""

from __future__ import annotations

import json
import logging

import litellm
import pytest
from sqlalchemy import text

from app.audit import verify_workspace_chain
from tests._helpers import seed_org_with_owner

USER_A = "11111111-1111-1111-1111-111111111111"
LARGE = "mistral/mistral-large-latest"   # = Settings.sovereign_model default
SMALL = "mistral/mistral-small-latest"


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _seed_model(
    db,
    name: str,
    *,
    provider: str = "openai",
    cost_input: float = 5.0,
    cost_output: float = 30.0,
    sovereign_eligible: bool = False,
    eu_hosted: bool = False,
) -> None:
    db.execute(
        text(
            "INSERT INTO models (name, provider, cost_input, cost_output, "
            "eu_hosted, sovereign_eligible, tier_min, enabled) "
            "VALUES (:n, :p, :ci, :co, :eu, :sov, 'free', true)"
        ),
        {"n": name, "p": provider, "ci": cost_input, "co": cost_output,
         "eu": eu_hosted, "sov": sovereign_eligible},
    )


def _seed_sovereign_pair(db) -> None:
    _seed_model(db, LARGE, provider="mistral", cost_input=2.0, cost_output=6.0,
                sovereign_eligible=True, eu_hosted=True)
    _seed_model(db, SMALL, provider="mistral", cost_input=0.1, cost_output=0.3,
                sovereign_eligible=True, eu_hosted=True)


def _svc(model: str, provider: str = "openai"):
    return litellm.ServiceUnavailableError("down", provider, model)


def _bad(model: str, provider: str = "openai"):
    return litellm.BadRequestError("bad", model, provider)


def _events(body: str) -> list[str]:
    return [
        b.strip()[len("data: ") :]
        for b in body.split("\n\n")
        if b.strip().startswith("data: ")
    ]


def _called(stub) -> list[str]:
    return [c["model"] for c in stub.calls]


def _post(client, token, model, **extra):
    return client.post(
        "/v1/chat/completions",
        headers=_auth(token),
        json={"model": model, "messages": [{"role": "user", "content": "hi"}], **extra},
    )


@pytest.mark.asyncio
async def test_nonstream_transient_fallback(client, db, make_token, stub_llm) -> None:
    _org, ws = seed_org_with_owner(db, USER_A, "a@firma.de")
    _seed_model(db, "test-model", cost_input=5.0, cost_output=30.0)
    _seed_model(db, LARGE, provider="mistral", cost_input=2.0, cost_output=6.0,
                sovereign_eligible=True, eu_hosted=True)
    stub_llm.state["fail_models"] = {"test-model": _svc("test-model")}
    token = make_token(sub=USER_A, email="a@firma.de")

    r = await _post(client, token, "test-model")
    assert r.status_code == 200
    d = r.json()["decyra"]
    assert d["effective_model"] == LARGE and d["routed_to"] == "mistral"

    msg = db.execute(
        text("SELECT model, cost FROM messages WHERE role='assistant' "
             "ORDER BY created_at DESC LIMIT 1")
    ).one()
    assert msg.model == LARGE
    # fallback prices 2/6, stub usage pt=10 ct=5 -> 5e-5 (not the chosen 5/30)
    assert abs(float(msg.cost) - 0.00005) < 1e-9
    # only the successful call is audited (the failed primary is not)
    assert db.execute(text("SELECT count(*) FROM audit_events")).scalar_one() == 1
    assert verify_workspace_chain(db, ws).valid is True
    assert _called(stub_llm) == ["test-model", LARGE]


@pytest.mark.asyncio
async def test_permanent_error_no_fallback_400(client, db, make_token, stub_llm) -> None:
    seed_org_with_owner(db, USER_A, "a@firma.de")
    _seed_model(db, "test-model")
    _seed_model(db, LARGE, provider="mistral", sovereign_eligible=True, eu_hosted=True)
    stub_llm.state["fail_models"] = {"test-model": _bad("test-model")}
    token = make_token(sub=USER_A, email="a@firma.de")

    r = await _post(client, token, "test-model")
    assert r.status_code == 400
    assert _called(stub_llm) == ["test-model"]  # no fallback attempted


@pytest.mark.asyncio
async def test_all_transient_exhausted_503_no_audit(
    client, db, make_token, stub_llm
) -> None:
    seed_org_with_owner(db, USER_A, "a@firma.de")
    _seed_model(db, "test-model")
    _seed_sovereign_pair(db)
    stub_llm.state["fail_models"] = {
        m: _svc(m) for m in ("test-model", LARGE, SMALL)
    }
    token = make_token(sub=USER_A, email="a@firma.de")

    r = await _post(client, token, "test-model")
    assert r.status_code == 503
    # Invariant 3: a failed call writes NO audit event (the error goes to the
    # decyra.errors log only — see test_error_log_fires_and_chain_clean).
    assert db.execute(text("SELECT count(*) FROM audit_events")).scalar_one() == 0
    assert set(_called(stub_llm)) == {"test-model", LARGE, SMALL}


def test_error_log_fires() -> None:
    """Invariant 3 at the unit level (deterministic, no threadpool): a
    fully-failed call logs every failure to the `decyra.errors` logger.
    The 'no audit event' half is the HTTP test above."""
    from app import llm_call

    settings = type("S", (), {"request_timeout_seconds": 60.0, "num_retries": 2})()

    def _row(provider):
        return type("R", (), {
            "name": "m", "provider": provider, "cost_input": 1.0,
            "cost_output": 2.0, "eu_hosted": True, "sovereign_eligible": True,
        })()

    candidates = [("m1", _row("openai")), ("m2", _row("mistral"))]
    stub_llm_fail = {
        "m1": _svc("m1", "openai"),
        "m2": _svc("m2", "mistral"),
    }

    records: list[logging.LogRecord] = []
    handler = logging.Handler()
    handler.emit = records.append  # type: ignore[method-assign]
    err_logger = logging.getLogger("decyra.errors")
    # alembic's fileConfig (run in conftest's session setup) disables existing
    # loggers — a TEST-only artifact (alembic runs as a separate process in
    # prod, so the API's error logger stays live). Re-enable for this test.
    prev_disabled = err_logger.disabled
    err_logger.disabled = False
    err_logger.addHandler(handler)
    orig = litellm.completion

    def _fake(**kw):
        m = kw.get("model")
        if m in stub_llm_fail:
            raise stub_llm_fail[m]
        raise AssertionError("unexpected success")

    litellm.completion = _fake
    try:
        with pytest.raises(llm_call.ProvidersUnavailable):
            llm_call.complete_with_fallback(
                candidates, {"messages": []}, settings,
                log_ctx={"workspace_id": "w", "user_id": "u"},
            )
    finally:
        litellm.completion = orig
        err_logger.removeHandler(handler)
        err_logger.disabled = prev_disabled

    assert [r.name for r in records] == ["decyra.errors", "decyra.errors"]
    assert records[-1].levelname == "ERROR"  # last candidate -> ERROR


@pytest.mark.asyncio
async def test_inv1_sovereign_falls_back_only_to_sovereign(
    client, db, make_token, stub_llm, stub_pii
) -> None:
    seed_org_with_owner(db, USER_A, "a@firma.de")
    _seed_model(db, "gpt-5.5")  # enabled NON-sovereign — must never be used
    _seed_sovereign_pair(db)
    stub_pii.state["force"] = "detected"  # PII -> reroute to LARGE (sovereign)
    stub_llm.state["fail_models"] = {LARGE: _svc(LARGE, "mistral")}
    token = make_token(sub=USER_A, email="a@firma.de")

    r = await _post(client, token, "gpt-5.5")
    assert r.status_code == 200
    assert r.json()["decyra"]["effective_model"] == SMALL
    called = _called(stub_llm)
    assert "gpt-5.5" not in called
    assert set(called) <= {LARGE, SMALL}


@pytest.mark.asyncio
async def test_inv1_single_sovereign_failure_blocks_503(
    client, db, make_token, stub_llm, stub_pii
) -> None:
    seed_org_with_owner(db, USER_A, "a@firma.de")
    _seed_model(db, "gpt-5.5")  # non-sovereign, must NOT be used as fallback
    _seed_model(db, LARGE, provider="mistral", sovereign_eligible=True, eu_hosted=True)
    # SMALL deliberately not enabled -> no sovereign fallback
    stub_pii.state["force"] = "detected"
    stub_llm.state["fail_models"] = {LARGE: _svc(LARGE, "mistral")}
    token = make_token(sub=USER_A, email="a@firma.de")

    r = await _post(client, token, "gpt-5.5")
    assert r.status_code == 503
    assert _called(stub_llm) == [LARGE]  # never gpt-5.5


@pytest.mark.asyncio
async def test_nonsovereign_default_fallback_is_sovereign(
    client, db, make_token, stub_llm
) -> None:
    seed_org_with_owner(db, USER_A, "a@firma.de")
    _seed_model(db, "gpt-5.5")
    _seed_model(db, LARGE, provider="mistral", sovereign_eligible=True, eu_hosted=True)
    stub_llm.state["fail_models"] = {"gpt-5.5": _svc("gpt-5.5")}  # clean prompt
    token = make_token(sub=USER_A, email="a@firma.de")

    r = await _post(client, token, "gpt-5.5")
    assert r.json()["decyra"]["effective_model"] == LARGE


@pytest.mark.asyncio
async def test_dedup_primary_not_tried_twice(client, db, make_token, stub_llm) -> None:
    seed_org_with_owner(db, USER_A, "a@firma.de")
    _seed_sovereign_pair(db)  # LARGE in fallback_models AND the chosen model
    stub_llm.state["fail_models"] = {LARGE: _svc(LARGE, "mistral")}
    token = make_token(sub=USER_A, email="a@firma.de")

    r = await _post(client, token, LARGE)
    assert r.status_code == 200
    called = _called(stub_llm)
    assert called.count(LARGE) == 1
    assert called == [LARGE, SMALL]


@pytest.mark.asyncio
async def test_stream_fallback_before_first_chunk(
    client, db, make_token, stub_llm
) -> None:
    _org, ws = seed_org_with_owner(db, USER_A, "a@firma.de")
    _seed_model(db, "test-model")
    _seed_model(db, LARGE, provider="mistral", cost_input=2.0, cost_output=6.0,
                sovereign_eligible=True, eu_hosted=True)
    stub_llm.state["fail_models"] = {"test-model": _svc("test-model")}
    token = make_token(sub=USER_A, email="a@firma.de")

    r = await _post(client, token, "test-model", stream=True)
    assert r.status_code == 200
    events = _events(r.text)
    assert json.loads(events[0])["decyra"]["effective_model"] == LARGE
    assert "[DONE]" in events
    msg = db.execute(
        text("SELECT model FROM messages WHERE role='assistant' "
             "ORDER BY created_at DESC LIMIT 1")
    ).one()
    assert msg.model == LARGE
    assert verify_workspace_chain(db, ws).valid is True


@pytest.mark.asyncio
async def test_stream_all_fail_error_no_done_no_persist(
    client, db, make_token, stub_llm
) -> None:
    seed_org_with_owner(db, USER_A, "a@firma.de")
    _seed_model(db, "test-model")
    _seed_sovereign_pair(db)
    stub_llm.state["fail_models"] = {
        m: _svc(m) for m in ("test-model", LARGE, SMALL)
    }
    token = make_token(sub=USER_A, email="a@firma.de")

    r = await _post(client, token, "test-model", stream=True)
    events = _events(r.text)
    assert "[DONE]" not in events
    assert any(json.loads(e).get("error") for e in events)
    assert db.execute(text("SELECT count(*) FROM conversations")).scalar_one() == 0
    assert db.execute(text("SELECT count(*) FROM audit_events")).scalar_one() == 0


@pytest.mark.asyncio
async def test_stream_midstream_abort_no_fallback(
    client, db, make_token, stub_llm
) -> None:
    """Mid-stream provider abort follows 4.4 (persist partial with the PRIMARY
    model, error event), NOT a fallback restart."""
    _org, ws = seed_org_with_owner(db, USER_A, "a@firma.de")
    _seed_model(db, "test-model")
    _seed_model(db, LARGE, provider="mistral", sovereign_eligible=True, eu_hosted=True)
    stub_llm.state["content"] = "eins zwei drei vier"
    stub_llm.state["raise_after"] = 2  # connects, streams 2 chunks, then aborts
    token = make_token(sub=USER_A, email="a@firma.de")

    r = await _post(client, token, "test-model", stream=True)
    events = _events(r.text)
    assert "[DONE]" not in events
    assert any(json.loads(e).get("error") for e in events)
    msg = db.execute(
        text("SELECT model, content FROM messages WHERE role='assistant' "
             "ORDER BY created_at DESC LIMIT 1")
    ).one()
    assert msg.model == "test-model" and msg.content == "eins zwei"
    assert LARGE not in _called(stub_llm)  # no mid-stream fallback
