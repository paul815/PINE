"""Where PINE's installer and launcher files live on disk.

The repo ships two installers at the root, `Setup_WIN.bat` and
`Setup_MAC.command`. On first run each creates the venv, installs the base
dependencies and seeds a copy of both installers under `backend/` — and stops
there. The root itself is left untouched until onboarding actually succeeds and
the user presses Launch PINE: only then does `finalize_root_layout_after_onboarding`
move the installers and dev files out of the root and put the plain launcher
(`Launch Pine.bat` / `Launch Pine.command`, plus `Launch Pine.lnk` on Windows)
in their place. Until that moment the root installer is the only way back into
an interrupted install, so nothing may remove it early.

This module owns that shuffle and nothing else — it knows no models. Its one
sizeable piece is `_windows_app_launcher_contents()`, a .bat script kept as a
string because it must exist before Python does.

A reset undoes the shuffle: see `restore_default_launcher_layout_after_reset`,
which puts both installers back so the next run starts from a clean slate.
"""

import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

from ..ports import DEFAULT_BACKEND_PORT, DEFAULT_SUPERVISOR_PORT
from .launcher_state import clear_onboarding_complete

log = logging.getLogger(__name__)

IS_MAC = sys.platform == 'darwin'


WIN_INSTALL_LAUNCHER = 'Setup_WIN.bat'

MAC_INSTALL_LAUNCHER = 'Setup_MAC.command'

WIN_APP_LAUNCHER = 'Launch Pine.bat'

MAC_APP_LAUNCHER = 'Launch Pine.command'

LEGACY_WIN_LAUNCHERS = ('Launch_WIN.bat',)

LEGACY_MAC_LAUNCHERS = ('Launch_MAC.command',)

# Installer names PINE shipped before the Setup_* pair. A copy unpacked over
# an older install still has them at the root; mapped to their replacement so
# the cleanup only fires once the current installer is really in place.
LEGACY_INSTALLERS = {
    'WIN_Install.bat': WIN_INSTALL_LAUNCHER,
    'MAC_Install.command': MAC_INSTALL_LAUNCHER,
}

INSTALLER_STORAGE_DIR = 'backend'

# Root files that belong to the source checkout rather than to an installed
# copy, mapped to where the finished install parks them. The root CLAUDE.md
# holds context-mode routing rules, not project documentation, so it lands
# under a name that says so; it is gitignored at both ends. AGENTS.md stays in
# the root — agent tools only look for it there.
#
# .gitattributes is deliberately absent and must stay absent: it carries
# "*.bat text eol=crlf", and cmd.exe cannot find labels in an LF batch file, so
# a checkout without it leaves every installer and launcher unrunnable.
RELOCATED_ROOT_FILES = (
    ('DESIGN.md', 'documentation/DESIGN.md'),
    ('CLAUDE.md', 'documentation/CLAUDE.context-mode.md'),
    ('LICENSE', 'documentation/dev-config/LICENSE'),
    ('.editorconfig', 'documentation/dev-config/.editorconfig'),
    ('.gitignore', 'documentation/dev-config/.gitignore'),
)

def _platform_launcher_sets():
    if IS_MAC:
        return (
            (MAC_APP_LAUNCHER, MAC_INSTALL_LAUNCHER, *LEGACY_MAC_LAUNCHERS),
            (WIN_APP_LAUNCHER, WIN_INSTALL_LAUNCHER, *LEGACY_WIN_LAUNCHERS),
        )
    return (
        (WIN_APP_LAUNCHER, WIN_INSTALL_LAUNCHER, *LEGACY_WIN_LAUNCHERS),
        (MAC_APP_LAUNCHER, MAC_INSTALL_LAUNCHER, *LEGACY_MAC_LAUNCHERS),
    )

def _repo_root_path(repo_root=None):
    return Path(repo_root) if repo_root else Path(__file__).resolve().parents[3]

def _launcher_candidates(root: Path, launcher_name: str):
    return (
        root / INSTALLER_STORAGE_DIR / launcher_name,
        root / launcher_name,
    )

def _first_existing_launcher(root: Path, *launcher_names: str):
    for launcher_name in launcher_names:
        for candidate in _launcher_candidates(root, launcher_name):
            if candidate.exists():
                return candidate
    return None

