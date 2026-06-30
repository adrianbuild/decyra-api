"""Task 5B.2 Sub-Task 6 — append-only ``code_execution_events`` audit.

THE GOVERNING COMPLIANCE DECISION (proven here): every sandbox execution is one
audit event, and the event stores the **LLM-GENERATED** code (the ``code`` the
model returned, BEFORE de-anonymisation) — NOT the de-anonymised ``run_code``
that actually ran. In strict mode the generated code references PLACEHOLDER
column names (``[[DCY_PERSON_0]]``), so NO raw column name (PII) ever rests in
the audit — mirroring how ``audit_events`` holds the anonymised ``request_text``.
The de-anonymisation map is NEVER stored (storing it would make the placeholders
reversible and defeat the protection). In sovereign mode there is no anonymiser,
so generated code == executed code (real columns) — consistent with sovereign
holding real EU-resident data. No PII-at-rest door via the code interpreter.

The audit also stores the chart as a SHA-256 HASH (a reference), never the raw
PNG bytes.
"""

from __future__ import annotations

import hashlib
import json

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection

from app import analysis, code_audit
from app.sandbox.runner import SandboxResult
from tests._helpers import add_member, seed_workspace
from tests.test_pii_routing import (
    CHOSEN,
    USER_A,
    _auth,
    _seed_model,
    _seed_sovereign,
)


# =====================================================================
# DB-level audit helper proofs (tests 1, 3 partial, 5, 6)
# =====================================================================


def _set_ctx(db: Connection, ws: str) -> None:
    db.execute(
        text("SELECT set_config('app.current_workspace_id', :w, true)"),
        {"w": ws},
    )


def test_code_execution_event_appended(db: Connection) -> None:
    """A successful analysis row: status='ok', generated_code non-empty,
    chart_sha256 == hex sha256 of the chart bytes, event_type='code_execution'."""
    ws, user = seed_workspace(db)
    add_member(db, ws, user)
    _set_ctx(db, ws)

    chart_bytes = b"\x89PNG_chart_bytes_here"
    digest = hashlib.sha256(chart_bytes).hexdigest()

    code_audit.insert_code_execution_event(
        db,
        workspace_id=ws,
        user_id=user,
        status="ok",
        generated_code="df['[[DCY_PERSON_0]]'].plot()",
        chart_sha256=digest,
    )

    row = db.execute(
        text(
            "SELECT event_type, status, generated_code, chart_sha256 "
            "FROM code_execution_events"
        )
    ).one()
    assert row.event_type == "code_execution"
    assert row.status == "ok"
    assert row.generated_code == "df['[[DCY_PERSON_0]]'].plot()"
    assert row.chart_sha256 == digest


def test_code_execution_event_columns_have_no_image_or_map(db: Connection) -> None:
    """Schema check: there is NO column that could hold the raw chart bytes or a
    de-anonymisation map. Only a hash reference exists for the chart."""
    cols = {
        r.column_name
        for r in db.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'code_execution_events'"
            )
        ).all()
    }
    assert cols == {
        "id",
        "workspace_id",
        "user_id",
        "event_type",
        "status",
        "generated_code",
        "chart_sha256",
        "created_at",
    }
    # No image / map columns exist by ANY plausible name.
    for forbidden in ("chart_png", "chart", "image", "png", "deanon_map",
                      "deanonymization_map", "anon_map", "mapping"):
        assert forbidden not in cols


