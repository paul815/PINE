"""Lazy setup for the bundled ffmpeg binaries.

``static_ffmpeg.add_paths()`` fetches a ~190 MB build from GitHub the first time
it runs and only prepends a directory to PATH on every run after that. That call
used to sit at the top of run.py, above the Flask import, so a first launch
against a fresh venv could not serve a single page until the transfer finished —
roughly three and a half minutes of a window that looked hung.

The fetch now belongs to the model download step, which already reports progress
and is where the user expects to wait. Everything here exists to keep that one
blocking call off the startup path while still guaranteeing that whoever
actually needs ffmpeg gets it.
"""

import os
import shutil
import threading

# add_paths() mutates os.environ['PATH'] for the whole process, so two callers
# racing it (the download thread and a transcription starting up) would both pay
# the fetch and interleave their edits.
_lock = threading.Lock()


def ffmpeg_on_path():
    """True if an ffmpeg binary is callable right now. Never downloads."""
    return shutil.which('ffmpeg') is not None


def ffmpeg_downloaded():
    """True if static_ffmpeg already holds a binary, making add_paths() cheap.

    Walks the package's own bin directory instead of calling add_paths(), which
    would start the very download this module defers. Used to tell "present but
    not yet on PATH" apart from "not fetched at all".
    """
    try:
        import static_ffmpeg
    except ImportError:
        return False

    root = os.path.join(os.path.dirname(static_ffmpeg.__file__), 'bin')
    name = 'ffmpeg.exe' if os.name == 'nt' else 'ffmpeg'
    # The archive unpacks into a per-platform subdirectory (bin/win32/...), so
    # walk rather than assuming the layout of a third-party package.
    for _dirpath, _dirnames, filenames in os.walk(root):
        if name in filenames:
            return True
    return False


def ensure_ffmpeg(allow_download=True):
    """Put ffmpeg on PATH, fetching it first if necessary.

    Returns True when ffmpeg is callable afterwards.

    With ``allow_download=False`` an already-fetched copy is still adopted onto
    PATH, but a missing one is reported rather than downloaded — for callers on
    a latency-sensitive path that need the answer, not the binary.
    """
    if ffmpeg_on_path():
        return True

    with _lock:
        # Another thread may have finished while this one waited for the lock.
        if ffmpeg_on_path():
            return True
        if not allow_download and not ffmpeg_downloaded():
            return False
        try:
            import static_ffmpeg
            static_ffmpeg.add_paths()
        except Exception:
            return False
        return ffmpeg_on_path()
