"""A job must not start on a file that is still arriving.

Recordings reach the queue from more places than a finished upload:
requeue_interrupted re-queues whatever a crash left behind, linked material
lives wherever the user put it, and project folders are folders people copy
into. On 2026-08-26 a backend died mid-upload, the restart re-queued the stump
it left, and the user got a raw ffmpeg CalledProcessError. Two halves here: the
upload never publishes a partial file under the real name, and the job refuses
to start on one — with a sentence that says which of missing/empty/still-being
-written it is.
"""

import os
import threading
import time

import pytest

from app.services import file_utils
from app.services.transcription import job_runner


class _Upload:
    """The part of werkzeug's FileStorage these call sites use."""

    def __init__(self, payload, fail_after=None):
        self._payload = payload
        self._fail_after = fail_after

    def save(self, path):
        with open(path, 'wb') as handle:
            if self._fail_after is None:
                handle.write(self._payload)
                return
            handle.write(self._payload[:self._fail_after])
            handle.flush()
            raise ConnectionResetError('client went away')


def _job(path, tracks=()):
    return {'audio_path': str(path),
            'tracks': [{'path': str(p)} for p in tracks]}


def _settled(tmp_path, name='rec.m4a', payload=b'x' * 2048):
    path = tmp_path / name
    path.write_bytes(payload)
    old = time.time() - 60
    os.utime(path, (old, old))
    return path


# ── the upload half ──

def test_a_finished_upload_lands_under_its_real_name(tmp_path):
    dest = tmp_path / 'interview.mp3'
    file_utils.save_upload_atomically(_Upload(b'audio bytes'), str(dest))

    assert dest.read_bytes() == b'audio bytes'
    assert not list(tmp_path.glob('*.part'))


def test_an_interrupted_upload_never_appears_under_the_real_name(tmp_path):
    dest = tmp_path / 'interview.mp3'

    with pytest.raises(ConnectionResetError):
        file_utils.save_upload_atomically(_Upload(b'audio bytes', fail_after=4),
                                          str(dest))

    assert not dest.exists()
    assert not list(tmp_path.glob('*.part'))


def test_the_upload_replaces_the_name_claim_left_behind(tmp_path):
    # claim_free_path reserves the name with an empty file; the upload has to
    # land on top of it rather than beside it.
    dest, name = file_utils.claim_free_path(str(tmp_path), 'interview.mp3')
    file_utils.save_upload_atomically(_Upload(b'audio bytes'), dest)

    assert name == 'interview.mp3'
    assert open(dest, 'rb').read() == b'audio bytes'


# ── the job half ──

def test_a_complete_file_starts_immediately(tmp_path):
    started = time.monotonic()
    job_runner._wait_for_media(_job(_settled(tmp_path)))
    # No poll paid at all: the file has been quiet since before the job.
    assert time.monotonic() - started < job_runner.MEDIA_SETTLE_INTERVAL_SEC


def test_missing_media_is_named_as_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(job_runner, 'MEDIA_SETTLE_TIMEOUT_SEC', 0.05)
    monkeypatch.setattr(job_runner, 'MEDIA_SETTLE_INTERVAL_SEC', 0.01)

    with pytest.raises(RuntimeError, match='missing'):
        job_runner._wait_for_media(_job(tmp_path / 'gone.m4a'))


def test_an_empty_file_is_named_as_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(job_runner, 'MEDIA_SETTLE_TIMEOUT_SEC', 0.05)
    monkeypatch.setattr(job_runner, 'MEDIA_SETTLE_INTERVAL_SEC', 0.01)
    stump = tmp_path / 'rec.m4a'
    stump.touch()

    with pytest.raises(RuntimeError, match='empty'):
        job_runner._wait_for_media(_job(stump))


def test_a_file_still_being_written_is_named_as_such(tmp_path, monkeypatch):
    monkeypatch.setattr(job_runner, 'MEDIA_SETTLE_TIMEOUT_SEC', 0.3)
    monkeypatch.setattr(job_runner, 'MEDIA_SETTLE_INTERVAL_SEC', 0.01)
    growing = tmp_path / 'rec.m4a'
    growing.write_bytes(b'x')

    stop = threading.Event()

    def keep_writing():
        while not stop.is_set():
            with open(growing, 'ab') as handle:
                handle.write(b'x' * 64)
            time.sleep(0.005)

    writer = threading.Thread(target=keep_writing, daemon=True)
    writer.start()
    try:
        with pytest.raises(RuntimeError, match='still being written'):
            job_runner._wait_for_media(_job(growing))
    finally:
        stop.set()
        writer.join(timeout=2)


def test_a_copy_that_finishes_in_time_is_waited_out(tmp_path, monkeypatch):
    monkeypatch.setattr(job_runner, 'MEDIA_SETTLE_TIMEOUT_SEC', 5.0)
    monkeypatch.setattr(job_runner, 'MEDIA_SETTLE_INTERVAL_SEC', 0.02)
    arriving = tmp_path / 'rec.m4a'
    arriving.write_bytes(b'x')

    def finish_the_copy():
        time.sleep(0.1)
        with open(arriving, 'ab') as handle:
            handle.write(b'x' * 4096)

    threading.Thread(target=finish_the_copy, daemon=True).start()

    job_runner._wait_for_media(_job(arriving))       # returns once it stops growing


def test_speaker_tracks_are_checked_too(tmp_path, monkeypatch):
    monkeypatch.setattr(job_runner, 'MEDIA_SETTLE_TIMEOUT_SEC', 0.05)
    monkeypatch.setattr(job_runner, 'MEDIA_SETTLE_INTERVAL_SEC', 0.01)
    main = _settled(tmp_path)

    with pytest.raises(RuntimeError, match='missing'):
        job_runner._wait_for_media(_job(main, tracks=[tmp_path / 'track2.wav']))
