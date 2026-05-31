"""audit hash chain

Revision ID: 36cbe1faa786
Revises: 313c10e517e1
Create Date: 2026-05-30 17:38:18.268782

Task 3.1 — SHA-256 hash chain on audit_events.

Adds a BEFORE INSERT trigger that derives ``prev_hash`` from the last
row in the same workspace and computes ``current_hash`` over a
length-prefixed canonical string. The canonical string is the
contract; any change here MUST bump the version prefix (``v1`` → ``v2``)
and the Python mirror in ``app/audit.py`` simultaneously.

Notes:
- ``audit_events_hash_chain_insert`` is the only BEFORE INSERT trigger
  on this table. The existing ``audit_events_no_update`` and
  ``audit_events_no_delete`` triggers fire on UPDATE / DELETE
  respectively — no firing-order interaction. No other INSERT
  constraints on the table.
- Tie-break in the ORDER BY is ``id DESC``. ``gen_random_uuid()`` is
  not temporally ordered, so this tie-break is effectively random.
  This is harmless: ``pg_advisory_xact_lock`` above serializes inserts
  per workspace, so two rows with identical microsecond timestamps in
  the same workspace cannot occur from parallel transactions.
- SHA-256 via Postgres built-in ``sha256(bytea)`` (PG 11+). No
  pgcrypto extension needed.
- The function is SECURITY DEFINER with a pinned search_path so the
  predecessor lookup works even when the calling role is
  NOSUPERUSER/NOBYPASSRLS and ``app.current_workspace_id`` happens to
  diverge from ``NEW.workspace_id``.
- This migration also flips ``audit_events.timestamp`` from a ``now()``
  default to ``clock_timestamp()``. ``now()`` / ``transaction_timestamp()``
  returns the transaction-start time, so audit events written in the
  same transaction would share a timestamp, breaking the
  ``ORDER BY timestamp DESC`` determinism the chain trigger relies on
  to pick the immediate predecessor. ``clock_timestamp()`` returns
  wall-clock time at statement execution, ensuring per-event uniqueness.
"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "36cbe1faa786"
down_revision: Union[str, Sequence[str], None] = "313c10e517e1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Statement-time timestamps so multiple events in one transaction
    # do not collide. See module docstring for the full rationale.
    op.execute(
        "ALTER TABLE audit_events "
        "ALTER COLUMN timestamp SET DEFAULT clock_timestamp()"
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION audit_events_hash_chain() RETURNS trigger
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $$
        DECLARE
            last_hash text;
            canonical text;
        BEGIN
            -- Serialize inserts per workspace so two parallel transactions
            -- cannot read the same "last row" and both chain to it. The lock
            -- is transaction-scoped (auto-released on COMMIT/ROLLBACK).
            PERFORM pg_advisory_xact_lock(
                hashtext('audit_chain:' || NEW.workspace_id::text)
            );

            SELECT current_hash INTO last_hash
            FROM audit_events
            WHERE workspace_id = NEW.workspace_id
            ORDER BY timestamp DESC, id DESC
            LIMIT 1;

            NEW.prev_hash := last_hash;  -- NULL for the workspace's genesis

            -- Canonical v1: length-prefixed UTF-8 for free-form text fields.
            -- octet_length() counts UTF-8 bytes; the Python mirror uses
            -- len(s.encode('utf-8')). UUIDs are lowercase-text, timestamps
            -- are ISO-8601 with microsecond precision in UTC.
            canonical :=
                'v1|' ||
                COALESCE(NEW.prev_hash, '') || '|' ||
                NEW.workspace_id::text || '|' ||
                NEW.user_id::text || '|' ||
                to_char(NEW.timestamp AT TIME ZONE 'UTC',
                        'YYYY-MM-DD"T"HH24:MI:SS.US"Z"') || '|' ||
                octet_length(NEW.model)::text || ':' || NEW.model || '|' ||
                octet_length(NEW.request_text)::text || ':' || NEW.request_text || '|' ||
                octet_length(NEW.response_text)::text || ':' || NEW.response_text;

            NEW.current_hash := encode(
                sha256(convert_to(canonical, 'UTF8')), 'hex'
            );

            RETURN NEW;
        END;
        $$;
        """
    )

    op.execute(
        """
        CREATE TRIGGER audit_events_hash_chain_insert
            BEFORE INSERT ON audit_events
            FOR EACH ROW EXECUTE FUNCTION audit_events_hash_chain();
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS audit_events_hash_chain_insert "
        "ON audit_events"
    )
    op.execute("DROP FUNCTION IF EXISTS audit_events_hash_chain()")
    op.execute(
        "ALTER TABLE audit_events "
        "ALTER COLUMN timestamp SET DEFAULT now()"
    )
