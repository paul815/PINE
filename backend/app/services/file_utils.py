"""Shared file I/O utilities for safe, atomic writes."""

import json
import os
import tempfile
import time

# os.replace is one atomic rename on POSIX, but on Windows it goes through
# MoveFileExW, which does not queue behind whoever currently holds the
# destination — it fails the call outright. ERROR_ACCESS_DENIED (5) and
# ERROR_SHARING_VIOLATION (32) both mean "someone had it open for a moment":
# another thread replacing the same file, the search indexer, an antivirus
# scanner reading what we just wrote.
_TRANSIENT_REPLACE_ERRORS = frozenset({5, 32})


def _replace_atomically(tmp_path, path, attempts=10):
    """os.replace, retried through Windows' transient replace failures.

    Three recordings uploaded into one project at once all rewrite that
    project's README.md, and two of the three replaces would collide: one
    upload returned 500 with WinError 5 roughly one run in forty. The window
    is microseconds wide, so the first backoff almost always wins; a real
    permission problem still raises, just ~0.26s later.

    On POSIX no exception carries .winerror, so this is a plain os.replace.
    """
    for attempt in range(attempts):
        try:
            os.replace(tmp_path, path)
            return
        except OSError as exc:
            last = attempt == attempts - 1
            if last or getattr(exc, 'winerror', None) not in _TRANSIENT_REPLACE_ERRORS:
                raise
            time.sleep(min(0.001 * (2 ** attempt), 0.05))


def atomic_write_json(path, data):
    """Write JSON to a temp file then atomically replace the target.

    Uses tempfile.mkstemp in the same directory as the target so that
    the replace stays on one filesystem.  If anything goes wrong, the temp
    file is cleaned up and the original is untouched.
    """
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=directory, suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        _replace_atomically(tmp_path, path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def atomic_write_text(path, text):
    """Write text to a temp file then atomically replace the target."""
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=directory, suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(text)
        _replace_atomically(tmp_path, path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def claim_free_path(directory, filename, max_tries=10000):
    """Reserve a non-colliding path inside *directory* and return it.

    Returns ``(path, name)`` for an empty file this call just created. The
    caller is expected to overwrite it.

    The obvious version of this — walk a counter while ``os.path.exists`` is
    true, then write — has a window between the check and the write. Two
    uploads of the same filename arriving together both see the name as free
    and the second one overwrites the first recording's audio, leaving two DB
    rows pointing at one file. ``O_CREAT | O_EXCL`` closes that window: the
    filesystem hands the name to exactly one caller and the loser retries with
    the next counter.

    Names go ``interview.mp3``, ``interview_2.mp3``, ``interview_3.mp3`` …,
    matching what the check-then-write version produced.
    """
    os.makedirs(directory, exist_ok=True)
    base, extension = os.path.splitext(filename)
    name = filename
    for counter in range(1, max_tries + 1):
        if counter > 1:
            name = f'{base}_{counter}{extension}'
        path = os.path.join(directory, name)
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            continue
        os.close(fd)
        return path, name
    raise OSError(f'Could not find a free filename for {filename!r} in {directory!r}')
