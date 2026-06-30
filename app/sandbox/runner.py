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
        import requests.exceptions
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
            except (requests.exceptions.Timeout,
                    requests.exceptions.ConnectionError):
                # The wait read-timeout (and its urllib3-wrapped ConnectionError
                # form) is the ONLY signal that means "container exceeded the
                # timeout". Catch ONLY that — a genuine RuntimeError from the SDK
                # must propagate (and `finally` still force-removes the
                # container), never be silently masked as a timeout.
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
