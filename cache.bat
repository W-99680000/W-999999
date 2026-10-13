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

if not exist "%CAND1%" goto findpy
"%CAND1%" -c "import faster_whisper" >nul 2>nul
if errorlevel 1 goto findpy
set "PY=%CAND1%"
goto pyset

:findpy
for /f "delims=" %%P in ('where python 2^>nul') do (
  if not defined PY (
    "%%P" -c "import faster_whisper" >nul 2>nul
    if not errorlevel 1 set "PY=%%P"
  )
)

:pyset
if not defined PY goto nopy

set HF_HUB_DISABLE_SYMLINKS_WARNING=1
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
