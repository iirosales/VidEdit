# VidEdit

A minimalist desktop video editor built with Python, PySide6, and FFmpeg.

## Features
- **Trim (In / Out)**: Scrub video playback and set Start/End points to keep the exact clip you want.
- **Crop**: Draw a screenshot-style crop box directly on the video preview (`C`).
- **Export to MP4**: Save as high-quality H.264 MP4 or target a specific file size (**10 MB**, **15 MB**, **20 MB**, or **Custom MB**) using 2-pass bitrate encoding—ideal for Discord uploads without Nitro.
- **Export to GIF**: Generate crisp animated GIFs using 2-pass palette optimization (`palettegen` / `paletteuse`), with FPS, width, optional auto-fit size limit controls, and Impact meme captions.
- **YouTube MP4 Downloader**: Collapsible right-edge side drawer (`Ctrl+Y`) powered by `yt-dlp` and FFmpeg—paste single links or expand `Multi` mode for batch/playlist downloads, pick max quality (up to 4K+), and auto-load downloaded MP4s directly into the editor.

## Requirements
- Python 3.10+
- [FFmpeg](https://ffmpeg.org/) (`ffmpeg` and `ffprobe` on `PATH` or installed via `winget install Gyan.FFmpeg`)
- [yt-dlp](https://github.com/yt-dlp/yt-dlp) (installed via `requirements.txt` or automatically by `VidEdit.bat`)

## Setup & Run

```powershell
python -m venv .venv
.\.venv\Scripts\pip.exe install -r requirements.txt
.\.venv\Scripts\python.exe run.py
```

For my non-technical friends on Windows, you can simply double-click `VidEdit.bat`—it automatically checks for Python 3.10+, FFmpeg, and `.venv` dependencies (including `yt-dlp`), installs anything missing, and launches immediately on future runs.

## Keyboard Shortcuts
- `Space`: Play / Pause
- `I`: Set **In** (Start) point at current playhead
- `O`: Set **Out** (End) point at current playhead
- `C`: Toggle video crop mode
- `Left` / `Right`: Step backward / forward 0.1 seconds
- `Shift + Left` / `Shift + Right`: Step backward / forward 1 second
- `Ctrl + Y`: Toggle YouTube MP4 Downloader side drawer
- `Ctrl + K`: Open Command Palette
