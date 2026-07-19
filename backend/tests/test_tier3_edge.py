"""Tier 3 edge-case tests: corrupted files, disk limits, unusual inputs."""

import json
import os
from unittest.mock import patch

import pytest


class TestCorruptedAnnotationJson:
    """Corrupted annotation JSON should fall back to defaults, never crash."""

    def test_corrupted_json_returns_defaults(self, project_dir):
        from app.services.annotations import get_annotations

        # Write garbage to the annotation file
        ann_path = os.path.join(project_dir, 'bad_annotations.json')
        with open(ann_path, 'w') as f:
            f.write('{{{invalid json!!!}}}')

        result = get_annotations(project_dir, 'bad.mp3')
        assert result == {'tag_spans': [], 'comments': [], 'speaker_labels': {}}

    def test_empty_file_returns_defaults(self, project_dir):
        from app.services.annotations import get_annotations

        ann_path = os.path.join(project_dir, 'empty_annotations.json')
        with open(ann_path, 'w') as f:
            f.write('')

        result = get_annotations(project_dir, 'empty.mp3')
        assert result == {'tag_spans': [], 'comments': [], 'speaker_labels': {}}

    def test_corrupted_project_tags_returns_empty(self, project_dir):
        from app.services.annotations import get_project_tags

        tags_path = os.path.join(project_dir, 'project_tags.json')
        with open(tags_path, 'w') as f:
            f.write('NOT JSON')

        result = get_project_tags(project_dir)
        assert result == []


class TestDiskFullAnnotationSave:
    """Disk-full errors during annotation save should propagate and clean up."""

    def test_atomic_write_cleans_up_on_error(self, project_dir):
        from app.services.annotations import save_annotations

        # Mock os.fdopen to raise OSError (simulating disk full)
        original_fdopen = os.fdopen
        call_count = 0

        def failing_fdopen(fd, *args, **kwargs):
            nonlocal call_count
            call_count += 1
            # Close the fd so it doesn't leak, then raise
            os.close(fd)
            raise OSError('No space left on device')

        with patch('app.services.annotations.os.fdopen', side_effect=failing_fdopen):
            with pytest.raises(OSError, match='No space'):
                save_annotations(project_dir, 'test.mp3',
                                 {'tag_spans': [], 'comments': [], 'speaker_labels': {}})

        # Verify no .tmp files left behind
        tmp_files = [f for f in os.listdir(project_dir) if f.endswith('.tmp')]
        assert tmp_files == [], f'Temp files not cleaned up: {tmp_files}'


class TestVeryLongProjectName:
    """A 500-character project name should not break folder creation."""

    def test_long_name_creates_project(self, client):
        long_name = 'A' * 500
        r = client.post('/api/projects', json={'name': long_name})
        assert r.status_code == 201
        data = r.get_json()
        # folder_name should be truncated to a sane length
        assert len(data['folder_name']) <= 255
        assert data['name'] == long_name

    def test_long_name_folder_exists(self, client, app):
        long_name = 'B' * 500
        r = client.post('/api/projects', json={'name': long_name})
        pid = r.get_json()['id']

        with app.app_context():
            from app.extensions import db
            from app.models.project import Project

            proj = db.session.get(Project, pid)
            folder = os.path.join(app.config['DEFAULT_PROJECTS_PATH'], proj.folder_name)
            assert os.path.isdir(folder)


