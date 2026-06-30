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
