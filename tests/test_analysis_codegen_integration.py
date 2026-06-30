"""Task 5B.2 Sub-Task 4 — REAL-CONTAINER proofs for the codegen loop.

These are the security-critical proofs at the first end-to-end point where
LLM-generated code actually runs in the proven Docker sandbox. They use the REAL
``DockerSandboxRunner`` (NOT ``stub_sandbox``) with a REAL CSV in the runner's
stdin bundle, driven through ``analysis.generate_and_run`` with a fake
``complete_fn`` standing in for the LLM (it returns adversarial code).

* Proof 1 — isolation holds under real load: LLM code attempts a network call
  AND a host-FS read; the box must contain it (run yields ``error``, no chart).
* Proof 2 — forge protection holds with a real file present: LLM code runs the
  stack-introspection forgery payload (recover the nonce via ``sys._getframe``,
  print a fake ``ok`` + fake chart); the forged result must be REJECTED.
* Proof 4 (real-runner orphan check): after these real failure paths, no
  container labelled ``decyra.sandbox=1`` is left behind.

The sandbox/box itself is UNCHANGED by this task — these tests only prove the
codegen loop runs LLM-shaped code THROUGH the existing proven box.
"""

from __future__ import annotations

import textwrap

import pytest

from app import analysis
from app.config import Settings
from app.sandbox.runner import DockerSandboxRunner
from tests.conftest import requires_docker

pytestmark = [pytest.mark.integration, requires_docker]

CSV = b"kunde,umsatz\nMueller GmbH,1000\nSchmidt AG,2000\n"

SCHEMA = analysis.DataSchema(
    columns=["kunde", "umsatz"],
    dtypes={"kunde": "object", "umsatz": "int64"},
    row_count=2,
)


def _runner() -> DockerSandboxRunner:
    return DockerSandboxRunner(Settings(database_url="postgresql://x/y"))


class _FixedLLM:
    """Returns a fixed piece of code every attempt (stands in for the gated LLM)."""

    def __init__(self, code: str):
        self.code = code
        self.calls: list[list[dict]] = []

    def __call__(self, messages):
        self.calls.append(messages)
        return self.code


def _no_orphans() -> bool:
    import docker

    left = docker.from_env().containers.list(
        all=True, filters={"label": "decyra.sandbox=1"}
    )
    return left == []


# --- Proof 1: isolation holds under real load (network + host-FS attempt) ----


def test_real_codegen_network_and_hostfs_blocked():
    """LLM-generated code attempts BOTH a network call and a host-FS read against
    the real file in the box. Both must be contained: the run yields 'error' and
    produces NO chart."""
    malicious = textwrap.dedent(
        """
        import pandas as pd
        df = pd.read_csv(INPUT_PATH)           # real file is present in the box
        import urllib.request as u
        u.urlopen('http://1.1.1.1', timeout=5)  # network attempt -> must fail
        open('/Users/adrian/PROJECT/decyra-api/.env').read()  # host-FS read
        with open(CHART_PATH, 'wb') as fh:
            fh.write(b'should-never-happen')
        """
    )
    llm = _FixedLLM(malicious)
    outcome = analysis.generate_and_run(
        schema=SCHEMA,
        question="Umsatz pro Kunde",
        file_bytes=CSV,
        filename="d.csv",
        complete_fn=llm,
        runner=_runner(),
        anonymizer=None,
        max_retries=1,  # one shot is enough to prove containment
    )
    # The attack is contained: no chart bytes ever come back.
    assert outcome.ok is False
    assert outcome.chart_png is None
    assert outcome.status == "error"
    # The schema-safe feedback names the exception class only, never a host path
    # or a network address.
    assert "/Users/adrian" not in outcome.error_feedback
    assert "1.1.1.1" not in outcome.error_feedback
    assert _no_orphans()


# --- Proof 2: forge protection holds with a real file present ----------------


