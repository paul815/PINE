"""API integration tests for projects endpoints."""

import json
import os
from unittest.mock import patch

import pytest


class TestProjectsCRUD:
    """Project create, read, update, delete."""

    def test_list_empty(self, client):
        r = client.get('/api/projects')
        assert r.status_code == 200
        data = r.get_json()
        assert 'active' in data and 'archived' in data
        assert data['active'] == [] and data['archived'] == []

    def test_create_project(self, client):
        r = client.post('/api/projects', json={'name': 'Test Project'})
        assert r.status_code == 201
        data = r.get_json()
        assert data['name'] == 'Test Project'
        assert 'folder_name' in data
        assert 'id' in data

    def test_create_project_special_chars(self, client):
        r = client.post('/api/projects', json={'name': 'Audio & Video — Test!'})
        assert r.status_code == 201
        data = r.get_json()
        assert 'folder_name' in data
        assert '&' not in data['folder_name']
        assert '—' not in data['folder_name']

    def test_get_project_not_found(self, client):
        r = client.get('/api/projects/99999')
        assert r.status_code == 404

    def test_update_project(self, client):
        create = client.post('/api/projects', json={'name': 'Original'})
        pid = create.get_json()['id']
        r = client.patch(f'/api/projects/{pid}', json={'name': 'Updated'})
        assert r.status_code == 200
        assert r.get_json()['name'] == 'Updated'

    def test_patch_default_transcription_language(self, client):
        create = client.post('/api/projects', json={'name': 'Lang Project'})
        pid = create.get_json()['id']
        r = client.patch(f'/api/projects/{pid}', json={'default_transcription_language': 'ru'})
        assert r.status_code == 200
        assert r.get_json()['default_transcription_language'] == 'ru'
        r2 = client.get(f'/api/projects/{pid}')
        assert r2.get_json()['default_transcription_language'] == 'ru'
        r3 = client.patch(f'/api/projects/{pid}', json={'default_transcription_language': ''})
        assert r3.status_code == 200
        assert r3.get_json()['default_transcription_language'] == ''

    def test_new_project_has_empty_enabled_sections(self, client):
        r = client.post('/api/projects', json={'name': 'Sections Default'})
        assert r.status_code == 201
        assert r.get_json().get('enabled_sections') == []

    def test_enabled_sections_patch_persists_empty_after_migrate_db(self, app, client):
        """Empty enabled_sections must not be overwritten on startup migration."""
        import app as app_module

        create = client.post('/api/projects', json={'name': 'Sections Persist'})
        pid = create.get_json()['id']
        r = client.patch(
            f'/api/projects/{pid}',
            json={'enabled_sections': ['objective', 'questions']},
        )
        assert r.status_code == 200
        assert r.get_json()['enabled_sections'] == ['objective', 'questions']

        r = client.patch(f'/api/projects/{pid}', json={'enabled_sections': []})
        assert r.status_code == 200
        assert r.get_json()['enabled_sections'] == []

        db_path = os.path.join(app.config['DATA_DIR'], app.config['SQLITE_DB_FILENAME'])
        app_module._migrate_db(db_path)

        r2 = client.get(f'/api/projects/{pid}')
        assert r2.status_code == 200
        assert r2.get_json()['enabled_sections'] == []

    def test_archive_unarchive(self, client):
        create = client.post('/api/projects', json={'name': 'To Archive'})
        pid = create.get_json()['id']
        r = client.post(f'/api/projects/{pid}/archive')
        assert r.status_code == 200
        assert r.get_json()['is_archived'] is True
        assert r.get_json().get('archived_at')

        r2 = client.get('/api/projects')
        data = r2.get_json()
        assert pid not in [p['id'] for p in data['active']]
        assert pid in [p['id'] for p in data['archived']]

        r3 = client.post(f'/api/projects/{pid}/unarchive')
        assert r3.status_code == 200
        assert r3.get_json()['is_archived'] is False
        assert r3.get_json()['archived_at'] is None

    def test_delete_project(self, client):
        create = client.post('/api/projects', json={'name': 'To Delete'})
        pid = create.get_json()['id']
        r = client.delete(f'/api/projects/{pid}')
        assert r.status_code == 200
        assert r.get_json().get('ok') is True
        assert r.get_json().get('safety_backup')

        r2 = client.get(f'/api/projects/{pid}')
        assert r2.status_code == 404


