@echo off
setlocal
cd /d "%~dp0"

call "%~dp0setup_calendar.cmd"
if errorlevel 1 (
    echo.
    echo Calendar setup failed. Please review the message above.
    pause
    exit /b 1
)

if /I "%~1"=="gui" goto calendar_gui
if /I "%~1"=="group" goto group_editor

:menu
title Calendar Tools
echo.
echo ==============================
echo        Calendar Tools
echo ==============================
echo 1. Open Calendar GUI
echo 2. Run CLI Command (Normal)
echo 3. Run CLI Command (Debug Mode)
echo 4. Show CLI help
echo 5. Open Timetable Editor (use this)
echo 0. Exit
echo.
set /p "choice=Choose an option: "

if "%choice%"=="1" goto calendar_gui
if "%choice%"=="2" goto cmd_normal
if "%choice%"=="3" goto cmd_debug
if "%choice%"=="4" goto cli_help
if "%choice%"=="5" goto group_editor
if "%choice%"=="0" goto end
echo Invalid choice.
goto menu

:cmd_normal
echo.
echo Enter CLI command (e.g., --schedule --from today):
set /p "user_cmd= > "
echo.
python main.py %user_cmd%
echo.
pause
goto menu

:cmd_debug
echo.
echo Enter CLI command in DEBUG mode (e.g., --schedule --from today):
set /p "user_cmd= > "
echo.
python main.py --debug %user_cmd%
echo.
pause
goto menu

:calendar_gui
python main.py gui
if errorlevel 1 (
    echo.
    echo Calendar GUI failed to start.
    echo Check that Python and Tkinter are installed.
    pause
)
goto end

:group_editor
python main.py group-schedule
if errorlevel 1 (
    echo.
    echo Group Schedule Editor failed to start.
    pause
)
goto end

:cli_help
python main.py --help
pause
goto menu

:end
endlocal