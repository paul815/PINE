"""Pytest fixtures for PINE backend tests."""

import os
import shutil
import stat
import sys
import tempfile
import threading

import pytest

# Ensure backend app is importable when running pytest from project root
_backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

# Use a testing config that avoids touching real data
os.environ.setdefault('PINE_TESTING', '1')


# --- Process-exit guard ------------------------------------------------------
#
# Four routes deliberately terminate the backend from a *daemon thread* after a
# short sleep:
#
#   POST /api/settings/reset        0.8s -> graceful_exit(0)  -> os._exit(0)
#   POST /api/utils/restart         0.3s -> os.execv(...)     (replaces the image)
#   POST /api/internal/quit-backend 0.3s -> graceful_exit(0)
#   the lease-shutdown route        0.5s -> graceful_exit(0)
#
# In production that is correct: these run on daemon threads, where sys.exit()
# only raises SystemExit inside the thread and would not stop the process.
# Under pytest it is a disaster — the thread kills the *test runner* with status
# 0, so the run reports success with no summary line and no --junitxml file
# while most of the suite never executes.
#
# We neutralise the exit primitives instead of touching production shutdown
# behaviour.  Two properties matter:
#
#   * The threads fire on a delay, so a call armed by one test lands during a
#     later test, during teardown, or during session finish.  The guard must
#     therefore be installed once and *never* lifted — a function-scoped
#     monkeypatch would reopen the window every time it unwound.
#   * Every graceful_exit() call site imports it lazily (`from ..shutdown
#     import graceful_exit` inside the function body), so patching the module
#     attribute reaches all of them; no stale direct references exist.
#
# Recorded calls are exposed through the `process_exit_calls` fixture so a test
# can assert that a route asked to exit.
_process_exit_calls = []


def _record_process_exit(name):
    """Return a stand-in that records the exit request instead of performing it."""

    def _recorder(*args, **kwargs):
        # The calling thread is recorded because these threads outlive the test
        # that armed them: a reset thread from one test lands during a later
        # one.  Assertions filter on the ident so they cannot be tripped by
        # another test's in-flight shutdown.
        _process_exit_calls.append((name, args, threading.get_ident()))
        return None

    # Marker so a test can assert the guard is installed without depending on
    # how pytest happened to name this module on import.
    _recorder._pine_exit_guard = True
    return _recorder


# Installed at import time, before any test can arm a thread, and never undone.
os._exit = _record_process_exit('os._exit')
os.execv = _record_process_exit('os.execv')


@pytest.fixture(scope='session', autouse=True)
def _guard_graceful_exit():
    """Stop app.shutdown.graceful_exit from running its teardown during tests.

    os._exit is already stubbed above, which is what actually keeps the process
    alive.  Patching graceful_exit as well keeps its SQLite WAL checkpoint and
    engine dispose from running against a live test database.  Imported here
    rather than at module scope so importing the app stays inside the fixture
    lifecycle.  Deliberately not restored: see the note above.
    """
    from app import shutdown as app_shutdown

    app_shutdown.graceful_exit = _record_process_exit('graceful_exit')
    yield


@pytest.fixture
def process_exit_calls():
    """Exit requests recorded so far, as (name, args, thread_ident) triples.

    Entries can arrive from threads armed by earlier tests, so filter on
    thread_ident rather than asserting against the whole list.
    """
    return _process_exit_calls


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

            defaults = {
                'project_id': proj.id,
                'original_name': 'rec.mp3',
                'stored_name': 'rec.mp3',
                'transcript_path': 'rec_transcript.json',
                'transcription_status': status,
                'duration_seconds': 60,
            }
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