def test_code_execution_event_rls_workspace_scoped(db: Connection) -> None:
    """An event written for workspace A is invisible under workspace B's RLS
    context (and vice-versa)."""
    ws_a, user_a = seed_workspace(db)
    add_member(db, ws_a, user_a)

    # Second workspace (distinct email to dodge the users unique constraint).
    org_b = db.execute(
        text("INSERT INTO organizations (name) VALUES ('Beta') RETURNING id")
    ).scalar_one()
    ws_b = str(db.execute(text("SELECT gen_random_uuid()")).scalar_one())
    db.execute(text(f"SET LOCAL app.current_workspace_id = '{ws_b}'"))
    db.execute(
        text(
            "INSERT INTO workspaces (id, organization_id, name) "
            "VALUES (:i, :o, 'B')"
        ),
        {"i": ws_b, "o": org_b},
    )
    user_b = str(
        db.execute(
            text("INSERT INTO users (email) VALUES ('b@b.de') RETURNING id")
        ).scalar_one()
    )
    add_member(db, ws_b, user_b)

    # Seed one event in each workspace (as postgres; RLS is enforced on read
    # below under the unprivileged role — superusers bypass RLS even with FORCE).
    _set_ctx(db, ws_a)
    code_audit.insert_code_execution_event(
        db, workspace_id=ws_a, user_id=user_a, status="ok",
        generated_code="A-code", chart_sha256="a" * 64,
    )
    _set_ctx(db, ws_b)
    code_audit.insert_code_execution_event(
        db, workspace_id=ws_b, user_id=user_b, status="error",
        generated_code="B-code", chart_sha256=None,
    )

    # Drop to the unprivileged role so the RLS policy actually fires.
    db.execute(text("SET LOCAL ROLE decyra_app"))
    _assert_unprivileged(db)

    # Under A's context only A's row is visible.
    _set_ctx(db, ws_a)
    seen_a = db.execute(
        text("SELECT generated_code FROM code_execution_events")
    ).scalars().all()
    assert seen_a == ["A-code"]

    # Under B's context only B's row is visible — proof RLS scopes the audit.
    _set_ctx(db, ws_b)
    seen_b = db.execute(
        text("SELECT generated_code FROM code_execution_events")
    ).scalars().all()
    assert seen_b == ["B-code"]


# =====================================================================
# Append-only (grant-level) proofs under the unprivileged decyra_app role
# =====================================================================


def _assert_unprivileged(db: Connection) -> None:
    who = db.execute(
        text("SELECT current_user, current_setting('is_superuser')")
    ).one()
    assert who[0] == "decyra_app", f"expected decyra_app, got {who[0]!r}"
    assert who[1] == "off", "append-only proof is worthless if the role is superuser"


def _seed_one_event(db: Connection) -> str:
    ws, user = seed_workspace(db)
    add_member(db, ws, user)
    _set_ctx(db, ws)
    code_audit.insert_code_execution_event(
        db, workspace_id=ws, user_id=user, status="ok",
        generated_code="df.plot()", chart_sha256="a" * 64,
    )
    return ws


def test_code_execution_event_append_only_no_update(db: Connection) -> None:
    """decyra_app has SELECT+INSERT but NO UPDATE — like document_events."""
    _seed_one_event(db)
    db.execute(text("SET LOCAL ROLE decyra_app"))
    _assert_unprivileged(db)
    with pytest.raises(Exception) as exc:
        with db.begin_nested():
            db.execute(text("UPDATE code_execution_events SET status = 'tampered'"))
    assert "permission denied" in str(exc.value).lower()


def test_code_execution_event_append_only_no_delete(db: Connection) -> None:
    """decyra_app has SELECT+INSERT but NO DELETE — events are immutable."""
    _seed_one_event(db)
    db.execute(text("SET LOCAL ROLE decyra_app"))
    _assert_unprivileged(db)
    with pytest.raises(Exception) as exc:
        with db.begin_nested():
            db.execute(text("DELETE FROM code_execution_events"))
    assert "permission denied" in str(exc.value).lower()


def test_code_execution_event_insert_allowed_under_decyra_app(db: Connection) -> None:
    """Positive control: decyra_app CAN insert (so the grant test above is about
    the missing UPDATE/DELETE, not a blanket denial)."""
    ws, user = seed_workspace(db)
    add_member(db, ws, user)
    db.execute(text("SET LOCAL ROLE decyra_app"))
    _assert_unprivileged(db)
    _set_ctx(db, ws)
    code_audit.insert_code_execution_event(
        db, workspace_id=ws, user_id=user, status="ok",
        generated_code="df.plot()", chart_sha256="a" * 64,
    )
    n = db.execute(text("SELECT count(*) FROM code_execution_events")).scalar_one()
    assert n == 1


# =====================================================================
# generate_and_run exposes one ExecutionRecord per sandbox run (test 4 unit)
# =====================================================================


class _FakeLLM:
    def __init__(self, codes):
        self._codes = list(codes)
        self.calls = []

    def __call__(self, messages):
        self.calls.append([dict(m) for m in messages])
        idx = min(len(self.calls) - 1, len(self._codes) - 1)
        return self._codes[idx]