class TestUploadValidation:
    """File upload validation."""

    def test_upload_rejects_no_file(self, client):
        r = client.post('/api/projects/1/recordings')
        # Project 1 may not exist - we get 404. Create project first.
        create = client.post('/api/projects', json={'name': 'Upload Test'})
        pid = create.get_json()['id']
        r = client.post(f'/api/projects/{pid}/recordings')
        assert r.status_code == 400
        assert 'error' in r.get_json()

    @patch('app.services.transcription.enqueue')
    def test_upload_rejects_txt(self, mock_enqueue, client):
        from io import BytesIO
        create = client.post('/api/projects', json={'name': 'Upload Test'})
        pid = create.get_json()['id']
        r = client.post(
            f'/api/projects/{pid}/recordings',
            data={'file': (BytesIO(b'fake audio'), 'test.txt')},
            content_type='multipart/form-data',
        )
        assert r.status_code == 400
        assert 'Unsupported' in r.get_json().get('error', '') or 'format' in r.get_json().get('error', '').lower()

    def test_upload_nonexistent_project(self, client):
        r = client.post('/api/projects/99999/recordings')
        assert r.status_code == 404


class TestAnnotationsAPI:
    """Annotations GET/PATCH."""

    @patch('app.services.transcription.enqueue')
    def test_annotations_roundtrip(self, mock_enqueue, client, app):
        # Create project with recording (no real file needed for annotations)
        create = client.post('/api/projects', json={'name': 'Ann Test'})
        pid = create.get_json()['id']

        with app.app_context():
            from app.extensions import db
            from app.models.recording import Recording
            from app.models.project import Project

            proj = db.session.get(Project, pid)
            rec = Recording(
                project_id=pid,
                original_name='r.mp3',
                stored_name='r.mp3',
                transcription_status='transcribed',
                transcript_path='r_transcript.json',
            )
            db.session.add(rec)
            db.session.commit()
            rid = rec.id

            # Create project dir and transcript so get_recording works
            projects_path = app.config['DEFAULT_PROJECTS_PATH']
            proj_dir = os.path.join(projects_path, proj.folder_name)
            os.makedirs(proj_dir, exist_ok=True)
            with open(os.path.join(proj_dir, 'r_transcript.json'), 'w') as f:
                json.dump({'segments': [], 'duration_seconds': 0}, f)

        ann = {
            'tag_spans': [{'segment_idx': 0, 'start_char': 0, 'end_char': 5, 'tag_id': 'pain'}],
            'comments': [{'segment_idx': 0, 'text': 'Note'}],
            'speaker_labels': {'SPEAKER_00': 'Moderator'},
        }
        r = client.patch(f'/api/projects/{pid}/recordings/{rid}/annotations', json=ann)
        assert r.status_code == 200

        r2 = client.get(f'/api/projects/{pid}/recordings/{rid}/annotations')
        assert r2.status_code == 200
        data = r2.get_json()
        assert data['tag_spans'] == ann['tag_spans']
        assert data['comments'] == ann['comments']
        assert data['speaker_labels'] == ann['speaker_labels']

    def test_annotations_isolated_for_same_name_without_transcript(self, client, app):
        with app.app_context():
            from app.extensions import db
            from app.models.project import Project
            from app.models.recording import Recording
            from app.models.setting import Setting

            projects_path = app.config['DEFAULT_PROJECTS_PATH']
            os.makedirs(projects_path, exist_ok=True)
            Setting.set('projects_path', projects_path)

            proj = Project(name='Isolation', folder_name='ann_isolation')
            db.session.add(proj)
            db.session.flush()
            rec1 = Recording(
                project_id=proj.id,
                original_name='same.mp3',
                stored_name='same.mp3',
                transcription_status='pending',
            )
            rec2 = Recording(
                project_id=proj.id,
                original_name='same.mp3',
                stored_name='same.mp3',
                transcription_status='pending',
            )
            db.session.add(rec1)
            db.session.add(rec2)
            db.session.commit()
            pid, rid1, rid2 = proj.id, rec1.id, rec2.id

            os.makedirs(os.path.join(projects_path, proj.folder_name), exist_ok=True)

        r1 = client.patch(
            f'/api/projects/{pid}/recordings/{rid1}/annotations',
            json={'speaker_labels': {'SPEAKER_00': 'Alice'}},
        )
        assert r1.status_code == 200
        r2 = client.patch(
            f'/api/projects/{pid}/recordings/{rid2}/annotations',
            json={'speaker_labels': {'SPEAKER_00': 'Bob'}},
        )
        assert r2.status_code == 200

        g1 = client.get(f'/api/projects/{pid}/recordings/{rid1}/annotations')
        g2 = client.get(f'/api/projects/{pid}/recordings/{rid2}/annotations')
        assert g1.status_code == 200 and g2.status_code == 200
        assert g1.get_json()['speaker_labels'] == {'SPEAKER_00': 'Alice'}
        assert g2.get_json()['speaker_labels'] == {'SPEAKER_00': 'Bob'}


