from app.services import launcher_layout, model_manager, pip_installer


def test_cleanup_cross_platform_launchers_on_mac(tmp_path, monkeypatch):
    mac_install = tmp_path / 'Setup_MAC.command'
    mac_launch = tmp_path / 'Launch Pine.command'
    win_install = tmp_path / 'Setup_WIN.bat'
    win_launch = tmp_path / 'Launch Pine.bat'
    mac_install.write_text('echo mac install', encoding='utf-8')
    mac_launch.write_text('echo mac launch', encoding='utf-8')
    win_install.write_text('echo win install', encoding='utf-8')
    win_launch.write_text('echo win launch', encoding='utf-8')

    monkeypatch.setattr(launcher_layout, 'IS_MAC', True)
    launcher_layout._cleanup_cross_platform_launchers(repo_root=tmp_path)

    assert mac_install.exists()
    assert mac_launch.exists()
    assert not win_install.exists()
    assert not win_launch.exists()


def test_cleanup_cross_platform_launchers_on_windows(tmp_path, monkeypatch):
    mac_install = tmp_path / 'Setup_MAC.command'
    mac_launch = tmp_path / 'Launch Pine.command'
    win_install = tmp_path / 'Setup_WIN.bat'
    win_launch = tmp_path / 'Launch Pine.bat'
    mac_install.write_text('echo mac install', encoding='utf-8')
    mac_launch.write_text('echo mac launch', encoding='utf-8')
    win_install.write_text('echo win install', encoding='utf-8')
    win_launch.write_text('echo win launch', encoding='utf-8')

    monkeypatch.setattr(launcher_layout, 'IS_MAC', False)
    launcher_layout._cleanup_cross_platform_launchers(repo_root=tmp_path)

    assert not mac_install.exists()
    assert not mac_launch.exists()
    assert win_install.exists()
    assert win_launch.exists()


def test_promote_platform_launcher_on_mac(tmp_path, monkeypatch):
    mac_install = tmp_path / 'Setup_MAC.command'
    mac_install.write_text('echo mac install', encoding='utf-8')

    monkeypatch.setattr(launcher_layout, 'IS_MAC', True)
    launcher_layout._promote_platform_launcher(repo_root=tmp_path)

    assert mac_install.exists()
    assert (tmp_path / 'Launch Pine.command').exists()


def test_promote_platform_launcher_on_windows(tmp_path, monkeypatch):
    win_install = tmp_path / 'Setup_WIN.bat'
    win_install.write_text('@echo off\n', encoding='utf-8')

    monkeypatch.setattr(launcher_layout, 'IS_MAC', False)
    launcher_layout._promote_platform_launcher(repo_root=tmp_path)

    assert win_install.exists()
    launcher = tmp_path / 'backend' / 'Launch Pine.bat'
    assert launcher.exists()
    launcher_text = launcher.read_text(encoding='utf-8')
    assert 'if not exist "%BACKEND_DIR%run.py" set "BACKEND_DIR=%SCRIPT_DIR%backend\\"' in launcher_text
    assert 'call "%BACKEND_DIR%Setup_WIN.bat"' in launcher_text
    # Readiness is probed by PowerShell first, with a Python one-liner as
    # fallback. Assert the gate exists on both paths rather than pinning the
    # exact fallback expression, which now keys off the browser lease count.
    assert '$data.supervisor_running -and $data.backend_ready' in launcher_text
    assert "data.get^('supervisor_running'^)" in launcher_text
    assert 'call :wait_for_background_launch_and_open' in launcher_text
    # Diagnostics now re-run startup visibly in the same window via
    # :run_diagnostic_launch instead of spawning a separate one.
    assert 'call :run_diagnostic_launch' in launcher_text
    assert 'set "PINE_LAUNCHER_RUN_ID=%RANDOM%%RANDOM%"' in launcher_text
    assert 'set "PINE_HIDDEN_CMD=%LOG_DIR%\\launcher-hidden-%PINE_LAUNCHER_RUN_ID%.cmd"' in launcher_text
    assert 'set "PINE_LAUNCHER_RUNNER_LOG=%LOG_DIR%\\launcher-runner.log"' in launcher_text
    assert 'Launcher handoff log was never created' in launcher_text
    assert 'start /b "" "%VENV_DIR%\\Scripts\\python.exe" run.py' not in launcher_text