class TestZeroDurationRecording:
    """A recording with 0-second transcript should not cause division errors."""

    def test_export_zero_duration(self, client, app):
        """Export a recording with 0-second duration — no crash."""
        with app.app_context():
            from app.extensions import db
            from app.models.project import Project
            from app.models.recording import Recording
            from app.models.setting import Setting

            projects_path = app.config['DEFAULT_PROJECTS_PATH']
            Setting.set('projects_path', projects_path)

            proj = Project(name='Zero Dur', folder_name='zero_dur')
            db.session.add(proj)
            db.session.flush()
            rec = Recording(project_id=proj.id, original_name='z.mp3',
                            stored_name='z.mp3',
                            transcript_path='z_transcript.json',
                            duration_seconds=0,
                            transcription_status='transcribed')
            db.session.add(rec)
            db.session.commit()
            pid, rid = proj.id, rec.id

            proj_dir = os.path.join(projects_path, 'zero_dur')
            os.makedirs(proj_dir, exist_ok=True)
            with open(os.path.join(proj_dir, 'z_transcript.json'), 'w') as f:
                json.dump({'segments': [], 'duration_seconds': 0}, f)

        r = client.post(f'/api/projects/{pid}/recordings/{rid}/export',
                        json={'format': 'markdown'})
        assert r.status_code == 200

    def test_tag_quotes_zero_duration(self, client, app):
        """Tag quotes endpoint handles 0-duration recording without error."""
        with app.app_context():
            from app.extensions import db
            from app.models.project import Project
            from app.models.recording import Recording
            from app.models.setting import Setting

            projects_path = app.config['DEFAULT_PROJECTS_PATH']
            Setting.set('projects_path', projects_path)

            proj = Project(name='Zero Quotes', folder_name='zero_quotes')
            db.session.add(proj)
            db.session.flush()
            rec = Recording(project_id=proj.id, original_name='z2.mp3',
                            stored_name='z2.mp3',
                            transcript_path='z2_transcript.json',
                            duration_seconds=0,
                            transcription_status='transcribed')
            db.session.add(rec)
            db.session.commit()
            pid = proj.id

            proj_dir = os.path.join(projects_path, 'zero_quotes')
            os.makedirs(proj_dir, exist_ok=True)
            with open(os.path.join(proj_dir, 'z2_transcript.json'), 'w') as f:
                json.dump({'segments': [
                    {'start': 0, 'end': 0, 'text': 'Empty', 'speaker': 'S'}
                ], 'duration_seconds': 0}, f)

        r = client.get(f'/api/projects/{pid}/tags/quotes')
        assert r.status_code == 200


class TestDeleteProjectWithPendingTranscription:
    """Deleting a project with a pending/transcribing recording should clean up."""

    @patch('app.services.transcription.enqueue')
    def test_delete_with_pending_recording(self, mock_enqueue, client, app):
        create = client.post('/api/projects', json={'name': 'Pending Del'})
        pid = create.get_json()['id']

        with app.app_context():
            from app.extensions import db
            from app.models.project import Project
            from app.models.recording import Recording

            rec = Recording(project_id=pid, original_name='p.mp3',
                            stored_name='p.mp3',
                            transcription_status='pending')
            db.session.add(rec)
            db.session.commit()
            rid = rec.id

            proj = db.session.get(Project, pid)
            folder = os.path.join(app.config['DEFAULT_PROJECTS_PATH'], proj.folder_name)

        assert os.path.isdir(folder)

        r = client.delete(f'/api/projects/{pid}')
        assert r.status_code == 200

        # Folder should be gone
        assert not os.path.isdir(folder)

        # DB records should be gone
        with app.app_context():
            from app.extensions import db
            from app.models.project import Project
            from app.models.recording import Recording

            assert db.session.get(Project, pid) is None
            assert db.session.get(Recording, rid) is None

    @patch('app.services.transcription.enqueue')
    def test_delete_with_transcribing_recording(self, mock_enqueue, client, app):
        """Delete project while a recording is in 'transcribing' status."""
        create = client.post('/api/projects', json={'name': 'Transcribing Del'})
        pid = create.get_json()['id']

        with app.app_context():
            from app.extensions import db
            from app.models.recording import Recording

            rec = Recording(project_id=pid, original_name='t.mp3',
                            stored_name='t.mp3',
                            transcription_status='transcribing')
            db.session.add(rec)
            db.session.commit()

        r = client.delete(f'/api/projects/{pid}')
        assert r.status_code == 200

        r2 = client.get(f'/api/projects/{pid}')
        assert r2.status_code == 404


# ---------------------------------------------------------------------------
# Link recording edge cases
# ---------------------------------------------------------------------------

