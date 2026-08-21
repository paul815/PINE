import logging
import os
import secrets

log = logging.getLogger(__name__)

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT_DIR = os.path.dirname(BACKEND_DIR)


SECRET_KEY_FILENAME = 'secret_key'


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
    try:
        with open(path, encoding='utf-8') as f:
            existing = f.read().strip()
        if existing:
            return existing
    except OSError:
        pass

    key = secrets.token_urlsafe(48)
    try:
        os.makedirs(data_dir, exist_ok=True)
        # O_EXCL: two workers starting together must not each write a key and
        # leave the loser using one that is no longer on disk.
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        try:
            os.write(fd, key.encode('utf-8'))
        finally:
            os.close(fd)
    except FileExistsError:
        try:
            with open(path, encoding='utf-8') as f:
                return f.read().strip() or key
        except OSError:
            return key
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
