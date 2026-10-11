@echo off
chcp 65001 >nul
title live-subtitle (streaming)
cd /d "%~dp0"

rem Streaming mode: LocalAgreement-2 policy, lower first-word latency,
rem but subtitles may lag behind on long continuous speech.
rem Keep this file ASCII-only (cmd.exe reads .bat in the system ANSI codepage).
where python >nul 2>nul && (set PY=python) || (set PY=py)
set HF_HUB_DISABLE_SYMLINKS_WARNING=1
set PYTHONIOENCODING=utf-8

echo Streaming mode: first-word latency ~1-2s,
echo but subtitles may lag behind on long continuous speech.
echo.

%PY% -u live_subtitle_v2.py --stream %*

echo.
echo (stopped)
pause
