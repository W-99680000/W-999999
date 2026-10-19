@echo off
chcp 65001 >nul
title Babel Plan - Desktop App
cd /d "%~dp0"

rem ============================================================
rem  Single-entry desktop shell (pywebview + embedded web admin).
rem  Needs: pywebview pystray pillow in the chosen interpreter.
rem  Engine interpreter is picked separately by babel_admin/dlls.py
rem  (CUDA DLLs etc.) -- this shell only runs the window + service.
rem  Keep this file ASCII-only.
rem ============================================================

set "PY="
set "SYSPY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if exist "%SYSPY%" (
  "%SYSPY%" -c "import webview" >nul 2>nul && set "PY=%SYSPY%"
)
if not defined PY (
  set "DSHPY=%USERPROFILE%\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe"
  if exist "%DSHPY%" (
    "%DSHPY%" -c "import webview" >nul 2>nul && set "PY=%DSHPY%"
  )
)
if not defined PY (
  where python >nul 2>nul && python -c "import webview" >nul 2>nul && set "PY=python"
)
if not defined PY (
  echo [ERROR] No interpreter with pywebview found.
  echo   Run:  "%SYSPY%" -m pip install pywebview pystray pillow
  pause
  exit /b 1
)

set PYTHONIOENCODING=utf-8
rem ── 模型统一落在 D 盘（HF_HOME / 缓存），不要写 C 盘 ──
if not defined HF_HOME set "HF_HOME=%~dp0models\hf_home"
if not defined HUGGINGFACE_HUB_CACHE set "HUGGINGFACE_HUB_CACHE=%HF_HOME%\hub"
if not defined HF_HUB_CACHE set "HF_HUB_CACHE=%HF_HOME%\hub"
if not defined MODELSCOPE_CACHE set "MODELSCOPE_CACHE=%~dp0models\modelscope"
if not defined TORCH_HOME set "TORCH_HOME=%~dp0models\torch_home"
set PYTHONUTF8=1

echo Starting Babel Plan desktop app...
echo   python: %PY%
echo.
"%PY%" -u app.py %*

echo.
echo (app exited - %ERRORLEVEL%)
pause
