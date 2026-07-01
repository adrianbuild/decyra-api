# Code-Interpreter / Datenanalyse (5B.2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Excel/CSV hochladen → KI schreibt pandas-Code → in einer nachweislich dichten, ephemeren Docker-Sandbox (kein Netz, kein Host-FS, harte Limits) ausführen → matplotlib-Chart zurück in den Chat — mit auditiertem Code-Ausführungs-Ereignis.

**Architecture:** Das Backend startet pro Ausführung einen frischen, hart geflaggten Docker-Container über ein `SandboxRunner`-Interface (Dev: lokaler Daemon). Eingabe-Bytes und generierter Code gehen ausschließlich über einen kontrollierten stdin-Kanal rein, das Chart kommt als base64-PNG über stdout raus — kein Host-Verzeichnis-Mount. Schema-Info (nur Spaltennamen + dtypes + Zeilenzahl, keine Werte) läuft durch denselben PII-Routing/Anonymisierungs-Chokepoint wie die vier bestehenden Quellen. Roh-Bytes sind transient (kein neuer Persistent-Storage). Die Isolation wird ZUERST gegen einen echten Container mit bösartigen Payloads bewiesen, bevor LLM-generierter Code reingelegt wird.

**Tech Stack:** Python 3.11, FastAPI, `docker` Python-SDK (neu), Docker Desktop (Dev), pandas/matplotlib/openpyxl (im Sandbox-Image, nicht im App-Image), pytest + pytest-asyncio, Alembic, Postgres/RLS.

---

## Sicherheits-Invarianten → Test-Mapping (Pflicht-Gate)

| Inv | Bedeutung | Beweis-Test (Integration, echter Container) |
|-----|-----------|---------------------------------------------|
| S1 | Kein Netzwerk | `test_sandbox_network_blocked` |
| S2 | Kein Host-FS / kein Root / read-only rootfs / keine Host-Secrets | `test_sandbox_no_host_paths`, `test_sandbox_read_only_rootfs`, `test_sandbox_runs_as_nonroot`, `test_sandbox_no_host_env_secrets` |
| S3 | Ressourcen-Limits (CPU/Timeout, Speicher, PIDs) | `test_sandbox_timeout_infinite_loop`, `test_sandbox_memory_bomb_killed`, `test_sandbox_pids_limit` |
| S4 | Ephemerer Container, garantierter Teardown | `test_sandbox_container_removed_after_run`, `test_sandbox_container_removed_on_timeout`, `test_reaper_removes_orphans` |
| S5 | Kontrollierter Datenkanal (stdin rein / stdout raus) | `test_sandbox_runs_trivial_code_returns_chart` |
| S6 | PII-Grenze: Schema-Spaltennamen durch den Gate | `test_column_names_routed_sovereign`, `test_column_names_anonymized_strict` |
| S7 | Audit: Code-Exec ist append-only Ereignis | `test_code_exec_event_append_only_*` |
| S8 | 207 Bestandstests bleiben grün; Isolation mock-bar + wenige echte Integrationstests | gesamte Suite + `@pytest.mark.integration` |

**REIHENFOLGE-REGEL:** Sub-Task 1 (Isolation + Beweis) MUSS komplett grün und live bewiesen sein, bevor Sub-Task 2+ (Roh-Bytes, Schema, Codegen, Chart, Audit) beginnt.

---

## File Structure

**Neu erstellt:**
- `docker/sandbox/Dockerfile` — gepinntes Sandbox-Image (python+pandas+matplotlib+openpyxl, non-root, Agg).
- `docker/sandbox/bootstrap.py` — Entrypoint im Image: liest JSON-Bundle von stdin, schreibt nach `/tmp`, führt User-Code aus, gibt Chart als base64 zwischen Sentinels auf stdout.
- `app/sandbox/__init__.py`
- `app/sandbox/runner.py` — `SandboxRunner` (ABC), `DockerSandboxRunner`, `SandboxResult`, Concurrency-Semaphore, garantierter Teardown.
- `app/sandbox/reaper.py` — entfernt verwaiste, gelabelte Container (Backend-Crash-Schutz).
- `app/analysis.py` — Schema-Extraktion (xlsx/csv → Spalten+dtypes+Zeilenzahl, KEINE Werte), Codegen-Prompt-Bau, Retry-Orchestrierung.
- `app/code_audit.py` — Append-Funktion für `code_execution_events`.
- `alembic/versions/<rev>_code_execution_events_5b2.py` — neue append-only Tabelle.
- Tests: `tests/test_sandbox_isolation.py` (integration), `tests/test_sandbox_runner.py` (unit/mock), `tests/test_analysis_schema.py`, `tests/test_analysis_gate.py`, `tests/test_analysis_codegen.py`, `tests/test_analysis_flow.py`, `tests/test_code_audit.py`.

**Modifiziert:**
- `requirements.txt` — `docker>=7.1,<8.0` hinzufügen.
- `pyproject.toml` — `markers` + `integration`-Marker registrieren.
- `app/config.py` — Sandbox-Settings (Image, mem, pids, cpu, timeout, concurrency).
- `tests/conftest.py` — `docker_available` Skip-Helper + autouse `stub_sandbox`-Fixture (Mock-Ebene für Nicht-Integration-Tests).
- `app/main.py` — `/v1/chat/completions`: neuer Request-Flag `use_code_interpreter`; Analyse-Branch (transiente Bytes, kein chat_attachment-Persist).
- `app/config.py` Request-Schema (ChatCompletionRequest) — Flag `use_code_interpreter: bool = False`.
- `PROGRESS.md` — runc-Restrisiko + Prod-Isolation-PRE-PILOT-BLOCKER.

