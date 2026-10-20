@echo off
setlocal
cd /d "%~dp0"

if /I "%~1"=="gui" goto calendar_gui
if /I "%~1"=="group" goto group_editor

:menu
title Calendar Tools
echo.
echo ==============================
echo       Calendar Tools
echo ==============================
echo 1. Open Calendar GUI
echo 2. Open Group Schedule Editor
echo 3. Show CLI help
echo 0. Exit
echo.
set /p "choice=Choose an option: "

if "%choice%"=="1" goto calendar_gui
if "%choice%"=="2" goto group_editor
if "%choice%"=="3" goto cli_help
if "%choice%"=="0" goto end
echo Invalid choice.
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
goto end

:end
endlocal
