"""API tests for editing one transcript paragraph in place.

The algorithm has its own tests; these are about the round trip -- what the
endpoint refuses, what it writes to disk, and what it hands back for the page
to re-render from.
"""

import json
import os
import time

from app.extensions import db
from app.models.project import Project
from app.models.recording import Recording
from app.models.setting import Setting

SEGMENTS = [
    {'start': 0, 'end': 2, 'text': 'The kubernetis cluster', 'speaker': 'S1'},
    {'start': 2, 'end': 5, 'text': 'went down again.', 'speaker': 'S1'},
]
BLOCK = 'The kubernetis cluster went down again.'


def _setup(app, segments=None, annotations=None, status='transcribed'):
    projects_path = app.config['DEFAULT_PROJECTS_PATH']
    os.makedirs(projects_path, exist_ok=True)
    Setting.set('projects_path', projects_path)

    proj = Project(name='Block Proj', folder_name='block_proj')
    db.session.add(proj)
    db.session.flush()
    rec = Recording(
        project_id=proj.id,
        original_name='t.mp3',
        stored_name='t.mp3',
        transcript_path='t_transcript.json',
        duration_seconds=10,
        transcription_status=status,
    )
    db.session.add(rec)
    db.session.commit()
    pid, rid = proj.id, rec.id

    proj_dir = os.path.join(projects_path, 'block_proj')
    os.makedirs(proj_dir, exist_ok=True)
    with open(os.path.join(proj_dir, 't_transcript.json'), 'w', encoding='utf-8') as f:
        json.dump({'segments': json.loads(json.dumps(
            SEGMENTS if segments is None else segments))}, f)
    if annotations is not None:
        with open(os.path.join(proj_dir, 't_annotations.json'), 'w', encoding='utf-8') as f:
            json.dump(annotations, f)
    return pid, rid, proj_dir


def _edit(client, pid, rid, **payload):
    return client.post(
        f'/api/projects/{pid}/recordings/{rid}/transcript/block', json=payload)


def _on_disk(proj_dir):
    with open(os.path.join(proj_dir, 't_transcript.json'), encoding='utf-8') as f:
        return json.load(f)


def test_an_edit_is_applied_and_written_to_disk(client, app):
    with app.app_context():
        pid, rid, proj_dir = _setup(app)

    r = _edit(client, pid, rid, indices=[0, 1], original_text=BLOCK,
              new_text='The Kubernetes cluster went down again.')

    assert r.status_code == 200
    body = r.get_json()
    assert body['changed'] is True
    assert body['block_text'] == 'The Kubernetes cluster went down again.'
    assert body['transcript']['segments'][0]['text'] == 'The Kubernetes cluster'
    # Export reads the file, not the response, so the file is what matters.
    assert _on_disk(proj_dir)['segments'][0]['text'] == 'The Kubernetes cluster'


def test_the_response_carries_the_annotations_back(client, app):
    """The page re-renders highlights from this, so they have to come back moved."""
    annotations = {'tag_spans': [{
        'segment_idx': 1, 'start_char': 0, 'end_char': 4,
        'anchor_text': 'went', 'tag_id': 't1',
    }], 'comments': []}
    with app.app_context():
        pid, rid, _ = _setup(app, annotations=annotations)

    r = _edit(client, pid, rid, indices=[0, 1], original_text=BLOCK,
              new_text='The Kubernetes cluster went down again.')

    assert r.status_code == 200
    span = r.get_json()['annotations']['tag_spans'][0]
    assert span['start_char'] == 0 and span['end_char'] == 4


def test_the_same_text_back_changes_nothing_on_disk(client, app):
    with app.app_context():
        pid, rid, proj_dir = _setup(app)
    path = os.path.join(proj_dir, 't_transcript.json')
    before = os.stat(path).st_mtime_ns
    time.sleep(0.01)

    r = _edit(client, pid, rid, indices=[0, 1],
              original_text=BLOCK, new_text=BLOCK)

    assert r.status_code == 200
    assert r.get_json()['changed'] is False
    assert os.stat(path).st_mtime_ns == before, 'an unchanged block rewrote the file'


