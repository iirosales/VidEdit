"""Unit and headless UI tests for the integrated YouTube MP4 downloader."""

from __future__ import annotations

import os
import subprocess
import threading
from pathlib import Path
from typing import Any

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

import videdit.youtube_downloader as yt_mod
from videdit.ffmpeg_tools import find_ffmpeg_binaries
from videdit.ui.icons import make_monoline_icon
from videdit.ui.main_window import MainWindow
from videdit.ui.youtube_drawer import resolve_default_youtube_dir


@pytest.fixture(scope="module")
def qt_app() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_parse_urls_deduplicates_and_splits() -> None:
    raw = """
    https://youtu.be/abc123   https://youtube.com/watch?v=xyz
    https://youtu.be/abc123
      https://youtube.com/watch?v=second
    """
    assert yt_mod.parse_urls(raw) == [
        "https://youtu.be/abc123",
        "https://youtube.com/watch?v=xyz",
        "https://youtube.com/watch?v=second",
    ]


def test_build_format_selector_variants() -> None:
    with_ffmpeg_1080 = yt_mod.build_format_selector(1080, has_ffmpeg=True)
    assert "height<=1080" in with_ffmpeg_1080
    assert "vcodec^=avc1" in with_ffmpeg_1080

    with_ffmpeg_best = yt_mod.build_format_selector(None, has_ffmpeg=True)
    assert "bestvideo[vcodec^=avc1]+bestaudio[ext=m4a]" in with_ffmpeg_best

    no_ffmpeg_720 = yt_mod.build_format_selector(720, has_ffmpeg=False)
    assert no_ffmpeg_720.startswith("best[ext=mp4][height<=720]")


def test_download_videos_collects_paths_and_passes_ffmpeg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured_opts: list[dict[str, Any]] = []
    saved_events: list[Path] = []
    progress_events: list[tuple[int, str]] = []

    class FakeYoutubeDL:
        def __init__(self, opts: dict[str, Any]) -> None:
            self.opts = opts
            captured_opts.append(opts)

        def __enter__(self) -> "FakeYoutubeDL":
            return self

        def __exit__(self, *_args) -> None:
            return None

        def extract_info(self, url: str, download: bool = True) -> dict[str, Any]:
            assert download is True
            out_file = tmp_path / "Sample Video [id123].mp4"
            out_file.write_bytes(b"fake-mp4")
            for hook in self.opts["progress_hooks"]:
                hook({"status": "downloading", "downloaded_bytes": 50, "total_bytes": 100})
                hook({"status": "finished"})
            for pp_hook in self.opts["postprocessor_hooks"]:
                pp_hook({"status": "finished", "info_dict": {"filepath": str(out_file)}})
            return {
                "title": "Sample Video",
                "requested_downloads": [{"filepath": str(out_file)}],
            }

    monkeypatch.setattr(yt_mod, "yt_dlp", type("FakeMod", (), {"YoutubeDL": FakeYoutubeDL}))
    monkeypatch.setattr(yt_mod, "find_ffmpeg_binaries", lambda: ("C:/fake/ffmpeg.exe", "C:/fake/ffprobe.exe"))

    result = yt_mod.download_videos(
        urls=["https://youtu.be/id123"],
        output_dir=tmp_path,
        max_height=1080,
        on_progress=lambda p, s: progress_events.append((p, s)),
        on_file_saved=saved_events.append,
    )

    assert not result.cancelled
    assert result.failed_count == 0
    assert result.saved_paths == [tmp_path / "Sample Video [id123].mp4"]
    assert saved_events == [tmp_path / "Sample Video [id123].mp4"]
    assert captured_opts[0]["ffmpeg_location"] == "C:/fake/ffmpeg.exe"
    assert captured_opts[0]["merge_output_format"] == "mp4"
    assert progress_events[-1][0] == 100


def test_download_videos_respects_cancellation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cancel = threading.Event()
    cancel.set()

    monkeypatch.setattr(yt_mod, "yt_dlp", object())
    monkeypatch.setattr(yt_mod, "find_ffmpeg_binaries", lambda: (None, None))

    result = yt_mod.download_videos(
        urls=["https://youtu.be/id123"],
        output_dir=tmp_path,
        cancel_event=cancel,
    )
    assert result.cancelled is True
    assert result.saved_paths == []


