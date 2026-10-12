@echo off
chcp 65001 >nul
title live-subtitle
cd /d "%~dp0"

rem (note: keep this file ASCII-only)
rem (note: keep this file ASCII-only)
rem (note: keep this file ASCII-only)
rem (note: keep this file ASCII-only)
rem
rem (note: keep this file ASCII-only)
rem
rem (note: keep this file ASCII-only)
rem (note: keep this file ASCII-only)

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
rem Models are already cached locally; skip hub network checks so a
rem broken proxy cannot kill startup (observed: 502 -> GPU misdetected
rem -> CPU fallback -> download attempt -> crash). Remove this line if
rem you need to download a new model (or use get_models.py).
set HF_HUB_OFFLINE=1

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
echo   Recognition: cloud qwen3-asr-flash when a key exists (~/.dsh/.dashscope),
echo                otherwise local whisper. Disable with --no-remote-asr.
echo.
echo   No frame by default - just text over the video, like mpv or
echo   YouTube captions. Press M if you want to see the box outline.
echo.
echo   Also writes live.srt - load it in your player if the
echo   overlay does not show up.
echo   If something goes wrong, see run.log
echo ============================================================
echo.

rem -- local translation (offline HY-MT via llama.cpp) ------------
rem Default model: medium 1.8B (best speed/quality balance).
rem For better quality (7B, slower first load), run:
rem   set HYMT_MODEL=F:\hh_si_pro\hh-translate-model\hh-translate-large.gguf
rem   start.bat
if not defined HYMT_MODEL set "HYMT_MODEL=F:\hh_si_pro\hh-translate-model\hh-translate-medium.gguf"

echo   Translation: local HY-MT (offline) - %HYMT_MODEL%
echo.

"%PY%" -u live_subtitle_v2.py --mt-local --mt-local-model "%HYMT_MODEL%" %*

echo.
echo (subtitle stopped - exit %ERRORLEVEL%)
pause
