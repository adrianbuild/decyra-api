from unittest.mock import MagicMock

import pytest

from app.config import Settings
from app.sandbox import runner as runner_mod


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


def test_runner_concurrency_capped(monkeypatch):
    import app.sandbox.runner as runner_mod

    s = _settings()
    s.sandbox_max_concurrency = 2
    r = runner_mod.DockerSandboxRunner(s)
    assert type(r)._sem_size == 2
