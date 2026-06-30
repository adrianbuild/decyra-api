from unittest.mock import MagicMock

import pytest

from app.config import Settings
from app.sandbox import runner as runner_mod


def _settings():
    return Settings(database_url="postgresql://x/y")


def test_runner_force_removes_container_even_on_error(monkeypatch):
    """Teardown is GUARANTEED even when the run itself blows up mid-flight.

    The CLI runner (the plan's documented fallback, because the docker-SDK
    attach_socket stdin path did not deliver EOF on this Docker Desktop) runs the
    container via ``subprocess.run`` and force-removes it in ``finally`` via
    ``_force_remove``. We make the run raise a RuntimeError mid-flight and prove
    (a) it propagates (a real error is never silently masked) and (b) the named
    container is still force-removed exactly once."""
    removed: list[str] = []
    monkeypatch.setattr(runner_mod, "_force_remove", lambda name: removed.append(name))

    def _boom(*args, **kwargs):
        raise RuntimeError("boom mid-run")

    monkeypatch.setattr(runner_mod.subprocess, "run", _boom)

    r = runner_mod.DockerSandboxRunner(_settings())
    with pytest.raises(RuntimeError):
        r.run(file_bytes=b"a,b\n1,2", filename="x.csv", code="pass")

    assert len(removed) == 1  # force-removed exactly once, despite the mid-run error


def test_runner_concurrency_capped(monkeypatch):
    import app.sandbox.runner as runner_mod

    s = _settings()
    s.sandbox_max_concurrency = 2
    r = runner_mod.DockerSandboxRunner(s)
    assert type(r)._sem_size == 2


def test_reaper_removes_orphans(monkeypatch):
    from app.sandbox import reaper

    c = MagicMock()
    client = MagicMock()
    client.containers.list.return_value = [c, c]
    monkeypatch.setattr("docker.from_env", lambda: client)
    assert reaper.reap_orphans() == 2
    assert c.remove.call_count == 2
