#!/usr/bin/env python3
"""PINE supervisor: keeps backend alive and exposes restart/shutdown controls."""

from __future__ import annotations

import json
import logging
import os
import secrets
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import IO, Optional

SUPERVISOR_TOKEN = os.environ.get("PINE_SUPERVISOR_TOKEN") or secrets.token_urlsafe(32)


HOST = "127.0.0.1"
PORT = 5001
BACKEND_PORT = 5000
BACKEND_SCRIPT = "run.py"
BACKEND_HEALTH_PATH = "/api/health"
LEASE_TIMEOUT_SECONDS = 10.0
STARTUP_LEASE_GRACE_SECONDS = 45.0
LEASE_RELEASE_SHUTDOWN_GRACE_SECONDS = 2.0
EXPIRED_LEASE_SHUTDOWN_GRACE_SECONDS = float(
    os.environ.get("PINE_EXPIRED_LEASE_SHUTDOWN_GRACE_SECONDS") or (5.0 * 60.0)
)
LOG = logging.getLogger(__name__)

_DATA_DIR = Path(os.environ.get("PINE_DATA_DIR") or (Path(__file__).resolve().parent / "data"))
PID_FILE_PATH = _DATA_DIR / "supervisor.pid"


def _write_pid_file() -> None:
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    PID_FILE_PATH.write_text(str(os.getpid()), encoding="utf-8")
    LOG.info("PID file written: %s (pid=%s)", PID_FILE_PATH, os.getpid())


def _remove_pid_file() -> None:
    try:
        PID_FILE_PATH.unlink(missing_ok=True)
        LOG.info("PID file removed: %s", PID_FILE_PATH)
    except OSError:
        pass


def _read_stale_pid() -> int | None:
    """Read PID from file.  Return PID if the file exists, else None."""
    try:
        return int(PID_FILE_PATH.read_text(encoding="utf-8").strip())
    except (FileNotFoundError, ValueError, OSError):
        return None


PORT_FILE_PATH = _DATA_DIR / "supervisor.port"


def _find_free_port(preferred: int, host: str = "127.0.0.1",
                    exclude: frozenset = frozenset()) -> int:
    """Return *preferred* if free, otherwise scan upward for the next available port.

    The probe socket is closed before returning, so a port this hands out is
    free again until whoever asked for it actually binds. Callers picking more
    than one port have to pass the earlier answers in *exclude*, or they will
    be handed the same port twice.
    """
    for port in range(preferred, preferred + 100):
        if port in exclude:
            continue
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                s.bind((host, port))
                return port
        except OSError:
            continue
    raise RuntimeError(f"No free port found in range {preferred}–{preferred + 99}")


def _write_port_file(supervisor_port: int, backend_port: int) -> None:
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    PORT_FILE_PATH.write_text(
        json.dumps({"supervisor_port": supervisor_port, "backend_port": backend_port}),
        encoding="utf-8",
    )
    LOG.info("Port file written: %s", PORT_FILE_PATH)


def _remove_port_file() -> None:
    try:
        PORT_FILE_PATH.unlink(missing_ok=True)
    except OSError:
        pass


def read_port_file() -> dict | None:
    """Read port file.  Returns dict with supervisor_port/backend_port or None."""
    try:
        return json.loads(PORT_FILE_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError, OSError):
        return None


def _default_log_dir() -> Path:
    return Path(os.environ.get("PINE_LOG_DIR") or (Path(__file__).resolve().parent / "logs"))


def _cleanup_old_log_folders(max_age_days: int = 7) -> None:
    """Remove daily log folders older than *max_age_days*."""
    import shutil
    from datetime import timedelta

    log_dir = _default_log_dir()
    if not log_dir.is_dir():
        return
    cutoff = datetime.now().date() - timedelta(days=max_age_days)
    for entry in log_dir.iterdir():
        if not entry.is_dir():
            continue
        try:
            folder_date = datetime.strptime(entry.name, "%Y-%m-%d").date()
        except ValueError:
            continue
        if folder_date < cutoff:
            shutil.rmtree(entry, ignore_errors=True)
            LOG.info("Removed old log folder: %s", entry)


