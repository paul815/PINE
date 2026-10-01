import json
import logging
import os
import shutil
import socket
import threading
import time
import urllib.request
from datetime import datetime
from http.server import ThreadingHTTPServer
from pathlib import Path

import supervisor
from supervisor import SUPERVISOR_TOKEN, BackendSupervisor, make_supervisor_handler

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
    """pythonw.exe has no console, so the backend is started with python.exe.

    The swap only ever happens on Windows, but it is plain path work, so it is
    worth exercising everywhere.  The directory has to be built with the
    running platform's separator: PosixPath(r"C:\\test\\pythonw.exe") is a
    single name with backslashes in it, .name never equals "pythonw.exe", and
    the test would assert against a swap that could not have happened.
    """
    launcher = Path(r"C:\test\pythonw.exe") if os.name == "nt" else Path("/test/pythonw.exe")

    monkeypatch.setattr(Path, "exists", lambda self: self.name.lower() == "python.exe")
    resolved = BackendSupervisor._resolve_backend_python(launcher)

    assert resolved == launcher.with_name("python.exe")


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
    # Otherwise the probe reaches whatever is really listening on the backend
    # port — a PINE the developer left running answers for it.
    monkeypatch.setattr(sup, "_backend_busy", lambda: False)
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


def test_release_lease_keeps_the_startup_grace_after_a_restart():
    """A page unloading because the backend under it restarted is not a quit.

    Its release lands on the supervisor while the /api/quit beside it dies with
    the old backend, so two seconds later the supervisor would shut down the
    backend the reloading page is about to look for.
    """
    sup = BackendSupervisor()
    before = time.time()
    sup._restart_grace_until = before + supervisor.STARTUP_LEASE_GRACE_SECONDS

    sup.heartbeat_lease("tab-1")
    sup.release_lease("tab-1")

    remaining = sup._lease_grace_until - before
    assert remaining > supervisor.LEASE_RELEASE_SHUTDOWN_GRACE_SECONDS
    assert remaining <= supervisor.STARTUP_LEASE_GRACE_SECONDS + 0.5


def test_monitor_loop_holds_on_while_the_backend_is_still_transcribing(monkeypatch):
    """A backgrounded tab stops heartbeating long before a long job finishes."""
    sup = BackendSupervisor()
    shutdown_attempts = {"count": 0}
    sleep_calls = {"count": 0}
    before = time.time()

    sup._leases.clear()
    sup._lease_grace_until = before - 1

    monkeypatch.setattr(sup, "_backend_busy", lambda: True)
    monkeypatch.setattr(
        sup,
        "request_shutdown",
        lambda **_kwargs: shutdown_attempts.__setitem__("count", shutdown_attempts["count"] + 1),
    )

    def _sleep_twice(_seconds):
        sleep_calls["count"] += 1
        if sleep_calls["count"] > 1:
            sup._shutdown_requested = True

    monkeypatch.setattr(time, "sleep", _sleep_twice)

    sup.monitor_loop()

    assert shutdown_attempts["count"] == 0
    held_for = sup._lease_grace_until - before
    assert held_for >= supervisor.BUSY_BACKEND_RECHECK_SECONDS - 1


def test_monitor_loop_shuts_down_once_the_backend_reports_itself_idle(monkeypatch):
    sup = BackendSupervisor()
    shutdown_attempts = {"count": 0}

    def _request_shutdown(**_kwargs):
        shutdown_attempts["count"] += 1
        sup._shutdown_requested = True

    monkeypatch.setattr(sup, "request_shutdown", _request_shutdown)
    monkeypatch.setattr(sup, "_backend_busy", lambda: False)
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)
    sup._leases.clear()
    sup._lease_grace_until = time.time() - 1

    sup.monitor_loop()

    assert shutdown_attempts["count"] == 1


def test_backend_busy_reads_the_health_payload(monkeypatch):
    sup = BackendSupervisor()

    monkeypatch.setattr(sup, "_probe_backend_health", lambda: {"ok": True, "busy": True})
    assert sup._backend_busy() is True

    monkeypatch.setattr(sup, "_probe_backend_health", lambda: {"ok": True, "busy": False})
    assert sup._backend_busy() is False


def test_backend_that_never_answers_is_not_busy(monkeypatch):
    """Unreachable is gone, not working — the shutdown it blocked goes ahead."""
    sup = BackendSupervisor()
    monkeypatch.setattr(sup, "_probe_backend_health", lambda: None)
    assert sup._backend_busy() is False
    assert sup._probe_backend_http() is False


