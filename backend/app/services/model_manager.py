import os
import sys
import subprocess
import shutil
import time
import threading
import logging
from datetime import datetime
from pathlib import Path

from ..extensions import db, socketio
from ..models.ml_model import MLModel
from .launcher_state import clear_onboarding_complete, mark_onboarding_complete

log = logging.getLogger(__name__)

WIN_INSTALL_LAUNCHER = 'WIN_Install.bat'
MAC_INSTALL_LAUNCHER = 'MAC_Install.command'
WIN_APP_LAUNCHER = 'Launch Pine.bat'
MAC_APP_LAUNCHER = 'Launch Pine.command'
LEGACY_WIN_LAUNCHERS = ('Launch_WIN.bat',)
LEGACY_MAC_LAUNCHERS = ('Launch_MAC.command',)
INSTALLER_STORAGE_DIR = 'backend'


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


def _windows_app_launcher_contents() -> str:
    """Dedicated post-onboarding launcher generated under backend/."""
    return """@echo off
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
set "PINE_BACKGROUND_WAIT_SECONDS=180"
set "PINE_LAUNCHER_RUN_ID=%RANDOM%%RANDOM%"
set "PINE_STAGE_FILE=%LOG_DIR%\\launcher-stage.txt"
set "PINE_LAUNCHER_LOG=%LOG_DIR%\\launcher.log"
set "PINE_LAUNCHER_RUNNER_LOG=%LOG_DIR%\\launcher-runner.log"
set "PINE_HIDDEN_CMD=%LOG_DIR%\\launcher-hidden-%PINE_LAUNCHER_RUN_ID%.cmd"
set "PINE_DIAGNOSTIC_CMD=%LOG_DIR%\\launcher-diagnostic-%PINE_LAUNCHER_RUN_ID%.cmd"

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
if exist "%BACKEND_DIR%WIN_Install.bat" (
    call "%BACKEND_DIR%WIN_Install.bat"
    exit /b %errorlevel%
)
echo.
echo   PINE is not set up yet.
echo   Run backend\\WIN_Install.bat to finish setup.
echo.
pause
exit /b 1

:read_onboarding_complete
set "ONBOARDING_COMPLETE=false"
if exist "%ONBOARDING_FLAG%" set "ONBOARDING_COMPLETE=true"
goto :eof

:cleanup_post_onboarding_root_artifacts
if /I not "%ONBOARDING_COMPLETE%"=="true" goto :eof
if exist "%PINE_ROOT_DIR%WIN_Install.bat" del /f /q "%PINE_ROOT_DIR%WIN_Install.bat" >nul 2>nul
if exist "%PINE_ROOT_DIR%MAC_Install.command" del /f /q "%PINE_ROOT_DIR%MAC_Install.command" >nul 2>nul
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

:ensure_supervisor_ready
call :write_stage "checking for existing supervisor"
python -c "import json, urllib.request; data=json.loads^(urllib.request.urlopen^('http://127.0.0.1:5001/status', timeout=0.5^).read^(^).decode^(^)^); raise SystemExit^(0 if data.get^('supervisor_running'^) and not data.get^('backend_running'^) else 1^)" 2>nul
if not errorlevel 1 (
    call :log_launcher_event "stale supervisor detected; requesting shutdown"
    python -c "import urllib.request; req=urllib.request.Request^('http://127.0.0.1:5001/shutdown', method='POST'^); urllib.request.urlopen^(req, timeout=0.5^)" 2>nul
    timeout /t 1 /nobreak >nul
)

call :probe_supervisor_status
if not errorlevel 1 (
    call :write_stage "supervisor status reachable"
    goto :eof
)

call :write_stage "starting supervisor"
call :log_launcher_event "supervisor start attempted"
if exist "%VENV_DIR%\\Scripts\\pythonw.exe" (
    start "" "%VENV_DIR%\\Scripts\\pythonw.exe" supervisor.py
) else if exist "%VENV_DIR%\\Scripts\\python.exe" (
    powershell -NoProfile -Command "Start-Process -WindowStyle Hidden -FilePath '%VENV_DIR%\\Scripts\\python.exe' -ArgumentList 'supervisor.py'" >nul
) else (
    powershell -NoProfile -Command "Start-Process -WindowStyle Hidden -FilePath 'python' -ArgumentList 'supervisor.py'" >nul
)

set SUP_WAIT=0
:wait_supervisor_ready
powershell -NoProfile -Command "Start-Sleep -Milliseconds 300" >nul
call :probe_supervisor_status
if not errorlevel 1 (
    call :write_stage "supervisor status reachable"
    goto :eof
)
set /a SUP_WAIT+=1
if !SUP_WAIT! EQU 30 (
    call :log_launcher_event "supervisor status still unavailable; retrying launch"
    if exist "%VENV_DIR%\\Scripts\\pythonw.exe" (
        start "" "%VENV_DIR%\\Scripts\\pythonw.exe" supervisor.py
    ) else if exist "%VENV_DIR%\\Scripts\\python.exe" (
        powershell -NoProfile -Command "Start-Process -WindowStyle Hidden -FilePath '%VENV_DIR%\\Scripts\\python.exe' -ArgumentList 'supervisor.py'" >nul
    ) else (
        powershell -NoProfile -Command "Start-Process -WindowStyle Hidden -FilePath 'python' -ArgumentList 'supervisor.py'" >nul
    )
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
    python -c "import urllib.request; req=urllib.request.Request^('http://127.0.0.1:5001/restart', method='POST'^); urllib.request.urlopen^(req, timeout=5^)" 2>nul
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
timeout /t 1 /nobreak >nul
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
timeout /t 1 /nobreak >nul
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
powershell -NoProfile -Command "Start-Sleep -Seconds 1" >nul
call :print_current_startup_status
call :probe_supervisor_backend_ready
if errorlevel 1 (
    set /a WAIT_BG_COUNT+=1
    goto waitbgloop
)
call :log_launcher_event "backend ready; opening browser"
call :open_browser_and_confirm_lease "http://127.0.0.1:5000/"
goto :eof

:open_diagnostic_window
call :log_launcher_event "opening diagnostic window"
(
    echo @echo off
    echo setlocal
    echo set "PINE_DIAGNOSTIC_LAUNCH=1"
    echo call "%~f0"
) > "%PINE_DIAGNOSTIC_CMD%"
powershell -NoProfile -Command "Start-Process -WorkingDirectory '%~dp0' -FilePath '%PINE_DIAGNOSTIC_CMD%'" >nul
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
if errorlevel 1 exit /b 1
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
powershell -NoProfile -Command "Start-Sleep -Seconds 1" >nul
set /a WAIT_LEASE_COUNT+=1
goto waitbrowserleaseloop
goto :eof

:probe_supervisor_status
powershell -NoProfile -Command "try { $null = Invoke-RestMethod -Uri 'http://127.0.0.1:5001/status' -TimeoutSec 2; exit 0 } catch { exit 1 }" >nul 2>nul
goto :eof

:probe_supervisor_backend_ready
powershell -NoProfile -Command "try { $data = Invoke-RestMethod -Uri 'http://127.0.0.1:5001/status' -TimeoutSec 2; if ($data.supervisor_running -and $data.backend_ready) { exit 0 } else { exit 1 } } catch { exit 1 }" >nul 2>nul
goto :eof

:probe_supervisor_lease_active
python -c "import json, urllib.request; data=json.loads^(urllib.request.urlopen^('http://127.0.0.1:5001/status', timeout=2^).read^(^).decode^(^)^); raise SystemExit^(0 if data.get^('supervisor_running'^) and int^(data.get^('lease_count'^) or 0^) ^> 0 else 1^)" >nul 2>nul
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
    target_path = root / INSTALLER_STORAGE_DIR / current_launchers[0]
    target_path.parent.mkdir(parents=True, exist_ok=True)
    if not IS_MAC and target_path.name == WIN_APP_LAUNCHER:
        try:
            target_path.write_text(
                _windows_app_launcher_contents(),
                encoding='utf-8',
                newline='\r\n',
            )
            target_path.chmod(target_path.stat().st_mode | 0o111)
        except OSError as exc:
            log.warning('Failed to write generated launcher %s: %s', target_path, exc)
        return
    source_path = _first_existing_launcher(root, *current_launchers[1:])
    if source_path is None:
        return
    try:
        shutil.copy2(source_path, target_path)
        if target_path.suffix.lower() in {'.bat', '.command'}:
            target_path.chmod(target_path.stat().st_mode | 0o111)
    except OSError as exc:
        log.warning('Failed to copy launcher %s -> %s: %s', source_path, target_path, exc)


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
        try:
            shutil.copy2(source_path, target_path)
            if target_path.suffix.lower() in {'.bat', '.command'}:
                target_path.chmod(target_path.stat().st_mode | 0o111)
            source_path.unlink()
        except OSError as exc:
            log.warning('Failed to move installer %s -> %s: %s', source_path, target_path, exc)


def _sync_platform_launcher_layout(repo_root=None):
    """Keep post-install launcher layout consistent across upgrades and first install."""
    _promote_platform_launcher(repo_root=repo_root)
    _move_installers_to_backend(repo_root=repo_root)
    _cleanup_cross_platform_launchers(repo_root=repo_root)
    _ensure_root_windows_shortcut(repo_root=repo_root)


def _ensure_root_windows_shortcut(repo_root=None):
    """Ensure the repo-root Launch Pine.lnk exists after onboarding."""
    if sys.platform != 'win32':
        return
    try:
        from ..api import settings as settings_api
    except Exception as exc:
        log.warning('Could not import settings API to create root shortcut: %s', exc)
        return

    root = _repo_root_path(repo_root)
    shortcut_path = root / 'Launch Pine.lnk'
    try:
        settings_api._write_launcher_file(str(shortcut_path))
    except (subprocess.CalledProcessError, OSError) as exc:
        log.warning('Failed to create root launcher shortcut %s: %s', shortcut_path, exc)


def restore_default_launcher_layout_after_reset(repo_root=None):
    """Return launcher files to the original pre-install repo layout."""
    root = _repo_root_path(repo_root)
    backend_dir = root / INSTALLER_STORAGE_DIR

    clear_onboarding_complete(root)

    for launcher_name in (
        WIN_APP_LAUNCHER,
        MAC_APP_LAUNCHER,
        *LEGACY_WIN_LAUNCHERS,
        *LEGACY_MAC_LAUNCHERS,
    ):
        launcher_path = root / launcher_name
        try:
            if launcher_path.exists():
                launcher_path.unlink()
        except OSError as exc:
            log.warning('Failed to remove reset launcher %s: %s', launcher_path, exc)

    for installer_name in (WIN_INSTALL_LAUNCHER, MAC_INSTALL_LAUNCHER):
        root_installer = root / installer_name
        backend_installer = backend_dir / installer_name

        if not root_installer.exists() and backend_installer.exists():
            try:
                shutil.copy2(backend_installer, root_installer)
                if root_installer.suffix.lower() in {'.bat', '.command'}:
                    root_installer.chmod(root_installer.stat().st_mode | 0o111)
            except OSError as exc:
                log.warning(
                    'Failed to restore installer %s -> %s: %s',
                    backend_installer,
                    root_installer,
                    exc,
                )

        if not root_installer.exists():
            continue

        try:
            if backend_installer.exists():
                backend_installer.unlink()
        except OSError as exc:
            log.warning('Failed to remove backend installer %s: %s', backend_installer, exc)


def _refresh_windows_launcher_shortcuts():
    """Re-save existing Windows shortcuts so they point at the renamed launcher and icon."""
    if sys.platform != 'win32':
        return
    try:
        from ..api import settings as settings_api
    except Exception as exc:
        log.warning('Could not import settings API to refresh shortcuts: %s', exc)
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
        try:
            settings_api._write_launcher_file(shortcut_path)
        except (subprocess.CalledProcessError, OSError) as exc:
            log.warning('Failed to refresh launcher shortcut %s: %s', shortcut_path, exc)


def _cleanup_cross_platform_launchers(repo_root=None):
    """Remove launcher scripts that don't match the current platform."""
    root = _repo_root_path(repo_root)
    _, to_remove = _platform_launcher_sets()
    for launcher_name in to_remove:
        launcher_path = root / launcher_name
        try:
            if launcher_path.exists():
                launcher_path.unlink()
        except OSError as exc:
            log.warning('Failed to remove launcher %s: %s', launcher_path, exc)


