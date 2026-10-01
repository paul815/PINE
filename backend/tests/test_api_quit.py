import json
import os
import shutil
import threading
import uuid


def _build_test_client():
    from app import create_app

    temp_root = os.path.join(
        os.getcwd(),
        f"pytest-api-quit-{uuid.uuid4().hex}",
    )
    data_dir = os.path.join(temp_root, "data")
    projects_dir = os.path.join(temp_root, "projects")
    models_dir = os.path.join(temp_root, "models")
    os.makedirs(data_dir, exist_ok=True)
    os.makedirs(projects_dir, exist_ok=True)
    os.makedirs(models_dir, exist_ok=True)

    class Config:
        SECRET_KEY = "test-secret"
        TESTING = True
        SQLITE_DB_FILENAME = "pine.db"
        SQLITE_LEGACY_DB_FILENAME = "port.db"
        SQLALCHEMY_DATABASE_URI = f"sqlite:///{os.path.join(data_dir, 'pine.db')}"
        SQLALCHEMY_TRACK_MODIFICATIONS = False
        MAX_CONTENT_LENGTH = 4 * 1024 * 1024 * 1024
        DATA_DIR = data_dir
        ROOT_DIR = temp_root
        DEFAULT_PROJECTS_PATH = projects_dir
        DEFAULT_MODELS_PATH = models_dir

    app = create_app(Config)
    return app.test_client(), temp_root


def test_api_quit_forwards_shutdown_context(monkeypatch):
    import app as app_module

    called = threading.Event()
    captured = {}

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    def _fake_urlopen(req, timeout=0):
        captured["url"] = req.full_url
        captured["method"] = req.get_method()
        captured["payload"] = json.loads((req.data or b"{}").decode("utf-8"))
        called.set()
        return _Response()

    monkeypatch.setattr(app_module.urllib.request, "urlopen", _fake_urlopen)
    monkeypatch.setattr(app_module.os, "_exit", lambda _code: None)
    client, temp_root = _build_test_client()
    try:
        response = client.post(
            "/api/quit",
            json={
                "reason": "user_quit_action",
                "source": "main_page",
                "lease_id": "tab-42",
            },
        )
        assert response.status_code == 200
        assert response.get_json() == {"ok": True}
        assert called.wait(timeout=2)
        assert captured["url"] == "http://127.0.0.1:5001/shutdown"
        assert captured["method"] == "POST"
        assert captured["payload"] == {
            "reason": "user_quit_action",
            "source": "main_page",
            "lease_id": "tab-42",
        }
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def test_api_quit_uses_backend_defaults_when_context_missing(monkeypatch):
    import app as app_module

    called = threading.Event()
    captured = {}

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    def _fake_urlopen(req, timeout=0):
        captured["payload"] = json.loads((req.data or b"{}").decode("utf-8"))
        called.set()
        return _Response()

    monkeypatch.setattr(app_module.urllib.request, "urlopen", _fake_urlopen)
    monkeypatch.setattr(app_module.os, "_exit", lambda _code: None)
    client, temp_root = _build_test_client()
    try:
        response = client.post("/api/quit", json={})
        assert response.status_code == 200
        assert response.get_json() == {"ok": True}
        assert called.wait(timeout=2)
        assert captured["payload"] == {
            "reason": "unknown",
            "source": "unspecified",
            "lease_id": "",
        }
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)
