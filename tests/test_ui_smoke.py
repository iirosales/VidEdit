"""Headless UI smoke tests for MainWindow and TrimTimelineWidget."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from videdit.ffmpeg_tools import find_ffmpeg_binaries
from videdit.ui.main_window import MainWindow, format_file_size, format_timestamp_ms
from videdit.ui.timeline import TrimTimelineWidget


@pytest.fixture(scope="module")
def qt_app() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_formatters() -> None:
    assert format_timestamp_ms(0) == "00:00.00"
    assert format_timestamp_ms(65_430) == "01:05.43"
    assert format_timestamp_ms(3_661_250) == "01:01:01.25"
    assert format_file_size(500) == "500 B"
    assert format_file_size(10_500_000) == "10.50 MB"


def test_timeline_clamping(qt_app: QApplication) -> None:
    timeline = TrimTimelineWidget()
    timeline.set_duration(10_000)
    assert timeline.in_ms() == 0
    assert timeline.out_ms() == 10_000

    # Clamp In so it never crosses Out - min_clip_ms (200ms)
    timeline.set_trim_points(9_950, 10_000)
    assert timeline.in_ms() <= 9_800
    assert timeline.out_ms() == 10_000


def test_main_window_loads_video_and_switches_presets(qt_app: QApplication, tmp_path: Path) -> None:
    ffmpeg_path, _ = find_ffmpeg_binaries()
    if not ffmpeg_path:
        pytest.skip("ffmpeg not available")

    clip = tmp_path / "ui_test.mp4"
    subprocess.run(
        [
            ffmpeg_path,
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=640x360:rate=30:duration=4",
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

    win = MainWindow()
    assert win._step_back_btn.text() == "-0.1s"
    assert win._step_fwd_btn.text() == "+0.1s"
    win.load_video(clip)

    assert win._video_info is not None
    assert win._export_btn.isEnabled()

    win._step_fwd_btn.click()
    assert win._timeline._position_ms == 100
    win._step_back_btn.click()
    assert win._timeline._position_ms == 0

    # Switch to 10 MB preset and verify estimate & output filename
    win._size_buttons["10"].setChecked(True)
    win._on_mp4_size_changed()
    assert win._selected_target_mb() == 10.0
    assert "_cut_10mb.mp4" in win._output_edit.text()
    assert "2-Pass Plan:" in win._mp4_estimate_label.text()

    # Switch to GIF format
    win._fmt_gif_btn.setChecked(True)
    win._set_output_format("gif")
    assert win._output_edit.text().endswith(".gif")

    win.close()
    qt_app.processEvents()
