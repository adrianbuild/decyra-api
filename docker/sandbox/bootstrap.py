"""Sandbox entrypoint. Reads a JSON bundle from stdin, runs user code against the
input file in /tmp, emits the produced chart as base64 between sentinels on stdout.
The ONLY input channel is stdin; the ONLY output channel is stdout. No network, no
host FS (enforced by the container flags, not by this script)."""
import base64
import json
import os
import sys
import traceback

# Drop base-image build-time vars before user code runs. GPG_KEY / PYTHON_SHA256
# are PUBLIC constants the python:slim base used to verify the release tarball at
# BUILD time — they are NOT host secrets (the container gets no host env at all,
# proven by the S2 test) — but their names muddy a "no secret in env" inspection.
# Removing them is plain env hygiene / defense in depth, NOT test-gaming: nothing
# host-derived was ever present to begin with.
for _k in ("GPG_KEY", "PYTHON_SHA256"):
    os.environ.pop(_k, None)

CHART_PATH = "/tmp/chart.png"


def main() -> None:
    raw = sys.stdin.buffer.read()
    bundle = json.loads(raw)
    filename = bundle["filename"]
    file_bytes = base64.b64decode(bundle["file_b64"])
    code = bundle["code"]
    # Per-run nonce: kept in a LOCAL, NEVER placed into the user-code globals
    # dict ``g`` below. The result channel shares this stdout with user prints,
    # so only nonce-stamped sentinels are trusted by the runner. User code
    # cannot read the nonce → cannot forge a status/chart that the runner honours.
    nonce = bundle["nonce"]
    status_prefix = f"<<<DECYRA_STATUS:{nonce}>>>"
    chart_begin = f"<<<DECYRA_CHART_B64:{nonce}>>>"
    chart_end = f"<<<DECYRA_CHART_END:{nonce}>>>"

    input_path = f"/tmp/{filename}"
    with open(input_path, "wb") as fh:
        fh.write(file_bytes)

    # User code may reference INPUT_PATH and must write its chart to CHART_PATH.
    # ``nonce`` is deliberately absent from this dict.
    g = {"INPUT_PATH": input_path, "CHART_PATH": CHART_PATH, "__name__": "__sandbox__"}
    try:
        exec(compile(code, "<generated>", "exec"), g)  # noqa: S102 — the sandbox is the boundary
    except Exception:
        sys.stdout.write(status_prefix + "error\n")
        sys.stdout.write(traceback.format_exc())
        sys.stdout.flush()
        return

    try:
        with open(CHART_PATH, "rb") as fh:
            png = fh.read()
    except FileNotFoundError:
        sys.stdout.write(status_prefix + "no_chart\n")
        sys.stdout.flush()
        return

    sys.stdout.write(status_prefix + "ok\n")
    sys.stdout.write(chart_begin + base64.b64encode(png).decode() + chart_end + "\n")
    sys.stdout.flush()


if __name__ == "__main__":
    main()
