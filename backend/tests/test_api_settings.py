"""API integration tests for settings endpoints."""

import os
from pathlib import Path

from app.api.settings import ALLOWED_KEYS


class TestSettingsAPI:
    """Settings GET/PATCH."""

    def test_get_settings(self, client):
        r = client.get('/api/settings')
        assert r.status_code == 200
        data = r.get_json()
        for key in ALLOWED_KEYS:
            assert key in data

    def test_update_allowed_key(self, client):
        r = client.patch('/api/settings', json={'font_size': '16'})
        assert r.status_code == 200
        assert r.get_json().get('ok') is True

        r2 = client.get('/api/settings')
        assert r2.get_json().get('font_size') == '16'

    def test_font_family_persists(self, client):
        r = client.patch('/api/settings', json={'font_family': 'roboto'})
        assert r.status_code == 200
        r2 = client.get('/api/settings')
        assert r2.get_json().get('font_family') == 'roboto'

    def test_invalid_font_family_rejected(self, client):
        client.patch('/api/settings', json={'font_family': 'inter'})
        r = client.patch('/api/settings', json={'font_family': 'invalid-font'})
        assert r.status_code == 200
        r2 = client.get('/api/settings')
        assert r2.get_json().get('font_family') == 'inter'  # unchanged

    def test_last_open_project_id_persists(self, client):
        r = client.patch('/api/settings', json={'last_open_project_id': '42'})
        assert r.status_code == 200
        assert client.get('/api/settings').get_json().get('last_open_project_id') == '42'

    def test_invalid_last_open_project_id_rejected(self, client):
        client.patch('/api/settings', json={'last_open_project_id': '7'})
        r = client.patch('/api/settings', json={'last_open_project_id': 'not-a-number'})
        assert r.status_code == 200
        assert client.get('/api/settings').get_json().get('last_open_project_id') == '7'

    def test_disallowed_key_ignored(self, client):
        r = client.patch('/api/settings', json={'font_size': '14', 'malicious_key': 'bad'})
        assert r.status_code == 200
        data = r.get_json()
        assert 'updated' in data
        assert 'font_size' in data['updated']
        assert 'malicious_key' not in data.get('updated', {})

    def test_settings_persist_independently(self, client):
        """Updating one setting in a second request does not clobber others."""
        client.patch('/api/settings', json={'font_size': '18', 'theme': 'dark'})
        client.patch('/api/settings', json={'font_size': '20'})
        r = client.get('/api/settings')
        data = r.get_json()
        assert data['font_size'] == '20'
        assert data['theme'] == 'dark'

    def test_backup_runtime_settings_refresh_scheduler(self, client, monkeypatch):
        from app.api import settings as settings_api

        calls = []

        def fake_refresh(_app):
            calls.append('refresh')

        monkeypatch.setattr(
            settings_api,
            'current_app',
            type('FakeCurrentApp', (), {'_get_current_object': staticmethod(lambda: object())}),
        )
        monkeypatch.setattr(
            'app.services.backup_service.refresh_auto_backup',
            fake_refresh,
        )

        r = client.patch('/api/settings', json={'auto_backup_enabled': 'true'})
        assert r.status_code == 200
        assert calls == ['refresh']

    def test_reset_requires_yes(self, client):
        """Reset endpoint rejects requests without confirm: 'Yes'."""
        r = client.post('/api/settings/reset', json={})
        assert r.status_code == 400
        r = client.post('/api/settings/reset', json={'confirm': 'yes'})
        assert r.status_code == 400
        r = client.post('/api/settings/reset', json={'confirm': 'Yes'})
        assert r.status_code == 200
        assert r.get_json().get('ok') is True

    def test_reset_removes_non_essential_workspace_entries(self, client, app):
        """Reset keeps only allowlisted repo/backend entries and deletes the rest."""
        root_dir = app.config['ROOT_DIR']
        backend_dir = os.path.join(root_dir, 'backend')
        os.makedirs(backend_dir, exist_ok=True)

        for rel_path in (
            '.claude',
            '.codex_tmp',
            '.github',
            '.venv',
            'backups',
            'projects',
            'backend/data',
            'backend/pytest-cache-files-1234',
            'backend/__pycache__',
        ):
            abs_path = os.path.join(root_dir, *rel_path.split('/'))
            os.makedirs(abs_path, exist_ok=True)
            with open(os.path.join(abs_path, 'marker.txt'), 'w', encoding='utf-8') as fh:
                fh.write('temp')

        preserved_root_dir = os.path.join(root_dir, 'models')
        os.makedirs(preserved_root_dir, exist_ok=True)
        preserved_backend_dir = os.path.join(backend_dir, 'app')
        os.makedirs(preserved_backend_dir, exist_ok=True)
        backend_data_dir = os.path.join(backend_dir, 'data')
        os.makedirs(backend_data_dir, exist_ok=True)
        launch_bat = os.path.join(root_dir, 'Launch Pine.bat')
        launch_cmd = os.path.join(root_dir, 'Launch Pine.command')
        launch_vbs = os.path.join(root_dir, 'Launch Pine.vbs')
        win_install = os.path.join(root_dir, 'WIN_Install.bat')
        mac_install = os.path.join(root_dir, 'MAC_Install.command')
        backend_win_install = os.path.join(backend_dir, 'WIN_Install.bat')
        backend_mac_install = os.path.join(backend_dir, 'MAC_Install.command')
        onboarding_flag = os.path.join(backend_data_dir, 'onboarding_complete.flag')
        for path in (launch_bat, launch_cmd, launch_vbs, win_install, mac_install):
            with open(path, 'w', encoding='utf-8') as fh:
                fh.write('launcher')
        for path in (backend_win_install, backend_mac_install, onboarding_flag):
            with open(path, 'w', encoding='utf-8') as fh:
                fh.write('backend-state')

        os.remove(win_install)
        os.remove(mac_install)

        r = client.post('/api/settings/reset', json={'confirm': 'Yes'})

        assert r.status_code == 200
        assert r.get_json().get('ok') is True
        assert 'all project data' in r.get_json().get('message', '').lower()

        for rel_path in (
            '.claude',
            '.codex_tmp',
            '.venv',
            'backups',
            'projects',
            'backend/data',
            'backend/pytest-cache-files-1234',
            'backend/__pycache__',
        ):
            abs_path = os.path.join(root_dir, *rel_path.split('/'))
            assert not os.path.exists(abs_path)

        # .github has always been on the allowlist the reset scripts use; the
        # in-app reset deleted it only because it kept a second copy of that
        # list. Both now read backend/tools/reset_preserve_root.txt.
        assert os.path.isdir(os.path.join(root_dir, '.github'))

        assert not os.path.exists(launch_bat)
        assert not os.path.exists(launch_cmd)
        assert not os.path.exists(launch_vbs)
        assert os.path.isfile(win_install)
        assert os.path.isfile(mac_install)
        assert not os.path.exists(backend_win_install)
        assert not os.path.exists(backend_mac_install)
        assert not os.path.exists(onboarding_flag)
        assert os.path.isdir(preserved_root_dir)
        assert os.path.isdir(preserved_backend_dir)

    def test_reset_schedules_followup_cleanup_for_locked_windows_venv(self, client, app, monkeypatch):
        """Reset should schedule a post-shutdown cleanup when Windows leaves venv locked."""
        from app.api import settings as settings_api

        called = {}
        root_dir = app.config['ROOT_DIR']

        def fake_cleanup(_root):
            return [os.path.join(root_dir, '.venv')]

        def fake_schedule(_root, failed_paths):
            called['scheduled'] = list(failed_paths)
            return True

        def fake_shutdown():
            called['shutdown'] = True

        monkeypatch.setattr(settings_api, '_is_windows', lambda: True)
        monkeypatch.setattr(settings_api, '_cleanup_reset_workspace', fake_cleanup)
        monkeypatch.setattr(settings_api, '_schedule_windows_post_reset_cleanup', fake_schedule)
        monkeypatch.setattr(settings_api, '_shutdown_backend_after_reset', fake_shutdown)

        r = client.post('/api/settings/reset', json={'confirm': 'Yes'})

        assert r.status_code == 200
        data = r.get_json()
        assert data.get('ok') is True
        assert data.get('shutdown') is True
        assert 'close to finish reset cleanup' in data.get('message', '').lower()
        assert called['scheduled'] == [os.path.join(root_dir, '.venv')]
        assert called['shutdown'] is True

    def test_reset_removes_start_menu_and_desktop_shortcuts(self, app, client, monkeypatch, tmp_path):
        from app.api import settings as settings_api
        from app.extensions import db
        from app.models import Setting

        start_menu = tmp_path / 'Programs' / 'PINE.lnk'
        desktop = tmp_path / 'Desktop' / 'Launch Pine.lnk'
        legacy_start_menu = tmp_path / 'Programs' / 'Launch Pine.lnk'
        legacy_desktop = tmp_path / 'Desktop' / 'PINE.lnk'
        legacy_desktop_bat = tmp_path / 'Desktop' / 'PINE.bat'

        for path in (start_menu, desktop, legacy_start_menu, legacy_desktop, legacy_desktop_bat):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('shortcut', encoding='utf-8')

        monkeypatch.setattr(settings_api, '_start_menu_launcher_path', lambda: str(start_menu))
        monkeypatch.setattr(settings_api, '_desktop_launcher_paths', lambda: [str(desktop)])
        monkeypatch.setattr(settings_api, '_legacy_start_menu_launcher_paths', lambda: [str(legacy_start_menu)])
        monkeypatch.setattr(settings_api, '_legacy_start_menu_launcher_path', lambda: None)
        monkeypatch.setattr(settings_api, '_legacy_start_menu_bat_path', lambda: None)
        monkeypatch.setattr(settings_api, '_legacy_desktop_launcher_paths', lambda: [str(legacy_desktop)])
        monkeypatch.setattr(settings_api, '_legacy_desktop_bat_paths', lambda: [str(legacy_desktop_bat)])

        with app.app_context():
            Setting.set(settings_api.START_MENU_ENABLED_KEY, 'true')
            Setting.set(settings_api.DESKTOP_ENABLED_KEY, 'true')
            db.session.commit()

        r = client.post('/api/settings/reset', json={'confirm': 'Yes'})

        assert r.status_code == 200
        assert not start_menu.exists()
        assert not desktop.exists()
        assert not legacy_start_menu.exists()
        assert not legacy_desktop.exists()
        assert not legacy_desktop_bat.exists()
        with app.app_context():
            assert Setting.get(settings_api.START_MENU_ENABLED_KEY, 'true') == 'false'
            assert Setting.get(settings_api.DESKTOP_ENABLED_KEY, 'true') == 'false'

    def test_start_menu_status_and_toggle(self, client, monkeypatch, tmp_path):
        from app.api import settings as settings_api

        launch_bat = tmp_path / 'Launch Pine.bat'
        launch_bat.write_text('@echo off\n', encoding='utf-8')
        launcher = tmp_path / 'Programs' / 'PINE.lnk'

        monkeypatch.setattr(settings_api, '_is_windows', lambda: True)
        monkeypatch.setattr(settings_api, '_is_macos', lambda: False)
        monkeypatch.setattr(settings_api, '_launch_win_bat_path', lambda: str(launch_bat))
        monkeypatch.setattr(settings_api, '_start_menu_launcher_path', lambda: str(launcher))
        monkeypatch.setattr(settings_api, '_legacy_start_menu_launcher_paths', list)
        monkeypatch.setattr(settings_api, '_legacy_start_menu_launcher_path', lambda: str(tmp_path / 'Programs' / 'PINE' / 'Open PINE.bat'))
        monkeypatch.setattr(settings_api, '_legacy_start_menu_bat_path', lambda: str(tmp_path / 'Programs' / 'PINE.bat'))
        monkeypatch.setattr(settings_api, '_write_launcher_file', lambda p: (p and (launcher.parent.mkdir(parents=True, exist_ok=True), launcher.write_text('shortcut', encoding='utf-8'))))

        r0 = client.get('/api/settings/start-menu')
        assert r0.status_code == 200
        assert r0.get_json()['supported'] is True
        assert r0.get_json()['added'] is False

        r1 = client.post('/api/settings/start-menu/add')
        assert r1.status_code == 200
        assert r1.get_json()['ok'] is True
        assert r1.get_json()['added'] is True
        assert launcher.exists()

        r2 = client.get('/api/settings/start-menu')
        assert r2.status_code == 200
        assert r2.get_json()['added'] is True

        r3 = client.post('/api/settings/start-menu/remove')
        assert r3.status_code == 200
        assert r3.get_json()['ok'] is True
        assert r3.get_json()['added'] is False
        assert not launcher.exists()

    def test_start_menu_add_rejected_when_unsupported(self, client, monkeypatch):
        from app.api import settings as settings_api

        monkeypatch.setattr(settings_api, '_is_windows', lambda: False)
        monkeypatch.setattr(settings_api, '_is_macos', lambda: False)
        r = client.post('/api/settings/start-menu/add')
        assert r.status_code == 400
        assert 'error' in r.get_json()

    def test_desktop_status_and_toggle(self, client, monkeypatch, tmp_path):
        from app.api import settings as settings_api

        launch_bat = tmp_path / 'Launch Pine.bat'
        launch_bat.write_text('@echo off\n', encoding='utf-8')
        launcher = tmp_path / 'Desktop' / 'Launch Pine.lnk'

        monkeypatch.setattr(settings_api, '_is_windows', lambda: True)
        monkeypatch.setattr(settings_api, '_is_macos', lambda: False)
        monkeypatch.setattr(settings_api, '_launch_win_bat_path', lambda: str(launch_bat))
        monkeypatch.setattr(settings_api, '_desktop_launcher_path', lambda: str(launcher))
        monkeypatch.setattr(settings_api, '_desktop_launcher_paths', lambda: [str(launcher)])
        monkeypatch.setattr(settings_api, '_legacy_desktop_launcher_paths', list)
        monkeypatch.setattr(settings_api, '_legacy_desktop_bat_paths', lambda: [str(tmp_path / 'Desktop' / 'PINE.bat')])
        monkeypatch.setattr(settings_api, '_write_launcher_file', lambda p: (p and (launcher.parent.mkdir(parents=True, exist_ok=True), launcher.write_text('shortcut', encoding='utf-8'))))

        r0 = client.get('/api/settings/desktop')
        assert r0.status_code == 200
        assert r0.get_json()['supported'] is True
        assert r0.get_json()['added'] is False

        r1 = client.post('/api/settings/desktop/add')
        assert r1.status_code == 200
        assert r1.get_json()['ok'] is True
        assert r1.get_json()['added'] is True
        assert launcher.exists()

        r2 = client.post('/api/settings/desktop/remove')
        assert r2.status_code == 200
        assert r2.get_json()['ok'] is True
        assert r2.get_json()['added'] is False
        assert not launcher.exists()

    def test_app_launch_status(self, client, monkeypatch, tmp_path):
        from app.api import settings as settings_api

        launch_bat = tmp_path / 'Launch Pine.bat'
        launch_bat.write_text('@echo off\n', encoding='utf-8')
        start_menu = tmp_path / 'Programs' / 'PINE.lnk'
        desktop = tmp_path / 'Desktop' / 'Launch Pine.lnk'
        start_menu.parent.mkdir(parents=True, exist_ok=True)
        start_menu.write_text('@echo off\n', encoding='utf-8')

        monkeypatch.setattr(settings_api, '_is_windows', lambda: True)
        monkeypatch.setattr(settings_api, '_is_macos', lambda: False)
        monkeypatch.setattr(settings_api, '_launch_win_bat_path', lambda: str(launch_bat))
        monkeypatch.setattr(settings_api, '_start_menu_launcher_path', lambda: str(start_menu))
        monkeypatch.setattr(settings_api, '_desktop_launcher_path', lambda: str(desktop))
        monkeypatch.setattr(settings_api, '_desktop_launcher_paths', lambda: [str(desktop)])
        monkeypatch.setattr(settings_api, '_legacy_start_menu_launcher_paths', list)
        monkeypatch.setattr(settings_api, '_legacy_desktop_launcher_paths', list)

        r = client.get('/api/settings/app-launch')
        assert r.status_code == 200
        data = r.get_json()
        assert data['start_menu']['supported'] is True
        assert data['start_menu']['added'] is True
        assert data['desktop']['supported'] is True
        assert data['desktop']['added'] is False

    def test_app_launch_reports_added_after_user_adds_each_entry(self, client, monkeypatch, tmp_path):
        """Round-trip: nothing on disk reads as not added, POSTing add flips it.

        `added` reports whether the shortcut file is actually present — the
        Setting is a cache that re-syncs to the filesystem on every status
        read (see test_app_launch_status_clears_stale_start_menu_flag_...).
        So the honest "not added yet" state is an empty Start Menu / Desktop,
        not a flag that lags behind a shortcut sitting there.
        """
        from app.api import settings as settings_api

        launch_bat = tmp_path / 'Launch Pine.bat'
        launch_bat.write_text('@echo off\n', encoding='utf-8')
        start_menu = tmp_path / 'Programs' / 'PINE.lnk'
        desktop = tmp_path / 'Desktop' / 'Launch Pine.lnk'
        start_menu.parent.mkdir(parents=True, exist_ok=True)
        desktop.parent.mkdir(parents=True, exist_ok=True)

        monkeypatch.setattr(settings_api, '_is_windows', lambda: True)
        monkeypatch.setattr(settings_api, '_is_macos', lambda: False)
        monkeypatch.setattr(settings_api, '_launch_win_bat_path', lambda: str(launch_bat))
        monkeypatch.setattr(settings_api, '_start_menu_launcher_path', lambda: str(start_menu))
        monkeypatch.setattr(settings_api, '_desktop_launcher_path', lambda: str(desktop))
        monkeypatch.setattr(settings_api, '_desktop_launcher_paths', lambda: [str(desktop)])
        monkeypatch.setattr(settings_api, '_legacy_start_menu_launcher_paths', list)
        monkeypatch.setattr(settings_api, '_legacy_desktop_launcher_paths', list)
        monkeypatch.setattr(settings_api, '_legacy_desktop_bat_paths', list)
        monkeypatch.setattr(
            settings_api,
            '_write_launcher_file',
            lambda p: Path(p).write_text('shortcut', encoding='utf-8'),
        )

        r0 = client.get('/api/settings/app-launch')
        assert r0.status_code == 200
        data0 = r0.get_json()
        assert data0['start_menu']['added'] is False
        assert data0['desktop']['added'] is False

        r1 = client.post('/api/settings/start-menu/add')
        assert r1.status_code == 200
        assert r1.get_json()['added'] is True

        r2 = client.post('/api/settings/desktop/add')
        assert r2.status_code == 200
        assert r2.get_json()['added'] is True

        r3 = client.get('/api/settings/app-launch')
        assert r3.status_code == 200
        data3 = r3.get_json()
        assert data3['start_menu']['added'] is True
        assert data3['desktop']['added'] is True

    def test_app_launch_prompt_retires_once_a_shortcut_is_added(self, client, monkeypatch, tmp_path):
        """The first-run offer stops coming back after the user adds a shortcut.

        main.html shows the card on every launch while `prompt_dismissed` is
        false, so the flag has to flip on the add itself — not only when the
        card's own "Don't ask again" is clicked, or adding from the Settings
        page would leave the offer nagging about a shortcut already on disk.
        """
        from app.api import settings as settings_api

        launch_bat = tmp_path / 'Launch Pine.bat'
        launch_bat.write_text('@echo off\n', encoding='utf-8')
        desktop = tmp_path / 'Desktop' / 'Launch Pine.lnk'
        desktop.parent.mkdir(parents=True, exist_ok=True)

        monkeypatch.setattr(settings_api, '_is_windows', lambda: True)
        monkeypatch.setattr(settings_api, '_is_macos', lambda: False)
        monkeypatch.setattr(settings_api, '_launch_win_bat_path', lambda: str(launch_bat))
        monkeypatch.setattr(settings_api, '_start_menu_launcher_path', lambda: None)
        monkeypatch.setattr(settings_api, '_desktop_launcher_path', lambda: str(desktop))
        monkeypatch.setattr(settings_api, '_desktop_launcher_paths', lambda: [str(desktop)])
        monkeypatch.setattr(settings_api, '_legacy_start_menu_launcher_paths', list)
        monkeypatch.setattr(settings_api, '_legacy_desktop_launcher_paths', list)
        monkeypatch.setattr(settings_api, '_legacy_desktop_bat_paths', list)
        monkeypatch.setattr(
            settings_api,
            '_write_launcher_file',
            lambda p: Path(p).write_text('shortcut', encoding='utf-8'),
        )

        assert client.get('/api/settings/app-launch').get_json()['prompt_dismissed'] is False

        assert client.post('/api/settings/desktop/add').status_code == 200

        assert client.get('/api/settings/app-launch').get_json()['prompt_dismissed'] is True

    def test_app_launch_prompt_can_be_dismissed_without_adding_anything(self, client):
        """"Don't ask again" goes through the ordinary settings PATCH."""
        r = client.patch(
            '/api/settings',
            json={'app_launch_prompt_dismissed': 'true'},
        )
        assert r.status_code == 200
        assert client.get('/api/settings').get_json()['app_launch_prompt_dismissed'] == 'true'
        assert client.get('/api/settings/app-launch').get_json()['prompt_dismissed'] is True

    def test_app_launch_status_clears_stale_start_menu_flag_when_shortcut_missing(self, app, client, monkeypatch, tmp_path):
        from app.api import settings as settings_api
        from app.extensions import db
        from app.models import Setting

        launch_bat = tmp_path / 'Launch Pine.bat'
        launch_bat.write_text('@echo off\n', encoding='utf-8')
        start_menu = tmp_path / 'Programs' / 'PINE.lnk'

        monkeypatch.setattr(settings_api, '_is_windows', lambda: True)
        monkeypatch.setattr(settings_api, '_is_macos', lambda: False)
        monkeypatch.setattr(settings_api, '_launch_win_bat_path', lambda: str(launch_bat))
        monkeypatch.setattr(settings_api, '_start_menu_launcher_path', lambda: str(start_menu))
        monkeypatch.setattr(settings_api, '_legacy_start_menu_launcher_paths', list)
        monkeypatch.setattr(settings_api, '_desktop_launcher_paths', list)
        monkeypatch.setattr(settings_api, '_legacy_desktop_launcher_paths', list)
        monkeypatch.setattr(settings_api, '_legacy_desktop_bat_paths', list)

        # Setting touches the DB, so it needs an app context of its own —
        # the `client` fixture only pushes one for the duration of a request.
        with app.app_context():
            Setting.set(settings_api.START_MENU_ENABLED_KEY, 'true')
            db.session.commit()

        r = client.get('/api/settings/app-launch')

        assert r.status_code == 200
        assert r.get_json()['start_menu']['added'] is False
        with app.app_context():
            assert Setting.get(settings_api.START_MENU_ENABLED_KEY) == 'false'

    def test_start_menu_add_returns_500_when_shortcut_file_is_not_created(self, app, client, monkeypatch, tmp_path):
        from app.api import settings as settings_api
        from app.models import Setting

        launch_bat = tmp_path / 'Launch Pine.bat'
        launch_bat.write_text('@echo off\n', encoding='utf-8')
        launcher = tmp_path / 'Programs' / 'PINE.lnk'

        monkeypatch.setattr(settings_api, '_is_windows', lambda: True)
        monkeypatch.setattr(settings_api, '_is_macos', lambda: False)
        monkeypatch.setattr(settings_api, '_launch_win_bat_path', lambda: str(launch_bat))
        monkeypatch.setattr(settings_api, '_start_menu_launcher_path', lambda: str(launcher))
        monkeypatch.setattr(settings_api, '_legacy_start_menu_launcher_paths', list)
        monkeypatch.setattr(settings_api, '_legacy_start_menu_launcher_path', lambda: str(tmp_path / 'Programs' / 'PINE' / 'Open PINE.bat'))
        monkeypatch.setattr(settings_api, '_legacy_start_menu_bat_path', lambda: str(tmp_path / 'Programs' / 'PINE.bat'))
        monkeypatch.setattr(settings_api, '_write_launcher_file', lambda p: None)

        r = client.post('/api/settings/start-menu/add')

        assert r.status_code == 500
        assert 'error' in r.get_json()
        with app.app_context():
            assert Setting.get(settings_api.START_MENU_ENABLED_KEY, 'false') == 'false'

    def test_launch_win_bat_path_falls_back_to_backend_installer(self, monkeypatch, tmp_path):
        from app.api import settings as settings_api

        backend_dir = tmp_path / 'backend'
        backend_dir.mkdir()
        launch_bat = backend_dir / 'WIN_Install.bat'
        launch_bat.write_text('@echo off\n', encoding='utf-8')

        monkeypatch.setattr(settings_api, '_repo_root', lambda: str(tmp_path))

        assert settings_api._launch_win_bat_path() == str(launch_bat)

    def test_windows_shortcut_target_uses_backend_installer_without_promoting_root_launcher(self, monkeypatch, tmp_path):
        from app.api import settings as settings_api

        backend_dir = tmp_path / 'backend'
        backend_dir.mkdir()
        launch_bat = backend_dir / 'WIN_Install.bat'
        launch_bat.write_text('@echo off\r\necho backend\r\n', encoding='utf-8')

        monkeypatch.setattr(settings_api, '_repo_root', lambda: str(tmp_path))

        target = settings_api._ensure_windows_shortcut_target()

        assert target == str(launch_bat)
        assert not (tmp_path / 'Launch Pine.bat').exists()

    def test_launch_mac_command_path_falls_back_to_backend_installer(self, monkeypatch, tmp_path):
        from app.api import settings as settings_api

        backend_dir = tmp_path / 'backend'
        backend_dir.mkdir()
        launch_cmd = backend_dir / 'MAC_Install.command'
        launch_cmd.write_text('#!/bin/bash\necho ok\n', encoding='utf-8')

        monkeypatch.setattr(settings_api, '_repo_root', lambda: str(tmp_path))

        assert settings_api._launch_mac_command_path() == str(launch_cmd)

    def test_macos_start_menu_status_and_toggle(self, client, monkeypatch, tmp_path):
        from app.api import settings as settings_api

        launch_cmd = tmp_path / 'MAC_Install.command'
        launch_cmd.write_text('#!/bin/bash\necho ok\n', encoding='utf-8')
        launcher = tmp_path / 'Applications' / 'Launch Pine.app'

        monkeypatch.setattr(settings_api, '_is_windows', lambda: False)
        monkeypatch.setattr(settings_api, '_is_macos', lambda: True)
        monkeypatch.setattr(settings_api, '_launch_mac_command_path', lambda: str(launch_cmd))
        monkeypatch.setattr(settings_api, '_start_menu_launcher_path', lambda: str(launcher))
        monkeypatch.setattr(settings_api, '_legacy_start_menu_launcher_paths', list)
        monkeypatch.setattr(settings_api, '_write_launcher_file', lambda p: (p and launcher.mkdir(parents=True, exist_ok=True)))

        r0 = client.get('/api/settings/start-menu')
        assert r0.status_code == 200
        assert r0.get_json()['supported'] is True
        assert r0.get_json()['added'] is False

        r1 = client.post('/api/settings/start-menu/add')
        assert r1.status_code == 200
        assert r1.get_json()['added'] is True
        assert launcher.exists()

        r2 = client.post('/api/settings/start-menu/remove')
        assert r2.status_code == 200
        assert r2.get_json()['added'] is False
        assert not launcher.exists()

    def test_macos_desktop_status_and_toggle(self, client, monkeypatch, tmp_path):
        from app.api import settings as settings_api

        launch_cmd = tmp_path / 'MAC_Install.command'
        launch_cmd.write_text('#!/bin/bash\necho ok\n', encoding='utf-8')
        launcher = tmp_path / 'Desktop' / 'Launch Pine.app'

        monkeypatch.setattr(settings_api, '_is_windows', lambda: False)
        monkeypatch.setattr(settings_api, '_is_macos', lambda: True)
        monkeypatch.setattr(settings_api, '_launch_mac_command_path', lambda: str(launch_cmd))
        monkeypatch.setattr(settings_api, '_desktop_launcher_path', lambda: str(launcher))
        monkeypatch.setattr(settings_api, '_desktop_launcher_paths', lambda: [str(launcher)])
        monkeypatch.setattr(settings_api, '_legacy_desktop_launcher_paths', list)
        monkeypatch.setattr(settings_api, '_legacy_desktop_bat_paths', list)
        monkeypatch.setattr(settings_api, '_write_launcher_file', lambda p: (p and launcher.mkdir(parents=True, exist_ok=True)))

        r0 = client.get('/api/settings/desktop')
        assert r0.status_code == 200
        assert r0.get_json()['supported'] is True
        assert r0.get_json()['added'] is False

        r1 = client.post('/api/settings/desktop/add')
        assert r1.status_code == 200
        assert r1.get_json()['added'] is True
        assert launcher.exists()

        r2 = client.post('/api/settings/desktop/remove')
        assert r2.status_code == 200
        assert r2.get_json()['added'] is False
        assert not launcher.exists()