def _begin_windows_hf_hub_no_symlinks():
    """Force huggingface_hub to copy files instead of symlinking (blobs→snapshots).

    On Windows without Developer Mode or SeCreateSymbolicLinkPrivilege, ``os.symlink`` raises
    WinError 1314 ("A required privilege is not held by the client") during model download.
    HF's probe can still report symlinks supported, so we override the check for this thread's downloads.
    """
    if sys.platform != 'win32':
        return None
    try:
        import huggingface_hub.file_download as fd
        orig = fd.are_symlinks_supported

        def _no_symlinks(cache_dir=None):
            return False

        fd.are_symlinks_supported = _no_symlinks
        return orig
    except Exception as exc:
        log.warning('Windows HF symlink workaround unavailable: %s', exc)
        return None


def _end_windows_hf_hub_no_symlinks(orig):
    if orig is None:
        return
    try:
        import huggingface_hub.file_download as fd
        fd.are_symlinks_supported = orig
    except Exception:
        pass


def _safe_emit(event, data):
    """Emit SocketIO event, silently ignoring disconnected sessions."""
    try:
        socketio.emit(event, data)
    except Exception:
        pass

# Subprocess environment that prevents pip from ever touching system or user packages.
# PIP_REQUIRE_VIRTUALENV  — pip refuses to run if not inside a venv.
# PYTHONNOUSERSITE        — disables ~/.local / %APPDATA%\Python user site-packages.
_PIP_ENV = {**os.environ, 'PIP_REQUIRE_VIRTUALENV': '1', 'PYTHONNOUSERSITE': '1'}

