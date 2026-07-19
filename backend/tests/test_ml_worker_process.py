"""Smoke tests for the ML worker subprocess boundary (no models required).

Spawns the real ``python -m ml_worker`` process and talks the JSON-lines
protocol: startup handshake, ping/pong, clean shutdown, and the error path
for a job that cannot possibly run. Model-quality tests live elsewhere.
"""

import os
import queue
import subprocess
import sys
import threading

import pytest

from ml_worker import protocol

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class WorkerHandle:
    """Test double of the backend side: spawn, send ops, read events with a timeout."""

    def __init__(self):
        self.proc = subprocess.Popen(
            [sys.executable, '-m', 'ml_worker'],
            cwd=BACKEND_DIR,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True, encoding='utf-8', errors='replace', bufsize=1,
        )
        self._events = queue.Queue()
        threading.Thread(target=self._pump, daemon=True).start()
        # Drain stderr like the real worker_client does — otherwise the 64 KB
        # pipe buffer fills up on chatty ML imports and deadlocks the worker.
        threading.Thread(target=self._drain_stderr, daemon=True).start()

    def _pump(self):
        for line in self.proc.stdout:
            msg = protocol.parse_line(line)
            if msg is not None:
                self._events.put(msg)
        self._events.put(None)  # EOF marker

    def _drain_stderr(self):
        try:
            for _ in self.proc.stderr:
                pass
        except Exception:
            pass

    def send(self, **op):
        self.proc.stdin.write(protocol.dumps_line(op))
        self.proc.stdin.flush()

    def next_event(self, timeout=60):
        try:
            return self._events.get(timeout=timeout)
        except queue.Empty:
            pytest.fail(f'No worker event within {timeout}s')

    def close(self):
        try:
            self.proc.kill()
        except Exception:
            pass


@pytest.fixture
def worker():
    w = WorkerHandle()
    yield w
    w.close()


class TestWorkerProtocol:
    def test_ready_ping_shutdown(self, worker):
        ready = worker.next_event(timeout=30)
        assert ready is not None and ready.get('ev') == 'ready'
        # NB: not compared to Popen.pid — in a Windows venv, python.exe is a
        # launcher and the real interpreter is its child process.
        assert isinstance(ready.get('pid'), int) and ready.get('pid') > 0

        worker.send(op='ping')
        assert worker.next_event(timeout=10).get('ev') == 'pong'

        worker.send(op='shutdown')
        worker.proc.wait(timeout=15)
        assert worker.proc.returncode == 0

    def test_bogus_job_reports_error_event(self, worker):
        """A job with nonexistent paths must produce an 'error' event, not a crash."""
        ready = worker.next_event(timeout=30)
        assert ready.get('ev') == 'ready'

        missing = os.path.join(BACKEND_DIR, 'no-such-dir')
        worker.send(op='job', env={
            'stt_model_id': 'whisperx-large-v3',
            # An existing directory that is not a model: ctranslate2 fails
            # fast and locally. (A nonexistent path would make faster-whisper
            # fall back to Hugging Face resolution — slow and networked.)
            'model_dir': os.path.join(BACKEND_DIR, 'tests'),
            'diarize_dir': os.path.join(missing, 'pyannote-diarization'),
            'pyannote_cache': os.path.join(missing, 'pyannote_cache'),
            'hf_token': '',
            'hf_offline': True,
        }, job={
            'recording_id': 42,
            'audio_path': os.path.join(missing, 'nope.wav'),
            'num_speakers': None,
            'forced_language': 'en',
            'confirm_language': False,
        })

        # Model loading may take a while to fail (imports torch/whisperx);
        # skip status events, wait for the terminal one.
        deadline_events = 200
        for _ in range(deadline_events):
            ev = worker.next_event(timeout=180)
            assert ev is not None, 'worker died instead of reporting an error event'
            if ev.get('ev') == 'error':
                assert ev.get('recording_id') == 42
                assert ev.get('message')
                break
            assert ev.get('ev') in ('status', 'pong'), f'unexpected event: {ev}'
        else:
            pytest.fail('No error event received for a bogus job')

        # Worker must survive a failed job and stay responsive.
        worker.send(op='ping')
        assert worker.next_event(timeout=10).get('ev') == 'pong'