def _make_executable(path: Path):
    """Give a launcher its execute bit — the thing macOS needs to run a .command."""
    if path.suffix.lower() not in {'.bat', '.command'}:
        return
    path.chmod(path.stat().st_mode | 0o111)

def _copy_launcher(source: Path, target: Path, what: str) -> bool:
    """Copy a launcher into place, executable, without letting a failure escape.

    Every caller wants the same thing and the same failure mode: the layout
    shuffle is cosmetic, so a locked file or a read-only folder must degrade to
    a log line rather than take down the request that triggered it.
    """
    try:
        shutil.copy2(source, target)
        _make_executable(target)
        return True
    except OSError as exc:
        log.warning('Failed to %s %s -> %s: %s', what, source, target, exc)
        return False

def _remove_launcher(path: Path, what: str):
    """Delete a launcher if it is there; log and carry on if it will not go."""
    try:
        if path.exists():
            path.unlink()
    except OSError as exc:
        log.warning('Failed to remove %s %s: %s', what, path, exc)

def _move_file(source: Path, target: Path, what: str):
    """Move one file, degrading a locked path or a read-only folder to a log line.

    A destination that already exists wins: the root copy is then just dropped,
    so re-running this never overwrites what an earlier install parked there.
    """
    if not source.is_file():
        return
    try:
        if target.exists():
            source.unlink()
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(target))
    except OSError as exc:
        log.warning('Failed to %s %s -> %s: %s', what, source, target, exc)

def _relocate_dev_files(repo_root=None):
    """Move the source-checkout files out of the root once the install is done."""
    root = _repo_root_path(repo_root)
    for file_name, relative_destination in RELOCATED_ROOT_FILES:
        _move_file(root / file_name, root / relative_destination, 'relocate root file')

def _restore_dev_files_to_root(repo_root=None):
    """Put the relocated dev files back, so a reset really is a clean slate."""
    root = _repo_root_path(repo_root)
    for file_name, relative_destination in RELOCATED_ROOT_FILES:
        _move_file(root / relative_destination, root / file_name, 'restore root file')

def _settings_api():
    """The settings API module, or None when it cannot be imported.

    Both shortcut helpers reach into it for ``_write_launcher_file``, and both
    have to tolerate it being unavailable: this module is imported during app
    startup, early enough that an import error here must not be fatal.
    """
    try:
        from ..api import settings as settings_api
        return settings_api
    except Exception as exc:
        log.warning('Could not import settings API for launcher shortcuts: %s', exc)
        return None

def _write_shortcut(settings_api, shortcut_path):
    """Write one Windows .lnk, turning the usual failures into a log line."""
    try:
        settings_api._write_launcher_file(str(shortcut_path))
    except (subprocess.CalledProcessError, OSError) as exc:
        log.warning('Failed to write launcher shortcut %s: %s', shortcut_path, exc)

def _windows_app_launcher_contents() -> str:
    """Dedicated post-onboarding launcher generated under backend/.

    The port numbers are stamped in from ``app.ports`` rather than written out
    again here: they are only the *starting* guess anyway, since the supervisor
    scans upward when 5000/5001 are taken and records what it actually bound in
    ``data/supervisor.port``. The script reads that file (``:resolve_ports``)
    for the same reason Setup_WIN.bat does — probing and opening a hardcoded
    port sends the user to whatever else happens to be listening there.
    """
    return _WIN_APP_LAUNCHER_TEMPLATE.replace(
        '@BACKEND_PORT@', str(DEFAULT_BACKEND_PORT),
    ).replace(
        '@SUPERVISOR_PORT@', str(DEFAULT_SUPERVISOR_PORT),
    )


