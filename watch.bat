@echo off
chcp 65001 >nul
title live-subtitle watch
cd /d "%~dp0"

rem ============================================================
rem  watch.bat - pre-translated subtitles for VOD / online video
rem  (note: keep this file ASCII-only)
rem
rem    watch.bat movie.mp4
rem    watch.bat https://www.bilibili.com/video/BVxxx/
rem    watch.bat --auto        resident mode - auto pre-translate
rem ============================================================

set "PY="
set "CAND1=C:\Users\w\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe"
set "CAND2=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"

rem Interpreter probe: find_spec (milliseconds) instead of importing
rem faster_whisper+PyQt5 (seconds per candidate, worse cold).
rem Probe MUST include watch-only deps: winsdk (SMTC position), yt_dlp
rem (URL resolve/download), av (audio decode), numpy. A python that only
rem has faster_whisper+PyQt5 runs but silently loses realtime sync and
rem every auto pre-translate fails (measured 2026-10-18).
set "PROBE=import importlib.util as u,sys;sys.exit(0 if all(u.find_spec(m) for m in ('faster_whisper','PyQt5','winsdk','yt_dlp','av','numpy')) else 1)"

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
set PYTHONIOENCODING=utf-8
set HF_HUB_OFFLINE=1

if /i "%~1"=="help" goto usage

rem double-click with no args -> resident mode (any video, auto)
rem only watch options (leading "-") -> resident mode as well, e.g.
rem   watch.bat --no-pretranslate
set "ARGS="
set "A1=%~1"
if "%A1%"=="" set "ARGS=--auto"
if not "%A1%"=="" if "%A1:~0,1%"=="-" set "ARGS=--auto"

echo launching watch mode ...
echo.
"%PY%" -u watch_player.py %ARGS% %*

echo.
echo watch stopped - exit %ERRORLEVEL%
pause
exit /b 0

:usage
echo ============================================================
echo   live-subtitle watch - pre-translated subtitles for VOD
echo ============================================================
echo.
echo   Double-click (no args) = resident mode:
echo     leave the window open, then just watch anything.
echo     If a cached srt exists it is used instantly; if not, the
echo     video URL is looked up in browser history and the whole
echo     video is pre-translated in the background, then subtitles
echo     appear and follow the playback automatically.
echo.
echo   Command line:
echo     watch.bat movie.mp4
echo     watch.bat https://www.bilibili.com/video/BVxxx/
echo     watch.bat --auto      resident mode, see notes below
echo     watch.bat --no-pretranslate    cached-only, no auto translate
echo     watch.bat help        this text
echo.
echo   First run transcribes + translates the whole video
echo   using large-v3 with full context - far better than realtime.
echo   The result is cached in pre_cache - second run is instant.
echo   Live streams are skipped on purpose: there is no audio to
echo   pre-fetch, use start.bat for those.
echo.
echo   Position source, chosen automatically:
echo     mpv found ....... mpv IPC, follows pause and seek
echo     otherwise ....... Windows SMTC - browser playback is
echo                       tracked automatically, nothing to press
echo     fallback ........ manual clock - click overlay, press
echo                       Space to align, arrows to fine tune
echo ============================================================
echo.
pause
exit /b 0

:nopy
echo ============================================================
echo   ERROR: no python with faster_whisper + PyQt5 was found.
echo ============================================================
echo   Install: pip install -r requirements.txt
echo   Or set PY to your interpreter path in this file.
echo ============================================================
pause
exit /b 1
