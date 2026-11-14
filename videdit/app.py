"""Application bootstrap for VidEdit."""

from __future__ import annotations

import ctypes
import sys
from pathlib import Path
from typing import Optional

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMessageBox

from videdit.ffmpeg_tools import find_ffmpeg_binaries
from videdit.ui.main_window import MainWindow


def resolve_app_icon_path() -> Optional[Path]:
    """Return the path to favicon.ico in the project root if present."""
    candidate = Path(__file__).resolve().parent.parent / "favicon.ico"
    return candidate if candidate.is_file() else None


def _set_windows_app_user_model_id() -> None:
    """Assign an explicit Windows AppUserModelID so the taskbar uses VidEdit's icon instead of Python's."""
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("iirosales.VidEdit.App.1")
    except (AttributeError, OSError):
        pass


def main() -> int:
    """Launch the VidEdit desktop GUI."""
    _set_windows_app_user_model_id()

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