class TestLinkRecording:
    """Tests for POST /<project_id>/recordings/link."""

    def test_link_nonexistent_file(self, client):
        cr = client.post('/api/projects', json={'name': 'Link Test'})
        pid = cr.get_json()['id']

        r = client.post(f'/api/projects/{pid}/recordings/link',
                        json={'path': '/nonexistent/fake_audio.mp3'})
        assert r.status_code == 400
        assert 'not found' in r.get_json()['error'].lower()

    def test_link_missing_path(self, client):
        cr = client.post('/api/projects', json={'name': 'Link Empty'})
        pid = cr.get_json()['id']

        r = client.post(f'/api/projects/{pid}/recordings/link', json={})
        assert r.status_code == 400

    @patch('app.services.transcription.enqueue')
    def test_link_valid_file(self, mock_enqueue, client, temp_dir):
        cr = client.post('/api/projects', json={'name': 'Link OK'})
        pid = cr.get_json()['id']

        # Create a real audio file to link
        audio_path = os.path.join(temp_dir, 'interview.mp3')
        with open(audio_path, 'wb') as f:
            f.write(b'\xff\xfb\x90\x00' * 100)

        r = client.post(f'/api/projects/{pid}/recordings/link',
                        json={'path': audio_path})
        assert r.status_code == 201
        data = r.get_json()
        assert data['is_linked'] is True
        assert data['original_name'] == 'interview.mp3'
        assert data['transcription_status'] == 'pending'

    def test_link_unsupported_format(self, client, temp_dir):
        cr = client.post('/api/projects', json={'name': 'Link Bad Ext'})
        pid = cr.get_json()['id']

        txt_path = os.path.join(temp_dir, 'notes.txt')
        with open(txt_path, 'w') as f:
            f.write('Not audio')

        r = client.post(f'/api/projects/{pid}/recordings/link',
                        json={'path': txt_path})
        assert r.status_code == 400
        assert 'unsupported' in r.get_json()['error'].lower()


# ---------------------------------------------------------------------------
# Transcript retrieval edge cases
# ---------------------------------------------------------------------------

class TestGetTranscript:
    """Tests for GET /<project_id>/recordings/<recording_id>/transcript."""

    def test_transcript_not_transcribed(self, client, app):
        """Requesting transcript for a pending recording → 404."""
        cr = client.post('/api/projects', json={'name': 'Trans Test'})
        pid = cr.get_json()['id']

        with app.app_context():
            from app.extensions import db
            from app.models.recording import Recording
            rec = Recording(project_id=pid, original_name='t.mp3',
                            stored_name='t.mp3', transcription_status='pending')
            db.session.add(rec)
            db.session.commit()
            rid = rec.id

        r = client.get(f'/api/projects/{pid}/recordings/{rid}/transcript')
        assert r.status_code == 404

    def test_transcript_file_missing(self, client, app):
        """Recording marked transcribed but file deleted → 404."""
        cr = client.post('/api/projects', json={'name': 'Trans Missing'})
        pid = cr.get_json()['id']

        with app.app_context():
            from app.extensions import db
            from app.models.recording import Recording
            rec = Recording(project_id=pid, original_name='m.mp3',
                            stored_name='m.mp3',
                            transcript_path='m_transcript.json',
                            transcription_status='transcribed')
            db.session.add(rec)
            db.session.commit()
            rid = rec.id

        r = client.get(f'/api/projects/{pid}/recordings/{rid}/transcript')
        assert r.status_code == 404

    def test_transcript_valid(self, client, app):
        """Valid transcript file returns JSON."""
        cr = client.post('/api/projects', json={'name': 'Trans OK'})
        pid = cr.get_json()['id']

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

            rec = Recording(project_id=pid, original_name='v.mp3',
                            stored_name='v.mp3',
                            transcript_path='v_transcript.json',
                            transcription_status='transcribed')
            db.session.add(rec)
            db.session.commit()
            rid = rec.id

            with open(os.path.join(proj_dir, 'v_transcript.json'), 'w') as f:
                json.dump({'segments': [
                    {'start': 0, 'end': 5, 'text': 'Hello world', 'speaker': 'S'}
                ]}, f)

        r = client.get(f'/api/projects/{pid}/recordings/{rid}/transcript')
        assert r.status_code == 200
        data = r.get_json()
        assert len(data['segments']) == 1
        assert data['segments'][0]['text'] == 'Hello world'


