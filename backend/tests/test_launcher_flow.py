import re
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
    assert 'call :open_browser_and_confirm_lease "http://pine.localhost:!PINE_BACKEND_PORT!/"' in launcher_layout_source
    assert 'call :wait_for_browser_lease 15' in launcher_layout_source
    assert 'browser lease missing after primary open attempt; leaving diagnostics in logs only' in launcher_layout_source
    assert 'timeout waiting for backend ready; leaving diagnostics in logs only' in launcher_layout_source
    assert 'Diagnostic mode will not open extra browser tabs automatically.' in launcher_layout_source
    assert "Start-Process -FilePath '%PINE_URL%'" in launcher_layout_source
    assert 'rundll32.exe url.dll,FileProtocolHandler "%PINE_URL%"' in launcher_layout_source
    assert "$data.supervisor_running -and $data.lease_count -gt 0" in launcher_layout_source
    assert 'Launcher handoff log was never created' in launcher_layout_source
    assert "Invoke-RestMethod -Uri 'http://127.0.0.1:!PINE_SUP_PORT!/status' -TimeoutSec 2" in launcher_layout_source
    assert "$data.supervisor_running -and $data.backend_ready" in launcher_layout_source
    assert 'call :wait_for_background_launch_and_open' in launcher_layout_source
    assert 'call "%BACKEND_DIR%Setup_WIN.bat"' in launcher_layout_source


def test_installers_leave_the_repo_root_alone():
    """The root tidy waits for Launch PINE; until then the installer is the way back in."""
    windows_installer = (REPO_ROOT / 'Setup_WIN.bat').read_text(encoding='utf-8')
    macos_installer = (REPO_ROOT / 'Setup_MAC.command').read_text(encoding='utf-8')

    # Both may seed backend/ -- reset rebuilds the clean root from those copies.
    assert 'call :seed_backend_installers' in windows_installer
    assert 'seed_backend_installers()' in macos_installer

    # Neither may drop the root installer, move dev files out, or write the
    # shortcut. All three are the finalize step's job now.
    assert 'del /f /q "%PINE_ROOT_DIR%Setup_WIN.bat"' not in windows_installer
    assert 'del /f /q "%PINE_ROOT_DIR%Setup_MAC.command"' not in windows_installer
    assert 'rm -f "$ROOT_DIR/$f"' not in macos_installer
    assert 'dev-config' not in windows_installer
    assert 'dev-config' not in macos_installer
    assert 'Launch Pine.lnk' not in windows_installer


def _installer_label_body(installer, label, next_label):
    return installer.split(f'\n:{label}\n', 1)[1].split(f'\n:{next_label}\n', 1)[0]


def test_every_install_launch_gets_its_own_hidden_log():
    """One shared launcher-hidden.log let a leftover instance lock out the next.

    cmd opens a >> target without write sharing. While an earlier hidden
    instance, or the supervisor it started, held the file, the next one failed
    on its own redirect before running a line, and the window could only say
    PINE never came up.
    """
    installer = (REPO_ROOT / 'Setup_WIN.bat').read_text(encoding='utf-8')

    assert 'set "PINE_HIDDEN_CMD=%LOG_DIR%\\hidden-launch-%PINE_RUN_ID%.cmd"' in installer
    assert 'set "PINE_HIDDEN_LOG=%LOG_DIR%\\launcher-hidden-%PINE_RUN_ID%.log"' in installer
    assert 'echo call "%PINE_HIDDEN_TARGET%" ^>^>"%PINE_HIDDEN_LOG%" 2^>^&1' in installer
    code = '\n'.join(
        line for line in installer.splitlines()
        if not line.strip().upper().startswith('REM ')
    )
    assert 'launcher-hidden.log' not in code


def test_a_failed_install_launch_shows_only_its_own_logs():
    """The newest backend log on disk can be an earlier run's, with another error."""
    installer = (REPO_ROOT / 'Setup_WIN.bat').read_text(encoding='utf-8')
    failure = _installer_label_body(installer, 'show_backend_failure', 'open_browser')

    assert '$since = $h.CreationTime' in failure
    assert '$_.CreationTime -ge $since' in failure
    # The hidden log exists before the hidden instance runs, so it can anchor.
    assert installer.index('type nul > "%PINE_HIDDEN_LOG%"') < installer.index('Start-Process -WindowStyle Hidden')


def test_the_hidden_install_instance_stops_waiting_once_the_supervisor_is_gone():
    """Otherwise it polled a dead backend for six minutes, holding its files."""
    installer = (REPO_ROOT / 'Setup_WIN.bat').read_text(encoding='utf-8')
    wait = _installer_label_body(installer, 'wait_for_ready_and_open', 'reset_venv')

    assert 'curl -sf --max-time 2 http://127.0.0.1:!PINE_SUP_PORT!/status >nul 2>nul' in wait
    assert 'if !WAIT_READY_COUNT! LSS 10 goto waitreadytick' in wait


