@echo off
setlocal EnableDelayedExpansion
title PINE - Private Interview ^& Notes Environment
cd /d "%~dp0"

set "SCRIPT_DIR=%~dp0"
set "BACKEND_DIR=%SCRIPT_DIR%"
if not exist "%BACKEND_DIR%run.py" set "BACKEND_DIR=%SCRIPT_DIR%backend\"
set "PINE_ROOT_DIR=%BACKEND_DIR%"
for %%D in ("%BACKEND_DIR%..") do set "PINE_ROOT_DIR=%%~fD\"

set "VENV_DIR=%BACKEND_DIR%.venv"
set "LOG_DIR=%BACKEND_DIR%logs"
set "ONBOARDING_FLAG=%BACKEND_DIR%data\onboarding_complete.flag"
set "PINE_BACKGROUND_WAIT_SECONDS=180"
set "PINE_LAUNCHER_RUN_ID=%RANDOM%%RANDOM%"
set "PINE_STAGE_FILE=%LOG_DIR%\launcher-stage.txt"
set "PINE_LAUNCHER_LOG=%LOG_DIR%\launcher.log"
set "PINE_LAUNCHER_RUNNER_LOG=%LOG_DIR%\launcher-runner.log"
set "PINE_HIDDEN_CMD=%LOG_DIR%\launcher-hidden-%PINE_LAUNCHER_RUN_ID%.cmd"

if not exist "%LOG_DIR%" mkdir "%LOG_DIR%" >nul 2>nul
if not exist "%VENV_DIR%\Scripts\activate.bat" goto :run_installer
if not exist "%VENV_DIR%\Scripts\python.exe" goto :run_installer

call "%VENV_DIR%\Scripts\activate.bat"
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
echo   Run backend\WIN_Install.bat to finish setup.
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
del /q "%LOG_DIR%\launcher-hidden*.cmd" >nul 2>nul
del /q "%LOG_DIR%\launcher-diagnostic*.cmd" >nul 2>nul
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
if exist "%VENV_DIR%\Scripts\pythonw.exe" (
    start "" "%VENV_DIR%\Scripts\pythonw.exe" supervisor.py
) else if exist "%VENV_DIR%\Scripts\python.exe" (
    powershell -NoProfile -Command "Start-Process -WindowStyle Hidden -FilePath '%VENV_DIR%\Scripts\python.exe' -ArgumentList 'supervisor.py'" >nul
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
    if exist "%VENV_DIR%\Scripts\pythonw.exe" (
        start "" "%VENV_DIR%\Scripts\pythonw.exe" supervisor.py
    ) else if exist "%VENV_DIR%\Scripts\python.exe" (
        powershell -NoProfile -Command "Start-Process -WindowStyle Hidden -FilePath '%VENV_DIR%\Scripts\python.exe' -ArgumentList 'supervisor.py'" >nul
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
