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
rem Models are cached on disk. A broken proxy (seen as
rem 502 Bad Gateway) must not stall model loading.
set HF_HUB_OFFLINE=1

echo Starting the control panel (separate window)...
start "live-subtitle panel" "%PY%" control_panel.py

echo Starting the subtitles...
echo.
%PY% -u live_subtitle_v2.py %*

echo.
echo (subtitle stopped - exit %ERRORLEVEL%)
pause
