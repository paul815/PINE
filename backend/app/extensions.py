import sqlite3

from flask_socketio import SocketIO
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import event
from sqlalchemy.engine import Engine

db = SQLAlchemy()


@event.listens_for(Engine, "connect")
def _set_sqlite_pragmas(dbapi_connection, connection_record):
    """Apply the safest SQLite PRAGMAs supported by the current filesystem."""
    if not isinstance(dbapi_connection, sqlite3.Connection):
        return

    cursor = dbapi_connection.cursor()
    journal_mode = None

    try:
        result = cursor.execute("PRAGMA journal_mode=WAL;").fetchone()
        journal_mode = (result[0] if result else "").lower()
    except sqlite3.DatabaseError:
        journal_mode = None

    if journal_mode != "wal":
        # Some Windows filesystems reject WAL/DELETE journaling with disk I/O
        # errors. TRUNCATE keeps rollback journaling enabled while avoiding the
        # failing unlink path.
        result = cursor.execute("PRAGMA journal_mode=TRUNCATE;").fetchone()
        journal_mode = (result[0] if result else "").lower()

    if journal_mode == "wal":
        cursor.execute("PRAGMA synchronous=NORMAL;")
    else:
        cursor.execute("PRAGMA synchronous=FULL;")
    cursor.execute("PRAGMA foreign_keys=ON;")
    cursor.close()


from .ports import allowed_origins

# Read once: SocketIO is constructed below at import time and cannot be told
# about a new origin later. The supervisor sets PINE_BACKEND_PORT in the child
# environment before the backend starts, so the value is already final here.
ALLOWED_ORIGINS = allowed_origins()

socketio = SocketIO(cors_allowed_origins=ALLOWED_ORIGINS, async_mode='threading',
                    ping_timeout=120, ping_interval=25)


def safe_emit(event, data):
    """Emit a SocketIO event, ignoring a disconnected or absent client.

    Progress events are advisory: a download or install must finish even when
    nobody is watching, so a failure to deliver one must never propagate into
    the work that produced it.
    """
    try:
        socketio.emit(event, data)
    except Exception:
        pass
