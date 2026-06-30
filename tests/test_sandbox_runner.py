import threading
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


def test_runner_concurrency_capped_blocks(monkeypatch):
    """The cap must actually BLOCK, not merely report a size.

    With concurrency=1, the second run must NOT be able to enter the critical
    section (the docker call) until the first releases its permit. We patch the
    docker call to block on an event and prove run #2 is held outside the
    section while #1 is in-flight, then proceeds once #1 finishes. A short
    timeout guards against a hang if the semaphore were defeated.

    Old behaviour this guards against: when the semaphore was a per-instance
    rebindable class attribute, a permit held on a stale semaphore would let a
    second run slip through — i.e. this blocking assertion would fail."""
    monkeypatch.setattr(runner_mod, "_SEM", threading.Semaphore(1))

    in_section = threading.Event()      # set while run #1 holds the section
    release_first = threading.Event()   # test releases run #1
    entered = []                        # records entry order

    def _blocking_run(*args, **kwargs):
        entered.append(threading.current_thread().name)
        if threading.current_thread().name == "first":
            in_section.set()
            # Hold the permit until the test explicitly releases it.
            if not release_first.wait(timeout=5):
                raise AssertionError("release_first never signalled")
        # Minimal fake CompletedProcess: no status line, rc 0 → status 'error'.
        return MagicMock(stdout=b"", stderr=b"", returncode=0)

    monkeypatch.setattr(runner_mod, "_force_remove", lambda name: None)
    monkeypatch.setattr(runner_mod.subprocess, "run", _blocking_run)

    r = runner_mod.DockerSandboxRunner(_settings())

    def _do():
        r.run(file_bytes=b"a,b\n1,2", filename="x.csv", code="pass")

    t1 = threading.Thread(target=_do, name="first")
    t2 = threading.Thread(target=_do, name="second")
    t1.start()
    assert in_section.wait(timeout=5), "run #1 never entered the section"

    # Run #1 holds the only permit. Start run #2; it must be BLOCKED on the
    # semaphore — it cannot have entered _blocking_run yet.
    t2.start()
    t2.join(timeout=1.0)              # give it a chance to (wrongly) proceed
    assert t2.is_alive(), "run #2 entered the section while #1 held the cap"
    assert entered == ["first"], "only run #1 should have entered so far"

    # Release run #1; run #2 may now acquire the permit and proceed.
    release_first.set()
    t1.join(timeout=5)
    t2.join(timeout=5)
    assert not t1.is_alive() and not t2.is_alive()
    assert entered == ["first", "second"]


def test_force_remove_warns_and_retries_on_persistent_failure(monkeypatch, caplog):
    """Timeout teardown needs an OBSERVABLE backstop.

    Old behaviour: ``_force_remove`` swallowed every exception silently with a
    single attempt — so a ``docker rm -f`` that kept failing left an infinite-
    loop container pinning a CPU with NO signal. The hardened version retries
    once and emits a ``logging.warning`` if removal ultimately fails, while
    still never raising. This test proves: (a) it retried (2 attempts), (b) a
    warning naming the container was logged, (c) no exception propagated.

    Against the old code: attempts==1 and no warning → both asserts fail."""
    import logging

    attempts = {"n": 0}

    def _always_fail(*args, **kwargs):
        attempts["n"] += 1
        raise RuntimeError("docker daemon unreachable")

    monkeypatch.setattr(runner_mod.subprocess, "run", _always_fail)

    with caplog.at_level(logging.WARNING):
        runner_mod._force_remove("decyra-sbx-stuck")  # must NOT raise

    assert attempts["n"] == 2, "should retry once (2 total attempts)"
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any("decyra-sbx-stuck" in r.getMessage() for r in warnings), (
        "persistent teardown failure must be logged (observable backstop)"
    )


def test_force_remove_swallows_already_gone(monkeypatch, caplog):
    """A container already removed by --rm (rc!=0, 'No such container') is the
    success case — no warning, no retry-storm."""
    import logging

    calls = {"n": 0}

    def _no_such(*args, **kwargs):
        calls["n"] += 1
        return MagicMock(returncode=1, stderr=b"Error: No such container: x")

    monkeypatch.setattr(runner_mod.subprocess, "run", _no_such)
    with caplog.at_level(logging.WARNING):
        runner_mod._force_remove("decyra-sbx-gone")

    assert calls["n"] == 1, "already-gone is success → no retry"
    assert not [r for r in caplog.records if r.levelno == logging.WARNING]


def test_reaper_removes_orphans(monkeypatch):
    from app.sandbox import reaper

    c = MagicMock()
    client = MagicMock()
    client.containers.list.return_value = [c, c]
    monkeypatch.setattr("docker.from_env", lambda: client)
    assert reaper.reap_orphans() == 2
    assert c.remove.call_count == 2
