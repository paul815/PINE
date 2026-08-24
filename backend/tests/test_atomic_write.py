"""The atomic writers, and the Windows replace failure they used to leak.

os.replace is a single atomic rename on POSIX. On Windows it is MoveFileExW,
which fails the call rather than waiting when anything else holds the
destination for a moment. Three recordings uploaded into one project at once
all rewrite that project's README.md, and one upload used to come back 500
with "WinError 5: Access is denied" about one run in forty.
"""
import json
import os
import threading

import pytest

from app.services.file_utils import atomic_write_json, atomic_write_text


def _oserror(winerror):
    """An OSError shaped like the one Windows raises, on any platform."""
    err = OSError(13, 'Access is denied')
    err.winerror = winerror
    return err


def _tmp_leftovers(directory):
    return [n for n in os.listdir(directory) if n.endswith('.tmp')]


class TestTransientReplaceFailures:
    @pytest.mark.parametrize('winerror', [5, 32])
    def test_a_transient_replace_failure_is_retried(self, tmp_path, monkeypatch, winerror):
        """ERROR_ACCESS_DENIED and ERROR_SHARING_VIOLATION both mean 'try again'."""
        target = tmp_path / 'README.md'
        real_replace = os.replace
        calls = []

        def flaky_replace(src, dst):
            calls.append(src)
            if len(calls) <= 2:
                raise _oserror(winerror)
            real_replace(src, dst)

        monkeypatch.setattr(os, 'replace', flaky_replace)
        atomic_write_text(str(target), 'survived')

        assert len(calls) == 3
        assert target.read_text(encoding='utf-8') == 'survived'
        assert not _tmp_leftovers(tmp_path)

    def test_a_permanent_failure_still_raises(self, tmp_path, monkeypatch):
        """Retrying must not bury a real permission problem."""
        target = tmp_path / 'README.md'

        def always_denied(src, dst):
            raise _oserror(1)  # ERROR_INVALID_FUNCTION — not in the transient set

        monkeypatch.setattr(os, 'replace', always_denied)
        with pytest.raises(OSError):
            atomic_write_text(str(target), 'nope')

        assert not target.exists()
        assert not _tmp_leftovers(tmp_path)

    def test_a_plain_oserror_is_not_retried(self, tmp_path, monkeypatch):
        """POSIX errors carry no .winerror, so they must propagate at once."""
        target = tmp_path / 'README.md'
        calls = []

        def refuse(src, dst):
            calls.append(src)
            raise OSError(28, 'No space left on device')

        monkeypatch.setattr(os, 'replace', refuse)
        with pytest.raises(OSError):
            atomic_write_json(str(target), {'a': 1})

        assert len(calls) == 1
        assert not _tmp_leftovers(tmp_path)

    def test_the_retry_gives_up_rather_than_spinning(self, tmp_path, monkeypatch):
        target = tmp_path / 'README.md'
        calls = []

        def always_transient(src, dst):
            calls.append(src)
            raise _oserror(5)

        monkeypatch.setattr(os, 'replace', always_transient)
        with pytest.raises(OSError):
            atomic_write_text(str(target), 'nope')

        assert len(calls) == 10
        assert not _tmp_leftovers(tmp_path)


class TestConcurrentRewrites:
    """The real race, unmocked. On Windows this is what used to 500."""

    def test_many_threads_rewriting_one_file_never_fails(self, tmp_path):
        target = str(tmp_path / 'README.md')
        errors = []

        def rewrite(idx):
            try:
                for n in range(25):
                    atomic_write_text(target, f'writer {idx} pass {n}\n')
            except Exception as exc:  # noqa: BLE001 — the point is that none escape
                errors.append(exc)

        threads = [threading.Thread(target=rewrite, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f'atomic_write_text raised under contention: {errors}'
        assert os.path.exists(target)
        assert os.path.getsize(target) > 0
        assert not _tmp_leftovers(tmp_path)

    def test_a_reader_never_sees_a_half_written_json_document(self, tmp_path):
        """Atomicity itself: every read that succeeds parses as a whole document.

        Failing to *open* the file is a different matter and is not asserted
        here. On Windows a reader that arrives inside the replace window gets
        PermissionError instead of a torn file — the writers are fixed, the
        read sites are not, and retrying every open in the app is its own
        change. What must never happen, on any platform, is a successful read
        of half a document.
        """
        target = str(tmp_path / 'tags.json')
        atomic_write_json(target, {'tags': ['seed']})
        stop = threading.Event()
        torn = []
        read_ok = []

        def rewrite():
            try:
                for n in range(60):
                    atomic_write_json(target, {'tags': [f'tag-{n}'] * 40})
            finally:
                stop.set()

        def read():
            while not stop.is_set():
                try:
                    with open(target, encoding='utf-8') as fh:
                        payload = json.load(fh)
                except (FileNotFoundError, PermissionError):
                    continue  # the replace window, not a torn file
                except Exception as exc:  # noqa: BLE001
                    torn.append(exc)
                    return
                read_ok.append(payload)

        writer = threading.Thread(target=rewrite)
        reader = threading.Thread(target=read)
        writer.start()
        reader.start()
        writer.join()
        reader.join()

        assert not torn, f'reader parsed a half-written file: {torn}'
        assert read_ok, 'the reader never got a look in — the test proves nothing'
        assert all(isinstance(p.get('tags'), list) and p['tags'] for p in read_ok)
        assert not _tmp_leftovers(tmp_path)