# ---------------------------------------------------------------------------
# Cancel already-completed transcription
# ---------------------------------------------------------------------------

class TestCancelCompletedTranscription:
    """Cancelling a finished or pending recording should return 409."""

    def test_cancel_transcribed_recording(self, client, app):
        cr = client.post('/api/projects', json={'name': 'Cancel Done'})
        pid = cr.get_json()['id']

        with app.app_context():
            from app.extensions import db
            from app.models.recording import Recording
            rec = Recording(project_id=pid, original_name='d.mp3',
                            stored_name='d.mp3',
                            transcription_status='transcribed')
            db.session.add(rec)
            db.session.commit()
            rid = rec.id

        r = client.delete(f'/api/projects/{pid}/recordings/{rid}/transcription')
        assert r.status_code == 409

    def test_cancel_pending_recording(self, client, app):
        cr = client.post('/api/projects', json={'name': 'Cancel Pending'})
        pid = cr.get_json()['id']

        with app.app_context():
            from app.extensions import db
            from app.models.recording import Recording
            rec = Recording(project_id=pid, original_name='p.mp3',
                            stored_name='p.mp3',
                            transcription_status='pending')
            db.session.add(rec)
            db.session.commit()
            rid = rec.id

        r = client.delete(f'/api/projects/{pid}/recordings/{rid}/transcription')
        assert r.status_code == 409


# ---------------------------------------------------------------------------
# Double-delete project
# ---------------------------------------------------------------------------

class TestDoubleDeleteProject:
    """Deleting the same project twice should 404 on the second attempt."""

    def test_double_delete(self, client):
        cr = client.post('/api/projects', json={'name': 'Double Del'})
        pid = cr.get_json()['id']

        r1 = client.delete(f'/api/projects/{pid}')
        assert r1.status_code == 200

        r2 = client.delete(f'/api/projects/{pid}')
        assert r2.status_code == 404


# ---------------------------------------------------------------------------
# Project-wide export
# ---------------------------------------------------------------------------

class TestProjectWideExport:
    """Tests for GET/POST /<project_id>/export."""

    def test_export_project_markdown(self, client, app):
        """Project export with 2 recordings returns valid markdown."""
        cr = client.post('/api/projects', json={'name': 'Export Proj'})
        pid = cr.get_json()['id']

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

            rids = []
            for i in range(2):
                rec = Recording(project_id=pid, original_name=f'r{i}.mp3',
                                stored_name=f'r{i}.mp3',
                                transcript_path=f'r{i}_transcript.json',
                                transcription_status='transcribed')
                db.session.add(rec)
                db.session.flush()
                rids.append(rec.id)

                with open(os.path.join(proj_dir, f'r{i}_transcript.json'), 'w') as f:
                    json.dump({'segments': [
                        {'start': 0, 'end': 5, 'text': f'Segment from recording {i}',
                         'speaker': 'SPEAKER_00'}
                    ]}, f)
                with open(os.path.join(proj_dir, f'r{i}_annotations.json'), 'w') as f:
                    json.dump({'tag_spans': [], 'comments': [], 'speaker_labels': {}}, f)

            db.session.commit()

        r = client.post(f'/api/projects/{pid}/export',
                        json={'format': 'markdown', 'recording_ids': rids})
        assert r.status_code == 200
        text = r.data.decode('utf-8')
        assert 'Segment from recording 0' in text
        assert 'Segment from recording 1' in text

    def test_export_nonexistent_project(self, client):
        r = client.post('/api/projects/99999/export', json={'format': 'markdown'})
        assert r.status_code == 404
