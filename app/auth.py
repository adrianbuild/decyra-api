"""Supabase JWT validation via JWKS endpoint.

Validates ES256-signed JWTs from Supabase Auth. Public signing keys are
loaded from ``{SUPABASE_URL}/auth/v1/.well-known/jwks.json`` (PyJWKClient,
in-process cache with 5min TTL).

Required settings:
- ``SUPABASE_URL``: Supabase project URL.

Optional settings:
- ``SUPABASE_JWT_AUDIENCE``: default ``"authenticated"``.
- ``SUPABASE_JWT_ISSUER``: default ``f"{SUPABASE_URL}/auth/v1"``.
"""

from __future__ import annotations

from dataclasses import dataclass

import jwt
from fastapi import Depends, HTTPException, Request, status
from jwt import PyJWKClient

from app.config import Settings, get_settings


@dataclass(frozen=True, slots=True)
class AuthenticatedUser:
    user_id: str
    email: str | None


_JWKS_CLIENT: PyJWKClient | None = None
_JWKS_CLIENT_URL: str | None = None


def _get_jwks_client(jwks_url: str) -> PyJWKClient:
    global _JWKS_CLIENT, _JWKS_CLIENT_URL
    if _JWKS_CLIENT is None or _JWKS_CLIENT_URL != jwks_url:
        _JWKS_CLIENT = PyJWKClient(
            jwks_url,
            cache_keys=True,
            max_cached_keys=16,
            lifespan=300,
        )
        _JWKS_CLIENT_URL = jwks_url
    return _JWKS_CLIENT


def _extract_bearer(request: Request) -> str:
    header = request.headers.get("Authorization")
    if not header:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            detail="Missing Authorization header",
        )
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            detail="Authorization header must be 'Bearer <token>'",
        )
    return token


def decode_token(token: str, settings: Settings) -> dict:
    if not settings.supabase_url:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Server auth not configured (SUPABASE_URL missing)",
        )
    issuer = (
        settings.supabase_jwt_issuer
        or f"{settings.supabase_url}/auth/v1"
    )
    jwks_url = f"{settings.supabase_url}/auth/v1/.well-known/jwks.json"
    try:
        signing_key = _get_jwks_client(jwks_url).get_signing_key_from_jwt(
            token
        )
        return jwt.decode(
            token,
            signing_key.key,
            algorithms=["ES256"],
            audience=settings.supabase_jwt_audience,
            issuer=issuer,
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, detail="Token expired"
        )
    except jwt.InvalidTokenError as e:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, detail=f"Invalid token: {e}"
        )
    except jwt.PyJWKClientError as e:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            detail=f"Token signing key not found: {e}",
        )


def get_current_user(
    request: Request,
    settings: Settings = Depends(get_settings),
) -> AuthenticatedUser:
    token = _extract_bearer(request)
    claims = decode_token(token, settings)
    sub = claims.get("sub")
    if not sub:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, detail="Token missing 'sub' claim"
        )
    return AuthenticatedUser(user_id=sub, email=claims.get("email"))
