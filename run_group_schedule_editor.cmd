@echo off
cd /d "%~dp0"
python -m cal_engine.group_schedule_gui
if errorlevel 1 pause
