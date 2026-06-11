import logging
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from typing import Callable, ContextManager
from uuid import UUID

import litellm
from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection, Engine

from app import chat, invitations, mail, pii
from app.audit import verify_workspace_chain
from app.auth import AuthenticatedUser, get_current_user
from app.config import Settings, get_settings
from app.llm import configure_litellm
from app.onboarding import ensure_workspace
from app.verify_token import decode_verify_token

logger = logging.getLogger("decyra.chat")


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


def get_write_txn(
    settings: Settings = Depends(get_settings),
) -> Callable[[], ContextManager[Connection]]:
    """Return a FACTORY that opens a short write transaction on demand —
    not a yield-dependency. A StreamingResponse finalises yield-deps only
    after the whole body is sent; using ``get_db_write`` would hold the
    transaction (and idle the connection) across the entire stream. The
    factory lets the streaming generator open the transaction AFTER the
    last chunk, so the hash-chain advisory lock is held for milliseconds.

    The non-streaming chat path uses the same factory (one mechanism). In
    tests this dependency is overridden to yield the per-test Connection,
    so the streaming write never opens its own ``engine.begin()`` and the
    fixture rollback keeps isolation. ``onboarding`` still uses
    ``get_db_write`` unchanged.
    """

    @contextmanager
    def _open() -> Iterator[Connection]:
        global _engine
        if _engine is None:
            _engine = create_engine(settings.database_url, future=True)
        with _engine.begin() as conn:
            yield conn

    return _open


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


def _pii_mode(db: Connection, workspace_id: str) -> str:
    """workspaces.settings->>'pii_mode' with read-validation: anything
    unknown/missing defaults to 'sovereign' (secure default, also covers
    existing workspaces whose settings is '{}')."""
    row = db.execute(
        text("SELECT settings->>'pii_mode' AS m FROM workspaces WHERE id = :w"),
        {"w": workspace_id},
    ).one_or_none()
    mode = row.m if row is not None else None
    return mode if mode in ("sovereign", "strict") else "sovereign"


def _resolve_effective_model(
    db: Connection,
    *,
    chosen_model: str,
    chosen_row,
    outcome: pii.PiiOutcome,
    settings: Settings,
):
    """Reroute decision (Task 4.5a). Returns (effective_model, effective_row).
    Reroute only when protection is needed AND the chosen model is not itself
    sovereign-eligible. If no sovereign target is enabled -> 503 (never fall
    back to a non-sovereign model)."""
    if not outcome.needs_protection or chosen_row.sovereign_eligible:
        return chosen_model, chosen_row
    target = db.execute(
        text(
            "SELECT provider, cost_input, cost_output, eu_hosted, "
            "sovereign_eligible FROM models "
            "WHERE name = :m AND enabled = true AND sovereign_eligible = true"
        ),
        {"m": settings.sovereign_model},
    ).one_or_none()
    if target is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="no sovereign EU model available for PII routing",
        )
    return settings.sovereign_model, target


def _stream_turn(
    *,
    kwargs: dict,
    open_txn: Callable[[], ContextManager[Connection]],
    ws: str,
    user_id: str,
    existing_cid: str | None,
    new_messages: list[dict],
    llm_input: list[dict],
    model: str,
    provider: str,
    cost_input: float,
    cost_output: float,
    pii_detected: bool,
    status_block: dict,
) -> Iterator[str]:
    """Stream provider chunks to the client AND collect them; after the
    stream, persist+audit the assembled answer in ONE short transaction
    (the 4.3 write block, reused via ``chat.persist_stream_turn``). Three
    exit paths share the same persist:

    - client abort (GeneratorExit): persist the collected partial, no yield
      (yielding during GeneratorExit is illegal), then re-raise.
    - provider abort (Exception): the partial was SEEN by the user, so it is
      persisted+audited; the client gets an ``error`` event (no [DONE]).
    - clean finish (else): persist the full answer, then the final chunk
      (carrying the authoritative conversation_id) + [DONE].
    """
    collected: list = []

    def _persist() -> str | None:
        with open_txn() as db_write:
            set_workspace_context(db_write, ws)
            return chat.persist_stream_turn(
                db_write,
                ws,
                user_id,
                existing_cid,
                new_messages,
                collected,
                llm_input,
                model=model,
                provider=provider,
                cost_input=cost_input,
                cost_output=cost_output,
                pii_detected=pii_detected,
            )

    # First event: the PII/routing status (decision is known pre-stream), so
    # the client can show the notice before any token arrives.
    yield chat.sse_status(status_block)
    try:
        stream = litellm.completion(
            **kwargs, stream=True, stream_options={"include_usage": True}
        )
        for chunk in stream:
            collected.append(chunk)
            yield chat.sse_chunk(chunk, existing_cid)
    except GeneratorExit:
        _persist()
        raise
    except Exception as e:
        cid = _persist()
        yield chat.sse_error(str(e), cid or existing_cid)
        return
    cid = _persist()
    yield chat.sse_final(cid or existing_cid, model)
    yield chat.sse_done()


