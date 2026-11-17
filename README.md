# VidEdit

A minimalist desktop video editor built with Python, PySide6, and FFmpeg.

## Features
- **Trim (In / Out)**: Scrub video playback and set Start/End points to keep the exact clip you want.
- **Export to MP4**: Save as high-quality H.264 MP4 or target a specific file size (**10 MB**, **15 MB**, **20 MB**, or **Custom MB**) using 2-pass bitrate encoding—ideal for Discord uploads without Nitro.
- **Export to GIF**: Generate crisp animated GIFs using 2-pass palette optimization (`palettegen` / `paletteuse`), with FPS, width, and optional auto-fit size limit controls.

## Requirements
- Python 3.10+
- [FFmpeg](https://ffmpeg.org/) (`ffmpeg` and `ffprobe` on `PATH` or installed via `winget install Gyan.FFmpeg`)

## Setup & Run

```powershell
python -m venv .venv
.\.venv\Scripts\pip.exe install -r requirements.txt
.\.venv\Scripts\python.exe run.py
```

On Windows, you can simply double-click `VidEdit.bat`—it automatically checks for Python 3.10+, FFmpeg, and `.venv` dependencies, installs anything missing on first run, and launches immediately on future runs.

## Keyboard Shortcuts
- `Space`: Play / Pause
- `I`: Set **In** (Start) point at current playhead
- `O`: Set **Out** (End) point at current playhead
- `Left` / `Right`: Step backward / forward 0.1 seconds
- `Shift + Left` / `Shift + Right`: Step backward / forward 1 second