def test_text_that_moved_underneath_the_editor_is_refused(client, app):
    with app.app_context():
        pid, rid, proj_dir = _setup(app)

    r = _edit(client, pid, rid, indices=[0, 1],
              original_text='Something else entirely', new_text='Whatever')

    assert r.status_code == 409
    assert r.get_json()['error'] == 'stale'
    assert _on_disk(proj_dir)['segments'][0]['text'] == 'The kubernetis cluster'


def test_indices_that_name_no_block_are_refused(client, app):
    with app.app_context():
        pid, rid, _ = _setup(app)

    r = _edit(client, pid, rid, indices=[0], original_text=BLOCK, new_text='Whatever')

    assert r.status_code == 409
    assert r.get_json()['error'] == 'stale'


def test_indices_outside_the_transcript_are_refused(client, app):
    with app.app_context():
        pid, rid, _ = _setup(app)

    r = _edit(client, pid, rid, indices=[0, 99], original_text=BLOCK, new_text='Whatever')

    assert r.status_code == 409


def test_an_empty_paragraph_is_refused(client, app):
    with app.app_context():
        pid, rid, proj_dir = _setup(app)

    r = _edit(client, pid, rid, indices=[0, 1], original_text=BLOCK, new_text='   ')

    assert r.status_code == 400
    assert _on_disk(proj_dir)['segments'][0]['text'] == 'The kubernetis cluster'


def test_a_body_without_indices_is_rejected(client, app):
    with app.app_context():
        pid, rid, _ = _setup(app)

    assert _edit(client, pid, rid, original_text=BLOCK, new_text='x').status_code == 400
    assert _edit(client, pid, rid, indices=[], original_text=BLOCK,
                 new_text='x').status_code == 400
    assert _edit(client, pid, rid, indices='0,1', original_text=BLOCK,
                 new_text='x').status_code == 400
    assert _edit(client, pid, rid, indices=[0, 'one'], original_text=BLOCK,
                 new_text='x').status_code == 400


def test_a_recording_still_transcribing_cannot_be_edited(client, app):
    """Half a transcript is not a paragraph; the job still owns the file."""
    with app.app_context():
        pid, rid, _ = _setup(app, status='transcribing')

    r = _edit(client, pid, rid, indices=[0, 1], original_text=BLOCK, new_text='x')

    assert r.status_code == 404


def test_an_unknown_recording_is_not_found(client, app):
    with app.app_context():
        pid, _rid, _ = _setup(app)

    r = _edit(client, pid, 9999, indices=[0], original_text='x', new_text='y')

    assert r.status_code == 404


def test_a_missing_transcript_file_is_not_found(client, app):
    with app.app_context():
        pid, rid, proj_dir = _setup(app)
    os.remove(os.path.join(proj_dir, 't_transcript.json'))

    r = _edit(client, pid, rid, indices=[0, 1], original_text=BLOCK, new_text='x')

    assert r.status_code == 404


def test_a_non_contiguous_block_round_trips(client, app):
    """An interjection splits the segments; the paragraph is still one edit."""
    segments = [
        {'start': 0, 'end': 4, 'text': 'so the cluster was,', 'speaker': 'S1'},
        {'start': 3.5, 'end': 4.3, 'text': 'Right.', 'speaker': 'S2'},
        {'start': 4.9, 'end': 9, 'text': 'well, gone.', 'speaker': 'S1'},
    ]
    with app.app_context():
        pid, rid, proj_dir = _setup(app, segments=segments)

    r = _edit(client, pid, rid, indices=[0, 2],
              original_text='so the cluster was, well, gone.',
              new_text='so the cluster was, well, down.')

    assert r.status_code == 200
    on_disk = _on_disk(proj_dir)['segments']
    assert on_disk[1]['text'] == 'Right.', 'the interjection was edited instead'
    assert on_disk[2]['text'] == 'well, down.'
