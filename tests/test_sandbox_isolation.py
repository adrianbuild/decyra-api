import pytest

from app.config import Settings
from app.sandbox.runner import DockerSandboxRunner
from tests.conftest import requires_docker

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
    res = _runner().run(
        file_bytes=CSV,
        filename="d.csv",
        code="import os\nassert os.getuid()==5000\nopen(CHART_PATH,'wb').write(b'')\n",
    )
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
    left = docker.from_env().containers.list(
        all=True, filters={"label": "decyra.sandbox=1"}
    )
    assert left == []


def test_sandbox_container_removed_on_timeout():  # S4
    import docker

    _runner().run(file_bytes=CSV, filename="d.csv", code="while True: pass\n")
    left = docker.from_env().containers.list(
        all=True, filters={"label": "decyra.sandbox=1"}
    )
    assert left == []
