
@echo off
setlocal
cd /d "%~dp0"

call "%~dp0setup_calendar.cmd"
if errorlevel 1 (
    echo.
    echo Calendar setup failed.
    pause
    exit /b 1
)

python calendar_launcher.py %*
endlocal
