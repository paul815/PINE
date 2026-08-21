from pathlib import Path

from app import _launch_page_html

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_launch_page_uses_windows_safe_fallback():
    body = _launch_page_html()

    assert 'isWindows' in body
    assert 'window.location.replace("/")' in body
    assert 'window.open("/","pine_app")' in body


def test_index_route_still_serves_primary_app_entry():
    app_source = (REPO_ROOT / 'backend' / 'app' / '__init__.py').read_text(encoding='utf-8')

    assert "@app.route('/')" in app_source
    assert "return render_template('main.html')" in app_source
    assert "return render_template('onboarding.html')" in app_source


def test_generated_windows_launcher_uses_supervisor_handoff_flow():
    launcher_layout_source = (REPO_ROOT / 'backend' / 'app' / 'services' / 'launcher_layout.py').read_text(encoding='utf-8')

    assert 'def _windows_app_launcher_contents()' in launcher_layout_source
    assert 'set "PINE_BACKGROUND_WAIT_SECONDS=180"' in launcher_layout_source
    assert 'call :print_current_startup_status' in launcher_layout_source
    assert 'set "PINE_LAUNCHER_RUN_ID=%RANDOM%%RANDOM%"' in launcher_layout_source
    assert 'set "PINE_HIDDEN_CMD=%LOG_DIR%\\\\launcher-hidden-%PINE_LAUNCHER_RUN_ID%.cmd"' in launcher_layout_source
    assert 'set "PINE_LAUNCHER_RUNNER_LOG=%LOG_DIR%\\\\launcher-runner.log"' in launcher_layout_source
    assert 'call :run_diagnostic_launch' in launcher_layout_source
    assert 'call :print_log_paths' in launcher_layout_source
    assert 'call :open_browser_and_confirm_lease "http://127.0.0.1:5000/"' in launcher_layout_source
    assert 'call :wait_for_browser_lease 15' in launcher_layout_source
    assert 'browser lease missing after primary open attempt; leaving diagnostics in logs only' in launcher_layout_source
    assert 'timeout waiting for backend ready; leaving diagnostics in logs only' in launcher_layout_source
    assert 'Diagnostic mode will not open extra browser tabs automatically.' in launcher_layout_source
    assert "Start-Process -FilePath '%PINE_URL%'" in launcher_layout_source
    assert 'rundll32.exe url.dll,FileProtocolHandler "%PINE_URL%"' in launcher_layout_source
    assert "data.get^('lease_count'^) or 0" in launcher_layout_source
    assert 'Launcher handoff log was never created' in launcher_layout_source
    assert "Invoke-RestMethod -Uri 'http://127.0.0.1:5001/status' -TimeoutSec 2" in launcher_layout_source
    assert "$data.supervisor_running -and $data.backend_ready" in launcher_layout_source
    assert 'call :wait_for_background_launch_and_open' in launcher_layout_source
    assert 'call "%BACKEND_DIR%WIN_Install.bat"' in launcher_layout_source


def test_windows_installer_checks_shortcut_creation_result():
    installer_source = (REPO_ROOT / 'WIN_Install.bat').read_text(encoding='utf-8')

    assert 'set "PINE_SHORTCUT_PATH=%PINE_ROOT_DIR%Launch Pine.lnk"' in installer_source
    assert 'CreateShortcut($env:PINE_SHORTCUT_PATH)' in installer_source
    assert 'if exist "%PINE_SHORTCUT_PATH%" goto :eof' in installer_source
    assert 'Warning: could not create Launch Pine.lnk in the repo root.' in installer_source


def test_main_pagehide_releases_lease_without_quitting_backend():
    main_source = (REPO_ROOT / 'backend' / 'templates' / 'main.html').read_text(encoding='utf-8')

    assert 'function releaseLeaseOnly()' in main_source
    assert "window.addEventListener('pagehide', () => {" in main_source
    assert '  releaseLeaseOnly();' in main_source
