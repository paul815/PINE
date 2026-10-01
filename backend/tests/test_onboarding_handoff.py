import os
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
    assert "if (st.supervisor_running && st.backend_ready && await backendAnswersHere()) return st;" in template
    assert "window.location.replace(prep.main_url || '/');" in template


def test_onboarding_handoff_waits_for_this_origin_to_serve_health():
    """backend_ready fires from create_app, before the server binds its port.

    On a Mac the AirPlay Receiver answers :5000 in that gap with a bare 403,
    so the page must hear PINE's own health payload before it navigates.
    """
    template = (REPO_ROOT / 'backend' / 'templates' / 'onboarding.html').read_text(encoding='utf-8')

    assert "const res = await fetch('/api/health', { cache: 'no-store' });" in template
    assert 'return !!(body && body.ok === true);' in template


def test_onboarding_template_suppresses_disconnect_banner_during_handoff():
    template = (REPO_ROOT / 'backend' / 'templates' / 'onboarding.html').read_text(encoding='utf-8')

    assert 'function hideOnboardingSocketBanner()' in template
    assert 'if (_onboardingHandoffStarted) {' in template
    assert "hideOnboardingSocketBanner();" in template


def test_token_of_a_supervisor_we_did_not_start_comes_from_the_port_file(monkeypatch):
    """Only the process that spawned a supervisor has its token in the environment.

    A backend that was restarted, or started by the launcher, has an empty one —
    and used to hand the page exactly that, so every control call it made was
    refused. The supervisor publishes the token beside its ports for this.
    """
    monkeypatch.delenv('PINE_SUPERVISOR_TOKEN', raising=False)
    monkeypatch.setattr(onboarding, '_probe_supervisor_status',
                        lambda timeout=0.6: {'supervisor_running': True, 'supervisor_port': 5101})
    monkeypatch.setattr(onboarding, 'read_supervisor_port_file',
                        lambda: {'supervisor_port': 5101, 'backend_port': 5100, 'token': 'tok-from-file'})

    info = onboarding._ensure_supervisor_running()

    assert info == {'token': 'tok-from-file', 'port': 5101}
    # Cached, so the next call does not go back to disk for it.
    assert os.environ['PINE_SUPERVISOR_TOKEN'] == 'tok-from-file'


def test_an_environment_token_still_wins_over_the_file(monkeypatch):
    """We spawned this supervisor: what we passed it is authoritative."""
    monkeypatch.setenv('PINE_SUPERVISOR_TOKEN', 'tok-from-env')
    monkeypatch.setattr(onboarding, '_probe_supervisor_status',
                        lambda timeout=0.6: {'supervisor_running': True, 'supervisor_port': 5101})
    monkeypatch.setattr(onboarding, 'read_supervisor_port_file',
                        lambda: {'token': 'tok-from-file'})

    assert onboarding._ensure_supervisor_running()['token'] == 'tok-from-env'


def test_a_missing_port_file_is_not_an_error(monkeypatch):
    """The supervisor removes it on shutdown; absence is a normal state."""
    monkeypatch.delenv('PINE_SUPERVISOR_TOKEN', raising=False)
    monkeypatch.setattr(onboarding, '_probe_supervisor_status',
                        lambda timeout=0.6: {'supervisor_running': True, 'supervisor_port': 5101})
    monkeypatch.setattr(onboarding, 'read_supervisor_port_file', lambda: None)

    assert onboarding._ensure_supervisor_running() == {'token': '', 'port': 5101}
