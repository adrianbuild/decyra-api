"""Task 5B.2 Sub-Task 6 — append-only audit of code-interpreter executions.

One row per sandbox run is appended to ``code_execution_events``. The governing
compliance decision (see the migration): the row stores the LLM-GENERATED code
(the ``code`` the model returned, BEFORE de-anonymisation) — NOT the
de-anonymised code that actually ran. In strict mode that generated code carries
PLACEHOLDER column names, so NO raw column name (PII) ever rests here; in
sovereign mode there is no anonymiser, so generated == executed (real columns).
The de-anonymisation map is NEVER stored. The chart is recorded only as a
SHA-256 HASH, never the raw PNG bytes.

Invariant: ``workspace_id`` is ALWAYS passed in by the caller (derived from the
authenticated request's resolved membership), never request-derived — the same
rule as ``attachments.insert_attachment`` / ``chat.insert_audit_event``.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Connection


def insert_code_execution_event(
    db: Connection,
    *,
    workspace_id: str,
    user_id: str,
    status: str,
    generated_code: str,
    chart_sha256: str | None,
) -> str:
    """Append one ``code_execution_events`` row and return its new UUID string.

    ``generated_code`` MUST be the LLM-generated code (pre de-anonymisation) —
    never the de-anonymised ``run_code`` that executed. ``chart_sha256`` is the
    hex SHA-256 of the produced chart PNG, or ``None`` when there was no chart.
    ``status`` is one of ok|error|timeout|killed|no_chart.

    Bound parameters only; ``workspace_id`` is supplied by the caller (never from
    the request body) and the table's RLS WITH CHECK independently enforces that
    it matches the session's ``app.current_workspace_id``.
    """
    new_id = str(
        db.execute(
            text(
                "INSERT INTO code_execution_events "
                "(workspace_id, user_id, status, generated_code, chart_sha256) "
                "VALUES (:w, :u, :st, :code, :sha) "
                "RETURNING id"
            ),
            {
                "w": workspace_id,
                "u": user_id,
                "st": status,
                "code": generated_code,
                "sha": chart_sha256,
            },
        ).scalar_one()
    )
    return new_id