**Bewusst NICHT in diesem Repo:** Frontend-Chart-Rendering. `decyra-api` ist Backend-only (Inspektion bestätigt: kein Frontend-Code). Lieferobjekt hier ist der **API-Vertrag** — Chart als Image-Part in der Chat-Antwort, auf API-Ebene getestet. UI-Rendering = separater Task im Frontend-Repo (im Plan als Annahme dokumentiert, nicht als Code).

---

## Sub-Task 1 — Sandbox-Isolation + Image + Sicherheitsbeweise (S1–S5) — PFLICHT-GATE, ZUERST

**Files:**
- Create: `docker/sandbox/Dockerfile`, `docker/sandbox/bootstrap.py`
- Create: `app/sandbox/__init__.py`, `app/sandbox/runner.py`, `app/sandbox/reaper.py`
- Modify: `requirements.txt`, `pyproject.toml`, `app/config.py`, `tests/conftest.py`
- Test: `tests/test_sandbox_isolation.py` (integration), `tests/test_sandbox_runner.py` (unit)

### 1.1 Dependency + Marker + Settings

- [ ] **Step 1: `docker` SDK zu requirements.txt hinzufügen**

In `requirements.txt` nach `filetype` ergänzen:
```
docker>=7.1,<8.0
```
Dann: `pip install -r requirements.txt`

- [ ] **Step 2: `integration`-Marker registrieren**

In `pyproject.toml` unter `[tool.pytest.ini_options]`:
```toml
markers = [
    "integration: requires a real Docker daemon (sandbox isolation proofs); skipped if unavailable",
]
```

- [ ] **Step 3: Sandbox-Settings in `app/config.py`**

In der `Settings`-Klasse ergänzen:
```python
# Code-Interpreter sandbox (Task 5B.2). Image is prebuilt+pinned; runtime has no network.
sandbox_image: str = "decyra-sandbox:0.1.0"
sandbox_mem_limit: str = "512m"
sandbox_pids_limit: int = 128
sandbox_nano_cpus: int = 1_000_000_000  # 1.0 CPU
sandbox_timeout_seconds: float = 20.0
sandbox_max_concurrency: int = 2
sandbox_tmpfs_size: str = "64m"
```

- [ ] **Step 4: Commit**
```bash
git add requirements.txt pyproject.toml app/config.py
git commit -m "chore(5b2): add docker SDK, integration marker, sandbox settings"
```

### 1.2 Sandbox-Image + Bootstrap (Datenkanal S5)

- [ ] **Step 5: `docker/sandbox/Dockerfile` schreiben (gepinnt, non-root, Agg, kein Netz zur Laufzeit)**
```dockerfile
FROM python:3.11-slim

# Analysis libs baked in — runtime has NO network, so nothing installs at run time.
RUN pip install --no-cache-dir \
    pandas==2.2.3 \
    matplotlib==3.9.2 \
    openpyxl==3.1.5

# Non-root, no home, no shell.
RUN useradd --uid 5000 --no-create-home --shell /usr/sbin/nologin sandbox

# Agg = headless backend; MPLCONFIGDIR points at the writable tmpfs (/tmp) at run time.
ENV MPLBACKEND=Agg \
    MPLCONFIGDIR=/tmp/mpl \
    PYTHONDONTWRITEBYTECODE=1

COPY bootstrap.py /opt/bootstrap.py

USER 5000
WORKDIR /tmp
ENTRYPOINT ["python", "/opt/bootstrap.py"]
```

- [ ] **Step 6: `docker/sandbox/bootstrap.py` schreiben (stdin rein / stdout raus, Sentinels)**
```python
"""Sandbox entrypoint. Reads a JSON bundle from stdin, runs user code against the
input file in /tmp, emits the produced chart as base64 between sentinels on stdout.
The ONLY input channel is stdin; the ONLY output channel is stdout. No network, no
host FS (enforced by the container flags, not by this script)."""
import base64
import json
import sys
import traceback

CHART_BEGIN = "<<<DECYRA_CHART_B64>>>"
CHART_END = "<<<DECYRA_CHART_END>>>"
STATUS_PREFIX = "<<<DECYRA_STATUS>>>"

CHART_PATH = "/tmp/chart.png"


def main() -> None:
    raw = sys.stdin.buffer.read()
    bundle = json.loads(raw)
    filename = bundle["filename"]
    file_bytes = base64.b64decode(bundle["file_b64"])
    code = bundle["code"]

    input_path = f"/tmp/{filename}"
    with open(input_path, "wb") as fh:
        fh.write(file_bytes)

    # User code may reference INPUT_PATH and must write its chart to CHART_PATH.
    g = {"INPUT_PATH": input_path, "CHART_PATH": CHART_PATH, "__name__": "__sandbox__"}
    try:
        exec(compile(code, "<generated>", "exec"), g)  # noqa: S102 — the sandbox is the boundary
    except Exception:
        sys.stdout.write(STATUS_PREFIX + "error\n")
        sys.stdout.write(traceback.format_exc())
        sys.stdout.flush()
        return

    try:
        with open(CHART_PATH, "rb") as fh:
            png = fh.read()
    except FileNotFoundError:
        sys.stdout.write(STATUS_PREFIX + "no_chart\n")
        sys.stdout.flush()
        return

    sys.stdout.write(STATUS_PREFIX + "ok\n")
    sys.stdout.write(CHART_BEGIN + base64.b64encode(png).decode() + CHART_END + "\n")
    sys.stdout.flush()


if __name__ == "__main__":
    main()
```

