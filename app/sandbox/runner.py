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
import logging
import subprocess
import threading
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass

from app.config import Settings, get_settings

_LABEL = "decyra.sandbox"

# Process-wide concurrency cap, built ONCE from the cached settings. Many
# concurrent containers would exhaust the host. Building it once (no per-instance
# rebind) means a permit is never silently orphaned on a stale semaphore — every
# DockerSandboxRunner shares this one gate, so the cap genuinely holds.
_SEM = threading.Semaphore(get_settings().sandbox_max_concurrency)


@dataclass(frozen=True)
class SandboxResult:
    status: str            # "ok" | "error" | "timeout" | "killed" | "no_chart"
    chart_png: bytes | None
    output: str            # stdout/stderr (traceback on error) — for retry feedback


def _docker_client():
    import docker

    return docker.from_env()


def _force_remove(name: str) -> None:
    """Best-effort teardown of a named container (S4). ``docker run --rm`` already
    removes on normal exit; this covers the timeout-kill / crash path. Never raises
    — teardown must not mask the run's own outcome.

    Retries once and, if removal still fails, emits a ``logging.warning`` so the
    failure is OBSERVABLE: a surviving infinite-loop container would otherwise pin
    a CPU with no signal. (Prod hardening: a periodic reaper sweeping the
    ``decyra.sandbox`` label — NOT built here; the startup reaper plus this warning
    are the current backstop.)"""
    last_err: Exception | None = None
    for _ in range(2):  # one retry
        try:
            proc = subprocess.run(
                ["docker", "rm", "-f", name],
                capture_output=True,
                timeout=15,
            )
            stderr = proc.stderr.decode(errors="replace").strip()
            # rc==0 → removed. "No such container" → already gone (--rm won the
            # race); the container is absent either way, which is the goal.
            if proc.returncode == 0 or "No such container" in stderr:
                return
            last_err = RuntimeError(stderr)
        except Exception as exc:  # noqa: BLE001 — teardown must never raise
            last_err = exc
    logging.warning(
        "sandbox teardown: failed to force-remove container %s: %s", name, last_err
    )


class SandboxRunner(ABC):
    @abstractmethod
    def run(self, *, file_bytes: bytes, filename: str, code: str) -> SandboxResult: ...


class DockerSandboxRunner(SandboxRunner):
    def __init__(self, settings: Settings):
        self.s = settings

    def run(self, *, file_bytes: bytes, filename: str, code: str) -> SandboxResult:
        # Per-run nonce: the result channel (status/chart sentinels) shares the
        # one stdout user code prints to. Without a nonce, code could print
        # forged sentinels and steer the parsed result. The nonce is generated
        # here, handed to bootstrap via the stdin bundle (read into a LOCAL var,
        # never the user-code globals), and stamped into every sentinel. The
        # runner honours ONLY sentinels bearing this exact nonce — so user code,
        # which never sees it, cannot forge the channel.
        nonce = uuid.uuid4().hex
        bundle = json.dumps({
            "filename": filename,
            "file_b64": base64.b64encode(file_bytes).decode(),
            "code": code,
            "nonce": nonce,
        }).encode()

        with _SEM:
            return self._run_locked(bundle, nonce)

    def _docker_run_cmd(self, name: str) -> list[str]:
        s = self.s
        cpus = f"{s.sandbox_nano_cpus / 1_000_000_000:g}"
        return [
            "docker", "run", "--rm", "-i",
            "--name", name,
            "--label", f"{_LABEL}=1",              # S4 (reaper)
            "--network", "none",                   # S1
            "--read-only",                         # S2
            # S5: the ONLY writable surfaces are /tmp and /dev/shm. --read-only
            # leaves Docker's default 64MB /dev/shm writable+exec; we replace it
            # with a bounded, noexec/nosuid/nodev tmpfs so it's neither an
            # unaccounted surface nor an exec staging ground.
            "--tmpfs", f"/tmp:size={s.sandbox_tmpfs_size}",
            "--tmpfs", "/dev/shm:size=16m,noexec,nosuid,nodev",
            "--memory", s.sandbox_mem_limit,       # S3
            "--memory-swap", s.sandbox_mem_limit,  # no swap headroom
            "--pids-limit", str(s.sandbox_pids_limit),  # S3
            "--cpus", cpus,                        # S3
            "--cap-drop", "ALL",                   # S2
            "--security-opt", "no-new-privileges",  # S2
            "--user", "5000",                      # S2 (non-root)
            s.sandbox_image,
        ]

    def _run_locked(self, bundle: bytes, nonce: str) -> SandboxResult:
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
            return self._parse(logs, nonce, proc.returncode)
        finally:
            _force_remove(name)                    # S4: guaranteed teardown

    @staticmethod
    def _parse(logs: str, nonce: str, returncode: int) -> SandboxResult:
        # Only nonce-stamped sentinels are honoured (S5 result-channel integrity).
        status_prefix = f"<<<DECYRA_STATUS:{nonce}>>>"
        chart_begin = f"<<<DECYRA_CHART_B64:{nonce}>>>"
        chart_end = f"<<<DECYRA_CHART_END:{nonce}>>>"

        status = "error"
        if status_prefix in logs:
            status = logs.split(status_prefix, 1)[1].splitlines()[0].strip()
        elif returncode == 137:
            # docker run returns 128+SIGKILL(9) when the container is OOM- or
            # force-killed (e.g. the --memory cap fires). Surface it positively
            # so the memory-bomb proof is a real assertion, not the silent
            # "no status line → error" default.
            return SandboxResult("killed", None, logs)

        png = None
        if chart_begin in logs and chart_end in logs:
            b64 = logs.split(chart_begin, 1)[1].split(chart_end, 1)[0]
            try:
                png = base64.b64decode(b64)
            except Exception:
                png = None
        return SandboxResult(status=status, chart_png=png, output=logs)


def get_sandbox_runner(settings: Settings) -> SandboxRunner:
    return DockerSandboxRunner(settings)
