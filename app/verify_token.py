"""Public verify-token for the GET /v/{token} endpoint.

JWT (HS256) signed with ``AUDIT_VERIFY_SECRET`` — separate from the
Supabase JWT secret used by ``app.auth``. Claims::

    sub = workspace_id (UUID string)
    iss = "decyra-audit"
    iat = unix time at issuance
    exp = iat + ttl_seconds

Default TTL is 30 days, overridable via
``AUDIT_VERIFY_TOKEN_DEFAULT_TTL_SECONDS``. The endpoint is fully
public — no Supabase auth required — so the token alone proves the
caller was authorized to share a verify link for that workspace.

``decode_verify_token`` also validates that ``sub`` is a well-formed
UUID; otherwise an attacker (or a buggy issuer) could pass a non-UUID
that would surface as ``invalid input syntax for type uuid`` from
Postgres, turning a 401 into a 500.
"""

from __future__ import annotations

import time
from uuid import UUID

import jwt
from fastapi import HTTPException, status

from app.config import Settings, get_settings

ISSUER = "decyra-audit"


def issue_verify_token(
    workspace_id: UUID | str,
    settings: Settings | None = None,
    ttl_seconds: int | None = None,
) -> str:
    s = settings or get_settings()
    if not s.audit_verify_secret:
        raise RuntimeError("AUDIT_VERIFY_SECRET not configured")
    now = int(time.time())
    ttl = ttl_seconds or s.audit_verify_token_default_ttl_seconds
    payload = {
        "sub": str(workspace_id),
        "iss": ISSUER,
        "iat": now,
        "exp": now + ttl,
    }
    return jwt.encode(payload, s.audit_verify_secret, algorithm="HS256")


def decode_verify_token(
    token: str, settings: Settings | None = None
) -> str:
    """Return the workspace_id (``sub`` claim). Raise 401 on any error."""
    s = settings or get_settings()
    if not s.audit_verify_secret:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Server verify not configured (AUDIT_VERIFY_SECRET missing)",
        )
    try:
        claims = jwt.decode(
            token,
            s.audit_verify_secret,
            algorithms=["HS256"],
            issuer=ISSUER,
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, detail="Token expired"
        )
    except jwt.InvalidTokenError as e:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, detail=f"Invalid token: {e}"
        )
    sub = claims.get("sub")
    if not sub:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            detail="Token missing 'sub' claim",
        )
    try:
        UUID(sub)
    except (ValueError, TypeError):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            detail="Token 'sub' is not a valid workspace UUID",
        )
    return sub