class _FakeRunner:
    def __init__(self, results):
        self._results = list(results)
        self.run_codes = []

    def run(self, *, file_bytes, filename, code):
        self.run_codes.append(code)
        idx = min(len(self.run_codes) - 1, len(self._results) - 1)
        return self._results[idx]


def _schema():
    return analysis.DataSchema(
        columns=["q", "umsatz"],
        dtypes={"q": "object", "umsatz": "int64"},
        row_count=4,
    )


def test_one_execution_record_per_sandbox_run() -> None:
    """error, error, ok -> THREE ExecutionRecords (one per sandbox run)."""
    schema = _schema()
    llm = _FakeLLM(["c1", "c2", "c3"])
    runner = _FakeRunner(
        [
            SandboxResult("error", None, '  File "<generated>", line 1\nKeyError: 1\n'),
            SandboxResult("error", None, '  File "<generated>", line 1\nKeyError: 2\n'),
            SandboxResult("ok", b"\x89PNG_ok", ""),
        ]
    )
    outcome = analysis.generate_and_run(
        schema=schema, question="balken", file_bytes=b"q,umsatz\nQ1,1\n",
        filename="u.csv", complete_fn=llm, runner=runner, anonymizer=None,
        max_retries=3,
    )
    assert outcome.ok is True
    assert len(outcome.executions) == 3
    # Generated code is recorded for every run, in order.
    assert [e.generated_code for e in outcome.executions] == ["c1", "c2", "c3"]
    # Status per run.
    assert [e.status for e in outcome.executions] == ["error", "error", "ok"]
    # chart_sha256 only on the ok run, and it matches the chart bytes.
    assert outcome.executions[0].chart_sha256 is None
    assert outcome.executions[1].chart_sha256 is None
    assert outcome.executions[2].chart_sha256 == hashlib.sha256(b"\x89PNG_ok").hexdigest()


class _FakeAnon:
    """Deanonymise maps the placeholder back to the real column (Mustermann)."""

    def deanonymize(self, text: str) -> str:
        return text.replace("[[DCY_PERSON_0]]", "Mustermann")


def test_execution_record_holds_generated_not_executed_code() -> None:
    """The ExecutionRecord must carry the PRE-de-anon code (placeholder), even
    though the de-anonymised code is what ran in the box."""
    schema = analysis.DataSchema(
        columns=["[[DCY_PERSON_0]]"], dtypes={"[[DCY_PERSON_0]]": "object"},
        row_count=1,
    )
    placeholder_code = "df['[[DCY_PERSON_0]]'].plot()\nplt.savefig(CHART_PATH)\n"
    llm = _FakeLLM([placeholder_code])
    runner = _FakeRunner([SandboxResult("ok", b"\x89PNG_ok", "")])

    outcome = analysis.generate_and_run(
        schema=schema, question="chart", file_bytes=b"x\n1\n", filename="u.csv",
        complete_fn=llm, runner=runner, anonymizer=_FakeAnon(), max_retries=3,
    )
    rec = outcome.executions[0]
    # The RECORD holds the placeholder (generated) code...
    assert "[[DCY_PERSON_0]]" in rec.generated_code
    assert "Mustermann" not in rec.generated_code
    # ...while the code that actually RAN in the box was de-anonymised.
    assert "Mustermann" in runner.run_codes[0]


# =====================================================================
# Endpoint-driven proofs through the real route (tests 1, 2, 3, 4, 7)
# =====================================================================


def _seed_endpoint(db) -> None:
    from tests._helpers import seed_org_with_owner

    seed_org_with_owner(db, USER_A, "a@firma.de")
    _seed_model(db, CHOSEN)
    _seed_sovereign(db)


def _multipart(file_bytes, *, content, filename="u.csv", model=CHOSEN, **extra):
    payload = {
        "model": model,
        "use_code_interpreter": True,
        "messages": [{"role": "user", "content": content}],
        **extra,
    }
    return {
        "files": {"file": (filename, file_bytes, "text/csv")},
        "data": {"payload": json.dumps(payload)},
    }


