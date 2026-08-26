"""The watchdog's kill must not queue behind the start it exists to interrupt.

get_client() used to hold the module lock across ensure_started(), which can sit
for a long time: it downloads ffmpeg on a fresh venv, then blocks on the worker's
first line with no timeout. kill_worker() takes the same lock, so the watchdog
thread stopped there — and everything after that call in _watchdog_loop, the
generation bump and the new transcription worker, never ran. One stall during
startup took the whole transcription subsystem down until a restart.
"""

import threading
import time

import pytest

from app.services.transcription import worker_client


@pytest.fixture(autouse=True)
def _isolated_singleton(monkeypatch):
    monkeypatch.setattr(worker_client, '_client', None)
    yield
    worker_client._client = None


class _StallingClient:
    """A client whose start blocks until released, like a hung ffmpeg fetch."""

    def __init__(self, release):
        self.release = release
        self.killed = threading.Event()
        self.started = threading.Event()

    def ensure_started(self):
        self.started.set()
        self.release.wait(timeout=10)

    def kill(self):
        self.killed.set()


def test_kill_worker_returns_while_a_start_is_stuck(monkeypatch):
    release = threading.Event()
    client = _StallingClient(release)
    monkeypatch.setattr(worker_client, 'MLWorkerClient', lambda: client)

    starter = threading.Thread(target=worker_client.get_client, daemon=True)
    starter.start()
    assert client.started.wait(timeout=5), 'the start never began'

    killed_in_time = threading.Event()

    def _kill():
        worker_client.kill_worker()
        killed_in_time.set()

    killer = threading.Thread(target=_kill, daemon=True)
    killer.start()

    # Before the fix this blocked on the module lock until the start finished.
    assert killed_in_time.wait(timeout=5), 'kill_worker blocked behind the start'
    assert client.killed.is_set()

    release.set()
    starter.join(timeout=5)
    killer.join(timeout=5)


def test_two_threads_asking_at_once_still_start_one_worker(monkeypatch):
    """The module lock stopped double spawns; _start_lock has to keep doing it."""
    starts = []
    entered = threading.Event()

    class _CountingClient(worker_client.MLWorkerClient):
        def _start(self):
            entered.set()
            time.sleep(0.2)          # widen the window a racing thread needs
            starts.append(1)
            self._proc = _FakeProc()

    class _FakeProc:
        def poll(self):
            return None

    monkeypatch.setattr(worker_client, 'MLWorkerClient', _CountingClient)

    threads = [threading.Thread(target=worker_client.get_client, daemon=True)
               for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)

    assert starts == [1], f'spawned {len(starts)} workers'


def test_a_client_that_never_started_is_not_killed(monkeypatch):
    """No worker, nothing to kill — and no exception either."""
    worker_client.kill_worker()   # _client is None