class TestDeleteRecordingAnnotationsCleanup:
    def test_delete_removes_current_and_legacy_annotation_files(self, client, app):
        with app.app_context():
            from app.extensions import db
            from app.models.project import Project
            from app.models.recording import Recording
            from app.models.setting import Setting
            from app.services.annotations import (
                annotation_recording_ref,
                annotations_filename,
            )

            projects_path = app.config['DEFAULT_PROJECTS_PATH']
            os.makedirs(projects_path, exist_ok=True)
            Setting.set('projects_path', projects_path)

            proj = Project(name='Cleanup', folder_name='ann_cleanup')
            db.session.add(proj)
            db.session.flush()
            rec = Recording(
                project_id=proj.id,
                original_name='same.mp3',
                stored_name='same.mp3',
                transcript_path='same_999_transcript.json',
                transcription_status='transcribed',
            )
            db.session.add(rec)
            db.session.commit()
            pid, rid = proj.id, rec.id

            proj_dir = os.path.join(projects_path, proj.folder_name)
            os.makedirs(proj_dir, exist_ok=True)

            current_ann = os.path.join(
                proj_dir,
                annotations_filename(annotation_recording_ref(rec)),
            )
            legacy_ann = os.path.join(
                proj_dir,
                annotations_filename(rec.stored_name),
            )
            with open(current_ann, 'w', encoding='utf-8') as f:
                json.dump({'speaker_labels': {'SPEAKER_00': 'New'}}, f)
            with open(legacy_ann, 'w', encoding='utf-8') as f:
                json.dump({'speaker_labels': {'SPEAKER_00': 'Old'}}, f)

        r = client.delete(f'/api/projects/{pid}/recordings/{rid}')
        assert r.status_code == 200
        assert r.get_json().get('ok') is True
        assert not os.path.isfile(current_ann)
        assert not os.path.isfile(legacy_ann)


