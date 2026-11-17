"""Tests for frozen bundle resource, FFmpeg, and taskbar relaunch resolution."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from videdit.ffmpeg_tools import find_ffmpeg_binaries
from videdit.paths import resolve_resource_path
from videdit.win_taskbar import build_relaunch_command


def test_resolve_resource_path_finds_favicon_in_source_checkout() -> None:
    icon = resolve_resource_path("favicon.ico")
    assert icon is not None
    assert icon.is_file()
    assert icon.name == "favicon.ico"


def test_resolve_resource_path_and_ffmpeg_in_frozen_meipass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle_dir = tmp_path / "_internal"
    bundle_dir.mkdir()
    exe_dir = tmp_path / "dist"
    exe_dir.mkdir()
    fake_exe = exe_dir / "VidEdit.exe"
    fake_exe.write_bytes(b"")

    ext = ".exe" if os.name == "nt" else ""
    bundled_ffmpeg = bundle_dir / f"ffmpeg{ext}"
    bundled_ffprobe = bundle_dir / f"ffprobe{ext}"
    bundled_icon = bundle_dir / "favicon.ico"
    bundled_ffmpeg.write_bytes(b"")
    bundled_ffprobe.write_bytes(b"")
    bundled_icon.write_bytes(b"")

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(bundle_dir), raising=False)
    monkeypatch.setattr(sys, "executable", str(fake_exe))

    assert resolve_resource_path("favicon.ico") == bundled_icon
    ffmpeg_path, ffprobe_path = find_ffmpeg_binaries()
    assert ffmpeg_path == str(bundled_ffmpeg)
    assert ffprobe_path == str(bundled_ffprobe)
    assert build_relaunch_command(tmp_path) == f'"{fake_exe}"'
