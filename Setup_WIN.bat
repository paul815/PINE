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
REM The hidden instance logs to its own output, which already is its per-run
REM log. launcher.log is the parent's, held open around each of its PowerShell
REM calls, and a hidden instance landing in one of those windows lost the line
REM and printed "The process cannot access the file..." in its place.
if /I "%PINE_HIDDEN_LAUNCH%"=="1" (
    echo [LAUNCHER] [%date% %time%] Hidden instance started from "%~f0"
) else (
    >>"%LAUNCHER_LOG%" echo [LAUNCHER] [%date% %time%] Starting launcher from "%~f0"
)

REM Before judging the venv, let an in-app reset finish deleting it.
call :wait_for_reset_cleanup

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

    echo.
    echo   First setup step complete!
    echo.

    REM Store the installers where a reset can find them, and stop there. The
    REM root is rearranged only when the user presses Launch PINE.
    call :seed_backend_installers
)

REM --- Launch via Python launcher ----------------------------------------
call "%VENV_DIR%\Scripts\activate.bat"
cd /d "%BACKEND_DIR%"

REM This console window exists only to show the install. Hand PINE off to a hidden
REM background instance, wait until the browser is up, then let this window close.
if /I "%PINE_HIDDEN_LAUNCH%"=="1" goto :hidden_main
call :relaunch_hidden
call :wait_for_background_launch_and_open
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

:relaunch_hidden
echo   Starting PINE in the background...
REM Prefer the canonical backend\ copy: the root one is the way back into an
REM install that never finished, so leave it free rather than run from it.
set "PINE_HIDDEN_TARGET=%~f0"
if exist "%BACKEND_DIR%Setup_WIN.bat" set "PINE_HIDDEN_TARGET=%BACKEND_DIR%Setup_WIN.bat"
REM Every launch gets its own helper and its own log. They used to be one shared
REM launcher-hidden.log, and cmd opens a >> target without write sharing: while
REM an earlier hidden instance -- or the supervisor it started, which inherits
REM the handle -- still had it open, the next instance failed on its own
REM redirect before running a single line. Hidden, so the error went nowhere,
REM and this window could only report that PINE never came up.
set "PINE_RUN_ID=%RANDOM%%RANDOM%"
set "PINE_HIDDEN_CMD=%LOG_DIR%\hidden-launch-%PINE_RUN_ID%.cmd"
set "PINE_HIDDEN_LOG=%LOG_DIR%\launcher-hidden-%PINE_RUN_ID%.log"
REM Created here rather than by the redirect: it then exists even when the hidden
REM instance never starts, and its creation time marks where this launch begins,
REM which is what :show_backend_failure measures "this run's logs" against.
type nul > "%PINE_HIDDEN_LOG%"
REM Route the command through a helper .cmd instead of passing it inline, so the
REM argument handed to cmd is a single path with no embedded quoting to unpick.
> "%PINE_HIDDEN_CMD%" echo @echo off
>>"%PINE_HIDDEN_CMD%" echo set "PINE_HIDDEN_LAUNCH=1"
REM Its own file, not launcher.log: the outer redirect below holds that handle
REM open for the whole run, so the [LAUNCHER] lines the script appends to
REM launcher.log directly would collide with it and be dropped.
>>"%PINE_HIDDEN_CMD%" echo call "%PINE_HIDDEN_TARGET%" ^>^>"%PINE_HIDDEN_LOG%" 2^>^&1
set "PINE_HIDDEN_LAUNCHER=%PINE_HIDDEN_CMD%"
REM The [char]34 wrapping is load-bearing: Start-Process joins ArgumentList with
REM spaces and quotes nothing, so an install path like "F:\AI Stuff\..." reaches
REM cmd unquoted and dies on "'F:\AI' is not recognized". The window is hidden,
REM so that error goes nowhere and the caller just polls a dead port instead.
REM After the launch, the same call clears what earlier launches left: their
REM one-shot helpers, and all but the newest five logs. Anything a live instance
REM still holds open simply fails to delete and stays.
powershell -NoProfile -Command "$q=[char]34; Start-Process -WindowStyle Hidden -WorkingDirectory $env:PINE_ROOT_DIR -FilePath $env:ComSpec -ArgumentList '/c', ($q + $env:PINE_HIDDEN_LAUNCHER + $q); Get-ChildItem -LiteralPath $env:LOG_DIR -Filter 'hidden-launch*.cmd' -File -ErrorAction SilentlyContinue | Where-Object { $_.FullName -ne $env:PINE_HIDDEN_CMD } | Remove-Item -Force -ErrorAction SilentlyContinue; Get-ChildItem -LiteralPath $env:LOG_DIR -Filter 'launcher-hidden*.log' -File -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -Skip 5 | Remove-Item -Force -ErrorAction SilentlyContinue" >>"%LAUNCHER_LOG%" 2>&1
goto :eof