def test_backend_health_without_a_busy_field_is_not_busy(monkeypatch):
    """An older backend answers the probe but knows nothing about jobs."""
    sup = BackendSupervisor()
    monkeypatch.setattr(sup, "_probe_backend_health", lambda: {"ok": True})
    assert sup._backend_busy() is False
    assert sup._probe_backend_http() is True


def test_cleanup_sweeps_the_undashed_install_log_folders(monkeypatch, tmp_path):
    from datetime import timedelta

    monkeypatch.setenv("PINE_LOG_DIR", str(tmp_path))
    old = datetime.now() - timedelta(days=30)
    recent = datetime.now() - timedelta(days=1)
    stale_legacy = tmp_path / old.strftime("%Y%m%d")
    stale_dashed = tmp_path / old.strftime("%Y-%m-%d")
    fresh_legacy = tmp_path / recent.strftime("%Y%m%d")
    not_a_date = tmp_path / "archive"
    for folder in (stale_legacy, stale_dashed, fresh_legacy, not_a_date):
        folder.mkdir()

    supervisor._cleanup_old_log_folders(max_age_days=7)

    assert not stale_legacy.exists()
    assert not stale_dashed.exists()
    assert fresh_legacy.is_dir()
    assert not_a_date.is_dir()


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


class _CorsSupStub:
    _backend_port = 5000
    _supervisor_port = 5001

    def backend_running(self):
        return True

    def backend_ready(self):
        return True

    def lease_count(self):
        return 0


def _status_headers(origin=None):
    """Fetch /status from a real supervisor server and return its headers."""
    handler = make_supervisor_handler(_CorsSupStub(), threading.Event())
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    host, port = server.server_address
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        req = urllib.request.Request(f"http://{host}:{port}/status")
        if origin is not None:
            req.add_header("Origin", origin)
        with urllib.request.urlopen(req, timeout=2) as resp:
            return dict(resp.headers)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_status_grants_cors_to_the_apps_own_origin():
    """The UI is served on one port and calls the supervisor on another."""
    headers = _status_headers(origin="http://pine.localhost:5000")

    assert headers["Access-Control-Allow-Origin"] == "http://pine.localhost:5000"
    assert headers["Vary"] == "Origin"


def test_status_grants_cors_to_no_one_else():
    """/status takes no token: "*" handed our ports to any site the user had open."""
    headers = _status_headers(origin="https://evil.com")

    assert "Access-Control-Allow-Origin" not in headers


def test_status_still_answers_a_caller_without_an_origin():
    """urllib and the launcher scripts never send Origin and never read one."""
    headers = _status_headers(origin=None)

    assert "Access-Control-Allow-Origin" not in headers


def test_port_file_publishes_the_token_with_the_ports(tmp_path, monkeypatch):
    """Without this the only holders of the token are the supervisor and the page.

    Launch Pine.bat is neither, so its /shutdown and /restart POSTs were
    answered 403 every time and a stale supervisor was never cleaned up.
    """
    monkeypatch.setattr(supervisor, "_DATA_DIR", tmp_path)
    monkeypatch.setattr(supervisor, "PORT_FILE_PATH", tmp_path / "supervisor.port")

    supervisor._write_port_file(5101, 5100)
    published = json.loads((tmp_path / "supervisor.port").read_text(encoding="utf-8"))

    assert published == {
        "supervisor_port": 5101,
        "backend_port": 5100,
        "token": SUPERVISOR_TOKEN,
    }


def test_port_file_is_not_world_readable(tmp_path, monkeypatch):
    """It holds a credential now. Windows ignores the mode; POSIX must not."""
    monkeypatch.setattr(supervisor, "_DATA_DIR", tmp_path)
    monkeypatch.setattr(supervisor, "PORT_FILE_PATH", tmp_path / "supervisor.port")

    supervisor._write_port_file(5101, 5100)

    if os.name != "nt":
        mode = (tmp_path / "supervisor.port").stat().st_mode
        assert mode & 0o077 == 0


def test_rewriting_the_port_file_narrows_a_permissive_leftover(tmp_path, monkeypatch):
    """O_CREAT's mode applies only to a file it creates."""
    port_file = tmp_path / "supervisor.port"
    port_file.write_text("{}", encoding="utf-8")
    port_file.chmod(0o644)
    monkeypatch.setattr(supervisor, "_DATA_DIR", tmp_path)
    monkeypatch.setattr(supervisor, "PORT_FILE_PATH", port_file)

    supervisor._write_port_file(5101, 5100)

    if os.name != "nt":
        assert port_file.stat().st_mode & 0o077 == 0


