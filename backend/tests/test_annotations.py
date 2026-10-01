"""Unit tests for annotations service."""

import json
import os

from app.services.annotations import (
    get_annotations,
    get_project_tags,
    get_project_themes,
    save_annotations,
    save_project_tags,
    save_project_themes,
)


class TestProjectTags:
    """Tests for get_project_tags and save_project_tags."""

    def test_get_missing_file_returns_empty(self, project_dir):
        tags = get_project_tags(project_dir)
        assert tags == []

    def test_save_and_get(self, project_dir):
        # Tags are normalized on save/read: a valid hex color and explicit
        # description/group_id round-trip unchanged.
        tags = [{'id': 't1', 'name': 'Tag 1', 'color': '#3366cc',
                 'description': 'When the user is blocked', 'group_id': 'g1'}]
        save_project_tags(project_dir, tags)
        loaded = get_project_tags(project_dir)
        assert loaded == [{
            'id': 't1', 'name': 'Tag 1', 'color': '#3366cc',
            'description': 'When the user is blocked', 'group_id': 'g1',
        }]

    def test_save_normalizes_color_and_defaults(self, project_dir):
        # Unsafe color collapses to a safe token; missing fields are defaulted;
        # entries without id/name (or non-dicts) are dropped.
        tags = [
            {'id': 't1', 'name': 'Tag 1'},
            {'id': 't2', 'name': 'Bad color', 'color': 'red'},
            {'id': '', 'name': 'No id'},
            {'name': 'No id key'},
            'not a dict',
        ]
        save_project_tags(project_dir, tags)
        loaded = get_project_tags(project_dir)
        assert loaded == [
            {'id': 't1', 'name': 'Tag 1', 'color': 'fu', 'description': '', 'group_id': None},
            {'id': 't2', 'name': 'Bad color', 'color': 'fu', 'description': '', 'group_id': None},
        ]

    def test_malformed_file_does_not_crash(self, project_dir):
        # A hand-edited / imported file missing required keys must never raise.
        path = os.path.join(project_dir, 'project_tags.json')
        with open(path, 'w', encoding='utf-8') as f:
            json.dump([{'name': 'no id'}, {'id': 'ok', 'name': 'OK'}], f)
        loaded = get_project_tags(project_dir)
        assert loaded == [{'id': 'ok', 'name': 'OK', 'color': 'fu', 'description': '', 'group_id': None}]

    def test_corrupt_json_returns_empty(self, project_dir):
        path = os.path.join(project_dir, 'project_tags.json')
        with open(path, 'w', encoding='utf-8') as f:
            f.write('{ invalid json')
        tags = get_project_tags(project_dir)
        assert tags == []


class TestProjectThemes:
    """Tests for tag themes (two-level hierarchy)."""

    def test_get_missing_file_returns_empty(self, project_dir):
        assert get_project_themes(project_dir) == []

    def test_save_and_get(self, project_dir):
        themes = [{'id': 'g1', 'name': 'Onboarding', 'color': 'pain'}]
        save_project_themes(project_dir, themes)
        assert get_project_themes(project_dir) == themes

    def test_normalizes_and_drops_invalid(self, project_dir):
        themes = [{'id': 'g1', 'name': 'T', 'color': 'bogus'}, {'name': 'no id'}, 42]
        save_project_themes(project_dir, themes)
        assert get_project_themes(project_dir) == [{'id': 'g1', 'name': 'T', 'color': 'fu'}]


