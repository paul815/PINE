"""ML worker subprocess: lifecycle and JSON-lines RPC.

The worker (``python -m ml_worker``) hosts the models and the pipeline.
This module spawns it lazily on the first job, keeps it alive between jobs
(models stay loaded), and kills it when the watchdog detects a hang. If the
backend dies, the worker notices stdin EOF and exits on its own.
"""

import atexit
import logging
import os
import subprocess
import sys
import threading

from ml_worker import protocol

log = logging.getLogger(__name__)

_client = None
_client_lock = threading.RLock()

_CREATE_NO_WINDOW = 0x08000000  # Windows: no flashing console for the child


class WorkerProcessError(RuntimeError):
    """The ML worker process is unavailable or died unexpectedly."""


def _backend_dir():
    # .../backend/app/services/transcription/worker_client.py → .../backend
    return os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))))


class MLWorkerClient:
    """One ML worker subprocess. Send ops from any thread; one reader at a time."""

    def __init__(self):
        self._proc = None
        self._send_lock = threading.Lock()

    def alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def exit_code(self):
        return self._proc.poll() if self._proc is not None else None

    @property
    def pid(self):
        return self._proc.pid if self._proc is not None else None

    def ensure_started(self):
        if self.alive():
            return
        # The worker shells out to bare 'ffmpeg', so it has to be on PATH before
        # the env below is snapshotted -- add_paths() edits this process's PATH,
        # and the child only ever sees the copy. Normally a no-op: the model
        # download already fetched it. It is not a no-op when onboarding was
        # skipped or the venv was rebuilt, which is exactly why it is here and
        # not assumed to have happened.
        from ..ffmpeg_setup import ensure_ffmpeg
        ensure_ffmpeg()
        env = os.environ.copy()
        env.setdefault('PYTHONUNBUFFERED', '1')
        env.setdefault('PYTHONIOENCODING', 'utf-8')
        kwargs = {}
        if os.name == 'nt':
            kwargs['creationflags'] = _CREATE_NO_WINDOW
        self._proc = subprocess.Popen(
            [sys.executable, '-m', 'ml_worker'],
            cwd=_backend_dir(),
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True, encoding='utf-8', errors='replace', bufsize=1,
            **kwargs,
        )
        threading.Thread(target=self._drain_stderr, args=(self._proc,),
                         daemon=True, name='ml-worker-stderr').start()
        first = self.read_event()
        if not first or first.get('ev') != 'ready':
            code = self.exit_code()
            self.kill()
            raise WorkerProcessError(
                f'ML worker failed to start (no ready event, exit code {code}). '
                'See backend logs for [ml-worker] lines.')
        log.info('ML worker started (pid=%s, version=%s)',
                 self._proc.pid, first.get('version'))

    @staticmethod
    def _drain_stderr(proc):
        """Forward the worker's stderr (its log) into the backend log."""
        try:
            for line in proc.stderr:
                line = line.rstrip()
                if line:
                    log.info('[ml-worker] %s', line)
        except Exception:
            pass

    def send(self, obj):
        """Write one op; raises WorkerProcessError when the worker is gone."""
        with self._send_lock:
            if not self.alive():
                raise WorkerProcessError('ML worker is not running')
            try:
                self._proc.stdin.write(protocol.dumps_line(obj))
                self._proc.stdin.flush()
            except OSError as exc:
                raise WorkerProcessError(f'Failed to write to ML worker: {exc}') from exc

    def read_event(self):
        """Blocking read of the next event dict; None on EOF (worker exited)."""
        proc = self._proc
        if proc is None or proc.stdout is None:
            return None
        while True:
            try:
                line = proc.stdout.readline()
            except OSError:
                return None
            if line == '':
                return None
            msg = protocol.parse_line(line)
            if msg is not None:
                return msg

    def kill(self):
        proc = self._proc
        self._proc = None
        if proc is None:
            return
        # Closing stdin makes the worker's main thread see EOF and exit on its
        # own — this works even while a job thread is stuck in a GPU op.
        try:
            if proc.stdin:
                proc.stdin.close()
        except Exception:
            pass
        try:
            if proc.poll() is None:
                if os.name == 'nt':
                    # In a Windows venv, Popen.pid is the venv launcher; the
                    # real python is its child. /T kills the whole tree.
                    subprocess.run(
                        ['taskkill', '/PID', str(proc.pid), '/T', '/F'],
                        capture_output=True)
                else:
                    proc.kill()
                proc.wait(timeout=10)
                log.info('ML worker killed (pid=%s)', proc.pid)
        except Exception as exc:
            log.warning('Failed to kill ML worker: %s', exc)

    def shutdown(self):
        """Polite stop: shutdown op, short wait, then kill."""
        proc = self._proc
        if proc is None:
            return
        try:
            self.send({'op': 'shutdown'})
            proc.wait(timeout=5)
            self._proc = None
        except Exception:
            self.kill()


# ── module-level singleton ──

def get_client() -> MLWorkerClient:
    """Return the shared client with a running worker (spawn if needed)."""
    global _client
    with _client_lock:
        if _client is None:
            _client = MLWorkerClient()
        _client.ensure_started()
        return _client


def kill_worker():
    """Hard-kill the worker process (watchdog path). Next job respawns it."""
    with _client_lock:
        if _client is not None:
            _client.kill()


def notify_cancel(recording_id):
    """Best-effort cancel forwarding; never raises."""
    with _client_lock:
        client = _client
    if client is None or not client.alive():
        return
    try:
        client.send({'op': 'cancel', 'recording_id': recording_id})
    except Exception:
        pass


def _shutdown_at_exit():
    with _client_lock:
        if _client is not None:
            _client.shutdown()


atexit.register(_shutdown_at_exit)