:wait_for_background_launch_and_open
echo.
echo   Waiting for PINE to become ready...
echo   This window closes by itself once PINE opens in your browser.
echo   Details: logs\launcher-hidden-%PINE_RUN_ID%.log
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
    echo   If that page does not load, see logs\launcher-hidden-!PINE_RUN_ID!.log.
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
REM output, which is where a failure before Python starts shows up. Only logs
REM from this launch count. The newest backend log on disk can belong to an
REM earlier run, and printing its error as this one's points the diagnosis the
REM wrong way -- a stale "failed to locate pyvenv.cfg" once stood in for a
REM hidden instance that had never started at all.
powershell -NoProfile -ExecutionPolicy Bypass -Command "$d = $env:PINE_LOG_ROOT; $h = $null; if ($env:PINE_HIDDEN_LOG) { $h = Get-Item -LiteralPath $env:PINE_HIDDEN_LOG -ErrorAction SilentlyContinue }; $since = Get-Date; if ($h) { $since = $h.CreationTime }; $f = Get-ChildItem -Path $d -Recurse -Filter 'backend-*.log' -ErrorAction SilentlyContinue | Where-Object { $_.CreationTime -ge $since -and $_.Length -gt 0 } | Sort-Object LastWriteTime -Descending | Select-Object -First 1; if (-not $f -and $h -and $h.Length -gt 0) { $f = $h }; if ($f) { Write-Host ''; Write-Host ('  Last lines of ' + $f.Name + ':'); Write-Host ''; Get-Content $f.FullName -Tail 20 | ForEach-Object { Write-Host ('    ' + $_) } } else { Write-Host ''; Write-Host '  No log was written -- PINE never got far enough to start.' }"
echo.
echo   Full logs: %LOG_DIR%
goto :eof

:open_browser
set "PINE_URL=%~1"
REM The visible parent window owns opening the browser. Without this guard the
REM hidden instance opens a second tab for the same URL.
if /I "%PINE_HIDDEN_LAUNCH%"=="1" (
    echo [LAUNCHER] [%date% %time%] Hidden instance: leaving browser open to parent.
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
if not errorlevel 1 goto waitreadyopen
REM Give up once the supervisor is gone, the way the parent's wait does. Without
REM this, an instance whose backend had already died polled out the whole budget
REM -- six minutes, since a refused curl on Windows spends its full 2s -- and
REM kept everything it had open locked for all of it. Same startup grace as the
REM parent: the supervisor binds a second or two after writing its port file.
if !WAIT_READY_COUNT! LSS 10 goto waitreadytick
curl -sf --max-time 2 http://127.0.0.1:!PINE_SUP_PORT!/status >nul 2>nul
if errorlevel 1 (
    echo   The supervisor exited before the backend became ready.
    goto :eof
)
:waitreadytick
set /a WAIT_READY_COUNT+=1
goto waitreadyloop
:waitreadyopen
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

:wait_for_reset_cleanup
REM An in-app reset finishes after PINE has closed: the interpreter holds the
REM venv, so the app hands it to tools\reset_post_cleanup.cmd, which keeps
REM deleting for a while after the window says PINE is gone, and removes its
REM targets list when done. Launch inside that window and the venv still looks
REM installed -- activate.bat and python.exe are there -- so setup is skipped,
REM the supervisor starts, and the helper then deletes pyvenv.cfg out from under
REM it: the backend dies on "failed to locate pyvenv.cfg" and nothing reinstalls.
set "PINE_RESET_TARGETS=%BACKEND_DIR%tools\reset_cleanup_targets.txt"
set "PINE_RESET_HELPER=%BACKEND_DIR%tools\reset_post_cleanup.cmd"
if not exist "%PINE_RESET_TARGETS%" goto :eof
REM A list with no helper working through it was left by one that was killed;
REM waiting on it would stall every launch for nothing.
powershell -NoProfile -Command "$h = $env:PINE_RESET_HELPER; $p = Get-CimInstance Win32_Process -Filter \"Name='cmd.exe'\" -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -and $_.CommandLine.IndexOf($h, [StringComparison]::OrdinalIgnoreCase) -ge 0 }; if ($p) { exit 0 } else { exit 1 }" >nul 2>nul
if errorlevel 1 goto :drop_reset_targets
echo.
echo   Finishing the reset PINE started before it closed...
>>"%LAUNCHER_LOG%" echo [LAUNCHER] [%date% %time%] Waiting for the post-reset cleanup to finish.
set /a PINE_RESET_WAIT=0
:wait_for_reset_cleanup_loop
if not exist "%PINE_RESET_TARGETS%" goto :eof
if !PINE_RESET_WAIT! GEQ 180 goto :drop_reset_targets
ping -n 2 127.0.0.1 >nul 2>&1
set /a PINE_RESET_WAIT+=1
goto :wait_for_reset_cleanup_loop
:drop_reset_targets
REM The helper rereads the list on every pass, so removing it is also what stops
REM one that is stuck, before it can reach the venv this run is about to build.
>>"%LAUNCHER_LOG%" echo [LAUNCHER] [%date% %time%] Dropping a stale post-reset cleanup list.
del /f /q "%PINE_RESET_TARGETS%" >nul 2>nul
goto :eof

:seed_backend_installers
REM Store the canonical installer copies in backend\ after a fresh install.
REM reset_win.bat and reset.command rebuild the clean-install root from exactly
REM these copies, so skipping this step means a reset can never restore the
REM installers -- they would be gone for good.
REM
REM Nothing else happens to the root here. Its installers, dev files and
REM shortcut are finalize_root_layout_after_onboarding's business, and that
REM runs when the user presses Launch PINE: until the install is known to have
REM succeeded, this installer is the only way back into it.
if exist "%PINE_ROOT_DIR%Setup_MAC.command" if not exist "%BACKEND_DIR%Setup_MAC.command" (
    copy /y "%PINE_ROOT_DIR%Setup_MAC.command" "%BACKEND_DIR%Setup_MAC.command" >nul 2>nul
)
if exist "%PINE_ROOT_DIR%Setup_WIN.bat" if not exist "%BACKEND_DIR%Setup_WIN.bat" (
    copy /y "%PINE_ROOT_DIR%Setup_WIN.bat" "%BACKEND_DIR%Setup_WIN.bat" >nul 2>nul
)
goto :eof