def test_the_installer_waits_out_a_reset_that_is_still_deleting():
    """A launch inside the post-reset cleanup ran on a venv being deleted under it."""
    installer = (REPO_ROOT / 'Setup_WIN.bat').read_text(encoding='utf-8')
    settings_source = (REPO_ROOT / 'backend' / 'app' / 'api' / 'settings.py').read_text(encoding='utf-8')
    helper = (REPO_ROOT / 'backend' / 'tools' / 'reset_post_cleanup.cmd').read_text(encoding='utf-8')

    # The installer watches the same two files the in-app reset hands off...
    assert "'reset_cleanup_targets.txt'" in settings_source
    assert "'reset_post_cleanup.cmd'" in settings_source
    assert 'set "PINE_RESET_TARGETS=%BACKEND_DIR%tools\\reset_cleanup_targets.txt"' in installer
    assert 'set "PINE_RESET_HELPER=%BACKEND_DIR%tools\\reset_post_cleanup.cmd"' in installer
    # ...whose helper signals the end by deleting its list, and rereads it on
    # every pass, which is what lets the installer stop a stuck one.
    assert 'del /f /q "%LIST%"' in helper
    assert helper.index(':retry') < helper.index('in ("%LIST%")')
    # And it waits before deciding whether the venv is installed.
    assert installer.index('call :wait_for_reset_cleanup') < installer.index('set "NEED_SETUP=0"')


def test_every_label_the_installer_calls_exists():
    """A call to a missing label kills the window without a word."""
    installer = (REPO_ROOT / 'Setup_WIN.bat').read_text(encoding='utf-8')
    labels = set(re.findall(r'^\s*:([A-Za-z_]\w*)\s*$', installer, re.M))
    referenced = set(re.findall(r'\bcall\s+:([A-Za-z_]\w*)', installer))
    referenced |= set(re.findall(r'\bgoto\s+:?([A-Za-z_]\w*)', installer))

    assert referenced - {'eof'} - labels == set()


def test_reset_also_stops_the_hidden_install_launcher():
    """It is cmd.exe, not python, and it keeps its log in logs\\ open."""
    reset = (REPO_ROOT / 'backend' / 'reset_win.bat').read_text(encoding='utf-8')

    assert "Name='cmd.exe'" in reset
    assert "$_.CommandLine -match ($dir + 'logs\\\\hidden-launch')" in reset


def test_root_layout_is_finalized_only_from_the_launch_handoff():
    onboarding_source = (REPO_ROOT / 'backend' / 'app' / 'api' / 'onboarding.py').read_text(encoding='utf-8')
    model_manager_source = (
        REPO_ROOT / 'backend' / 'app' / 'services' / 'model_manager.py'
    ).read_text(encoding='utf-8')

    # /handoff/prepare is what the Launch PINE button calls.
    assert 'def prepare_handoff():' in onboarding_source
    assert 'finalize_root_layout_after_onboarding(' in onboarding_source

    # Finishing the downloads is not finishing onboarding.
    assert '_sync_platform_launcher_layout' not in model_manager_source
    assert '_refresh_windows_launcher_shortcuts' not in model_manager_source


def test_pagehide_releases_the_lease():
    """Every page gives its lease back on the way out.

    This used to be asserted against main.html, which carried its own copy of
    the lease; the six copies are one module now, so the contract is checked
    where it lives.
    """
    lease_source = (
        REPO_ROOT / 'backend' / 'app' / 'static' / 'js' / 'lease.js'
    ).read_text(encoding='utf-8')

    assert "window.addEventListener('pagehide'" in lease_source
    # ...and a page that must not stop the backend releases without quitting.
    assert 'if (quitOnUnload) releaseAndQuit();' in lease_source
    assert 'else release();' in lease_source


def test_onboarding_keeps_the_backend_alive_when_it_closes():
    """The handoff restarts the backend it just installed — quitting would race it."""
    onboarding_source = (
        REPO_ROOT / 'backend' / 'templates' / 'onboarding.html'
    ).read_text(encoding='utf-8')

    assert 'data-quit-on-unload="false"' in onboarding_source
    # And the lease follows the supervisor the handoff started.
    assert 'PineLease.setSupervisor(' in onboarding_source


def test_every_page_holds_a_lease():
    """A page that loads without lease.js lets the backend time out under it."""
    templates = sorted((REPO_ROOT / 'backend' / 'templates').glob('*.html'))

    missing = [
        path.name for path in templates
        if "filename='js/lease.js'" not in path.read_text(encoding='utf-8')
    ]

    assert missing == []
