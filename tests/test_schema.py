"""Schema-level verification tests for Task 1.3.

All tests run inside the per-test transaction from ``conftest.db``, so
writes are rolled back at teardown.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection


EXPECTED_TABLES = {
    "organizations",
    "workspaces",
    "users",
    "workspace_members",
    "models",
    "audit_events",
    "documents",
    "document_chunks",
}


def test_all_tables_exist(db: Connection) -> None:
    rows = db.execute(
        text(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_type = 'BASE TABLE'"
        )
    ).scalars().all()
    assert EXPECTED_TABLES.issubset(set(rows)), (
        f"Missing: {EXPECTED_TABLES - set(rows)}"
    )


def test_pgvector_installed(db: Connection) -> None:
    name = db.execute(
        text("SELECT extname FROM pg_extension WHERE extname = 'vector'")
    ).scalar()
    assert name == "vector"


def test_document_chunks_embedding_is_vector_1024(db: Connection) -> None:
    # pg_attribute / format_type gives the precise type incl. dimension.
    type_str = db.execute(
        text(
            "SELECT format_type(a.atttypid, a.atttypmod) "
            "FROM pg_attribute a "
            "JOIN pg_class c ON c.oid = a.attrelid "
            "WHERE c.relname = 'document_chunks' AND a.attname = 'embedding'"
        )
    ).scalar()
    assert type_str == "vector(1024)"


def _seed_workspace_with_audit(db: Connection) -> tuple[str, str]:
    org_id = db.execute(
        text("INSERT INTO organizations (name) VALUES ('Acme') RETURNING id")
    ).scalar()
    ws_id = db.execute(text("SELECT gen_random_uuid()")).scalar()
    db.execute(text(f"SET LOCAL app.current_workspace_id = '{ws_id}'"))
    db.execute(
        text(
            "INSERT INTO workspaces (id, organization_id, name) "
            "VALUES (:i, :o, 'Test')"
        ),
        {"i": ws_id, "o": org_id},
    )
    user_id = db.execute(
        text("INSERT INTO users (email) VALUES ('a@b.de') RETURNING id")
    ).scalar()
    db.execute(
        text(
            "INSERT INTO audit_events "
            "(workspace_id, user_id, model, request_text, response_text, routed_to) "
            "VALUES (:w, :u, 'gpt-5', 'req', 'res', 'openai')"
        ),
        {"w": ws_id, "u": user_id},
    )
    return ws_id, user_id


def test_audit_events_rejects_update(db: Connection) -> None:
    _seed_workspace_with_audit(db)

    sp = db.begin_nested()
    with pytest.raises(Exception) as exc:
        db.execute(text("UPDATE audit_events SET request_text = 'x'"))
    assert "append-only" in str(exc.value)
    sp.rollback()


def test_audit_events_rejects_delete(db: Connection) -> None:
    _seed_workspace_with_audit(db)

    sp = db.begin_nested()
    with pytest.raises(Exception) as exc:
        db.execute(text("DELETE FROM audit_events"))
    assert "append-only" in str(exc.value)
    sp.rollback()


def test_workspace_id_isolation_via_rls(db: Connection) -> None:
    """RLS sanity check: a query under workspace A's GUC sees only A's rows.

    Runs as ``decyra_app`` (no SUPERUSER, no BYPASSRLS). ``postgres`` would
    bypass RLS even with FORCE; this test proves the policy actually fires
    for an application-tier role.
    """
    org_id = db.execute(
        text("INSERT INTO organizations (name) VALUES ('Acme') RETURNING id")
    ).scalar()
    user_id = db.execute(
        text("INSERT INTO users (email) VALUES ('iso@test.de') RETURNING id")
    ).scalar()

    ws_a = db.execute(text("SELECT gen_random_uuid()")).scalar()
    ws_b = db.execute(text("SELECT gen_random_uuid()")).scalar()

    db.execute(text("SET LOCAL ROLE decyra_app"))

    db.execute(text(f"SET LOCAL app.current_workspace_id = '{ws_a}'"))
    db.execute(
        text(
            "INSERT INTO workspaces (id, organization_id, name) "
            "VALUES (:i, :o, 'A')"
        ),
        {"i": ws_a, "o": org_id},
    )
    db.execute(
        text(
            "INSERT INTO documents (workspace_id, filename, uploaded_by, "
            "storage_key, mime_type, size_bytes, extracted_text, extraction_status) "
            "VALUES (:w, 'a.pdf', :u, 'k-a', 'application/pdf', 0, '', 'ok')"
        ),
        {"w": ws_a, "u": user_id},
    )

    db.execute(text(f"SET LOCAL app.current_workspace_id = '{ws_b}'"))
    db.execute(
        text(
            "INSERT INTO workspaces (id, organization_id, name) "
            "VALUES (:i, :o, 'B')"
        ),
        {"i": ws_b, "o": org_id},
    )
    db.execute(
        text(
            "INSERT INTO documents (workspace_id, filename, uploaded_by, "
            "storage_key, mime_type, size_bytes, extracted_text, extraction_status) "
            "VALUES (:w, 'b.pdf', :u, 'k-b', 'application/pdf', 0, '', 'ok')"
        ),
        {"w": ws_b, "u": user_id},
    )

    db.execute(text(f"SET LOCAL app.current_workspace_id = '{ws_a}'"))
    visible = db.execute(text("SELECT filename FROM documents")).scalars().all()
    assert visible == ["a.pdf"], (
        f"RLS leak: workspace A sees {visible} (expected only ['a.pdf'])"
    )

    db.execute(text(f"SET LOCAL app.current_workspace_id = '{ws_b}'"))
    visible = db.execute(text("SELECT filename FROM documents")).scalars().all()
    assert visible == ["b.pdf"], (
        f"RLS leak: workspace B sees {visible} (expected only ['b.pdf'])"
    )
