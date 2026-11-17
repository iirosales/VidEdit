"""Runtime path helpers for both source checkouts and frozen PyInstaller bundles."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional


def is_frozen() -> bool:
    """Return True when running inside a frozen executable bundle (e.g., PyInstaller)."""
    return bool(getattr(sys, "frozen", False))


def bundle_search_dirs() -> list[Path]:
    """Return ordered directories to search for bundled binaries and assets."""
    dirs: list[Path] = []
    if is_frozen():
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            dirs.append(Path(meipass))
        exe_dir = Path(sys.executable).resolve().parent
        dirs.append(exe_dir)
        dirs.append(exe_dir / "bin")
    dirs.append(Path(__file__).resolve().parent.parent)

    unique: list[Path] = []
    for directory in dirs:
        if directory not in unique:
            unique.append(directory)
    return unique


def resolve_resource_path(relative_path: str) -> Optional[Path]:
    """Locate a bundled asset (such as favicon.ico) across bundle and source roots."""
    for directory in bundle_search_dirs():
        candidate = directory / relative_path
        if candidate.is_file():
            return candidate
    return None
