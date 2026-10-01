"""The atomic readers and writers, and the Windows replace window they cross.

A rename is one atomic step on POSIX. On Windows it is MoveFileExW, and for
the moment it takes to swap the file in it refuses everybody — because
CPython opens without FILE_SHARE_DELETE, any reader holding the file blocks
the replace, and any reader arriving mid-replace is blocked in turn.

Both halves were live bugs. Three recordings uploaded into one project at once
all rewrite that project's README.md, and one upload came back 500. A reader
in the same window got PermissionError, which every caller of these files read
as "no tags", "no attachments", or — in update_annotations — as "start from
defaults", which it then wrote back over real speaker labels.

The two halves report the same condition differently, and that is the part
worth guarding: os.replace fails from the Win32 API and carries .winerror,
open() fails from the C runtime with EACCES and no .winerror at all.
"""
import errno
import json
import os
import threading

import pytest

from app.services import file_utils, windows_io
from app.services.file_utils import (
    _WRITE_ATTEMPTS,
    _is_transient,
    atomic_read_json,
    atomic_read_text,
    atomic_write_json,
    atomic_write_text,
)


def _win32_error(winerror):
    """An OSError shaped like the one os.replace raises, on any platform."""
    err = OSError(errno.EACCES, 'Access is denied')
    err.winerror = winerror
    return err


def _crt_error(err_no=errno.EACCES):
    """An OSError shaped like the one open() raises: errno only, no winerror."""
    return OSError(err_no, 'Permission denied')


def _tmp_leftovers(directory):
    return [n for n in os.listdir(directory) if n.endswith('.tmp')]


@pytest.fixture(scope='module')
def renames_over_open_files(tmp_path_factory):
    """Can this machine rename over a file somebody is holding?

    A plain rename(2) does on POSIX. On Windows it needs 1607 or newer and a
    filesystem that implements FileRenameInfoEx — a checkout on an exFAT
    stick answers no, falls back to os.replace, and the two guarantees that
    depend on this stop holding.

    This runs the real thing rather than reasoning from os.name: hold the
    destination open exactly as a reader would, and see whether the write
    path can still replace it. Inferring it from the platform would have
    called a Windows machine POSIX-safe the moment anything stubbed the
    module out.
    """
    directory = tmp_path_factory.mktemp('rename-probe')
    src, dst = directory / 'src', directory / 'dst'
    src.write_text('src', encoding='utf-8')
    dst.write_text('dst', encoding='utf-8')
    with file_utils._open_for_read(str(dst), 'utf-8'):
        try:
            file_utils._rename(str(src), str(dst))
        except OSError:
            return False
    return True


class TestWhatCountsAsTransient:
    """The predicate both halves hang on. Getting it wrong is silent."""

    @pytest.mark.parametrize('winerror', [5, 32])
    def test_the_two_win32_replace_codes_are_transient(self, winerror):
        assert _is_transient(_win32_error(winerror))

    def test_another_win32_code_is_not(self):
        """.winerror, when present, is the authoritative answer.

        This one also carries EACCES. Falling through to the errno check would
        retry a genuine Win32 failure for a second on a coincidence.
        """
        assert not _is_transient(_win32_error(1))

    def test_a_bare_eacces_is_transient_only_on_windows(self, monkeypatch):
        """open() reports the replace window this way and no other."""
        monkeypatch.setattr(os, 'name', 'nt')
        assert _is_transient(_crt_error())

        monkeypatch.setattr(os, 'name', 'posix')
        assert not _is_transient(_crt_error())

    def test_other_errnos_are_never_transient(self, monkeypatch):
        monkeypatch.setattr(os, 'name', 'nt')
        assert not _is_transient(_crt_error(errno.ENOSPC))
        assert not _is_transient(_crt_error(errno.ENOENT))


