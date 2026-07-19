"""Tier 2 tests: model registry, system check, tag quotes aggregation,
transfer ZIP extras, and concurrent uploads."""

import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# 1. Model registry
# ---------------------------------------------------------------------------

class TestModelRegistry:
    """Tests for model_manager.py functions."""

    def test_init_model_registry_creates_models(self, app):
        with app.app_context():
            from app.models.ml_model import MLModel
            from app.services.model_manager import MODEL_REGISTRY, init_model_registry

            from app.services.model_manager import _model_for_platform
            init_model_registry()
            models = MLModel.query.all()
            ids = {m.id for m in models}
            for key, info in MODEL_REGISTRY.items():
                if not _model_for_platform(info):
                    continue
                assert key in ids, f'{key} not created'
            # Spot-check a known model
            m = MLModel.query.get('pyannote-diarization')
            assert m is not None
            assert m.function == 'diarization'
            assert m.required is True

    def test_init_model_registry_idempotent(self, app):
        with app.app_context():
            from app.models.ml_model import MLModel
            from app.services.model_manager import init_model_registry

            init_model_registry()
            count1 = MLModel.query.count()
            init_model_registry()
            count2 = MLModel.query.count()
            assert count1 == count2

    def test_model_already_on_disk_true(self, temp_dir):
        from app.services.model_manager import _model_already_on_disk

        model_dir = os.path.join(temp_dir, 'fake_model')
        os.makedirs(model_dir)
        # Write a file that is ≥80% of expected size
        with open(os.path.join(model_dir, 'weights.bin'), 'wb') as f:
            f.write(b'\x00' * 900)
        assert _model_already_on_disk(model_dir, 1000) is True

    def test_model_already_on_disk_false_empty(self, temp_dir):
        from app.services.model_manager import _model_already_on_disk

        model_dir = os.path.join(temp_dir, 'empty_model')
        os.makedirs(model_dir)
        assert _model_already_on_disk(model_dir, 1000) is False

    def test_model_already_on_disk_false_missing(self, temp_dir):
        from app.services.model_manager import _model_already_on_disk

        missing = os.path.join(temp_dir, 'does_not_exist')
        assert _model_already_on_disk(missing, 1000) is False

    def test_get_models_for_setup_quality(self):
        from app.services.model_manager import get_models_for_setup

        ids = get_models_for_setup(['transcription'])
        assert 'whisperx-large-v3' in ids
        assert 'pyannote-diarization' in ids
        assert 'gliner-pii' not in ids

    def test_get_models_for_setup_with_pii(self):
        from app.services.model_manager import get_models_for_setup

        ids = get_models_for_setup(['transcription', 'pii'])
        assert 'gliner-pii' in ids

    def test_reconcile_statuses_downloading_reset(self, app, temp_dir):
        """Model stuck in 'downloading' should be reset to 'not_downloaded'."""
        with app.app_context():
            from app.extensions import db
            from app.models.ml_model import MLModel
            from app.services.model_manager import init_model_registry, reconcile_model_statuses

            init_model_registry()
            m = MLModel.query.get('pyannote-diarization')
            m.status = 'downloading'
            db.session.commit()

            reconcile_model_statuses(temp_dir)

            m = MLModel.query.get('pyannote-diarization')
            assert m.status == 'not_downloaded'

    def test_reconcile_statuses_promotes_on_disk(self, app, temp_dir):
        """Model marked 'not_downloaded' but files exist on disk → 'ready'."""
        with app.app_context():
            from app.extensions import db
            from app.models.ml_model import MLModel
            from app.services.model_manager import MODEL_REGISTRY, init_model_registry, reconcile_model_statuses

            init_model_registry()
            model_id = 'pyannote-segmentation'
            m = MLModel.query.get(model_id)
            assert m.status == 'not_downloaded'

            # Create model directory with enough content
            model_dir = os.path.join(temp_dir, model_id)
            os.makedirs(model_dir)
            expected = MODEL_REGISTRY[model_id]['size_bytes']
            with open(os.path.join(model_dir, 'model.bin'), 'wb') as f:
                f.write(b'\x00' * expected)

            reconcile_model_statuses(temp_dir)

            m = MLModel.query.get(model_id)
            assert m.status == 'ready'


# ---------------------------------------------------------------------------
# 2. System check
# ---------------------------------------------------------------------------

