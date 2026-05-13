@echo off
title Court Automation — Watcher
color 0A
echo.
echo  =====================================================
echo   Court Automation — Watcher
echo   Polls GitHub every 60s for run requests
echo   Keep this window open while using the app
echo  =====================================================
echo.

cd /d "%~dp0"

python --version >nul 2>&1
if errorlevel 1 (
    echo  [ERROR] Python not found in PATH.
    pause & exit /b 1
)

pip show requests >nul 2>&1
if errorlevel 1 (
    echo  Installing requests...
    pip install requests
)

echo  Starting watcher.py...
echo  Logs saved to watcher.log in this folder.
echo.

python watcher.py

pause