def test_real_codegen_forgery_rejected():
    """LLM code runs the stack-introspection forgery (recover the parent nonce,
    print a fake nonce-stamped 'ok' + fake chart). With the out-of-band, process-
    separated channel the child cannot recover the nonce, so the forged sentinels
    are NOT honoured: the run is not 'ok' and the forged bytes are never accepted
    as the chart."""
    forgery = textwrap.dedent(
        """
        import sys, base64, os
        n = None
        f = sys._getframe(0)
        while f is not None:
            if 'nonce' in f.f_locals:
                n = f.f_locals['nonce']
                break
            f = f.f_back
        sys.stdout.write(f'<<<DECYRA_STATUS:{n}>>>ok\\n')
        sys.stdout.write(
            f'<<<DECYRA_CHART_B64:{n}>>>'
            + base64.b64encode(b'FORGED').decode()
            + f'<<<DECYRA_CHART_END:{n}>>>\\n'
        )
        sys.stdout.flush()
        os._exit(0)
        """
    )
    llm = _FixedLLM(forgery)
    outcome = analysis.generate_and_run(
        schema=SCHEMA,
        question="balken",
        file_bytes=CSV,
        filename="d.csv",
        complete_fn=llm,
        runner=_runner(),
        anonymizer=None,
        max_retries=1,
    )
    # The forgery must NOT be honoured as a success.
    assert outcome.ok is False
    # And the forged b'FORGED' bytes must never be accepted as the chart.
    assert outcome.chart_png is None
    assert _no_orphans()


# --- Proof: a genuine chart DOES come back through the loop (positive) --------


def test_real_codegen_genuine_chart_succeeds():
    """Sanity / positive: real LLM-shaped code that reads the real file and saves
    a real chart succeeds through the loop and returns a genuine PNG."""
    good = textwrap.dedent(
        """
        import pandas as pd, matplotlib.pyplot as plt
        df = pd.read_csv(INPUT_PATH)
        df.plot(x='kunde', y='umsatz', kind='bar')
        plt.savefig(CHART_PATH)
        """
    )
    llm = _FixedLLM(good)
    outcome = analysis.generate_and_run(
        schema=SCHEMA,
        question="Umsatz pro Kunde als Balken",
        file_bytes=CSV,
        filename="d.csv",
        complete_fn=llm,
        runner=_runner(),
        anonymizer=None,
        max_retries=3,
    )
    assert outcome.ok is True
    assert outcome.chart_png is not None
    assert outcome.chart_png[:8] == b"\x89PNG\r\n\x1a\n"
    assert outcome.attempts == 1
    assert _no_orphans()


# --- Proof: strict-mode de-anonymisation runs real-column code in the box -----


class _RealishAnon:
    """Maps the placeholder back to the REAL column name ('kunde')."""

    def deanonymize(self, text: str) -> str:
        return text.replace("[[DCY_PERSON_0]]", "kunde")


def test_real_codegen_strict_deanon_runs_real_columns():
    """In strict mode the model would write PLACEHOLDER-column code. The loop must
    de-anonymise it to the REAL column before it runs, so it actually works
    against the real file. We hand the loop placeholder code + an anonymizer and
    assert a genuine chart comes back (proving the de-anon happened — placeholder
    code alone would KeyError on the real columns)."""
    placeholder_code = textwrap.dedent(
        """
        import pandas as pd, matplotlib.pyplot as plt
        df = pd.read_csv(INPUT_PATH)
        df.plot(x='[[DCY_PERSON_0]]', y='umsatz', kind='bar')
        plt.savefig(CHART_PATH)
        """
    )
    llm = _FixedLLM(placeholder_code)
    outcome = analysis.generate_and_run(
        schema=analysis.DataSchema(
            columns=["[[DCY_PERSON_0]]", "umsatz"],
            dtypes={"[[DCY_PERSON_0]]": "object", "umsatz": "int64"},
            row_count=2,
        ),
        question="chart",
        file_bytes=CSV,
        filename="d.csv",
        complete_fn=llm,
        runner=_runner(),
        anonymizer=_RealishAnon(),
        max_retries=1,
    )
    assert outcome.ok is True, outcome.error_feedback
    assert outcome.chart_png is not None
    assert outcome.chart_png[:8] == b"\x89PNG\r\n\x1a\n"
    assert _no_orphans()