- [ ] **Step 7: Image bauen + manueller Smoke (vor jedem Test, der ihn braucht)**
```bash
docker build -t decyra-sandbox:0.1.0 docker/sandbox/
echo '{"filename":"x.csv","file_b64":"YSxiCjEsMg==","code":"import matplotlib.pyplot as plt; plt.plot([1,2,3]); plt.savefig(CHART_PATH)"}' \
  | docker run --rm -i --network none --read-only --tmpfs /tmp:size=64m --user 5000 decyra-sandbox:0.1.0 | head -c 80
```
Expected: Ausgabe beginnt mit `<<<DECYRA_STATUS>>>ok` gefolgt von `<<<DECYRA_CHART_B64>>>`.

- [ ] **Step 8: Commit**
```bash
git add docker/sandbox/
git commit -m "feat(5b2): pinned sandbox image + stdin/stdout bootstrap channel"
```

### 1.3 SandboxRunner (Isolation-Flags, Teardown, Concurrency)

- [ ] **Step 9: `tests/conftest.py` — `docker_available` Skip-Helper + autouse `stub_sandbox`**
```python
import shutil
import pytest

def _docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        import docker
        docker.from_env().ping()
        return True
    except Exception:
        return False

DOCKER_AVAILABLE = _docker_available()

requires_docker = pytest.mark.skipif(
    not DOCKER_AVAILABLE, reason="real Docker daemon required for sandbox isolation proofs"
)

@pytest.fixture
def stub_sandbox(monkeypatch):
    """Mock the SandboxRunner so non-integration tests never start a container.
    The controller lets a test set the returned result or force a failure."""
    from app.sandbox import runner as runner_mod

    class _Stub:
        def __init__(self):
            self.state = {"status": "ok", "chart_png": b"\x89PNG_stub", "output": ""}
            self.calls = []
        def run(self, *, file_bytes, filename, code):
            self.calls.append({"filename": filename, "code": code})
            return runner_mod.SandboxResult(
                status=self.state["status"],
                chart_png=self.state["chart_png"] if self.state["status"] == "ok" else None,
                output=self.state["output"],
            )
    stub = _Stub()
    monkeypatch.setattr(runner_mod, "get_sandbox_runner", lambda settings: stub)
    return stub
```

- [ ] **Step 10: `tests/test_sandbox_runner.py` — Unit-Test: Teardown garantiert (mock, schlägt mitten drin fehl)**
```python
from unittest.mock import MagicMock
import pytest
from app.sandbox import runner as runner_mod
from app.config import Settings

def _settings():
    return Settings(database_url="postgresql://x/y")

def test_runner_force_removes_container_even_on_error(monkeypatch):
    container = MagicMock()
    client = MagicMock()
    client.containers.create.return_value = container
    container.wait.side_effect = RuntimeError("boom mid-run")
    monkeypatch.setattr(runner_mod, "_docker_client", lambda: client)

    r = runner_mod.DockerSandboxRunner(_settings())
    with pytest.raises(RuntimeError):
        r.run(file_bytes=b"a,b\n1,2", filename="x.csv", code="pass")

    container.remove.assert_called_once_with(force=True)
```

- [ ] **Step 11: Test laufen lassen → fehlschlägt**

Run: `pytest tests/test_sandbox_runner.py::test_runner_force_removes_container_even_on_error -v`
Expected: FAIL (`app.sandbox.runner` existiert noch nicht).

- [ ] **Step 12: `app/sandbox/__init__.py` + `app/sandbox/runner.py` implementieren**
```python
# app/sandbox/__init__.py
from app.sandbox.runner import SandboxResult, SandboxRunner, get_sandbox_runner

__all__ = ["SandboxResult", "SandboxRunner", "get_sandbox_runner"]
```
```python
# app/sandbox/runner.py
"""Ephemeral, hard-flagged Docker sandbox for LLM-generated code (Task 5B.2).

The container is the security boundary (S1-S5), not the prompt. Input goes in via
stdin, the chart comes out via stdout; no host directory is ever mounted."""
from __future__ import annotations

import base64
import json
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass

from app.config import Settings

_CHART_BEGIN = "<<<DECYRA_CHART_B64>>>"
_CHART_END = "<<<DECYRA_CHART_END>>>"
_STATUS_PREFIX = "<<<DECYRA_STATUS>>>"
_LABEL = "decyra.sandbox"


@dataclass(frozen=True)
class SandboxResult:
    status: str            # "ok" | "error" | "timeout" | "no_chart"
    chart_png: bytes | None
    output: str            # stdout/stderr (traceback on error) — for retry feedback


def _docker_client():
    import docker
    return docker.from_env()


class SandboxRunner(ABC):
    @abstractmethod
    def run(self, *, file_bytes: bytes, filename: str, code: str) -> SandboxResult: ...


class DockerSandboxRunner(SandboxRunner):
    # Process-wide cap: many concurrent containers would exhaust the host.
    _sem = threading.Semaphore(1)
    _sem_size = 1

    def __init__(self, settings: Settings):
        self.s = settings
        cls = type(self)
        if cls._sem_size != settings.sandbox_max_concurrency:
            cls._sem = threading.Semaphore(settings.sandbox_max_concurrency)
            cls._sem_size = settings.sandbox_max_concurrency

    def run(self, *, file_bytes: bytes, filename: str, code: str) -> SandboxResult:
        bundle = json.dumps({
            "filename": filename,
            "file_b64": base64.b64encode(file_bytes).decode(),
            "code": code,
        }).encode()

        with type(self)._sem:
            return self._run_locked(bundle)

    def _run_locked(self, bundle: bytes) -> SandboxResult:
        import docker
        from docker.errors import NotFound

        client = _docker_client()
        container = client.containers.create(
            image=self.s.sandbox_image,
            stdin_open=True,
            network_mode="none",                  # S1
            mem_limit=self.s.sandbox_mem_limit,    # S3
            memswap_limit=self.s.sandbox_mem_limit,  # no swap headroom
            pids_limit=self.s.sandbox_pids_limit,  # S3
            nano_cpus=self.s.sandbox_nano_cpus,    # S3
            read_only=True,                        # S2
            tmpfs={"/tmp": f"size={self.s.sandbox_tmpfs_size}"},  # only writable surface
            cap_drop=["ALL"],                      # S2
            security_opt=["no-new-privileges"],    # S2
            user="5000",                           # S2 (non-root)
            labels={_LABEL: "1"},                  # S4 (reaper)
            detach=True,
        )
        try:
            sock = container.attach_socket(params={"stdin": 1, "stdout": 1,
                                                   "stderr": 1, "stream": 1})
            container.start()
            sock._sock.sendall(bundle)
            sock._sock.shutdown(1)  # SHUT_WR → EOF for the bootstrap's stdin.read()

            try:
                container.wait(timeout=self.s.sandbox_timeout_seconds)  # S3
            except Exception:
                return SandboxResult("timeout", None, "execution timed out")

            logs = container.logs(stdout=True, stderr=True).decode(errors="replace")
            return self._parse(logs)
        finally:
            try:
                container.remove(force=True)       # S4: guaranteed teardown
            except NotFound:
                pass

    @staticmethod
    def _parse(logs: str) -> SandboxResult:
        status = "error"
        if _STATUS_PREFIX in logs:
            status = logs.split(_STATUS_PREFIX, 1)[1].splitlines()[0].strip()
        png = None
        if _CHART_BEGIN in logs and _CHART_END in logs:
            b64 = logs.split(_CHART_BEGIN, 1)[1].split(_CHART_END, 1)[0]
            try:
                png = base64.b64decode(b64)
            except Exception:
                png = None
        return SandboxResult(status=status, chart_png=png, output=logs)


def get_sandbox_runner(settings: Settings) -> SandboxRunner:
    return DockerSandboxRunner(settings)
```
> **Hinweis für den Ausführenden:** Die exakte `attach_socket`-Plumbing (stdin-EOF, log-Capture) ist genau das, was die Integrationstests in 1.4 live beweisen. Falls die SDK-Variante zickt, ist der dokumentierte Fallback der `docker`-CLI-Subprozess mit identischen Flags und `--rm` — Schnittstelle (`SandboxResult`) bleibt gleich.

