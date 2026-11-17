@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"

set "FFMPEG_OK="
where ffmpeg >nul 2>&1 && set "FFMPEG_OK=1"
if not defined FFMPEG_OK if exist "%LOCALAPPDATA%\Microsoft\WinGet\Links\ffmpeg.exe" set "FFMPEG_OK=1"
if not defined FFMPEG_OK (
    for /d %%D in ("%LOCALAPPDATA%\Microsoft\WinGet\Packages\Gyan.FFmpeg*") do (
        for /d %%B in ("%%~fD\*") do (
            if exist "%%~fB\bin\ffmpeg.exe" set "FFMPEG_OK=1"
        )
    )
)

rem Fast path: launch immediately when .venv and FFmpeg are already present
if exist ".venv\Scripts\pythonw.exe" if defined FFMPEG_OK (
    start "" ".venv\Scripts\pythonw.exe" "run.py" %*
    exit /b 0
)

echo [VidEdit] First-time setup: checking required dependencies...

if not defined FFMPEG_OK (
    where winget >nul 2>&1 || (
        echo [VidEdit] ERROR: FFmpeg is missing and winget is unavailable.
        echo Please install FFmpeg manually: https://ffmpeg.org/
        pause
        exit /b 1
    )
    echo [VidEdit] Installing FFmpeg...
    winget install --id Gyan.FFmpeg -e --accept-source-agreements --accept-package-agreements
    if errorlevel 1 (
        echo [VidEdit] ERROR: Failed to install FFmpeg.
        pause
        exit /b 1
    )
)

if not exist ".venv\Scripts\pythonw.exe" (
    call :find_python
    if not defined PYTHON_EXE (
        where winget >nul 2>&1 || (
            echo [VidEdit] ERROR: Python 3.10+ is missing and winget is unavailable.
            echo Please install Python 3.10+ from https://www.python.org/downloads/
            pause
            exit /b 1
        )
        echo [VidEdit] Installing Python 3.12...
        winget install --id Python.Python.3.12 -e --accept-source-agreements --accept-package-agreements
        if errorlevel 1 (
            echo [VidEdit] ERROR: Failed to install Python.
            pause
            exit /b 1
        )
        call :find_python
    )

    if not defined PYTHON_EXE (
        echo [VidEdit] ERROR: Python 3.10+ was installed but could not be located yet.
        echo Please close this window and double-click VidEdit.bat again.
        pause
        exit /b 1
    )

    echo [VidEdit] Creating virtual environment with "!PYTHON_EXE!"...
    "!PYTHON_EXE!" -m venv .venv
    if errorlevel 1 (
        echo [VidEdit] ERROR: Failed to create .venv.
        pause
        exit /b 1
    )

    echo [VidEdit] Installing Python dependencies...
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
    if errorlevel 1 (
        echo [VidEdit] ERROR: Failed to install requirements.
        pause
        exit /b 1
    )
)

start "" ".venv\Scripts\pythonw.exe" "run.py" %*
exit /b 0

:find_python
set "PYTHON_EXE="
py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
if not errorlevel 1 (
    for /f "usebackq delims=" %%P in (`py -3 -c "import sys; print(sys.executable)" 2^>nul`) do set "PYTHON_EXE=%%P"
    if defined PYTHON_EXE exit /b 0
)

for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python3*") do (
    if exist "%%~fD\python.exe" (
        "%%~fD\python.exe" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
        if not errorlevel 1 (
            set "PYTHON_EXE=%%~fD\python.exe"
            exit /b 0
        )
    )
)

for /d %%D in ("%ProgramFiles%\Python3*") do (
    if exist "%%~fD\python.exe" (
        "%%~fD\python.exe" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
        if not errorlevel 1 (
            set "PYTHON_EXE=%%~fD\python.exe"
            exit /b 0
        )
    )
)

for /f "usebackq delims=" %%P in (`where python 2^>nul`) do (
    echo "%%P" | findstr /i "WindowsApps" >nul
    if errorlevel 1 (
        "%%P" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
        if not errorlevel 1 (
            set "PYTHON_EXE=%%P"
            exit /b 0
        )
    )
)
exit /b 0