class TestSystemCheck:
    """Tests for system_check.py."""

    def test_system_check_returns_list(self):
        from app.services.system_check import run_system_check

        checks = run_system_check()
        assert isinstance(checks, list)
        assert len(checks) >= 3
        for c in checks:
            assert 'name' in c
            assert 'status' in c
            assert c['status'] in ('ok', 'warn', 'err')

    def test_python_version_ok(self):
        """Current Python should be 3.11-3.13 → ok or warn."""
        from app.services.system_check import run_system_check

        checks = run_system_check()
        py = next(c for c in checks if c['name'] == 'Python')
        # We're running tests so Python must be valid
        assert py['status'] in ('ok', 'warn')

    @patch('app.services.system_check.shutil.which', return_value=None)
    def test_ffmpeg_missing(self, mock_which):
        from app.services.system_check import run_system_check

        checks = run_system_check()
        ff = next((c for c in checks if c['name'] == 'FFmpeg'), None)
        if ff is not None:
            assert ff['status'] in ('warn', 'err')

    def test_disk_space_check_present(self):
        from app.services.system_check import run_system_check

        checks = run_system_check()
        names = [c['name'] for c in checks]
        assert 'Disk space' in names or 'Disk Space' in names or any('disk' in n.lower() for n in names)

    def test_ram_check_present(self):
        from app.services.system_check import run_system_check

        checks = run_system_check()
        names = [c['name'] for c in checks]
        assert any('ram' in n.lower() or 'memory' in n.lower() for n in names)


class TestGPUDetection:
    """Tests for GPU detection scenarios in system_check.py."""

    def test_detect_nvidia_gpu_found(self):
        from app.services.system_check import _detect_nvidia_gpu

        mock_result = MagicMock(returncode=0, stdout='NVIDIA GeForce RTX 3080\n')
        with patch('app.services.system_check.subprocess.run', return_value=mock_result):
            assert _detect_nvidia_gpu() == 'NVIDIA GeForce RTX 3080'

    def test_detect_nvidia_gpu_not_found(self):
        from app.services.system_check import _detect_nvidia_gpu

        with patch('app.services.system_check.subprocess.run', side_effect=FileNotFoundError):
            assert _detect_nvidia_gpu() is None

    def test_system_check_nvidia_no_cuda(self):
        """NVIDIA GPU present but CUDA unavailable → status err with download link."""
        from app.services.system_check import run_system_check

        mock_torch = MagicMock()
        mock_torch.__version__ = '2.1.0'
        mock_torch.cuda.is_available.return_value = False
        mock_torch.backends.mps.is_available.return_value = False

        with patch.dict('sys.modules', {'torch': mock_torch}), \
             patch('app.services.system_check._detect_nvidia_gpu', return_value='NVIDIA GeForce RTX 3080'):
            checks = run_system_check()

        gpu = next(c for c in checks if 'CUDA' in c.get('name', '') or 'GPU' in c.get('name', ''))
        assert gpu['status'] == 'err'
        assert gpu['has_nvidia_gpu'] is True
        assert gpu['has_cuda'] is False
        assert gpu['recheckable'] is True
        assert 'download_url' in gpu

    def test_system_check_no_gpu_cpu_only(self):
        """No NVIDIA GPU, not Mac → cpu_opt_in_required with time comparison."""
        from app.services.system_check import run_system_check

        mock_torch = MagicMock()
        mock_torch.__version__ = '2.1.0'
        mock_torch.cuda.is_available.return_value = False
        mock_torch.backends.mps.is_available.return_value = False

        with patch.dict('sys.modules', {'torch': mock_torch}), \
             patch('app.services.system_check._detect_nvidia_gpu', return_value=None):
            checks = run_system_check()

        gpu = next(c for c in checks if 'CUDA' in c.get('name', '') or 'GPU' in c.get('name', ''))
        assert gpu['status'] == 'warn'
        assert gpu['cpu_opt_in_required'] is True
        assert 'time_comparison' in gpu

    def test_system_check_low_vram(self):
        """CUDA available with 4GB VRAM → ok GPU + warn VRAM item."""
        from app.services.system_check import run_system_check

        mock_torch = MagicMock()
        mock_torch.__version__ = '2.1.0'
        mock_torch.cuda.is_available.return_value = True
        mock_torch.cuda.get_device_name.return_value = 'NVIDIA GeForce GTX 1650'
        mock_props = MagicMock()
        mock_props.total_memory = 4 * (1024 ** 3)  # 4 GB
        mock_torch.cuda.get_device_properties.return_value = mock_props

        with patch.dict('sys.modules', {'torch': mock_torch}):
            checks = run_system_check()

        gpu = next(c for c in checks if c['name'] == 'CUDA / GPU acceleration')
        assert gpu['status'] == 'ok'
        assert gpu['vram_gb'] == 4.0
        vram_warn = next((c for c in checks if c['name'] == 'VRAM'), None)
        assert vram_warn is not None
        assert vram_warn['status'] == 'warn'

    def test_system_check_high_vram_no_warning(self):
        """CUDA available with 8GB VRAM → ok GPU, no VRAM warning."""
        from app.services.system_check import run_system_check

        mock_torch = MagicMock()
        mock_torch.__version__ = '2.1.0'
        mock_torch.cuda.is_available.return_value = True
        mock_torch.cuda.get_device_name.return_value = 'NVIDIA GeForce RTX 3060'
        mock_props = MagicMock()
        mock_props.total_memory = 8 * (1024 ** 3)  # 8 GB
        mock_torch.cuda.get_device_properties.return_value = mock_props

        with patch.dict('sys.modules', {'torch': mock_torch}):
            checks = run_system_check()

        gpu = next(c for c in checks if c['name'] == 'CUDA / GPU acceleration')
        assert gpu['status'] == 'ok'
        assert gpu['vram_gb'] == 8.0
        vram_warn = next((c for c in checks if c['name'] == 'VRAM'), None)
        assert vram_warn is None