- [ ] **Step 13: Test laufen lassen → grün**

Run: `pytest tests/test_sandbox_runner.py::test_runner_force_removes_container_even_on_error -v`
Expected: PASS.

- [ ] **Step 14: Concurrency-Unit-Test ergänzen + grün**
```python
def test_runner_concurrency_capped(monkeypatch):
    import app.sandbox.runner as runner_mod
    s = _settings(); s.sandbox_max_concurrency = 2
    r = runner_mod.DockerSandboxRunner(s)
    assert type(r)._sem_size == 2
```
Run: `pytest tests/test_sandbox_runner.py -v` → PASS.

- [ ] **Step 15: Commit**
```bash
git add app/sandbox/ tests/test_sandbox_runner.py tests/conftest.py
git commit -m "feat(5b2): DockerSandboxRunner with hard isolation flags + guaranteed teardown"
```

### 1.4 SICHERHEITSBEWEISE gegen ECHTEN Container (Pflicht-Gate, `@pytest.mark.integration`)

> Jeder Test hier startet einen echten Container. `requires_docker` skippt sauber, wo kein Daemon da ist — aber in einer Docker-fähigen Umgebung sind diese Tests PFLICHT und müssen grün sein, bevor Sub-Task 2 beginnt. **Ein gemockter Sandbox beweist NICHTS über Isolation.**

