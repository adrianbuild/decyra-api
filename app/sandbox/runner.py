"""Ephemeral, hard-flagged Docker sandbox for LLM-generated code (Task 5B.2).

The container is the security boundary (S1-S5), not the prompt. Input goes in via
stdin, the chart comes out via stdout; no host directory is ever mounted.

Implementation note (runner channel): the docker-SDK ``attach_socket`` stdin path
did NOT reliably deliver EOF to the container's ``sys.stdin.read()`` on this Docker
Desktop — every run blocked until the timeout. The plan's documented fallback is a
``docker`` CLI subprocess with identical hardening flags and ``--rm``; that path
delivers stdin → stdout deterministically and is what we use here. The
``SandboxResult`` interface is unchanged. Teardown stays guaranteed by ``--rm`` plus
a best-effort ``docker rm -f <name>`` in ``finally`` (covers the kill-on-timeout
case), and orphans are swept by the reaper via the ``decyra.sandbox`` label."""
from __future__ import annotations

import base64
import json
import subprocess
import threading
import uuid
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


def _force_remove(name: str) -> None:
    """Best-effort teardown of a named container (S4). ``docker run --rm`` already
    removes on normal exit; this covers the timeout-kill / crash path. Never raises
    — teardown must not mask the run's own outcome."""
    try:
        subprocess.run(
            ["docker", "rm", "-f", name],
            capture_output=True,
            timeout=15,
        )
    except Exception:
        pass


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

    def _docker_run_cmd(self, name: str) -> list[str]:
        s = self.s
        cpus = f"{s.sandbox_nano_cpus / 1_000_000_000:g}"
        return [
            "docker", "run", "--rm", "-i",
            "--name", name,
            "--label", f"{_LABEL}=1",              # S4 (reaper)
            "--network", "none",                   # S1
            "--read-only",                         # S2
            "--tmpfs", f"/tmp:size={s.sandbox_tmpfs_size}",  # only writable surface
            "--memory", s.sandbox_mem_limit,       # S3
            "--memory-swap", s.sandbox_mem_limit,  # no swap headroom
            "--pids-limit", str(s.sandbox_pids_limit),  # S3
            "--cpus", cpus,                        # S3
            "--cap-drop", "ALL",                   # S2
            "--security-opt", "no-new-privileges",  # S2
            "--user", "5000",                      # S2 (non-root)
            s.sandbox_image,
        ]

    def _run_locked(self, bundle: bytes) -> SandboxResult:
        name = f"decyra-sbx-{uuid.uuid4().hex[:12]}"
        cmd = self._docker_run_cmd(name)
        try:
            try:
                proc = subprocess.run(
                    cmd,
                    input=bundle,
                    capture_output=True,
                    timeout=self.s.sandbox_timeout_seconds,  # S3
                )
            except subprocess.TimeoutExpired:
                return SandboxResult("timeout", None, "execution timed out")

            logs = proc.stdout.decode(errors="replace")
            if proc.stderr:
                logs += proc.stderr.decode(errors="replace")
            return self._parse(logs)
        finally:
            _force_remove(name)                    # S4: guaranteed teardown

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
