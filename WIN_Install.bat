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
set "SELF_NAME=%~nx0"
set "NEED_SETUP=1"
set "LOG_DIR=%BACKEND_DIR%logs"
set "LAUNCHER_LOG=%LOG_DIR%\launcher.log"

if not exist "%LOG_DIR%" mkdir "%LOG_DIR%" >nul 2>&1
>>"%LAUNCHER_LOG%" echo [LAUNCHER] [%date% %time%] Starting launcher from "%~f0"

if exist "%VENV_DIR%\Scripts\activate.bat" if exist "%VENV_DIR%\Scripts\python.exe" set "NEED_SETUP=0"

REM --- First-time setup (only if .venv is missing) ---
if "%NEED_SETUP%"=="1" (
    echo.
    echo   First launch -- setting up PINE ^(this takes a minute^)...
    echo.

    REM Find Python 3.11, 3.12, or 3.13 (ML stack does not support 3.14+ yet)
    set PYEXE=
    set PYVER=
    for %%c in (python python3) do (
        if not defined PYEXE (
            for /f "delims=" %%p in ('%%c -c "import sys; v=sys.version_info; print(sys.executable) if v.major==3 and v.minor in (11,12,13) else exit(1)" 2^>nul') do (
                set PYEXE=%%p
                for /f "delims=" %%v in ('%%c -c "import sys; print(f\"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}\")" 2^>nul') do set PYVER=%%v
            )
        )
    )

    if not defined PYEXE (
        echo.
        echo   ERROR: Python 3.11, 3.12, or 3.13 not found.
        set FOUND_VER=
        for /f "delims=" %%v in ('python -c "import sys; print(f\"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}\")" 2^>nul') do set FOUND_VER=%%v
        if not defined FOUND_VER for /f "delims=" %%v in ('python3 -c "import sys; print(f\"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}\")" 2^>nul') do set FOUND_VER=%%v
        if defined FOUND_VER (
            echo   Current Python: !FOUND_VER!
            echo.
        )
        echo   PINE requires 3.11, 3.12, or 3.13 because WhisperX and its
        echo   dependencies ^(ctranslate2, PyTorch^) only provide wheels for those versions.
        echo.
        choice /C YN /M "Would you like to install Python 3.13 now"
        if errorlevel 2 goto :py_not_found_exit
        echo.
        echo   Installing Python 3.13 via winget...
        winget install Python.Python.3.13 --accept-package-agreements --accept-source-agreements >>"%LAUNCHER_LOG%" 2>&1
        if errorlevel 1 (
            echo   Winget not available or failed. Opening download page...
            start https://www.python.org/downloads/release/python-3130/
            echo.
            echo   After installing, run !SELF_NAME! again.
            echo.
            pause
            exit /b 0
        )
        echo.
        echo   Python 3.13 installed. Launching PINE...
        start "" "%~f0"
        exit /b 0
        :py_not_found_exit
        echo.
        echo   Download manually: https://www.python.org/downloads/
        echo.
        pause
        exit /b 1
    )

    echo   Using Python !PYVER!: !PYEXE!

    if exist "%VENV_DIR%" (
        echo   Found an incomplete virtual environment. Rebuilding it...
        rmdir /s /q "%VENV_DIR%" 2>>"%LAUNCHER_LOG%"
        if exist "%VENV_DIR%" (
            timeout /t 1 /nobreak >nul
            rmdir /s /q "%VENV_DIR%" 2>>"%LAUNCHER_LOG%"
        )
        if exist "%VENV_DIR%" (
            echo.
            echo   Failed to remove "%VENV_DIR%".
            echo   Close any running PINE, python.exe, or pythonw.exe processes and delete the ".venv" folder manually.
            echo   Then run %SELF_NAME% again.
            echo.
            pause
            exit /b 1
        )
    )

    echo   Creating virtual environment...
    "!PYEXE!" -m venv "%VENV_DIR%"
    if errorlevel 1 (
        echo   Virtual environment creation failed. Retrying once...
        if exist "%VENV_DIR%" (
            rmdir /s /q "%VENV_DIR%" 2>>"%LAUNCHER_LOG%"
            if exist "%VENV_DIR%" (
                timeout /t 1 /nobreak >nul
                rmdir /s /q "%VENV_DIR%" 2>>"%LAUNCHER_LOG%"
            )
            if exist "%VENV_DIR%" (
                echo.
                echo   Failed to remove "%VENV_DIR%".
                echo   Close any running PINE, python.exe, or pythonw.exe processes and delete the ".venv" folder manually.
                echo   Then run %SELF_NAME% again.
                echo.
                pause
                exit /b 1
            )
        )
        timeout /t 2 /nobreak >nul
        "!PYEXE!" -m venv "%VENV_DIR%"
        if errorlevel 1 (
            echo   Failed to create virtual environment.
            pause
            exit /b 1
        )
    )

    call "%VENV_DIR%\Scripts\activate.bat"
    echo   Upgrading pip...
    python -m pip install --upgrade pip --quiet >>"%LAUNCHER_LOG%" 2>&1
    echo   Installing dependencies...
    pip install -r "%BACKEND_DIR%requirements.txt" --quiet >>"%LAUNCHER_LOG%" 2>&1
    if errorlevel 1 (
        echo   Failed to install requirements.
        pause
        exit /b 1
    )

    REM Move dev/GitHub files out of root for end-users. .gitattributes is NOT in
    REM this list on purpose: it carries "*.bat text eol=crlf", and cmd.exe cannot
    REM find labels in an LF batch file -- this very launcher dies at
    REM "call :open_browser" and the window closes with no browser. Move it away and
    REM the next git checkout rewrites the installers as LF.
    if not exist "%PINE_ROOT_DIR%documentation\dev-config\" mkdir "%PINE_ROOT_DIR%documentation\dev-config\"
    for %%F in (AGENTS.md LICENSE .editorconfig .gitignore .pre-commit-config.yaml) do (
        if exist "%PINE_ROOT_DIR%%%F" move "%PINE_ROOT_DIR%%%F" "%PINE_ROOT_DIR%documentation\dev-config\%%F" >nul 2>&1
    )

    echo.
    echo   First setup step complete!
    echo.

    REM Tidy the repo root now that the install finished.
    call :finalize_install_layout
)