class TestExportAPI:
    """Export recording to Markdown/ODT."""

    def test_export_not_found(self, client):
        r = client.post('/api/projects/1/recordings/1/export', json={'format': 'markdown'})
        assert r.status_code == 404

    def test_export_success(self, client, app):
        with app.app_context():
            from app.extensions import db
            from app.models.project import Project
            from app.models.recording import Recording
            from app.models.setting import Setting

            projects_path = app.config['DEFAULT_PROJECTS_PATH']
            os.makedirs(projects_path, exist_ok=True)
            Setting.set('projects_path', projects_path)

            proj = Project(name='Export Proj', folder_name='export_proj')
            db.session.add(proj)
            db.session.flush()
            rec = Recording(
                project_id=proj.id,
                original_name='e.mp3',
                stored_name='e.mp3',
                transcript_path='e_transcript.json',
                duration_seconds=60,
                transcription_status='transcribed',
            )
            db.session.add(rec)
            db.session.commit()
            pid, rid = proj.id, rec.id

            proj_dir = os.path.join(projects_path, 'export_proj')
            os.makedirs(proj_dir, exist_ok=True)
            with open(os.path.join(proj_dir, 'e_transcript.json'), 'w') as f:
                json.dump({
                    'segments': [{'start': 0, 'end': 5, 'text': 'Hello', 'speaker': 'Moderator'}],
                    'duration_seconds': 5,
                }, f)

        r = client.post(
            f'/api/projects/{pid}/recordings/{rid}/export',
            json={'format': 'markdown'},
        )
        assert r.status_code == 200
        assert 'text/markdown' in r.headers.get('Content-Type', '')
        assert b'# Export Proj' in r.data
        assert b'Hello' in r.data

    def test_export_get_success(self, client, app):
        """GET export works for direct link download (avoids Chrome blocking async blob)."""
        with app.app_context():
            from app.extensions import db
            from app.models.project import Project
            from app.models.recording import Recording
            from app.models.setting import Setting

            projects_path = app.config['DEFAULT_PROJECTS_PATH']
            os.makedirs(projects_path, exist_ok=True)
            Setting.set('projects_path', projects_path)

            proj = Project(name='GET Export', folder_name='get_export')
            db.session.add(proj)
            db.session.flush()
            rec = Recording(
                project_id=proj.id,
                original_name='g.mp3',
                stored_name='g.mp3',
                transcript_path='g_transcript.json',
                duration_seconds=30,
                transcription_status='transcribed',
            )
            db.session.add(rec)
            db.session.commit()
            pid, rid = proj.id, rec.id

            proj_dir = os.path.join(projects_path, 'get_export')
            os.makedirs(proj_dir, exist_ok=True)
            with open(os.path.join(proj_dir, 'g_transcript.json'), 'w') as f:
                json.dump({
                    'segments': [{'start': 0, 'end': 2, 'text': 'GET test', 'speaker': 'S1'}],
                    'duration_seconds': 2,
                }, f)

        r = client.get(
            f'/api/projects/{pid}/recordings/{rid}/export',
            query_string={'format': 'markdown', 'include_comments': 'true', 'include_tags': 'true', 'remove_pii': 'false'},
        )
        assert r.status_code == 200
        assert 'text/markdown' in r.headers.get('Content-Type', '')
        assert b'# GET Export' in r.data
        assert b'GET test' in r.data

    def test_export_odt_format(self, client, app):
        """ODT format returns ODT content and correct headers."""
        with app.app_context():
            from app.extensions import db
            from app.models.project import Project
            from app.models.recording import Recording
            from app.models.setting import Setting

            projects_path = app.config['DEFAULT_PROJECTS_PATH']
            os.makedirs(projects_path, exist_ok=True)
            Setting.set('projects_path', projects_path)

            proj = Project(name='ODT Test', folder_name='odt_test')
            db.session.add(proj)
            db.session.flush()
            rec = Recording(
                project_id=proj.id,
                original_name='o.mp3',
                stored_name='o.mp3',
                transcript_path='o_transcript.json',
                duration_seconds=10,
                transcription_status='transcribed',
            )
            db.session.add(rec)
            db.session.commit()
            pid, rid = proj.id, rec.id

            proj_dir = os.path.join(projects_path, 'odt_test')
            os.makedirs(proj_dir, exist_ok=True)
            with open(os.path.join(proj_dir, 'o_transcript.json'), 'w') as f:
                json.dump({
                    'segments': [{'start': 0, 'end': 2, 'text': 'ODT content', 'speaker': 'S1'}],
                    'duration_seconds': 2,
                }, f)

        r = client.get(
            f'/api/projects/{pid}/recordings/{rid}/export',
            query_string={'format': 'odt'},
        )
        assert r.status_code == 200
        assert 'opendocument' in r.headers.get('Content-Type', '')
        assert '.odt' in r.headers.get('Content-Disposition', '')


class TestUploadFormats:
    """All 8 allowed audio/video formats must be accepted."""

    @pytest.mark.parametrize('ext', ['mp3', 'mp4', 'm4a', 'wav', 'mkv', 'webm', 'ogg', 'flac'])
    @patch('app.services.transcription.enqueue')
    def test_upload_accepts_format(self, mock_enqueue, ext, client):
        from io import BytesIO
        create = client.post('/api/projects', json={'name': 'Format Test'})
        pid = create.get_json()['id']
        r = client.post(
            f'/api/projects/{pid}/recordings',
            data={'file': (BytesIO(b'FAKE'), f'recording.{ext}')},
            content_type='multipart/form-data',
        )
        assert r.status_code == 201, f'.{ext} was rejected: {r.get_json()}'

    @patch('app.services.transcription.enqueue')
    def test_upload_unicode_filename(self, mock_enqueue, client):
        from io import BytesIO
        create = client.post('/api/projects', json={'name': 'Unicode Test'})
        pid = create.get_json()['id']
        r = client.post(
            f'/api/projects/{pid}/recordings',
            data={'file': (BytesIO(b'FAKE'), '\u0438\u043d\u0442\u0435\u0440\u0432\u044c\u044e_2024.mp3')},
            content_type='multipart/form-data',
        )
        assert r.status_code == 201