def test_the_port_file_goes_away_with_the_supervisor(tmp_path, monkeypatch):
    """A credential must not outlive the process it authenticates."""
    monkeypatch.setattr(supervisor, "_DATA_DIR", tmp_path)
    monkeypatch.setattr(supervisor, "PORT_FILE_PATH", tmp_path / "supervisor.port")
    supervisor._write_port_file(5101, 5100)

    supervisor._remove_port_file(5101)

    assert not (tmp_path / "supervisor.port").exists()


def test_a_leaving_supervisor_keeps_its_successors_port_file(tmp_path, monkeypatch):
    """Two supervisors overlap when the second finds the first one's port busy.

    It moves to the next port and rewrites both files with its own details. The
    first one leaving must not take them with it: reset_win.bat kills by the pid
    in there and both launchers resolve the ports from it, so the live
    supervisor would go unreachable.
    """
    monkeypatch.setattr(supervisor, "_DATA_DIR", tmp_path)
    monkeypatch.setattr(supervisor, "PORT_FILE_PATH", tmp_path / "supervisor.port")

    supervisor._write_port_file(5101, 5100)      # the first supervisor
    supervisor._write_port_file(5102, 5103)      # the second one takes over

    supervisor._remove_port_file(5101)           # ...and then the first exits

    published = json.loads((tmp_path / "supervisor.port").read_text(encoding="utf-8"))
    assert published["supervisor_port"] == 5102
    assert published["backend_port"] == 5103


def test_a_leaving_supervisor_keeps_its_successors_pid_file(tmp_path, monkeypatch):
    monkeypatch.setattr(supervisor, "_DATA_DIR", tmp_path)
    pid_file = tmp_path / "supervisor.pid"
    monkeypatch.setattr(supervisor, "PID_FILE_PATH", pid_file)
    pid_file.write_text(str(os.getpid() + 1), encoding="utf-8")

    supervisor._remove_pid_file()

    assert pid_file.read_text(encoding="utf-8") == str(os.getpid() + 1)


def test_a_supervisor_still_removes_its_own_pid_file(tmp_path, monkeypatch):
    monkeypatch.setattr(supervisor, "_DATA_DIR", tmp_path)
    monkeypatch.setattr(supervisor, "PID_FILE_PATH", tmp_path / "supervisor.pid")

    supervisor._write_pid_file()
    supervisor._remove_pid_file()

    assert not (tmp_path / "supervisor.pid").exists()


def test_an_unreadable_port_file_is_still_cleaned_up(tmp_path, monkeypatch):
    """Garbage in there names no owner, so the credential goes."""
    monkeypatch.setattr(supervisor, "_DATA_DIR", tmp_path)
    port_file = tmp_path / "supervisor.port"
    monkeypatch.setattr(supervisor, "PORT_FILE_PATH", port_file)
    port_file.write_text("not json at all", encoding="utf-8")

    supervisor._remove_port_file(5101)

    assert not port_file.exists()


# ── the other half of the busy probe: what /api/health actually reports ──

def test_health_reports_the_running_job_to_the_supervisor(client):
    from app.services import transcription

    before = client.get("/api/health").get_json()
    assert before["ok"] is True
    assert before["busy"] is False
    assert before["recording_id"] is None

    transcription._current_recording_id = 7
    try:
        during = client.get("/api/health").get_json()
    finally:
        transcription._current_recording_id = None

    assert during["busy"] is True
    assert during["recording_id"] == 7


def test_health_counts_a_queued_recording_as_busy(client):
    """The queue is work too — shutting down here loses the jobs waiting in it."""
    from app.services import transcription

    with transcription._queue_lock:
        transcription._queued_ids.append(11)
    try:
        payload = client.get("/api/health").get_json()
    finally:
        with transcription._queue_lock:
            transcription._queued_ids.remove(11)

    assert payload["busy"] is True
    assert payload["queued"] == 1


def test_health_still_answers_when_the_queue_cannot_be_read(client, monkeypatch):
    """A 500 here reads as a dead backend, and the supervisor kills it."""
    from app.services import transcription

    def _boom():
        raise RuntimeError("queue is on fire")

    monkeypatch.setattr(transcription, "busy_snapshot", _boom)

    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.get_json()["ok"] is True
    assert response.get_json()["busy"] is False


def test_mac_port_probe_skips_a_port_something_already_answers_on(monkeypatch):
    """The AirPlay Receiver holds *:5000 on macOS; a bind probe cannot see it."""
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    taken = listener.getsockname()[1]
    try:
        monkeypatch.setattr(supervisor.sys, "platform", "darwin")
        assert supervisor._find_free_port(taken) != taken
    finally:
        listener.close()


def test_port_answers_false_when_nothing_listens():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        idle = probe.getsockname()[1]
    assert supervisor._port_answers("127.0.0.1", idle) is False