@pytest.mark.asyncio
async def test_endpoint_appends_one_event_status_ok(
    client, db, make_token, stub_pii, stub_llm, stub_sandbox
) -> None:
    """A successful analysis request writes EXACTLY one code_execution_events row:
    status='ok', generated_code non-empty, chart_sha256 == hex sha256 of the
    returned chart bytes, event_type='code_execution'."""
    _seed_endpoint(db)
    token = make_token(sub=USER_A, email="a@firma.de")
    stub_sandbox.state["status"] = "ok"
    stub_sandbox.state["chart_png"] = b"\x89PNG_real_chart"
    stub_llm.state["content"] = "df.plot()\nplt.savefig(CHART_PATH)\n"

    r = await client.post(
        "/v1/chat/completions", headers=_auth(token),
        **_multipart(b"q,umsatz\nQ1,10\n", content="balken"),
    )
    assert r.status_code == 200, r.text

    rows = db.execute(
        text(
            "SELECT event_type, status, generated_code, chart_sha256 "
            "FROM code_execution_events"
        )
    ).all()
    assert len(rows) == 1
    row = rows[0]
    assert row.event_type == "code_execution"
    assert row.status == "ok"
    assert row.generated_code.strip() != ""
    assert row.chart_sha256 == hashlib.sha256(b"\x89PNG_real_chart").hexdigest()


@pytest.mark.asyncio
async def test_audit_stores_generated_not_executed_code_strict(
    client, db, make_token, stub_pii, stub_analyze, stub_llm, stub_sandbox
) -> None:
    """THE no-PII-at-rest proof. Strict mode, a column name (``Mustermann``) is
    PII tagged PERSON. The LLM returns codegen output referencing the PLACEHOLDER
    column ([[DCY_PERSON_0]]) as the real model would (its messages carried only
    placeholders). The stored generated_code therefore CONTAINS the placeholder
    and NOT the raw ``Mustermann`` — no raw PII rests in the audit. The de-anon
    map is never stored in any column."""
    _seed_endpoint(db)
    db.execute(
        text("UPDATE workspaces SET settings = '{\"pii_mode\":\"strict\"}'::jsonb "
             "WHERE id IN (SELECT workspace_id FROM workspace_members "
             "WHERE user_id = :u)"),
        {"u": USER_A},
    )
    # Strict mode anonymises (keeps model); the column name Mustermann is PERSON.
    stub_pii.state["force"] = "detected"
    stub_analyze.state["spans_for"] = {"Mustermann": "PERSON"}
    token = make_token(sub=USER_A, email="a@firma.de")

    # The model, having seen only the placeholder column, writes placeholder code.
    stub_llm.state["content"] = (
        "df['[[DCY_PERSON_0]]'].plot()\nplt.savefig(CHART_PATH)\n"
    )
    stub_sandbox.state["status"] = "ok"
    stub_sandbox.state["chart_png"] = b"\x89PNG_ok"

    # The uploaded file's COLUMN NAME is the PII (Mustermann).
    csv = b"Mustermann,umsatz\nA,10\nB,20\n"
    r = await client.post(
        "/v1/chat/completions", headers=_auth(token),
        **_multipart(csv, content="Umsatz pro Person"),
    )
    assert r.status_code == 200, r.text
    assert r.json()["decyra"]["pii_mode"] == "strict"
    assert r.json()["decyra"]["anonymized"] is True

    stored = db.execute(
        text("SELECT generated_code FROM code_execution_events")
    ).scalars().all()
    assert len(stored) == 1
    gen = stored[0]
    # The audit holds the ANONYMISED/GENERATED code: placeholder present...
    assert "[[DCY_PERSON_0]]" in gen
    # ...and the raw PII column name is ABSENT (no PII at rest).
    assert "Mustermann" not in gen

    # No column anywhere stores the de-anonymisation map (placeholder -> real).
    cols = {
        r.column_name
        for r in db.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'code_execution_events'"
            )
        ).all()
    }
    for forbidden in ("deanon_map", "deanonymization_map", "anon_map", "mapping"):
        assert forbidden not in cols
    # And the real value does not appear in ANY text column of the row.
    full = db.execute(
        text("SELECT generated_code, chart_sha256, status, event_type "
             "FROM code_execution_events")
    ).one()
    assert "Mustermann" not in (full.generated_code or "")
    assert "Mustermann" not in (full.chart_sha256 or "")