def test_youtube_drawer_ui_default_folder_and_autoload(
    qt_app: QApplication, tmp_path: Path
) -> None:
    assert not make_monoline_icon("download", "#F4F1F8").isNull()
    assert not make_monoline_icon("paste", "#F4F1F8").isNull()

    ini_path = tmp_path / "yt_settings.ini"
    settings = QSettings(str(ini_path), QSettings.Format.IniFormat)

    default_open_dir = tmp_path / "OpenDefault"
    default_open_dir.mkdir()
    custom_yt_dir = tmp_path / "YouTubeCustom"
    custom_yt_dir.mkdir()

    # Fallback before any setting is configured
    assert resolve_default_youtube_dir(settings) == yt_mod.DEFAULT_YOUTUBE_DIR

    win = MainWindow(initial_theme="dark", settings=settings)
    try:
        # Semi-hidden on startup: edge tab visible, drawer hidden
        assert not win._yt_drawer.isVisibleTo(win)
        assert win._yt_edge_btn.text() == "‹\nY\nT"

        cmd_labels = [entry.label for entry in win.build_command_entries()]
        assert "Toggle YouTube MP4 Downloader" in cmd_labels

        # Open drawer via edge button, then close via drawer X button
        win._yt_edge_btn.click()
        qt_app.processEvents()
        assert win._yt_drawer.isVisibleTo(win)
        assert win._yt_edge_btn.text() == "›\nY\nT"

        win._yt_drawer._close_btn.click()
        qt_app.processEvents()
        assert not win._yt_drawer.isVisibleTo(win)
        assert win._yt_edge_btn.text() == "‹\nY\nT"

        # VidEdit Default Folder propagates into YouTube drawer unless YT folder is customized
        win.set_default_open_dir(default_open_dir)
        assert Path(win._yt_drawer._output_dir_edit.text()) == default_open_dir

        # Custom YT folder persists separately from VidEdit Default Folder
        win._yt_drawer._output_dir_edit.setText(str(custom_yt_dir))
        win._yt_drawer._save_custom_dir_from_edit()
        assert resolve_default_youtube_dir(settings) == custom_yt_dir
        assert win.default_open_dir() == default_open_dir

        # Smart paste of multiple URLs switches to Multi mode automatically
        clipboard = QApplication.clipboard()
        assert clipboard is not None
        clipboard.setText("https://youtu.be/one\nhttps://youtu.be/two")
        win._yt_drawer._paste_clipboard()
        assert win._yt_drawer._batch_toggle_btn.isChecked()
        assert win._yt_drawer._current_urls() == [
            "https://youtu.be/one",
            "https://youtu.be/two",
        ]

        # Test auto-load into VidEdit when a single real MP4 finishes
        ffmpeg_path, _ = find_ffmpeg_binaries()
        if not ffmpeg_path:
            pytest.skip("ffmpeg not available")

        clip1 = tmp_path / "yt_one.mp4"
        clip2 = tmp_path / "yt_two.mp4"
        for clip in (clip1, clip2):
            subprocess.run(
                [
                    ffmpeg_path,
                    "-y",
                    "-f",
                    "lavfi",
                    "-i",
                    "testsrc2=size=320x180:rate=30:duration=1",
                    "-c:v",
                    "libx264",
                    "-preset",
                    "ultrafast",
                    "-pix_fmt",
                    "yuv420p",
                    str(clip),
                ],
                capture_output=True,
                check=True,
            )

        win._yt_drawer._on_finished([str(clip1)], 0, False, [])
        qt_app.processEvents()
        assert win._video_info is not None
        assert win._video_info.path == clip1

        # Multi-file completion keeps clip list visible and loads selected clip on demand
        win._yt_drawer._on_finished([str(clip1), str(clip2)], 0, False, [])
        qt_app.processEvents()
        assert win._yt_drawer._clips_list.count() == 2
        win._yt_drawer._clips_list.setCurrentRow(1)
        win._yt_drawer._load_clip_btn.click()
        qt_app.processEvents()
        assert win._video_info.path == clip2
    finally:
        win.close()
        qt_app.processEvents()