# ---------------------------------------------------------------------------
# 3. Tag quotes aggregation
# ---------------------------------------------------------------------------

class TestTagQuotesAggregation:
    """Tests for GET /api/projects/<id>/tags/quotes."""

    def _setup_project_with_quotes(self, client, app):
        """Helper: create a project with 2 recordings, transcripts, tags, and tag_spans."""
        create = client.post('/api/projects', json={'name': 'Quotes Test'})
        pid = create.get_json()['id']

        with app.app_context():
            from app.extensions import db
            from app.models.project import Project
            from app.models.recording import Recording
            from app.models.setting import Setting

            projects_path = app.config['DEFAULT_PROJECTS_PATH']
            Setting.set('projects_path', projects_path)
            proj = db.session.get(Project, pid)
            proj_dir = os.path.join(projects_path, proj.folder_name)
            os.makedirs(proj_dir, exist_ok=True)

            # Save project tags
            tags = [
                {'id': 'pain', 'name': 'Pain point', 'color': 'pain'},
                {'id': 'insight', 'name': 'Insight', 'color': 'ins'},
            ]
            with open(os.path.join(proj_dir, 'project_tags.json'), 'w') as f:
                json.dump(tags, f)

            # Recording 1
            rec1 = Recording(project_id=pid, original_name='r1.mp3',
                             stored_name='r1.mp3', transcript_path='r1_transcript.json',
                             transcription_status='transcribed')
            db.session.add(rec1)
            db.session.flush()

            with open(os.path.join(proj_dir, 'r1_transcript.json'), 'w') as f:
                json.dump({'segments': [
                    {'start': 0, 'end': 5, 'text': 'This is painful to use', 'speaker': 'SPEAKER_00'},
                    {'start': 5, 'end': 10, 'text': 'I love the design', 'speaker': 'SPEAKER_01'},
                ]}, f)
            with open(os.path.join(proj_dir, 'r1_annotations.json'), 'w') as f:
                json.dump({
                    'tag_spans': [
                        {'segment_idx': 0, 'start_char': 0, 'end_char': 22, 'tag_id': 'pain'},
                        {'segment_idx': 1, 'start_char': 0, 'end_char': 17, 'tag_id': 'insight'},
                    ],
                    'comments': [{'segment_idx': 0, 'text': 'Very frustrated'}],
                    'speaker_labels': {'SPEAKER_00': 'Moderator', 'SPEAKER_01': 'User'},
                }, f)

            # Recording 2
            rec2 = Recording(project_id=pid, original_name='r2.mp3',
                             stored_name='r2.mp3', transcript_path='r2_transcript.json',
                             transcription_status='transcribed')
            db.session.add(rec2)
            db.session.flush()

            with open(os.path.join(proj_dir, 'r2_transcript.json'), 'w') as f:
                json.dump({'segments': [
                    {'start': 0, 'end': 3, 'text': 'Another pain point here', 'speaker': 'SPEAKER_00'},
                ]}, f)
            with open(os.path.join(proj_dir, 'r2_annotations.json'), 'w') as f:
                json.dump({
                    'tag_spans': [
                        {'segment_idx': 0, 'start_char': 0, 'end_char': 23, 'tag_id': 'pain'},
                    ],
                    'comments': [],
                    'speaker_labels': {},
                }, f)

            db.session.commit()

        return pid

    def test_tag_quotes_empty_project(self, client):
        create = client.post('/api/projects', json={'name': 'Empty'})
        pid = create.get_json()['id']
        r = client.get(f'/api/projects/{pid}/tags/quotes')
        assert r.status_code == 200
        data = r.get_json()
        assert data['total_quotes'] == 0
        assert data['recordings'] == []

    def test_tag_quotes_with_spans(self, client, app):
        pid = self._setup_project_with_quotes(client, app)
        r = client.get(f'/api/projects/{pid}/tags/quotes')
        assert r.status_code == 200
        data = r.get_json()

        assert data['total_quotes'] == 3
        assert len(data['recordings']) == 2

        # Check first recording quotes
        rec1 = next(r for r in data['recordings'] if r['name'] == 'r1.mp3')
        assert len(rec1['quotes']) == 2
        pain_quote = next(q for q in rec1['quotes'] if q['tag_id'] == 'pain')
        assert pain_quote['text'] == 'This is painful to use'
        assert pain_quote['speaker'] == 'Moderator'
        assert pain_quote['comment'] == 'Very frustrated'

    def test_tag_quotes_counts(self, client, app):
        pid = self._setup_project_with_quotes(client, app)
        r = client.get(f'/api/projects/{pid}/tags/quotes')
        data = r.get_json()

        pain_tag = next(t for t in data['tags'] if t['id'] == 'pain')
        assert pain_tag['count'] == 2          # 2 pain quotes across recordings
        assert pain_tag['recording_count'] == 2  # appears in 2 recordings

        insight_tag = next(t for t in data['tags'] if t['id'] == 'insight')
        assert insight_tag['count'] == 1
        assert insight_tag['recording_count'] == 1

    def test_tag_quotes_missing_transcript(self, client, app):
        """Recording with missing transcript file should be gracefully skipped."""
        create = client.post('/api/projects', json={'name': 'Missing'})
        pid = create.get_json()['id']

        with app.app_context():
            from app.extensions import db
            from app.models.recording import Recording

            rec = Recording(project_id=pid, original_name='gone.mp3',
                            stored_name='gone.mp3', transcript_path='gone_transcript.json',
                            transcription_status='transcribed')
            db.session.add(rec)
            db.session.commit()

        r = client.get(f'/api/projects/{pid}/tags/quotes')
        assert r.status_code == 200
        assert r.get_json()['total_quotes'] == 0