MODEL_REGISTRY = {
    'whisperx-large-v3': {
        'name': 'WhisperX + faster-whisper large-v3',
        'function': 'stt',
        'repo_id': 'Systran/faster-whisper-large-v3',
        'size_bytes': 3_000_000_000,
        'required': True,
        'language': 'multi',
        'platform': None,           # Windows / Linux only (None = all, but skipped on Mac)
    },
    'mlx-whisper-large-v3': {
        'name': 'mlx-whisper large-v3 (Metal)',
        'function': 'stt',
        'repo_id': 'mlx-community/whisper-large-v3-mlx',
        'size_bytes': 3_000_000_000,
        'required': True,
        'language': 'multi',
        'platform': 'darwin',       # Mac only
    },
    'pyannote-diarization': {
        'name': 'pyannote speaker-diarization-community-1',
        'function': 'diarization',
        'repo_id': 'pyannote/speaker-diarization-community-1',
        # Repo is pipeline config + small assets only (weights live in other pyannote_* entries).
        'size_bytes': 8_000_000,
        'required': True,
        'language': None,
        # pyannote loads checkpoints via hf_hub_download(..., cache_dir=PYANNOTE_CACHE)
        'use_pyannote_hub_cache': True,
    },
    'pyannote-segmentation': {
        'name': 'pyannote segmentation-3.0',
        'function': 'diarization',
        'repo_id': 'pyannote/segmentation-3.0',
        'size_bytes': 6_000_000,
        'required': True,
        'language': None,
        'use_pyannote_hub_cache': True,
    },
    'pyannote-wespeaker-voxceleb-resnet34-LM': {
        'name': 'pyannote WeSpeaker embedding (diarization)',
        'function': 'diarization',
        'repo_id': 'pyannote/wespeaker-voxceleb-resnet34-LM',
        'size_bytes': 55_000_000,
        'required': True,
        'language': None,
        'use_pyannote_hub_cache': True,
    },
    'gliner-pii': {
        'name': 'GLiNER v2 PII removal',
        'function': 'pii',
        'repo_id': 'urchade/gliner_multi_pii-v1',
        'size_bytes': 1_800_000_000,
        'required': False,
        'language': None,
        # GLiNER needs the mdeberta tokenizer files locally to avoid runtime HF fetches
        'tokenizer_repo': 'microsoft/mdeberta-v3-base',
        'tokenizer_files': [
            'spm.model', 'tokenizer_config.json', 'config.json',
        ],
    },
}


def _model_for_platform(info):
    """Return True if this model entry applies to the current platform."""
    plat = info.get('platform')
    if plat is None:
        # STT models with platform=None are for non-Mac platforms
        if info.get('function') == 'stt':
            return not IS_MAC
        return True
    return plat == sys.platform


def init_model_registry():
    """Populate the ml_models table with known models (idempotent)."""
    for model_id, info in MODEL_REGISTRY.items():
        if not _model_for_platform(info):
            continue
        m = db.session.get(MLModel, model_id)
        if m:
            # Always sync size_bytes from registry (catches stale values)
            if info.get('size_bytes') and m.size_bytes != info['size_bytes']:
                m.size_bytes = info['size_bytes']
        else:
            db.session.add(MLModel(
                id=model_id,
                name=info['name'],
                function=info['function'],
                repo_id=info['repo_id'],
                filename=info.get('filename'),
                size_bytes=info['size_bytes'],
                status='not_downloaded',
                language=info.get('language'),
                required=info['required'],
            ))
    db.session.commit()


def pyannote_hub_cache_root(models_path):
    """Directory where pyannote HF repos are snapshotted (matches PYANNOTE_CACHE in transcription)."""
    return os.path.join(models_path, 'pyannote_cache')


def _hub_cache_marker_file(repo_id):
    # Pipeline-config repos (no weights) use config.yaml as the cache marker.
    _pipeline_repos = ('speaker-diarization-3.1', 'speaker-diarization-community-1')
    if any(repo_id.endswith(r) for r in _pipeline_repos):
        return 'config.yaml'
    return 'pytorch_model.bin'


def snapshot_dir_if_repo_cached(repo_id, cache_dir):
    """Return snapshot directory for repo if HF hub cache under cache_dir has the marker file."""
    from huggingface_hub import try_to_load_from_cache, _CACHED_NO_EXIST

    marker = _hub_cache_marker_file(repo_id)
    fp = try_to_load_from_cache(repo_id, marker, cache_dir=cache_dir)
    if fp is None or fp is _CACHED_NO_EXIST:
        return None
    return os.path.dirname(fp)


def model_storage_dir(models_path, m, info):
    """Resolved on-disk location for a model row (hub-cache snapshot, legacy folder, or STT path)."""
    if m.path and os.path.isdir(m.path):
        return m.path
    if info.get('use_pyannote_hub_cache'):
        pyc = pyannote_hub_cache_root(models_path)
        snap = snapshot_dir_if_repo_cached(info['repo_id'], pyc)
        if snap:
            return snap
        legacy = os.path.join(models_path, m.id)
        if os.path.isdir(legacy):
            return legacy
        return None
    return os.path.join(models_path, m.id)


def reconcile_model_statuses(models_path):
    """On startup, fix stale model statuses by checking what is actually on disk.

    - Models stuck in 'downloading' are reset to 'not_downloaded' (re-shows the Download button).
    - Models marked 'not_downloaded' that already have files on disk are promoted to 'ready'.
    """
    changed = False
    for m in MLModel.query.all():
        info = MODEL_REGISTRY.get(m.id, {})
        if not info:
            continue
        dest = model_storage_dir(models_path, m, info)
        expected = m.size_bytes or info.get('size_bytes', 0)

        if m.status == 'downloading':
            # Server restarted mid-download; decide based on what's actually on disk
            if dest and _model_already_on_disk(dest, expected):
                log.info('Model %s found complete on disk after restart, marking ready', m.id)
                m.status = 'ready'
                m.path = dest
                m.downloaded_bytes = expected
            else:
                log.info('Model %s was mid-download at restart, resetting to not_downloaded', m.id)
                m.status = 'not_downloaded'
                m.downloaded_bytes = 0
            changed = True

        elif m.status == 'not_downloaded':
            # Model may have been downloaded in a previous session whose DB was lost/reset
            if dest and _model_already_on_disk(dest, expected):
                log.info('Model %s already on disk but DB shows not_downloaded, marking ready', m.id)
                m.status = 'ready'
                m.path = dest
                m.downloaded_bytes = expected
                changed = True

    if changed:
        db.session.commit()


def _gated_pyannote_repos():
    """Repo IDs that require accepting a HuggingFace license before download.

    Derived from MODEL_REGISTRY so it stays in sync with what we actually
    download — the pyannote diarization stack is gated and the user must accept
    each model's conditions on huggingface.co first.
    """
    return [
        info['repo_id']
        for info in MODEL_REGISTRY.values()
        if info.get('use_pyannote_hub_cache') and info.get('repo_id')
    ]