class TestWriteSide:
    """The retry loop, over the os.replace path it falls back to.

    Windows normally renames through windows_io now, so these pin AVAILABLE
    off: this is the path taken on POSIX, on Windows before 1607, and on any
    volume without FileRenameInfoEx. TestRenameFallback and
    test_a_held_file_can_still_be_replaced cover the other one.
    """

    @pytest.fixture(autouse=True)
    def _use_the_os_replace_path(self, monkeypatch):
        monkeypatch.setattr(windows_io, 'AVAILABLE', False)

    @pytest.mark.parametrize('winerror', [5, 32])
    def test_a_transient_replace_failure_is_retried(self, tmp_path, monkeypatch, winerror):
        target = tmp_path / 'README.md'
        real_replace = os.replace
        calls = []

        def flaky_replace(src, dst):
            calls.append(src)
            if len(calls) <= 2:
                raise _win32_error(winerror)
            real_replace(src, dst)

        monkeypatch.setattr(os, 'replace', flaky_replace)
        atomic_write_text(str(target), 'survived')

        assert len(calls) == 3
        assert target.read_text(encoding='utf-8') == 'survived'
        assert not _tmp_leftovers(tmp_path)

    def test_a_permanent_failure_still_raises(self, tmp_path, monkeypatch):
        """Retrying must not bury a real problem, or leave the temp file behind."""
        target = tmp_path / 'README.md'

        monkeypatch.setattr(os, 'replace', lambda src, dst: (_ for _ in ()).throw(_win32_error(1)))
        with pytest.raises(OSError):
            atomic_write_text(str(target), 'nope')

        assert not target.exists()
        assert not _tmp_leftovers(tmp_path)

    def test_the_retry_gives_up_rather_than_spinning(self, tmp_path, monkeypatch):
        target = tmp_path / 'README.md'
        calls = []

        def always_transient(src, dst):
            calls.append(src)
            raise _win32_error(5)

        monkeypatch.setattr(os, 'replace', always_transient)
        with pytest.raises(OSError):
            atomic_write_json(str(target), {'a': 1})

        assert len(calls) == _WRITE_ATTEMPTS
        assert not _tmp_leftovers(tmp_path)


class TestReadSide:
    def test_the_error_open_actually_raises_is_retried(self, tmp_path, monkeypatch):
        """The regression that matters: errno only, no .winerror.

        Matching on .winerror alone made the read-side retry dead code, and a
        mock that set .winerror by hand could not tell.
        """
        monkeypatch.setattr(os, 'name', 'nt')
        target = tmp_path / 'project_tags.json'
        atomic_write_json(str(target), {'tags': ['kept']})
        real_opener = file_utils._open_for_read
        calls = []

        def flaky_open(path, encoding):
            calls.append(path)
            if len(calls) <= 2:
                raise _crt_error()
            return real_opener(path, encoding)

        monkeypatch.setattr(file_utils, '_open_for_read', flaky_open)

        assert atomic_read_json(str(target)) == {'tags': ['kept']}
        assert len(calls) == 3

    def test_a_missing_file_surfaces_at_once(self, tmp_path, monkeypatch):
        """ENOENT is an answer, not a transient — retrying it wastes 200ms."""
        calls = []
        real_opener = file_utils._open_for_read

        def counting_open(path, encoding):
            calls.append(path)
            return real_opener(path, encoding)

        monkeypatch.setattr(file_utils, '_open_for_read', counting_open)
        with pytest.raises(FileNotFoundError):
            atomic_read_text(str(tmp_path / 'absent.json'))

        assert len(calls) == 1

    def test_a_corrupt_file_still_raises_a_decode_error(self, tmp_path):
        """The retry must not turn a genuinely bad file into a silent default."""
        target = tmp_path / 'project_tags.json'
        target.write_text('{not json at all', encoding='utf-8')

        with pytest.raises(json.JSONDecodeError):
            atomic_read_json(str(target))

    def test_the_reader_hands_back_whole_content(self, tmp_path):
        target = tmp_path / 'notes.txt'
        atomic_write_text(str(target), 'line one\nline two\n')
        assert atomic_read_text(str(target)) == 'line one\nline two\n'


class TestRenameFallback:
    """POSIX-semantics rename is not available everywhere, and must not be assumed.

    Windows before 1607 does not know FileRenameInfoEx, and neither does a
    project folder on an exFAT stick. Both report it as a Win32 error, and the
    difference between "this volume cannot" and "this went wrong" decides
    whether a write quietly falls back or loudly fails.
    """

    @pytest.mark.parametrize('winerror', sorted(windows_io.RENAME_UNSUPPORTED))
    def test_an_unsupported_volume_falls_back_to_os_replace(
        self, tmp_path, monkeypatch, winerror,
    ):
        monkeypatch.setattr(windows_io, 'AVAILABLE', True)
        monkeypatch.setattr(
            windows_io, 'rename_posix',
            lambda src, dst: (_ for _ in ()).throw(_win32_error(winerror)),
            raising=False,
        )
        target = tmp_path / 'README.md'
        atomic_write_text(str(target), 'fell back cleanly')

        assert target.read_text(encoding='utf-8') == 'fell back cleanly'
        assert not _tmp_leftovers(tmp_path)

    def test_any_other_rename_error_is_not_swallowed(self, tmp_path, monkeypatch):
        """Falling back on everything would hide a genuine failure to write."""
        monkeypatch.setattr(windows_io, 'AVAILABLE', True)
        monkeypatch.setattr(
            windows_io, 'rename_posix',
            lambda src, dst: (_ for _ in ()).throw(_win32_error(1234)),
            raising=False,
        )
        with pytest.raises(OSError):
            atomic_write_text(str(tmp_path / 'README.md'), 'nope')

        assert not _tmp_leftovers(tmp_path)