class TestAnnotations:
    """Tests for get_annotations and save_annotations."""

    def test_get_missing_file_returns_defaults(self, project_dir):
        ann = get_annotations(project_dir, 'recording.mp3')
        assert ann == {
            'tag_spans': [],
            'comments': [],
            'speaker_labels': {},
            'speaker_colors': {},
        }

    def test_save_and_get(self, project_dir):
        data = {
            'tag_spans': [{'segment_idx': 0, 'start_char': 0, 'end_char': 5, 'tag_id': 'pain'}],
            'comments': [{'segment_idx': 0, 'text': 'Interesting'}],
            'speaker_labels': {'SPEAKER_00': 'Moderator'},
            'speaker_colors': {'Moderator': 'mod'},
        }
        save_annotations(project_dir, 'rec.mp3', data)
        loaded = get_annotations(project_dir, 'rec.mp3')
        assert loaded == data

    def test_corrupt_json_returns_defaults(self, project_dir):
        base = os.path.splitext('bad.mp3')[0]
        path = os.path.join(project_dir, f'{base}_annotations.json')
        with open(path, 'w', encoding='utf-8') as f:
            f.write('not json at all')
        ann = get_annotations(project_dir, 'bad.mp3')
        assert ann == {
            'tag_spans': [],
            'comments': [],
            'speaker_labels': {},
            'speaker_colors': {},
        }

    def test_unicode_preserved(self, project_dir):
        data = {
            'tag_spans': [],
            'comments': [{'segment_idx': 0, 'text': 'Комментарий 日本語'}],
            'speaker_labels': {},
        }
        save_annotations(project_dir, 'u.mp3', data)
        loaded = get_annotations(project_dir, 'u.mp3')
        assert loaded['comments'][0]['text'] == 'Комментарий 日本語'


class TestSpeakerColorsRoundTrip:
    """speaker_colors must survive a PATCH -> reload, like speaker_labels.

    The recording page applies ``ann.speaker_colors`` on load, so a colour the
    user picks in the speaker popover is only sticky if the PATCH whitelist in
    the annotations endpoint lets the field through.
    """

    def test_patch_speaker_colors_is_persisted_and_returned_by_get(self, client, app):
        with app.app_context():
            from app.extensions import db
            from app.models.project import Project
            from app.models.recording import Recording
            from app.models.setting import Setting

            projects_path = app.config['DEFAULT_PROJECTS_PATH']
            os.makedirs(projects_path, exist_ok=True)
            Setting.set('projects_path', projects_path)

            proj = Project(name='Colours', folder_name='speaker_colours')
            db.session.add(proj)
            db.session.flush()
            rec = Recording(
                project_id=proj.id,
                original_name='interview.mp3',
                stored_name='interview.mp3',
                transcription_status='completed',
            )
            db.session.add(rec)
            db.session.commit()
            pid, rid = proj.id, rec.id

            os.makedirs(os.path.join(projects_path, proj.folder_name), exist_ok=True)

        # Rename a speaker and pick a colour for the new name, as the popover does.
        patch = client.patch(
            f'/api/projects/{pid}/recordings/{rid}/annotations',
            json={
                'speaker_labels': {'SPEAKER_00': 'Пётр'},
                'speaker_colors': {'Пётр': 'p3'},
            },
        )
        assert patch.status_code == 200
        assert patch.get_json()['speaker_colors'] == {'Пётр': 'p3'}

        # What the page sees on reload.
        got = client.get(f'/api/projects/{pid}/recordings/{rid}/annotations')
        assert got.status_code == 200
        data = got.get_json()
        assert data['speaker_labels'] == {'SPEAKER_00': 'Пётр'}
        assert data['speaker_colors'] == {'Пётр': 'p3'}

    def test_patching_other_fields_leaves_speaker_colors_intact(self, client, app):
        with app.app_context():
            from app.extensions import db
            from app.models.project import Project
            from app.models.recording import Recording
            from app.models.setting import Setting

            projects_path = app.config['DEFAULT_PROJECTS_PATH']
            os.makedirs(projects_path, exist_ok=True)
            Setting.set('projects_path', projects_path)

            proj = Project(name='Colours 2', folder_name='speaker_colours_2')
            db.session.add(proj)
            db.session.flush()
            rec = Recording(
                project_id=proj.id,
                original_name='interview.mp3',
                stored_name='interview.mp3',
                transcription_status='completed',
            )
            db.session.add(rec)
            db.session.commit()
            pid, rid = proj.id, rec.id

            os.makedirs(os.path.join(projects_path, proj.folder_name), exist_ok=True)

        client.patch(
            f'/api/projects/{pid}/recordings/{rid}/annotations',
            json={'speaker_colors': {'Moderator': 'mod'}},
        )
        client.patch(
            f'/api/projects/{pid}/recordings/{rid}/annotations',
            json={'comments': [{'segment_idx': 0, 'text': 'Note'}]},
        )

        data = client.get(f'/api/projects/{pid}/recordings/{rid}/annotations').get_json()
        assert data['speaker_colors'] == {'Moderator': 'mod'}
        assert data['comments'] == [{'segment_idx': 0, 'text': 'Note'}]