def _configure_logging() -> Path:
    log_dir = _default_log_dir()
    today = datetime.now()
    daily_dir = log_dir / today.strftime("%Y-%m-%d")
    daily_dir.mkdir(parents=True, exist_ok=True)
    session_stamp = today.strftime("%H%M%S")
    log_path = daily_dir / f"supervisor-{session_stamp}-{os.getpid()}.log"

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    for handler in list(root.handlers):
        if getattr(handler, "_pine_managed", False):
            root.removeHandler(handler)
            try:
                handler.close()
            except Exception:
                pass

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)
    file_handler._pine_managed = True  # type: ignore[attr-defined]
    root.addHandler(file_handler)

    if sys.stderr is not None:
        # The console window is the user's, not ours: only warnings and errors
        # surface there. The full DEBUG trail still goes to the log file above.
        # Set PINE_VERBOSE=1 to stream everything to the console while debugging.
        verbose = os.environ.get("PINE_VERBOSE", "").strip().lower() in ("1", "true", "yes")
        stream_handler = logging.StreamHandler()
        stream_handler.setLevel(logging.DEBUG if verbose else logging.WARNING)
        stream_handler.setFormatter(formatter)
        stream_handler._pine_managed = True  # type: ignore[attr-defined]
        root.addHandler(stream_handler)
    LOG.debug("Supervisor log file initialised at %s", log_path)
    return log_path


