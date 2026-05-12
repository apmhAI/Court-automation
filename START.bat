@echo off
title Court Cause List — Launcher
color 0A
echo.
echo  =====================================================
echo   Court Cause List Automation
echo   Starting local server and opening UI...
echo  =====================================================
echo.

cd /d "%~dp0"

python --version >nul 2>&1
if errorlevel 1 (
    echo  [ERROR] Python not found in PATH.
    echo  Please install Python from https://python.org
    echo  and make sure "Add Python to PATH" is checked.
    pause
    exit /b 1
)

echo  Starting server.py ...
echo  The UI will open in your browser automatically.
echo  Keep this window open while using the UI.
echo  Close it when you are done.
echo.

python server.py

pause
