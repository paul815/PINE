"""Unit tests for annotations service."""

import json
import os

import pytest

from app.services.annotations import (
    get_project_tags,
    save_project_tags,
    get_project_themes,
    save_project_themes,
    get_annotations,
    save_annotations,
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
        }

    def test_save_and_get(self, project_dir):
        data = {
            'tag_spans': [{'segment_idx': 0, 'start_char': 0, 'end_char': 5, 'tag_id': 'pain'}],
            'comments': [{'segment_idx': 0, 'text': 'Interesting'}],
            'speaker_labels': {'SPEAKER_00': 'Moderator'},
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
