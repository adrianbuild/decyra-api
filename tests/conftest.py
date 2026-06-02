"""Pytest fixtures for decyra-api.

Test DB is strictly separate from the dev DB. The DATABASE_URL env
var is overridden BEFORE any ``from app...`` import, so the cached
``Settings()`` instance picks up the test URL.

Schema lifecycle:
- Session start: drop+recreate public schema, drop vector extension,
  then ``alembic upgrade head`` against the test DB. Guarantees we
  test the migration, not stale state.
- Per test: open a connection, begin a transaction, hand it to the
  test as the ``db`` fixture, rollback at teardown. Tests can't bleed
  state into each other.

RLS testing: ``postgres`` is a SUPERUSER and superusers bypass RLS
even with FORCE. The session fixture creates a ``decyra_app`` role
(NOSUPERUSER, NOBYPASSRLS) with full DML on public schema. RLS-aware
tests call ``SET LOCAL ROLE decyra_app`` inside their transaction to
prove the policies actually fire. This mirrors what production will
need (the API must not run as postgres).

Auth (JWKS/ES256):
- A session-scoped ECC P-256 keypair stands in for Supabase's JWKS.
  An autouse function-scope fixture monkeypatches
  ``PyJWKClient.get_signing_key_from_jwt`` to return the test public
  key, so ``decode_token`` exercises real ``jwt.decode`` (signature,
  audience, issuer, expiry) without hitting the network.
- The ``make_token`` fixture signs tokens with the session private key.
"""

from __future__ import annotations

import os
import time
from collections.abc import AsyncIterator, Iterator

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql://postgres:postgres@localhost:55432/decyra_test",
)
os.environ["DATABASE_URL"] = TEST_DATABASE_URL
# Pin the migration URL to the TEST db too. Otherwise pydantic reads
# MIGRATION_DATABASE_URL from .env (the DEV db) and alembic upgrade would
# run against dev while the engine fixture drops/recreates decyra_test —
# leaving the test schema empty. Tests connect as postgres anyway, so the
# migration role == the test DATABASE_URL role.
os.environ["MIGRATION_DATABASE_URL"] = TEST_DATABASE_URL
os.environ.setdefault("SUPABASE_URL", "http://test-supabase.local")
os.environ.setdefault(
    "AUDIT_VERIFY_SECRET",
    "test-verify-secret-padded-to-32-plus-bytes-x",
)

import jwt  # noqa: E402
import pytest  # noqa: E402  (env override must happen first)
import pytest_asyncio  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config as AlembicConfig  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ec  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.engine import Connection, Engine  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.main import app, get_db, get_db_write  # noqa: E402

TEST_ISSUER = f"{get_settings().supabase_url}/auth/v1"


@pytest.fixture(scope="session")
def engine() -> Iterator[Engine]:
    settings = get_settings()
    assert "decyra_test" in settings.database_url, (
        "Test fixture refuses to bind to a non-test database. "
        f"Got: {settings.database_url!r}"
    )
    eng = create_engine(settings.database_url, future=True)

    with eng.begin() as conn:
        conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
        conn.execute(text("DROP EXTENSION IF EXISTS vector"))

    alembic_cfg = AlembicConfig("alembic.ini")
    command.upgrade(alembic_cfg, "head")

    # NOTE: the decyra_app role AND its grants now come from the 2.2c
    # migration (single source of truth). conftest deliberately does NOT
    # grant anything extra — a blanket GRANT ALL here would give decyra_app
    # UPDATE on audit_events and make the append-only-grant test worthless.
    # Tests reach decyra_app via SET LOCAL ROLE on the postgres session.

    yield eng
    eng.dispose()


@pytest.fixture
def db(engine: Engine) -> Iterator[Connection]:
    connection = engine.connect()
    transaction = connection.begin()
    try:
        yield connection
    finally:
        transaction.rollback()
        connection.close()


