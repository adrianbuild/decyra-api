"""Task 5B.2 Sub-Task 4 — codegen prompt, schema-safe retry feedback, and the
bounded retry orchestrator (`analysis.generate_and_run`).

These are UNIT tests over the pure functions plus the orchestrator driven by a
fake LLM/sandbox. The endpoint-level proofs (3, 4, 5 through the real route) live
in the same file further down; the REAL-container proofs (1, 2 and the orphan
check) live in ``tests/test_analysis_codegen_integration.py`` (integration).

The load-bearing invariant proven here: NO data cell value can reach the cloud
LLM via a retry traceback. The sandbox traceback (which in strict mode quotes
REAL identifiers) is scrubbed to SCHEMA-SAFE parts only — the exception CLASS
name and the failing ``<generated>`` line number — before it is ever appended to
the next LLM attempt's messages.
"""

from __future__ import annotations

import json

import pytest

from app import analysis
from app.config import Settings
from app.sandbox.runner import SandboxResult

# --- A fake LLM "complete" callback the orchestrator calls each attempt -------
# generate_and_run is decoupled from llm_call: it is handed a `complete_fn`
# closure (the app passes one that calls the SAME gated complete_with_fallback,
# so stub_llm captures it in the endpoint tests). Here we drive it directly.


class _FakeLLM:
    """Records every (messages) it was asked to complete, returns queued code."""

    def __init__(self, code_sequence):
        self._codes = list(code_sequence)
        self.calls: list[list[dict]] = []

    def __call__(self, messages):
        self.calls.append([dict(m) for m in messages])
        # Last code repeats if the orchestrator asks more times than provided.
        idx = min(len(self.calls) - 1, len(self._codes) - 1)
        return self._codes[idx]


class _FakeRunner:
    """Returns queued SandboxResults; records the code actually run (post de-anon)."""

    def __init__(self, results):
        self._results = list(results)
        self.run_codes: list[str] = []

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


# --- 1. Codegen prompt is schema-only ---------------------------------------


def test_codegen_prompt_is_schema_only():
    msgs = analysis.build_codegen_messages(
        _schema(), "Umsatz pro Quartal als Balken"
    )
    blob = " ".join(m["content"] for m in msgs)
    # Column names (schema) + the run contract are present.
    assert "umsatz" in blob and "q" in blob
    assert "INPUT_PATH" in blob and "CHART_PATH" in blob
    # The user question is present.
    assert "Umsatz pro Quartal als Balken" in blob
    # System prompt constrains the model to pandas/matplotlib, no network, no
    # extra imports.
    sys_msg = msgs[0]["content"].lower()
    assert "pandas" in sys_msg and "matplotlib" in sys_msg
    assert "network" in sys_msg or "netz" in sys_msg


def test_codegen_prompt_carries_no_cell_values():
    """build_codegen_messages takes ONLY a DataSchema — there is no channel for
    a cell value to enter the prompt."""
    schema = analysis.DataSchema(
        columns=["kunde"], dtypes={"kunde": "object"}, row_count=2
    )
    msgs = analysis.build_codegen_messages(schema, "Chart bauen")
    blob = " ".join(m["content"] for m in msgs)
    assert "Mueller" not in blob and "Schmidt" not in blob


# --- 2. Schema-safe retry feedback ------------------------------------------


def test_retry_feedback_keeps_exception_class_drops_message_and_values():
    """A pandas traceback frequently quotes the offending DATA ROW. The feedback
    sent back to the LLM must contain ONLY the exception class + the failing
    <generated> line, never the message text (which quotes a value)."""
    output = (
        "<<<DECYRA_STATUS:abc>>>error\n"
        "Traceback (most recent call last):\n"
        '  File "<generated>", line 3, in <module>\n'
        "    df['umsatz'].astype(float)\n"
        "ValueError: could not convert string to float: 'Mueller GmbH'\n"
    )
    res = SandboxResult(status="error", chart_png=None, output=output)
    fb = analysis.build_retry_feedback(res)

    # Safe tokens MAY be present.
    assert "ValueError" in fb
    assert "3" in fb  # the failing <generated> line number
    # The data cell value and the raw message text must be ABSENT.
    assert "Mueller" not in fb
    assert "could not convert" not in fb
    # The raw traceback frame text must not leak either.
    assert "astype" not in fb


def test_retry_feedback_generic_for_timeout_killed_no_chart():
    for status, marker in (
        ("timeout", "timed out"),
        ("killed", "memory"),
        ("no_chart", "no chart"),
    ):
        res = SandboxResult(status=status, chart_png=None, output="irrelevant raw")
        fb = analysis.build_retry_feedback(res).lower()
        assert marker in fb
        # Never forwards the raw output for these states.
        assert "irrelevant raw" not in fb


