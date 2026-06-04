from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from uuid import UUID

import litellm
from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection, Engine

from app import chat, invitations, mail
from app.audit import verify_workspace_chain
from app.auth import AuthenticatedUser, get_current_user
from app.config import Settings, get_settings
from app.llm import configure_litellm
from app.onboarding import ensure_workspace
from app.verify_token import decode_verify_token


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Push provider keys from Settings into os.environ so litellm finds
    # them. Without this litellm has no credentials.
    configure_litellm()
    yield


app = FastAPI(title="Decyra API", lifespan=lifespan)

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


def set_org_context(db: Connection, organization_id: object) -> None:
    """Transaction-local RLS context for ORG-scoped tables (invitations).
    Bound param, never an f-string. Separate GUC from the workspace one:
    org data <-> org context, workspace data <-> workspace context."""
    db.execute(
        text("SELECT set_config('app.current_organization_id', :org, true)"),
        {"org": str(organization_id)},
    )


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/me")
def me(
    user: AuthenticatedUser = Depends(get_current_user),
) -> dict[str, str | None]:
    return {"user_id": user.user_id, "email": user.email}


@app.get("/models")
def list_models(
    user: AuthenticatedUser = Depends(get_current_user),
    db: Connection = Depends(get_db),
) -> list[dict]:
    """Enabled models for the model picker. The ``models`` table is RLS-free
    (Task 1.3) — no workspace context, no membership check; any authenticated
    user may see the available models. eu_hosted/sovereign_eligible are
    deliberately omitted (a later one-liner adds them for the sovereign
    badge)."""
    rows = db.execute(
        text(
            "SELECT name, provider FROM models WHERE enabled = true "
            "ORDER BY provider, name"
        )
    ).all()
    return [{"name": r.name, "provider": r.provider} for r in rows]


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


class InvitationCreate(BaseModel):
    email: str
    role: str


@app.post("/invitations")
def create_invitation(
    payload: InvitationCreate,
    user: AuthenticatedUser = Depends(get_current_user),
    db: Connection = Depends(get_db_write),
    settings: Settings = Depends(get_settings),
) -> dict:
    """Create an email-bound invitation (Owner/Admin only). Sends a mail
    via Mailpit best-effort and returns the invitation incl. a link the
    owner can also share manually."""
    member = invitations.resolve_membership(db, user.user_id)
    invitations.require_role(member, invitations.MANAGER_ROLES)
    if payload.role not in invitations.INVITABLE_ROLES:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="role must be one of: admin, user",
        )
    assert member is not None  # require_role raised otherwise
    set_org_context(db, member.organization_id)
    inv = invitations.create_invitation(
        db, member.organization_id, payload.email, payload.role, user.user_id
    )
    # Best-effort: a down SMTP must not roll back the created invitation.
    try:
        mail.send_invitation_email(
            payload.email, inv["token"], payload.role, settings
        )
        inv["mail_sent"] = True
    except Exception:
        inv["mail_sent"] = False
    inv["invite_link"] = f"{settings.app_base_url}/login?invite={inv['token']}"
    return inv


@app.get("/invitations")
def list_invitations(
    user: AuthenticatedUser = Depends(get_current_user),
    db: Connection = Depends(get_db),
) -> list[dict]:
    """List the own org's pending invitations (Owner/Admin only)."""
    member = invitations.resolve_membership(db, user.user_id)
    invitations.require_role(member, invitations.MANAGER_ROLES)
    assert member is not None
    set_org_context(db, member.organization_id)
    return invitations.list_pending_invitations(db, member.organization_id)


@app.post("/invitations/{token}/revoke")
def revoke_invitation(
    token: str,
    user: AuthenticatedUser = Depends(get_current_user),
    db: Connection = Depends(get_db_write),
) -> dict[str, bool]:
    """Revoke a pending invitation (Owner/Admin only). RLS scopes this to
    the caller's org — a foreign-org token yields 404."""
    member = invitations.resolve_membership(db, user.user_id)
    invitations.require_role(member, invitations.MANAGER_ROLES)
    assert member is not None
    set_org_context(db, member.organization_id)
    if not invitations.revoke_invitation(db, token, member.organization_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="invitation not found")
    return {"revoked": True}


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatCompletionRequest(BaseModel):
    model: str
    messages: list[ChatMessage]
    temperature: float | None = None
    max_tokens: int | None = None
    stream: bool | None = None
    conversation_id: str | None = None  # Decyra extension


