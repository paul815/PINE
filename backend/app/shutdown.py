"""Graceful shutdown utilities for the PINE backend."""

import logging
import os

log = logging.getLogger(__name__)


def graceful_exit(code: int = 0) -> None:
    """Flush SQLite WAL and dispose engine before terminating the process.

    Called from daemon threads where ``sys.exit()`` only raises SystemExit
    inside the thread (it does NOT terminate the process).  We therefore still
    call ``os._exit()`` — but only *after* flushing pending writes.
    """
    try:
        from .extensions import db

        with db.engine.connect() as conn:
            conn.execute(db.text("PRAGMA wal_checkpoint(TRUNCATE);"))
            log.info("SQLite WAL checkpoint completed")
        db.engine.dispose()
        log.info("Database engine disposed")
    except Exception:
        log.warning("Could not flush SQLite during shutdown", exc_info=True)

    os._exit(code)
