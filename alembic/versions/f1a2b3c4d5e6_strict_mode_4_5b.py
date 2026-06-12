"""strict mode (Task 4.5b): per-row canonical v2 + conversation pii_mode

Two schema changes for the strict PII mode:

1. audit_events gains ``canonical_version`` (v1/v2), ``pii_mode`` and
   ``anonymized``. The hash-chain trigger is replaced to emit **v2**: the
   canonical string now binds pii_mode + anonymized, so the immutable chain
   attests which mode governed each request and whether it was anonymised.

   PER-ROW VERSION, NOT a cutover: the column DEFAULT 'v1' backfills every
   existing production row to exactly what it was hashed as, so live v1 chains
   do NOT break. New rows are v2. ``verify_chain`` reads each row's version and
   recomputes with the matching formula; a mixed v1/v2 chain verifies across
   the boundary. The version literal is INSIDE the hashed string, so the stored
   discriminator is self-protecting.

   The canonical string is the contract: this plpgsql and the Python mirror in
   ``app/audit.py`` (canonical_string, v2 branch) MUST change together.

2. conversations gains a nullable ``pii_mode`` (NULL = inherit the workspace
   default). The per-chat toggle persists here; decyra_app already holds UPDATE
   on conversations (c5d9e1f0a2b3), so no new grant is needed.

Revision ID: f1a2b3c4d5e6
Revises: c5d9e1f0a2b3
Create Date: 2026-06-12
"""
from typing import Sequence, Union

from alembic import op


revision: str = "f1a2b3c4d5e6"
down_revision: Union[str, Sequence[str], None] = "c5d9e1f0a2b3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1a. New columns. DEFAULT 'v1' backfills existing rows (they remain what
    # they were hashed as). pii_mode/anonymized stay NULL on those v1 rows —
    # the v1 formula does not reference them.
    op.execute(
        """
        ALTER TABLE audit_events
            ADD COLUMN canonical_version text NOT NULL DEFAULT 'v1'
                CHECK (canonical_version IN ('v1','v2')),
            ADD COLUMN pii_mode text
                CHECK (pii_mode IS NULL OR pii_mode IN ('sovereign','strict')),
            ADD COLUMN anonymized boolean
        """
    )

    # 1b. Replace the chain trigger to emit v2. Canonical v2 appends a
    # length-prefixed pii_mode (normalised to 'sovereign' if absent, so the
    # stored value matches what was hashed) and a 'true'/'false' anonymized
    # literal. Everything else (advisory lock, predecessor lookup, SECURITY
    # DEFINER, pinned search_path) is unchanged from 36cbe1faa786.
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
            PERFORM pg_advisory_xact_lock(
                hashtext('audit_chain:' || NEW.workspace_id::text)
            );

            SELECT current_hash INTO last_hash
            FROM audit_events
            WHERE workspace_id = NEW.workspace_id
            ORDER BY timestamp DESC, id DESC
            LIMIT 1;

            NEW.prev_hash := last_hash;

            -- Every new row is v2. Normalise the two new fields so the stored
            -- values equal what we hash.
            NEW.canonical_version := 'v2';
            NEW.pii_mode := COALESCE(NEW.pii_mode, 'sovereign');
            NEW.anonymized := COALESCE(NEW.anonymized, false);

            canonical :=
                'v2|' ||
                COALESCE(NEW.prev_hash, '') || '|' ||
                NEW.workspace_id::text || '|' ||
                NEW.user_id::text || '|' ||
                to_char(NEW.timestamp AT TIME ZONE 'UTC',
                        'YYYY-MM-DD"T"HH24:MI:SS.US"Z"') || '|' ||
                octet_length(NEW.model)::text || ':' || NEW.model || '|' ||
                octet_length(NEW.request_text)::text || ':' || NEW.request_text || '|' ||
                octet_length(NEW.response_text)::text || ':' || NEW.response_text || '|' ||
                octet_length(NEW.pii_mode)::text || ':' || NEW.pii_mode || '|' ||
                (CASE WHEN NEW.anonymized THEN 'true' ELSE 'false' END);

            NEW.current_hash := encode(
                sha256(convert_to(canonical, 'UTF8')), 'hex'
            );

            RETURN NEW;
        END;
        $$;
        """
    )

    # 2. Conversation-level mode override (NULL = inherit workspace default).
    op.execute(
        """
        ALTER TABLE conversations
            ADD COLUMN pii_mode text
                CHECK (pii_mode IS NULL OR pii_mode IN ('sovereign','strict'))
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE conversations DROP COLUMN IF EXISTS pii_mode")
    # Restore the v1 trigger (mirror of 36cbe1faa786).
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
            PERFORM pg_advisory_xact_lock(
                hashtext('audit_chain:' || NEW.workspace_id::text)
            );
            SELECT current_hash INTO last_hash
            FROM audit_events
            WHERE workspace_id = NEW.workspace_id
            ORDER BY timestamp DESC, id DESC
            LIMIT 1;
            NEW.prev_hash := last_hash;
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
        "ALTER TABLE audit_events "
        "DROP COLUMN IF EXISTS anonymized, "
        "DROP COLUMN IF EXISTS pii_mode, "
        "DROP COLUMN IF EXISTS canonical_version"
    )