class TestUnderContention:
    """The real races, unmocked. On Windows these are what used to break."""

    @pytest.mark.skipif(os.name != 'nt', reason='the replace window is a Windows behaviour')
    def test_a_held_file_can_still_be_replaced(self, tmp_path, renames_over_open_files):
        """The whole point, in one place, against the real Win32 calls.

        Before this, holding the file was enough to make the write fail. Both
        halves are needed and both are checked: the reader shares delete so
        the rename is allowed, and the rename has POSIX semantics so the
        reader keeps its own view until it closes.
        """
        if not renames_over_open_files:
            pytest.skip('this volume or Windows build has no FileRenameInfoEx')
        target = str(tmp_path / 'project_tags.json')
        atomic_write_json(target, {'tags': ['old']})

        with file_utils._open_for_read(target, 'utf-8') as held:
            atomic_write_json(target, {'tags': ['new']})
            assert json.loads(held.read()) == {'tags': ['old']}

        assert atomic_read_json(target) == {'tags': ['new']}
        assert not _tmp_leftovers(tmp_path)

    def test_many_writers_rewriting_one_file_never_fail(self, tmp_path):
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
        assert os.path.getsize(target) > 0
        assert not _tmp_leftovers(tmp_path)

    def test_readers_and_a_writer_coexist(self, tmp_path, renames_over_open_files):
        """Readers polling a file while it is rewritten — the app's own shape.

        Both sides are asserted. An earlier version of this test watched only
        the readers, and passed while the writer thread was dying quietly.

        There are deliberately no pauses. With retries alone, readers looping
        this tightly starved the writer — 5 writes lost in 750 — because a
        held handle blocked MoveFileExW outright. Sharing delete on the read
        and renaming with POSIX semantics on the write removed that, so the
        tight loop is now the assertion rather than the caveat. Where the
        rename is unavailable the old limit is still real, so the tight loop
        is not a fair thing to demand.
        """
        if not renames_over_open_files:
            pytest.skip('this volume or Windows build has no FileRenameInfoEx')
        target = str(tmp_path / 'project_tags.json')
        atomic_write_json(target, {'tags': ['seed']})
        stop = threading.Event()
        write_errors = []
        read_errors = []
        reads = []

        def rewrite():
            try:
                for n in range(60):
                    atomic_write_json(target, {'tags': [f'tag-{n}'] * 40})
            except Exception as exc:  # noqa: BLE001
                write_errors.append(exc)
            finally:
                stop.set()

        def read():
            while not stop.is_set():
                try:
                    reads.append(atomic_read_json(target))
                except Exception as exc:  # noqa: BLE001
                    read_errors.append(exc)
                    return

        writer = threading.Thread(target=rewrite)
        readers = [threading.Thread(target=read) for _ in range(3)]
        writer.start()
        for r in readers:
            r.start()
        writer.join()
        for r in readers:
            r.join()

        assert not write_errors, f'the writer was starved out: {write_errors}'
        assert not read_errors, f'a reader was turned away mid-replace: {read_errors}'
        assert reads, 'the readers never got a look in — the test proves nothing'
        assert all(isinstance(p.get('tags'), list) and p['tags'] for p in reads)
        assert not _tmp_leftovers(tmp_path)

    def test_a_reader_never_parses_half_a_document(self, tmp_path):
        """Atomicity itself, independent of the Windows window."""
        target = str(tmp_path / 'annotations.json')
        atomic_write_json(target, {'speaker_labels': {}})
        seen = []

        def rewrite():
            for n in range(120):
                atomic_write_json(target, {'speaker_labels': {str(i): f'S{n}' for i in range(30)}})

        def read():
            for _ in range(400):
                try:
                    seen.append(atomic_read_json(target))
                except OSError:
                    pass

        threads = [threading.Thread(target=rewrite), threading.Thread(target=read)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert seen
        assert all('speaker_labels' in p for p in seen)
        assert not _tmp_leftovers(tmp_path)


def test_the_module_exposes_both_halves():
    """A read site that reaches for open() directly reintroduces the bug."""
    for name in ('atomic_read_text', 'atomic_read_json', 'atomic_write_text', 'atomic_write_json'):
        assert callable(getattr(file_utils, name))
