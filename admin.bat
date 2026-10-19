@echo off
chcp 65001 >nul
title Babel Plan - Admin Console
cd /d "%~dp0"

rem ============================================================
rem  Babel Plan admin console (live-subtitle)
rem  Serves babel_admin/index.html on http://127.0.0.1:8770 AND
rem  starts/stops the native subtitle engine (live_subtitle_v2.py),
rem  which is what draws the always-on-top overlay window.
rem
rem  Keep this file ASCII-only (cmd.exe reads .bat in the system
rem  ANSI codepage; Chinese text here breaks command parsing).
rem ============================================================

rem --- Pick the interpreter that actually has CUDA DLLs -------------
rem Two Python 3.12 installs exist on this machine and only ONE of
rem them has the nvidia cuBLAS/cuDNN packages:
rem   system  : ...\Programs\Python\Python312        -> NO cublas64_12.dll
rem   DSH rt  : ...\.dsh\dsh-runtimes\...\python    -> HAS cublas64_12.dll
rem Picking the wrong one makes gpu_available() fail, the engine
rem falls back to CPU/remote ASR, and the probe stalls for ~35s.
rem serve.py re-checks this itself (dlls.env_for_engine); this is a
rem fast path so the console starts on the right foot.
set "PY="
set "DSHPY=%USERPROFILE%\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe"
if exist "%DSHPY%" (
  "%DSHPY%" -c "import glob,os,sys;sp=os.path.join(sys.prefix,'Lib','site-packages');sys.exit(0 if glob.glob(os.path.join(sp,'nvidia','*','bin','cublas64_12.dll')) else 1)" >nul 2>nul
  if not errorlevel 1 set "PY=%DSHPY%"
)
if not defined PY (
  where python >nul 2>nul && set "PY=python"
)
if not defined PY set "PY=py"

set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
set HF_HUB_DISABLE_SYMLINKS_WARNING=1
rem ── 模型统一落在 D 盘（HF_HOME / 缓存），不要写 C 盘 ──
if not defined HF_HOME set "HF_HOME=%~dp0models\hf_home"
if not defined HUGGINGFACE_HUB_CACHE set "HUGGINGFACE_HUB_CACHE=%HF_HOME%\hub"
if not defined HF_HUB_CACHE set "HF_HUB_CACHE=%HF_HOME%\hub"
if not defined MODELSCOPE_CACHE set "MODELSCOPE_CACHE=%~dp0models\modelscope"
if not defined TORCH_HOME set "TORCH_HOME=%~dp0models\torch_home"
rem Models are cached locally. A broken proxy must not stall model
rem loading or the GPU probe (no-timeout HTTP hangs; seen in the wild).
if not defined HF_HUB_OFFLINE set "HF_HUB_OFFLINE=1"

echo Starting the admin console at http://127.0.0.1:8770 ...
echo   python: %PY%
echo.
%PY% -u babel_admin\serve.py %*

echo.
echo (admin console stopped - exit %ERRORLEVEL%)
pause