_WIN_APP_LAUNCHER_TEMPLATE = """@echo off
setlocal EnableDelayedExpansion
title PINE - Private Interview ^& Notes Environment
cd /d "%~dp0"

set "SCRIPT_DIR=%~dp0"
set "BACKEND_DIR=%SCRIPT_DIR%"
if not exist "%BACKEND_DIR%run.py" set "BACKEND_DIR=%SCRIPT_DIR%backend\\"
set "PINE_ROOT_DIR=%BACKEND_DIR%"
for %%D in ("%BACKEND_DIR%..") do set "PINE_ROOT_DIR=%%~fD\\"

set "VENV_DIR=%BACKEND_DIR%.venv"
set "LOG_DIR=%BACKEND_DIR%logs"
set "ONBOARDING_FLAG=%BACKEND_DIR%data\\onboarding_complete.flag"
set "PINE_PORT_FILE=%BACKEND_DIR%data\\supervisor.port"
set "PINE_BACKGROUND_WAIT_SECONDS=180"
set "PINE_LAUNCHER_RUN_ID=%RANDOM%%RANDOM%"
set "PINE_STAGE_FILE=%LOG_DIR%\\launcher-stage.txt"
set "PINE_LAUNCHER_LOG=%LOG_DIR%\\launcher.log"
set "PINE_LAUNCHER_RUNNER_LOG=%LOG_DIR%\\launcher-runner.log"
set "PINE_HIDDEN_CMD=%LOG_DIR%\\launcher-hidden-%PINE_LAUNCHER_RUN_ID%.cmd"

if not exist "%LOG_DIR%" mkdir "%LOG_DIR%" >nul 2>nul
if not exist "%VENV_DIR%\\Scripts\\activate.bat" goto :run_installer
if not exist "%VENV_DIR%\\Scripts\\python.exe" goto :run_installer

call "%VENV_DIR%\\Scripts\\activate.bat"
cd /d "%BACKEND_DIR%"
call :read_onboarding_complete
call :cleanup_post_onboarding_root_artifacts
if /I not "%ONBOARDING_COMPLETE%"=="true" goto :run_installer

if /I "%PINE_DIAGNOSTIC_LAUNCH%"=="1" (
    call :run_diagnostic_launch
    exit /b %errorlevel%
)

if /I not "%PINE_HIDDEN_LAUNCH%"=="1" (
    call :reset_launcher_artifacts
    call :relaunch_hidden
    if errorlevel 1 exit /b 1
    call :wait_for_background_launch_and_open
    exit /b 0
)

call :log_launcher_event "hidden launcher entered"
call :write_stage "launcher hidden entry confirmed"
call :ensure_supervisor_ready
if errorlevel 1 exit /b 1
call :ensure_backend_ready
exit /b %errorlevel%

:run_installer
if exist "%BACKEND_DIR%Setup_WIN.bat" (
    call "%BACKEND_DIR%Setup_WIN.bat"
    exit /b %errorlevel%
)
echo.
echo   PINE is not set up yet.
echo   Run backend\\Setup_WIN.bat to finish setup.
echo.
pause
exit /b 1

:read_onboarding_complete
set "ONBOARDING_COMPLETE=false"
if exist "%ONBOARDING_FLAG%" set "ONBOARDING_COMPLETE=true"
goto :eof

:cleanup_post_onboarding_root_artifacts
if /I not "%ONBOARDING_COMPLETE%"=="true" goto :eof
if exist "%PINE_ROOT_DIR%Setup_WIN.bat" del /f /q "%PINE_ROOT_DIR%Setup_WIN.bat" >nul 2>nul
if exist "%PINE_ROOT_DIR%Setup_MAC.command" del /f /q "%PINE_ROOT_DIR%Setup_MAC.command" >nul 2>nul
set "ROOT_LAUNCHER=%PINE_ROOT_DIR%Launch Pine.bat"
if /I not "%~f0"=="%ROOT_LAUNCHER%" if exist "%ROOT_LAUNCHER%" del /f /q "%ROOT_LAUNCHER%" >nul 2>nul
goto :eof

:reset_launcher_artifacts
del /q "%LOG_DIR%\\launcher-hidden*.cmd" >nul 2>nul
del /q "%LOG_DIR%\\launcher-diagnostic*.cmd" >nul 2>nul
del /q "%PINE_STAGE_FILE%" >nul 2>nul
del /q "%PINE_LAUNCHER_LOG%" >nul 2>nul
del /q "%PINE_LAUNCHER_RUNNER_LOG%" >nul 2>nul
goto :eof

:log_launcher_event
set "PINE_EVENT=%~1"
>> "%PINE_LAUNCHER_LOG%" echo [%date% %time%] %PINE_EVENT%
goto :eof

:write_stage
set "PINE_STAGE=%~1"
> "%PINE_STAGE_FILE%" echo %PINE_STAGE%
call :log_launcher_event "stage: %PINE_STAGE%"
goto :eof

:sleep_one_second
REM Deliberately not "timeout /t": the hidden instance runs with its stdio
REM redirected into a log, and timeout exits immediately rather than wait when
REM stdin is not a console -- which silently turns every loop built on it into a
REM busy spin that reports a timeout in milliseconds. Setup_WIN.bat hit this
REM exact failure and settled on ping; the loops below are the same shape.
ping -n 2 127.0.0.1 >nul 2>&1
goto :eof

:latest_log_path
set "%~1="
for /f "usebackq delims=" %%L in (`powershell -NoProfile -Command "$f = Get-ChildItem -LiteralPath '%LOG_DIR%' -Filter '%~2' -File -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 1 -ExpandProperty FullName; if ($f) { Write-Output $f }"`) do set "%~1=%%L"
goto :eof

:print_log_paths
call :latest_log_path PINE_LATEST_SUPERVISOR_LOG "supervisor-*.log"
call :latest_log_path PINE_LATEST_BACKEND_LOG "backend-*.log"
echo   Launcher log: %PINE_LAUNCHER_LOG%
if exist "%PINE_LAUNCHER_RUNNER_LOG%" echo   Launcher runner log: %PINE_LAUNCHER_RUNNER_LOG%
if exist "%PINE_STAGE_FILE%" echo   Launcher stage file: %PINE_STAGE_FILE%
if defined PINE_LATEST_SUPERVISOR_LOG (
    echo   Latest supervisor log: !PINE_LATEST_SUPERVISOR_LOG!
) else (
    echo   Latest supervisor log: ^<not created yet^>
)
if defined PINE_LATEST_BACKEND_LOG (
    echo   Latest backend log: !PINE_LATEST_BACKEND_LOG!
) else (
    echo   Latest backend log: ^<not created yet^>
)
goto :eof

:resolve_ports
REM The ports the supervisor actually bound. It scans upward from the defaults
REM when they are busy (see supervisor.py _find_free_port) and writes the chosen
REM pair to data\\supervisor.port. Without reading it, every probe below and the
REM browser URL point at whatever else is listening on the default port.
REM The same file carries the token every supervisor POST has to present, so
REM this is also what makes /shutdown and /restart below work at all.
REM Safe to call from inside a wait loop: once the file has been read the flag
REM short-circuits the rest, so this costs three python starts per launch.
if not defined PINE_BACKEND_PORT set "PINE_BACKEND_PORT=@BACKEND_PORT@"
if not defined PINE_SUP_PORT set "PINE_SUP_PORT=@SUPERVISOR_PORT@"
if defined PINE_PORTS_RESOLVED goto :eof
if not exist "%PINE_PORT_FILE%" goto :eof
for /f "delims=" %%p in ('python -c "import json,os; print(json.load(open(os.environ['PINE_PORT_FILE'])).get('backend_port',@BACKEND_PORT@))" 2^>nul') do set "PINE_BACKEND_PORT=%%p"
for /f "delims=" %%p in ('python -c "import json,os; print(json.load(open(os.environ['PINE_PORT_FILE'])).get('supervisor_port',@SUPERVISOR_PORT@))" 2^>nul') do set "PINE_SUP_PORT=%%p"
REM Through the environment rather than the command line: a token in an argv
REM shows up in anyone's process list, and these calls run in a loop.
for /f "delims=" %%p in ('python -c "import json,os; print(json.load(open(os.environ['PINE_PORT_FILE'])).get('token',''))" 2^>nul') do set "PINE_SUP_TOKEN=%%p"
set "PINE_PORTS_RESOLVED=1"
call :log_launcher_event "ports resolved backend=!PINE_BACKEND_PORT! supervisor=!PINE_SUP_PORT!"
goto :eof

:start_supervisor_process
if exist "%VENV_DIR%\\Scripts\\pythonw.exe" (
    start "" "%VENV_DIR%\\Scripts\\pythonw.exe" supervisor.py
) else if exist "%VENV_DIR%\\Scripts\\python.exe" (
    powershell -NoProfile -Command "Start-Process -WindowStyle Hidden -FilePath '%VENV_DIR%\\Scripts\\python.exe' -ArgumentList 'supervisor.py'" >nul
) else (
    powershell -NoProfile -Command "Start-Process -WindowStyle Hidden -FilePath 'python' -ArgumentList 'supervisor.py'" >nul
)
goto :eof

:ensure_supervisor_ready
call :write_stage "checking for existing supervisor"
call :resolve_ports
REM A supervisor with no backend behind it is the state a crashed session
REM leaves. Shutting it down here is what lets the fresh one below start
REM cleanly. The probe and its errorlevel test stay adjacent on purpose.
python -c "import json, urllib.request; data=json.loads^(urllib.request.urlopen^('http://127.0.0.1:!PINE_SUP_PORT!/status', timeout=0.5^).read^(^).decode^(^)^); raise SystemExit^(0 if data.get^('supervisor_running'^) and not data.get^('backend_running'^) else 1^)" 2>nul
if not errorlevel 1 (
    call :log_launcher_event "stale supervisor detected; requesting shutdown"
    python -c "import os, urllib.request; req=urllib.request.Request^('http://127.0.0.1:!PINE_SUP_PORT!/shutdown', method='POST', headers={'X-Pine-Supervisor-Token': os.environ.get^('PINE_SUP_TOKEN', ''^)}^); urllib.request.urlopen^(req, timeout=0.5^)" 2>nul
    if errorlevel 1 call :log_launcher_event "stale supervisor shutdown refused; continuing without it"
    call :sleep_one_second
)

call :probe_supervisor_status
if not errorlevel 1 (
    call :write_stage "supervisor status reachable"
    goto :eof
)

call :write_stage "starting supervisor"
call :log_launcher_event "supervisor start attempted"
call :start_supervisor_process

set SUP_WAIT=0
:wait_supervisor_ready
REM 300ms granularity, so this one wait keeps PowerShell: ping cannot go below
REM a second, and 50 whole seconds of it would be a visible startup delay.
powershell -NoProfile -Command "Start-Sleep -Milliseconds 300" >nul
call :resolve_ports
call :probe_supervisor_status
if not errorlevel 1 (
    call :write_stage "supervisor status reachable"
    goto :eof
)
set /a SUP_WAIT+=1
if !SUP_WAIT! EQU 30 (
    call :log_launcher_event "supervisor status still unavailable; retrying launch"
    call :start_supervisor_process
)
if !SUP_WAIT! GEQ 50 (
    call :write_stage "supervisor start timed out"
    exit /b 1
)
goto :wait_supervisor_ready

:ensure_backend_ready
call :write_stage "checking backend readiness"
call :probe_supervisor_backend_ready
if not errorlevel 1 (
    call :write_stage "backend ready"
    goto :eof
)
call :write_stage "waiting for backend startup"
call :wait_for_backend_ready_grace 12
if not errorlevel 1 (
    call :write_stage "backend ready"
    goto :eof
)
call :write_stage "requesting backend restart"
call :log_launcher_event "backend restart requested via supervisor"
REM Token-gated like /shutdown; PINE_SUP_TOKEN comes from :resolve_ports. The
REM errorlevel branch below still matters -- an unreachable or already-exiting
REM supervisor lands there too.
python -c "import os, urllib.request; req=urllib.request.Request^('http://127.0.0.1:!PINE_SUP_PORT!/restart', method='POST', headers={'X-Pine-Supervisor-Token': os.environ.get^('PINE_SUP_TOKEN', ''^)}^); urllib.request.urlopen^(req, timeout=5^)" 2>nul
if errorlevel 1 (
    call :log_launcher_event "backend restart request failed; rechecking backend readiness"
    call :write_stage "backend restart request failed; rechecking readiness"
    call :wait_for_backend_ready_grace 12
    if errorlevel 1 (
        call :write_stage "backend restart request failed"
        exit /b 1
    )
    call :write_stage "backend ready"
    goto :eof
)
call :write_stage "backend spawn requested"
call :wait_for_supervised_backend
goto :eof

:wait_for_supervised_backend
set WAIT_COUNT=0
:waitsupervisedloop
if !WAIT_COUNT! GEQ 300 (
    call :write_stage "backend readiness timed out"
    exit /b 1
)
call :sleep_one_second
call :probe_supervisor_backend_ready
if errorlevel 1 (
    set /a WAIT_COUNT+=1
    goto waitsupervisedloop
)
call :write_stage "backend ready"
goto :eof

:wait_for_backend_ready_grace
set "WAIT_READY_GRACE=%~1"
if not defined WAIT_READY_GRACE set "WAIT_READY_GRACE=10"
set WAIT_READY_GRACE_COUNT=0
:waitreadygraceloop
if !WAIT_READY_GRACE_COUNT! GEQ !WAIT_READY_GRACE! exit /b 1
call :sleep_one_second
call :probe_supervisor_backend_ready
if errorlevel 1 (
    set /a WAIT_READY_GRACE_COUNT+=1
    goto waitreadygraceloop
)
exit /b 0

:relaunch_hidden
call :write_stage "launcher handoff started"
call :log_launcher_event "launcher handoff started"
(
    echo @echo off
    echo setlocal
    echo set "PINE_HIDDEN_LAUNCH=1"
    echo call "%~f0" ^>^> "%PINE_LAUNCHER_RUNNER_LOG%" 2^>^&1
) > "%PINE_HIDDEN_CMD%"
powershell -NoProfile -Command "Start-Process -WindowStyle Hidden -WorkingDirectory '%~dp0' -FilePath '%PINE_HIDDEN_CMD%'" >nul
if errorlevel 1 (
    call :write_stage "launcher handoff failed"
    call :log_launcher_event "launcher handoff failed"
    exit /b 1
)
goto :eof

:wait_for_background_launch_and_open
echo.
echo   Waiting for PINE to become ready...
call :print_current_startup_status
set WAIT_BG_COUNT=0
:waitbgloop
if !WAIT_BG_COUNT! GEQ %PINE_BACKGROUND_WAIT_SECONDS% (
    call :write_stage "timeout waiting for backend ready; see launcher logs"
    call :log_launcher_event "timeout waiting for backend ready; leaving diagnostics in logs only"
    echo   PINE started in the background, but the app was not ready within %PINE_BACKGROUND_WAIT_SECONDS% seconds.
    call :print_log_paths
    if not exist "%PINE_LAUNCHER_LOG%" echo   Launcher handoff log was never created, which usually means hidden launch failed before startup logging began.
    goto :eof
)
call :sleep_one_second
call :resolve_ports
call :print_current_startup_status
call :probe_supervisor_backend_ready
if errorlevel 1 (
    set /a WAIT_BG_COUNT+=1
    goto waitbgloop
)
call :log_launcher_event "backend ready; opening browser"
REM pine.localhost, not 127.0.0.1: the named host is the app's own origin, so it
REM keeps its own localStorage. Opening the numeric spelling here would hand the
REM user a second, empty copy of every UI preference the installer's browser
REM open (which uses the name) had set up. See app/ports.py APP_HOSTNAME.
call :open_browser_and_confirm_lease "http://pine.localhost:!PINE_BACKEND_PORT!/"
goto :eof

:run_diagnostic_launch
title PINE - Startup Diagnostics
echo.
echo   PINE could not finish starting in the background.
echo   Entering diagnostic mode...
call :print_log_paths
echo.
if not exist "%PINE_LAUNCHER_LOG%" (
    echo   Launcher log was not created, which points to a failed hidden relaunch handoff.
    echo.
)
call :print_current_startup_status
echo.
echo   Re-running startup visibly so the failing stage is shown here.
echo   Diagnostic mode will not open extra browser tabs automatically.
echo.
call :log_launcher_event "diagnostic launch entered"
call :write_stage "diagnostic launch entered"
call :ensure_supervisor_ready
if errorlevel 1 (
    echo   Supervisor failed to become reachable in diagnostic mode.
    call :print_log_paths
    goto :diagnostic_hold
)
call :ensure_backend_ready
if errorlevel 1 (
    echo   Backend failed to become ready in diagnostic mode.
    call :print_log_paths
    goto :diagnostic_hold
)
echo   Backend is ready.
call :print_log_paths
:diagnostic_hold
echo.
echo   This window remains open for diagnostics.
pause
exit /b 0

:open_browser_and_confirm_lease
set "PINE_URL=%~1"
call :open_browser "%PINE_URL%"
call :wait_for_browser_lease 15
if not errorlevel 1 goto :eof
call :write_stage "browser did not connect; see launcher logs"
call :log_launcher_event "browser lease missing after primary open attempt; leaving diagnostics in logs only"
goto :eof

:open_browser
set "PINE_URL=%~1"
powershell -NoProfile -Command "Start-Process -FilePath '%PINE_URL%'" >nul 2>nul
if not errorlevel 1 goto :eof
REM Without this the fallback below was unreachable: nothing called it, so a
REM failed Start-Process ended the launch with "browser did not connect" and no
REM second attempt, even though two working ones were sitting right here.
call :log_launcher_event "powershell browser open failed; trying cmd start"
call :open_browser_fallback "%PINE_URL%"
goto :eof

:open_browser_fallback
set "PINE_URL=%~1"
start "" "%PINE_URL%" >nul 2>nul
if not errorlevel 1 goto :eof
call :log_launcher_event "cmd start browser fallback failed; trying rundll32"
rundll32.exe url.dll,FileProtocolHandler "%PINE_URL%" >nul 2>nul
goto :eof

:wait_for_browser_lease
set "WAIT_LEASE_SECONDS=%~1"
if not defined WAIT_LEASE_SECONDS set "WAIT_LEASE_SECONDS=10"
set WAIT_LEASE_COUNT=0
:waitbrowserleaseloop
call :probe_supervisor_lease_active
if not errorlevel 1 exit /b 0
if !WAIT_LEASE_COUNT! GEQ !WAIT_LEASE_SECONDS! exit /b 1
call :sleep_one_second
set /a WAIT_LEASE_COUNT+=1
goto waitbrowserleaseloop
goto :eof

REM All three probes ask the same endpoint and now do it the same way. The lease
REM one used to be a python -c one-liner instead, which meant two spellings of
REM "GET /status and read a field" drifting independently in one file.
:probe_supervisor_status
powershell -NoProfile -Command "try { $null = Invoke-RestMethod -Uri 'http://127.0.0.1:!PINE_SUP_PORT!/status' -TimeoutSec 2; exit 0 } catch { exit 1 }" >nul 2>nul
goto :eof

:probe_supervisor_backend_ready
powershell -NoProfile -Command "try { $data = Invoke-RestMethod -Uri 'http://127.0.0.1:!PINE_SUP_PORT!/status' -TimeoutSec 2; if ($data.supervisor_running -and $data.backend_ready) { exit 0 } else { exit 1 } } catch { exit 1 }" >nul 2>nul
goto :eof

:probe_supervisor_lease_active
powershell -NoProfile -Command "try { $data = Invoke-RestMethod -Uri 'http://127.0.0.1:!PINE_SUP_PORT!/status' -TimeoutSec 2; if ($data.supervisor_running -and $data.lease_count -gt 0) { exit 0 } else { exit 1 } } catch { exit 1 }" >nul 2>nul
goto :eof

:print_current_startup_status
set "PINE_STATUS=Starting supervisor..."
if exist "%PINE_STAGE_FILE%" (
    set /p PINE_STATUS=<"%PINE_STAGE_FILE%"
)
if not defined LAST_PINE_STATUS (
    echo   Status: !PINE_STATUS!
) else if /I not "!LAST_PINE_STATUS!"=="!PINE_STATUS!" (
    echo   Status: !PINE_STATUS!
)
set "LAST_PINE_STATUS=!PINE_STATUS!"
goto :eof
"""

