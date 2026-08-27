@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"

REM --- Options ---------------------------------------------------------------
REM --keep-venv   DEV ONLY. Keeps backend\.venv, so the multi-gigabyte ML stack
REM               (torch/torchaudio/whisperx/pyannote, installed by
REM               model_manager.py during onboarding - not by the installer)
REM               survives the reset and onboarding reuses it. Turns a
REM               minutes-long cycle into a seconds-long one.
REM               Because WIN_Install.bat runs its first-time setup only when
REM               .venv is missing, this ALSO skips the install-time file layout
REM               move - so it does not exercise the installer.
REM               Must be OFF for release verification: a release build has to
REM               be reset with no flags, so the installer and the file-layout
REM               move are exercised the way a first-time user hits them.
set "KEEP_VENV="
:parse_args
if "%~1"=="" goto args_done
if /I "%~1"=="--keep-venv" goto arg_keep_venv
echo.
echo   Unknown option: %~1
echo   Usage: reset_win.bat [--keep-venv]
exit /b 2
:arg_keep_venv
set "KEEP_VENV=1"
shift
goto parse_args
:args_done

echo.
echo ============================================================
echo   WARNING: This will PERMANENTLY remove ALL project data.
echo   All projects, recordings, transcripts, and annotations
echo   will be deleted. This cannot be undone.
echo ============================================================
echo.
if defined KEEP_VENV (
    echo   DEV MODE ^(--keep-venv^): backend\.venv will be PRESERVED.
    echo   The installer will skip first-time setup, so the install-time
    echo   file layout is NOT re-tested by this run.
    echo.
)
set /p CONFIRM="Type Yes and press Enter to proceed: "
if /i not "%CONFIRM%"=="Yes" (
    echo Reset cancelled.
    exit /b 0
)
echo.

REM Stop ONLY PINE's own Python processes. Never blanket-kill python.exe /
REM pythonw.exe by image name - that would also terminate the user's unrelated
REM (keep this file pure ASCII: under chcp 65001 a single multi-byte character
REM makes cmd.exe lose its place in the file and run the wrong branch)
REM Python (Jupyter, other apps). We scope the kill two ways:
REM   1) the supervisor PID we recorded in data\supervisor.pid (plus its child
REM      backend/worker processes via /t), and
REM   2) any python(w).exe whose command line runs from THIS install directory.
echo Stopping PINE background processes...
set "PINE_PIDFILE=%~dp0data\supervisor.pid"
if exist "%PINE_PIDFILE%" (
    set "PINE_SUP_PID="
    set /p PINE_SUP_PID=<"%PINE_PIDFILE%"
    if defined PINE_SUP_PID taskkill /f /t /pid !PINE_SUP_PID! >nul 2>&1
)
powershell -NoProfile -ExecutionPolicy Bypass -Command "$dir=[regex]::Escape('%~dp0'); Get-CimInstance Win32_Process -Filter \"Name='python.exe' OR Name='pythonw.exe'\" -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -match $dir } | ForEach-Object { try { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue } catch {} }" >nul 2>&1
timeout /t 2 /nobreak >nul

if exist "..\backend\data\onboarding_complete.flag" del /f /q "..\backend\data\onboarding_complete.flag" >nul 2>nul
for %%L in ("Launch Pine.bat" "Launch Pine.command" "Launch Pine.vbs" "Launch_WIN.bat" "Launch_MAC.command") do (
    if exist "..\%%~L" del /f /q "..\%%~L" >nul 2>nul
)

REM Restore dev files moved during install
for %%F in (LICENSE .editorconfig .gitattributes .gitignore) do (
    if exist "..\documentation\dev-config\%%F" if not exist "..\%%F" move "..\documentation\dev-config\%%F" "..\%%F" >nul 2>&1
)
if exist "..\documentation\dev-config\" rd "..\documentation\dev-config" >nul 2>&1

if not exist "..\WIN_Install.bat" if exist ".\WIN_Install.bat" copy /y ".\WIN_Install.bat" "..\WIN_Install.bat" >nul
if not exist "..\MAC_Install.command" if exist ".\MAC_Install.command" copy /y ".\MAC_Install.command" "..\MAC_Install.command" >nul
if exist ".\WIN_Install.bat" del /f /q ".\WIN_Install.bat" >nul 2>nul
if exist ".\MAC_Install.command" del /f /q ".\MAC_Install.command" >nul 2>nul

