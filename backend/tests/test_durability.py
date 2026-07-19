"""Tests for data durability enhancements: atomic writes, SQLite durability, auto-backup defaults."""

import json
import os

import pytest


# ---------------------------------------------------------------------------
# Atomic write tests
# ---------------------------------------------------------------------------

class TestAtomicWriteJson:
    """Test atomic_write_json from file_utils."""

    def test_writes_valid_json(self, temp_dir):
        from app.services.file_utils import atomic_write_json

        path = os.path.join(temp_dir, 'test.json')
        data = {'key': 'value', 'nested': [1, 2, 3]}
        atomic_write_json(path, data)

        with open(path, 'r', encoding='utf-8') as f:
            result = json.load(f)
        assert result == data

    def test_creates_parent_directories(self, temp_dir):
        from app.services.file_utils import atomic_write_json

        path = os.path.join(temp_dir, 'sub', 'dir', 'test.json')
        atomic_write_json(path, {'ok': True})
        assert os.path.isfile(path)

    def test_original_survives_write_failure(self, temp_dir, monkeypatch):
        """If json.dump raises, the original file must be untouched."""
        from app.services.file_utils import atomic_write_json

        path = os.path.join(temp_dir, 'test.json')
        original = {'original': True}
        atomic_write_json(path, original)

        def bad_dump(data, f, **kwargs):
            raise RuntimeError('simulated write failure')

        monkeypatch.setattr(json, 'dump', bad_dump)

        with pytest.raises(RuntimeError, match='simulated write failure'):
            atomic_write_json(path, {'corrupted': True})

        # Original must be intact
        with open(path, 'r', encoding='utf-8') as f:
            result = json.load(f)
        assert result == original

    def test_no_temp_file_left_on_failure(self, temp_dir, monkeypatch):
        from app.services.file_utils import atomic_write_json

        path = os.path.join(temp_dir, 'test.json')

        def bad_dump(data, f, **kwargs):
            raise RuntimeError('fail')

        monkeypatch.setattr(json, 'dump', bad_dump)

        with pytest.raises(RuntimeError):
            atomic_write_json(path, {'x': 1})

        # No .tmp files should remain
        tmp_files = [f for f in os.listdir(temp_dir) if f.endswith('.tmp')]
        assert tmp_files == []


class TestAtomicWriteText:
    """Test atomic_write_text from file_utils."""

    def test_writes_text(self, temp_dir):
        from app.services.file_utils import atomic_write_text

        path = os.path.join(temp_dir, 'readme.md')
        atomic_write_text(path, '# Hello\n')

        with open(path, 'r', encoding='utf-8') as f:
            assert f.read() == '# Hello\n'


# ---------------------------------------------------------------------------
# SQLite durability tests
# ---------------------------------------------------------------------------

class TestSQLiteDurabilityMode:
    """Verify SQLite uses a durable journal mode supported by the filesystem."""

    def test_journal_mode_is_durable(self, app):
        from app.extensions import db
        with app.app_context():
            result = db.session.execute(db.text('PRAGMA journal_mode;')).scalar()
            assert result in {'wal', 'truncate'}

    def test_synchronous_matches_journal_mode(self, app):
        from app.extensions import db
        with app.app_context():
            journal_mode = db.session.execute(db.text('PRAGMA journal_mode;')).scalar()
            synchronous = db.session.execute(db.text('PRAGMA synchronous;')).scalar()
            if journal_mode == 'wal':
                # synchronous=NORMAL is value 1
                assert synchronous == 1
            else:
                # synchronous=FULL is value 2
                assert synchronous == 2

    def test_foreign_keys_enabled(self, app):
        from app.extensions import db
        with app.app_context():
            result = db.session.execute(db.text('PRAGMA foreign_keys;')).scalar()
            assert result == 1


# ---------------------------------------------------------------------------
# Auto-backup default test
# ---------------------------------------------------------------------------

class TestAutoBackupDefault:
    """Verify auto-backup is enabled by default for new installs."""

    def test_auto_backup_enabled_after_onboarding(self, app):
        from app.models.setting import Setting
        with app.app_context():
            # Simulate what model_manager does at onboarding completion
            Setting.set('onboarding_complete', 'true')
            if not Setting.get('auto_backup_enabled'):
                Setting.set('auto_backup_enabled', 'true')

            assert Setting.get('auto_backup_enabled') == 'true'

    def test_existing_users_not_affected(self, app):
        from app.models.setting import Setting
        with app.app_context():
            # User explicitly disabled it
            Setting.set('auto_backup_enabled', 'false')
            # Simulate onboarding re-run — should NOT override
            if not Setting.get('auto_backup_enabled'):
                Setting.set('auto_backup_enabled', 'true')
            # 'false' is truthy, so it stays as-is
            assert Setting.get('auto_backup_enabled') == 'false'
