from pathlib import Path

from flask import Flask

from app.api import onboarding


REPO_ROOT = Path(__file__).resolve().parents[2]


def _call_prepare_handoff(monkeypatch, onboarding_complete: str, supervisor_ready: bool):
    app = Flask(__name__)
    monkeypatch.setattr(onboarding.Setting, 'get', lambda key, default=None: onboarding_complete if key == 'onboarding_complete' else default)
    fake_info = {'token': 'tok-abc', 'port': 5001} if supervisor_ready else None
    monkeypatch.setattr(onboarding, '_ensure_supervisor_running', lambda timeout=8.0: fake_info)

    with app.test_request_context('/api/onboarding/handoff/prepare', method='POST'):
        result = onboarding.prepare_handoff()

    if isinstance(result, tuple):
        response, status = result
        response.status_code = status
        return response
    return result


def test_prepare_handoff_requires_completed_onboarding(monkeypatch):
    resp = _call_prepare_handoff(monkeypatch, onboarding_complete='false', supervisor_ready=True)

    assert resp.status_code == 409
    payload = resp.get_json()
    assert payload['ok'] is False


def test_prepare_handoff_starts_supervisor_when_onboarding_complete(monkeypatch):
    resp = _call_prepare_handoff(monkeypatch, onboarding_complete='true', supervisor_ready=True)

    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload == {
        'ok': True,
        'supervisor_running': True,
        'supervisor_token': 'tok-abc',
        'supervisor_port': 5001,
        'main_url': '/',
    }


def test_prepare_handoff_returns_503_when_supervisor_cannot_start(monkeypatch):
    resp = _call_prepare_handoff(monkeypatch, onboarding_complete='true', supervisor_ready=False)

    assert resp.status_code == 503
    payload = resp.get_json()
    assert payload['ok'] is False


def test_onboarding_template_waits_for_backend_ready_before_navigation():
    template = (REPO_ROOT / 'backend' / 'templates' / 'onboarding.html').read_text(encoding='utf-8')

    assert 'const HANDOFF_BACKEND_READY_TIMEOUT_MS = 150000;' in template
    assert "if (st.supervisor_running && st.backend_ready) return st;" in template
    assert "window.location.replace(prep.main_url || '/');" in template


def test_onboarding_template_suppresses_disconnect_banner_during_handoff():
    template = (REPO_ROOT / 'backend' / 'templates' / 'onboarding.html').read_text(encoding='utf-8')

    assert 'function hideOnboardingSocketBanner()' in template
    assert 'if (_onboardingHandoffStarted) {' in template
    assert "hideOnboardingSocketBanner();" in template