@app.post("/v1/chat/completions")
def chat_completions(
    payload: ChatCompletionRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    db_read: Connection = Depends(get_db),
    db_write: Connection = Depends(get_db_write),
) -> dict:
    """OpenAI-compatible chat proxy (non-streaming). EVERY call persists
    the turn and writes an audit event — no path chats without auditing.

    Two DB connections: db_read for history/ownership, db_write for the
    persist. The LLM call happens between them, while db_write is still
    idle (no statements, no locks) — the hash-chain advisory lock is only
    taken at the final audit INSERT.
    """
    if payload.stream:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="streaming is not supported here (see Task 4.4)",
        )

    member = invitations.resolve_membership(db_read, user.user_id)
    if member is None:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, detail="no workspace membership"
        )
    ws = member.workspace_id

    model_row = db_read.execute(
        text(
            "SELECT provider, cost_input, cost_output FROM models "
            "WHERE name = :m AND enabled = true"
        ),
        {"m": payload.model},
    ).one_or_none()
    if model_row is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=f"model '{payload.model}' is not available",
        )

    new_messages = [
        {"role": m.role, "content": m.content} for m in payload.messages
    ]

    # History + ownership (Decyra mode). conversation_id wins over the
    # messages array: stored history is always prepended.
    set_workspace_context(db_read, ws)
    if payload.conversation_id:
        try:
            UUID(payload.conversation_id)
        except ValueError:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, detail="conversation not found"
            )
        owner = chat.load_conversation_owner(db_read, payload.conversation_id)
        if owner is None or owner != user.user_id:
            # 404 (not 403): don't reveal a colleague's conversation exists.
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, detail="conversation not found"
            )
        llm_input = chat.load_history(db_read, payload.conversation_id) + new_messages
    else:
        llm_input = new_messages

    # LLM call — db_write still idle.
    kwargs: dict = {"model": payload.model, "messages": llm_input}
    if payload.temperature is not None:
        kwargs["temperature"] = payload.temperature
    if payload.max_tokens is not None:
        kwargs["max_tokens"] = payload.max_tokens
    resp = litellm.completion(**kwargs)
    assistant_content = resp.choices[0].message.content or ""
    cost = chat.compute_cost(
        resp.usage.prompt_tokens,
        resp.usage.completion_tokens,
        model_row.cost_input,
        model_row.cost_output,
    )

    # WRITE block (first statements on db_write). messages.workspace_id is
    # always ws (the conversation's workspace), never request-derived.
    set_workspace_context(db_write, ws)
    if payload.conversation_id:
        cid = payload.conversation_id
    else:
        cid = chat.create_conversation(
            db_write, ws, user.user_id, chat.derive_title(new_messages)
        )
    for m in chat.persistable_messages(new_messages):
        chat.insert_message(db_write, cid, ws, m["role"], m["content"])
    chat.insert_message(
        db_write,
        cid,
        ws,
        "assistant",
        assistant_content,
        model=payload.model,
        prompt_tokens=resp.usage.prompt_tokens,
        completion_tokens=resp.usage.completion_tokens,
        cost=cost,
    )
    chat.touch_conversation(db_write, cid)
    chat.insert_audit_event(
        db_write,
        ws,
        user.user_id,
        payload.model,
        chat.audit_request_text(new_messages),
        assistant_content,
        model_row.provider,
    )
    return chat.build_openai_response(resp, payload.model, cid)


@app.get("/conversations")
def list_conversations(
    user: AuthenticatedUser = Depends(get_current_user),
    db: Connection = Depends(get_db),
) -> list[dict]:
    """The caller's OWN conversations (private). RLS scopes to the
    workspace; the explicit user_id filter is the privacy layer."""
    member = invitations.resolve_membership(db, user.user_id)
    if member is None:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, detail="no workspace membership"
        )
    set_workspace_context(db, member.workspace_id)
    rows = db.execute(
        text(
            "SELECT id, title, created_at, updated_at FROM conversations "
            "WHERE user_id = :u ORDER BY updated_at DESC"
        ),
        {"u": user.user_id},
    ).all()
    return [
        {
            "id": str(r.id),
            "title": r.title,
            "created_at": r.created_at.isoformat(),
            "updated_at": r.updated_at.isoformat(),
        }
        for r in rows
    ]


@app.get("/conversations/{conversation_id}")
def get_conversation(
    conversation_id: UUID,
    user: AuthenticatedUser = Depends(get_current_user),
    db: Connection = Depends(get_db),
) -> dict:
    member = invitations.resolve_membership(db, user.user_id)
    if member is None:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, detail="no workspace membership"
        )
    set_workspace_context(db, member.workspace_id)
    conv = db.execute(
        text(
            "SELECT id, title, created_at, updated_at FROM conversations "
            "WHERE id = :c AND user_id = :u"
        ),
        {"c": str(conversation_id), "u": user.user_id},
    ).one_or_none()
    if conv is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail="conversation not found"
        )
    msgs = db.execute(
        text(
            "SELECT role, content, model, created_at FROM messages "
            "WHERE conversation_id = :c ORDER BY created_at ASC, id ASC"
        ),
        {"c": str(conversation_id)},
    ).all()
    return {
        "id": str(conv.id),
        "title": conv.title,
        "created_at": conv.created_at.isoformat(),
        "updated_at": conv.updated_at.isoformat(),
        "messages": [
            {
                "role": m.role,
                "content": m.content,
                "model": m.model,
                "created_at": m.created_at.isoformat(),
            }
            for m in msgs
        ],
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