class TestProjectFolder:
    """Project folder is created, preserved on archive, removed on delete."""

    def test_delete_removes_folder(self, client, app):
        create = client.post('/api/projects', json={'name': 'To Delete Folder'})
        pid = create.get_json()['id']

        with app.app_context():
            from app.extensions import db as _db
            from app.models.project import Project
            proj = _db.session.get(Project, pid)
            folder = os.path.join(app.config['DEFAULT_PROJECTS_PATH'], proj.folder_name)

        assert os.path.isdir(folder)

        client.delete(f'/api/projects/{pid}')

        assert not os.path.isdir(folder)

    def test_archive_preserves_folder(self, client, app):
        create = client.post('/api/projects', json={'name': 'To Archive Folder'})
        pid = create.get_json()['id']

        with app.app_context():
            from app.extensions import db as _db
            from app.models.project import Project
            proj = _db.session.get(Project, pid)
            folder = os.path.join(app.config['DEFAULT_PROJECTS_PATH'], proj.folder_name)

        assert os.path.isdir(folder)
        client.post(f'/api/projects/{pid}/archive')
        assert os.path.isdir(folder)


class TestTransferZip:
    """Transfer endpoint returns a valid ZIP with required structure."""

    def test_transfer_zip_structure(self, client, app):
        import zipfile
        import io

        with app.app_context():
            from app.extensions import db as _db
            from app.models.project import Project
            from app.models.recording import Recording
            from app.models.setting import Setting

            projects_path = app.config['DEFAULT_PROJECTS_PATH']
            Setting.set('projects_path', projects_path)

            proj = Project(name='Transfer Proj', folder_name='transfer_proj')
            _db.session.add(proj)
            _db.session.flush()
            rec = Recording(
                project_id=proj.id,
                original_name='t.mp3',
                stored_name='t.mp3',
                transcript_path='t_transcript.json',
                transcription_status='transcribed',
            )
            _db.session.add(rec)
            _db.session.commit()
            pid = proj.id

            proj_dir = os.path.join(projects_path, 'transfer_proj')
            os.makedirs(proj_dir, exist_ok=True)
            with open(os.path.join(proj_dir, 't_transcript.json'), 'w') as f:
                json.dump({'segments': []}, f)
            with open(os.path.join(proj_dir, 't_annotations.json'), 'w') as f:
                json.dump({'tag_spans': [], 'comments': []}, f)

        r = client.get(f'/api/projects/{pid}/transfer')
        assert r.status_code == 200
        assert r.headers.get('Content-Type', '').startswith('application/zip')

        zf = zipfile.ZipFile(io.BytesIO(r.data))
        names = zf.namelist()

        assert 'project.json' in names
        assert 'project_tags.json' in names
        assert any(n.startswith('transcripts/') for n in names)
        assert any(n.startswith('annotations/') for n in names)

        meta = json.loads(zf.read('project.json'))
        assert meta['name'] == 'Transfer Proj'


class TestListProjectsIncludeTags:
    """GET /api/projects?include_tags=1 enriches each project with tag definitions."""

    def test_omit_tags_without_query_flag(self, client, app):
        create = client.post('/api/projects', json={'name': 'No Tag Field'})
        assert create.status_code == 201
        r = client.get('/api/projects')
        assert r.status_code == 200
        data = r.get_json()
        for p in data['active'] + data['archived']:
            assert 'tags' not in p

    def test_include_tags_accepts_camel_case_param(self, client, app):
        create = client.post('/api/projects', json={'name': 'Camel Tags'})
        assert create.status_code == 201
        r = client.get('/api/projects?includeTags=1')
        assert r.status_code == 200
        data = r.get_json()
        for p in data['active'] + data['archived']:
            assert 'tags' in p

    def test_include_tags_empty_list(self, client, app):
        create = client.post('/api/projects', json={'name': 'Tags Empty'})
        assert create.status_code == 201
        r = client.get('/api/projects?include_tags=1')
        assert r.status_code == 200
        data = r.get_json()
        for p in data['active'] + data['archived']:
            assert 'tags' in p
            assert p['tags'] == []

    def test_include_tags_from_project_tags_json(self, client, app):
        create = client.post('/api/projects', json={'name': 'Tags From File'})
        assert create.status_code == 201
        row = create.get_json()
        folder = row['folder_name']
        proj_dir = os.path.join(app.config['DEFAULT_PROJECTS_PATH'], folder)
        tags = [{'id': 'custom', 'name': 'Alpha Tag', 'color': 'ins'}]
        with open(os.path.join(proj_dir, 'project_tags.json'), 'w', encoding='utf-8') as f:
            json.dump(tags, f)

        r = client.get('/api/projects?include_tags=1')
        assert r.status_code == 200
        data = r.get_json()
        match = next(p for p in data['active'] if p['id'] == row['id'])
        assert len(match['tags']) == 1
        assert match['tags'][0]['name'] == 'Alpha Tag'
        assert match['tags'][0]['color'] == 'ins'