def validate_hf_token(token):
    """Validate a HuggingFace token AND verify it can access the gated pyannote repos.

    Returns a dict:
      - valid:       token authenticates (whoami succeeds)
      - username:    HF account name (when valid)
      - gated_ok:    True if every gated pyannote repo is accessible
      - gated_repos: per-repo access detail [{repo_id, url, accessible, reason}]

    A token that authenticates but is missing a license acceptance returns
    ``valid=True, gated_ok=False`` so the UI can tell the user exactly which
    licenses to accept — instead of letting the failure surface deep inside the
    multi-GB model download.
    """
    try:
        from huggingface_hub import HfApi
    except Exception as exc:
        return {'valid': False, 'error': f'huggingface_hub unavailable: {exc}'}

    try:
        api = HfApi(token=token)
        user = api.whoami()
    except Exception as exc:
        return {'valid': False, 'error': str(exc)}

    try:
        from huggingface_hub.utils import GatedRepoError, RepositoryNotFoundError
    except Exception:  # pragma: no cover - defensive
        GatedRepoError = RepositoryNotFoundError = ()

    gated_repos = []
    auth_check = getattr(api, 'auth_check', None)
    for repo_id in _gated_pyannote_repos():
        entry = {
            'repo_id': repo_id,
            'url': f'https://huggingface.co/{repo_id}',
            'accessible': True,
            'reason': '',
        }
        if auth_check is None:
            # Older huggingface_hub without auth_check — cannot pre-verify.
            gated_repos.append(entry)
            continue
        try:
            auth_check(repo_id)
        except GatedRepoError:
            entry['accessible'] = False
            entry['reason'] = 'license_not_accepted'
        except RepositoryNotFoundError:
            entry['accessible'] = False
            entry['reason'] = 'not_found_or_no_access'
        except Exception as exc:
            # Network/transient error: surface but do not hard-block onboarding.
            entry['accessible'] = None
            entry['reason'] = f'check_failed: {exc}'
        gated_repos.append(entry)

    blocked = [g for g in gated_repos if g['accessible'] is False]
    return {
        'valid': True,
        'username': user.get('name', ''),
        'gated_ok': not blocked,
        'gated_repos': gated_repos,
    }


STT_MODEL_QUALITY = 'whisperx-large-v3'
MLX_STT_MODEL_QUALITY = 'mlx-whisper-large-v3'

LEGACY_STT_MODEL_ID_ALIASES = {
    'whisperx-large-v3-turbo': STT_MODEL_QUALITY,
    'mlx-whisper-large-v3-turbo': MLX_STT_MODEL_QUALITY,
}

IS_MAC = sys.platform == 'darwin'


def get_default_stt_model():
    """Return the default STT model ID for the current platform."""
    return MLX_STT_MODEL_QUALITY if IS_MAC else STT_MODEL_QUALITY


def normalize_stt_model_id(stt_model_id):
    """Map legacy/invalid STT IDs to the single supported model for this platform."""
    model_id = (stt_model_id or '').strip()
    if model_id in LEGACY_STT_MODEL_ID_ALIASES:
        model_id = LEGACY_STT_MODEL_ID_ALIASES[model_id]

    allowed = (
        {MLX_STT_MODEL_QUALITY, STT_MODEL_QUALITY}
        if IS_MAC else {STT_MODEL_QUALITY}
    )
    if model_id not in allowed:
        return get_default_stt_model()
    return model_id


def get_models_for_setup(modules):
    """Return list of model IDs to download based on user choices."""
    stt_model = get_default_stt_model()
    ids = [
        stt_model,
        'pyannote-diarization',
    ]

    if 'pii' in modules:
        ids.append('gliner-pii')

    return ids


def remove_model(model_id, models_path):
    """Remove a model from disk and reset its DB status."""
    m = db.session.get(MLModel, model_id)
    info = MODEL_REGISTRY.get(model_id, {})
    dest = None
    if m and m.path and os.path.isdir(m.path):
        dest = m.path
    elif info.get('use_pyannote_hub_cache'):
        pyc = pyannote_hub_cache_root(models_path)
        dest = snapshot_dir_if_repo_cached(info['repo_id'], pyc)
    if dest is None:
        dest = os.path.join(models_path, model_id)
    if os.path.isdir(dest):
        try:
            shutil.rmtree(dest)
            log.info('Removed model %s from %s', model_id, dest)
        except OSError as exc:
            log.warning('Could not remove model dir %s: %s', dest, exc)
    if m:
        m.status = 'not_downloaded'
        m.path = None
        m.downloaded_bytes = 0
        db.session.commit()


# ---------------------------------------------------------------------------
# Pip dependency installation
# ---------------------------------------------------------------------------

if IS_MAC:
    REQUIRED_PACKAGES = [
        ('torch', 'torch', 'PyTorch ML runtime'),
        ('torchaudio', 'torchaudio', 'Audio processing'),
        ('mlx_whisper', 'mlx-whisper', 'Transcription (mlx-whisper, Metal-accelerated)'),
        ('pyannote.audio', 'pyannote-audio', 'Speaker diarization (pyannote)'),
    ]
    # torchaudio 2.9+ dropped AudioMetaData; patched at runtime in ml_worker.compat.patch_torchaudio_for_pyannote().
    PIP_INSTALL_TARGETS = ['torch', 'torchaudio', 'mlx-whisper', 'pyannote-audio']
else:
    REQUIRED_PACKAGES = [
        ('torchruntime', 'torchruntime', 'GPU auto-detection'),
        ('torch', 'torch', 'PyTorch ML runtime'),
        ('torchaudio', 'torchaudio', 'Audio processing'),
        ('whisperx', 'whisperx', 'Transcription + alignment (WhisperX)'),
        ('faster_whisper', 'faster-whisper', 'Whisper inference engine'),
        ('pyannote.audio', 'pyannote-audio', 'Speaker diarization (pyannote)'),
    ]
    PIP_INSTALL_TARGETS = ['torchruntime', 'torchaudio', 'whisperx']

# Optional pip packages required by specific models (beyond REQUIRED_PACKAGES)
MODEL_OPTIONAL_PACKAGES = {
    'gliner-pii': [('gliner', 'gliner', 'PII detection')],
}


def _is_package_installed(import_name):
    """True if *import_name* is importable (e.g. ``torch``, ``pyannote.audio``)."""
    import importlib
    import importlib.util
    try:
        if importlib.util.find_spec(import_name) is not None:
            return True
    except (ModuleNotFoundError, ValueError):
        pass
    # find_spec can miss some layouts; importing is the ground truth (slightly slower).
    try:
        importlib.import_module(import_name)
        return True
    except ImportError:
        return False


def _check_packages():
    """Return list of dicts with install status for each required package."""
    results = []
    for import_name, pip_name, description in REQUIRED_PACKAGES:
        results.append({
            'name': pip_name,
            'description': description,
            'installed': _is_package_installed(import_name),
        })
    return results


# The subset of REQUIRED_PACKAGES that must be importable before transcription.
if IS_MAC:
    _TRANSCRIPTION_IMPORTS = [
        ('torch',          'torch'),
        ('mlx_whisper',    'mlx-whisper'),
        ('pyannote.audio', 'pyannote-audio'),
    ]
else:
    _TRANSCRIPTION_IMPORTS = [
        ('torch',          'torch'),
        ('whisperx',       'whisperx'),
        ('pyannote.audio', 'pyannote-audio'),
    ]


def _selected_stt_imports():
    """Return extra STT imports required by the user's selected model."""
    if not IS_MAC:
        return []
    try:
        from ..models.setting import Setting
        model_id = normalize_stt_model_id(Setting.get('stt_model_id', get_default_stt_model()))
    except Exception:
        model_id = get_default_stt_model()
    if model_id == STT_MODEL_QUALITY:
        return [
            ('whisperx', 'whisperx'),
            ('faster_whisper', 'faster-whisper'),
            ('torchaudio', 'torchaudio'),
        ]
    return []


def _transcription_imports():
    """Base transcription imports plus any model-specific extras."""
    imports = list(_TRANSCRIPTION_IMPORTS)
    for item in _selected_stt_imports():
        if item not in imports:
            imports.append(item)
    return imports


def check_ml_deps():
    """Return a list of pip package names that are missing.

    Call this before starting transcription to surface a clear error instead
    of a cryptic ImportError.  Returns an empty list when all deps are present.
    """
    return [pip for imp, pip in _transcription_imports()
            if not _is_package_installed(imp)]