- [ ] **Step 16: `tests/test_sandbox_isolation.py` — alle Isolations-Beweise schreiben**
```python
import pytest
from tests.conftest import requires_docker
from app.config import Settings
from app.sandbox.runner import DockerSandboxRunner

pytestmark = [pytest.mark.integration, requires_docker]

CSV = b"q,umsatz\nQ1,10\nQ2,20\n"

def _runner():
    return DockerSandboxRunner(Settings(database_url="postgresql://x/y"))

CHART_CODE = (
    "import pandas as pd, matplotlib.pyplot as plt\n"
    "df = pd.read_csv(INPUT_PATH)\n"
    "df.plot(x='q', y='umsatz', kind='bar')\n"
    "plt.savefig(CHART_PATH)\n"
)

def test_sandbox_runs_trivial_code_returns_chart():  # S5 happy path
    res = _runner().run(file_bytes=CSV, filename="d.csv", code=CHART_CODE)
    assert res.status == "ok"
    assert res.chart_png and res.chart_png[:8] == b"\x89PNG\r\n\x1a\n"

def test_sandbox_network_blocked():  # S1
    code = ("import urllib.request as u\n"
            "u.urlopen('http://1.1.1.1', timeout=5)\n")
    res = _runner().run(file_bytes=CSV, filename="d.csv", code=code)
    assert res.status == "error"
    assert "URLError" in res.output or "Network is unreachable" in res.output

def test_sandbox_no_host_paths():  # S2
    code = "open('/Users/adrian/PROJECT/decyra-api/.env').read()\n"
    res = _runner().run(file_bytes=CSV, filename="d.csv", code=code)
    assert res.status == "error"
    assert "FileNotFoundError" in res.output

def test_sandbox_read_only_rootfs():  # S2
    code = "open('/evil', 'w').write('x')\n"
    res = _runner().run(file_bytes=CSV, filename="d.csv", code=code)
    assert res.status == "error"
    assert "Read-only file system" in res.output or "OSError" in res.output

def test_sandbox_runs_as_nonroot():  # S2
    code = "import os; print('UID', os.getuid()); raise SystemExit\n"
    res = _runner().run(file_bytes=CSV, filename="d.csv", code="import os\nassert os.getuid()==5000\nopen(CHART_PATH,'wb').write(b'')\n")
    # getuid must not be 0; assertion inside sandbox fails loudly if it is
    assert res.status in ("ok", "no_chart")

def test_sandbox_no_host_env_secrets():  # S2
    code = ("import os\n"
            "leaked=[k for k in os.environ if 'KEY' in k or 'SECRET' in k or 'DATABASE' in k]\n"
            "assert not leaked, leaked\n")
    res = _runner().run(file_bytes=CSV, filename="d.csv", code=code)
    assert res.status in ("error", "no_chart")  # assert passes → no_chart (no leaked secrets)
    assert "AssertionError" not in res.output

def test_sandbox_timeout_infinite_loop():  # S3
    res = _runner().run(file_bytes=CSV, filename="d.csv", code="while True: pass\n")
    assert res.status == "timeout"

def test_sandbox_memory_bomb_killed():  # S3
    code = "x=[]\nwhile True: x.append(' '*10_000_000)\n"
    res = _runner().run(file_bytes=CSV, filename="d.csv", code=code)
    assert res.status in ("error", "timeout")  # OOM-killed, host stays up

def test_sandbox_pids_limit():  # S3
    code = ("import os\n"
            "for _ in range(1000):\n"
            "    try: os.fork()\n"
            "    except OSError: break\n")
    res = _runner().run(file_bytes=CSV, filename="d.csv", code=code)
    assert res.status in ("error", "no_chart", "timeout")  # contained, host unharmed

def test_sandbox_container_removed_after_run():  # S4
    import docker
    _runner().run(file_bytes=CSV, filename="d.csv", code=CHART_CODE)
    left = docker.from_env().containers.list(all=True, filters={"label": "decyra.sandbox=1"})
    assert left == []

def test_sandbox_container_removed_on_timeout():  # S4
    import docker
    _runner().run(file_bytes=CSV, filename="d.csv", code="while True: pass\n")
    left = docker.from_env().containers.list(all=True, filters={"label": "decyra.sandbox=1"})
    assert left == []
```

- [ ] **Step 17: Integrationstests live laufen lassen → ALLE grün**

Run: `docker build -t decyra-sandbox:0.1.0 docker/sandbox/ && pytest tests/test_sandbox_isolation.py -v -m integration`
Expected: 11 PASS. **Bei JEDEM Fail: STOP, Diagnose vor Fix** — ein durchgelassener Angriff ist ein Architektur-Defekt, kein Test-Bug.

- [ ] **Step 18: Reaper implementieren + Test**

`app/sandbox/reaper.py`:
```python
"""Remove orphaned sandbox containers (e.g. if the backend crashed mid-run).
Call on app startup (lifespan)."""
def reap_orphans() -> int:
    import docker
    client = docker.from_env()
    n = 0
    for c in client.containers.list(all=True, filters={"label": "decyra.sandbox=1"}):
        c.remove(force=True)
        n += 1
    return n
```
Unit-Test in `tests/test_sandbox_runner.py`:
```python
def test_reaper_removes_orphans(monkeypatch):
    from app.sandbox import reaper
    c = MagicMock()
    client = MagicMock(); client.containers.list.return_value = [c, c]
    monkeypatch.setattr("docker.from_env", lambda: client)
    assert reaper.reap_orphans() == 2
    assert c.remove.call_count == 2
```
Run: `pytest tests/test_sandbox_runner.py::test_reaper_removes_orphans -v` → PASS.
Dann in `app/main.py` Lifespan `reap_orphans()` aufrufen (best-effort, in try/except).

- [ ] **Step 19: Volle Suite — Bestand bleibt grün**

Run: `pytest -q` (Integration wird geskippt, wenn kein Docker)
Expected: 207 Bestand + neue Unit-Tests grün; Integration passed-or-skipped.

- [ ] **Step 20: Commit (GATE geschlossen)**
```bash
git add app/sandbox/reaper.py app/main.py tests/
git commit -m "feat(5b2): live isolation proofs (S1-S5) + orphan reaper — sandbox gate green"
```

**>>> GATE: Erst weiter, wenn Step 17 live grün ist. <<<**

---

## Sub-Task 2 — Roh-Byte-Kanal (transient, kein Persist)

**Files:** Modify: `app/main.py`, Request-Schema; Test: `tests/test_analysis_flow.py`

- [ ] **Step 1: Failing test — `use_code_interpreter` lädt Datei transient, persistiert NICHT als chat_attachment**
```python
@pytest.mark.asyncio
async def test_analysis_does_not_persist_attachment(client, db, make_token, stub_sandbox):
    token = make_token(sub=USER_A, email="a@firma.de")
    files = {"file": ("u.csv", b"q,umsatz\nQ1,10\n", "text/csv")}
    data = {"payload": json.dumps({"model": MODEL, "use_code_interpreter": True,
            "messages": [{"role": "user", "content": "Umsatz pro Quartal als Balken"}]})}
    r = await client.post("/v1/chat/completions", headers=_auth(token), files=files, data=data)
    assert r.status_code == 200
    rows = db.execute(text("SELECT count(*) FROM chat_attachments")).scalar_one()
    assert rows == 0  # transient — nothing persisted
```
Run → FAIL.

- [ ] **Step 2: Request-Flag `use_code_interpreter: bool = False`** zum `ChatCompletionRequest`-Schema hinzufügen.