class BackendSupervisor:
    def __init__(self, supervisor_port: int = PORT, backend_port: int = BACKEND_PORT) -> None:
        self._lock = threading.Lock()
        self._proc: Optional[subprocess.Popen] = None
        self._backend_log_handle: Optional[IO[str]] = None
        self._shutdown_requested = False
        self._shutdown_reason_logged = False
        self._stop_server_event: Optional[threading.Event] = None
        self._backend_ready_event = threading.Event()
        self._leases: dict[str, float] = {}
        self._lease_grace_until = time.time() + STARTUP_LEASE_GRACE_SECONDS
        self._supervisor_port = supervisor_port
        self._backend_port = backend_port
        self._cwd = str(Path(__file__).resolve().parent)
        exe = self._resolve_backend_python(Path(sys.executable))
        self._cmd = [str(exe), BACKEND_SCRIPT]
        LOG.info("BackendSupervisor initialised pid=%s cwd=%s cmd=%s ports=(%s,%s)",
                 os.getpid(), self._cwd, self._cmd, supervisor_port, backend_port)

    def bind_stop_server_event(self, stop_server_event: threading.Event) -> None:
        self._stop_server_event = stop_server_event

    @staticmethod
    def _resolve_backend_python(executable: Path) -> Path:
        """Prefer console Python for the backend when supervisor uses pythonw.exe."""
        if executable.name.lower() == "pythonw.exe":
            console_exe = executable.with_name("python.exe")
            if console_exe.exists():
                return console_exe
        return executable

    def backend_running(self) -> bool:
        with self._lock:
            owned_alive = self._proc is not None and self._proc.poll() is None
        return owned_alive or self._probe_backend_http()

    def backend_ready(self) -> bool:
        if self._backend_ready_event.is_set():
            return True
        return self._probe_backend_http()

    def signal_backend_ready(self) -> None:
        self._backend_ready_event.set()
        with self._lock:
            new_deadline = time.time() + STARTUP_LEASE_GRACE_SECONDS
            if new_deadline > self._lease_grace_until:
                self._lease_grace_until = new_deadline
                LOG.info("Backend signaled ready; lease grace reset to %.0fs from now",
                         STARTUP_LEASE_GRACE_SECONDS)
            else:
                LOG.info("Backend signaled ready")

    def _open_backend_log_handle(self) -> IO[str]:
        log_dir = _default_log_dir()
        today = datetime.now()
        daily_dir = log_dir / today.strftime("%Y-%m-%d")
        daily_dir.mkdir(parents=True, exist_ok=True)
        session_stamp = today.strftime("%H%M%S")
        log_path = daily_dir / f"backend-{session_stamp}-{os.getpid()}.log"
        self._backend_log_handle = open(log_path, "a", encoding="utf-8")
        LOG.info("Capturing backend stdout/stderr to %s", log_path)
        return self._backend_log_handle

    def _close_backend_log_handle(self) -> None:
        if self._backend_log_handle is None:
            return
        try:
            self._backend_log_handle.close()
        except Exception:
            pass
        self._backend_log_handle = None

    def _spawn_backend(self) -> None:
        kwargs = {"cwd": self._cwd}
        env = os.environ.copy()
        env["PINE_SUPERVISOR_TOKEN"] = SUPERVISOR_TOKEN
        env["PINE_SUPERVISOR_PORT"] = str(self._supervisor_port)
        env["PINE_BACKEND_PORT"] = str(self._backend_port)
        kwargs["env"] = env
        log_handle = self._open_backend_log_handle()
        kwargs["stdout"] = log_handle
        kwargs["stderr"] = subprocess.STDOUT
        if os.name == "nt":
            flags = subprocess.CREATE_NEW_PROCESS_GROUP
            create_no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            flags |= create_no_window
            kwargs["creationflags"] = flags
        else:
            kwargs["start_new_session"] = True
            kwargs["close_fds"] = True
        self._proc = subprocess.Popen(self._cmd, **kwargs)
        LOG.info("Spawned backend pid=%s", getattr(self._proc, "pid", None))

    def restart_backend(self) -> None:
        with self._lock:
            self._backend_ready_event.clear()
            LOG.info("Restarting backend")
            if self._proc is not None and self._proc.poll() is not None:
                self._proc = None
                self._close_backend_log_handle()
            if self._proc is not None and self._proc.poll() is None:
                self._terminate_locked(timeout=8.0)
            if self._proc is None and self._probe_backend_http():
                self._request_unmanaged_backend_stop_locked(timeout=6.0)
            if not self._shutdown_requested:
                self._spawn_backend()

    @staticmethod
    def _normalize_shutdown_context(
        reason: str | None = None,
        source: str | None = None,
        lease_id: str | None = None,
    ) -> tuple[str, str, str]:
        reason_value = str(reason or "").strip() or "unknown"
        source_value = str(source or "").strip() or "unspecified"
        lease_value = str(lease_id or "").strip()
        return reason_value, source_value, lease_value

    def request_shutdown(
        self,
        *,
        reason: str | None = None,
        source: str | None = None,
        lease_id: str | None = None,
        trigger: str | None = None,
    ) -> None:
        reason_value, source_value, lease_value = self._normalize_shutdown_context(
            reason=reason,
            source=source,
            lease_id=lease_id,
        )
        with self._lock:
            self._shutdown_requested = True
            if not self._shutdown_reason_logged:
                LOG.info(
                    "Supervisor shutdown requested reason=%s source=%s lease_id=%s trigger=%s",
                    reason_value,
                    source_value,
                    lease_value or "-",
                    str(trigger or "").strip() or "unspecified",
                )
                self._shutdown_reason_logged = True
            self._terminate_locked(timeout=8.0)

    def heartbeat_lease(self, lease_id: str) -> int:
        now = time.time()
        with self._lock:
            self._leases[lease_id] = now
            self._lease_grace_until = now + LEASE_TIMEOUT_SECONDS
            return len(self._leases)

    def release_lease(self, lease_id: str) -> int:
        now = time.time()
        with self._lock:
            self._leases.pop(lease_id, None)
            count = len(self._leases)
            if count == 0:
                self._lease_grace_until = now + LEASE_RELEASE_SHUTDOWN_GRACE_SECONDS
            return count

    def lease_count(self) -> int:
        now = time.time()
        with self._lock:
            stale = [k for k, v in self._leases.items() if now - v > LEASE_TIMEOUT_SECONDS]
            for key in stale:
                self._leases.pop(key, None)
            return len(self._leases)

    def monitor_loop(self) -> None:
        while True:
            time.sleep(1.0)
            should_shutdown = False
            with self._lock:
                if self._shutdown_requested:
                    return
                now = time.time()
                had_leases = bool(self._leases)
                stale = [k for k, v in self._leases.items() if now - v > LEASE_TIMEOUT_SECONDS]
                for key in stale:
                    self._leases.pop(key, None)
                if had_leases and stale and not self._leases:
                    self._lease_grace_until = max(
                        self._lease_grace_until,
                        now + EXPIRED_LEASE_SHUTDOWN_GRACE_SECONDS,
                    )
                    LOG.info(
                        "All browser leases expired without explicit release; "
                        "keeping supervisor alive for %.1f seconds",
                        EXPIRED_LEASE_SHUTDOWN_GRACE_SECONDS,
                    )
                if not self._leases and now >= self._lease_grace_until:
                    backend_starting = (
                        not self._backend_ready_event.is_set()
                        and self._proc is not None
                        and self._proc.poll() is None
                    )
                    if backend_starting:
                        self._lease_grace_until = now + STARTUP_LEASE_GRACE_SECONDS
                        LOG.info(
                            "Backend process alive but not yet ready; "
                            "extending lease grace by %.0fs",
                            STARTUP_LEASE_GRACE_SECONDS,
                        )
                    else:
                        should_shutdown = True
                        self._shutdown_requested = True
                        LOG.info("No active browser leases remain; shutting down supervisor")
            if should_shutdown:
                stop_server_event = self._stop_server_event
                LOG.info("No active browser leases remain; stopping supervisor server")
                if stop_server_event is not None:
                    stop_server_event.set()
                self.request_shutdown(
                    reason="no_active_leases_grace_elapsed",
                    source="supervisor_monitor",
                    trigger="lease_monitor",
                )
                return

    def _probe_backend_http(self) -> bool:
        try:
            urllib.request.urlopen(
                f'http://{HOST}:{self._backend_port}{BACKEND_HEALTH_PATH}',
                timeout=0.8,
            )
            return True
        except Exception:
            return False

    def _terminate_locked(self, timeout: float) -> None:
        if self._proc is None or self._proc.poll() is not None:
            if self._proc is not None and self._proc.poll() is not None:
                self._close_backend_log_handle()
            return
        proc = self._proc
        try:
            if os.name == "nt":
                proc.send_signal(signal.CTRL_BREAK_EVENT)
            else:
                proc.terminate()
        except Exception:
            pass
        try:
            proc.wait(timeout=timeout)
        except Exception:
            try:
                proc.kill()
                proc.wait(timeout=2)
            except Exception:
                pass
        self._close_backend_log_handle()
        LOG.info("Backend process terminated pid=%s", getattr(proc, "pid", None))

    def _request_unmanaged_backend_stop_locked(self, timeout: float) -> None:
        LOG.info("Requesting unmanaged backend shutdown")
        try:
            req = urllib.request.Request(
                f'http://{HOST}:{self._backend_port}/api/internal/quit-backend',
                method='POST',
            )
            urllib.request.urlopen(req, timeout=1.5)
        except Exception:
            return
        deadline = time.time() + timeout
        while time.time() < deadline:
            if not self._probe_backend_http():
                return
            time.sleep(0.15)

    def _stop_backend_only(self) -> None:
        with self._lock:
            managed_alive = self._proc is not None and self._proc.poll() is None
            if managed_alive:
                self._terminate_locked(timeout=6.0)
                return
        try:
            req = urllib.request.Request(
                f'http://{HOST}:{self._backend_port}/api/internal/quit-backend',
                method='POST',
            )
            urllib.request.urlopen(req, timeout=1.5)
        except Exception:
            pass