class TestSttModelChoice:
    """Choosing, installing and removing the transcription model."""

    def _mark_ready(self, app, model_id):
        from app.extensions import db
        from app.models.ml_model import MLModel
        with app.app_context():
            row = db.session.get(MLModel, model_id)
            row.status = 'ready'
            db.session.commit()

    def test_get_settings_lists_choices(self, client):
        from app.services.model_manager import get_default_stt_model

        data = client.get('/api/settings').get_json()
        ids = [m['id'] for m in data['stt_models']]
        assert get_default_stt_model() in ids
        assert all('installed' in m for m in data['stt_models'])

    def test_switch_rejected_while_model_missing(self, client):
        from app.services.model_manager import get_default_stt_model

        r = client.patch('/api/settings', json={'stt_model_id': get_default_stt_model()})
        assert r.status_code == 409
        assert 'not installed' in r.get_json()['error']

    def test_switch_allowed_once_installed(self, app, client):
        from app.services.model_manager import get_default_stt_model

        model_id = get_default_stt_model()
        self._mark_ready(app, model_id)

        r = client.patch('/api/settings', json={'stt_model_id': model_id})
        assert r.status_code == 200
        assert client.get('/api/settings').get_json()['stt_model_id'] == model_id

    def test_install_rejects_unknown_model(self, client):
        r = client.post('/api/settings/stt-model/install', json={'model_id': 'whisper-tiny'})
        assert r.status_code == 400

    def test_remove_endpoint_is_gone(self, client):
        """One model per platform, and it is always the one in use — nothing to remove."""
        from app.services.model_manager import get_default_stt_model

        r = client.post('/api/settings/stt-model/remove',
                        json={'model_id': get_default_stt_model()})
        assert r.status_code == 404

