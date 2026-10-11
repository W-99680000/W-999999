@echo off
chcp 65001 >nul
title live-subtitle (streaming)
cd /d "%~dp0"

rem Streaming mode: LocalAgreement-2 policy, lower first-word latency,
rem but subtitles may lag behind on long continuous speech.
rem Keep this file ASCII-only (cmd.exe reads .bat in the system ANSI codepage).

rem ── pick a python that actually has the packages ───────────────
rem Do NOT just use "where python": the python on PATH may lack
rem faster_whisper / PyQt5 / pyaudiowpatch and the app dies at startup
rem with ModuleNotFoundError (that regression already happened once).
set "PY="
set "CAND1=C:\Users\w\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe"

if exist "%CAND1%" (
  "%CAND1%" -c "import faster_whisper,PyQt5,pyaudiowpatch" >nul 2>nul
  if not errorlevel 1 set "PY=%CAND1%"
)

if not defined PY (
  for /f "delims=" %%P in ('where python 2^>nul') do (
    if not defined PY (
      "%%P" -c "import faster_whisper,PyQt5,pyaudiowpatch" >nul 2>nul
      if not errorlevel 1 set "PY=%%P"
    )
  )
)

if not defined PY (
  py -c "import faster_whisper,PyQt5,pyaudiowpatch" >nul 2>nul
  if not errorlevel 1 set "PY=py"
)

if not defined PY (
  echo ERROR: no python with faster_whisper / PyQt5 / pyaudiowpatch found.
  echo Run:  pip install -r requirements.txt
  echo Or:   set PY=C:\path\to\python.exe
  pause
  exit /b 1
)

set HF_HUB_DISABLE_SYMLINKS_WARNING=1
set PYTHONIOENCODING=utf-8

echo Streaming mode: first-word latency ~1-2s,
echo but subtitles may lag behind on long continuous speech.
echo python: %PY%
echo.

"%PY%" -u live_subtitle_v2.py --stream %*

echo.
echo (stopped)
pause