class TestRecordingEndpoints:
    """Recording CRUD endpoints."""

    def test_get_recording(self, client, project_with_recording):
        pid, rid, _ = project_with_recording(
            transcript_data={
                'segments': [{'start': 0, 'end': 2, 'text': 'Hello', 'speaker': 'Mod'}],
                'duration_seconds': 2,
            },
        )
        r = client.get(f'/api/projects/{pid}/recordings/{rid}')
        assert r.status_code == 200
        data = r.get_json()
        assert 'recording' in data
        assert data['recording']['id'] == rid

    def test_get_recording_not_found(self, client):
        create = client.post('/api/projects', json={'name': 'Rec Not Found'})
        assert create.status_code == 201
        pid = create.get_json()['id']
        r = client.get(f'/api/projects/{pid}/recordings/99999')
        assert r.status_code == 404

    def test_update_recording_participant_notes(self, client, project_with_recording):
        pid, rid, _ = project_with_recording(status='pending')
        r = client.patch(
            f'/api/projects/{pid}/recordings/{rid}',
            json={'participant_notes': 'Some notes'},
        )
        assert r.status_code == 200
        data = r.get_json()
        assert data['participant_notes'] == 'Some notes'

    def test_update_recording_segment(self, client, project_with_recording):
        pid, rid, _ = project_with_recording(status='pending')
        seg = client.post(f'/api/projects/{pid}/segments', json={'name': 'Seg'})
        assert seg.status_code in (200, 201)
        segment_id = seg.get_json()['id']
        r = client.patch(
            f'/api/projects/{pid}/recordings/{rid}',
            json={'segment_id': segment_id},
        )
        assert r.status_code == 200

    def test_update_recording_not_found(self, client):
        create = client.post('/api/projects', json={'name': 'Patch Not Found'})
        assert create.status_code == 201
        pid = create.get_json()['id']
        r = client.patch(f'/api/projects/{pid}/recordings/99999', json={})
        assert r.status_code == 404


class TestTranscriptEndpoint:
    """Transcript retrieval."""

    def test_get_transcript_success(self, client, project_with_recording):
        pid, rid, _ = project_with_recording(
            transcript_data={
                'segments': [{'start': 0, 'end': 2, 'text': 'Hi', 'speaker': 'Mod'}],
                'duration_seconds': 2,
            },
        )
        r = client.get(f'/api/projects/{pid}/recordings/{rid}/transcript')
        assert r.status_code == 200
        data = r.get_json()
        assert 'segments' in data

    def test_get_transcript_not_transcribed(self, client, project_with_recording):
        pid, rid, _ = project_with_recording(status='pending')
        r = client.get(f'/api/projects/{pid}/recordings/{rid}/transcript')
        assert r.status_code == 404

    def test_get_transcript_file_missing(self, client, project_with_recording):
        pid, rid, _ = project_with_recording(status='transcribed')
        r = client.get(f'/api/projects/{pid}/recordings/{rid}/transcript')
        assert r.status_code == 404


class TestCancelTranscription:
    """Cancel transcription endpoint."""

    def test_cancel_not_transcribing(self, client, project_with_recording):
        pid, rid, _ = project_with_recording(status='pending')
        r = client.delete(f'/api/projects/{pid}/recordings/{rid}/transcription')
        assert r.status_code == 409

    def test_cancel_not_found(self, client):
        create = client.post('/api/projects', json={'name': 'Cancel NF'})
        assert create.status_code == 201
        pid = create.get_json()['id']
        r = client.delete(f'/api/projects/{pid}/recordings/99999/transcription')
        assert r.status_code == 404

    @patch('app.services.transcription.cancel_transcription', return_value=True)
    def test_cancel_success(self, mock_cancel, client, project_with_recording):
        pid, rid, _ = project_with_recording(status='transcribing')
        r = client.delete(f'/api/projects/{pid}/recordings/{rid}/transcription')
        assert r.status_code == 200
        data = r.get_json()
        assert data['ok'] is True