@app.post("/v1/chat/completions")
def chat_completions(
    payload: ChatCompletionRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    db_read: Connection = Depends(get_db),
    open_txn: Callable[[], ContextManager[Connection]] = Depends(get_write_txn),
    settings: Settings = Depends(get_settings),
):
    """OpenAI-compatible chat proxy. EVERY call (streaming or not) persists
    the turn and writes an audit event — no path chats without auditing.

    Shared validation runs on db_read FIRST, so 403/400/404 are real HTTP
    statuses returned before any stream begins. Then: stream=true -> SSE
    StreamingResponse; otherwise the non-streaming 4.3 path. Both open a
    SHORT write transaction via ``open_txn()`` so the hash-chain advisory
    lock is held only for the persist (never across the stream).
    """
    member = invitations.resolve_membership(db_read, user.user_id)
    if member is None:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, detail="no workspace membership"
        )
    ws = member.workspace_id

    model_row = db_read.execute(
        text(
            "SELECT provider, cost_input, cost_output, eu_hosted, "
            "sovereign_eligible FROM models "
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
    existing_cid: str | None = None
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
        existing_cid = payload.conversation_id
        llm_input = chat.load_history(db_read, payload.conversation_id) + new_messages
    else:
        llm_input = new_messages

    # --- PII check on the FULL llm_input (history + new) — Invariant 1 ---
    # Runs in both sovereign and strict modes (in 4.5a strict behaves like
    # sovereign). Scanning the whole input gives the one-way ratchet for
    # free AND is necessary: history is re-sent, so a later "clean" turn
    # must not leak earlier PII to a non-sovereign model.
    _mode = _pii_mode(db_read, ws)  # noqa: F841 — both modes protect in 4.5a
    full_text = "\n".join(m["content"] for m in llm_input if m.get("content"))
    outcome = pii.contains_pii(full_text, settings)
    effective_model, eff_row = _resolve_effective_model(
        db_read,
        chosen_model=payload.model,
        chosen_row=model_row,
        outcome=outcome,
        settings=settings,
    )
    if effective_model != payload.model:
        if outcome.status == "unavailable":
            logger.warning(
                "PII check unavailable — fail-safe reroute %s -> %s (ws=%s)",
                payload.model, effective_model, ws,
            )
        else:
            logger.info(
                "PII detected — reroute %s -> %s (ws=%s)",
                payload.model, effective_model, ws,
            )
    status_block = chat.pii_status(
        pii_detected=outcome.detected,
        pii_check=outcome.status,
        routed_to=eff_row.provider,
        effective_model=effective_model,
    )

    kwargs: dict = {"model": effective_model, "messages": llm_input}
    if payload.temperature is not None:
        kwargs["temperature"] = payload.temperature
    if payload.max_tokens is not None:
        kwargs["max_tokens"] = payload.max_tokens

    # --- Streaming path (Task 4.4 + 4.5a) ------------------------------
    if payload.stream:
        return StreamingResponse(
            _stream_turn(
                kwargs=kwargs,
                open_txn=open_txn,
                ws=ws,
                user_id=user.user_id,
                existing_cid=existing_cid,
                new_messages=new_messages,
                llm_input=llm_input,
                model=effective_model,
                provider=eff_row.provider,
                cost_input=eff_row.cost_input,
                cost_output=eff_row.cost_output,
                pii_detected=outcome.detected,
                status_block=status_block,
            ),
            media_type="text/event-stream",
        )

    # --- Non-streaming path (4.3 write block; effective model) ---------
    resp = litellm.completion(**kwargs)
    assistant_content = resp.choices[0].message.content or ""
    cost = chat.compute_cost(
        resp.usage.prompt_tokens,
        resp.usage.completion_tokens,
        eff_row.cost_input,
        eff_row.cost_output,
    )

    # messages.workspace_id is always ws (the conversation's workspace),
    # never request-derived. Short transaction via the same factory the
    # streaming path uses.
    with open_txn() as db_write:
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
            model=effective_model,
            prompt_tokens=resp.usage.prompt_tokens,
            completion_tokens=resp.usage.completion_tokens,
            cost=cost,
        )
        chat.touch_conversation(db_write, cid)
        chat.insert_audit_event(
            db_write,
            ws,
            user.user_id,
            effective_model,
            chat.audit_request_text(new_messages),
            assistant_content,
            eff_row.provider,
            outcome.detected,
        )
    return chat.build_openai_response(
        resp, effective_model, cid, status_block
    )


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