def test_retry_feedback_falls_back_when_no_generated_frame():
    """If the traceback has no <generated> frame and no recognisable class, the
    feedback degrades to a safe generic line — still no raw output forwarded."""
    res = SandboxResult(
        status="error",
        chart_png=None,
        output="some opaque blob mentioning Mueller GmbH 12345",
    )
    fb = analysis.build_retry_feedback(res)
    assert "Mueller" not in fb
    assert "12345" not in fb
    assert fb.strip()  # non-empty, generic guidance


# --- 3. Orchestrator: success first try -------------------------------------


def _settings():
    return Settings(database_url="postgresql://x/y")


def test_generate_and_run_success_first_try():
    schema = _schema()
    llm = _FakeLLM(["import matplotlib.pyplot as plt\nplt.savefig(CHART_PATH)\n"])
    runner = _FakeRunner([SandboxResult("ok", b"\x89PNG_ok", "")])

    outcome = analysis.generate_and_run(
        schema=schema,
        question="balken",
        file_bytes=b"q,umsatz\nQ1,10\n",
        filename="u.csv",
        complete_fn=llm,
        runner=runner,
        anonymizer=None,
        max_retries=3,
    )
    assert outcome.ok is True
    assert outcome.chart_png == b"\x89PNG_ok"
    assert outcome.attempts == 1
    assert len(llm.calls) == 1


# --- 4. Orchestrator: retry on failure, then success ------------------------


def test_generate_and_run_retries_then_succeeds():
    schema = _schema()
    llm = _FakeLLM(["broken", "good"])
    runner = _FakeRunner(
        [
            SandboxResult(
                "error",
                None,
                '<<<DECYRA_STATUS:n>>>error\n  File "<generated>", line 2\nKeyError: \'x\'\n',
            ),
            SandboxResult("ok", b"\x89PNG_ok", ""),
        ]
    )
    outcome = analysis.generate_and_run(
        schema=schema,
        question="balken",
        file_bytes=b"q,umsatz\nQ1,10\n",
        filename="u.csv",
        complete_fn=llm,
        runner=runner,
        anonymizer=None,
        max_retries=3,
    )
    assert outcome.ok is True
    assert outcome.attempts == 2
    assert len(llm.calls) == 2
    # The 2nd LLM call carried the schema-safe feedback (class name), NOT raw.
    second_blob = " ".join(m["content"] for m in llm.calls[1])
    assert "KeyError" in second_blob


# --- 5. Orchestrator: retry cap, clean failure ------------------------------


def test_generate_and_run_caps_at_max_retries():
    schema = _schema()
    llm = _FakeLLM(["always-broken"])
    runner = _FakeRunner([SandboxResult("error", None, "<generated> line 1 KeyError")])
    outcome = analysis.generate_and_run(
        schema=schema,
        question="balken",
        file_bytes=b"q,umsatz\nQ1,10\n",
        filename="u.csv",
        complete_fn=llm,
        runner=runner,
        anonymizer=None,
        max_retries=3,
    )
    assert outcome.ok is False
    assert outcome.chart_png is None
    assert outcome.attempts == 3
    assert len(llm.calls) == 3  # EXACTLY max_retries — no infinite loop
    assert len(runner.run_codes) == 3


# --- 6. Strict mode: code is de-anonymised before it hits the sandbox -------


class _FakeAnon:
    """Stand-in Anonymizer: deanonymize maps a placeholder back to the real col."""

    def deanonymize(self, text: str) -> str:
        return text.replace("[[DCY_PERSON_0]]", "Mustermann")


def test_generate_and_run_deanonymizes_code_before_sandbox():
    """In strict mode the LLM writes PLACEHOLDER column code; the orchestrator
    must de-anonymise it with the SAME anonymizer so the REAL column code runs in
    the box."""
    schema = analysis.DataSchema(
        columns=["[[DCY_PERSON_0]]"], dtypes={"[[DCY_PERSON_0]]": "object"}, row_count=1
    )
    placeholder_code = "df['[[DCY_PERSON_0]]'].plot()\nplt.savefig(CHART_PATH)\n"
    llm = _FakeLLM([placeholder_code])
    runner = _FakeRunner([SandboxResult("ok", b"\x89PNG_ok", "")])

    outcome = analysis.generate_and_run(
        schema=schema,
        question="chart",
        file_bytes=b"x\n1\n",
        filename="u.csv",
        complete_fn=llm,
        runner=runner,
        anonymizer=_FakeAnon(),
        max_retries=3,
    )
    assert outcome.ok is True
    # The code that ACTUALLY ran references the REAL column, not the placeholder.
    assert "Mustermann" in runner.run_codes[0]
    assert "[[DCY_PERSON_0]]" not in runner.run_codes[0]