@pytest.fixture
def app_with_db(db: Connection):
    """FastAPI app with ``get_db`` overridden to the per-test Connection.

    Endpoints under test see the same transaction the test seeded into,
    so reads observe writes and the test-fixture rollback cleans both.
    Auth tests that don't touch the DB are unaffected — the override
    is set but unused.
    """

    def _override():
        yield db

    # Both read (get_db) and write (get_db_write) endpoints share the
    # per-test Connection. The write dependency's real engine.begin()
    # never runs under test; the db fixture's transaction rolls back,
    # so onboarding writes stay isolated.
    app.dependency_overrides[get_db] = _override
    app.dependency_overrides[get_db_write] = _override
    yield app
    app.dependency_overrides.pop(get_db, None)
    app.dependency_overrides.pop(get_db_write, None)


@pytest_asyncio.fixture
async def client(app_with_db) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app_with_db)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture
def app_with_db_decyra_app(db: Connection):
    """Like app_with_db, but the per-request connection drops to decyra_app
    (SET LOCAL ROLE) so endpoint code runs under sharp RLS — proving the
    full request path (endpoint -> get_db -> set_config -> RLS), not just
    the data layer. Asserts authenticity so a silent superuser fallthrough
    fails loud."""

    def _override():
        db.execute(text("SET LOCAL ROLE decyra_app"))
        who = db.execute(
            text("SELECT current_user, current_setting('is_superuser')")
        ).one()
        assert who[0] == "decyra_app" and who[1] == "off", (
            f"expected unprivileged decyra_app, got {who}"
        )
        yield db

    app.dependency_overrides[get_db] = _override
    app.dependency_overrides[get_db_write] = _override
    yield app
    app.dependency_overrides.pop(get_db, None)
    app.dependency_overrides.pop(get_db_write, None)


@pytest_asyncio.fixture
async def client_decyra_app(
    app_with_db_decyra_app,
) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app_with_db_decyra_app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# --- Auth test plumbing -------------------------------------------------


@pytest.fixture(scope="session")
def test_ec_private_key() -> ec.EllipticCurvePrivateKey:
    """Single ECC P-256 keypair for the test session."""
    return ec.generate_private_key(ec.SECP256R1())


class _StubSigningKey:
    def __init__(self, key) -> None:  # type: ignore[no-untyped-def]
        self.key = key


@pytest.fixture(autouse=True)
def _patch_jwks(monkeypatch, test_ec_private_key):  # type: ignore[no-untyped-def]
    """Make PyJWKClient.get_signing_key_from_jwt return the test public key.

    Real jwt.decode still runs (signature, aud, iss, exp all checked),
    only the JWKS-fetch step is short-circuited.
    """
    public_key = test_ec_private_key.public_key()

    def _stub(self, token):  # type: ignore[no-untyped-def]
        return _StubSigningKey(public_key)

    monkeypatch.setattr(
        jwt.PyJWKClient, "get_signing_key_from_jwt", _stub
    )


@pytest.fixture
def make_token(test_ec_private_key):  # type: ignore[no-untyped-def]
    """Mint a Supabase-shaped ES256 JWT for tests."""

    def _make(
        sub: str = "11111111-1111-1111-1111-111111111111",
        email: str | None = "u@test.local",
        exp_offset: int = 3600,
        audience: str = "authenticated",
        issuer: str = TEST_ISSUER,
        signing_key: ec.EllipticCurvePrivateKey | None = None,
    ) -> str:
        now = int(time.time())
        payload: dict[str, object] = {
            "sub": sub,
            "aud": audience,
            "iss": issuer,
            "iat": now,
            "exp": now + exp_offset,
            "role": "authenticated",
        }
        if email is not None:
            payload["email"] = email
        return jwt.encode(
            payload,
            signing_key or test_ec_private_key,
            algorithm="ES256",
        )

    return _make


@pytest.fixture
def make_verify_token():
    """Mint a public verify token (HS256, AUDIT_VERIFY_SECRET)."""
    from app.verify_token import ISSUER, issue_verify_token

    def _make(
        workspace_id: str,
        ttl_seconds: int | None = None,
        secret_override: str | None = None,
    ) -> str:
        if secret_override is None:
            return issue_verify_token(
                workspace_id, ttl_seconds=ttl_seconds
            )
        # Manually encode with a different secret for bad-sig tests.
        now = int(time.time())
        ttl = ttl_seconds if ttl_seconds is not None else 60
        payload: dict[str, object] = {
            "sub": workspace_id,
            "iss": ISSUER,
            "iat": now,
            "exp": now + ttl,
        }
        return jwt.encode(payload, secret_override, algorithm="HS256")

    return _make
