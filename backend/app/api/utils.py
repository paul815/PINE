"""Utility endpoints — OS-level helpers for the local desktop context."""

import os
import platform
import subprocess
import sys
import threading
import time

from flask import Blueprint, jsonify

utils_bp = Blueprint('utils', __name__)

ALLOWED_EXTENSIONS = {'mp3', 'mp4', 'm4a', 'wav', 'mkv', 'webm', 'ogg', 'flac'}
_FILETYPES = [
    ('Audio / Video', ' '.join(f'*.{e}' for e in sorted(ALLOWED_EXTENSIONS))),
    ('All files', '*.*'),
]

_IS_MAC = platform.system() == 'Darwin'

# ---------------------------------------------------------------------------
# macOS: use AppleScript for file picking so we don't need tkinter on the
# main thread (Flask serves requests on worker threads, and tkinter hangs).
# ---------------------------------------------------------------------------

def _mac_pick_files(multiple=False):
    """Use osascript to open the native Finder file-picker on macOS."""
    # Plain extensions are more reliable than UTIs across macOS versions
    exts = ', '.join(f'"{e}"' for e in sorted(ALLOWED_EXTENSIONS))
    multi_flag = 'with multiple selections allowed' if multiple else ''
    script = (
        'set theFiles to choose file '
        f'with prompt "Select recordings" '
        f'of type {{{exts}}} '
        f'{multi_flag}\n'
        'set output to ""\n'
        'if class of theFiles is list then\n'
        '  repeat with f in theFiles\n'
        '    set output to output & POSIX path of f & linefeed\n'
        '  end repeat\n'
        'else\n'
        '  set output to POSIX path of theFiles & linefeed\n'
        'end if\n'
        'return output'
    )
    result = subprocess.run(
        ['osascript', '-e', script],
        capture_output=True, text=True, timeout=300,
    )
    if result.returncode != 0:
        # User cancelled (return code 1) or error
        return []
    return [p for p in result.stdout.strip().splitlines() if p]

# ---------------------------------------------------------------------------
# Windows / Linux: tkinter works fine from any thread on these platforms.
# ---------------------------------------------------------------------------

def _tk_pick_files(multiple=False):
    """Use tkinter to open a file-picker dialog."""
    import tkinter as tk
    from tkinter import filedialog

    root = tk.Tk()
    root.withdraw()
    root.attributes('-topmost', True)
    if multiple:
        selection = filedialog.askopenfilenames(
            title='Select recordings', filetypes=_FILETYPES,
        )
        paths = list(selection) if selection else []
    else:
        path = filedialog.askopenfilename(
            title='Select a recording', filetypes=_FILETYPES,
        )
        paths = [path] if path else []
    root.destroy()
    return paths


def _pick_files(multiple=False):
    if _IS_MAC:
        return _mac_pick_files(multiple=multiple)
    return _tk_pick_files(multiple=multiple)


@utils_bp.route('/pick-file', methods=['POST'])
def pick_file():
    """Open the native OS file picker and return the selected path.

    This works because PINE is a local app — the server and the user's files
    are on the same machine, so the server can open a native dialog.
    Returns: { path: str } or { path: null } if the user cancelled.
    """
    try:
        paths = _pick_files(multiple=False)
        return jsonify({'path': paths[0] if paths else None})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@utils_bp.route('/pick-files', methods=['POST'])
def pick_files():
    """Open the native OS file picker for multiple files; return absolute paths.

    Returns: { paths: [str, ...] } — empty list if the user cancelled.
    """
    try:
        paths = _pick_files(multiple=True)
        return jsonify({'paths': paths})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@utils_bp.route('/runtime-status', methods=['GET'])
def runtime_status():
    """Lightweight heartbeat endpoint for browser <-> backend link checks."""
    return jsonify({'ok': True, 'python_running': True})


@utils_bp.route('/restart', methods=['POST'])
def restart_runtime():
    """Relaunch current Python process and terminate this one."""
    argv = [sys.executable] + sys.argv
    env = os.environ.copy()
    cwd = os.getcwd()

    def _restart():
        time.sleep(0.3)
        try:
            os.execv(sys.executable, argv)
        except Exception:
            kwargs = {'cwd': cwd, 'env': env}
            if platform.system() == 'Windows':
                kwargs['creationflags'] = subprocess.CREATE_NEW_PROCESS_GROUP
            else:
                kwargs['start_new_session'] = True
                kwargs['close_fds'] = True
            try:
                subprocess.Popen(argv, **kwargs)
            finally:
                from ..shutdown import graceful_exit
                graceful_exit(0)

    threading.Thread(target=_restart, daemon=True).start()
    return jsonify({'ok': True, 'status': 'restarting'})
