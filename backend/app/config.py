import logging
import os
import secrets
import tempfile
import time

log = logging.getLogger(__name__)

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT_DIR = os.path.dirname(BACKEND_DIR)


SECRET_KEY_FILENAME = 'secret_key'


def _read_key(path):
    """The key stored at *path*, or '' when the file is missing or still empty."""
    try:
        with open(path, encoding='utf-8') as f:
            return f.read().strip()
    except OSError:
        return ''


def _wait_for_key(path, attempts=50, pause=0.01):
    """Re-read *path* while it is still empty, for up to attempts * pause.

    Only the fallback in _claim_key_file can publish an empty file, and only
    for the moment between creating it and writing to it. Half a second is
    several orders of magnitude more than that window, and a start that waited
    it out and still found nothing is better off with a session-only key than
    with a wait that never ends.
    """
    for _ in range(attempts):
        existing = _read_key(path)
        if existing:
            return existing
        time.sleep(pause)
    return ''


def _claim_key_file(path, data_dir, key):
    """Publish *key* at *path*; return False when another start got there first.

    The file has to become visible with the key already in it. Creating it
    empty with O_EXCL and writing a moment later looks atomic but is not: a
    second start arriving inside that window finds the name taken, reads
    nothing, and keeps its own key -- which is the exact outcome the exclusive
    create exists to prevent. So the key goes into a temporary file first and
    the name is claimed with a link, which fails instead of clobbering when
    someone else already holds it.
    """
    fd, tmp_path = tempfile.mkstemp(prefix=SECRET_KEY_FILENAME + '.', dir=data_dir)
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(key.encode('utf-8'))
        try:
            os.link(tmp_path, path)
            return True
        except FileExistsError:
            return False
        except OSError:
            # No hard links here -- a FAT/exFAT install directory. Fall back to
            # the two-step create: it still settles who wins the name, and the
            # empty window it reopens is what _wait_for_key covers.
            try:
                fallback_fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                return False
            try:
                os.write(fallback_fd, key.encode('utf-8'))
            finally:
                os.close(fallback_fd)
            return True
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def resolve_secret_key(data_dir):
    """Return this installation's Flask secret key, creating it on first run.

    Order: the ``SECRET_KEY`` environment variable, then ``data/secret_key``,
    then a fresh random key written to that file. Nothing in PINE uses Flask
    sessions today, so a rotated key logs nobody out — but a key shipped in
    source is a key every installation shares, and that stops being harmless
    the moment someone does start signing something with it.

    Falls back to an in-memory key if the file cannot be written (read-only
    install directory): the app still runs, it just gets a new key per start.
    """
    from_env = os.environ.get('SECRET_KEY')
    if from_env:
        return from_env

    path = os.path.join(data_dir, SECRET_KEY_FILENAME)
    existing = _read_key(path)
    if existing:
        return existing

    key = secrets.token_urlsafe(48)
    try:
        os.makedirs(data_dir, exist_ok=True)
        # Two starts together must not each write a key and leave the loser
        # using one that is no longer on disk.
        if _claim_key_file(path, data_dir, key):
            return key
        return _wait_for_key(path) or key
    except OSError:
        log.warning('Could not persist the secret key to %s; using a session-only key', path)
    return key


class Config:
    # Replaced in create_app() with the per-installation key. The placeholder
    # only has to be truthy for Flask's config machinery.
    SECRET_KEY = 'pine-unset'

    ROOT_DIR = ROOT_DIR
    DATA_DIR = os.path.join(BACKEND_DIR, 'data')
    SQLITE_DB_FILENAME = 'pine.db'
    SQLITE_LEGACY_DB_FILENAME = 'port.db'
    SQLALCHEMY_DATABASE_URI = f'sqlite:///{os.path.join(DATA_DIR, SQLITE_DB_FILENAME)}'
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    DEFAULT_MODELS_PATH = os.path.join(ROOT_DIR, 'models')
    DEFAULT_PROJECTS_PATH = os.path.join(ROOT_DIR, 'projects')

    MAX_CONTENT_LENGTH = 4 * 1024 * 1024 * 1024  # 4 GB
