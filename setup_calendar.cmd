@echo off
setlocal
cd /d "%~dp0"

python --version >nul 2>&1
if errorlevel 1 (
    echo Python 3.10 or later was not found. Install Python and make sure it is available as "python".
    exit /b 1
)

python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
if errorlevel 1 (
    echo Python 3.10 or later is required.
    exit /b 1
)

python -c "import jsonschema" >nul 2>&1
if not errorlevel 1 (
    echo Runtime dependencies are already installed.
    exit /b 0
)

echo Installing missing runtime dependencies...
python -m pip --version >nul 2>&1
if errorlevel 1 (
    python -m ensurepip --upgrade
    if errorlevel 1 (
        echo Could not enable pip. Please install pip for this Python interpreter and try again.
        exit /b 1
    )
)

python -m pip install -r "%~dp0requirements.txt"
if errorlevel 1 (
    echo Dependency installation failed. Check your internet connection and try again.
    exit /b 1
)

python -c "import jsonschema" >nul 2>&1
if errorlevel 1 (
    echo Setup finished, but jsonschema could not be imported.
    exit /b 1
)

echo Setup completed successfully.
exit /b 0
