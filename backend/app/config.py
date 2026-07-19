import os

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT_DIR = os.path.dirname(BACKEND_DIR)


class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY', 'port-dev-key-change-in-prod')

    ROOT_DIR = ROOT_DIR
    DATA_DIR = os.path.join(BACKEND_DIR, 'data')
    SQLITE_DB_FILENAME = 'pine.db'
    SQLITE_LEGACY_DB_FILENAME = 'port.db'
    SQLALCHEMY_DATABASE_URI = f'sqlite:///{os.path.join(DATA_DIR, SQLITE_DB_FILENAME)}'
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    DEFAULT_MODELS_PATH = os.path.join(ROOT_DIR, 'models')
    DEFAULT_PROJECTS_PATH = os.path.join(ROOT_DIR, 'projects')

    MAX_CONTENT_LENGTH = 4 * 1024 * 1024 * 1024  # 4 GB