def test_repo_windows_installer_detects_backend_layout():
    # Shipped layout: installers sit at the repo root, where _promote_platform_launcher
    # expects them. _sync_platform_launcher_layout moves them into backend/ only after
    # onboarding, so the repo copy is the root one.
    repo_root = model_manager.Path(__file__).resolve().parents[2]
    installer_path = repo_root / 'Setup_WIN.bat'
    installer_text = installer_path.read_text(encoding='utf-8')

    assert 'set "BACKEND_DIR=%SCRIPT_DIR%"' in installer_text
    assert 'if not exist "%BACKEND_DIR%run.py" set "BACKEND_DIR=%SCRIPT_DIR%backend\\"' in installer_text
    assert 'set "VENV_DIR=%BACKEND_DIR%.venv"' in installer_text
    assert 'set "LOG_DIR=%BACKEND_DIR%logs"' in installer_text
    assert 'cd /d "%BACKEND_DIR%"' in installer_text


def test_sync_platform_launcher_layout_on_windows_moves_installers_to_backend(tmp_path, monkeypatch):
    backend_dir = tmp_path / 'backend'
    win_install = tmp_path / 'Setup_WIN.bat'
    mac_install = tmp_path / 'Setup_MAC.command'
    stale_mac_launch = tmp_path / 'Launch Pine.command'

    win_install.write_text('@echo off\n', encoding='utf-8')
    mac_install.write_text('#!/bin/bash\necho mac install\n', encoding='utf-8')
    stale_mac_launch.write_text('#!/bin/bash\necho stale\n', encoding='utf-8')

    monkeypatch.setattr(launcher_layout, 'IS_MAC', False)
    launcher_layout._sync_platform_launcher_layout(repo_root=tmp_path)

    assert (backend_dir / 'Launch Pine.bat').exists()
    assert not (tmp_path / 'Launch Pine.bat').exists()
    assert not win_install.exists()
    assert not mac_install.exists()
    assert not stale_mac_launch.exists()
    assert (backend_dir / 'Setup_WIN.bat').exists()
    assert (backend_dir / 'Setup_MAC.command').exists()


def test_sync_platform_launcher_layout_on_mac_moves_installers_to_backend(tmp_path, monkeypatch):
    backend_dir = tmp_path / 'backend'
    mac_install = tmp_path / 'Setup_MAC.command'
    win_install = tmp_path / 'Setup_WIN.bat'
    stale_win_launch = tmp_path / 'Launch Pine.bat'

    mac_install.write_text('#!/bin/bash\necho mac install\n', encoding='utf-8')
    win_install.write_text('@echo off\n', encoding='utf-8')
    stale_win_launch.write_text('@echo off\n', encoding='utf-8')

    monkeypatch.setattr(launcher_layout, 'IS_MAC', True)
    launcher_layout._sync_platform_launcher_layout(repo_root=tmp_path)

    assert (tmp_path / 'Launch Pine.command').exists()
    assert not mac_install.exists()
    assert not win_install.exists()
    assert not stale_win_launch.exists()
    assert (backend_dir / 'Setup_MAC.command').exists()
    assert (backend_dir / 'Setup_WIN.bat').exists()


def test_sync_platform_launcher_layout_drops_pre_rename_installers(tmp_path, monkeypatch):
    backend_dir = tmp_path / 'backend'
    legacy_win = tmp_path / 'WIN_Install.bat'
    legacy_mac = tmp_path / 'MAC_Install.command'

    (tmp_path / 'Setup_WIN.bat').write_text('@echo off\n', encoding='utf-8')
    (tmp_path / 'Setup_MAC.command').write_text('#!/bin/bash\necho mac install\n', encoding='utf-8')
    legacy_win.write_text('@echo off\n', encoding='utf-8')
    legacy_mac.write_text('#!/bin/bash\necho legacy\n', encoding='utf-8')

    monkeypatch.setattr(launcher_layout, 'IS_MAC', False)
    launcher_layout._sync_platform_launcher_layout(repo_root=tmp_path)

    assert not legacy_win.exists()
    assert not legacy_mac.exists()
    assert (backend_dir / 'Setup_WIN.bat').exists()
    assert (backend_dir / 'Setup_MAC.command').exists()


def test_sync_platform_launcher_layout_keeps_pre_rename_installer_without_replacement(tmp_path, monkeypatch):
    legacy_win = tmp_path / 'WIN_Install.bat'
    legacy_win.write_text('@echo off\n', encoding='utf-8')

    monkeypatch.setattr(launcher_layout, 'IS_MAC', False)
    launcher_layout._sync_platform_launcher_layout(repo_root=tmp_path)

    assert legacy_win.exists()


