@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 goto no_python

python -c "import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)" >nul 2>nul
if errorlevel 1 goto bad_python

python app.py %*
if errorlevel 1 pause
exit /b 0

:no_python
echo [ERROR] Python 3.10+ not found.
echo Install from https://www.python.org/downloads/
pause
exit /b 1

:bad_python
echo [ERROR] Python 3.10 or newer is required.
echo Current Python is too old.
pause
exit /b 1
