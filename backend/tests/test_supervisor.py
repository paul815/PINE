import json
import logging
import shutil
import threading
import time
import urllib.request
from pathlib import Path

from http.server import ThreadingHTTPServer

import supervisor
from supervisor import BackendSupervisor, make_supervisor_handler, SUPERVISOR_TOKEN

_TOKEN_HEADER = {"X-Pine-Supervisor-Token": SUPERVISOR_TOKEN}


class _FakeProc:
    def __init__(self, alive=True):
        self._alive = alive

    def poll(self):
        return None if self._alive else 0


def test_backend_running_true_for_owned_alive_process(monkeypatch):
    sup = BackendSupervisor()
    sup._proc = _FakeProc(alive=True)
    monkeypatch.setattr(sup, "_probe_backend_http", lambda: False)
    assert sup.backend_running() is True


def test_backend_running_true_for_unmanaged_http_backend(monkeypatch):
    sup = BackendSupervisor()
    sup._proc = None
    monkeypatch.setattr(sup, "_probe_backend_http", lambda: True)
    assert sup.backend_running() is True


def test_backend_running_false_when_no_process_and_no_http(monkeypatch):
    sup = BackendSupervisor()
    sup._proc = None
    monkeypatch.setattr(sup, "_probe_backend_http", lambda: False)
    assert sup.backend_running() is False


def test_backend_ready_reflects_http_probe(monkeypatch):
    sup = BackendSupervisor()
    monkeypatch.setattr(sup, "_probe_backend_http", lambda: True)
    assert sup.backend_ready() is True

    monkeypatch.setattr(sup, "_probe_backend_http", lambda: False)
    assert sup.backend_ready() is False


def test_resolve_backend_python_prefers_console_python_for_pythonw(monkeypatch):
    monkeypatch.setattr(Path, "exists", lambda self: self.name.lower() == "python.exe")
    resolved = BackendSupervisor._resolve_backend_python(Path(r"C:\test\pythonw.exe"))
    assert resolved == Path(r"C:\test\python.exe")


def test_configure_logging_skips_stream_handler_without_stderr(monkeypatch):
    temp_root = Path.cwd() / "logs" / "test-no-stderr"
    temp_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("PINE_LOG_DIR", str(temp_root))
    monkeypatch.setattr(supervisor.sys, "stderr", None)

    root = logging.getLogger()
    original_handlers = list(root.handlers)

    try:
        log_path = supervisor._configure_logging()
        assert log_path.parent.parent == temp_root
        managed_handlers = [handler for handler in root.handlers if getattr(handler, "_pine_managed", False)]
        assert len(managed_handlers) == 1
        assert isinstance(managed_handlers[0], logging.FileHandler)
    finally:
        for handler in list(root.handlers):
            if getattr(handler, "_pine_managed", False):
                root.removeHandler(handler)
                handler.close()
        for handler in original_handlers:
            if handler not in root.handlers:
                root.addHandler(handler)
        shutil.rmtree(temp_root, ignore_errors=True)


def test_restart_backend_spawns_when_managed_process_dead(monkeypatch):
    sup = BackendSupervisor()
    sup._proc = _FakeProc(alive=False)
    spawned = {"count": 0}

    monkeypatch.setattr(sup, "_probe_backend_http", lambda: False)

    def _spawn_backend(new_console=False):
        spawned["count"] += 1

    monkeypatch.setattr(sup, "_spawn_backend", _spawn_backend)
    sup.restart_backend()
    assert spawned["count"] == 1


def test_restart_backend_stops_unmanaged_backend_then_spawns(monkeypatch):
    sup = BackendSupervisor()
    sup._proc = None
    spawned = {"count": 0}
    stopped = {"count": 0}
    monkeypatch.setattr(sup, "_probe_backend_http", lambda: True)
    monkeypatch.setattr(
        sup,
        "_request_unmanaged_backend_stop_locked",
        lambda timeout: stopped.__setitem__("count", stopped["count"] + 1),
    )
    monkeypatch.setattr(sup, "_spawn_backend", lambda new_console=False: spawned.__setitem__("count", spawned["count"] + 1))
    sup.restart_backend()
    assert stopped["count"] == 1
    assert spawned["count"] == 1


def test_monitor_loop_keeps_running_while_lease_grace_is_active(monkeypatch):
    sup = BackendSupervisor()
    shutdown_attempts = {"count": 0}

    monkeypatch.setattr(sup, "request_shutdown", lambda: shutdown_attempts.__setitem__("count", shutdown_attempts["count"] + 1))

    def _sleep_once(_seconds):
        sup._shutdown_requested = True

    monkeypatch.setattr(time, "sleep", _sleep_once)
    sup._lease_grace_until = time.time() + 60
    sup._leases.clear()

    sup.monitor_loop()

    assert shutdown_attempts["count"] == 0


