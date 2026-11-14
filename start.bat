@echo off
rem Starts Concierge on Windows (the first start sets up the Python environment).
cd /d "%~dp0"
set VENV=.venv-windows

rem Find Python: first "python", otherwise the Python launcher "py".
set PYTHON=
python --version >nul 2>&1 && set PYTHON=python
if not defined PYTHON (
    py -3 --version >nul 2>&1 && set PYTHON=py -3
)

rem An environment copied from another PC (e.g. on a USB stick) points to that PC's Python and
rem does not work here. Then it is created again.
if exist "%VENV%\Scripts\python.exe" (
    "%VENV%\Scripts\python.exe" -c "import sys" >nul 2>&1
    if errorlevel 1 (
        echo The Python environment comes from another PC and is set up again ...
        rmdir /s /q "%VENV%"
    )
)

if not exist "%VENV%\Scripts\python.exe" (
    if not defined PYTHON (
        echo.
        echo Python was not found. Please install Python 3.11 or newer from https://www.python.org
        echo and tick "Add python.exe to PATH". Then run start.bat again.
        pause
        exit /b 1
    )
    echo First start: setting up the Python environment ...
    %PYTHON% -m venv "%VENV%"
    if errorlevel 1 (
        echo The Python environment could not be created.
        pause
        exit /b 1
    )
    del "%VENV%\requirements.installed" >nul 2>&1
)

fc /b requirements.txt "%VENV%\requirements.installed" >nul 2>&1
if errorlevel 1 (
    echo Installing dependencies ^(takes one or two minutes the first time^) ...
    "%VENV%\Scripts\python.exe" -m pip install --upgrade pip >nul
    "%VENV%\Scripts\python.exe" -m pip install -r requirements.txt
    if errorlevel 1 (
        echo Installation failed. Is there an internet connection?
        pause
        exit /b 1
    )
    copy /y requirements.txt "%VENV%\requirements.installed" >nul
)

rem Last check before the app starts without a console window (errors would not be visible there).
"%VENV%\Scripts\python.exe" -c "import nicegui, imap_tools, icalendar, keyring, cryptography" >nul 2>&1
if errorlevel 1 (
    echo The Python environment is incomplete. Please delete the folder %VENV% and run start.bat again.
    pause
    exit /b 1
)

start "" "%VENV%\Scripts\pythonw.exe" -m app.main