def _promote_platform_launcher(repo_root=None):
    """Create or refresh the app launcher from the current platform installer."""
    root = _repo_root_path(repo_root)
    current_launchers, _ = _platform_launcher_sets()
    if IS_MAC:
        # macOS has no .lnk equivalent, so the .command file *is* what the user
        # double-clicks and it belongs at the repo root. On Windows the
        # generated .bat stays under backend/ and _ensure_root_windows_shortcut
        # puts the user-facing shortcut at the root instead.
        target_path = root / current_launchers[0]
    else:
        target_path = root / INSTALLER_STORAGE_DIR / current_launchers[0]
    target_path.parent.mkdir(parents=True, exist_ok=True)
    if not IS_MAC and target_path.name == WIN_APP_LAUNCHER:
        try:
            target_path.write_text(
                _windows_app_launcher_contents(),
                encoding='utf-8',
                newline='\r\n',
            )
            _make_executable(target_path)
        except OSError as exc:
            log.warning('Failed to write generated launcher %s: %s', target_path, exc)
        return
    source_path = _first_existing_launcher(root, *current_launchers[1:])
    if source_path is None:
        return
    _copy_launcher(source_path, target_path, 'copy launcher')

def _move_installers_to_backend(repo_root=None):
    """Store both platform install launchers under backend/ after onboarding."""
    root = _repo_root_path(repo_root)
    backend_dir = root / INSTALLER_STORAGE_DIR
    backend_dir.mkdir(parents=True, exist_ok=True)

    for installer_name in (WIN_INSTALL_LAUNCHER, MAC_INSTALL_LAUNCHER):
        source_path = root / installer_name
        target_path = backend_dir / installer_name
        if not source_path.exists():
            continue
        # The unlink stays conditional on the copy, as before: dropping the root
        # installer without a stored copy would leave no way to reinstall.
        if _copy_launcher(source_path, target_path, 'move installer'):
            _remove_launcher(source_path, 'moved installer')

    for legacy_name, current_name in LEGACY_INSTALLERS.items():
        legacy_path = root / legacy_name
        # Never the last installer standing: the pre-rename copy only goes once
        # its replacement exists, at the root or already stored under backend/.
        if legacy_path.exists() and _first_existing_launcher(root, current_name):
            _remove_launcher(legacy_path, 'legacy installer')