- [ ] **Step 3: Analyse-Branch in `/v1/chat/completions`** — wenn `use_code_interpreter and attachment_bytes`: nur `documents.sniff_mime` auf `{XLSX, TXT/CSV}` beschränken (PDF/DOCX → 415), Bytes in Memory halten, **kein** `attachments.insert_attachment`, weiter zu Sub-Task 3/4. Cap `max_upload_bytes` gilt weiter.

- [ ] **Step 4: Test grün** + Tests für `accepts_xlsx`, `accepts_csv`, `rejects_pdf` (415), `rejects_oversize` (413), `requires_auth` (401).
Run: `pytest tests/test_analysis_flow.py -v` → PASS.

- [ ] **Step 5: Commit** `feat(5b2): transient raw-byte channel for analysis (no persistence)`

---

## Sub-Task 3 — Schema-Extraktion + PII-Gate-Durchlauf (S6)

**Files:** Create: `app/analysis.py`; Test: `tests/test_analysis_schema.py`, `tests/test_analysis_gate.py`

- [ ] **Step 1: Failing test — Schema enthält Spalten+dtypes+Zeilenzahl, KEINE Werte**
```python
from app import analysis
def test_extract_schema_csv_has_no_values():
    csv = b"kunde,umsatz\nMueller GmbH,1000\nSchmidt AG,2000\n"
    schema = analysis.extract_schema("d.csv", csv)
    assert schema.columns == ["kunde", "umsatz"]
    assert schema.row_count == 2
    assert "int" in schema.dtypes["umsatz"]
    blob = analysis.schema_for_prompt(schema)
    assert "Mueller" not in blob and "Schmidt" not in blob and "1000" not in blob
```
Run → FAIL.

- [ ] **Step 2: `extract_schema` + `schema_for_prompt` implementieren** (pandas `read_csv`/`read_excel` lokal im App-Prozess NUR für Spaltennamen/dtypes/`len(df)`; Werte werden verworfen). `DataSchema`-Dataclass: `columns: list[str]`, `dtypes: dict[str,str]`, `row_count: int`.
> pandas läuft hier im App-Prozess (vertrauenswürdig, eigene Daten) nur zur Schema-Inspektion — NICHT zur Ausführung von LLM-Code. LLM-Code läuft ausschließlich in der Sandbox.

- [ ] **Step 3: Test grün.** Plus `test_extract_schema_xlsx`.

- [ ] **Step 4: Failing test — Spaltennamen laufen durch den PII-Gate (S6, Sovereign-Reroute)**
```python
@pytest.mark.asyncio
async def test_column_names_routed_sovereign(client, db, make_token, stub_sandbox, stub_pii):
    stub_pii.state["force"] = "detected"   # a column name triggers PII
    token = make_token(sub=USER_A, email="a@firma.de")
    files = {"file": ("u.csv", b"vorname,umsatz\nA,1\n", "text/csv")}
    data = {"payload": json.dumps({"model": "gpt-5.5", "use_code_interpreter": True,
            "messages":[{"role":"user","content":"chart"}]})}
    r = await client.post("/v1/chat/completions", headers=_auth(token), files=files, data=data)
    assert r.json()["decyra"]["effective_model"] == SOVEREIGN  # rerouted
```
Run → FAIL.

- [ ] **Step 5: Schema-Text als 5. Quelle in den Routing-Text einhängen** in `main.py` (~Z. 1038): `routing_text += "\n" + schema_for_prompt(schema)`. Im Strict-Modus geht der Schema-Block durch `anonymize_messages` mit. **Damit ist S6 erfüllt: Spaltennamen durchlaufen denselben Chokepoint wie User/Historie/RAG/Datei-Text.**

- [ ] **Step 6: Tests grün** — plus `test_column_names_anonymized_strict` (Strict: PII-Spaltenname → Platzhalter im LLM-Payload) und `test_schema_gate_clean_no_reroute`.
Run: `pytest tests/test_analysis_schema.py tests/test_analysis_gate.py -v` → PASS.

- [ ] **Step 7: Commit** `feat(5b2): schema-only extraction routed through the PII gate (S6)`

---

## Sub-Task 4 — Codegen + Retry

**Files:** Modify: `app/analysis.py`, `app/main.py`; Test: `tests/test_analysis_codegen.py`

- [ ] **Step 1: Failing test — Prompt enthält NUR Schema, keine Datenwerte**
```python
def test_codegen_prompt_is_schema_only():
    schema = analysis.DataSchema(columns=["q","umsatz"], dtypes={"q":"object","umsatz":"int64"}, row_count=4)
    msgs = analysis.build_codegen_messages(schema, "Umsatz pro Quartal als Balken")
    blob = " ".join(m["content"] for m in msgs)
    assert "umsatz" in blob and "INPUT_PATH" in blob and "CHART_PATH" in blob
```
Run → FAIL.

- [ ] **Step 2: `build_codegen_messages` implementieren** — System-Prompt: „Schreibe NUR pandas/matplotlib-Python. Lies die Datei aus `INPUT_PATH`, speichere genau ein Chart nach `CHART_PATH`. Kein Netz, keine Imports außer pandas/matplotlib. Gib nur Code zurück." + Schema-Block + User-Frage.

- [ ] **Step 3: Retry-Orchestrierung `generate_and_run`** — bis `max_retries` (Default 3): LLM-Code holen → `runner.run(...)` → bei `status in ("error","timeout")` Traceback (`result.output`, gekürzt) als Feedback-Message anhängen und erneut; bei `ok` zurückgeben. LLM-Aufruf geht über denselben gerouteten/anonymisierten Pfad.

