"""Tests for single transcription (no-project) mode."""

import io
import os


def test_list_empty(client):
    """GET single-transcriptions returns empty list when no system project exists."""
    resp = client.get('/api/projects/single-transcriptions')
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['recordings'] == []
    assert data['project_id'] is None


def test_upload_creates_system_project(client, app):
    """POST creates a hidden system project and a recording."""
    data = {'file': (io.BytesIO(b'\x00' * 100), 'test_audio.mp3')}
    resp = client.post('/api/projects/single-transcriptions', content_type='multipart/form-data', data=data)
    assert resp.status_code == 201
    rec = resp.get_json()
    assert rec['original_name'] == 'test_audio.mp3'
    assert rec['transcription_status'] == 'pending'

    # System project was created
    with app.app_context():
        from app.models.project import Project
        proj = Project.query.filter_by(is_system=True).first()
        assert proj is not None
        assert proj.folder_name == '__single_transcriptions'


def test_system_project_hidden_from_list(client):
    """System project should not appear in GET /api/projects."""
    # Create a single transcription (creates system project)
    data = {'file': (io.BytesIO(b'\x00' * 100), 'hidden.mp3')}
    client.post('/api/projects/single-transcriptions', content_type='multipart/form-data', data=data)

    # Also create a normal project
    client.post('/api/projects', json={'name': 'Visible Project'})

    resp = client.get('/api/projects')
    assert resp.status_code == 200
    projects = resp.get_json()
    all_names = [p['name'] for p in projects['active'] + projects['archived']]
    assert '__single_transcriptions' not in all_names
    assert 'Visible Project' in all_names


def test_list_after_upload(client):
    """GET returns the uploaded recording."""
    data = {'file': (io.BytesIO(b'\x00' * 100), 'interview.wav')}
    client.post('/api/projects/single-transcriptions', content_type='multipart/form-data', data=data)

    resp = client.get('/api/projects/single-transcriptions')
    data = resp.get_json()
    assert len(data['recordings']) == 1
    assert data['recordings'][0]['original_name'] == 'interview.wav'
    assert data['project_id'] is not None


def test_second_upload_reuses_project(client, app):
    """Multiple uploads should reuse the same system project."""
    for name in ('a.mp3', 'b.mp3'):
        data = {'file': (io.BytesIO(b'\x00' * 100), name)}
        client.post('/api/projects/single-transcriptions', content_type='multipart/form-data', data=data)

    with app.app_context():
        from app.models.project import Project
        system_projects = Project.query.filter_by(is_system=True).all()
        assert len(system_projects) == 1

    resp = client.get('/api/projects/single-transcriptions')
    assert len(resp.get_json()['recordings']) == 2


def test_delete_single_transcription(client, app, temp_dir):
    """DELETE removes recording and files from disk."""
    data = {'file': (io.BytesIO(b'\x00' * 100), 'deleteme.mp3')}
    resp = client.post('/api/projects/single-transcriptions', content_type='multipart/form-data', data=data)
    rid = resp.get_json()['id']

    resp = client.delete(f'/api/projects/single-transcriptions/{rid}')
    assert resp.status_code == 200
    assert resp.get_json()['ok'] is True

    # Verify it's gone from the list
    resp = client.get('/api/projects/single-transcriptions')
    assert len(resp.get_json()['recordings']) == 0


def test_delete_nonexistent(client):
    """DELETE returns 404 for unknown recording."""
    resp = client.delete('/api/projects/single-transcriptions/99999')
    assert resp.status_code == 404


def test_upload_bad_format(client):
    """Unsupported file format returns 400."""
    data = {'file': (io.BytesIO(b'\x00' * 100), 'bad.txt')}
    resp = client.post('/api/projects/single-transcriptions', content_type='multipart/form-data', data=data)
    assert resp.status_code == 400
    assert 'Unsupported' in resp.get_json()['error']


def test_upload_no_file(client):
    """POST without file returns 400."""
    resp = client.post('/api/projects/single-transcriptions', content_type='multipart/form-data', data={})
    assert resp.status_code == 400


def test_cannot_delete_system_project(client):
    """System project cannot be deleted via the normal delete endpoint."""
    # Create system project via upload
    data = {'file': (io.BytesIO(b'\x00' * 100), 'test.mp3')}
    client.post('/api/projects/single-transcriptions', content_type='multipart/form-data', data=data)

    # Get the system project ID
    resp = client.get('/api/projects/single-transcriptions')
    pid = resp.get_json()['project_id']

    # Try to delete it
    resp = client.delete(f'/api/projects/{pid}')
    assert resp.status_code == 403


def test_recording_viewable_via_existing_endpoint(client):
    """Single transcription recordings are accessible via normal recording GET endpoint."""
    data = {'file': (io.BytesIO(b'\x00' * 100), 'viewable.mp3')}
    resp = client.post('/api/projects/single-transcriptions', content_type='multipart/form-data', data=data)
    rec = resp.get_json()
    pid = rec['project_id']
    rid = rec['id']

    resp = client.get(f'/api/projects/{pid}/recordings/{rid}')
    assert resp.status_code == 200
    rdata = resp.get_json()
    assert rdata['project']['is_system'] is True