def ensure_transcription_dependencies():
    """Install any missing transcription pip packages, then re-check.

    Upgrades can add new required packages (e.g. pyannote-audio on Mac after dropping
    WhisperX) while onboarding stays marked complete; this self-heals on first transcribe.
    """
    for imp, pip in _transcription_imports():
        if not _is_package_installed(imp):
            log.info('Installing missing transcription dependency: %s', pip)
            _install_package_if_missing(imp, pip)
    return check_ml_deps()


def _install_package_if_missing(import_name, pip_name):
    """Install pip package if not already installed. Returns True if installed or already present."""
    if _is_package_installed(import_name):
        return True
    try:
        subprocess.run(
            [sys.executable, '-m', 'pip', 'install', '--no-input', pip_name],
            env=_PIP_ENV,
            capture_output=True,
            timeout=300,
            check=True,
        )
        log.info('Installed %s', pip_name)
        return True
    except subprocess.CalledProcessError as exc:
        log.warning('Failed to install %s: %s', pip_name, exc)
        return False
    except Exception as exc:
        log.warning('Failed to install %s: %s', pip_name, exc)
        return False


def _install_model_specific_packages(model_ids):
    """Install any pip packages required by the requested models.

    Returns True if a pip install was attempted (optional deps can alter torch; caller may re-align).
    """
    attempted = False
    for mid in model_ids:
        for import_name, pip_name, desc in MODEL_OPTIONAL_PACKAGES.get(mid, []):
            if not _is_package_installed(import_name):
                attempted = True
                _install_emit(f'Installing {pip_name} for {desc}...')
                _install_package_if_missing(import_name, pip_name)
    return attempted


def _base_python_torch_version():
    """Return torch version string if torch is installed in the base (non-venv) Python, else None."""
    # sys.base_exec_prefix is the real Python prefix, even when running inside a venv
    if sys.base_exec_prefix == sys.exec_prefix:
        return None  # not in a venv, no separate base to check
    base_exe = os.path.join(sys.base_exec_prefix, 'python.exe' if sys.platform == 'win32' else os.path.join('bin', 'python3'))
    if not os.path.isfile(base_exe):
        return None
    try:
        r = subprocess.run(
            [base_exe, '-c', 'import torch; print(torch.__version__)'],
            capture_output=True, text=True, timeout=10,
        )
        return r.stdout.strip() if r.returncode == 0 else None
    except Exception:
        return None


def _is_torch_cuda_available():
    """Return True if torch in the venv has CUDA support enabled."""
    try:
        r = subprocess.run(
            [sys.executable, '-c', 'import torch; print(torch.cuda.is_available())'],
            capture_output=True, text=True, timeout=30,
        )
        return r.stdout.strip() == 'True'
    except Exception:
        return False


def _run_torchruntime_install():
    """Run 'torchruntime install' to get the correct PyTorch variant for the GPU."""
    if _is_package_installed('torch') and _is_torch_cuda_available():
        log.info('PyTorch with CUDA already installed, skipping torchruntime install.')
        _install_emit('PyTorch (CUDA) already installed, skipping GPU detection step.')
        return

    if _is_package_installed('torch'):
        _install_emit('PyTorch installed but CUDA not available — running GPU detection to install correct variant...')

    try:
        _install_emit('\n--- Detecting GPU and installing PyTorch variant ---')
        proc = subprocess.Popen(
            [sys.executable, '-m', 'torchruntime', 'install'],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=_PIP_ENV,
        )
        for line in proc.stdout:
            line = line.rstrip('\n\r')
            if line:
                _install_emit(line)
        proc.wait()
        if proc.returncode != 0:
            log.warning('torchruntime install exited with code %d', proc.returncode)
            _install_emit('WARNING: torchruntime install had issues. CPU mode may be used.')
        else:
            log.info('torchruntime install succeeded.')
    except Exception as exc:
        log.warning('torchruntime install failed: %s', exc)
        _install_emit(f'WARNING: {exc}')


def _pytorch_wheel_index_url():
    """Wheel repo that matches the installed torch build (cpu vs cu128, etc.).

    Default PyPI often pulls torchvision with a *different* channel than torchruntime's torch
    (e.g. torch ``2.8.0+cpu`` + torchvision ``0.26.0+cu128``), which breaks at import with
    ``operator torchvision::nms does not exist``.
    """
    if IS_MAC:
        return None
    try:
        import torch
        ver = torch.__version__
    except Exception:
        return 'https://download.pytorch.org/whl/cpu'
    if '+' in ver:
        tag = ver.split('+', 1)[1]
        return f'https://download.pytorch.org/whl/{tag}'
    return 'https://download.pytorch.org/whl/cpu'


def _companion_pip_extra_args(force_reinstall=False):
    """Pip options to install torchaudio/torchvision from the same channel as torch."""
    if IS_MAC:
        return []
    url = _pytorch_wheel_index_url()
    args = ['--index-url', url]
    if force_reinstall:
        args.insert(0, '--force-reinstall')
    return args


def _torch_companion_channels_aligned():
    """True if torch and torchvision report the same +cpu / +cu* local version tag."""
    if IS_MAC:
        return True
    try:
        import torch
        th = torch.__version__
    except Exception:
        return True
    th_tag = th.split('+', 1)[1] if '+' in th else None
    try:
        import torchvision
        tv = torchvision.__version__
    except Exception:
        return False
    tv_tag = tv.split('+', 1)[1] if '+' in tv else None
    return th_tag == tv_tag


def _evict_torch_companion_modules():
    """Drop torchvision/torchaudio from sys.modules after pip reinstall in-process."""
    to_drop = [
        k for k in list(sys.modules)
        if k == 'torchvision' or k.startswith('torchvision.')
        or k == 'torchaudio' or k.startswith('torchaudio.')
    ]
    for k in to_drop:
        sys.modules.pop(k, None)


def repair_torch_companion_wheels_if_needed():
    """If torchvision/torchaudio channel mismatches torch, reinstall from PyTorch wheel index.

    Call before ``import whisperx`` so broken mixed installs self-heal without re-onboarding.
    """
    if IS_MAC:
        return True
    if _torch_companion_channels_aligned():
        return True
    try:
        import torch as _torch
        _th_ver = _torch.__version__
    except Exception:
        _th_ver = '?'
    log.warning(
        'torch (%s) and torchvision wheel channels differ; reinstalling torchaudio/torchvision from %s',
        _th_ver,
        _pytorch_wheel_index_url(),
    )
    _install_emit('\n--- Repairing torchaudio/torchvision to match PyTorch wheel channel ---')
    ok = _realign_torchaudio_torchvision()
    if ok:
        _evict_torch_companion_modules()
        log.info('torchaudio/torchvision repaired to match torch.')
    return ok


