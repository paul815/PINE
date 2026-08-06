@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"

echo.
echo ============================================================
echo   WARNING: This will PERMANENTLY remove ALL project data.
echo   All projects, recordings, transcripts, and annotations
echo   will be deleted. This cannot be undone.
echo ============================================================
echo.
set /p CONFIRM="Type Yes and press Enter to proceed: "
if /i not "%CONFIRM%"=="Yes" (
    echo Reset cancelled.
    exit /b 0
)
echo.

REM Stop ONLY PINE's own Python processes. Never blanket-kill python.exe /
REM pythonw.exe by image name — that would also terminate the user's unrelated
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
for %%F in (AGENTS.md LICENSE .editorconfig .gitattributes .gitignore .pre-commit-config.yaml .python-version) do (
    if exist "..\Documentation\dev-config\%%F" if not exist "..\%%F" move "..\Documentation\dev-config\%%F" "..\%%F" >nul 2>&1
)
if exist "..\Documentation\dev-config\" rd "..\Documentation\dev-config" >nul 2>&1

if not exist "..\WIN_Install.bat" if exist ".\WIN_Install.bat" copy /y ".\WIN_Install.bat" "..\WIN_Install.bat" >nul
if not exist "..\MAC_Install.command" if exist ".\MAC_Install.command" copy /y ".\MAC_Install.command" "..\MAC_Install.command" >nul
if exist ".\WIN_Install.bat" del /f /q ".\WIN_Install.bat" >nul 2>nul
if exist ".\MAC_Install.command" del /f /q ".\MAC_Install.command" >nul 2>nul

set FAIL=0

for /f "delims=" %%I in ('dir /b /a ".."') do (
    set "NAME=%%~I"
    set "KEEP=0"
    for %%K in (".editorconfig" ".gitattributes" ".github" ".gitignore" ".git" ".pre-commit-config.yaml" ".python-version" "AGENTS.md" "Documentation" "LICENSE" "MAC_Install.command" "README.md" "WIN_Install.bat" "backend" "models") do (
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
    for %%K in ("app" "ml_worker" "design-audit.js" "package-lock.json" "package.json" "pytest.ini" "requirements-lock.txt" "requirements.txt" "reset.command" "reset_win.bat" "run.py" "scripts" "supervisor.py" "templates" "tests" "tools" "MAC_Install.command" "WIN_Install.bat") do (
        if /I "!NAME!"=="%%~K" set "KEEP=1"
    )
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
echo Removing Python caches...
for /d /r "." %%D in (__pycache__ .pytest_cache) do if exist "%%~D" rd /s /q "%%~D" >nul 2>nul
del /s /q ".\*.pyc" >nul 2>nul
del /s /q ".\*.pyo" >nul 2>nul

echo.
if %FAIL%==1 (
    echo Reset INCOMPLETE. Some directories could not be removed.
    echo Close all apps and terminals, then try again.
    exit /b 1
)
echo Reset complete. Models folder preserved.
endlocal