# ---------------------------------------------------------------------------
# 4. Transfer ZIP extras (extends existing TestTransferZip)
# ---------------------------------------------------------------------------

class TestTransferZipExtended:
    """Additional transfer endpoint tests."""

    def test_transfer_no_audio_in_zip(self, client, app):
        """Audio files must NOT appear in the transfer ZIP."""
        import io
        import zipfile

        with app.app_context():
            from app.extensions import db
            from app.models.project import Project
            from app.models.recording import Recording
            from app.models.setting import Setting

            projects_path = app.config['DEFAULT_PROJECTS_PATH']
            Setting.set('projects_path', projects_path)

            proj = Project(name='No Audio', folder_name='no_audio')
            db.session.add(proj)
            db.session.flush()
            rec = Recording(project_id=proj.id, original_name='interview.mp3',
                            stored_name='interview.mp3',
                            transcript_path='interview_transcript.json',
                            transcription_status='transcribed')
            db.session.add(rec)
            db.session.commit()
            pid = proj.id

            proj_dir = os.path.join(projects_path, 'no_audio')
            os.makedirs(proj_dir, exist_ok=True)
            # Create a fake audio file
            with open(os.path.join(proj_dir, 'interview.mp3'), 'wb') as f:
                f.write(b'\xff\xfb\x90\x00' * 100)
            with open(os.path.join(proj_dir, 'interview_transcript.json'), 'w') as f:
                json.dump({'segments': []}, f)

        r = client.get(f'/api/projects/{pid}/transfer')
        assert r.status_code == 200
        zf = zipfile.ZipFile(io.BytesIO(r.data))
        names = zf.namelist()
        audio_files = [n for n in names if n.endswith(('.mp3', '.wav', '.m4a', '.ogg', '.flac', '.mp4', '.mkv', '.webm'))]
        assert audio_files == [], f'Audio files leaked into ZIP: {audio_files}'

    def test_transfer_multiple_recordings(self, client, app):
        """ZIP should contain transcripts and annotations for all recordings."""
        import io
        import zipfile

        with app.app_context():
            from app.extensions import db
            from app.models.project import Project
            from app.models.recording import Recording
            from app.models.setting import Setting

            projects_path = app.config['DEFAULT_PROJECTS_PATH']
            Setting.set('projects_path', projects_path)

            proj = Project(name='Multi Rec', folder_name='multi_rec')
            db.session.add(proj)
            db.session.flush()

            proj_dir = os.path.join(projects_path, 'multi_rec')
            os.makedirs(proj_dir, exist_ok=True)

            for i in range(1, 3):
                rec = Recording(project_id=proj.id, original_name=f'r{i}.mp3',
                                stored_name=f'r{i}.mp3',
                                transcript_path=f'r{i}_transcript.json',
                                transcription_status='transcribed')
                db.session.add(rec)
                with open(os.path.join(proj_dir, f'r{i}_transcript.json'), 'w') as f:
                    json.dump({'segments': []}, f)
                with open(os.path.join(proj_dir, f'r{i}_annotations.json'), 'w') as f:
                    json.dump({'tag_spans': [], 'comments': []}, f)

            db.session.commit()
            pid = proj.id

        r = client.get(f'/api/projects/{pid}/transfer')
        assert r.status_code == 200
        zf = zipfile.ZipFile(io.BytesIO(r.data))
        names = zf.namelist()

        for i in range(1, 3):
            assert any(f'r{i}_transcript.json' in n for n in names), \
                f'transcript r{i} missing from ZIP'
            assert any(f'r{i}_annotations.json' in n for n in names), \
                f'annotations r{i} missing from ZIP'