def _realign_torchaudio_torchvision():
    """Ensure torch + torchaudio + torchvision all match the targeted wheel channel.

    whisperx 3.8.5 pins ``torch~=2.8.0`` and its pip-install step will happily downgrade
    torch to the CPU-only 2.8.0 wheel from PyPI (~250 MB), leaving torchvision
    ``0.26.0+cu128`` behind — which then breaks at import with
    ``operator torchvision::nms does not exist``.

    We reinstall all three together from the PyTorch wheel channel derived from the
    previously installed torch version (e.g. ``+cu128``). ``--no-deps`` is intentional:
    torchvision ``0.26.0+cu128`` declares ``torch==2.11.0`` as a dependency, so without
    ``--no-deps`` pip cascades and re-downloads torch a second time (~2.75 GB). With it,
    only the explicit list is fetched, which also bypasses whisperx's ``torch~=2.8.0``
    pin; that leaves ``pip check`` noisy about whisperx, but runtime is compatible.
    """
    if IS_MAC:
        return True
    _install_emit('\n--- Re-aligning torchaudio/torchvision with installed PyTorch ---')
    ok = _run_pip(
        ['torch', 'torchaudio', 'torchvision'],
        extra_args=[*_companion_pip_extra_args(force_reinstall=True), '--no-deps'],
    )
    if not ok:
        log.error('Failed to re-align torch/torchaudio/torchvision')
    return ok


# ---------------------------------------------------------------------------
# Install log file (mirrors SocketIO install:log stream to disk)
# ---------------------------------------------------------------------------

_INSTALL_LOG_FH = None  # open file handle for the active install_pip_packages() session


def _install_log_path():
    """Return a per-session install log path under the same tree as app logs.

    Mirrors the convention in backend/app/__init__.py (log_dir = PINE_LOG_DIR or
    <backend>/logs, daily subdir YYYYMMDD). File name: install-{stamp}-{pid}.log.
    """
    base = os.environ.get('PINE_LOG_DIR')
    if not base:
        backend_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        base = os.path.join(backend_dir, 'logs')
    now = datetime.now()
    daily = os.path.join(base, now.strftime('%Y%m%d'))
    try:
        os.makedirs(daily, exist_ok=True)
    except Exception:
        # Fall back to the base dir if the daily subdir can't be created.
        daily = base
        try:
            os.makedirs(daily, exist_ok=True)
        except Exception:
            pass
    return os.path.join(daily, f'install-{now.strftime("%Y%m%d-%H%M%S")}-{os.getpid()}.log')


def _install_emit(line):
    """Emit an install log line to SocketIO clients AND append it to the install log file.

    If no install session is active, behaves like a plain _safe_emit('install:log', ...).
    """
    _safe_emit('install:log', {'line': line})
    fh = _INSTALL_LOG_FH
    if fh is None:
        return
    try:
        fh.write(line.rstrip('\r\n') + '\n')
        fh.flush()
    except Exception:
        pass


def _run_pip(packages, extra_args=None):
    """Run pip install with streaming output. Returns True on success."""
    cmd = [sys.executable, '-m', 'pip', 'install', '--no-input']
    if extra_args:
        cmd.extend(extra_args)
    cmd.extend(packages)
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=_PIP_ENV,
        )
        for line in proc.stdout:
            line = line.rstrip('\n\r')
            if line:
                _install_emit(line)
        proc.wait()
        if proc.returncode != 0:
            log.error('pip exited with code %d for %s', proc.returncode, packages)
        return proc.returncode == 0
    except Exception as exc:
        log.exception('Failed to run pip for %s: %s', packages, exc)
        _install_emit(f'ERROR: {exc}')
        return False


def install_pip_packages():
    """Install required pip packages if missing. Streams output via SocketIO and to
    a per-session install log file (``backend/logs/YYYYMMDD/install-{stamp}-{pid}.log``)."""
    global _INSTALL_LOG_FH

    status = _check_packages()
    all_installed = all(p['installed'] for p in status)

    if all_installed:
        log.info('All required packages already installed.')
        _safe_emit('install:complete', {'skipped': True, 'packages': status})
        return True

    log.info('Some packages missing, installing...')
    _safe_emit('install:start', {'packages': status})

    log_path = _install_log_path()
    log.info('Install log: %s', log_path)
    try:
        _INSTALL_LOG_FH = open(log_path, 'w', encoding='utf-8', buffering=1)
    except Exception as exc:
        log.warning('Could not open install log %s: %s', log_path, exc)
        _INSTALL_LOG_FH = None

    try:
        _install_emit(f'Install log: {log_path}')
        _install_emit(f'Started: {datetime.now().isoformat(timespec="seconds")}')
        _install_emit(f'Python:   {sys.executable}')
        _install_emit(f'Platform: {sys.platform}')
        missing = [p['name'] for p in status if not p['installed']]
        _install_emit(f'Missing:  {missing}')

        if IS_MAC:
            success = _run_pip(PIP_INSTALL_TARGETS)
        else:
            # Five-phase install:
            #   1. torchruntime (--no-deps to avoid CPU torch)
            #   2. GPU-detected torch via torchruntime install
            #   3. torchaudio/torchvision from matching wheel channel
            #   4. whisperx (--no-deps to keep GPU torch)
            #   5. whisperx's non-torch runtime deps
            # Re-align safety net runs only if channels actually diverged.
            _install_emit('\n--- Phase 1/5: Installing GPU detection tool ---')
            success = _run_pip(['torchruntime'], extra_args=['--no-deps'])

            if success:
                _install_emit('\n--- Phase 2/5: Detecting GPU and installing PyTorch ---')
                _run_torchruntime_install()

                # WhisperX → transformers imports torchvision; use PyTorch wheel index
                # (same +cpu / +cu* as torch). --no-deps avoids re-resolving numpy /
                # pillow / sympy which torchruntime already installed in Phase 2.
                _install_emit('\n--- Phase 3/5: Installing torchaudio and torchvision ---')
                success = _run_pip(
                    ['torchaudio', 'torchvision'],
                    extra_args=[*_companion_pip_extra_args(), '--no-deps'],
                )

            if success:
                # --no-deps prevents whisperx's ``torch~=2.8.0`` pin from
                # downgrading the GPU-enabled torch we installed in Phase 2.
                _install_emit('\n--- Phase 4/5: Installing whisperx (no-deps) ---')
                success = _run_pip(['whisperx'], extra_args=['--no-deps'])

            if success:
                # Now pull whisperx's non-torch runtime deps.  faster-whisper
                # transitively brings ctranslate2, onnxruntime, tokenizers, av;
                # pyannote-audio brings lightning, scikit-learn, etc.
                _install_emit('\n--- Phase 5/5: Installing whisperx dependencies ---')
                success = _run_pip([
                    'faster-whisper',
                    'pyannote-audio',
                    'transformers',
                    'nltk',
                    'pandas',
                    'omegaconf',
                    'huggingface-hub<1.0.0',
                ])

            if success and not _torch_companion_channels_aligned():
                # Safety net: re-align only when channels actually diverged.
                _install_emit('\n--- Re-aligning torch stack (channels diverged) ---')
                success = _realign_torchaudio_torchvision()

        if success:
            log.info('Package installation succeeded.')
            _install_emit('\nPackage installation succeeded.')
            _install_emit(f'Finished: {datetime.now().isoformat(timespec="seconds")}')
            _safe_emit('install:complete', {
                'skipped': False,
                'packages': _check_packages(),
            })
            return True
        else:
            msg = 'Package installation failed'
            log.error(msg)
            _install_emit(f'\nERROR: {msg}')
            _install_emit(f'Finished: {datetime.now().isoformat(timespec="seconds")}')
            _safe_emit('install:error', {'error': msg})
            return False
    finally:
        fh, _INSTALL_LOG_FH = _INSTALL_LOG_FH, None
        if fh is not None:
            try:
                fh.close()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Download orchestration
