"""API tests for project attachments and media streaming.

Covers the five attachment routes and the media-streaming route, which
previously had zero coverage (audit item P1-4).
"""

import os
from io import BytesIO


def _make_project(project_with_recording):
    """Create a project (with a throwaway recording) and return (project_id, project_dir)."""
    project_id, _recording_id, project_dir = project_with_recording(status='pending')
    return project_id, project_dir


# ── Attachments ──────────────────────────────────────────────────────────

def test_list_attachments_empty(client, project_with_recording):
    pid, _ = _make_project(project_with_recording)
    resp = client.get(f'/api/projects/{pid}/attachments')
    assert resp.status_code == 200
    assert resp.get_json() == []


def test_list_attachments_project_not_found(client):
    resp = client.get('/api/projects/999999/attachments')
    assert resp.status_code == 404


def test_upload_attachment(client, project_with_recording):
    pid, project_dir = _make_project(project_with_recording)
    data = {'file': (BytesIO(b'hello pdf'), 'notes.pdf')}
    resp = client.post(f'/api/projects/{pid}/attachments',
                       data=data, content_type='multipart/form-data')
    assert resp.status_code == 201
    entry = resp.get_json()
    assert entry['display_name'] == 'notes.pdf'
    assert entry['stored_name'].endswith('.pdf')
    assert entry['id']
    # Physically written into the attachments/ dir
    stored = os.path.join(project_dir, 'attachments', entry['stored_name'])
    assert os.path.isfile(stored)
    # And it shows up in the listing
    listing = client.get(f'/api/projects/{pid}/attachments').get_json()
    assert len(listing) == 1
    assert listing[0]['id'] == entry['id']


def test_upload_attachment_no_file(client, project_with_recording):
    pid, _ = _make_project(project_with_recording)
    resp = client.post(f'/api/projects/{pid}/attachments',
                       data={}, content_type='multipart/form-data')
    assert resp.status_code == 400


def test_upload_attachment_project_not_found(client):
    data = {'file': (BytesIO(b'x'), 'notes.pdf')}
    resp = client.post('/api/projects/999999/attachments',
                       data=data, content_type='multipart/form-data')
    assert resp.status_code == 404


def test_rename_attachment(client, project_with_recording):
    pid, _ = _make_project(project_with_recording)
    data = {'file': (BytesIO(b'x'), 'old.pdf')}
    aid = client.post(f'/api/projects/{pid}/attachments',
                      data=data, content_type='multipart/form-data').get_json()['id']

    resp = client.patch(f'/api/projects/{pid}/attachments/{aid}',
                        json={'display_name': 'new name.pdf'})
    assert resp.status_code == 200
    assert resp.get_json()['display_name'] == 'new name.pdf'


def test_rename_attachment_requires_name(client, project_with_recording):
    pid, _ = _make_project(project_with_recording)
    data = {'file': (BytesIO(b'x'), 'old.pdf')}
    aid = client.post(f'/api/projects/{pid}/attachments',
                      data=data, content_type='multipart/form-data').get_json()['id']

    resp = client.patch(f'/api/projects/{pid}/attachments/{aid}',
                        json={'display_name': '   '})
    assert resp.status_code == 400


def test_rename_attachment_not_found(client, project_with_recording):
    pid, _ = _make_project(project_with_recording)
    resp = client.patch(f'/api/projects/{pid}/attachments/nope',
                        json={'display_name': 'x'})
    assert resp.status_code == 404


def test_download_attachment(client, project_with_recording):
    pid, _ = _make_project(project_with_recording)
    data = {'file': (BytesIO(b'PDFDATA'), 'doc.pdf')}
    aid = client.post(f'/api/projects/{pid}/attachments',
                      data=data, content_type='multipart/form-data').get_json()['id']

    resp = client.get(f'/api/projects/{pid}/attachments/{aid}/download')
    assert resp.status_code == 200
    assert resp.data == b'PDFDATA'


def test_download_attachment_not_found(client, project_with_recording):
    pid, _ = _make_project(project_with_recording)
    resp = client.get(f'/api/projects/{pid}/attachments/nope/download')
    assert resp.status_code == 404


def test_delete_attachment(client, project_with_recording):
    pid, project_dir = _make_project(project_with_recording)
    data = {'file': (BytesIO(b'x'), 'doc.pdf')}
    entry = client.post(f'/api/projects/{pid}/attachments',
                        data=data, content_type='multipart/form-data').get_json()
    aid, stored_name = entry['id'], entry['stored_name']

    resp = client.delete(f'/api/projects/{pid}/attachments/{aid}')
    assert resp.status_code == 200
    assert resp.get_json()['ok'] is True
    # Removed from disk and from the listing
    assert not os.path.isfile(os.path.join(project_dir, 'attachments', stored_name))
    assert client.get(f'/api/projects/{pid}/attachments').get_json() == []


def test_delete_attachment_not_found(client, project_with_recording):
    pid, _ = _make_project(project_with_recording)
    resp = client.delete(f'/api/projects/{pid}/attachments/nope')
    assert resp.status_code == 404


# ── Media streaming ──────────────────────────────────────────────────────

def test_stream_media_returns_file(client, project_with_recording):
    pid, rid, project_dir = project_with_recording(status='pending')
    with open(os.path.join(project_dir, 'rec.mp3'), 'wb') as f:
        f.write(b'ID3FAKEAUDIO')

    resp = client.get(f'/api/projects/{pid}/recordings/{rid}/media')
    assert resp.status_code == 200
    assert resp.data == b'ID3FAKEAUDIO'


def test_stream_media_missing_file(client, project_with_recording):
    pid, rid, _ = project_with_recording(status='pending')
    # No media file written to disk
    resp = client.get(f'/api/projects/{pid}/recordings/{rid}/media')
    assert resp.status_code == 404


def test_stream_media_wrong_project(client, project_with_recording):
    pid, rid, _ = project_with_recording(status='pending')
    resp = client.get(f'/api/projects/{pid + 999}/recordings/{rid}/media')
    assert resp.status_code == 404
