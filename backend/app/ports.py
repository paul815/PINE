"""Where the two PINE processes listen, and the one place their defaults live.

The supervisor owns both numbers: it picks free ports, then passes them to the
backend it spawns as ``PINE_BACKEND_PORT`` / ``PINE_SUPERVISOR_PORT``. Anything
that needs a port asks here rather than repeating the literal.

These are functions, not module constants, on purpose. The onboarding handoff
rewrites ``os.environ['PINE_SUPERVISOR_PORT']`` in a running process when it
discovers the supervisor moved; a constant captured at import time would keep
pointing at the old port for the rest of the session.

Imports nothing from the app package — ``extensions`` needs it at import time.
"""

import json
import os

DEFAULT_BACKEND_PORT = 5000
DEFAULT_SUPERVISOR_PORT = 5001

#: Hostname the launcher opens. The browser resolves it to 127.0.0.1, but a
#: named host gives the app its own origin (and so its own localStorage and
#: cookie jar) instead of sharing one with every other local dev server.
APP_HOSTNAME = 'pine.localhost'


def _port(var, default):
    raw = os.environ.get(var)
    if not raw:
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def backend_port():
    """Port the Flask backend serves on."""
    return _port('PINE_BACKEND_PORT', DEFAULT_BACKEND_PORT)


def supervisor_port():
    """Port the supervisor's control API serves on."""
    return _port('PINE_SUPERVISOR_PORT', DEFAULT_SUPERVISOR_PORT)


def backend_origin(host='127.0.0.1'):
    """Origin of the backend, e.g. ``http://127.0.0.1:5000``."""
    return f'http://{host}:{backend_port()}'


def supervisor_url(path=''):
    """URL of a supervisor control endpoint, e.g. ``.../status``."""
    return f'http://127.0.0.1:{supervisor_port()}/{path.lstrip("/")}'


def supervisor_port_file():
    """Where the supervisor publishes the ports and token it is actually using.

    The path is duplicated from supervisor.py rather than imported, for the same
    reason the port defaults are: that process starts before the Flask stack
    exists and cannot import this module. Change both together.
    """
    data_dir = os.environ.get('PINE_DATA_DIR') or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        'data',
    )
    return os.path.join(data_dir, 'supervisor.port')


def read_supervisor_port_file():
    """What the running supervisor published, or None when there is no file.

    Absent is the normal case, not an error: the supervisor removes the file on
    shutdown, so nothing here may treat its absence as a failure.
    """
    try:
        with open(supervisor_port_file(), encoding='utf-8') as handle:
            published = json.load(handle)
    except (OSError, ValueError):
        return None
    return published if isinstance(published, dict) else None


def allowed_origins():
    """Origins the browser may call the API from.

    Both spellings of the same server: the launcher opens ``pine.localhost``,
    but a user who types the numeric address should not be locked out.
    """
    port = backend_port()
    return [
        f'http://{APP_HOSTNAME}:{port}',
        f'http://127.0.0.1:{port}',
    ]