# ---------------------------------------------------------------------------

def _dir_size(path):
    total = 0
    try:
        for f in Path(path).rglob('*'):
            if f.is_file():
                total += f.stat().st_size
    except Exception:
        pass
    return total


def _model_already_on_disk(dest, expected_bytes):
    """Return True if the model directory exists and has substantial content (≥80% of expected)."""
    if not expected_bytes or expected_bytes < 1000:
        return os.path.isdir(dest) and _dir_size(dest) > 1000
    return _dir_size(dest) >= 0.8 * expected_bytes


def _monitor_progress(model_id, dest, expected, completed_so_far, overall_total, stop, app):
    """Poll directory size every second and emit SocketIO progress."""
    prev_size = 0
    prev_time = time.time()
    while not stop.is_set():
        cur_size = min(_dir_size(dest), expected) if expected else _dir_size(dest)
        now = time.time()
        dt = now - prev_time
        speed = int((cur_size - prev_size) / dt) if dt > 0 else 0

        _safe_emit('download:progress', {
            'model_id': model_id,
            'downloaded_bytes': cur_size,
            'total_bytes': expected,
            'speed_bps': max(speed, 0),
            'overall_downloaded': completed_so_far + cur_size,
            'overall_total': overall_total,
        })

        with app.app_context():
            m = db.session.get(MLModel, model_id)
            if m:
                m.downloaded_bytes = cur_size
                db.session.commit()

        prev_size = cur_size
        prev_time = now
        stop.wait(1.0)


def play_install_complete_sound(app, volume_pct=50):
    """Play install-complete sound from the terminal (works regardless of browser tab focus).

    ``volume_pct`` is clamped to 0–100; 0 or below skips playback. Prefer ``ffplay`` when
    available so volume applies on all platforms; otherwise decode with ffmpeg and use the
    OS default player path (volume applied when ffmpeg runs).
    """
    try:
        vol = max(0, min(100, int(volume_pct)))
        if vol <= 0:
            return
        static_dir = Path(app.static_folder) if app.static_folder else Path(app.root_path) / 'static'
        sound_path = static_dir / 'sounds' / 'install-complete.mp3'
        if not sound_path.is_file():
            log.warning('Install-complete sound not found: %s', sound_path)
            return
        path_str = str(sound_path.resolve())

        def _play():
            try:
                ffplay = shutil.which('ffplay')
                if ffplay:
                    subprocess.Popen(
                        [ffplay, '-nodisp', '-autoexit', '-volume', str(vol),
                         '-loglevel', 'quiet', path_str],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                        start_new_session=True,
                    )
                    return

                import tempfile
                fd, wav_path = tempfile.mkstemp(suffix='.wav')
                os.close(fd)
                try:
                    vol_lin = vol / 100.0
                    r = subprocess.run(
                        [
                            'ffmpeg', '-y', '-i', path_str,
                            '-filter:a', f'volume={vol_lin}',
                            '-acodec', 'pcm_s16le', '-ar', '44100', '-ac', '1', wav_path,
                        ],
                        capture_output=True, timeout=60,
                    )
                    if r.returncode != 0 or not os.path.isfile(wav_path):
                        raise RuntimeError('ffmpeg wav decode failed')

                    if sys.platform == 'win32':
                        import winsound
                        winsound.PlaySound(wav_path, winsound.SND_FILENAME)
                    elif sys.platform == 'darwin':
                        subprocess.run(
                            ['afplay', wav_path], capture_output=True, timeout=120,
                        )
                    else:
                        aplay = shutil.which('aplay') or shutil.which('paplay')
                        if not aplay:
                            raise RuntimeError('no aplay/paplay')
                        subprocess.run(
                            [aplay, wav_path], capture_output=True, timeout=120,
                        )
                finally:
                    try:
                        os.remove(wav_path)
                    except OSError:
                        pass
            except Exception as exc:
                log.warning('Play sound failed: %s', exc)
                if sys.platform == 'win32':
                    try:
                        os.startfile(path_str)
                    except Exception:
                        pass

        threading.Thread(target=_play, daemon=True).start()
    except Exception as exc:
        log.warning('Failed to play install-complete sound: %s', exc)


def _preload_alignment_model():
    """Pre-download WhisperX alignment model (English) so first transcription isn't delayed."""
    try:
        _install_emit('\n--- Pre-downloading alignment model (English) ---')
        import torchaudio
        bundle = getattr(torchaudio.pipelines, 'WAV2VEC2_ASR_BASE_960H', None)
        if bundle:
            bundle.get_model()
            _install_emit('Alignment model cached.')
        else:
            _install_emit('Alignment model not found in torchaudio, will download on first use.')
    except Exception as exc:
        log.warning('Alignment model preload failed: %s', exc)
        _install_emit(f'Alignment preload skipped: {exc}')


def _warmup_pyannote_community1(models_path, hf_token=None):
    """Warm up pyannote community-1 so first diarization does not cold-start.

    This keeps the onboarding model list minimal (community-1 entrypoint only)
    while still pulling any internal artifacts the pipeline needs.
    """
    try:
        _install_emit('\n--- Preloading pyannote diarization pipeline (community-1) ---')
        from pyannote.audio import Pipeline
        import inspect
        import torch

        pyc = pyannote_hub_cache_root(models_path)
        os.makedirs(pyc, exist_ok=True)
        os.environ['PYANNOTE_CACHE'] = pyc

        sig = inspect.signature(Pipeline.from_pretrained)
        kwargs = {}
        auth = hf_token or None
        if 'token' in sig.parameters:
            kwargs['token'] = auth
        elif 'use_auth_token' in sig.parameters:
            kwargs['use_auth_token'] = auth
        if 'cache_dir' in sig.parameters:
            kwargs['cache_dir'] = pyc

        pipeline = Pipeline.from_pretrained('pyannote/speaker-diarization-community-1', **kwargs)
        pipeline.to(torch.device('cpu'))

        # Tiny dry-run to force lazy sub-component resolution during onboarding.
        waveform = torch.zeros((1, 16000 * 3), dtype=torch.float32)
        try:
            pipeline({'waveform': waveform, 'sample_rate': 16000}, min_speakers=1, max_speakers=2)
        except Exception as run_exc:
            log.info('pyannote warm-up inference skipped/partial: %s', run_exc)

        _install_emit('pyannote community-1 is warmed up and cached.')
    except Exception as exc:
        log.warning('pyannote community-1 warm-up failed: %s', exc)
        _install_emit(f'pyannote warm-up skipped: {exc}')