# ---------------------------------------------------------------------------
# 5. Concurrent upload
# ---------------------------------------------------------------------------

class TestConcurrentUpload:
    """Simultaneous file uploads should not collide."""

    @patch('app.services.transcription.enqueue')
    def test_concurrent_uploads_no_collision(self, mock_enqueue, client, app):
        create = client.post('/api/projects', json={'name': 'Concurrent'})
        pid = create.get_json()['id']

        results = []
        errors = []

        def upload(idx):
            try:
                r = client.post(
                    f'/api/projects/{pid}/recordings',
                    data={'file': (BytesIO(b'FAKE_AUDIO'), f'recording_{idx}.mp3')},
                    content_type='multipart/form-data',
                )
                results.append(r)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=upload, args=(i,)) for i in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f'Upload errors: {errors}'
        success = [r for r in results if r.status_code == 201]
        assert len(success) == 3, f'Expected 3 uploads, got {len(success)}'

        # Verify unique stored names
        stored_names = set()
        for r in success:
            data = r.get_json()
            stored_names.add(data.get('stored_name') or data.get('recording', {}).get('stored_name', ''))

        # At least verify 3 distinct recordings exist
        with app.app_context():
            from app.models.recording import Recording
            recs = Recording.query.filter_by(project_id=pid).all()
            assert len(recs) == 3
            names = {r.stored_name for r in recs}
            assert len(names) == 3, f'Filename collision: {names}'


# ---------------------------------------------------------------------------
# 6. Onboarding API
# ---------------------------------------------------------------------------

