"""Tests for annotation file locking and atomic writes."""

import json
import os
import threading

from app.services.annotations import (
    _atomic_write_json,
    _get_lock,
    get_annotations,
    save_annotations,
    update_annotations,
)


class TestAtomicWrite:
    """Verify atomic write behavior."""

    def test_creates_file(self, project_dir):
        path = os.path.join(project_dir, 'test.json')
        _atomic_write_json(path, {'key': 'value'})
        with open(path) as f:
            assert json.load(f) == {'key': 'value'}

    def test_no_temp_files_left(self, project_dir):
        path = os.path.join(project_dir, 'test.json')
        _atomic_write_json(path, {'key': 'value'})
        tmp_files = [f for f in os.listdir(project_dir) if f.endswith('.tmp')]
        assert tmp_files == []

    def test_replaces_existing(self, project_dir):
        path = os.path.join(project_dir, 'test.json')
        _atomic_write_json(path, {'v': 1})
        _atomic_write_json(path, {'v': 2})
        with open(path) as f:
            assert json.load(f)['v'] == 2


class TestGetLock:
    """Verify per-file lock management."""

    def test_same_path_returns_same_lock(self):
        lock1 = _get_lock('/some/unique/path1.json')
        lock2 = _get_lock('/some/unique/path1.json')
        assert lock1 is lock2

    def test_different_paths_return_different_locks(self):
        lock1 = _get_lock('/unique/a.json')
        lock2 = _get_lock('/unique/b.json')
        assert lock1 is not lock2


class TestUpdateAnnotations:
    """Verify atomic read-modify-write via update_annotations."""

    def test_creates_from_defaults(self, project_dir):
        ann = update_annotations(project_dir, 'rec.mp3',
                                 {'speaker_labels': {'SPEAKER_00': 'Moderator'}})
        assert ann['speaker_labels'] == {'SPEAKER_00': 'Moderator'}
        assert ann['tag_spans'] == []
        assert ann['comments'] == []

    def test_preserves_other_keys(self, project_dir):
        save_annotations(project_dir, 'rec.mp3', {
            'tag_spans': [{'tag_id': 'pain'}],
            'comments': [],
            'speaker_labels': {},
        })
        ann = update_annotations(project_dir, 'rec.mp3',
                                 {'speaker_labels': {'SPEAKER_00': 'Moderator'}})
        assert ann['tag_spans'] == [{'tag_id': 'pain'}]
        assert ann['speaker_labels'] == {'SPEAKER_00': 'Moderator'}

    def test_concurrent_updates_no_data_loss(self, project_dir):
        """Two threads updating different keys should not lose data."""
        save_annotations(project_dir, 'rec.mp3', {
            'tag_spans': [],
            'comments': [],
            'speaker_labels': {},
        })

        barrier = threading.Barrier(2)
        errors = []

        def update_tags():
            try:
                barrier.wait(timeout=5)
                for i in range(50):
                    update_annotations(project_dir, 'rec.mp3',
                                       {'tag_spans': [{'tag_id': f't{i}'}]})
            except Exception as e:
                errors.append(e)

        def update_speakers():
            try:
                barrier.wait(timeout=5)
                for i in range(50):
                    update_annotations(project_dir, 'rec.mp3',
                                       {'speaker_labels': {f'SPEAKER_{i:02d}': f'Speaker {i}'}})
            except Exception as e:
                errors.append(e)

        t1 = threading.Thread(target=update_tags)
        t2 = threading.Thread(target=update_speakers)
        t1.start()
        t2.start()
        t1.join(timeout=30)
        t2.join(timeout=30)

        assert not errors
        final = get_annotations(project_dir, 'rec.mp3')
        # Both keys should have their last value, neither should be empty
        assert len(final['tag_spans']) == 1
        assert len(final['speaker_labels']) == 1

    def test_save_annotations_no_temp_files(self, project_dir):
        """Verify no .tmp files are left after save."""
        data = {'tag_spans': [], 'comments': [], 'speaker_labels': {}}
        save_annotations(project_dir, 'rec.mp3', data)
        tmp_files = [f for f in os.listdir(project_dir) if f.endswith('.tmp')]
        assert tmp_files == []
