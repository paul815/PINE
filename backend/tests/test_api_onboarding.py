"""API integration tests for onboarding endpoints."""

import os
from unittest.mock import patch

import pytest


class TestOnboardingAPI:

    def test_onboarding_status(self, client):
        # GET /api/onboarding/status - should return 200 with expected keys
        r = client.get('/api/onboarding/status')
        assert r.status_code == 200
        data = r.get_json()
        assert 'completed' in data
        assert 'modules' in data

    def test_onboarding_defaults(self, client):
        # GET /api/onboarding/defaults - returns paths and disk info
        r = client.get('/api/onboarding/defaults')
        assert r.status_code == 200
        data = r.get_json()
        assert 'models_path' in data
        assert 'projects_path' in data
        assert 'disk_total_bytes' in data

    def test_onboarding_storage_success(self, client, temp_dir):
        # POST /api/onboarding/storage with valid temp paths
        models_path = os.path.join(temp_dir, 'models')
        projects_path = os.path.join(temp_dir, 'projects')
        os.makedirs(models_path, exist_ok=True)
        os.makedirs(projects_path, exist_ok=True)
        r = client.post('/api/onboarding/storage', json={
            'models_path': models_path,
            'projects_path': projects_path,
        })
        assert r.status_code == 200

    def test_onboarding_storage_missing_paths(self, client):
        # POST /api/onboarding/storage with empty body
        r = client.post('/api/onboarding/storage', json={})
        assert r.status_code == 400

    @patch('app.api.onboarding.run_system_check', return_value=[])
    def test_onboarding_system_check(self, mock_check, client):
        r = client.get('/api/onboarding/system-check')
        assert r.status_code == 200
        data = r.get_json()
        assert 'checks' in data
        assert 'has_blockers' in data

    @patch('app.api.onboarding.get_models_for_setup', return_value=[])
    def test_onboarding_modules(self, mock_models, client):
        r = client.post('/api/onboarding/modules', json={
            'modules': ['transcription'],
        })
        assert r.status_code == 200
        data = r.get_json()
        assert data.get('ok') is True

    def test_onboarding_download_status(self, client):
        r = client.get('/api/onboarding/download/status')
        assert r.status_code == 200
        data = r.get_json()
        assert 'models' in data
        assert isinstance(data['models'], list)

    @patch('app.api.onboarding.get_models_for_setup', return_value=[])
    @patch('app.api.onboarding.get_default_stt_model', return_value='whisper-large-v3')
    def test_onboarding_language(self, mock_stt, mock_models, client):
        r = client.post('/api/onboarding/language', json={})
        assert r.status_code == 200
        data = r.get_json()
        assert data.get('ok') is True
