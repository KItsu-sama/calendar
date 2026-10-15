@echo off
setlocal
cd /d "%~dp0"

title Calendar GUI
echo Starting Calendar GUI...

python main.py gui

if errorlevel 1 (
    echo.
    echo Calendar GUI failed to start.
    echo Check that Python and Tkinter are installed.
    pause
)

endlocal
