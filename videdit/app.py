"""Application bootstrap for VidEdit."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMessageBox

from videdit.ffmpeg_tools import find_ffmpeg_binaries
from videdit.paths import resolve_resource_path
from videdit.ui.main_window import MainWindow
from videdit.win_taskbar import set_process_app_user_model_id


def resolve_app_icon_path() -> Optional[Path]:
    """Return the path to favicon.ico in the app bundle or project root if present."""
    return resolve_resource_path("favicon.ico")


def main() -> int:
    """Launch the VidEdit desktop GUI."""
    set_process_app_user_model_id()

    app = QApplication(sys.argv)
    app.setApplicationName("VidEdit")
    app.setOrganizationName("iirosales")

    icon_path = resolve_app_icon_path()
    if icon_path is not None:
        app.setWindowIcon(QIcon(str(icon_path)))

    ffmpeg_path, ffprobe_path = find_ffmpeg_binaries()
    if not ffmpeg_path or not ffprobe_path:
        QMessageBox.critical(
            None,
            "FFmpeg Not Found",
            "VidEdit requires ffmpeg and ffprobe.\n\n"
            "Install FFmpeg on Windows with:\n"
            "  winget install --id Gyan.FFmpeg -e\n\n"
            "Then restart VidEdit.",
        )
        return 1

    window = MainWindow()
    window.show()

    # Optional CLI argument: open a video file directly
    if len(sys.argv) > 1:
        candidate = Path(sys.argv[1])
        if candidate.is_file():
            window.load_video(candidate)

    return app.exec()
