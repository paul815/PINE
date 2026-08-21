@echo off
setlocal EnableDelayedExpansion

rem ---------------------------------------------------------------------------
rem PINE dev reset - DEV ONLY, not part of the shipped flow.
rem
rem Clears runtime data only: the SQLite DB, the onboarding flag and the projects
rem folder. It deliberately does NOT touch backend\.venv, models\, logs\ or the
rem installed file layout, so the app comes back at the onboarding screen in
rem seconds instead of reinstalling the multi-gigabyte ML stack that
rem model_manager.py pulls during onboarding.
rem
rem Use this for the day-to-day loop. For a release-grade reset that also
rem exercises the installer and the file layout, run backend\reset_win.bat with
rem no flags. See documentation\TODO.md, "Before release - dev-only test
rem shortcuts", for what has to be verified before shipping.
rem
rem Usage: backend\tools\dev_reset.bat [-y] [--projects-dir <path>]
rem   -y, --yes           Skip the confirmation prompt.
rem   --projects-dir      Projects folder to clear. Defaults to <repo>\projects;
rem                       pass this if you moved it in Settings, because the path
rem                       lives in the database this script is about to delete.
rem ---------------------------------------------------------------------------

for %%A in ("%~dp0..") do set "BACKEND_DIR=%%~fA"
for %%A in ("%~dp0..\..") do set "ROOT_DIR=%%~fA"
set "ASSUME_YES="
set "PROJECTS_DIR=%ROOT_DIR%\projects"

:parse_args
if "%~1"=="" goto args_done
if /I "%~1"=="-y" goto arg_yes
if /I "%~1"=="--yes" goto arg_yes
if /I "%~1"=="--projects-dir" goto arg_projects
echo.
echo   Unknown option: %~1
echo   Usage: dev_reset.bat [-y] [--projects-dir ^<path^>]
exit /b 2
:arg_yes
set "ASSUME_YES=1"
shift
goto parse_args
:arg_projects
if "%~2"=="" (
    echo.
    echo   --projects-dir needs a path.
    exit /b 2
)
for %%A in ("%~2") do set "PROJECTS_DIR=%%~fA"
shift
shift
goto parse_args
:args_done

echo.
echo ============================================================
echo   DEV RESET: removes ALL projects, recordings and settings.
echo   Keeps backend\.venv, models\, logs\ and the file layout.
echo ============================================================
echo.
echo   Data:     %BACKEND_DIR%\data
echo   Projects: %PROJECTS_DIR%
echo.
if not defined ASSUME_YES (
    set /p CONFIRM="Type Yes and press Enter to proceed: "
    if /I not "!CONFIRM!"=="Yes" (
        echo Dev reset cancelled.
        exit /b 0
    )
    echo.
)

rem Stop ONLY PINE's own Python processes - same scoping as reset_win.bat: the
rem recorded supervisor PID (plus its children via /t) and any python(w).exe
rem whose command line runs from THIS backend directory. Never blanket-kill
rem python.exe by image name - that would also take out unrelated Python.
echo Stopping PINE background processes...
set "PINE_PIDFILE=%BACKEND_DIR%\data\supervisor.pid"
if exist "%PINE_PIDFILE%" (
    set "PINE_SUP_PID="
    set /p PINE_SUP_PID=<"%PINE_PIDFILE%"
    if defined PINE_SUP_PID taskkill /f /t /pid !PINE_SUP_PID! >nul 2>&1
)
rem The settle-wait is folded into the same PowerShell call instead of using
rem TIMEOUT, which refuses to run at all when stdin is not a console ("Input
rem redirection is not supported") - and this script is meant to be scriptable.
powershell -NoProfile -ExecutionPolicy Bypass -Command "$dir=[regex]::Escape('%BACKEND_DIR%'); Get-CimInstance Win32_Process -Filter \"Name='python.exe' OR Name='pythonw.exe'\" -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -match $dir } | ForEach-Object { try { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue } catch {} }; Start-Sleep -Seconds 2" >nul 2>&1

set FAIL=0

if exist "%BACKEND_DIR%\data\" (
    echo Removing data...
    rd /s /q "%BACKEND_DIR%\data"
    if exist "%BACKEND_DIR%\data\" (
        echo [ERROR] Could not remove data - the app may still be running.
        set FAIL=1
    )
)

if exist "%PROJECTS_DIR%\" (
    echo Removing projects...
    rd /s /q "%PROJECTS_DIR%"
    if exist "%PROJECTS_DIR%\" (
        echo [ERROR] Could not remove projects - files may still be in use.
        set FAIL=1
    )
)

echo.
if %FAIL%==1 (
    echo Dev reset INCOMPLETE. Close the app and any open file, then try again.
    exit /b 1
)
echo Dev reset complete. Launch PINE to start at onboarding.
echo Models, venv and installed layout untouched.
endlocal