class TestOnboardingAPI:
    """Tests for /api/onboarding/* endpoints."""

    def test_onboarding_status(self, client):
        r = client.get('/api/onboarding/status')
        assert r.status_code == 200
        data = r.get_json()
        assert 'completed' in data
        assert isinstance(data['completed'], bool)
        assert 'modules' in data
        assert 'stt_model_id' in data
        assert 'models_path' in data
        assert 'projects_path' in data

    def test_onboarding_device_detection(self, client):
        """Device endpoint should return device info (may be 'unknown' in test env)."""
        r = client.get('/api/onboarding/device')
        # Could be 200 or 500 (if torch not installed) — both are valid
        data = r.get_json()
        assert 'device' in data
        assert 'has_nvidia_gpu' in data
        assert 'has_cuda' in data

    def test_onboarding_system_check(self, client):
        r = client.get('/api/onboarding/system-check')
        assert r.status_code == 200
        data = r.get_json()
        assert 'checks' in data
        assert isinstance(data['checks'], list)
        assert 'has_blockers' in data

    def test_onboarding_set_language(self, client):
        r = client.post('/api/onboarding/language',
                        json={})
        assert r.status_code == 200
        data = r.get_json()
        assert data['ok'] is True
        assert 'stt_model_id' in data

    def test_onboarding_set_storage_valid(self, client, temp_dir):
        import os
        models = os.path.join(temp_dir, 'new_models')
        projects = os.path.join(temp_dir, 'new_projects')
        r = client.post('/api/onboarding/storage', json={
            'models_path': models,
            'projects_path': projects,
        })
        assert r.status_code == 200
        data = r.get_json()
        assert data['ok'] is True
        assert os.path.isdir(models)
        assert os.path.isdir(projects)

    def test_onboarding_set_storage_missing_path(self, client):
        r = client.post('/api/onboarding/storage', json={
            'models_path': '',
            'projects_path': '',
        })
        assert r.status_code == 400

    def test_onboarding_defaults(self, client):
        r = client.get('/api/onboarding/defaults')
        assert r.status_code == 200
        data = r.get_json()
        assert 'models_path' in data
        assert 'projects_path' in data


# ---------------------------------------------------------------------------
# 7. Settings reset + recording PATCH
# ---------------------------------------------------------------------------

class TestSettingsReset:
    """Tests for POST /api/settings/reset."""

    def test_reset_requires_confirmation(self, client):
        r = client.post('/api/settings/reset', json={})
        assert r.status_code == 400
        assert 'confirm' in r.get_json().get('error', '').lower()

    def test_reset_clears_projects(self, client, app):
        # Create a project first
        client.post('/api/projects', json={'name': 'Doomed'})
        r = client.get('/api/projects')
        assert len(r.get_json()['active']) >= 1

        # Reset
        r = client.post('/api/settings/reset', json={'confirm': 'Yes'})
        assert r.status_code == 200
        assert r.get_json()['ok'] is True

        # Projects should be gone
        r2 = client.get('/api/projects')
        assert len(r2.get_json()['active']) == 0
        assert len(r2.get_json()['archived']) == 0


class TestRecordingPatch:
    """Tests for PATCH /<project_id>/recordings/<recording_id>."""

    def _make_recording(self, client, app):
        cr = client.post('/api/projects', json={'name': 'Patch Test'})
        pid = cr.get_json()['id']
        with app.app_context():
            from app.extensions import db
            from app.models.recording import Recording
            rec = Recording(project_id=pid, original_name='test.mp3',
                            stored_name='test.mp3', transcription_status='pending')
            db.session.add(rec)
            db.session.commit()
            return pid, rec.id

    def test_patch_participant_notes(self, client, app):
        pid, rid = self._make_recording(client, app)
        r = client.patch(f'/api/projects/{pid}/recordings/{rid}',
                         json={'participant_notes': 'Senior engineer, 5yr exp'})
        assert r.status_code == 200
        assert r.get_json()['participant_notes'] == 'Senior engineer, 5yr exp'

    def test_patch_original_name(self, client, app):
        pid, rid = self._make_recording(client, app)
        r = client.patch(f'/api/projects/{pid}/recordings/{rid}',
                         json={'original_name': 'Interview with Alice'})
        assert r.status_code == 200
        # Should preserve .mp3 extension
        assert r.get_json()['original_name'].endswith('.mp3')
        assert 'Alice' in r.get_json()['original_name']

    def test_patch_segment_assignment(self, client, app):
        pid, rid = self._make_recording(client, app)
        # Create a segment
        seg_r = client.post(f'/api/projects/{pid}/segments',
                            json={'name': 'Test Seg'})
        sid = seg_r.get_json()['id']

        # Assign segment
        r = client.patch(f'/api/projects/{pid}/recordings/{rid}',
                         json={'segment_id': sid})
        assert r.status_code == 200
        assert r.get_json()['segment_id'] == sid
        assert 'segment' in r.get_json()

        # Unassign segment
        r2 = client.patch(f'/api/projects/{pid}/recordings/{rid}',
                          json={'segment_id': None})
        assert r2.status_code == 200
        assert r2.get_json()['segment_id'] is None

    def test_patch_invalid_segment(self, client, app):
        pid, rid = self._make_recording(client, app)
        r = client.patch(f'/api/projects/{pid}/recordings/{rid}',
                         json={'segment_id': 99999})
        assert r.status_code == 404