@pytest.mark.asyncio
async def test_chart_stored_as_hash_not_image(
    client, db, make_token, stub_pii, stub_llm, stub_sandbox
) -> None:
    """The row's chart_sha256 is a 64-char hex string and NONE of the columns
    contain the raw PNG bytes or its base64."""
    import base64 as _b64

    _seed_endpoint(db)
    token = make_token(sub=USER_A, email="a@firma.de")
    chart = b"\x89PNG\r\n\x1a\nrealimagebytes"
    stub_sandbox.state["status"] = "ok"
    stub_sandbox.state["chart_png"] = chart
    stub_llm.state["content"] = "df.plot()\nplt.savefig(CHART_PATH)\n"

    r = await client.post(
        "/v1/chat/completions", headers=_auth(token),
        **_multipart(b"q,umsatz\nQ1,10\n", content="balken"),
    )
    assert r.status_code == 200, r.text

    row = db.execute(
        text("SELECT generated_code, chart_sha256, status, event_type "
             "FROM code_execution_events")
    ).one()
    # chart_sha256 is exactly 64 lowercase hex chars.
    assert len(row.chart_sha256) == 64
    assert all(c in "0123456789abcdef" for c in row.chart_sha256)
    assert row.chart_sha256 == hashlib.sha256(chart).hexdigest()

    # The raw PNG bytes (or their base64) are NOWHERE in the row's text columns.
    chart_b64 = _b64.b64encode(chart).decode()
    for field in (row.generated_code, row.chart_sha256, row.status, row.event_type):
        assert "realimagebytes" not in (field or "")
        assert chart_b64 not in (field or "")


@pytest.mark.asyncio
async def test_endpoint_one_event_per_execution_three_errors(
    client, db, make_token, stub_pii, stub_llm, stub_sandbox
) -> None:
    """stub_sandbox always errors -> 3 sandbox runs -> EXACTLY 3
    code_execution_events rows (one event per execution)."""
    _seed_endpoint(db)
    token = make_token(sub=USER_A, email="a@firma.de")
    stub_sandbox.state["status"] = "error"
    stub_sandbox.state["output"] = '  File "<generated>", line 1\nKeyError: 1\n'
    stub_llm.state["content"] = "df.plot()\nplt.savefig(CHART_PATH)\n"

    r = await client.post(
        "/v1/chat/completions", headers=_auth(token),
        **_multipart(b"q,umsatz\nQ1,10\n", content="balken"),
    )
    assert r.status_code == 200, r.text
    assert len(stub_sandbox.calls) == 3

    n = db.execute(text("SELECT count(*) FROM code_execution_events")).scalar_one()
    assert n == 3
    statuses = db.execute(
        text("SELECT status FROM code_execution_events")
    ).scalars().all()
    assert statuses == ["error", "error", "error"]


@pytest.mark.parametrize("bad_status", ["error", "timeout", "killed", "no_chart"])
@pytest.mark.asyncio
async def test_endpoint_records_each_failure_status(
    bad_status, client, db, make_token, stub_pii, stub_llm, stub_sandbox
) -> None:
    """Each of error/timeout/killed/no_chart is recorded as the row's status on
    the corresponding execution(s)."""
    _seed_endpoint(db)
    token = make_token(sub=USER_A, email="a@firma.de")
    stub_sandbox.state["status"] = bad_status
    stub_sandbox.state["output"] = "raw box output"
    stub_llm.state["content"] = "df.plot()\nplt.savefig(CHART_PATH)\n"

    r = await client.post(
        "/v1/chat/completions", headers=_auth(token),
        **_multipart(b"q,umsatz\nQ1,10\n", content="balken"),
    )
    assert r.status_code == 200, r.text

    statuses = db.execute(
        text("SELECT status FROM code_execution_events")
    ).scalars().all()
    # All recorded rows carry the driven status; the chart hash is NULL (no ok run).
    assert statuses, "expected at least one recorded execution"
    assert set(statuses) == {bad_status}
    hashes = db.execute(
        text("SELECT chart_sha256 FROM code_execution_events")
    ).scalars().all()
    assert all(h is None for h in hashes)
