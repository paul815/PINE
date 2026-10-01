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
from typing import IO
from urllib.parse import urlsplit

SUPERVISOR_TOKEN = os.environ.get("PINE_SUPERVISOR_TOKEN") or secrets.token_urlsafe(32)


HOST = "127.0.0.1"
# The supervisor assigns both ports and passes them to the backend it spawns as
# PINE_SUPERVISOR_PORT / PINE_BACKEND_PORT. These two literals are deliberately
# not imported from app.ports: this process must start before (and independently
# of) the Flask stack, including while onboarding is still pip-installing it.
# app.ports.DEFAULT_* mirrors them for the backend side — change both together.
PORT = 5001
BACKEND_PORT = 5000
BACKEND_SCRIPT = "run.py"
BACKEND_HEALTH_PATH = "/api/health"
# How long a lease survives without a heartbeat. The page renews every 5s from a
# Worker (static/js/lease.js), so this is twelve missed beats — deliberately far
# more than a page under load could lose by accident.
#
# It was 10s against a 3s heartbeat, which sounds like the same ratio and was
# not: the heartbeat then came from setInterval, and a browser throttles a
# background tab's timers to about once a minute. Three beats were skipped by
# design, the lease expired, and a tab that was open the whole time was read as
# closed. The Worker timer is what makes a tight timeout safe; this number is
# what makes it unnecessary.
LEASE_TIMEOUT_SECONDS = 60.0
STARTUP_LEASE_GRACE_SECONDS = 45.0
LEASE_RELEASE_SHUTDOWN_GRACE_SECONDS = 2.0
# A lease that expired instead of being released means nobody said goodbye: the
# tab crashed, the browser froze it outright, or the machine slept with PINE
# open. Timing out is the *guess*, and this is how long the guess is given to be
# wrong before anything is stopped. A page that closes properly does not come
# here — it releases, and the two-second grace above applies.
EXPIRED_LEASE_SHUTDOWN_GRACE_SECONDS = float(
    os.environ.get("PINE_EXPIRED_LEASE_SHUTDOWN_GRACE_SECONDS") or (5.0 * 60.0)
)
# How long to hold off after finding the backend mid-transcription. The grace
# above is a fixed budget; a recording is not, and an hour of audio outlasts
# five minutes of it several times over. So the wait is re-armed for as long as
# the work lasts, one probe per interval rather than one per second.
BUSY_BACKEND_RECHECK_SECONDS = 60.0
LOG = logging.getLogger(__name__)

_DATA_DIR = Path(os.environ.get("PINE_DATA_DIR") or (Path(__file__).resolve().parent / "data"))
PID_FILE_PATH = _DATA_DIR / "supervisor.pid"


def _write_pid_file() -> None:
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    PID_FILE_PATH.write_text(str(os.getpid()), encoding="utf-8")
    LOG.info("PID file written: %s (pid=%s)", PID_FILE_PATH, os.getpid())


def _remove_pid_file() -> None:
    """Drop the PID file, unless a later supervisor has taken it over.

    Two supervisors can overlap: the second one finds 5001 busy, moves to 5002
    and rewrites both files with its own details. When the first then exits it
    used to delete them anyway, leaving the live supervisor invisible — and
    these files are how reset_win.bat finds the process to kill and how both
    launchers resolve the ports to talk to.
    """
    owner = _read_stale_pid()
    if owner is not None and owner != os.getpid():
        LOG.info("PID file now belongs to pid=%s; leaving it in place", owner)
        return
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