def make_supervisor_handler(
    supervisor: BackendSupervisor,
    stop_server: threading.Event,
) -> type[BaseHTTPRequestHandler]:
    """Create a request handler class bound to a specific supervisor instance."""

    class SupervisorHTTPHandler(BaseHTTPRequestHandler):

        def _set_headers(self, code: int = 200) -> None:
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Pine-Supervisor-Token")
            self.end_headers()

        def _check_token(self) -> bool:
            """Validate supervisor auth token. Returns True if valid."""
            token = self.headers.get("X-Pine-Supervisor-Token", "")
            if token != SUPERVISOR_TOKEN:
                self._write_json({"ok": False, "error": "unauthorized"}, code=403)
                return False
            return True

        def _write_json(self, payload: dict, code: int = 200) -> None:
            self._set_headers(code=code)
            self.wfile.write(json.dumps(payload).encode("utf-8"))

        def _read_json(self) -> dict:
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except Exception:
                length = 0
            if length <= 0:
                return {}
            try:
                raw = self.rfile.read(length)
                parsed = json.loads(raw.decode("utf-8"))
                return parsed if isinstance(parsed, dict) else {}
            except Exception:
                return {}

        def _shutdown_context_from_body(self, body: dict) -> tuple[str, str, str]:
            return BackendSupervisor._normalize_shutdown_context(
                reason=body.get("reason"),
                source=body.get("source"),
                lease_id=body.get("lease_id"),
            )

        def do_OPTIONS(self) -> None:  # noqa: N802
            self._set_headers(code=204)

        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/status":
                backend_ready = supervisor.backend_ready()
                self._write_json(
                    {
                        "ok": True,
                        "supervisor_running": True,
                        "backend_running": supervisor.backend_running(),
                        "backend_ready": backend_ready,
                        "backend_port": supervisor._backend_port,
                        "supervisor_port": supervisor._supervisor_port,
                        "lease_count": supervisor.lease_count(),
                    }
                )
                return
            self._write_json({"ok": False, "error": "not found"}, code=404)

        def do_POST(self) -> None:  # noqa: N802
            if not self._check_token():
                return
            if self.path == "/backend-ready":
                supervisor.signal_backend_ready()
                self._write_json({"ok": True, "action": "backend_ready"})
                return
            if self.path == "/restart":
                supervisor.restart_backend()
                self._write_json({"ok": True, "action": "restart"})
                return
            if self.path == "/lease/heartbeat":
                body = self._read_json()
                lease_id = str(body.get("lease_id", "")).strip()
                if not lease_id:
                    self._write_json({"ok": False, "error": "lease_id required"}, code=400)
                    return
                count = supervisor.heartbeat_lease(lease_id)
                self._write_json({"ok": True, "action": "lease_heartbeat", "lease_count": count})
                return
            if self.path == "/lease/release":
                body = self._read_json()
                lease_id = str(body.get("lease_id", "")).strip()
                if not lease_id:
                    self._write_json({"ok": False, "error": "lease_id required"}, code=400)
                    return
                count = supervisor.release_lease(lease_id)
                self._write_json({"ok": True, "action": "lease_release", "lease_count": count})
                return
            if self.path == "/shutdown":
                body = self._read_json()
                reason, source, lease_id = self._shutdown_context_from_body(body)
                LOG.info(
                    "Shutdown HTTP request received reason=%s source=%s lease_id=%s",
                    reason,
                    source,
                    lease_id or "-",
                )
                self._write_json({"ok": True, "action": "shutdown"})
                stop_server.set()
                threading.Thread(
                    target=supervisor.request_shutdown,
                    kwargs={
                        "reason": reason,
                        "source": source,
                        "lease_id": lease_id,
                        "trigger": "http_shutdown_endpoint",
                    },
                    daemon=True,
                ).start()
                return
            self._write_json({"ok": False, "error": "not found"}, code=404)

        def log_message(self, _fmt: str, *_args) -> None:
            return

    return SupervisorHTTPHandler