REM What survives a reset is listed in tools\reset_preserve_*.txt - the same two
REM files reset.command and the in-app reset read, so the three cannot drift
REM apart. Everything unlisted is deleted, so a missing or truncated list must
REM stop the run here, before anything is removed.
set "PRESERVE_ROOT_LIST=%~dp0tools\reset_preserve_root.txt"
set "PRESERVE_BACKEND_LIST=%~dp0tools\reset_preserve_backend.txt"
for %%L in ("%PRESERVE_ROOT_LIST%" "%PRESERVE_BACKEND_LIST%") do (
    if not exist "%%~L" (
        echo [ERROR] Missing allowlist %%~nxL - nothing was deleted.
        exit /b 1
    )
)
set /a PRESERVE_ROOT_COUNT=0
for /f "usebackq eol=# delims=" %%K in ("%PRESERVE_ROOT_LIST%") do set /a PRESERVE_ROOT_COUNT+=1
set /a PRESERVE_BACKEND_COUNT=0
for /f "usebackq eol=# delims=" %%K in ("%PRESERVE_BACKEND_LIST%") do set /a PRESERVE_BACKEND_COUNT+=1
if !PRESERVE_ROOT_COUNT! LSS 8 (
    echo [ERROR] reset_preserve_root.txt looks truncated - nothing was deleted.
    exit /b 1
)
if !PRESERVE_BACKEND_COUNT! LSS 8 (
    echo [ERROR] reset_preserve_backend.txt looks truncated - nothing was deleted.
    exit /b 1
)

set FAIL=0

for /f "delims=" %%I in ('dir /b /a ".."') do (
    set "NAME=%%~I"
    set "KEEP=0"
    for /f "usebackq eol=# delims=" %%K in ("%PRESERVE_ROOT_LIST%") do (
        if /I "!NAME!"=="%%~K" set "KEEP=1"
    )
    if "!KEEP!"=="0" (
        if exist "..\%%~I\" (
            echo Removing !NAME!...
            rd /s /q "..\%%~I"
            if exist "..\%%~I\" (
                echo [ERROR] Could not remove !NAME! - files may still be in use.
                set FAIL=1
            )
        ) else (
            echo Removing !NAME!...
            del /f /q "..\%%~I"
            if exist "..\%%~I" (
                echo [ERROR] Could not remove !NAME! - files may still be in use.
                set FAIL=1
            )
        )
    )
)

for /f "delims=" %%I in ('dir /b /a "."') do (
    set "NAME=%%~I"
    set "KEEP=0"
    for /f "usebackq eol=# delims=" %%K in ("%PRESERVE_BACKEND_LIST%") do (
        if /I "!NAME!"=="%%~K" set "KEEP=1"
    )
    if defined KEEP_VENV if /I "!NAME!"==".venv" set "KEEP=1"
    if "!KEEP!"=="0" (
        if exist ".\%%~I\" (
            echo Removing backend\!NAME!...
            rd /s /q ".\%%~I"
            if exist ".\%%~I\" (
                echo [ERROR] Could not remove backend\!NAME! - files may still be in use.
                set FAIL=1
            )
        ) else (
            echo Removing backend\!NAME!...
            del /f /q ".\%%~I"
            if exist ".\%%~I" (
                echo [ERROR] Could not remove backend\!NAME! - files may still be in use.
                set FAIL=1
            )
        )
    )
)

REM Purge Python bytecode and test caches so reset returns a pristine source tree.
REM With --keep-venv the sweep is scoped to the source folders: recursing through
REM a preserved .venv would churn thousands of site-packages caches for no gain
REM and spend exactly the time the flag exists to save.
echo Removing Python caches...
if defined KEEP_VENV (
    REM FOR /R will not take a FOR variable as its root - it fails to parse - so
    REM each source folder is swept in a subroutine where the root is a plain
    REM parameter.
    for %%S in (app ml_worker scripts templates tests tools) do if exist "%%S\" call :purge_caches "%%S"
    if exist "__pycache__" rd /s /q "__pycache__" >nul 2>nul
    if exist ".pytest_cache" rd /s /q ".pytest_cache" >nul 2>nul
    del /q ".\*.pyc" >nul 2>nul
    del /q ".\*.pyo" >nul 2>nul
) else (
    for /d /r "." %%D in (__pycache__ .pytest_cache) do if exist "%%~D" rd /s /q "%%~D" >nul 2>nul
    del /s /q ".\*.pyc" >nul 2>nul
    del /s /q ".\*.pyo" >nul 2>nul
)

echo.
if %FAIL%==1 (
    echo Reset INCOMPLETE. Some directories could not be removed.
    echo Close all apps and terminals, then try again.
    exit /b 1
)
if defined KEEP_VENV (
    echo Reset complete. Models folder and backend\.venv preserved.
    echo Onboarding will reuse the ML packages already in the venv.
    echo This is a DEV shortcut - run without --keep-venv before a release.
) else (
    echo Reset complete. Models folder preserved.
)
endlocal
exit /b 0

REM Sweep __pycache__/.pytest_cache and stray bytecode under one source folder.
REM Only reached via CALL from the --keep-venv purge branch above.
:purge_caches
for /d /r "%~1" %%D in (__pycache__ .pytest_cache) do if exist "%%~D" rd /s /q "%%~D" >nul 2>nul
del /s /q "%~1\*.pyc" >nul 2>nul
del /s /q "%~1\*.pyo" >nul 2>nul
exit /b 0
