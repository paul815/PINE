"""Pytest fixtures for PINE backend tests."""

import os
import sys
import tempfile
import shutil
import stat

import pytest

# Ensure backend app is importable when running pytest from project root
_backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

# Use a testing config that avoids touching real data
os.environ.setdefault('PINE_TESTING', '1')


class TestingConfig:
    """Config for tests: temp DB, temp project dir."""
    SECRET_KEY = 'test-secret'
    TESTING = True
    SQLITE_DB_FILENAME = 'pine.db'
    SQLITE_LEGACY_DB_FILENAME = 'port.db'
    SQLALCHEMY_DATABASE_URI = 'sqlite:///:memory:'
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    DEFAULT_MODELS_PATH = None  # Not used in most tests
    DEFAULT_PROJECTS_PATH = None  # Set per-fixture
    MAX_CONTENT_LENGTH = 4 * 1024 * 1024 * 1024
    DATA_DIR = None  # Set per-fixture


def pytest_sessionfinish(session, exitstatus):
    """Clean up pytest-created temp dirs in backend root after test session."""
    backend_root = _backend_dir

    def _on_rm_error(func, path, exc_info):
        try:
            os.chmod(path, stat.S_IWRITE)
            func(path)
        except Exception:
            pass

    try:
        for name in os.listdir(backend_root):
            if not name.startswith("pytest-cache-files-"):
                continue
            path = os.path.join(backend_root, name)
            if os.path.isdir(path):
                shutil.rmtree(path, onerror=_on_rm_error)
    except Exception:
        # Cleanup should never fail the test run.
        pass


@pytest.fixture
def temp_dir():
    """Create a temporary directory. Cleaned up after test."""
    d = tempfile.mkdtemp()
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def app(temp_dir):
    """Flask app with temp DB and temp projects path."""
    from app import create_app

    data_dir = os.path.join(temp_dir, 'data')
    projects_dir = os.path.join(temp_dir, 'projects')
    os.makedirs(data_dir, exist_ok=True)
    os.makedirs(projects_dir, exist_ok=True)

    class Config(TestingConfig):
        DATA_DIR = data_dir
        ROOT_DIR = temp_dir
        SQLALCHEMY_DATABASE_URI = f'sqlite:///{os.path.join(data_dir, "pine.db")}'
        DEFAULT_PROJECTS_PATH = projects_dir
        DEFAULT_MODELS_PATH = os.path.join(temp_dir, 'models')

    app = create_app(Config)
    return app


@pytest.fixture
def client(app):
    """Flask test client."""
    return app.test_client()


@pytest.fixture
def app_context(app):
    """Application context for tests that need it."""
    with app.app_context():
        yield


@pytest.fixture
def project_dir(temp_dir):
    """A temp directory simulating a project folder."""
    d = os.path.join(temp_dir, 'test_project')
    os.makedirs(d, exist_ok=True)
    return d


@pytest.fixture
def project_with_recording(app):
    """Create a project with a recording and optional transcript file on disk.

    Returns a factory function:
        create(transcript_data=None, status='transcribed', **rec_kwargs)
            → (project_id, recording_id, project_dir_path)
    """
    import json as _json

    def _create(transcript_data=None, status='transcribed', **rec_kwargs):
        with app.app_context():
            from app.extensions import db
            from app.models.project import Project
            from app.models.recording import Recording
            from app.models.setting import Setting

            projects_path = app.config['DEFAULT_PROJECTS_PATH']
            os.makedirs(projects_path, exist_ok=True)
            Setting.set('projects_path', projects_path)

            proj = Project(name='Test Proj', folder_name='test_proj')
            db.session.add(proj)
            db.session.flush()

            defaults = dict(
                project_id=proj.id,
                original_name='rec.mp3',
                stored_name='rec.mp3',
                transcript_path='rec_transcript.json',
                transcription_status=status,
                duration_seconds=60,
            )
            defaults.update(rec_kwargs)
            rec = Recording(**defaults)
            db.session.add(rec)
            db.session.commit()

            proj_dir = os.path.join(projects_path, proj.folder_name)
            os.makedirs(proj_dir, exist_ok=True)

            if transcript_data is not None:
                with open(os.path.join(proj_dir, rec.transcript_path), 'w', encoding='utf-8') as f:
                    _json.dump(transcript_data, f, ensure_ascii=False)

            return proj.id, rec.id, proj_dir

    return _create
