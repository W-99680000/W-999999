@echo off
chcp 65001 >nul
title live-subtitle
cd /d "%~dp0"

rem ── 找可用的 python ──────────────────────────────────────────────
rem 必须装了 faster_whisper / PyQt5 / pyaudiowpatch 的那个才可用。
rem 只写 python 是不行的：PATH 里的 python 可能没装这些依赖
rem （我改过一版只用 where python，结果启动就 ModuleNotFoundError）。
rem
rem 优先级：本机已知可用的解释器 → PATH 里的 python → py 启动器
rem
rem 注意本文件保持纯 ASCII：cmd.exe 按系统 ANSI 读 .bat，
rem 中文会变乱码并拆断命令。

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
  echo ============================================================
  echo   ERROR: no python with the required packages was found.
  echo ============================================================
  echo.
  echo   Need: faster_whisper  PyQt5  pyaudiowpatch
  echo   Install them into the python you want to use:
  echo.
  echo       pip install -r requirements.txt
  echo.
  echo   If the packages are in a different interpreter, set it here:
  echo       set PY=C:\path\to\python.exe
  echo.
  pause
  exit /b 1
)

set HF_HUB_DISABLE_SYMLINKS_WARNING=1
set PYTHONIOENCODING=utf-8

echo ============================================================
echo   live-subtitle - realtime translated subtitles
echo ============================================================
echo   python: %PY%
echo.
echo   Play your video. Subtitles appear at the bottom.
echo.
echo   OVERLAY CONTROLS
echo     drag the subtitles  move  (drag anywhere on the text block)
echo     wheel ............. font size
echo     Shift + wheel ..... number of lines
echo     Alt + wheel ....... width (how early the text wraps)
echo     Ctrl + wheel ...... opacity
echo     arrow keys ........ nudge
echo     L ................. layout (same line / two lines / zh only)
echo     J ................. text align (center / left / right)
echo     M ................. edit mode (shows border + grip for fine tuning)
echo     T ................. theme (dark / light video)
echo     H ................. hide / show
echo     C ................. recenter
echo     R ................. reset all
echo     Esc ............... quit
echo.
echo   No frame by default - just text over the video, like mpv or
echo   YouTube captions. Press M if you want to see the box outline.
echo.
echo   Also writes live.srt - load it in your player if the
echo   overlay does not show up.
echo   If something goes wrong, see run.log
echo ============================================================
echo.

"%PY%" -u live_subtitle_v2.py %*

echo.
echo (subtitle stopped - exit %ERRORLEVEL%)
pause
