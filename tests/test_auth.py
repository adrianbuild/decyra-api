import pytest
from cryptography.hazmat.primitives.asymmetric import ec


@pytest.mark.asyncio
async def test_me_with_valid_token_returns_user(client, make_token):
    token = make_token(sub="user-uuid-abc", email="alice@example.com")
    r = await client.get(
        "/me", headers={"Authorization": f"Bearer {token}"}
    )
    assert r.status_code == 200
    assert r.json() == {"user_id": "user-uuid-abc", "email": "alice@example.com"}


@pytest.mark.asyncio
async def test_me_without_token_returns_401(client):
    r = await client.get("/me")
    assert r.status_code == 401
    assert "Missing Authorization header" in r.json()["detail"]


@pytest.mark.asyncio
async def test_me_with_malformed_header_returns_401(client):
    r = await client.get("/me", headers={"Authorization": "Token abc"})
    assert r.status_code == 401
    assert "Bearer" in r.json()["detail"]


@pytest.mark.asyncio
async def test_me_with_wrong_signature_returns_401(client, make_token):
    # Sign with a fresh, unrelated keypair. The JWKS monkeypatch still
    # returns the SESSION public key, so signature verification fails.
    other_key = ec.generate_private_key(ec.SECP256R1())
    token = make_token(signing_key=other_key)
    r = await client.get(
        "/me", headers={"Authorization": f"Bearer {token}"}
    )
    assert r.status_code == 401
    assert "Invalid token" in r.json()["detail"]


@pytest.mark.asyncio
async def test_me_with_expired_token_returns_401(client, make_token):
    token = make_token(exp_offset=-60)
    r = await client.get(
        "/me", headers={"Authorization": f"Bearer {token}"}
    )
    assert r.status_code == 401
    assert "expired" in r.json()["detail"].lower()


@pytest.mark.asyncio
async def test_me_with_wrong_issuer_returns_401(client, make_token):
    token = make_token(issuer="https://bogus.example/auth/v1")
    r = await client.get(
        "/me", headers={"Authorization": f"Bearer {token}"}
    )
    assert r.status_code == 401
    assert "issuer" in r.json()["detail"].lower()