def _port_answers(host: str, port: int) -> bool:
    """True when something already accepts connections on *host*:*port*.

    The bind probe below cannot see a wildcard listener on macOS: BSD lets an
    SO_REUSEADDR bind of 127.0.0.1 slip in beside ``*:port``. The AirPlay
    Receiver holds ``*:5000`` on macOS 12 and later, so PINE used to share the
    port with it, and whenever PINE was not listening -- the backend restart
    behind Launch PINE, a crash, ``localhost`` resolving to ::1 -- the browser
    got AirPlay's bare 403 instead of PINE.
    """
    try:
        with socket.create_connection((host, port), timeout=0.2):
            return True
    except OSError:
        return False


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
        if sys.platform == "darwin" and _port_answers(host, port):
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
    """Publish where this supervisor listens, and the token for talking to it.

    The token rides along because the alternative is that nobody outside this
    process can use the control API at all. It is minted here (or handed in by
    whoever spawned us) and otherwise reaches only the backend's environment
    and the page it renders — so Launch Pine.bat, which is neither, could not
    authenticate a single POST. Its /shutdown and /restart calls were answered
    403 every time, which is why a stale supervisor never got cleaned up.

    Writing a secret to disk costs nothing here, because the file is not what
    the token defends against. It guards the control API from *web pages*: a
    page the user has open can POST to 127.0.0.1 but cannot read a file. Local
    processes could always kill this one outright, DB and transcripts included.
    data/secret_key has held a long-lived secret on the same terms since the
    first run. 0600 all the same, and _remove_port_file drops it on shutdown.
    """
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({
        "supervisor_port": supervisor_port,
        "backend_port": backend_port,
        "token": SUPERVISOR_TOKEN,
    })
    fd = os.open(PORT_FILE_PATH, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
    try:
        os.write(fd, payload.encode("utf-8"))
    finally:
        os.close(fd)
    try:
        # O_CREAT's mode only applies to a file it creates; one left behind by
        # an earlier run keeps whatever mode it already had.
        os.chmod(PORT_FILE_PATH, 0o600)
    except OSError:
        pass
    LOG.info("Port file written: %s", PORT_FILE_PATH)


def _port_file_supervisor_port() -> int | None:
    """Which supervisor the port file currently points at, if it can be read."""
    try:
        payload = json.loads(PORT_FILE_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError, OSError):
        return None
    port = payload.get("supervisor_port") if isinstance(payload, dict) else None
    return port if isinstance(port, int) else None


def _remove_port_file(supervisor_port: int | None = None) -> None:
    """Drop the port file, unless a later supervisor has taken it over.

    Same ownership question as the PID file above, answered with the port
    rather than the pid: the token in there can be inherited from the
    environment by both processes, the listening port cannot.
    """
    if supervisor_port is not None:
        owner = _port_file_supervisor_port()
        if owner is not None and owner != supervisor_port:
            LOG.info("Port file now points at port %s; leaving it in place", owner)
            return
    try:
        PORT_FILE_PATH.unlink(missing_ok=True)
    except OSError:
        pass


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
        folder_date = None
        # "%Y%m%d" is the spelling install logs used to land in. Nothing writes
        # it any more, but the folders it left behind are still on disk and were
        # skipped by every sweep until now.
        for pattern in ("%Y-%m-%d", "%Y%m%d"):
            try:
                folder_date = datetime.strptime(entry.name, pattern).date()
                break
            except ValueError:
                continue
        if folder_date is None:
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
        self._proc: subprocess.Popen | None = None
        self._backend_log_handle: IO[str] | None = None
        self._shutdown_requested = False
        self._shutdown_reason_logged = False
        self._stop_server_event: threading.Event | None = None
        self._backend_ready_event = threading.Event()
        self._leases: dict[str, float] = {}
        self._lease_grace_until = time.time() + STARTUP_LEASE_GRACE_SECONDS
        # Floor under the grace while a freshly spawned backend finds its page
        # again; see release_lease.
        self._restart_grace_until = 0.0
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
        self._restart_grace_until = time.time() + STARTUP_LEASE_GRACE_SECONDS
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
                # Two seconds is right for a tab the user closed: the page also
                # posts /api/quit and the supervisor should not outlive it. It
                # is wrong for the page unloading because the backend under it
                # restarted — that release lands on a live supervisor while the
                # quit dies with the old backend, and the reloading page finds
                # nothing left to reconnect to. Inside the startup window the
                # restart's own grace wins.
                self._lease_grace_until = max(
                    now + LEASE_RELEASE_SHUTDOWN_GRACE_SECONDS,
                    self._restart_grace_until,
                )
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
            grace_elapsed = False
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
                        grace_elapsed = True
            if not grace_elapsed:
                continue

            # Outside the lock on purpose: the probe blocks for up to a second,
            # and holding the lock through it would stall the heartbeat route —
            # expiring the very lease that would have called this off.
            if self._backend_busy():
                with self._lock:
                    self._lease_grace_until = max(
                        self._lease_grace_until,
                        time.time() + BUSY_BACKEND_RECHECK_SECONDS,
                    )
                LOG.info(
                    "No browser leases, but the backend is still working; "
                    "holding for another %.0fs",
                    BUSY_BACKEND_RECHECK_SECONDS,
                )
                continue

            should_shutdown = False
            with self._lock:
                if self._shutdown_requested:
                    return
                # A tab can come back while the probe above is in flight.
                if not self._leases and time.time() >= self._lease_grace_until:
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

    def _probe_backend_health(self) -> dict | None:
        """Health payload, or None when the backend did not answer."""
        try:
            with urllib.request.urlopen(
                f'http://{HOST}:{self._backend_port}{BACKEND_HEALTH_PATH}',
                timeout=0.8,
            ) as response:
                body = response.read(4096)
        except Exception:
            return None
        try:
            payload = json.loads(body.decode("utf-8"))
        except Exception:
            return {}
        return payload if isinstance(payload, dict) else {}

    def _probe_backend_http(self) -> bool:
        return self._probe_backend_health() is not None

    def _backend_busy(self) -> bool:
        """True while the backend reports a transcription queued or running.

        A backend that cannot be reached is not busy — it is gone, and the
        shutdown it was blocking should go ahead.
        """
        payload = self._probe_backend_health()
        return bool(payload and payload.get("busy"))

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

        def _cors_origin(self) -> str | None:
            """The caller's origin, when it is one this machine could serve.

            The UI is served on one port and calls the supervisor on another,
            so its replies genuinely need a CORS header. "*" was too generous
            for it: /status takes no token, so any site the user had open could
            read our ports and lease count. A loopback origin is either this
            app or something already running on the user's own machine.
            """
            origin = self.headers.get("Origin", "")
            if not origin:
                return None
            hostname = (urlsplit(origin).hostname or "").lower()
            if hostname in ("127.0.0.1", "localhost", "::1") or hostname.endswith(".localhost"):
                return origin
            return None

        def _set_headers(self, code: int = 200) -> None:
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            origin = self._cors_origin()
            if origin is not None:
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Pine-Supervisor-Token")
            self.end_headers()

        def _check_token(self, body: dict) -> bool:
            """Validate the supervisor token, from the header or from the body.

            The header is the normal path. It cannot be the only one: a page
            being closed sends its release with ``keepalive``, which has to go
            out in no-cors mode to survive the unload, and no-cors silently
            drops every header that is not CORS-safelisted -- the token with
            them. Those requests arrived here unauthenticated and were refused,
            invisibly, because an opaque response has no status to read. So the
            release never landed and the lease sat there until it timed out.

            The body is the same secret in a different envelope: a page that
            does not know the token still cannot produce one.
            """
            token = self.headers.get("X-Pine-Supervisor-Token", "")
            if token != SUPERVISOR_TOKEN:
                token = body.get("token") if isinstance(body.get("token"), str) else ""
            if not secrets.compare_digest(token, SUPERVISOR_TOKEN):
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
            # Read once: the body may carry the token, and rfile gives it up
            # only the first time.
            body = self._read_json()
            if not self._check_token(body):
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
                lease_id = str(body.get("lease_id", "")).strip()
                if not lease_id:
                    self._write_json({"ok": False, "error": "lease_id required"}, code=400)
                    return
                count = supervisor.heartbeat_lease(lease_id)
                self._write_json({"ok": True, "action": "lease_heartbeat", "lease_count": count})
                return
            if self.path == "/lease/release":
                lease_id = str(body.get("lease_id", "")).strip()
                if not lease_id:
                    self._write_json({"ok": False, "error": "lease_id required"}, code=400)
                    return
                count = supervisor.release_lease(lease_id)
                self._write_json({"ok": True, "action": "lease_release", "lease_count": count})
                return
            if self.path == "/shutdown":
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
        _remove_port_file(supervisor_port)


if __name__ == "__main__":
    raise SystemExit(main())
