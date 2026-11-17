@echo off
setlocal
cd /d "%~dp0"

set "VENV_PY=.venv\Scripts\python.exe"
if not exist "%VENV_PY%" (
    echo [VidEdit] Creating .venv and installing requirements...
    python -m venv .venv || goto :error
    "%VENV_PY%" -m pip install -r requirements.txt || goto :error
)

"%VENV_PY%" -m PyInstaller --version >nul 2>&1
if errorlevel 1 (
    echo [VidEdit] Installing PyInstaller into .venv...
    "%VENV_PY%" -m pip install pyinstaller || goto :error
)

for /f "usebackq delims=" %%I in (`%VENV_PY% -c "from videdit.ffmpeg_tools import find_ffmpeg_binaries; p, _ = find_ffmpeg_binaries(); print(p or '')"`) do set "FFMPEG_PATH=%%I"
for /f "usebackq delims=" %%I in (`%VENV_PY% -c "from videdit.ffmpeg_tools import find_ffmpeg_binaries; _, p = find_ffmpeg_binaries(); print(p or '')"`) do set "FFPROBE_PATH=%%I"

if "%FFMPEG_PATH%"=="" (
    echo [VidEdit] ERROR: ffmpeg.exe was not found. Install it first: winget install Gyan.FFmpeg
    goto :error
)
if "%FFPROBE_PATH%"=="" (
    echo [VidEdit] ERROR: ffprobe.exe was not found. Install it first: winget install Gyan.FFmpeg
    goto :error
)

echo [VidEdit] Bundling ffmpeg:  "%FFMPEG_PATH%"
echo [VidEdit] Bundling ffprobe: "%FFPROBE_PATH%"

if not exist "build" mkdir "build"

"%VENV_PY%" -m PyInstaller ^
    --noconfirm ^
    --clean ^
    --onedir ^
    --windowed ^
    --name "VidEdit" ^
    --icon "%~dp0favicon.ico" ^
    --specpath "build" ^
    --add-data "%~dp0favicon.ico;." ^
    --add-binary "%FFMPEG_PATH%;." ^
    --add-binary "%FFPROBE_PATH%;." ^
    --exclude-module pytest ^
    --exclude-module PySide6.QtWebEngine ^
    --exclude-module PySide6.QtWebEngineCore ^
    --exclude-module PySide6.QtWebEngineWidgets ^
    --exclude-module PySide6.QtPdf ^
    --exclude-module PySide6.QtPdfWidgets ^
    "%~dp0run.py" || goto :error

echo [VidEdit] Creating dist\VidEdit.zip...
if exist "dist\VidEdit.zip" del /f /q "dist\VidEdit.zip"
powershell -NoProfile -Command "Compress-Archive -Path 'dist\VidEdit' -DestinationPath 'dist\VidEdit.zip' -Force" || goto :error

echo.
echo [VidEdit] Build complete!
echo   Folder: %~dp0dist\VidEdit\VidEdit.exe
echo   Zip:    %~dp0dist\VidEdit.zip
echo.
echo Send dist\VidEdit.zip to your friend - they just unzip and double-click VidEdit.exe.
if /i not "%~1"=="--nopause" pause
exit /b 0

:error
echo.
echo [VidEdit] Build failed. See errors above.
if /i not "%~1"=="--nopause" pause
exit /b 1
