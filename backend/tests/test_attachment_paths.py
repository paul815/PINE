"""attachments.json travels with the project folder, so it is not ours to trust.

A project reaches PINE from somewhere: restored from a backup someone else made,
copied off a shared drive, unzipped from a colleague. The metadata file inside
it names the file on disk for every attachment, and both the download and the
delete route used to join that name onto a path and act on the result.
"""

import json
import os

import pytest

from app.api.projects.attachments import _attachment_file


def _entry(stored_name):
    return {'id': 'a1', 'display_name': 'notes.txt', 'stored_name': stored_name}


@pytest.fixture
def project_dir(tmp_path):
    (tmp_path / 'attachments').mkdir()
    (tmp_path / 'attachments' / 'real.txt').write_text('mine', encoding='utf-8')
    (tmp_path / 'secret.txt').write_text('not an attachment', encoding='utf-8')
    return str(tmp_path)


def test_a_normal_attachment_resolves(project_dir):
    resolved = _attachment_file(project_dir, _entry('real.txt'))

    assert resolved == os.path.realpath(os.path.join(project_dir, 'attachments', 'real.txt'))


@pytest.mark.parametrize('stored_name', [
    '../secret.txt',
    '../../secret.txt',
    'sub/../../secret.txt',
    os.path.join('..', 'secret.txt'),
    '/etc/passwd',
    'C:\\Windows\\System32\\drivers\\etc\\hosts',
    '',
])
def test_nothing_outside_the_attachments_folder_resolves(project_dir, stored_name):
    assert _attachment_file(project_dir, _entry(stored_name)) is None


def test_a_missing_stored_name_is_refused_not_crashed(project_dir):
    assert _attachment_file(project_dir, {'id': 'a1'}) is None


def test_the_folder_itself_is_not_an_attachment(project_dir):
    """A '.' would otherwise resolve to the directory and pass a naive prefix test."""
    assert _attachment_file(project_dir, _entry('.')) is None


# ── through the routes ───────────────────────────────────────────────────────

def _project_with_attachments(app, client, entries):
    created = client.post('/api/projects', json={'name': 'Shared'}).get_json()
    with app.app_context():
        root = app.config['DEFAULT_PROJECTS_PATH']
    folder = os.path.join(root, created['folder_name'])
    os.makedirs(os.path.join(folder, 'attachments'), exist_ok=True)
    with open(os.path.join(folder, 'attachments.json'), 'w', encoding='utf-8') as f:
        json.dump(entries, f)
    return created['id'], folder


def test_download_refuses_to_serve_a_file_outside_the_project(app, client):
    project_id, folder = _project_with_attachments(app, client, [_entry('../../outside.txt')])
    outside = os.path.join(os.path.dirname(os.path.dirname(folder)), 'outside.txt')
    with open(outside, 'w', encoding='utf-8') as f:
        f.write('private')

    response = client.get(f'/api/projects/{project_id}/attachments/a1/download')

    assert response.status_code == 404
    assert b'private' not in response.data


def test_delete_refuses_to_remove_a_file_outside_the_project(app, client):
    project_id, folder = _project_with_attachments(app, client, [_entry('../../outside.txt')])
    outside = os.path.join(os.path.dirname(os.path.dirname(folder)), 'outside.txt')
    with open(outside, 'w', encoding='utf-8') as f:
        f.write('private')

    response = client.delete(f'/api/projects/{project_id}/attachments/a1')

    assert response.status_code == 200
    assert os.path.isfile(outside), 'the file outside the project must survive'
    # The row it came from is gone, so the project stops carrying it.
    assert client.get(f'/api/projects/{project_id}/attachments').get_json() == []


def test_a_malformed_metadata_file_does_not_500(app, client):
    project_id, _folder = _project_with_attachments(
        app, client, ['not a dict', {'no_id': True}, 42])

    assert client.get(f'/api/projects/{project_id}/attachments').status_code == 200
    assert client.get(f'/api/projects/{project_id}/attachments/a1/download').status_code == 404
    assert client.delete(f'/api/projects/{project_id}/attachments/a1').status_code == 404