def test_monitor_loop_shuts_down_when_no_leases_remain_after_grace(monkeypatch):
    sup = BackendSupervisor()
    shutdown_attempts = {"count": 0}

    def _request_shutdown(**_kwargs):
        shutdown_attempts["count"] += 1
        sup._shutdown_requested = True

    monkeypatch.setattr(sup, "request_shutdown", _request_shutdown)
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)
    sup._lease_grace_until = time.time() - 1
    sup._leases.clear()

    sup.monitor_loop()

    assert shutdown_attempts["count"] == 1


def test_monitor_loop_extends_grace_when_last_lease_expires_without_release(monkeypatch):
    sup = BackendSupervisor()
    shutdown_attempts = {"count": 0}
    sleep_calls = {"count": 0}
    before = time.time()

    sup._leases = {"tab-1": before - supervisor.LEASE_TIMEOUT_SECONDS - 1}
    sup._lease_grace_until = before - 1

    monkeypatch.setattr(sup, "request_shutdown", lambda: shutdown_attempts.__setitem__("count", shutdown_attempts["count"] + 1))

    def _sleep_once(_seconds):
        sleep_calls["count"] += 1
        if sleep_calls["count"] > 1:
            sup._shutdown_requested = True

    monkeypatch.setattr(time, "sleep", _sleep_once)

    sup.monitor_loop()

    assert shutdown_attempts["count"] == 0
    assert not sup._leases
    remaining = sup._lease_grace_until - before
    assert remaining > supervisor.EXPIRED_LEASE_SHUTDOWN_GRACE_SECONDS - 1


def test_release_lease_shortens_grace_when_last_tab_closes():
    sup = BackendSupervisor()
    before = time.time()

    sup.heartbeat_lease("tab-1")
    count = sup.release_lease("tab-1")

    assert count == 0
    remaining = sup._lease_grace_until - before
    assert 0 < remaining <= supervisor.LEASE_RELEASE_SHUTDOWN_GRACE_SECONDS + 0.5