def test_generate_and_run_runs_code_asis_in_sovereign():
    """Sovereign mode: anonymizer is None → code runs verbatim (no de-anon)."""
    schema = _schema()
    code = "df['umsatz'].plot()\nplt.savefig(CHART_PATH)\n"
    llm = _FakeLLM([code])
    runner = _FakeRunner([SandboxResult("ok", b"\x89PNG_ok", "")])
    analysis.generate_and_run(
        schema=schema,
        question="chart",
        file_bytes=b"q,umsatz\nQ1,1\n",
        filename="u.csv",
        complete_fn=llm,
        runner=runner,
        anonymizer=None,
        max_retries=3,
    )
    assert runner.run_codes[0] == code


# --- 7. The orchestrator never lets a raw cell value into the next prompt ----


def test_no_cell_value_crosses_into_retry_prompt():
    """Proof 5 at the unit level: the sandbox traceback quotes a REAL cell value;
    the de-anonymised box ran real-column code so its traceback DOES contain the
    identifier — yet the NEXT LLM attempt must not see it."""
    schema = _schema()
    llm = _FakeLLM(["first", "second"])
    runner = _FakeRunner(
        [
            SandboxResult(
                "error",
                None,
                "Traceback (most recent call last):\n"
                '  File "<generated>", line 3, in <module>\n'
                "ValueError: could not convert string to float: 'Mueller GmbH'\n",
            ),
            SandboxResult("ok", b"\x89PNG_ok", ""),
        ]
    )
    analysis.generate_and_run(
        schema=schema,
        question="chart",
        file_bytes=b"q,umsatz\nQ1,1\n",
        filename="u.csv",
        complete_fn=llm,
        runner=runner,
        anonymizer=None,
        max_retries=3,
    )
    retry_blob = " ".join(m["content"] for m in llm.calls[1])
    assert "Mueller GmbH" not in retry_blob
    assert "Mueller" not in retry_blob
    assert "could not convert" not in retry_blob
    # But the SAFE token may be there.
    assert "ValueError" in retry_blob


# =====================================================================
# Endpoint-level proofs (Proof 3, 4-stub, 5) through the REAL route, with the
# sandbox/LLM STUBBED. The real-container proofs (1, 2 and the orphan check on
# the real-runner path) live in tests/test_analysis_codegen_integration.py.
# =====================================================================

from tests._helpers import seed_org_with_owner  # noqa: E402
from tests.test_pii_routing import (  # noqa: E402
    CHOSEN,
    _auth,
    _seed_model,
    _seed_sovereign,
)

USER_A = "11111111-1111-1111-1111-111111111111"


def _seed(db):
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


# --- Proof 3: retry cap — EXACTLY max_retries LLM attempts, then clean fail ---