call :ensure_local_launcher_shortcut

REM --- Launch via Python launcher ----------------------------------------
call "%VENV_DIR%\Scripts\activate.bat"
cd /d "%BACKEND_DIR%"

REM This console window exists only to show the install. Hand PINE off to a hidden
REM background instance, wait until the browser is up, then let this window close.
if /I "%PINE_HIDDEN_LAUNCH%"=="1" goto :hidden_main
call :relaunch_hidden
call :wait_for_background_launch_and_open
REM Remove the root installer copy only now, as this window's very last act.
REM cmd reads a batch file lazily and never locks it, so deleting the file any
REM earlier (the old schedule_self_delete helper) killed this very process at
REM its next line read -- the window vanished before it could poll the backend
REM or open the browser. "(goto) 2>nul" ends batch processing for this
REM already-parsed line, so the del runs with the file no longer needed.
if defined PINE_CLEANUP_ROOT_INSTALLER ((goto) 2>nul & del /f /q "%PINE_CLEANUP_ROOT_INSTALLER%")
exit /b 0

:hidden_main
REM Only the hidden instance gets here now; its output already goes to the log.
echo.
echo   Starting PINE...
echo.
start /b python supervisor.py
call :resolve_ports
call :wait_for_ready_and_open
python -c "import urllib.request; urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:!PINE_SUP_PORT!/status', method='GET'), timeout=2)" >nul 2>nul
if errorlevel 1 (
    endlocal & exit /b 0
)
REM Supervisor is still running; keep the window open until it exits
:keep_alive_loop
ping -n 6 127.0.0.1 >nul 2>&1
python -c "import urllib.request; urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:!PINE_SUP_PORT!/status', method='GET'), timeout=2)" >nul 2>nul
if not errorlevel 1 goto keep_alive_loop
endlocal & exit /b 0

