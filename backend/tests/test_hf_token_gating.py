"""Unit tests for HuggingFace token validation + gated-repo pre-check.

Covers the onboarding improvement where ``validate_hf_token`` verifies access to
the gated pyannote repos (via ``HfApi.auth_check``) instead of only ``whoami`` —
so a missing license is surfaced at the token step rather than failing deep
inside the multi-GB model download.
"""

from unittest.mock import patch

import requests
from huggingface_hub.utils import GatedRepoError

from app.services import model_manager


def _gated_error(repo_id):
    """Build a GatedRepoError the way huggingface_hub actually raises it.

    Recent huggingface_hub requires a ``response`` argument (HfHubHTTPError);
    constructing it with only a message raises TypeError, which would not
    exercise the ``except GatedRepoError`` path we want to verify.
    """
    resp = requests.Response()
    resp.status_code = 403
    try:
        return GatedRepoError(f'{repo_id} is gated', response=resp)
    except TypeError:  # pragma: no cover - older huggingface_hub signature
        return GatedRepoError(f'{repo_id} is gated')


class _FakeApi:
    """Stand-in for ``huggingface_hub.HfApi`` driven by the test's expectations."""

    def __init__(self, *, whoami_name='tester', blocked=None, raise_whoami=None):
        self._name = whoami_name
        self._blocked = set(blocked or [])
        self._raise_whoami = raise_whoami

    def whoami(self):
        if self._raise_whoami:
            raise self._raise_whoami
        return {'name': self._name}

    def auth_check(self, repo_id):
        if repo_id in self._blocked:
            raise _gated_error(repo_id)
        return None


def test_gated_repos_are_the_pyannote_stack():
    repos = model_manager._gated_pyannote_repos()
    # Derived from MODEL_REGISTRY so the pre-check can never drift from what we
    # actually download.
    assert repos, 'expected at least one gated pyannote repo'
    assert all(r.startswith('pyannote/') for r in repos)


def test_validate_hf_token_all_accessible():
    fake = _FakeApi()
    with patch('huggingface_hub.HfApi', return_value=fake):
        result = model_manager.validate_hf_token('hf_dummy')
    assert result['valid'] is True
    assert result['username'] == 'tester'
    assert result['gated_ok'] is True
    repos = {r['repo_id'] for r in result['gated_repos']}
    assert repos == set(model_manager._gated_pyannote_repos())
    assert all(r['accessible'] is True for r in result['gated_repos'])


def test_validate_hf_token_license_not_accepted():
    gated = model_manager._gated_pyannote_repos()
    blocked_repo = gated[0]
    fake = _FakeApi(blocked=[blocked_repo])
    with patch('huggingface_hub.HfApi', return_value=fake):
        result = model_manager.validate_hf_token('hf_dummy')
    assert result['valid'] is True
    assert result['gated_ok'] is False
    blocked = [r for r in result['gated_repos'] if r['accessible'] is False]
    assert [r['repo_id'] for r in blocked] == [blocked_repo]
    assert blocked[0]['reason'] == 'license_not_accepted'
    assert blocked[0]['url'].endswith(blocked_repo)


def test_validate_hf_token_bad_token():
    fake = _FakeApi(raise_whoami=ValueError('401 unauthorized'))
    with patch('huggingface_hub.HfApi', return_value=fake):
        result = model_manager.validate_hf_token('bad')
    assert result['valid'] is False
    assert 'error' in result
    # No gated probing happens when the token itself does not authenticate.
    assert 'gated_ok' not in result