def test_restart_endpoint_and_status_and_shutdown():
    class _SupStub:
        _backend_port = 5000
        _supervisor_port = 5001

        def __init__(self):
            self.restart_called = 0
            self.shutdown_called = 0
            self.leases = set()

        def backend_running(self):
            return True

        def backend_ready(self):
            return False

        def lease_count(self):
            return len(self.leases)

        def restart_backend(self):
            self.restart_called += 1

        def heartbeat_lease(self, lease_id):
            self.leases.add(lease_id)
            return len(self.leases)

        def release_lease(self, lease_id):
            self.leases.discard(lease_id)
            return len(self.leases)

        def request_shutdown(self, **_kwargs):
            self.shutdown_called += 1

    stop_evt = threading.Event()
    sup = _SupStub()

    handler = make_supervisor_handler(sup, stop_evt)
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    host, port = server.server_address
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urllib.request.urlopen(f"http://{host}:{port}/status", timeout=2) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
            assert payload["ok"] is True
            assert payload["supervisor_running"] is True
            assert payload["backend_running"] is True
            assert payload["backend_ready"] is False

        req_hb = urllib.request.Request(
            f"http://{host}:{port}/lease/heartbeat",
            method="POST",
            data=json.dumps({"lease_id": "tab-1"}).encode("utf-8"),
            headers={"Content-Type": "application/json", **_TOKEN_HEADER},
        )
        with urllib.request.urlopen(req_hb, timeout=2) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
            assert payload["ok"] is True
            assert payload["action"] == "lease_heartbeat"
            assert payload["lease_count"] == 1

        req_rel = urllib.request.Request(
            f"http://{host}:{port}/lease/release",
            method="POST",
            data=json.dumps({"lease_id": "tab-1"}).encode("utf-8"),
            headers={"Content-Type": "application/json", **_TOKEN_HEADER},
        )
        with urllib.request.urlopen(req_rel, timeout=2) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
            assert payload["ok"] is True
            assert payload["action"] == "lease_release"
            assert payload["lease_count"] == 0

        req_restart = urllib.request.Request(
            f"http://{host}:{port}/restart", method="POST",
            headers=_TOKEN_HEADER,
        )
        with urllib.request.urlopen(req_restart, timeout=2) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
            assert payload["ok"] is True
            assert payload["action"] == "restart"
        assert sup.restart_called == 1

        req_shutdown = urllib.request.Request(
            f"http://{host}:{port}/shutdown", method="POST",
            headers=_TOKEN_HEADER,
        )
        with urllib.request.urlopen(req_shutdown, timeout=2) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
            assert payload["ok"] is True
            assert payload["action"] == "shutdown"
        assert sup.shutdown_called == 1

        # Give handler a moment to set the event.
        deadline = time.time() + 2
        while time.time() < deadline and not stop_evt.is_set():
            time.sleep(0.02)
        assert stop_evt.is_set()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_shutdown_endpoint_responds_without_waiting_for_backend_stop():
    class _SupStub:
        _backend_port = 5000
        _supervisor_port = 5001

        def __init__(self):
            self.shutdown_called = threading.Event()

        def backend_running(self):
            return True

        def backend_ready(self):
            return True

        def lease_count(self):
            return 0

        def restart_backend(self):
            return None

        def heartbeat_lease(self, lease_id):
            return 1

        def release_lease(self, lease_id):
            return 0

        def request_shutdown(self, **_kwargs):
            self.shutdown_called.set()
            time.sleep(0.4)

    stop_evt = threading.Event()
    sup = _SupStub()

    handler = make_supervisor_handler(sup, stop_evt)
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    host, port = server.server_address
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        req_shutdown = urllib.request.Request(
            f"http://{host}:{port}/shutdown", method="POST",
            headers=_TOKEN_HEADER,
        )
        started = time.perf_counter()
        with urllib.request.urlopen(req_shutdown, timeout=0.2) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        elapsed = time.perf_counter() - started

        assert payload["ok"] is True
        assert payload["action"] == "shutdown"
        assert elapsed < 0.3
        assert stop_evt.wait(timeout=0.5)
        assert sup.shutdown_called.wait(timeout=0.5)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_shutdown_endpoint_logs_context_fields(caplog):
    class _SupStub:
        _backend_port = 5000
        _supervisor_port = 5001

        def backend_running(self):
            return True

        def backend_ready(self):
            return True

        def lease_count(self):
            return 0

        def restart_backend(self):
            return None

        def heartbeat_lease(self, lease_id):
            return 1

        def release_lease(self, lease_id):
            return 0

        def request_shutdown(self, **_kwargs):
            return None

    stop_evt = threading.Event()
    sup = _SupStub()

    caplog.set_level(logging.INFO)
    handler = make_supervisor_handler(sup, stop_evt)
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    host, port = server.server_address
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        req_shutdown = urllib.request.Request(
            f"http://{host}:{port}/shutdown",
            method="POST",
            data=json.dumps(
                {
                    "reason": "user_quit_action",
                    "source": "main_page",
                    "lease_id": "tab-1",
                }
            ).encode("utf-8"),
            headers={"Content-Type": "application/json", **_TOKEN_HEADER},
        )
        with urllib.request.urlopen(req_shutdown, timeout=2) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
            assert payload["ok"] is True
            assert payload["action"] == "shutdown"
        assert any(
            "Shutdown HTTP request received reason=user_quit_action source=main_page lease_id=tab-1"
            in msg
            for msg in caplog.messages
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_shutdown_endpoint_logs_unknown_context_when_missing_payload(caplog):
    class _SupStub:
        _backend_port = 5000
        _supervisor_port = 5001

        def backend_running(self):
            return True

        def backend_ready(self):
            return True

        def lease_count(self):
            return 0

        def restart_backend(self):
            return None

        def heartbeat_lease(self, lease_id):
            return 1

        def release_lease(self, lease_id):
            return 0

        def request_shutdown(self, **_kwargs):
            return None

    stop_evt = threading.Event()
    sup = _SupStub()

    caplog.set_level(logging.INFO)
    handler = make_supervisor_handler(sup, stop_evt)
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    host, port = server.server_address
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        req_shutdown = urllib.request.Request(
            f"http://{host}:{port}/shutdown",
            method="POST",
            headers=_TOKEN_HEADER,
        )
        with urllib.request.urlopen(req_shutdown, timeout=2) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
            assert payload["ok"] is True
            assert payload["action"] == "shutdown"
        assert any(
            "Shutdown HTTP request received reason=unknown source=unspecified lease_id=-"
            in msg
            for msg in caplog.messages
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_monitor_loop_logs_system_shutdown_reason(caplog, monkeypatch):
    sup = BackendSupervisor()
    caplog.set_level(logging.INFO)

    monkeypatch.setattr(sup, "_terminate_locked", lambda timeout: None)
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)
    sup._lease_grace_until = time.time() - 1
    sup._leases.clear()

    sup.monitor_loop()

    assert any(
        "Supervisor shutdown requested reason=no_active_leases_grace_elapsed source=supervisor_monitor"
        in msg
        for msg in caplog.messages
    )
