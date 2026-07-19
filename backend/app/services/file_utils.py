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
