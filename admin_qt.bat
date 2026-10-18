@echo off
chcp 65001 >nul
title Babel Plan - Admin Console (Qt)
cd /d "%~dp0"

rem ============================================================
rem  Native desktop admin console (PyQt5).
rem  Needs an interpreter with PyQt5. Prefer the DSH runtime
rem  (has PyQt5 AND the nvidia CUDA DLLs for the engine probe);
rem  fall back to system Python 3.12 (has PyQt5 too).
rem  Keep this file ASCII-only.
rem ============================================================

set "PY="
set "DSHPY=%USERPROFILE%\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe"
rem find_spec probe: presence check only, milliseconds (importing PyQt5
rem per candidate used to cost seconds before the window even appeared)
if exist "%DSHPY%" (
  "%DSHPY%" -c "import importlib.util as u,sys;sys.exit(0 if u.find_spec('PyQt5') else 1)" >nul 2>nul && set "PY=%DSHPY%"
)
if not defined PY (
  set "SYSPY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
  if exist "%SYSPY%" (
    "%SYSPY%" -c "import importlib.util as u,sys;sys.exit(0 if u.find_spec('PyQt5') else 1)" >nul 2>nul && set "PY=%SYSPY%"
  )
)
if not defined PY (
  where python >nul 2>nul && set "PY=python"
)
if not defined PY set "PY=py"

set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1

echo Starting the Qt admin console...
echo   python: %PY%
echo.
"%PY%" -u admin_qt.py %*

echo.
echo (admin console exited - %ERRORLEVEL%)
pause
