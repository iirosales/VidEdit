"""Headless UI smoke tests for MainWindow, TrimTimelineWidget, themes, and CommandPalette."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QEventLoop, QPointF, QSettings, QTimer, Qt
from PySide6.QtGui import QColor, QImage, QMouseEvent
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtWidgets import QApplication, QFileDialog, QWidget

from videdit.ffmpeg_tools import CropRect, GifCaption, find_ffmpeg_binaries
from videdit.ui.chrome import CommandPalette
from videdit.ui.icons import ClockworkSpinner, make_monoline_icon
from videdit.ui.main_window import (
    MainWindow,
    format_file_size,
    format_timestamp_ms,
    resolve_fallback_open_dir,
)
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


def test_screenshot_crop_drag_move_and_clear(
    qt_app: QApplication, main_window: MainWindow, tmp_path: Path
) -> None:
    assert not make_monoline_icon("crop", "#F4F1F8").isNull()

    ffmpeg_path, _ = find_ffmpeg_binaries()
    if not ffmpeg_path:
        pytest.skip("ffmpeg not available")

    clip = tmp_path / "crop_ui.mp4"
    subprocess.run(
        [
            ffmpeg_path,
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=640x360:rate=30:duration=2",
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
    win._video_widget.resize(640, 360)
    qt_app.processEvents()

    assert win._active_crop is None
    assert not win._clear_crop_btn.isVisibleTo(win)

    # Enable Crop Mode and drag a screenshot-style marquee from (64, 36) to (384, 216)
    win._crop_btn.click()
    qt_app.processEvents()
    assert win._video_widget.is_crop_mode()
    assert win._crop_btn.text() == "Done Crop (C)"

    start_pt = QPointF(64, 36)
    end_pt = QPointF(384, 216)
    QApplication.sendEvent(
        win._video_widget,
        QMouseEvent(
            QEvent.Type.MouseButtonPress,
            start_pt,
            start_pt,
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        ),
    )
    QApplication.sendEvent(
        win._video_widget,
        QMouseEvent(
            QEvent.Type.MouseMove,
            end_pt,
            end_pt,
            Qt.MouseButton.NoButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        ),
    )
    QApplication.sendEvent(
        win._video_widget,
        QMouseEvent(
            QEvent.Type.MouseButtonRelease,
            end_pt,
            end_pt,
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
        ),
    )
    qt_app.processEvents()

    crop = win._active_crop
    assert crop is not None
    assert crop.x == pytest.approx(64, abs=2)
    assert crop.y == pytest.approx(36, abs=2)
    assert crop.width == pytest.approx(320, abs=2)
    assert crop.height == pytest.approx(180, abs=2)
    assert crop.width % 2 == 0 and crop.height % 2 == 0
    assert win._clear_crop_btn.isVisibleTo(win)
    assert win._crop_badge.text() == f"Crop: {crop.width}×{crop.height}"

    # Render overlay paintEvent without errors
    win._video_widget.repaint()

    # Exit Crop Mode -> crop persists while clicking video toggles play/pause again
    win._crop_btn.click()
    qt_app.processEvents()
    assert not win._video_widget.is_crop_mode()
    assert win._active_crop == crop

    # Clear crop resets crop state and hides clear controls
    win._clear_crop_btn.click()
    qt_app.processEvents()
    assert win._active_crop is None
    assert not win._clear_crop_btn.isVisibleTo(win)


def test_default_open_folder_selection_and_persistence(
    qt_app: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ini_path = tmp_path / "test_settings.ini"
    settings = QSettings(str(ini_path), QSettings.Format.IniFormat)

    custom_dir = tmp_path / "MyClips"
    custom_dir.mkdir()
    second_dir = tmp_path / "MoreClips"
    second_dir.mkdir()

    win = MainWindow(initial_theme="dark", settings=settings)
    try:
        # Unconfigured default falls back to system Videos/home and never .venv/Scripts.
        fallback = resolve_fallback_open_dir()
        assert fallback.is_dir()
        assert win.default_open_dir() == fallback
        assert ".venv" not in str(win.default_open_dir()).lower()
        assert str(fallback) in win._default_folder_btn.toolTip()

        seen_open_dirs: list[str] = []

        def fake_open_file(_parent, _title, directory, _filt):
            seen_open_dirs.append(directory)
            return ("", "")

        monkeypatch.setattr(QFileDialog, "getOpenFileName", fake_open_file)

        # Both Open Video... and Select Clip use the resolved default folder, not "".
        win._open_btn.click()
        win._drop_open_btn.click()
        assert seen_open_dirs == [str(fallback), str(fallback)]

        # User clicks Default Folder... to pick their custom default folder.
        seen_dir_starts: list[str] = []

        def fake_choose_dir(_parent, _title, directory):
            seen_dir_starts.append(directory)
            return str(custom_dir)

        monkeypatch.setattr(QFileDialog, "getExistingDirectory", fake_choose_dir)
        win._default_folder_btn.click()

        assert seen_dir_starts == [str(fallback)]
        assert win.default_open_dir() == custom_dir
        assert str(custom_dir) in win._default_folder_btn.toolTip()
        assert str(custom_dir) in win._telemetry_label.text()

        # Subsequent Open Video clicks start in the user's chosen default folder.
        seen_open_dirs.clear()
        win._open_btn.click()
        assert seen_open_dirs == [str(custom_dir)]

        # Command palette exposes Set / Reset Default Open Folder.
        cmd_labels = [entry.label for entry in win.build_command_entries()]
        assert "Set Default Open Folder..." in cmd_labels
        assert "Reset Default Open Folder" in cmd_labels
    finally:
        win.close()
        qt_app.processEvents()

    # Recreating MainWindow with the same QSettings restores the user's chosen folder.
    reopened_settings = QSettings(str(ini_path), QSettings.Format.IniFormat)
    win2 = MainWindow(initial_theme="dark", settings=reopened_settings)
    try:
        assert win2.default_open_dir() == custom_dir

        # If the saved folder is deleted, it gracefully falls back instead of returning "".
        custom_dir.rmdir()
        assert win2.default_open_dir() == resolve_fallback_open_dir()

        # Setting another folder and resetting via command clears the persisted override.
        win2.set_default_open_dir(second_dir)
        assert win2.default_open_dir() == second_dir
        win2._reset_default_open_folder()
        assert win2.default_open_dir() == resolve_fallback_open_dir()
    finally:
        win2.close()
        qt_app.processEvents()


def test_gif_meme_captions_ui_and_live_preview(
    qt_app: QApplication, main_window: MainWindow, tmp_path: Path
) -> None:
    ffmpeg_path, _ = find_ffmpeg_binaries()
    if not ffmpeg_path:
        pytest.skip("ffmpeg not available")

    clip = tmp_path / "caption_preview.mp4"
    subprocess.run(
        [
            ffmpeg_path,
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=640x360:rate=30:duration=2",
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
    win.resize(920, 640)
    win.load_video(clip)
    win._select_format_pill("gif")
    qt_app.processEvents()

    # Ensure GIF panel controls remain cleanly sized even at minimum window size.
    assert win._gif_fps_combo.height() >= 28
    assert win._gif_width_combo.height() >= 28
    assert win._gif_limit_combo.height() >= 28
    assert win._gif_caption_size_combo.height() >= 28
    assert win._gif_caption_caps_check.isChecked()
    assert win._video_widget.caption_preview() is None

    # Typing top & bottom captions updates the live preview overlay.
    win._gif_top_caption_edit.setText("Top Meme Text")
    win._gif_bottom_caption_edit.setText("Bottom Meme Text")
    win._gif_caption_size_combo.setCurrentIndex(2)  # Large (1.28)
    win._gif_caption_caps_check.setChecked(False)
    qt_app.processEvents()

    preview_caption = win._video_widget.caption_preview()
    assert preview_caption == GifCaption(
        top_text="Top Meme Text",
        bottom_text="Bottom Meme Text",
        size_scale=pytest.approx(1.28),
        uppercase=False,
    )

    # Painting both full-frame and cropped preview with captions should succeed cleanly.
    win._video_widget.resize(640, 360)
    win._video_widget.repaint()
    win._video_widget.set_crop_rect(CropRect(80, 40, 400, 240), emit=True)
    win._video_widget.repaint()

    # Switching to MP4 hides GIF caption preview; switching back restores it.
    win._select_format_pill("mp4")
    qt_app.processEvents()
    assert win._video_widget.caption_preview() is None

    win._select_format_pill("gif")
    qt_app.processEvents()
    assert win._video_widget.caption_preview() == preview_caption

    # Clean up state for subsequent tests.
    win._clear_crop_btn.click()
    win._gif_top_caption_edit.clear()
    win._gif_bottom_caption_edit.clear()
    win._gif_caption_size_combo.setCurrentIndex(1)
    win._gif_caption_caps_check.setChecked(True)
    qt_app.processEvents()
    assert win._video_widget.caption_preview() is None


def test_meme_studio_multi_file_layers_and_mode_toggle(
    qt_app: QApplication, main_window: MainWindow, tmp_path: Path
) -> None:
    meme_path = tmp_path / "template.png"
    meme_img = QImage(640, 480, QImage.Format.Format_RGBA8888)
    meme_img.fill(QColor("#1B54B8"))
    assert meme_img.save(str(meme_path), "PNG")

    face_path = tmp_path / "friend.png"
    face_img = QImage(200, 200, QImage.Format.Format_RGBA8888)
    face_img.fill(QColor("#E0284A"))
    assert face_img.save(str(face_path), "PNG")

    win = main_window
    win.load_media_files([meme_path, face_path])
    qt_app.processEvents()

    # Auto-enters Meme Studio (GIF Animation tab) with PNG output for still images.
    assert win._output_format == "gif"
    assert win._video_widget.is_meme_mode() is True
    assert win._meme_panel.export_format() == "png"
    assert win._output_edit.text().endswith(".png")
    assert len(win._meme_layers) == 1
    assert win._selected_layer_id == win._meme_layers[0].id

    # Toggle Circle mask, Overlay Crop, Top White Bar, and add a draggable Text Box.
    win._meme_panel._shape_buttons["circle"].click()
    win._meme_panel._ov_crop_btn.setChecked(True)
    assert win._video_widget.is_overlay_crop_mode() is True
    win._meme_panel._ov_crop_btn.setChecked(False)
    win._meme_panel._top_bar_edit.setText("Top Meme Header")
    win.add_text_overlay()
    qt_app.processEvents()
    assert len(win._meme_layers) == 2

    win._video_widget.resize(640, 480)
    win._video_widget.repaint()

    # Switching to MP4 Video tab disables Meme Studio canvas mode; switching back re-enables it.
    win._select_format_pill("mp4")
    qt_app.processEvents()
    assert win._video_widget.is_meme_mode() is False

    win._select_format_pill("gif")
    qt_app.processEvents()
    assert win._video_widget.is_meme_mode() is True

    # Delete selected text layer
    win._meme_panel._layer_del_btn.click()
    qt_app.processEvents()
    assert len(win._meme_layers) == 1

    # Clean up state
    win._meme_panel._top_bar_edit.clear()
    win._meme_layers.clear()
    win._video_widget.clear_meme_caches()
    win._select_format_pill("mp4")
    qt_app.processEvents()
