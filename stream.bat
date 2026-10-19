@echo off
chcp 65001 >nul
title live-subtitle (streaming)
cd /d "%~dp0"

rem Streaming mode: LocalAgreement-2 policy, lower first-word latency,
rem but subtitles may lag behind on long continuous speech.
rem Keep this file ASCII-only (cmd.exe reads .bat in the system ANSI codepage).

rem -- pick a python that actually has the packages ---------------
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
rem ── 模型统一落在 D 盘（HF_HOME / 缓存），不要写 C 盘 ──
if not defined HF_HOME set "HF_HOME=%~dp0models\hf_home"
if not defined HUGGINGFACE_HUB_CACHE set "HUGGINGFACE_HUB_CACHE=%HF_HOME%\hub"
if not defined HF_HUB_CACHE set "HF_HUB_CACHE=%HF_HOME%\hub"
if not defined MODELSCOPE_CACHE set "MODELSCOPE_CACHE=%~dp0models\modelscope"
if not defined TORCH_HOME set "TORCH_HOME=%~dp0models\torch_home"
set PYTHONIOENCODING=utf-8
rem Models are cached locally; skip hub network checks (broken proxy 502
rem once caused GPU misdetection and a startup crash). See start.bat.
set HF_HUB_OFFLINE=1

echo Streaming mode: first-word latency ~1-2s,
echo but subtitles may lag behind on long continuous speech.
echo python: %PY%
echo.

rem -- local translation (offline HY-MT via llama.cpp) ------------
rem Default model: medium 1.8B. For 7B: set HYMT_MODEL=F:\hh_si_pro\hh-translate-model\hh-translate-large.gguf
if not defined HYMT_MODEL set "HYMT_MODEL=F:\hh_si_pro\hh-translate-model\hh-translate-medium.gguf"

"%PY%" -u live_subtitle_v2.py --stream --stream-model small --mt-local --mt-local-model "%HYMT_MODEL%" %*

echo.
echo (stopped)
pause