class TestTagEndpoints:
    """Project tag CRUD."""

    def test_patch_project_tags(self, client):
        create = client.post('/api/projects', json={'name': 'Tag Proj'})
        assert create.status_code == 201
        pid = create.get_json()['id']
        tags = [{'id': 't1', 'name': 'Pain', 'color': '#ff0000'}]
        r = client.patch(f'/api/projects/{pid}/tags', json={'tags': tags})
        assert r.status_code == 200
        data = r.get_json()
        assert isinstance(data, list)
        assert any(t['id'] == 't1' for t in data)

        r2 = client.get(f'/api/projects/{pid}/tags')
        assert r2.status_code == 200
        data2 = r2.get_json()
        assert any(t['id'] == 't1' for t in data2)

    def test_get_project_tags_empty(self, client):
        create = client.post('/api/projects', json={'name': 'Tags Empty Proj'})
        assert create.status_code == 201
        pid = create.get_json()['id']
        r = client.get(f'/api/projects/{pid}/tags')
        assert r.status_code == 200
        assert r.get_json() == []


class TestTagQuotes:
    """Tag quotes aggregation."""

    def test_tag_quotes_empty(self, client):
        create = client.post('/api/projects', json={'name': 'Quotes Empty'})
        assert create.status_code == 201
        pid = create.get_json()['id']
        r = client.get(f'/api/projects/{pid}/tags/quotes')
        assert r.status_code == 200
        data = r.get_json()
        assert 'tags' in data and isinstance(data['tags'], list)
        assert 'recordings' in data and isinstance(data['recordings'], list)

    def test_tag_quotes_with_data(self, client, project_with_recording):
        pid, rid, proj_dir = project_with_recording(
            transcript_data={
                'segments': [{'start': 0, 'end': 2, 'text': 'Hello', 'speaker': 'Mod'}],
                'duration_seconds': 2,
            },
        )
        tags = [{'id': 't1', 'name': 'Pain', 'color': '#f00'}]
        with open(os.path.join(proj_dir, 'project_tags.json'), 'w', encoding='utf-8') as f:
            json.dump(tags, f)

        annotations = {
            'tag_spans': [
                {'segment_idx': 0, 'start_char': 0, 'end_char': 5, 'tag_id': 't1'},
            ],
            'comments': [],
        }
        with open(os.path.join(proj_dir, 'rec_annotations.json'), 'w', encoding='utf-8') as f:
            json.dump(annotations, f)

        r = client.get(f'/api/projects/{pid}/tags/quotes')
        assert r.status_code == 200
        data = r.get_json()
        assert any(t.get('count', 0) > 0 for t in data['tags'])
        assert len(data['recordings']) > 0

    def test_tag_quotes_includes_themes_and_group(self, client, project_with_recording):
        pid, rid, proj_dir = project_with_recording(
            transcript_data={
                'segments': [{'start': 0, 'end': 2, 'text': 'Hello', 'speaker': 'Mod'}],
                'duration_seconds': 2,
            },
        )
        with open(os.path.join(proj_dir, 'project_themes.json'), 'w', encoding='utf-8') as f:
            json.dump([{'id': 'g1', 'name': 'Onboarding', 'color': 'pain'}], f)
        with open(os.path.join(proj_dir, 'project_tags.json'), 'w', encoding='utf-8') as f:
            json.dump([{'id': 't1', 'name': 'Pain', 'color': '#f00aa0',
                        'description': 'When blocked', 'group_id': 'g1'}], f)
        with open(os.path.join(proj_dir, 'rec_annotations.json'), 'w', encoding='utf-8') as f:
            json.dump({'tag_spans': [{'segment_idx': 0, 'start_char': 0, 'end_char': 5, 'tag_id': 't1'}],
                       'comments': []}, f)

        data = client.get(f'/api/projects/{pid}/tags/quotes').get_json()
        assert data['themes'] == [{'id': 'g1', 'name': 'Onboarding', 'color': 'pain'}]
        tag = next(t for t in data['tags'] if t['id'] == 't1')
        assert tag['group_id'] == 'g1'
        assert tag['description'] == 'When blocked'

    def test_tag_quotes_multiblock_keeps_middle(self, client, project_with_recording):
        # A selection spanning 3+ speaker blocks must keep the middle block(s),
        # not just the first and last.
        pid, rid, proj_dir = project_with_recording(
            transcript_data={
                'segments': [
                    {'start': 0, 'end': 1, 'text': 'AAA', 'speaker': 'Mod'},
                    {'start': 1, 'end': 2, 'text': 'BBB', 'speaker': 'P1'},
                    {'start': 2, 'end': 3, 'text': 'CCC', 'speaker': 'P2'},
                ],
                'duration_seconds': 3,
            },
        )
        with open(os.path.join(proj_dir, 'project_tags.json'), 'w', encoding='utf-8') as f:
            json.dump([{'id': 't1', 'name': 'T', 'color': 'pain'}], f)
        span = {'segment_idx': 0, 'merged_start': 0, 'merged_end': 3,
                'end_segment_idx': 2, 'end_merged_end': 3, 'tag_id': 't1'}
        with open(os.path.join(proj_dir, 'rec_annotations.json'), 'w', encoding='utf-8') as f:
            json.dump({'tag_spans': [span], 'comments': []}, f)

        data = client.get(f'/api/projects/{pid}/tags/quotes').get_json()
        quote = data['recordings'][0]['quotes'][0]['text']
        assert quote == 'AAA BBB CCC'