@pytest.mark.asyncio
async def test_endpoint_retry_cap_exactly_max_retries(
    client, db, make_token, stub_pii, stub_llm, stub_sandbox
):
    """stub_sandbox always returns status='error'; the endpoint must make EXACTLY
    max_retries (3) LLM attempts, then return a clean failure response — no
    infinite loop, no 500."""
    _seed(db)
    token = make_token(sub=USER_A, email="a@firma.de")
    stub_sandbox.state["status"] = "error"
    stub_sandbox.state["output"] = '  File "<generated>", line 1\nKeyError: \'x\'\n'
    stub_llm.state["content"] = "df.plot()\nplt.savefig(CHART_PATH)\n"

    r = await client.post(
        "/v1/chat/completions",
        headers=_auth(token),
        **_multipart(b"q,umsatz\nQ1,10\n", content="balken"),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    # EXACTLY 3 LLM attempts (the bounded retry cap).
    assert len(stub_llm.calls) == 3
    # 3 sandbox runs too.
    assert len(stub_sandbox.calls) == 3
    # Clean, data-free failure — no chart, an error field present.
    assert "chart_png_b64" not in body["decyra"]
    assert body["decyra"]["error"]
    assert body["decyra"]["analysis_status"] == "error"


# --- Proof 4: error handling — timeout/killed/error all give HTTP 200, no 500 -


@pytest.mark.parametrize("bad_status", ["timeout", "killed", "error"])
@pytest.mark.asyncio
async def test_endpoint_clean_message_for_each_failure_status(
    bad_status, client, db, make_token, stub_pii, stub_llm, stub_sandbox
):
    """For each of timeout/killed/error the endpoint returns a clean user-facing
    message at HTTP 200 with an error field — never a crash/500."""
    _seed(db)
    token = make_token(sub=USER_A, email="a@firma.de")
    stub_sandbox.state["status"] = bad_status
    stub_sandbox.state["output"] = "raw box output that must never reach the client body fields"
    stub_llm.state["content"] = "df.plot()\nplt.savefig(CHART_PATH)\n"

    r = await client.post(
        "/v1/chat/completions",
        headers=_auth(token),
        **_multipart(b"q,umsatz\nQ1,10\n", content="balken"),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["decyra"]["error"]
    assert body["decyra"]["analysis_status"] == bad_status
    assert "chart_png_b64" not in body["decyra"]
    # The raw box output is NOT surfaced in the client-facing decyra block.
    assert "raw box output" not in json.dumps(body)


@pytest.mark.asyncio
async def test_endpoint_success_returns_chart(
    client, db, make_token, stub_pii, stub_llm, stub_sandbox
):
    """Happy path: status='ok' -> the chart is delivered as decyra.chart_png_b64
    and analysis_status is 'ok'."""
    _seed(db)
    token = make_token(sub=USER_A, email="a@firma.de")
    stub_sandbox.state["status"] = "ok"
    stub_sandbox.state["chart_png"] = b"\x89PNG_ok_bytes"
    stub_llm.state["content"] = "df.plot()\nplt.savefig(CHART_PATH)\n"

    r = await client.post(
        "/v1/chat/completions",
        headers=_auth(token),
        **_multipart(b"q,umsatz\nQ1,10\n", content="balken"),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    import base64

    assert body["decyra"]["analysis_status"] == "ok"
    assert body["decyra"]["chart_png_b64"]
    assert base64.b64decode(body["decyra"]["chart_png_b64"]) == b"\x89PNG_ok_bytes"
    # Exactly one LLM attempt on the happy path.
    assert len(stub_llm.calls) == 1


# --- Proof 5: PII-safe retry traceback — a cell value never reaches the LLM ---


@pytest.mark.asyncio
async def test_endpoint_retry_traceback_is_pii_safe(
    client, db, make_token, stub_pii, stub_llm, stub_sandbox
):
    """stub_sandbox returns status='error' with output CONTAINING a cell value.
    On the NEXT LLM attempt the cell value must be ABSENT, while a safe token
    (the exception class) MAY be present. Proves the traceback was scrubbed to
    schema-safe BEFORE going to the cloud LLM."""
    _seed(db)
    token = make_token(sub=USER_A, email="a@firma.de")
    stub_sandbox.state["status"] = "error"
    stub_sandbox.state["output"] = (
        "Traceback (most recent call last):\n"
        '  File "<generated>", line 3, in <module>\n'
        "ValueError: could not convert 'Mueller GmbH' to float\n"
    )
    stub_llm.state["content"] = "df.plot()\nplt.savefig(CHART_PATH)\n"

    r = await client.post(
        "/v1/chat/completions",
        headers=_auth(token),
        **_multipart(
            b"kunde,umsatz\nMueller GmbH,1000\n", content="Umsatz pro Kunde"
        ),
    )
    assert r.status_code == 200, r.text
    # At least two attempts happened (error -> retry).
    assert len(stub_llm.calls) >= 2
    # Join EVERY message of the LAST LLM attempt.
    last_msgs = stub_llm.calls[-1]["messages"]
    blob = "\n".join((m.get("content") or "") for m in last_msgs)
    # The data cell value is ABSENT from the retry prompt.
    assert "Mueller GmbH" not in blob
    assert "Mueller" not in blob
    assert "could not convert" not in blob
    # A safe token (exception class and/or line number) MAY be present.
    assert "ValueError" in blob or "3" in blob


@pytest.mark.asyncio
async def test_endpoint_no_500_on_analysis_path(
    client, db, make_token, stub_pii, stub_llm, stub_sandbox
):
    """Belt-and-braces: even on the all-error path the endpoint never returns a
    5xx — the failure is a clean HTTP 200 body."""
    _seed(db)
    token = make_token(sub=USER_A, email="a@firma.de")
    stub_sandbox.state["status"] = "error"
    stub_sandbox.state["output"] = "no <generated> frame here at all"
    stub_llm.state["content"] = "broken code"

    r = await client.post(
        "/v1/chat/completions",
        headers=_auth(token),
        **_multipart(b"q,umsatz\nQ1,10\n", content="balken"),
    )
    assert r.status_code == 200, r.text