def main() -> int:
    log_path = _configure_logging()
    _cleanup_old_log_folders()

    supervisor_port = _find_free_port(PORT)
    backend_port = _find_free_port(BACKEND_PORT, exclude=frozenset({supervisor_port}))
    if supervisor_port != PORT:
        LOG.info("Default supervisor port %s busy; using %s", PORT, supervisor_port)
    if backend_port != BACKEND_PORT:
        LOG.info("Default backend port %s busy; using %s", BACKEND_PORT, backend_port)

    LOG.info("Starting supervisor pid=%s log_path=%s sup_port=%s be_port=%s",
             os.getpid(), log_path, supervisor_port, backend_port)

    _write_pid_file()
    _write_port_file(supervisor_port, backend_port)
    try:
        supervisor = BackendSupervisor(
            supervisor_port=supervisor_port,
            backend_port=backend_port,
        )

        stop_server = threading.Event()
        supervisor.bind_stop_server_event(stop_server)
        handler = make_supervisor_handler(supervisor, stop_server)

        try:
            server = ThreadingHTTPServer((HOST, supervisor_port), handler)
        except OSError:
            LOG.info("Supervisor already running on port %s; exiting", supervisor_port)
            return 0

        monitor = threading.Thread(target=supervisor.monitor_loop, daemon=True)
        monitor.start()

        serve = threading.Thread(target=server.serve_forever, daemon=True)
        serve.start()

        if not supervisor.backend_running():
            LOG.info("Backend not detected during supervisor startup; starting it now")
            supervisor.restart_backend()

        while not stop_server.is_set():
            time.sleep(0.2)

        server.shutdown()
        server.server_close()
        supervisor.request_shutdown()
        LOG.info("Supervisor stopped cleanly")
        return 0
    finally:
        _remove_pid_file()
        _remove_port_file()


if __name__ == "__main__":
    raise SystemExit(main())