class TestProjectThemes:
    """Tag themes endpoint (two-level hierarchy)."""

    def test_patch_and_get_themes(self, client):
        pid = client.post('/api/projects', json={'name': 'Theme Proj'}).get_json()['id']
        themes = [{'id': 'g1', 'name': 'Onboarding', 'color': 'pain'}]
        r = client.patch(f'/api/projects/{pid}/themes', json={'themes': themes})
        assert r.status_code == 200
        assert r.get_json() == themes
        assert client.get(f'/api/projects/{pid}/themes').get_json() == themes

    def test_themes_normalized_on_save(self, client):
        # Unsafe color → safe token; entry without id/name dropped.
        pid = client.post('/api/projects', json={'name': 'Theme Norm'}).get_json()['id']
        r = client.patch(f'/api/projects/{pid}/themes',
                         json={'themes': [{'id': 'g1', 'name': 'T', 'color': 'evil"x'}, {'bad': 1}]})
        assert r.status_code == 200
        assert r.get_json() == [{'id': 'g1', 'name': 'T', 'color': 'fu'}]

    def test_get_themes_empty(self, client):
        pid = client.post('/api/projects', json={'name': 'Theme Empty'}).get_json()['id']
        r = client.get(f'/api/projects/{pid}/themes')
        assert r.status_code == 200
        assert r.get_json() == []


class TestManageTagsPage:
    """Tag editing is integrated into the Tags screen; the old /manage route redirects."""

    def test_manage_page_redirects_to_tags(self, client, app):
        from app.models.setting import Setting
        from app.extensions import db
        with app.app_context():
            Setting.set('onboarding_complete', 'true')
            db.session.commit()
        pid = client.post('/api/projects', json={'name': 'Manage Proj'}).get_json()['id']
        r = client.get(f'/project/{pid}/tags/manage')
        assert r.status_code in (301, 302)
        assert f'/project/{pid}/tags' in r.headers['Location']

    def test_tags_page_renders_with_editor(self, client, app):
        from app.models.setting import Setting
        from app.extensions import db
        with app.app_context():
            Setting.set('onboarding_complete', 'true')
            db.session.commit()
        pid = client.post('/api/projects', json={'name': 'Tags Page'}).get_json()['id']
        r = client.get(f'/project/{pid}/tags')
        assert r.status_code == 200
        # Integrated editor present: the toggle button and the inspector panel
        # it reveals. Anchored on ids rather than the visible label, which the
        # UI review renamed from "Edit details" to "Editor".
        assert b'id="editToggle"' in r.data
        assert b'toggleInspector()' in r.data


class TestStreamMedia:
    """Media streaming endpoint."""

    def test_stream_media_success(self, client, project_with_recording):
        pid, rid, proj_dir = project_with_recording(status='transcribed')
        media_path = os.path.join(proj_dir, 'rec.mp3')
        with open(media_path, 'wb') as f:
            f.write(b'FAKEAUDIO')
        r = client.get(f'/api/projects/{pid}/recordings/{rid}/media')
        assert r.status_code == 200

    def test_stream_media_file_missing(self, client, project_with_recording):
        pid, rid, _ = project_with_recording(status='transcribed')
        r = client.get(f'/api/projects/{pid}/recordings/{rid}/media')
        assert r.status_code == 404


class TestRuntimeStatus:
    """Runtime status utility endpoint."""

    def test_runtime_status(self, client):
        r = client.get('/api/utils/runtime-status')
        assert r.status_code == 200
        data = r.get_json()
        assert data['ok'] is True
