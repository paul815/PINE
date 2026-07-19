@echo off
setlocal EnableExtensions EnableDelayedExpansion
rem ---------------------------------------------------------------------------
rem PINE post-reset cleanup helper (shipped with the app, not generated).
rem
rem After a full reset PINE shuts itself down so that files locked by the running
rem process - chiefly the virtual environment that hosts the interpreter - can be
rem removed. This helper runs as a short-lived detached process: it waits for the
rem app to release those files, then deletes every path listed in the targets
rem file. Paths are passed in by the app; this script contains no hard-coded
rem locations and does not delete itself.
rem
rem Usage: reset_post_cleanup.cmd <targets_list_file>
rem   <targets_list_file>  UTF-8 text file, one absolute path per line.
rem ---------------------------------------------------------------------------

set "LIST=%~1"
if "%LIST%"=="" exit /b 0
if not exist "%LIST%" exit /b 0

rem Give the app a moment to exit and unlock its files.
timeout /t 2 /nobreak >nul

set /a TRIES=0
:retry
set /a TRIES+=1
set "PENDING=0"
for /f "usebackq delims=" %%P in ("%LIST%") do (
    if exist "%%~P\" rd /s /q "%%~P" 2>nul
    if exist "%%~P" del /f /q "%%~P" 2>nul
    if exist "%%~P" set "PENDING=1"
)
if "!PENDING!"=="1" if !TRIES! LSS 20 (
    timeout /t 1 /nobreak >nul
    goto retry
)

rem Remove the temporary targets list once cleanup is done.
del /f /q "%LIST%" >nul 2>nul
exit /b 0
