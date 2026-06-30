"""Sandbox entrypoint (container PID 1, the TRUSTED parent).

Reads a JSON bundle from stdin (``filename``, ``file_b64``, ``code``, ``nonce``),
writes the input file to /tmp, then runs the untrusted user code in a SEPARATE
child process and derives the trusted result (status + chart) from the child's
PROCESS RESULT — never from anything the child printed.

Why a separate process (out-of-band result channel): the per-run ``nonce`` proves
the status/chart sentinels came from us. If user code ran via ``exec`` in THIS
process it could walk the call stack (``sys._getframe``) and recover the nonce
from the parent frame, then forge a nonce-stamped ``status=ok`` + chart straight
to stdout. Running the user code as a child means (a) the child cannot reach the
parent's nonce — it lives only in this process, never in the child's env or code
— and (b) the child's stdout is a PIPE, so it cannot write to the container's real
stdout at all. The parent is the only writer of nonce-stamped lines.

The ONLY input channel is stdin; the ONLY trusted output channel is this process's
stdout. No network, no host FS (enforced by the container flags, not this script)."""
import base64
import json
import os
import subprocess
import sys

# Drop base-image build-time vars before we do anything. GPG_KEY / PYTHON_SHA256
# are PUBLIC constants the python:slim base used to verify the release tarball at
# BUILD time — they are NOT host secrets (the container gets no host env at all,
# proven by the S2 test) — but their names muddy a "no secret in env" inspection.
# Removing them is plain env hygiene / defense in depth, NOT test-gaming: nothing
# host-derived was ever present to begin with. The child gets a curated env that
# does not include them either (see ``child_env`` below).
for _k in ("GPG_KEY", "PYTHON_SHA256"):
    os.environ.pop(_k, None)

CHART_PATH = "/tmp/chart.png"
CHILD_PATH = "/tmp/_child.py"

# The PNG magic. We trust a chart ONLY if the child wrote a real PNG, so user
# code can't drop an arbitrary file at CHART_PATH and have it returned as "ok".
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def main() -> None:
    raw = sys.stdin.buffer.read()
    bundle = json.loads(raw)
    filename = bundle["filename"]
    file_bytes = base64.b64decode(bundle["file_b64"])
    code = bundle["code"]
    # Per-run nonce: stays in THIS (parent) frame ONLY. It is never written to the
    # child program, the child env, or any file the child can read — so the child
    # cannot recover it and cannot forge a nonce-stamped status/chart line.
    nonce = bundle["nonce"]
    status_prefix = f"<<<DECYRA_STATUS:{nonce}>>>"
    chart_begin = f"<<<DECYRA_CHART_B64:{nonce}>>>"
    chart_end = f"<<<DECYRA_CHART_END:{nonce}>>>"

    input_path = f"/tmp/{filename}"
    with open(input_path, "wb") as fh:
        fh.write(file_bytes)

    # Build the child program: a small preamble defining the two globals the
    # generated code expects (INPUT_PATH, CHART_PATH), then the user code. Paths
    # are embedded via json.dumps so they are safely quoted Python literals.
    preamble = (
        f"INPUT_PATH = {json.dumps(input_path)}\n"
        f"CHART_PATH = {json.dumps(CHART_PATH)}\n"
    )
    with open(CHILD_PATH, "w", encoding="utf-8") as fh:
        fh.write(preamble)
        fh.write(code)

    # Curated env — NO nonce, NO host secret, NO base-image build vars. Just what
    # CPython + matplotlib need to run headless and write their config to /tmp.
    child_env = {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "HOME": "/tmp",
        "LANG": "C.UTF-8",
        "MPLBACKEND": "Agg",
        "MPLCONFIGDIR": "/tmp/mpl",
        "PYTHONDONTWRITEBYTECODE": "1",
    }

    # Run the untrusted code as a SEPARATE PROCESS.
    #  - stdin DEVNULL: the child has no input channel.
    #  - stdout PIPE + stderr→stdout: the child CANNOT reach the container's real
    #    stdout; everything it prints is captured here for re-emit (traceback
    #    feedback) but is NEVER trusted as a result.
    #  - NO inner timeout: the OUTER docker timeout (the runner) is the single
    #    timeout authority. An infinite loop here gets the whole container killed,
    #    and the runner surfaces "timeout".
    proc = subprocess.run(
        [sys.executable, CHILD_PATH],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        cwd="/tmp",
        env=child_env,
    )
    child_output = proc.stdout.decode("utf-8", errors="replace")
    rc = proc.returncode

    # Derive the trusted status from the child's PROCESS RESULT only.
    if rc == -9:
        # SIGKILL — the OOM-killer killed the child (the --memory cap fired).
        status = "killed"
        png = None
    elif rc != 0:
        # Any other signal (rc < 0) or non-zero exit → the user code failed.
        status = "error"
        png = None
    else:
        # Clean exit. A chart counts ONLY if a real PNG was written.
        png = None
        try:
            with open(CHART_PATH, "rb") as fh:
                data = fh.read()
        except FileNotFoundError:
            data = b""
        if data[:8] == PNG_MAGIC:
            status = "ok"
            png = data
        else:
            status = "no_chart"

    # Emit the trusted, nonce-stamped result on the REAL stdout.
    out = sys.stdout
    out.write(status_prefix + status + "\n")
    if status == "ok" and png is not None:
        out.write(chart_begin + base64.b64encode(png).decode() + chart_end + "\n")
    # Re-emit the child's captured output AFTER the status line, as plain
    # parent-controlled text. This carries the child's traceback for retry
    # feedback and for the isolation tests that grep it (URLError,
    # FileNotFoundError, AssertionError, ...). It is framing we control, and it
    # cannot itself be a valid nonce-stamped status (the child never had the
    # nonce), so it is not forgeable.
    out.write(child_output)
    out.flush()


if __name__ == "__main__":
    main()
