from collections.abc import Iterator
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection, Engine

from app.audit import verify_workspace_chain
from app.auth import AuthenticatedUser, get_current_user
from app.config import Settings, get_settings
from app.onboarding import ensure_workspace
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


def get_db_write(
    settings: Settings = Depends(get_settings),
) -> Iterator[Connection]:
    """Per-request DB connection for WRITE endpoints (onboarding).

    ``engine.begin()`` owns the transaction: it commits on a clean exit
    and rolls back if the endpoint raises (FastAPI throws the exception
    into this generator at the yield). The endpoint must NOT commit
    itself — in tests this dependency is overridden onto the per-test
    Connection, whose fixture rolls back for isolation; a manual commit
    would defeat that. Shares the same module-level ``_engine`` as
    ``get_db`` (one engine, lazily initialised).
    """
    global _engine
    if _engine is None:
        _engine = create_engine(settings.database_url, future=True)
    with _engine.begin() as conn:
        yield conn


def set_workspace_context(db: Connection, workspace_id: object) -> None:
    """Set the transaction-local RLS context for workspace-scoped queries.

    Uses set_config(..., is_local=true) with a BOUND parameter — never an
    f-string, or the workspace_id would be a SQL-injection vector. The
    is_local=true makes it transaction-scoped, so no bleed across pooled
    connections. Under decyra_app this is what scopes RLS; with the GUC
    unset, the policies match nothing (secure default).
    """
    db.execute(
        text("SELECT set_config('app.current_workspace_id', :ws, true)"),
        {"ws": str(workspace_id)},
    )


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/me")
def me(
    user: AuthenticatedUser = Depends(get_current_user),
) -> dict[str, str | None]:
    return {"user_id": user.user_id, "email": user.email}


@app.post("/onboarding")
def onboarding(
    user: AuthenticatedUser = Depends(get_current_user),
    db: Connection = Depends(get_db_write),
) -> dict[str, object]:
    """Idempotently provision the user's tenant hierarchy.

    First call for a new user creates users+org+workspace+owner-membership
    in one transaction; subsequent calls return the existing workspace
    without writing. Safe to call on every dashboard load.
    """
    if not user.email:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="Onboarding requires an email claim",
        )
    result = ensure_workspace(db, user.user_id, user.email)
    return {
        "workspace_id": result.workspace_id,
        "workspace_name": result.workspace_name,
        "created": result.created,
    }


@app.get("/workspaces/{workspace_id}/audit/verify")
def verify_workspace_audit(
    workspace_id: UUID,
    user: AuthenticatedUser = Depends(get_current_user),
    db: Connection = Depends(get_db),
) -> dict[str, object]:
    """Internal verify endpoint — JWT-authenticated, member-only.

    A valid JWT is not enough: the user must be a member of the
    workspace being verified, else 403. workspace_members is populated
    by POST /onboarding (Task 2.2b).
    """
    set_workspace_context(db, workspace_id)
    member = db.execute(
        text(
            "SELECT 1 FROM workspace_members "
            "WHERE user_id = :u AND workspace_id = :w"
        ),
        {"u": user.user_id, "w": str(workspace_id)},
    ).first()
    if member is None:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail="Not a member of this workspace",
        )
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
    set_workspace_context(db, workspace_id)
    result = verify_workspace_chain(db, workspace_id)
    return {
        "workspace_id": workspace_id,
        "valid": result.valid,
        "event_count": result.event_count,
        "broken_at": result.broken_at,
    }
