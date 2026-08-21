"""Shared file I/O utilities for safe, atomic writes."""

import json
import os
import tempfile


def atomic_write_json(path, data):
    """Write JSON to a temp file then atomically replace the target.

    Uses tempfile.mkstemp in the same directory as the target so that
    os.replace is guaranteed to be atomic (same filesystem).  If anything
    goes wrong, the temp file is cleaned up and the original is untouched.
    """
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=directory, suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, path)
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
        os.replace(tmp_path, path)
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