def download_models(app, model_ids, models_path, hf_token=None, finish_onboarding=True, block=False):
    """Download all requested models in a background thread.

    finish_onboarding: if True, sets onboarding_complete and preloads alignment model.
    Set False when re-downloading (e.g. STT model switch) after onboarding is complete.
    block: if True, wait for download to complete before returning.
    """
    from huggingface_hub import snapshot_download, hf_hub_download

    def _run():
        # Transcription sets HF_HUB_OFFLINE after onboarding; allow network here.
        _prev_hf_offline = os.environ.pop('HF_HUB_OFFLINE', None)
        # Also reset the module-level constant — huggingface_hub caches it at import
        # time, so clearing the env var alone is not enough.
        import huggingface_hub.constants as _hf_const
        _prev_hf_const_offline = getattr(_hf_const, 'HF_HUB_OFFLINE', False)
        _hf_const.HF_HUB_OFFLINE = False
        _hf_symlink_orig = None
        try:
            _hf_symlink_orig = _begin_windows_hf_hub_no_symlinks()
            time.sleep(1.5)

            # Skip full pip install when downloading only optional models and core packages are already installed
            requested_models = [MODEL_REGISTRY.get(mid, {}) for mid in model_ids]
            only_optional = all(info.get('required', True) is False for info in requested_models if info)
            all_core_installed = all(p['installed'] for p in _check_packages())

            if only_optional and all_core_installed:
                log.info('Skipping pip install: only optional models requested and core packages ready.')
            else:
                install_pip_packages()

            # ffmpeg rides along with the models. Its first fetch is ~190 MB from
            # GitHub and used to run at the top of run.py, blocking the browser
            # from opening at all; here it is inside the step the user already
            # waits on. No-op once the binaries are in place.
            from .ffmpeg_setup import ensure_ffmpeg, ffmpeg_on_path
            if not ffmpeg_on_path():
                _install_emit('Installing ffmpeg...')
                if ensure_ffmpeg():
                    _install_emit('ffmpeg ready.')
                else:
                    # Not fatal: the models are the point of this step, and
                    # transcription retries the fetch when it starts.
                    _install_emit('ffmpeg install failed -- audio features may not work.')
                    log.warning('ffmpeg could not be prepared during model download')

            # Fix 2: Install model-specific packages (e.g. gliner for gliner-pii)
            optional_pip = _install_model_specific_packages(model_ids)
            # Optional pip installs can alter torch; keep torchvision in sync for WhisperX/transformers.
            if not IS_MAC and optional_pip and not _realign_torchaudio_torchvision():
                log.warning(
                    'torchaudio/torchvision re-align failed after model-specific pip installs; '
                    'transcription may fail until you run: python -m pip install torchaudio torchvision'
                )

            with app.app_context():
                rows = [db.session.get(MLModel, mid) for mid in model_ids]
                rows = [r for r in rows if r and r.status != 'ready']
                overall_total = sum(r.size_bytes for r in rows)
                completed = 0

                pyc_root = pyannote_hub_cache_root(models_path)

                for model in rows:
                    info = MODEL_REGISTRY.get(model.id, {})
                    expected = model.size_bytes or info.get('size_bytes', 0)
                    use_pyc = bool(info.get('use_pyannote_hub_cache'))

                    if use_pyc:
                        os.makedirs(pyc_root, exist_ok=True)
                        os.environ['PYANNOTE_CACHE'] = pyc_root
                        dest = pyc_root
                        snap_existing = snapshot_dir_if_repo_cached(info['repo_id'], pyc_root)
                        if snap_existing and _model_already_on_disk(snap_existing, expected):
                            log.info('Model %s already in pyannote cache, skipping download', model.id)
                            model.status = 'ready'
                            model.path = snap_existing
                            model.downloaded_bytes = expected
                            db.session.commit()
                            completed += expected
                            _safe_emit('download:model_complete', {
                                'model_id': model.id,
                                'overall_downloaded': completed,
                                'overall_total': overall_total,
                            })
                            continue
                    else:
                        dest = os.path.join(models_path, model.id)
                        if _model_already_on_disk(dest, expected):
                            log.info('Model %s already on disk, skipping download', model.id)
                            model.status = 'ready'
                            model.path = dest
                            model.downloaded_bytes = expected
                            db.session.commit()
                            completed += expected
                            _safe_emit('download:model_complete', {
                                'model_id': model.id,
                                'overall_downloaded': completed,
                                'overall_total': overall_total,
                            })
                            continue

                    if not use_pyc:
                        os.makedirs(dest, exist_ok=True)
                    model.status = 'downloading'
                    model.downloaded_bytes = 0
                    db.session.commit()

                    _safe_emit('download:model_start', {
                        'model_id': model.id,
                        'name': model.name,
                        'size_bytes': model.size_bytes,
                    })

                    stop_evt = threading.Event()
                    mon = threading.Thread(
                        target=_monitor_progress,
                        args=(model.id, dest, model.size_bytes, completed, overall_total, stop_evt, app),
                        daemon=True,
                    )
                    mon.start()

                    try:
                        final_path = dest
                        if info.get('filename'):
                            hf_hub_download(
                                repo_id=info['repo_id'],
                                filename=info['filename'],
                                local_dir=dest,
                                token=hf_token,
                            )
                        elif use_pyc:
                            snap_kwargs = {
                                'repo_id': info['repo_id'],
                                'cache_dir': pyc_root,
                                'token': hf_token,
                            }
                            if info.get('ignore_patterns'):
                                snap_kwargs['ignore_patterns'] = info['ignore_patterns']
                            final_path = snapshot_download(**snap_kwargs)
                        else:
                            snap_kwargs = {
                                'repo_id': info['repo_id'],
                                'local_dir': dest,
                                'token': hf_token,
                            }
                            if info.get('ignore_patterns'):
                                snap_kwargs['ignore_patterns'] = info['ignore_patterns']
                            snapshot_download(**snap_kwargs)
                            final_path = dest

                        stop_evt.set()
                        mon.join(timeout=3)

                        # Download tokenizer files if the model needs them locally
                        tok_repo = info.get('tokenizer_repo')
                        tok_files = info.get('tokenizer_files', [])
                        if tok_repo and tok_files:
                            _install_emit(f'Downloading tokenizer for {model.name}...')
                            for tf in tok_files:
                                if not os.path.isfile(os.path.join(dest, tf)):
                                    try:
                                        hf_hub_download(
                                            repo_id=tok_repo,
                                            filename=tf,
                                            local_dir=dest,
                                            token=hf_token,
                                        )
                                    except Exception as tok_exc:
                                        log.warning('Tokenizer file %s download failed: %s', tf, tok_exc)

                        model.status = 'ready'
                        model.path = final_path if use_pyc else dest
                        model.downloaded_bytes = model.size_bytes
                        db.session.commit()
                        completed += model.size_bytes

                        _safe_emit('download:model_complete', {
                            'model_id': model.id,
                            'overall_downloaded': completed,
                            'overall_total': overall_total,
                        })

                    except Exception as exc:
                        stop_evt.set()
                        mon.join(timeout=3)

                        model.status = 'error'
                        model.error_message = str(exc)
                        db.session.commit()

                        _safe_emit('download:error', {
                            'model_id': model.id,
                            'error': str(exc),
                        })

                if finish_onboarding:
                    if not IS_MAC:
                        _preload_alignment_model()
                    _warmup_pyannote_community1(models_path, hf_token=hf_token)
                    from ..models.setting import Setting
                    Setting.set('onboarding_complete', 'true')
                    mark_onboarding_complete()
                    _sync_platform_launcher_layout()
                    _refresh_windows_launcher_shortcuts()
                    # Enable auto-backup by default for new installs
                    if not Setting.get('auto_backup_enabled'):
                        Setting.set('auto_backup_enabled', 'true')

                _safe_emit('download:all_complete', {
                    'overall_total': overall_total,
                })

        finally:
            _end_windows_hf_hub_no_symlinks(_hf_symlink_orig)
            _hf_const.HF_HUB_OFFLINE = _prev_hf_const_offline
            if _prev_hf_offline is not None:
                os.environ['HF_HUB_OFFLINE'] = _prev_hf_offline

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    if block:
        t.join()
