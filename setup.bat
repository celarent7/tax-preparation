@echo off
REM Setup script for Tax Preparation Plugin (Windows)

echo Setting up Tax Preparation Plugin environment...

REM Check for Python
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo Error: python could not be found. Please install Python 3.9 or higher.
    exit /b 1
)

REM Create virtual environment
echo Creating virtual environment (.venv)...
python -m venv .venv

REM Activate virtual environment
call .venv\Scripts\activate.bat

REM Install dependencies
echo Installing dependencies from requirements.txt...
python -m pip install --upgrade pip
pip install -r requirements.txt

echo.
echo Setup complete!
echo To activate the environment, run: .venv\Scripts\activate.bat
echo To start the MCP server, run: python scripts\mcp_server.py
