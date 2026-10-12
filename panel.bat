@echo off
chcp 65001 >nul
title live-subtitle (with control panel)
cd /d "%~dp0"

rem Launch the subtitles AND the control panel.
rem The panel is a separate process: closing it does not stop the subtitles.
rem Keep this file ASCII-only (cmd.exe reads .bat in the system ANSI codepage).

where python >nul 2>nul && (set PY=python) || (set PY=py)
set "CAND1=C:\Users\w\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe"
if exist "%CAND1%" (
  "%CAND1%" -c "import faster_whisper,PyQt5,pyaudiowpatch" >nul 2>nul
  if not errorlevel 1 set "PY=%CAND1%"
)

set HF_HUB_DISABLE_SYMLINKS_WARNING=1
set PYTHONIOENCODING=utf-8
rem Same as start.bat: models are cached locally; a broken proxy (observed
rem 502 Bad Gateway) must not kill startup via a hub connectivity check.
set HF_HUB_OFFLINE=1

echo Starting the control panel (separate window)...
start "live-subtitle panel" "%PY%" control_panel.py

echo Starting the subtitles...
echo.
%PY% -u live_subtitle_v2.py %*

echo.
echo (subtitle stopped - exit %ERRORLEVEL%)
pause
