"""API tests for the transcript find & replace endpoint."""

import os
import json

from app.extensions import db
from app.models.project import Project
from app.models.recording import Recording
from app.models.setting import Setting


def _setup(app, transcript, annotations=None):
    projects_path = app.config['DEFAULT_PROJECTS_PATH']
    os.makedirs(projects_path, exist_ok=True)
    Setting.set('projects_path', projects_path)

    proj = Project(name='FR Proj', folder_name='fr_proj')
    db.session.add(proj)
    db.session.flush()
    rec = Recording(
        project_id=proj.id,
        original_name='t.mp3',
        stored_name='t.mp3',
        transcript_path='t_transcript.json',
        duration_seconds=10,
        transcription_status='transcribed',
    )
    db.session.add(rec)
    db.session.commit()
    pid, rid = proj.id, rec.id

    proj_dir = os.path.join(projects_path, 'fr_proj')
    os.makedirs(proj_dir, exist_ok=True)
    with open(os.path.join(proj_dir, 't_transcript.json'), 'w', encoding='utf-8') as f:
        json.dump(transcript, f)
    if annotations is not None:
        with open(os.path.join(proj_dir, 't_annotations.json'), 'w', encoding='utf-8') as f:
            json.dump(annotations, f)
    return pid, rid, proj_dir


def _replace(client, pid, rid, **payload):
    return client.post(
        f'/api/projects/{pid}/recordings/{rid}/transcript/replace', json=payload)


def test_replace_updates_text_and_persists(client, app):
    with app.app_context():
        pid, rid, proj_dir = _setup(app, {'segments': [
            {'start': 0, 'end': 2, 'text': 'The kubernetis cluster', 'speaker': 'S1'}]})
    r = _replace(client, pid, rid, find='kubernetis', replace='Kubernetes')
    assert r.status_code == 200
    body = r.get_json()
    assert body['count'] == 1
    assert body['transcript']['segments'][0]['text'] == 'The Kubernetes cluster'
    with open(os.path.join(proj_dir, 't_transcript.json'), encoding='utf-8') as f:
        assert json.load(f)['segments'][0]['text'] == 'The Kubernetes cluster'


def test_replace_migrates_annotation_offsets_through_api(client, app):
    text = 'The kubernetis is great'
    start = text.index('great')
    with app.app_context():
        pid, rid, proj_dir = _setup(
            app,
            {'segments': [{'start': 0, 'end': 2, 'text': text, 'speaker': 'S1'}]},
            {'tag_spans': [{'id': 't1', 'tag_id': 'pain', 'segment_idx': 0,
                            'start_char': start, 'end_char': start + len('great')}],
             'comments': []},
        )
    r = _replace(client, pid, rid, find='kubernetis', replace='k8s')
    body = r.get_json()
    span = body['annotations']['tag_spans'][0]
    seg_text = body['transcript']['segments'][0]['text']
    assert seg_text[span['start_char']:span['end_char']] == 'great'
    # Persisted annotations stay aligned too.
    with open(os.path.join(proj_dir, 't_annotations.json'), encoding='utf-8') as f:
        persisted = json.load(f)['tag_spans'][0]
    assert seg_text[persisted['start_char']:persisted['end_char']] == 'great'


def test_replace_no_match_returns_zero(client, app):
    with app.app_context():
        pid, rid, _ = _setup(app, {'segments': [
            {'start': 0, 'end': 1, 'text': 'hello world', 'speaker': 'S1'}]})
    r = _replace(client, pid, rid, find='xyz', replace='q')
    assert r.status_code == 200
    assert r.get_json()['count'] == 0


def test_replace_empty_find_is_400(client, app):
    with app.app_context():
        pid, rid, _ = _setup(app, {'segments': [
            {'start': 0, 'end': 1, 'text': 'hi', 'speaker': 'S1'}]})
    r = _replace(client, pid, rid, find='', replace='q')
    assert r.status_code == 400


def test_replace_404_when_not_transcribed(client, app):
    with app.app_context():
        projects_path = app.config['DEFAULT_PROJECTS_PATH']
        os.makedirs(projects_path, exist_ok=True)
        Setting.set('projects_path', projects_path)
        proj = Project(name='P', folder_name='p_pending')
        db.session.add(proj)
        db.session.flush()
        rec = Recording(project_id=proj.id, original_name='a.mp3', stored_name='a.mp3',
                        transcription_status='pending')
        db.session.add(rec)
        db.session.commit()
        pid, rid = proj.id, rec.id
    r = _replace(client, pid, rid, find='a', replace='b')
    assert r.status_code == 404