:ensure_local_launcher_shortcut
set "PINE_ROOT_DIR=%~dp0"
if not exist "%PINE_ROOT_DIR%backend\" (
    for %%D in ("%~dp0..") do set "PINE_ROOT_DIR=%%~fD\"
)
set "PINE_SHORTCUT_PATH=%PINE_ROOT_DIR%Launch Pine.lnk"
set "PINE_SHORTCUT_TARGET=%PINE_ROOT_DIR%backend\Launch Pine.bat"
if not exist "%PINE_SHORTCUT_TARGET%" set "PINE_SHORTCUT_TARGET=%PINE_ROOT_DIR%backend\WIN_Install.bat"
set "PINE_SHORTCUT_ICON=%PINE_ROOT_DIR%backend\app\static\icons\pine.ico"
if not exist "%PINE_SHORTCUT_TARGET%" goto :eof
if not exist "%PINE_SHORTCUT_ICON%" set "PINE_SHORTCUT_ICON=%WINDIR%\System32\shell32.dll,220"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; $ws = New-Object -ComObject WScript.Shell; $sc = $ws.CreateShortcut($env:PINE_SHORTCUT_PATH); $sc.TargetPath = $env:PINE_SHORTCUT_TARGET; $sc.WorkingDirectory = $env:PINE_ROOT_DIR.TrimEnd('\'); $sc.Description = 'Launch Pine'; $sc.IconLocation = $env:PINE_SHORTCUT_ICON; $sc.Save()" >>"%LAUNCHER_LOG%" 2>&1
if exist "%PINE_SHORTCUT_PATH%" goto :eof
echo   Warning: could not create Launch Pine.lnk in the repo root.
echo   Target was "%PINE_SHORTCUT_TARGET%".
goto :eof

:relaunch_hidden
echo   Starting PINE in the background...
REM Prefer the canonical backend\ copy: the root copy may be scheduled for
REM deletion right after a first install, and running it would keep it locked.
set "PINE_HIDDEN_TARGET=%~f0"
if exist "%BACKEND_DIR%WIN_Install.bat" set "PINE_HIDDEN_TARGET=%BACKEND_DIR%WIN_Install.bat"
REM Route the command through a helper .cmd instead of passing it inline, so the
REM argument handed to cmd is a single path with no embedded quoting to unpick.
set "PINE_HIDDEN_CMD=%LOG_DIR%\hidden-launch.cmd"
> "%PINE_HIDDEN_CMD%" echo @echo off
>>"%PINE_HIDDEN_CMD%" echo set "PINE_HIDDEN_LAUNCH=1"
REM Its own file, not launcher.log: the outer redirect below holds that handle
REM open for the whole run, so the [LAUNCHER] lines the script appends to
REM launcher.log directly would collide with it and be dropped.
>>"%PINE_HIDDEN_CMD%" echo call "%PINE_HIDDEN_TARGET%" ^>^>"%LOG_DIR%\launcher-hidden.log" 2^>^&1
set "PINE_HIDDEN_LAUNCHER=%PINE_HIDDEN_CMD%"
REM The [char]34 wrapping is load-bearing: Start-Process joins ArgumentList with
REM spaces and quotes nothing, so an install path like "F:\AI Stuff\..." reaches
REM cmd unquoted and dies on "'F:\AI' is not recognized". The window is hidden,
REM so that error goes nowhere and the caller just polls a dead port instead.
powershell -NoProfile -Command "$q=[char]34; Start-Process -WindowStyle Hidden -WorkingDirectory $env:PINE_ROOT_DIR -FilePath $env:ComSpec -ArgumentList '/c', ($q + $env:PINE_HIDDEN_LAUNCHER + $q)" >>"%LAUNCHER_LOG%" 2>&1
goto :eof

:wait_for_background_launch_and_open
echo.
echo   Waiting for PINE to become ready...
echo   This window closes by itself once PINE opens in your browser.
echo   Details: logs\launcher.log
echo.
call :resolve_ports
REM No port file means the hidden instance never got as far as the supervisor.
REM Polling the backend for two minutes could not succeed, so fail out now.
if not exist "%PINE_PORT_FILE%" (
    echo   PINE could not start: the background process never came up.
    call :show_backend_failure
    pause
    goto :eof
)
set WAIT_BG_COUNT=0
:waitbgloop
REM Matches the 120s budget of :wait_for_ready_and_open. A first boot after a
REM fresh install is slow, and this window is what opens the browser now.
if !WAIT_BG_COUNT! GEQ 120 (
    echo   PINE started in the background, but the browser did not open automatically.
    echo   Open http://pine.localhost:!PINE_BACKEND_PORT!/ manually.
    echo   If that page does not load, see logs\launcher.log.
    call :show_backend_failure
    echo.
    REM This window closes itself on success, so hold it open on the failure path
    REM long enough for the message above to actually be read.
    pause
    goto :eof
)
REM ping is the 1s sleep here: unlike "timeout" it survives redirected stdin,
REM and unlike spawning powershell it adds no process-startup cost per pass --
REM that overhead is what used to stretch this 120s budget past five minutes.
ping -n 2 127.0.0.1 >nul 2>&1
REM Discard curl's stderr: "connection refused" once a second is the expected
REM state while the backend boots, and this window and the hidden instance poll
REM in lockstep -- both appending to one log just made them collide on it.
curl -sf --max-time 2 http://127.0.0.1:!PINE_BACKEND_PORT!/ >nul 2>nul
if not errorlevel 1 goto waitbgready
REM The supervisor outlives a slow boot but exits once the backend fails for
REM good; without it, waiting out the rest of the budget can only time out.
REM Skip the first few passes -- it binds its port a second or two after
REM writing the port file, and a miss there is startup, not failure.
if !WAIT_BG_COUNT! LSS 10 goto waitbgtick
curl -sf --max-time 2 http://127.0.0.1:!PINE_SUP_PORT!/status >nul 2>nul
if errorlevel 1 goto waitbgdead
:waitbgtick
set /a WAIT_BG_COUNT+=1
goto waitbgloop
:waitbgdead
echo   PINE stopped while starting up.
call :show_backend_failure
echo.
pause
goto :eof
:waitbgready
call :open_browser "http://pine.localhost:!PINE_BACKEND_PORT!/"
goto :eof

:show_backend_failure
REM A backend crash lands in logs\<date>\backend-*.log, not launcher.log, so the
REM console would otherwise show a bare "did not open" with nothing to act on.
set "PINE_LOG_ROOT=%LOG_DIR%"
REM Prefer the backend's own log; fall back to the hidden instance's console
REM output, which is where a failure before Python starts shows up.
powershell -NoProfile -ExecutionPolicy Bypass -Command "$d = $env:PINE_LOG_ROOT; $f = Get-ChildItem -Path $d -Recurse -Filter 'backend-*.log' -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 1; if (-not $f) { $f = Get-Item (Join-Path $d 'launcher-hidden.log') -ErrorAction SilentlyContinue }; if ($f) { Write-Host ''; Write-Host ('  Last lines of ' + $f.Name + ':'); Write-Host ''; Get-Content $f.FullName -Tail 20 | ForEach-Object { Write-Host ('    ' + $_) } } else { Write-Host ''; Write-Host '  No log was written -- PINE never got far enough to start.' }"
echo.
echo   Full logs: %LOG_DIR%
goto :eof

:open_browser
set "PINE_URL=%~1"
REM The visible parent window owns opening the browser. Without this guard the
REM hidden instance opens a second tab for the same URL.
if /I "%PINE_HIDDEN_LAUNCH%"=="1" (
    >>"%LAUNCHER_LOG%" echo [LAUNCHER] [%date% %time%] Hidden instance: leaving browser open to parent.
    goto :eof
)
>>"%LAUNCHER_LOG%" echo [LAUNCHER] [%date% %time%] Opening browser: "%PINE_URL%"
powershell -NoProfile -Command "Start-Process '%PINE_URL%'" >>"%LAUNCHER_LOG%" 2>&1
if errorlevel 1 (
    >>"%LAUNCHER_LOG%" echo [LAUNCHER] [%date% %time%] Start-Process failed, trying start fallback.
    start "" "%PINE_URL%"
    if errorlevel 1 (
        >>"%LAUNCHER_LOG%" echo [LAUNCHER] [%date% %time%] Browser open fallback also failed.
    ) else (
        >>"%LAUNCHER_LOG%" echo [LAUNCHER] [%date% %time%] Browser opened via fallback.
    )
) else (
    >>"%LAUNCHER_LOG%" echo [LAUNCHER] [%date% %time%] Browser open command sent successfully.
)
goto :eof

:resolve_ports
REM Read the ports the supervisor actually bound. It scans upward from 5000/5001
REM when those are busy (see supervisor.py _find_free_port) and records the chosen
REM values in data\supervisor.port. Reading them here keeps the browser-open and
REM status polls aligned with the real ports. Falls back to the 5000/5001 defaults
REM if the file never appears, so the worst case matches the previous behaviour.
if not defined PINE_BACKEND_PORT set "PINE_BACKEND_PORT=5000"
if not defined PINE_SUP_PORT set "PINE_SUP_PORT=5001"
set "PINE_PORT_FILE=%BACKEND_DIR%data\supervisor.port"
set /a PINE_PORT_WAIT=0
:resolve_ports_wait
if exist "%PINE_PORT_FILE%" goto :resolve_ports_read
set /a PINE_PORT_WAIT+=1
if !PINE_PORT_WAIT! GEQ 30 goto :eof
REM ping, not timeout: the hidden instance runs with its output redirected, and
REM timeout aborts on redirected stdin. That turned this 30s wait into a no-op.
ping -n 2 127.0.0.1 >nul 2>&1
goto :resolve_ports_wait
:resolve_ports_read
for /f "delims=" %%p in ('python -c "import json,os; print(json.load(open(os.environ['PINE_PORT_FILE'])).get('backend_port',5000))" 2^>nul') do set "PINE_BACKEND_PORT=%%p"
for /f "delims=" %%p in ('python -c "import json,os; print(json.load(open(os.environ['PINE_PORT_FILE'])).get('supervisor_port',5001))" 2^>nul') do set "PINE_SUP_PORT=%%p"
goto :eof

:wait_for_ready_and_open
if not defined PINE_BACKEND_PORT set "PINE_BACKEND_PORT=5000"
set WAIT_READY_COUNT=0
:waitreadyloop
if !WAIT_READY_COUNT! GEQ 120 (
    echo   Timed out waiting for backend. Open http://pine.localhost:!PINE_BACKEND_PORT!/ manually.
    goto :eof
)
ping -n 2 127.0.0.1 >nul 2>&1
curl -sf --max-time 2 http://127.0.0.1:!PINE_BACKEND_PORT!/ >nul 2>nul
if errorlevel 1 (
    set /a WAIT_READY_COUNT+=1
    goto waitreadyloop
)
call :open_browser "http://pine.localhost:!PINE_BACKEND_PORT!/"
goto :eof

:reset_venv
if not exist "%VENV_DIR%" goto :eof
rmdir /s /q "%VENV_DIR%" 2>>"%LAUNCHER_LOG%"
if exist "%VENV_DIR%" (
    timeout /t 1 /nobreak >nul
    rmdir /s /q "%VENV_DIR%" 2>>"%LAUNCHER_LOG%"
)
if exist "%VENV_DIR%" (
    echo.
    echo   Failed to remove "%VENV_DIR%".
    echo   Close any running PINE, python.exe, or pythonw.exe processes and delete the ".venv" folder manually.
    echo   Then run %SELF_NAME% again.
    echo.
    pause
    exit /b 1
)
goto :eof

:finalize_install_layout
REM Tidy the repo root after a fresh install: keep only "Launch Pine.lnk" plus the
REM backend\ and documentation\ folders. Canonical installer copies live in backend\.
set "DOC_DIR=%PINE_ROOT_DIR%documentation"
set "DEVCFG_DIR=%DOC_DIR%\dev-config"
if not exist "%DOC_DIR%\" mkdir "%DOC_DIR%" >nul 2>nul
if not exist "%DEVCFG_DIR%\" mkdir "%DEVCFG_DIR%" >nul 2>nul

REM Docs -> documentation\ . The root CLAUDE.md is the context-mode routing copy and
REM documentation\ already ships its own, so park it under a distinct name.
call :relocate_root_file "DESIGN.md" "%DOC_DIR%\DESIGN.md"
call :relocate_root_file "CLAUDE.md" "%DOC_DIR%\CLAUDE.context-mode.md"
REM .gitattributes deliberately stays in the root -- see the note above the
REM dev-config move list. Losing it makes every .bat check out as LF.
call :relocate_root_file ".gitignore" "%DEVCFG_DIR%\.gitignore"

REM Seed the canonical installer copies in backend\ BEFORE dropping the root ones.
REM reset_win.bat and reset.command rebuild the clean-install root from exactly
REM these copies, so skipping this step means a reset can never restore the
REM installers -- they would be gone for good.
if exist "%PINE_ROOT_DIR%MAC_Install.command" if not exist "%BACKEND_DIR%MAC_Install.command" (
    copy /y "%PINE_ROOT_DIR%MAC_Install.command" "%BACKEND_DIR%MAC_Install.command" >nul 2>nul
)
if exist "%PINE_ROOT_DIR%WIN_Install.bat" if not exist "%BACKEND_DIR%WIN_Install.bat" (
    copy /y "%PINE_ROOT_DIR%WIN_Install.bat" "%BACKEND_DIR%WIN_Install.bat" >nul 2>nul
)

REM Drop the macOS installer from the root -- only once its backend\ copy exists.
if exist "%BACKEND_DIR%MAC_Install.command" if exist "%PINE_ROOT_DIR%MAC_Install.command" (
    del /f /q "%PINE_ROOT_DIR%MAC_Install.command" >nul 2>nul
)

REM Drop the Windows installer from the root. If this running script *is* the root
REM copy, deleting it now would kill this cmd mid-run (batch files are read
REM lazily, not locked), so only flag it here; the main flow removes it as the
REM window's final act, after the browser is open.
set "ROOT_WIN_INSTALL=%PINE_ROOT_DIR%WIN_Install.bat"
if not exist "%BACKEND_DIR%WIN_Install.bat" goto :eof
if exist "%ROOT_WIN_INSTALL%" (
    if /I "%~f0"=="%ROOT_WIN_INSTALL%" (
        set "PINE_CLEANUP_ROOT_INSTALLER=%ROOT_WIN_INSTALL%"
    ) else (
        del /f /q "%ROOT_WIN_INSTALL%" >nul 2>nul
    )
)
goto :eof

:relocate_root_file
REM %1 = file name in PINE_ROOT_DIR, %2 = destination full path.
REM Moves the file if the destination is free; otherwise just drops the root copy.
set "RELO_SRC=%PINE_ROOT_DIR%%~1"
set "RELO_DEST=%~2"
if not exist "%RELO_SRC%" goto :eof
if exist "%RELO_DEST%" (
    del /f /q "%RELO_SRC%" >nul 2>nul
) else (
    move /y "%RELO_SRC%" "%RELO_DEST%" >nul 2>nul
)
goto :eof
