"""Task 4.2 — GET /models: the model picker's source. JWT-protected,
returns only enabled models as {name, provider}."""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection

USER_A = "11111111-1111-1111-1111-111111111111"


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _seed_model(
    db: Connection,
    name: str,
    provider: str = "openai",
    enabled: bool = True,
) -> None:
    db.execute(
        text(
            "INSERT INTO models (name, provider, cost_input, cost_output, "
            "eu_hosted, sovereign_eligible, tier_min, enabled) "
            "VALUES (:n, :p, 1.0, 2.0, false, false, 'free', :en)"
        ),
        {"n": name, "p": provider, "en": enabled},
    )


@pytest.mark.asyncio
async def test_models_requires_auth(client) -> None:
    r = await client.get("/models")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_models_returns_only_enabled(client, db, make_token) -> None:
    _seed_model(db, "enabled-model", provider="mistral", enabled=True)
    _seed_model(db, "disabled-model", provider="google", enabled=False)
    token = make_token(sub=USER_A, email="a@firma.de")

    r = await client.get("/models", headers=_auth(token))
    assert r.status_code == 200

    body = r.json()
    names = {m["name"] for m in body}
    assert "enabled-model" in names
    assert "disabled-model" not in names

    enabled = next(m for m in body if m["name"] == "enabled-model")
    assert enabled == {"name": "enabled-model", "provider": "mistral"}
    # eu_hosted / sovereign_eligible deliberately not exposed yet.
    assert set(enabled.keys()) == {"name", "provider"}
