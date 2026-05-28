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
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterator

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql://postgres:postgres@localhost:55432/decyra_test",
)
os.environ["DATABASE_URL"] = TEST_DATABASE_URL

import pytest  # noqa: E402  (env override must happen first)
import pytest_asyncio  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config as AlembicConfig  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.engine import Connection, Engine  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.main import app  # noqa: E402


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

    with eng.begin() as conn:
        conn.execute(text("""
            DO $$
            BEGIN
                IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'decyra_app') THEN
                    CREATE ROLE decyra_app NOLOGIN NOSUPERUSER NOBYPASSRLS;
                END IF;
            END
            $$;
        """))
        conn.execute(text("GRANT USAGE ON SCHEMA public TO decyra_app"))
        conn.execute(
            text("GRANT ALL ON ALL TABLES IN SCHEMA public TO decyra_app")
        )

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


@pytest_asyncio.fixture
async def client() -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