def _sync_platform_launcher_layout(repo_root=None):
    """Keep post-install launcher layout consistent across upgrades and first install."""
    _promote_platform_launcher(repo_root=repo_root)
    _move_installers_to_backend(repo_root=repo_root)
    _cleanup_cross_platform_launchers(repo_root=repo_root)
    _ensure_root_windows_shortcut(repo_root=repo_root)

def finalize_root_layout_after_onboarding(repo_root=None):
    """The install's last step, run when the user presses Launch PINE.

    Everything here rearranges what the user sees in the repo root, so it waits
    for the one moment the install is known to have succeeded. Before that the
    root installer has to stay put: it is the only way back into an onboarding
    that was interrupted, cancelled or never finished downloading.
    """
    _relocate_dev_files(repo_root=repo_root)
    _sync_platform_launcher_layout(repo_root=repo_root)
    _refresh_windows_launcher_shortcuts()

def _ensure_root_windows_shortcut(repo_root=None):
    """Ensure the repo-root Launch Pine.lnk exists after onboarding."""
    if sys.platform != 'win32':
        return
    settings_api = _settings_api()
    if settings_api is None:
        return
    _write_shortcut(settings_api, _repo_root_path(repo_root) / 'Launch Pine.lnk')

def restore_default_launcher_layout_after_reset(repo_root=None):
    """Return launcher files to the original pre-install repo layout."""
    root = _repo_root_path(repo_root)
    backend_dir = root / INSTALLER_STORAGE_DIR

    clear_onboarding_complete(root)
    _restore_dev_files_to_root(repo_root=repo_root)

    for launcher_name in (
        WIN_APP_LAUNCHER,
        MAC_APP_LAUNCHER,
        *LEGACY_WIN_LAUNCHERS,
        *LEGACY_MAC_LAUNCHERS,
    ):
        _remove_launcher(root / launcher_name, 'reset launcher')

    for installer_name in (WIN_INSTALL_LAUNCHER, MAC_INSTALL_LAUNCHER):
        root_installer = root / installer_name
        backend_installer = backend_dir / installer_name

        if not root_installer.exists() and backend_installer.exists():
            _copy_launcher(backend_installer, root_installer, 'restore installer')

        # Only once the root copy is really back does the stored one go.
        if not root_installer.exists():
            continue

        _remove_launcher(backend_installer, 'backend installer')

def _refresh_windows_launcher_shortcuts():
    """Re-save existing Windows shortcuts so they point at the renamed launcher and icon."""
    if sys.platform != 'win32':
        return
    settings_api = _settings_api()
    if settings_api is None:
        return

    shortcut_paths = [
        settings_api._start_menu_launcher_path(),
        *settings_api._legacy_start_menu_launcher_paths(),
        *settings_api._desktop_launcher_paths(),
        *settings_api._legacy_desktop_launcher_paths(),
    ]
    for shortcut_path in shortcut_paths:
        if not shortcut_path or not os.path.isfile(shortcut_path):
            continue
        _write_shortcut(settings_api, shortcut_path)

def _cleanup_cross_platform_launchers(repo_root=None):
    """Remove launcher scripts that don't match the current platform."""
    root = _repo_root_path(repo_root)
    _, to_remove = _platform_launcher_sets()
    for launcher_name in to_remove:
        _remove_launcher(root / launcher_name, 'cross-platform launcher')
