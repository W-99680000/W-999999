@echo off
chcp 65001 >nul
title live-subtitle cache
cd /d "%~dp0"

rem ============================================================
rem  cache.bat - pre-translate a video into a bilingual .srt
rem  (note: keep this file ASCII-only)
rem
rem    cache.bat movie.mp4
rem    cache.bat https://www.bilibili.com/video/BVxxx/
rem    cache.bat movie.mp4 --force      redo, ignore cache
rem ============================================================

set "PY="
set "CAND1=C:\Users\w\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe"
set "CAND2=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"

rem Probe needs faster_whisper (ASR), av (PyAV decode), yt_dlp (URL) and
rem numpy. Checking only "import faster_whisper" picked a python without
rem av/yt_dlp -> every auto pre-translate failed with a bare "source: url"
rem line (measured 2026-10-18).
set "PROBE=import importlib.util as u,sys;sys.exit(0 if all(u.find_spec(m) for m in ('faster_whisper','av','yt_dlp','numpy')) else 1)"

if not exist "%CAND1%" goto findpy
"%CAND1%" -c "%PROBE%" >nul 2>nul
if errorlevel 1 goto findpy
set "PY=%CAND1%"
goto pyset

:findpy
if not exist "%CAND2%" goto findpy2
"%CAND2%" -c "%PROBE%" >nul 2>nul
if errorlevel 1 goto findpy2
set "PY=%CAND2%"
goto pyset

:findpy2
for /f "delims=" %%P in ('where python 2^>nul') do (
  if not defined PY (
    "%%P" -c "%PROBE%" >nul 2>nul
    if not errorlevel 1 set "PY=%%P"
  )
)

:pyset
if not defined PY goto nopy

set HF_HUB_DISABLE_SYMLINKS_WARNING=1
rem ── 模型统一落在 D 盘（HF_HOME / 缓存），不要写 C 盘 ──
if not defined HF_HOME set "HF_HOME=%~dp0models\hf_home"
if not defined HUGGINGFACE_HUB_CACHE set "HUGGINGFACE_HUB_CACHE=%HF_HOME%\hub"
if not defined HF_HUB_CACHE set "HF_HUB_CACHE=%HF_HOME%\hub"
if not defined MODELSCOPE_CACHE set "MODELSCOPE_CACHE=%~dp0models\modelscope"
if not defined TORCH_HOME set "TORCH_HOME=%~dp0models\torch_home"
set PYTHONIOENCODING=utf-8
set HF_HUB_OFFLINE=1

if "%~1"=="" goto usage

echo pre-translating ...
echo.
"%PY%" -u pretranslate.py %*

echo.
echo cache done - exit %ERRORLEVEL%
pause
exit /b 0

:usage
echo ============================================================
echo   live-subtitle cache - pre-translate a video to .srt
echo ============================================================
echo.
echo   Usage:
echo     cache.bat movie.mp4
echo     cache.bat https://www.bilibili.com/video/BVxxx/
echo     cache.bat movie.mp4 --force
echo.
echo   Output:  video.zh.srt  - zh line + original line
echo   mpv / PotPlayer pick it up automatically.
echo   The file also lands in pre_cache, so watch.bat --auto
echo   can pick it up later without redoing anything.
echo ============================================================
echo.
pause
exit /b 0

:nopy
echo ============================================================
echo   ERROR: no python with faster_whisper was found.
echo ============================================================
echo   Install: pip install -r requirements.txt
echo   Or set PY to your interpreter path in this file.
echo ============================================================
pause
exit /b 1
