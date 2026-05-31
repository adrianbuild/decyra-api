from collections.abc import Iterator
from uuid import UUID

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import create_engine
from sqlalchemy.engine import Connection, Engine

from app.audit import verify_workspace_chain
from app.auth import AuthenticatedUser, get_current_user
from app.config import Settings, get_settings
from app.verify_token import decode_verify_token

app = FastAPI(title="Decyra API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


_engine: Engine | None = None


def get_db(
    settings: Settings = Depends(get_settings),
) -> Iterator[Connection]:
    """Per-request DB connection. Verify endpoints are read-only so we
    rollback at request end as a defensive default.

    TODO (Security-Härtung vor Pilot): split DATABASE_URL into
    MIGRATION_DATABASE_URL (postgres) and a runtime URL connecting as
    ``decyra_app`` (NOSUPERUSER NOBYPASSRLS), then SET LOCAL ROLE +
    SET LOCAL app.current_workspace_id per request so RLS actually
    fires. See PROGRESS.md > Security-Härtung vor Pilot.
    """
    global _engine
    if _engine is None:
        _engine = create_engine(settings.database_url, future=True)
    with _engine.connect() as conn:
        try:
            yield conn
        finally:
            conn.rollback()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/me")
def me(
    user: AuthenticatedUser = Depends(get_current_user),
) -> dict[str, str | None]:
    return {"user_id": user.user_id, "email": user.email}


@app.get("/workspaces/{workspace_id}/audit/verify")
def verify_workspace_audit(
    workspace_id: UUID,
    user: AuthenticatedUser = Depends(get_current_user),
    db: Connection = Depends(get_db),
) -> dict[str, object]:
    """Internal verify endpoint — JWT-authenticated.

    TODO (Security-Härtung vor Pilot): enforce workspace_members
    membership for ``user.user_id``. Currently any authenticated user
    can verify any workspace. See PROGRESS.md > Security-Härtung vor
    Pilot.
    """
    result = verify_workspace_chain(db, workspace_id)
    return {
        "valid": result.valid,
        "event_count": result.event_count,
        "broken_at": result.broken_at,
    }


@app.get("/v/{token}")
def public_verify(
    token: str,
    db: Connection = Depends(get_db),
) -> dict[str, object]:
    """Public verify endpoint — token-only, no Supabase auth."""
    workspace_id = decode_verify_token(token)
    result = verify_workspace_chain(db, workspace_id)
    return {
        "workspace_id": workspace_id,
        "valid": result.valid,
        "event_count": result.event_count,
        "broken_at": result.broken_at,
    }
