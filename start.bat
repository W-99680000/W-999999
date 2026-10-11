@echo off
chcp 65001 >nul
title live-subtitle
cd /d "%~dp0"

rem Use python from PATH; fall back to the py launcher.
rem NOTE: keep this file ASCII-only. cmd.exe reads .bat in the system
rem ANSI codepage, so non-ASCII text gets mangled and breaks commands.
where python >nul 2>nul && (set PY=python) || (set PY=py)
set HF_HUB_DISABLE_SYMLINKS_WARNING=1
set PYTHONIOENCODING=utf-8

echo ============================================================
echo   live-subtitle - realtime translated subtitles
echo ============================================================
echo.
echo   Play your video. Subtitles appear at the bottom.
echo.
echo   OVERLAY CONTROLS
echo     drag mouse ........ move
echo     wheel ............. font size
echo     Shift + wheel ..... number of lines
echo     Ctrl + wheel ...... opacity
echo     T ................. theme (dark / light video)
echo     H ................. hide / show
echo     R ................. reset
echo     Esc ............... quit
echo.
echo   Also writes live.srt - load it in your player if the
echo   overlay does not show up.
echo   If something goes wrong, see run.log
echo ============================================================
echo.

%PY% -u live_subtitle_v2.py %*

echo.
echo (subtitle stopped - exit %ERRORLEVEL%)
pause