def test_restore_default_launcher_layout_after_reset_moves_installers_to_root(tmp_path):
    backend_dir = tmp_path / 'backend'
    data_dir = backend_dir / 'data'
    backend_dir.mkdir()
    data_dir.mkdir()

    (backend_dir / 'Setup_WIN.bat').write_text('@echo off\n', encoding='utf-8')
    (backend_dir / 'Setup_MAC.command').write_text('#!/bin/bash\necho mac install\n', encoding='utf-8')
    (tmp_path / 'Launch Pine.bat').write_text('@echo off\n', encoding='utf-8')
    (tmp_path / 'Launch Pine.command').write_text('#!/bin/bash\necho launch\n', encoding='utf-8')
    (data_dir / 'onboarding_complete.flag').write_text('true\n', encoding='utf-8')

    launcher_layout.restore_default_launcher_layout_after_reset(repo_root=tmp_path)

    assert (tmp_path / 'Setup_WIN.bat').exists()
    assert (tmp_path / 'Setup_MAC.command').exists()
    assert not (tmp_path / 'Launch Pine.bat').exists()
    assert not (tmp_path / 'Launch Pine.command').exists()
    assert not (backend_dir / 'Setup_WIN.bat').exists()
    assert not (backend_dir / 'Setup_MAC.command').exists()
    assert not (data_dir / 'onboarding_complete.flag').exists()


def test_restore_default_launcher_layout_after_reset_keeps_existing_root_installers(tmp_path):
    backend_dir = tmp_path / 'backend'
    backend_dir.mkdir()

    root_win = tmp_path / 'Setup_WIN.bat'
    root_mac = tmp_path / 'Setup_MAC.command'
    root_win.write_text('root win\n', encoding='utf-8')
    root_mac.write_text('root mac\n', encoding='utf-8')
    (backend_dir / 'Setup_WIN.bat').write_text('backend win\n', encoding='utf-8')
    (backend_dir / 'Setup_MAC.command').write_text('backend mac\n', encoding='utf-8')

    launcher_layout.restore_default_launcher_layout_after_reset(repo_root=tmp_path)

    assert root_win.read_text(encoding='utf-8') == 'root win\n'
    assert root_mac.read_text(encoding='utf-8') == 'root mac\n'
    assert not (backend_dir / 'Setup_WIN.bat').exists()
    assert not (backend_dir / 'Setup_MAC.command').exists()


def test_install_log_path_uses_pine_log_dir(tmp_path, monkeypatch):
    monkeypatch.setenv('PINE_LOG_DIR', str(tmp_path))
    path = pip_installer._install_log_path()
    parent = tmp_path.resolve()
    daily = parent / _today_stamp()
    assert daily.is_dir(), 'daily subdir should have been created'
    assert path.startswith(str(daily))
    name = path[len(str(daily)) + 1:]
    assert name.startswith('install-')
    assert name.endswith('.log')


def test_install_emit_writes_to_open_log_file(tmp_path, monkeypatch):
    log_path = tmp_path / 'install.log'
    emitted = []
    monkeypatch.setattr(pip_installer, '_safe_emit', lambda event, data: emitted.append((event, data)))
    fh = open(log_path, 'w', encoding='utf-8', buffering=1)
    monkeypatch.setattr(pip_installer, '_INSTALL_LOG_FH', fh)
    try:
        pip_installer._install_emit('hello world')
        pip_installer._install_emit('line with \\n newline\n')
    finally:
        fh.close()
    contents = log_path.read_text(encoding='utf-8').splitlines()
    assert contents == ['hello world', 'line with \\n newline']
    assert emitted == [
        ('install:log', {'line': 'hello world'}),
        ('install:log', {'line': 'line with \\n newline\n'}),
    ]


def test_install_emit_without_session_only_socketio(monkeypatch):
    emitted = []
    monkeypatch.setattr(pip_installer, '_safe_emit', lambda event, data: emitted.append((event, data)))
    monkeypatch.setattr(pip_installer, '_INSTALL_LOG_FH', None)
    pip_installer._install_emit('pure socket line')
    assert emitted == [('install:log', {'line': 'pure socket line'})]


def _today_stamp():
    from datetime import datetime as _dt
    return _dt.now().strftime('%Y%m%d')
