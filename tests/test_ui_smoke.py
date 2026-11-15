"""Headless UI smoke tests for MainWindow, TrimTimelineWidget, themes, and CommandPalette."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QEventLoop, QPointF, QTimer, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtWidgets import QApplication, QWidget

from videdit.ffmpeg_tools import find_ffmpeg_binaries
from videdit.ui.chrome import CommandPalette
from videdit.ui.icons import ClockworkSpinner, make_monoline_icon
from videdit.ui.main_window import MainWindow, format_file_size, format_timestamp_ms
from videdit.ui.theme import DARK_THEME, LIGHT_THEME, build_stylesheet
from videdit.ui.timeline import TrimTimelineWidget
from videdit.win_taskbar import build_relaunch_command


@pytest.fixture(scope="module")
def qt_app() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


@pytest.fixture(scope="module")
def main_window(qt_app: QApplication):
    win = MainWindow(initial_theme="dark")
    yield win
    win.close()
    qt_app.processEvents()


def test_formatters() -> None:
    assert format_timestamp_ms(0) == "00:00.00"
    assert format_timestamp_ms(65_430) == "01:05.43"
    assert format_timestamp_ms(3_661_250) == "01:01:01.25"
    assert format_file_size(500) == "500 B"
    assert format_file_size(10_500_000) == "10.50 MB"


def test_dual_theme_stylesheets(qt_app: QApplication) -> None:
    light_qss = build_stylesheet(LIGHT_THEME)
    dark_qss = build_stylesheet(DARK_THEME)
    assert "#FAF9FC" in light_qss
    assert "#1B54B8" in light_qss
    assert "#9E1933" in light_qss
    assert "#110E16" in dark_qss
    assert "#3D7EFF" in dark_qss
    assert "#E0284A" in dark_qss


def test_monoline_icons_and_spinner(qt_app: QApplication) -> None:
    for name in ("play", "pause", "step_back", "step_fwd", "open", "volume", "muted", "theme_light", "theme_dark"):
        assert not make_monoline_icon(name, "#F4F1F8").isNull()
    spinner = ClockworkSpinner()
    spinner.set_active(True)
    spinner.set_active(False)


def test_relaunch_command_points_to_run_py() -> None:
    root = Path(__file__).resolve().parent.parent
    cmd = build_relaunch_command(root)
    assert "run.py" in cmd


def test_timeline_clamping(qt_app: QApplication) -> None:
    timeline = TrimTimelineWidget()
    timeline.set_duration(10_000)
    assert timeline.in_ms() == 0
    assert timeline.out_ms() == 10_000

    # Clamp In so it never crosses Out - min_clip_ms (200ms)
    timeline.set_trim_points(9_950, 10_000)
    assert timeline.in_ms() <= 9_800
    assert timeline.out_ms() == 10_000


def test_main_window_loads_video_and_switches_presets(
    qt_app: QApplication, main_window: MainWindow, tmp_path: Path
) -> None:
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

    win = main_window
    if (Path(__file__).resolve().parent.parent / "favicon.ico").is_file():
        assert not win.windowIcon().isNull()
    assert win._step_back_btn.text() == "-0.1s"
    assert win._step_fwd_btn.text() == "+0.1s"
    assert win._theme.name == "dark"

    win.toggle_theme()
    assert win._theme.name == "light"
    assert "Alabaster Light" in win._theme_toggle_btn.text()

    win.toggle_theme()
    assert win._theme.name == "dark"

    # Command palette filtering smoke test
    palette = CommandPalette(win, win.build_command_entries(), win._theme)
    palette._search.setText("10 MB")
    assert palette._list.count() == 1
    assert "10 MB" in palette._list.item(0).text()
    palette.close()

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


def test_click_video_surface_toggles_playback(
    qt_app: QApplication, main_window: MainWindow, tmp_path: Path
) -> None:
    ffmpeg_path, _ = find_ffmpeg_binaries()
    if not ffmpeg_path:
        pytest.skip("ffmpeg not available")

    clip = tmp_path / "click_toggle.mp4"
    subprocess.run(
        [
            ffmpeg_path,
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=320x180:rate=30:duration=2",
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

    win = main_window
    win.load_video(clip)
    qt_app.processEvents()

    assert win._player.playbackState() != QMediaPlayer.PlaybackState.PlayingState

    # Click directly on the internal child QWidget (if created) or _video_widget itself
    children = win._video_widget.findChildren(QWidget)
    target = children[0] if children else win._video_widget

    press_event = QMouseEvent(
        QEvent.Type.MouseButtonPress,
        QPointF(10, 10),
        QPointF(10, 10),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    QApplication.sendEvent(target, press_event)
    qt_app.processEvents()
    assert win._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState

    pause_event = QMouseEvent(
        QEvent.Type.MouseButtonPress,
        QPointF(10, 10),
        QPointF(10, 10),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    QApplication.sendEvent(win._video_widget, pause_event)
    qt_app.processEvents()
    assert win._player.playbackState() == QMediaPlayer.PlaybackState.PausedState


def test_end_of_media_and_backward_scrub_keep_frame_visible(
    qt_app: QApplication, main_window: MainWindow, tmp_path: Path
) -> None:
    ffmpeg_path, _ = find_ffmpeg_binaries()
    if not ffmpeg_path:
        pytest.skip("ffmpeg not available")

    clip = tmp_path / "eof_preview.mp4"
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

    win = main_window
    win._loop_check.setChecked(False)
    win.load_video(clip)

    def _wait_ms(ms: int) -> None:
        loop = QEventLoop()
        QTimer.singleShot(ms, loop.quit)
        loop.exec()

    # Wait until LoadedMedia settles and start playing through EndOfMedia.
    _wait_ms(200)
    win._player.setPosition(700)
    win._player.play()

    eof_reached = False
    for _ in range(30):
        _wait_ms(100)
        if win._player.mediaStatus() == QMediaPlayer.MediaStatus.EndOfMedia or (
            win._player.playbackState() == QMediaPlayer.PlaybackState.PausedState
            and win._player.position() >= 850
        ):
            eof_reached = True
            break

    assert eof_reached
    _wait_ms(250)

    assert win._player.playbackState() == QMediaPlayer.PlaybackState.PausedState
    sink = win._video_widget.videoSink()
    end_frame = sink.videoFrame()
    assert end_frame.isValid()
    end_img = end_frame.toImage()
    assert not end_img.isNull()
    assert end_img.pixelColor(80, 60).lightness() > 0 or end_img.pixelColor(200, 90).lightness() > 0

    # Scrub backward while paused after EndOfMedia and verify preview frame stays valid.
    win._timeline.seek_requested.emit(250)
    _wait_ms(250)

    assert win._player.playbackState() == QMediaPlayer.PlaybackState.PausedState
    scrub_frame = sink.videoFrame()
    assert scrub_frame.isValid()
    scrub_img = scrub_frame.toImage()
    assert not scrub_img.isNull()
    assert scrub_img.pixelColor(80, 60).lightness() > 0 or scrub_img.pixelColor(200, 90).lightness() > 0
