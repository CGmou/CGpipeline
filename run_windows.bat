@echo off
setlocal
cd /d "%~dp0"

:: Check if python is in path, else try common locations
where python >nul 2>1
if %ERRORLEVEL% NEQ 0 (
    echo Python not found in PATH. Checking common locations...
    if exist "C:\Python311\python.exe" set PYTHON_EXE="C:\Python311\python.exe"
    if exist "C:\Python310\python.exe" set PYTHON_EXE="C:\Python310\python.exe"
    if exist "%LocalAppData%\Programs\Python\Python311\python.exe" set PYTHON_EXE="%LocalAppData%\Programs\Python\Python311\python.exe"
    if exist "%LocalAppData%\Programs\Python\Python310\python.exe" set PYTHON_EXE="%LocalAppData%\Programs\Python\Python310\python.exe"
) else (
    set PYTHON_EXE=python
)

if not defined PYTHON_EXE (
    echo ERROR: Python not found. Please install Python 3.10+ and add to PATH.
    pause
    exit /b 1
)

echo Starting CGPipeline on Windows...
start "" %PYTHON_EXE% main.py
endlocal