- [ ] **Step 4: Tests grün** — `test_codegen_success_first_try`, `test_codegen_retry_on_traceback` (stub_llm liefert erst kaputten, dann guten Code via `stub_llm.state["chunks"]`-Sequenz; `stub_sandbox` erst `error` dann `ok`), `test_codegen_max_retries_then_error`, `test_error_message_no_internal_leak` (User-Meldung enthält keinen rohen Traceback/Pfade).
Run: `pytest tests/test_analysis_codegen.py -v` → PASS.

- [ ] **Step 5: Commit** `feat(5b2): pandas/matplotlib codegen with bounded traceback-feedback retry`

---

## Sub-Task 5 — Chart-Rückgabe in den Chat (API-Vertrag)

**Files:** Modify: `app/main.py`; Test: `tests/test_analysis_flow.py`

- [ ] **Step 1: Failing test — Chart als base64-Image-Part in der Antwort, NICHT zurück ans LLM**
```python
@pytest.mark.asyncio
async def test_chart_returned_and_not_resent_to_llm(client, db, make_token, stub_sandbox, stub_llm):
    stub_sandbox.state["status"] = "ok"; stub_sandbox.state["chart_png"] = b"\x89PNG_ok"
    token = make_token(sub=USER_A, email="a@firma.de")
    files = {"file": ("u.csv", b"q,umsatz\nQ1,10\n", "text/csv")}
    data = {"payload": json.dumps({"model": MODEL, "use_code_interpreter": True,
            "messages":[{"role":"user","content":"balken"}]})}
    r = await client.post("/v1/chat/completions", headers=_auth(token), files=files, data=data)
    body = r.json()
    assert body["decyra"]["chart_png_b64"]  # chart delivered in response
    # exactly one LLM round-trip per retry attempt — the PNG is never fed back to a model
    assert all("PNG" not in str(c.get("messages")) for c in stub_llm.calls)
```
Run → FAIL.

- [ ] **Step 2: Antwort zusammenbauen** — bei `ok` das Chart als `decyra.chart_png_b64` (base64) in die Chat-Completion-Antwort einhängen; Assistant-Text z. B. „Hier ist dein Diagramm." Das PNG geht **nie** in einen weiteren LLM-Call (kein Vision-Schritt in v1).

- [ ] **Step 3: Tests grün** — plus `test_chart_failure_returns_friendly_message` (nach max_retries: freundliche Fehlermeldung, kein Crash, HTTP 200 mit Fehler-Text).
Run: `pytest tests/test_analysis_flow.py -v` → PASS.

- [ ] **Step 4: Commit** `feat(5b2): deliver chart as image part in chat response (never re-sent to LLM)`

---

## Sub-Task 6 — Audit (`code_execution_events`, append-only via Grant)

**Files:** Create: Migration, `app/code_audit.py`; Modify: `app/main.py`; Test: `tests/test_code_audit.py`

- [ ] **Step 1: Failing test — Event wird angehängt (Code + Status + Chart-Hash, nicht Bild)**
```python
def test_code_exec_event_appended(db):
    ws, user = seed_ws_user(db)
    set_workspace_context(db, ws)
    from app import code_audit
    code_audit.insert_code_execution_event(db, ws, user, code="df.plot()",
        status="ok", chart_sha256="abc123")
    row = db.execute(text("SELECT event_type, status, generated_code, chart_sha256, chart_png "
                          "FROM code_execution_events")).one()
    assert row.event_type == "code_execution"
    assert row.status == "ok" and row.generated_code == "df.plot()"
    assert row.chart_sha256 == "abc123"
    assert not hasattr(row, "chart_png") or row.chart_png is None  # never the image itself
```
Run → FAIL.

- [ ] **Step 2: Alembic-Migration `code_execution_events_5b2`** (down_revision = `0b0a8d270079`):
```python
def upgrade():
    op.execute("""
        CREATE TABLE code_execution_events (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            workspace_id uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
            user_id uuid NOT NULL REFERENCES users(id),
            event_type text NOT NULL DEFAULT 'code_execution',
            status text NOT NULL,                 -- 'ok' | 'error' | 'timeout' | 'no_chart'
            generated_code text NOT NULL,
            chart_sha256 text,                    -- hash/reference, NOT the image
            created_at timestamptz NOT NULL DEFAULT clock_timestamp()
        );
        ALTER TABLE code_execution_events ENABLE ROW LEVEL SECURITY;
        ALTER TABLE code_execution_events FORCE ROW LEVEL SECURITY;
        CREATE POLICY cee_isolation ON code_execution_events
            USING (workspace_id = current_setting('app.current_workspace_id', true)::uuid)
            WITH CHECK (workspace_id = current_setting('app.current_workspace_id', true)::uuid);
        -- Append-only via grant (like document_events): SELECT+INSERT only, no UPDATE/DELETE.
        GRANT SELECT, INSERT ON code_execution_events TO decyra_app;
    """)
    op.execute("CREATE INDEX ix_cee_workspace ON code_execution_events (workspace_id, created_at)")
```
Run: `alembic upgrade head` → neuer Head.

- [ ] **Step 3: `app/code_audit.py::insert_code_execution_event`** (bound params, workspace vom Aufrufer) implementieren. Test grün.

- [ ] **Step 4: Append-only-Beweise** — `test_code_exec_event_append_only_no_update` und `_no_delete` (unter `app_with_db_decyra_app`-Rolle → UPDATE/DELETE wirft `InsufficientPrivilege`), `test_code_exec_event_rls_workspace_scoped`, `test_code_exec_event_status_values` (ok/error/timeout/no_chart).
Run: `pytest tests/test_code_audit.py -v` → PASS.

- [ ] **Step 5: In `main.py` Analyse-Branch** nach jeder Ausführung `insert_code_execution_event(...)` mit finalem Code, Status und `sha256(chart_png)` (oder NULL) in derselben Schreib-Transaktion. Test in `test_analysis_flow.py`: erfolgreicher Analyse-Request schreibt genau eine `code_execution_events`-Zeile.

- [ ] **Step 6: Commit** `feat(5b2): append-only code_execution_events audit (code+status+chart hash)`

---

## Sub-Task 7 — Doku, Restrisiko, Prod-Blocker, WORKPLAN-Tick

**Files:** Modify: `PROGRESS.md`, `WORKPLAN.md`

- [ ] **Step 1: `PROGRESS.md` ergänzen:**
  - **runc-Restrisiko:** Dev-Sandbox nutzt runc (kein harter Kernel-Boundary gegen Kernel-Exploits). MVP-Härtung: `--network none`, read-only rootfs, cap-drop ALL, no-new-privileges, non-root, mem/pids/cpu-Limits, Timeout-Kill. Live bewiesen (S1–S3).
  - **PRE-PILOT-BLOCKER (Prod-Isolation):** Vor erstem echten Kunden: `SandboxRunner`-Prod-Impl auf separatem Sandbox-Host / dediziertem Daemon — **KEIN `docker.sock` im App-Container**. Optional gVisor/Firecracker. Bis dahin ist die Sandbox dev-tauglich, nicht prod-sicher.
  - **Host-Concurrency:** `sandbox_max_concurrency` begrenzt parallele Container (Host-Erschöpfungs-Schutz).
  - **PII-Grenze (S6):** Datei läuft lokal/ohne Netz → verlässt System nie. Nur Schema (Spaltennamen+dtypes+Zeilenzahl) geht ans LLM, durch denselben Routing/Anonymisierungs-Gate. Chart geht nie zurück ans LLM.

- [ ] **Step 2: Volle Suite final**

Run: `docker build -t decyra-sandbox:0.1.0 docker/sandbox/ && pytest -q -m "not integration" && pytest -q -m integration`
Expected: alles grün (Integration in Docker-Umgebung Pflicht).

- [ ] **Step 3: `WORKPLAN.md` Task 5B.2** — alle Checkboxen abhaken (nur Tick, gem. WORKPLAN-Regel), nachdem alles grün ist.

- [ ] **Step 4: Commit** `docs(5b2): runc residual risk, prod-isolation pre-pilot blocker, workplan tick`

---

## Vollständige Testliste (echte Zahl)

**Bestand:** 207 (bleiben grün, S8).

**Neu — Integration (echter Container, `@pytest.mark.integration`, Sicherheits-Pflicht-Gate) — 11:**
1. `test_sandbox_runs_trivial_code_returns_chart` (S5)
2. `test_sandbox_network_blocked` (S1)
3. `test_sandbox_no_host_paths` (S2)
4. `test_sandbox_read_only_rootfs` (S2)
5. `test_sandbox_runs_as_nonroot` (S2)
6. `test_sandbox_no_host_env_secrets` (S2)
7. `test_sandbox_timeout_infinite_loop` (S3)
8. `test_sandbox_memory_bomb_killed` (S3)
9. `test_sandbox_pids_limit` (S3)
10. `test_sandbox_container_removed_after_run` (S4)
11. `test_sandbox_container_removed_on_timeout` (S4)

**Neu — Unit/Endpoint (gemockte Sandbox/LLM) — 28:**
- Runner: `test_runner_force_removes_container_even_on_error`, `test_runner_concurrency_capped`, `test_reaper_removes_orphans` (3)
- Roh-Bytes: `accepts_xlsx`, `accepts_csv`, `rejects_pdf`, `rejects_oversize`, `does_not_persist_attachment`, `requires_auth` (6)
- Schema: `extract_schema_csv_has_no_values`, `extract_schema_xlsx` (2)
- Gate (S6): `column_names_routed_sovereign`, `column_names_anonymized_strict`, `schema_gate_clean_no_reroute` (3)
- Codegen: `prompt_is_schema_only`, `success_first_try`, `retry_on_traceback`, `max_retries_then_error`, `error_message_no_internal_leak` (5)
- Chart: `chart_returned_and_not_resent_to_llm`, `chart_failure_returns_friendly_message` (2)
- Audit: `event_appended`, `append_only_no_update`, `append_only_no_delete`, `rls_workspace_scoped`, `status_values`, `analysis_writes_one_event`, `migration_head_advances` (7)

**Gesamt nach 5B.2: 207 + 11 + 28 = 246 Tests** (11 davon Integration, in Nicht-Docker-Umgebung sauber geskippt; in Docker-Umgebung Pflicht).

---

## Self-Review (gegen Spec)

- **S1–S5** → Sub-Task 1, live gegen echten Container bewiesen, als Gate vor allem anderen. ✓
- **S6** → Sub-Task 3 Step 4–6: Schema-Spaltennamen durch denselben Chokepoint; nur Schema, keine Werte; Chart nie zurück ans LLM (Sub-Task 5). ✓
- **S7** → Sub-Task 6: eigene append-only `code_execution_events`, Grant-Level, Chart als Hash. ✓
- **S8** → 207 bleiben grün; Mock-Ebene (`stub_sandbox`) für Unit, 11 echte Integration für Beweise. ✓
- **Entscheidung 1 (transient)** → Sub-Task 2: kein Persist. ✓
- **Entscheidung 4 (Dev-only + Interface)** → `SandboxRunner`-ABC jetzt; Prod als PRE-PILOT-BLOCKER in PROGRESS. ✓
- **Zusätze B/C/D/F3** → Marker+skip (B), gepinntes Image (C), Teardown+Reaper (D), Concurrency-Semaphore (F3). ✓
