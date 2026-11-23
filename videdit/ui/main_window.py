"""Main desktop window for VidEdit (Porcelain Clockwork Commander edition)."""

from __future__ import annotations

import os
import subprocess
from dataclasses import replace
from pathlib import Path
from typing import Literal, Optional

from PySide6.QtCore import QSettings, QStandardPaths, QTimer, Qt, QUrl
from PySide6.QtGui import QDragEnterEvent, QDropEvent, QIcon, QImage, QImageReader, QKeySequence, QMouseEvent, QShortcut
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizeGrip,
    QSizePolicy,
    QSlider,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from videdit.exporter import ExportOptions, ExportWorker
from videdit.ffmpeg_tools import (
    AUDIO_EXTENSIONS,
    AudioInfo,
    AudioOverlay,
    CropRect,
    GifCaption,
    VideoInfo,
    calculate_mp4_encoding_plan,
    probe_audio,
    probe_video,
)
from videdit.meme_project import (
    ALL_MEDIA_EXTENSIONS,
    BaseLayer,
    MediaAssetInfo,
    MediaOverlayLayer,
    MemeComposition,
    OverlayLayer,
    build_media_overlay_layer,
    build_text_overlay_layer,
    classify_media_path,
    probe_media_asset,
)
from videdit.ui.chrome import CommandEntry, CommandPalette, DropStageWidget, TitleBar
from videdit.ui.icons import ClockworkSpinner, make_monoline_icon
from videdit.ui.meme_panel import MemeExportFormat, MemePanel
from videdit.ui.theme import ThemeName, ThemeTokens, build_stylesheet, get_theme
from videdit.ui.timeline import TrimTimelineWidget
from videdit.ui.video_preview import VideoPreviewWidget
from videdit.ui.youtube_drawer import YouTubeDrawerWidget
from videdit.win_taskbar import apply_window_taskbar_metadata

SUPPORTED_EXTENSIONS = set(ALL_MEDIA_EXTENSIONS)
_RESIZE_MARGIN = 6
_DEFAULT_OPEN_DIR_KEY = "paths/default_open_dir"


def resolve_fallback_open_dir() -> Path:
    """Return the OS Videos folder (or home) so Open Video never defaults to .venv/Scripts."""
    movies = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.MoviesLocation)
    if movies:
        movies_path = Path(movies)
        if movies_path.is_dir():
            return movies_path
    videos_home = Path.home() / "Videos"
    if videos_home.is_dir():
        return videos_home
    home = Path.home()
    return home if home.is_dir() else Path.cwd()


def format_timestamp_ms(ms: int) -> str:
    """Format milliseconds into MM:SS.cc (or HH:MM:SS.cc when >= 1 hour)."""
    ms = max(0, int(ms))
    total_seconds, rem_ms = divmod(ms, 1000)
    centis = rem_ms // 10
    minutes, seconds = divmod(total_seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{centis:02d}"
    return f"{minutes:02d}:{seconds:02d}.{centis:02d}"


def format_file_size(num_bytes: int) -> str:
    """Format byte count using decimal MB (consistent with Discord upload limits)."""
    if num_bytes >= 1_000_000:
        return f"{num_bytes / 1_000_000:.2f} MB"
    if num_bytes >= 1_000:
        return f"{num_bytes / 1_000:.1f} KB"
    return f"{num_bytes} B"


def _resolve_icon_path() -> Optional[Path]:
    candidate = Path(__file__).resolve().parent.parent.parent / "favicon.ico"
    return candidate if candidate.is_file() else None


class MainWindow(QMainWindow):
    """Primary VidEdit desktop interface with dual Alabaster/Midnight themes."""

    def __init__(
        self,
        initial_theme: Optional[ThemeName] = None,
        settings: Optional[QSettings] = None,
    ) -> None:
        super().__init__()
        self.setWindowTitle("VidEdit — Cut, GIF & Discord Compressor")
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Window)

        self._icon_path = _resolve_icon_path()
        if self._icon_path is not None:
            self.setWindowIcon(QIcon(str(self._icon_path)))

        self.resize(1160, 740)
        self.setMinimumSize(920, 640)
        self.setAcceptDrops(True)

        self._settings = settings if settings is not None else QSettings("iirosales", "VidEdit")
        saved_theme = str(self._settings.value("ui/theme", "dark"))
        theme_key: ThemeName = initial_theme if initial_theme in ("light", "dark") else (
            "light" if saved_theme == "light" else "dark"
        )
        self._theme: ThemeTokens = get_theme(theme_key)

        self._video_info: Optional[VideoInfo] = None
        self._base_asset: Optional[MediaAssetInfo] = None
        self._meme_layers: list[OverlayLayer] = []
        self._selected_layer_id: Optional[str] = None
        self._top_bar_text: str = ""
        self._active_crop: Optional[CropRect] = None
        self._duration_ms: int = 0
        self._output_format: Literal["mp4", "gif"] = "mp4"
        self._output_customized: bool = False
        self._worker: Optional[ExportWorker] = None
        self._last_exported_path: Optional[Path] = None
        self._taskbar_props_applied: bool = False
        self._yt_drawer_added_width: int = 0

        self._still_playing: bool = False
        self._still_pos_ms: int = 0
        self._still_timer = QTimer(self)
        self._still_timer.setInterval(33)
        self._still_timer.timeout.connect(self._on_still_timer_tick)

        self._mp4_audio_info: Optional[AudioInfo] = None
        self._preview_muted: bool = False

        self._player = QMediaPlayer(self)
        self._audio_output = QAudioOutput(self)
        self._audio_output.setVolume(0.8)
        self._player.setAudioOutput(self._audio_output)

        self._mp4_audio_player = QMediaPlayer(self)
        self._mp4_audio_output = QAudioOutput(self)
        self._mp4_audio_output.setVolume(0.8)
        self._mp4_audio_player.setAudioOutput(self._mp4_audio_output)

        self._build_ui()
        self._connect_signals()
        self._setup_shortcuts()
        self._update_controls_enabled(False)
        self._refresh_default_open_dir_ui()
        self.apply_theme(self._theme.name)

    def _build_ui(self) -> None:
        shell = QWidget()
        shell.setObjectName("MainSurface")
        self.setCentralWidget(shell)

        shell_layout = QVBoxLayout(shell)
        shell_layout.setContentsMargins(0, 0, 0, 0)
        shell_layout.setSpacing(0)

        # 36px custom window titlebar
        self._title_bar = TitleBar(shell, self._icon_path)
        shell_layout.addWidget(self._title_bar)

        # Workspace body
        body = QWidget()
        outer = QHBoxLayout(body)
        outer.setContentsMargins(14, 12, 14, 12)
        outer.setSpacing(14)

        # Left column: video preview + transport + timeline
        left_col = QVBoxLayout()
        left_col.setSpacing(10)
        outer.addLayout(left_col, stretch=1)

        # Top file bar
        header = QHBoxLayout()
        header.setSpacing(10)
        self._open_btn = QPushButton("Open Video...")
        self._default_folder_btn = QPushButton("Default Folder...")
        self._file_label = QLabel("Drop videos or meme images here...")
        self._file_label.setObjectName("MutedLabel")
        self._meta_badge = QLabel("NO SOURCE")
        self._meta_badge.setObjectName("BadgeLabel")
        header.addWidget(self._open_btn)
        header.addWidget(self._default_folder_btn)
        header.addWidget(self._file_label, stretch=1)
        header.addWidget(self._meta_badge)
        left_col.addLayout(header)

        # Video stage (horological damask dropzone <-> QVideoWidget)
        video_frame = QFrame()
        video_frame.setObjectName("VideoFrame")
        video_frame_layout = QVBoxLayout(video_frame)
        video_frame_layout.setContentsMargins(2, 2, 2, 2)

        self._video_stack = QStackedWidget()
        self._drop_stage = DropStageWidget()
        drop_layout = QVBoxLayout(self._drop_stage)
        drop_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        drop_title = QLabel("DRAG & DROP VIDEO OR MEME")
        drop_title.setObjectName("DropTitle")
        drop_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        drop_sub = QLabel("MP4 · MOV · MKV · GIF · PNG · JPG · WebP  (drop multiple to layer)")
        drop_sub.setObjectName("MutedLabel")
        drop_sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._drop_open_btn = QPushButton("Select Clip or Image")
        self._drop_open_btn.setProperty("primary", True)
        drop_layout.addWidget(drop_title)
        drop_layout.addWidget(drop_sub)
        drop_layout.addSpacing(10)
        drop_layout.addWidget(self._drop_open_btn, alignment=Qt.AlignmentFlag.AlignCenter)

        self._video_widget = VideoPreviewWidget()
        self._player.setVideoOutput(self._video_widget.videoSink())

        self._video_stack.addWidget(self._drop_stage)
        self._video_stack.addWidget(self._video_widget)
        video_frame_layout.addWidget(self._video_stack)
        left_col.addWidget(video_frame, stretch=1)

        # Transport + timeline card
        trim_card = QFrame()
        trim_card.setObjectName("Card")
        trim_layout = QVBoxLayout(trim_card)
        trim_layout.setContentsMargins(12, 10, 12, 12)
        trim_layout.setSpacing(8)

        transport = QHBoxLayout()
        transport.setSpacing(6)
        self._play_btn = QPushButton("Play")
        self._play_btn.setFixedWidth(82)
        self._step_back_btn = QPushButton("-0.1s")
        self._step_fwd_btn = QPushButton("+0.1s")
        self._time_label = QLabel("00:00.00 / 00:00.00")
        self._time_label.setObjectName("BadgeLabel")

        self._loop_check = QCheckBox("Loop Cut")
        self._loop_check.setChecked(True)

        self._mute_preview_btn = QPushButton("Mute")
        self._volume_slider = QSlider(Qt.Orientation.Horizontal)
        self._volume_slider.setRange(0, 100)
        self._volume_slider.setValue(80)
        self._volume_slider.setFixedWidth(88)

        transport.addWidget(self._play_btn)
        transport.addWidget(self._step_back_btn)
        transport.addWidget(self._step_fwd_btn)
        transport.addWidget(self._time_label)
        transport.addStretch(1)
        transport.addWidget(self._loop_check)
        transport.addWidget(self._mute_preview_btn)
        transport.addWidget(self._volume_slider)
        trim_layout.addLayout(transport)

        self._timeline = TrimTimelineWidget()
        trim_layout.addWidget(self._timeline)

        # Second trim bar for MP4 audio overlay (shown when audio is attached)
        self._audio_timeline_row = QWidget()
        aud_tl_layout = QVBoxLayout(self._audio_timeline_row)
        aud_tl_layout.setContentsMargins(0, 2, 0, 0)
        aud_tl_layout.setSpacing(4)

        aud_hdr = QHBoxLayout()
        aud_hdr.setSpacing(6)
        self._audio_bar_label = QLabel("Audio Track")
        self._audio_bar_label.setObjectName("MutedLabel")
        self._audio_reset_btn = QPushButton("Reset Audio")
        self._audio_in_badge = QLabel("In: 00:00.00")
        self._audio_in_badge.setObjectName("BadgeLabel")
        self._audio_out_badge = QLabel("Out: 00:00.00")
        self._audio_out_badge.setObjectName("BadgeLabel")
        self._audio_dur_badge = QLabel("Audio: 0.00s")
        self._audio_dur_badge.setObjectName("BadgeLabel")
        aud_hdr.addWidget(self._audio_bar_label, stretch=1)
        aud_hdr.addWidget(self._audio_reset_btn)
        aud_hdr.addWidget(self._audio_in_badge)
        aud_hdr.addWidget(self._audio_out_badge)
        aud_hdr.addWidget(self._audio_dur_badge)

        self._audio_timeline = TrimTimelineWidget()
        aud_tl_layout.addWidget(self._audio_timeline)
        aud_tl_layout.addLayout(aud_hdr)
        self._audio_timeline_row.setVisible(False)
        trim_layout.addWidget(self._audio_timeline_row)

        trim_actions = QHBoxLayout()
        trim_actions.setSpacing(8)
        self._set_in_btn = QPushButton("Set In (I)")
        self._set_out_btn = QPushButton("Set Out (O)")
        self._reset_trim_btn = QPushButton("Reset")
        self._crop_btn = QPushButton("Crop (C)")
        self._crop_btn.setCheckable(True)
        self._crop_btn.setProperty("pill", True)
        self._crop_btn.setToolTip("Draw a screenshot-style crop box directly on the video (C)")
        self._clear_crop_btn = QPushButton("Clear Crop")
        self._clear_crop_btn.setVisible(False)
        self._crop_badge = QLabel("")
        self._crop_badge.setObjectName("BadgeLabel")
        self._crop_badge.setVisible(False)
        self._in_badge = QLabel("In: 00:00.00")
        self._in_badge.setObjectName("BadgeLabel")
        self._out_badge = QLabel("Out: 00:00.00")
        self._out_badge.setObjectName("BadgeLabel")
        self._dur_badge = QLabel("Clip: 0.00s")
        self._dur_badge.setObjectName("BadgeLabel")

        trim_actions.addWidget(self._set_in_btn)
        trim_actions.addWidget(self._set_out_btn)
        trim_actions.addWidget(self._reset_trim_btn)
        trim_actions.addWidget(self._crop_btn)
        trim_actions.addWidget(self._clear_crop_btn)
        trim_actions.addStretch(1)
        trim_actions.addWidget(self._crop_badge)
        trim_actions.addWidget(self._in_badge)
        trim_actions.addWidget(self._out_badge)
        trim_actions.addWidget(self._dur_badge)
        trim_layout.addLayout(trim_actions)

        left_col.addWidget(trim_card)

        # Right sidebar: Export settings
        sidebar = QFrame()
        sidebar.setObjectName("SidebarCard")
        sidebar.setFixedWidth(342)
        side_layout = QVBoxLayout(sidebar)
        side_layout.setContentsMargins(14, 14, 14, 14)
        side_layout.setSpacing(12)

        title = QLabel("EXPORT SETTINGS")
        title.setObjectName("SectionTitle")
        side_layout.addWidget(title)

        # Format switch
        fmt_row = QHBoxLayout()
        fmt_row.setSpacing(8)
        self._fmt_group = QButtonGroup(self)
        self._fmt_mp4_btn = QPushButton("MP4 Video")
        self._fmt_mp4_btn.setCheckable(True)
        self._fmt_mp4_btn.setChecked(True)
        self._fmt_mp4_btn.setProperty("pill", True)
        self._fmt_gif_btn = QPushButton("GIF Animation")
        self._fmt_gif_btn.setCheckable(True)
        self._fmt_gif_btn.setProperty("pill", True)
        self._fmt_group.addButton(self._fmt_mp4_btn)
        self._fmt_group.addButton(self._fmt_gif_btn)
        fmt_row.addWidget(self._fmt_mp4_btn)
        fmt_row.addWidget(self._fmt_gif_btn)
        side_layout.addLayout(fmt_row)

        # Format-specific stack
        self._options_stack = QStackedWidget()
        self._options_stack.addWidget(self._build_mp4_panel())
        self._options_stack.addWidget(self._build_gif_panel())
        side_layout.addWidget(self._options_stack)

        # Output destination
        out_label = QLabel("Save As")
        out_label.setObjectName("MutedLabel")
        side_layout.addWidget(out_label)

        out_row = QHBoxLayout()
        out_row.setSpacing(6)
        self._output_edit = QLineEdit()
        self._output_edit.setPlaceholderText("Select output file...")
        self._browse_btn = QPushButton("Browse")
        out_row.addWidget(self._output_edit, stretch=1)
        out_row.addWidget(self._browse_btn)
        side_layout.addLayout(out_row)

        side_layout.addStretch(1)

        # Progress + Action buttons
        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setValue(0)
        self._progress_bar.setVisible(False)
        side_layout.addWidget(self._progress_bar)

        self._status_label = QLabel("")
        self._status_label.setObjectName("MutedLabel")
        self._status_label.setWordWrap(True)
        side_layout.addWidget(self._status_label)

        # Success banner + open folder
        self._result_widget = QWidget()
        result_layout = QHBoxLayout(self._result_widget)
        result_layout.setContentsMargins(0, 0, 0, 0)
        result_layout.setSpacing(6)
        self._result_label = QLabel("")
        self._result_label.setObjectName("SuccessBanner")
        self._result_label.setWordWrap(True)
        self._open_folder_btn = QPushButton("Open Folder")
        result_layout.addWidget(self._result_label, stretch=1)
        result_layout.addWidget(self._open_folder_btn)
        self._result_widget.setVisible(False)
        side_layout.addWidget(self._result_widget)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        self._export_btn = QPushButton("Export MP4")
        self._export_btn.setProperty("primary", True)
        self._cancel_btn = QPushButton("Cancel")
        self._cancel_btn.setProperty("danger", True)
        self._cancel_btn.setVisible(False)
        btn_row.addWidget(self._export_btn, stretch=1)
        btn_row.addWidget(self._cancel_btn)
        side_layout.addLayout(btn_row)

        outer.addWidget(sidebar)

        # Semi-hidden right-edge YouTube MP4 downloader toggle & collapsible drawer
        yt_wrap = QWidget()
        yt_wrap_layout = QHBoxLayout(yt_wrap)
        yt_wrap_layout.setContentsMargins(0, 0, 0, 0)
        yt_wrap_layout.setSpacing(8)

        self._yt_edge_btn = QPushButton("‹\nY\nT")
        self._yt_edge_btn.setObjectName("EdgeDrawerBtn")
        self._yt_edge_btn.setCheckable(True)
        self._yt_edge_btn.setFixedWidth(24)
        self._yt_edge_btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        self._yt_edge_btn.setToolTip("YouTube MP4 Downloader (Ctrl+Y)")

        self._yt_drawer = YouTubeDrawerWidget(self._settings)
        self._yt_drawer.setVisible(False)

        yt_wrap_layout.addWidget(self._yt_edge_btn)
        yt_wrap_layout.addWidget(self._yt_drawer)
        outer.addWidget(yt_wrap)

        shell_layout.addWidget(body, stretch=1)

        # 26px bottom status bar
        status_bar = QFrame()
        status_bar.setObjectName("StatusBar")
        status_bar.setFixedHeight(26)
        sb_layout = QHBoxLayout(status_bar)
        sb_layout.setContentsMargins(10, 0, 4, 0)
        sb_layout.setSpacing(8)

        self._dial_spinner = ClockworkSpinner()
        self._telemetry_label = QLabel("READY")
        self._telemetry_label.setObjectName("MutedLabel")
        self._cmd_palette_btn = QPushButton("Ctrl+K Commands")
        self._cmd_palette_btn.setObjectName("StatusBtn")
        self._theme_toggle_btn = QPushButton("Theme: Midnight Dark")
        self._theme_toggle_btn.setObjectName("StatusBtn")
        self._size_grip = QSizeGrip(status_bar)
        self._size_grip.setFixedSize(14, 14)

        sb_layout.addWidget(self._dial_spinner)
        sb_layout.addWidget(self._telemetry_label, stretch=1)
        sb_layout.addWidget(self._cmd_palette_btn)
        sb_layout.addWidget(self._theme_toggle_btn)
        sb_layout.addWidget(self._size_grip, alignment=Qt.AlignmentFlag.AlignBottom)

        shell_layout.addWidget(status_bar)

    def _build_mp4_panel(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 4, 4, 4)
        layout.setSpacing(8)

        size_lbl = QLabel("Target File Size (Discord Compression)")
        size_lbl.setObjectName("MutedLabel")
        layout.addWidget(size_lbl)

        self._size_group = QButtonGroup(self)
        self._size_buttons: dict[str, QPushButton] = {}
        presets = [
            ("original", "Original"),
            ("10", "10 MB"),
            ("15", "15 MB"),
            ("20", "20 MB"),
            ("custom", "Custom"),
        ]

        row1 = QHBoxLayout()
        row1.setSpacing(6)
        row2 = QHBoxLayout()
        row2.setSpacing(6)
        for idx, (key, text) in enumerate(presets):
            btn = QPushButton(text)
            btn.setCheckable(True)
            btn.setProperty("pill", True)
            if key == "10":
                btn.setToolTip("Discord free upload limit (10 MB)")
            if key == "original":
                btn.setChecked(True)
            self._size_group.addButton(btn)
            self._size_buttons[key] = btn
            if idx < 3:
                row1.addWidget(btn)
            else:
                row2.addWidget(btn)

        layout.addLayout(row1)
        layout.addLayout(row2)

        self._custom_mb_row = QWidget()
        custom_layout = QHBoxLayout(self._custom_mb_row)
        custom_layout.setContentsMargins(0, 0, 0, 0)
        custom_layout.addWidget(QLabel("Target MB:"))
        self._custom_mb_spin = QDoubleSpinBox()
        self._custom_mb_spin.setRange(1.0, 500.0)
        self._custom_mb_spin.setDecimals(1)
        self._custom_mb_spin.setSingleStep(1.0)
        self._custom_mb_spin.setValue(8.0)
        self._custom_mb_spin.setSuffix(" MB")
        custom_layout.addWidget(self._custom_mb_spin, stretch=1)
        self._custom_mb_row.setVisible(False)
        layout.addWidget(self._custom_mb_row)

        res_lbl = QLabel("Resolution")
        res_lbl.setObjectName("MutedLabel")
        layout.addWidget(res_lbl)

        self._mp4_res_combo = QComboBox()
        self._mp4_res_combo.setMinimumHeight(28)
        self._mp4_res_combo.addItem("Auto (Recommended for Target Size)", ("auto", None))
        self._mp4_res_combo.addItem("Original Resolution", ("fixed", None))
        self._mp4_res_combo.addItem("1080p Max", ("fixed", 1080))
        self._mp4_res_combo.addItem("720p Max", ("fixed", 720))
        self._mp4_res_combo.addItem("480p Max", ("fixed", 480))
        layout.addWidget(self._mp4_res_combo)

        self._mute_export_check = QCheckBox("Mute Video Audio (Preview & Export)")
        layout.addWidget(self._mute_export_check)

        # Optional Audio Overlay section
        audio_lbl = QLabel("Audio Overlay (Optional)")
        audio_lbl.setObjectName("MutedLabel")
        layout.addWidget(audio_lbl)

        audio_pick_row = QHBoxLayout()
        audio_pick_row.setSpacing(6)
        self._mp4_audio_btn = QPushButton("Add Audio...")
        self._mp4_audio_btn.setToolTip("Overlay an MP3/WAV/M4A/OGG or another video's audio onto this clip")
        self._mp4_audio_label = QLabel("None")
        self._mp4_audio_label.setObjectName("MutedLabel")
        self._mp4_audio_clear_btn = QPushButton("×")
        self._mp4_audio_clear_btn.setFixedSize(26, 26)
        self._mp4_audio_clear_btn.setProperty("danger", True)
        self._mp4_audio_clear_btn.setToolTip("Remove audio overlay")
        self._mp4_audio_clear_btn.setVisible(False)
        audio_pick_row.addWidget(self._mp4_audio_btn)
        audio_pick_row.addWidget(self._mp4_audio_label, stretch=1)
        audio_pick_row.addWidget(self._mp4_audio_clear_btn)
        layout.addLayout(audio_pick_row)

        self._mp4_audio_opts_box = QFrame()
        self._mp4_audio_opts_box.setObjectName("Card")
        ao_layout = QVBoxLayout(self._mp4_audio_opts_box)
        ao_layout.setContentsMargins(8, 8, 8, 8)
        ao_layout.setSpacing(6)

        self._mp4_audio_mode_combo = QComboBox()
        self._mp4_audio_mode_combo.setMinimumHeight(28)
        self._mp4_audio_mode_combo.addItem("Mix with Video Audio", "mix")
        self._mp4_audio_mode_combo.addItem("Replace Video Audio", "replace")
        ao_layout.addWidget(self._mp4_audio_mode_combo)

        vol_row = QHBoxLayout()
        vol_row.setSpacing(6)
        vol_lbl = QLabel("Overlay Vol")
        vol_lbl.setObjectName("MutedLabel")
        self._mp4_audio_vol_slider = QSlider(Qt.Orientation.Horizontal)
        self._mp4_audio_vol_slider.setRange(0, 200)
        self._mp4_audio_vol_slider.setValue(100)
        self._mp4_audio_vol_val = QLabel("100%")
        self._mp4_audio_vol_val.setObjectName("MutedLabel")
        vol_row.addWidget(vol_lbl)
        vol_row.addWidget(self._mp4_audio_vol_slider, stretch=1)
        vol_row.addWidget(self._mp4_audio_vol_val)
        ao_layout.addLayout(vol_row)

        offset_row = QHBoxLayout()
        offset_row.setSpacing(6)
        start_lbl = QLabel("Start at (Video)")
        start_lbl.setObjectName("MutedLabel")
        self._mp4_audio_offset_spin = QDoubleSpinBox()
        self._mp4_audio_offset_spin.setRange(0.0, 600.0)
        self._mp4_audio_offset_spin.setDecimals(1)
        self._mp4_audio_offset_spin.setSingleStep(0.5)
        self._mp4_audio_offset_spin.setSuffix(" s")
        offset_row.addWidget(start_lbl)
        offset_row.addWidget(self._mp4_audio_offset_spin, stretch=1)
        ao_layout.addLayout(offset_row)

        self._mp4_audio_loop_check = QCheckBox("Loop Audio to Fit Clip")
        ao_layout.addWidget(self._mp4_audio_loop_check)

        self._mp4_audio_opts_box.setVisible(False)
        layout.addWidget(self._mp4_audio_opts_box)

        self._mp4_estimate_label = QLabel("")
        self._mp4_estimate_label.setObjectName("MutedLabel")
        self._mp4_estimate_label.setWordWrap(True)
        layout.addWidget(self._mp4_estimate_label)
        layout.addStretch(1)

        scroll.setWidget(panel)
        return scroll

    def _build_gif_panel(self) -> QWidget:
        self._meme_panel = MemePanel()
        self._gif_fps_combo = self._meme_panel.gif_fps_combo
        self._gif_width_combo = self._meme_panel.gif_width_combo
        self._gif_limit_combo = self._meme_panel.gif_limit_combo
        self._gif_top_caption_edit = self._meme_panel.gif_top_caption_edit
        self._gif_bottom_caption_edit = self._meme_panel.gif_bottom_caption_edit
        self._gif_caption_size_combo = self._meme_panel.gif_caption_size_combo
        self._gif_caption_caps_check = self._meme_panel.gif_caption_caps_check
        return self._meme_panel

    def _connect_signals(self) -> None:
        self._title_bar.minimize_requested.connect(self.showMinimized)
        self._title_bar.maximize_requested.connect(self._toggle_maximize)
        self._title_bar.close_requested.connect(self.close)

        self._open_btn.clicked.connect(self._choose_input_file)
        self._default_folder_btn.clicked.connect(self._choose_default_open_folder)
        self._drop_open_btn.clicked.connect(self._choose_input_file)

        self._play_btn.clicked.connect(self._toggle_play_pause)
        self._step_back_btn.clicked.connect(lambda: self._step_by_ms(-100))
        self._step_fwd_btn.clicked.connect(lambda: self._step_by_ms(100))
        self._mute_preview_btn.clicked.connect(self._toggle_preview_mute)
        self._volume_slider.valueChanged.connect(self._on_preview_volume_changed)

        self._set_in_btn.clicked.connect(self._set_in_at_playhead)
        self._set_out_btn.clicked.connect(self._set_out_at_playhead)
        self._reset_trim_btn.clicked.connect(self._reset_trim)
        self._crop_btn.toggled.connect(self._on_crop_mode_toggled)
        self._clear_crop_btn.clicked.connect(self._clear_crop)

        self._video_widget.clicked.connect(self._toggle_play_pause)
        self._video_widget.crop_changed.connect(self._on_crop_changed)
        self._video_widget.crop_mode_exit_requested.connect(self._exit_crop_mode)
        self._video_widget.layer_selected.connect(self._on_canvas_layer_selected)
        self._video_widget.overlay_changed.connect(self._on_layer_updated)
        self._video_widget.overlay_crop_exit_requested.connect(lambda: self._set_overlay_crop_mode(False))

        self._timeline.seek_requested.connect(self._seek_to_ms)
        self._timeline.trim_changed.connect(self._on_trim_changed)

        self._audio_timeline.seek_requested.connect(self._seek_audio_to_ms)
        self._audio_timeline.trim_changed.connect(self._on_audio_trim_changed)
        self._audio_reset_btn.clicked.connect(self._reset_audio_trim)
        self._mp4_audio_player.durationChanged.connect(self._on_audio_player_duration_changed)

        self._player.positionChanged.connect(self._on_player_position_changed)
        self._player.durationChanged.connect(self._on_player_duration_changed)
        self._player.playbackStateChanged.connect(self._on_playback_state_changed)
        self._player.mediaStatusChanged.connect(self._on_media_status_changed)

        self._fmt_mp4_btn.clicked.connect(lambda: self._set_output_format("mp4"))
        self._fmt_gif_btn.clicked.connect(lambda: self._set_output_format("gif"))

        for btn in self._size_buttons.values():
            btn.clicked.connect(self._on_mp4_size_changed)
        self._custom_mb_spin.valueChanged.connect(self._on_mp4_size_changed)
        self._mp4_res_combo.currentIndexChanged.connect(self._refresh_estimate_and_output)
        self._mute_export_check.toggled.connect(self._on_mp4_audio_settings_changed)

        self._mp4_audio_btn.clicked.connect(self._choose_mp4_audio_file)
        self._mp4_audio_clear_btn.clicked.connect(self.clear_mp4_audio_overlay)
        self._mp4_audio_mode_combo.currentIndexChanged.connect(self._on_mp4_audio_settings_changed)
        self._mp4_audio_vol_slider.valueChanged.connect(self._on_mp4_audio_vol_changed)
        self._mp4_audio_offset_spin.valueChanged.connect(self._on_mp4_audio_settings_changed)
        self._mp4_audio_loop_check.toggled.connect(self._on_mp4_audio_settings_changed)

        self._meme_panel.add_media_requested.connect(self._choose_overlay_files)
        self._meme_panel.add_text_requested.connect(self.add_text_overlay)
        self._meme_panel.layer_selected.connect(self._on_panel_layer_selected)
        self._meme_panel.layer_updated.connect(self._on_layer_updated)
        self._meme_panel.layer_reordered.connect(self._on_layer_reordered)
        self._meme_panel.layer_deleted.connect(self._on_layer_deleted)
        self._meme_panel.overlay_crop_toggled.connect(self._set_overlay_crop_mode)
        self._meme_panel.top_bar_changed.connect(self._on_top_bar_changed)
        self._meme_panel.classic_caption_changed.connect(self._sync_caption_preview)
        self._meme_panel.meme_format_changed.connect(self._on_meme_export_format_changed)
        self._meme_panel.settings_changed.connect(self._on_meme_settings_changed)

        self._browse_btn.clicked.connect(self._choose_output_file)
        self._output_edit.textEdited.connect(self._on_output_edited)
        self._export_btn.clicked.connect(self._start_export)
        self._cancel_btn.clicked.connect(self._cancel_export)
        self._open_folder_btn.clicked.connect(self._open_output_folder)

        self._cmd_palette_btn.clicked.connect(self.open_command_palette)
        self._theme_toggle_btn.clicked.connect(self.toggle_theme)

        self._yt_edge_btn.toggled.connect(self._set_youtube_drawer_open)
        self._yt_drawer.close_requested.connect(lambda: self._set_youtube_drawer_open(False))
        self._yt_drawer.load_video_requested.connect(self._load_downloaded_video)
        self._yt_drawer.download_state_changed.connect(self._on_yt_download_state_changed)
        self._yt_drawer.progress_summary.connect(self._on_yt_progress)

    def _setup_shortcuts(self) -> None:
        self._bind_shortcut("Space", self._toggle_play_pause)
        self._bind_shortcut("I", self._set_in_at_playhead)
        self._bind_shortcut("O", self._set_out_at_playhead)
        self._bind_shortcut("C", self._toggle_crop_mode)
        self._bind_shortcut("Escape", self._handle_escape)
        self._bind_shortcut("Delete", self._delete_selected_layer_shortcut)
        self._bind_shortcut("Left", lambda: self._step_by_ms(-100))
        self._bind_shortcut("Right", lambda: self._step_by_ms(100))
        self._bind_shortcut("Shift+Left", lambda: self._step_by_ms(-1000))
        self._bind_shortcut("Shift+Right", lambda: self._step_by_ms(1000))
        palette_sc = QShortcut(QKeySequence("Ctrl+K"), self)
        palette_sc.activated.connect(self.open_command_palette)
        yt_sc = QShortcut(QKeySequence("Ctrl+Y"), self)
        yt_sc.activated.connect(self.toggle_youtube_drawer)

    def _bind_shortcut(self, key: str, callback) -> None:
        shortcut = QShortcut(QKeySequence(key), self)

        def _handler() -> None:
            focus = QApplication.focusWidget()
            if isinstance(focus, (QLineEdit, QDoubleSpinBox)):
                return
            callback()

        shortcut.activated.connect(_handler)

    def apply_theme(self, theme_name: ThemeName) -> None:
        """Apply Alabaster Light or Midnight Dark theme across the entire window."""
        self._theme = get_theme(theme_name)
        self._settings.setValue("ui/theme", self._theme.name)

        qss = build_stylesheet(self._theme)
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(qss)
        else:
            self.setStyleSheet(qss)

        self._timeline.apply_theme(self._theme)
        self._audio_timeline.apply_theme(self._theme)
        self._drop_stage.apply_theme(self._theme)
        self._video_widget.apply_theme(self._theme)
        self._meme_panel.apply_theme(self._theme)
        self._yt_drawer.apply_theme(self._theme)
        self._dial_spinner.set_colors(self._theme.color_secondary, self._theme.accent_cobalt)
        self._theme_toggle_btn.setText(f"Theme: {self._theme.display_name}")
        self._refresh_icons()

    def toggle_theme(self) -> None:
        """Switch between Midnight Dark and Alabaster Light."""
        next_theme: ThemeName = "light" if self._theme.name == "dark" else "dark"
        self.apply_theme(next_theme)

    def _refresh_icons(self) -> None:
        c = self._theme.text_primary
        self._open_btn.setIcon(make_monoline_icon("open", c))
        self._step_back_btn.setIcon(make_monoline_icon("step_back", c))
        self._step_fwd_btn.setIcon(make_monoline_icon("step_fwd", c))
        crop_icon_color = self._theme.color_primary_text if self._crop_btn.isChecked() else c
        self._crop_btn.setIcon(make_monoline_icon("crop", crop_icon_color))
        is_playing = (
            self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
            or self._still_playing
        )
        self._play_btn.setIcon(make_monoline_icon("pause" if is_playing else "play", c))
        is_muted = self._preview_muted
        self._mute_preview_btn.setIcon(make_monoline_icon("muted" if is_muted else "volume", c))
        theme_icon = "theme_light" if self._theme.name == "light" else "theme_dark"
        self._theme_toggle_btn.setIcon(make_monoline_icon(theme_icon, self._theme.text_muted, size=14))

    def build_command_entries(self) -> list[CommandEntry]:
        """Return commands exposed in the Ctrl+K Command Palette."""
        return [
            CommandEntry("Open Video or Image...", "Click", self._choose_input_file),
            CommandEntry("Set Default Open Folder...", "", self._choose_default_open_folder),
            CommandEntry("Reset Default Open Folder", "", self._reset_default_open_folder),
            CommandEntry("Add MP4 Audio Overlay...", "", self._choose_mp4_audio_file),
            CommandEntry("Clear MP4 Audio Overlay", "", self.clear_mp4_audio_overlay),
            CommandEntry("Add Overlay Image / Video...", "", self._choose_overlay_files),
            CommandEntry("Add Meme Text Box", "", self.add_text_overlay),
            CommandEntry("Play / Pause Preview", "Space", self._toggle_play_pause),
            CommandEntry("Set In Point at Playhead", "I", self._set_in_at_playhead),
            CommandEntry("Set Out Point at Playhead", "O", self._set_out_at_playhead),
            CommandEntry("Reset Trim Range", "", self._reset_trim),
            CommandEntry("Toggle Video Crop Mode", "C", self._toggle_crop_mode),
            CommandEntry("Clear Video Crop", "", self._clear_crop),
            CommandEntry("Format: MP4 Video", "", lambda: self._select_format_pill("mp4")),
            CommandEntry("Format: GIF / Meme Studio", "", lambda: self._select_format_pill("gif")),
            CommandEntry("MP4 Preset: Original Quality", "", lambda: self._select_mp4_preset("original")),
            CommandEntry("MP4 Preset: 10 MB (Discord)", "", lambda: self._select_mp4_preset("10")),
            CommandEntry("MP4 Preset: 15 MB", "", lambda: self._select_mp4_preset("15")),
            CommandEntry("MP4 Preset: 20 MB", "", lambda: self._select_mp4_preset("20")),
            CommandEntry("Export Current Clip / Meme", "", self._start_export),
            CommandEntry("Toggle YouTube MP4 Downloader", "Ctrl+Y", self.toggle_youtube_drawer),
            CommandEntry("Toggle Light / Dark Theme", "", self.toggle_theme),
        ]

    def open_command_palette(self) -> None:
        """Show the floating Ctrl+K command dial centered over the window."""
        palette = CommandPalette(self, self.build_command_entries(), self._theme)
        geo = self.geometry()
        palette.move(
            geo.center().x() - palette.width() // 2,
            geo.top() + 80,
        )
        palette.exec()

    def _select_format_pill(self, fmt: Literal["mp4", "gif"]) -> None:
        self._fmt_mp4_btn.setChecked(fmt == "mp4")
        self._fmt_gif_btn.setChecked(fmt == "gif")
        self._set_output_format(fmt)

    def _select_mp4_preset(self, key: str) -> None:
        self._select_format_pill("mp4")
        if key in self._size_buttons:
            self._size_buttons[key].setChecked(True)
            self._on_mp4_size_changed()

    def _toggle_maximize(self) -> None:
        if self.isMaximized():
            self.showNormal()
            self._title_bar.set_maximized_state(False)
        else:
            self.showMaximized()
            self._title_bar.set_maximized_state(True)

    def _edges_at(self, pos) -> Qt.Edge:
        edges = Qt.Edge(0)
        if pos.x() <= _RESIZE_MARGIN:
            edges |= Qt.Edge.LeftEdge
        elif pos.x() >= self.width() - _RESIZE_MARGIN:
            edges |= Qt.Edge.RightEdge
        if pos.y() <= _RESIZE_MARGIN:
            edges |= Qt.Edge.TopEdge
        elif pos.y() >= self.height() - _RESIZE_MARGIN:
            edges |= Qt.Edge.BottomEdge
        return edges

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and not self.isMaximized():
            edges = self._edges_at(event.position().toPoint())
            if edges != Qt.Edge(0) and self.windowHandle() is not None:
                self.windowHandle().startSystemResize(edges)
                event.accept()
                return
        super().mousePressEvent(event)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                if url.isLocalFile():
                    ext = Path(url.toLocalFile()).suffix.lower()
                    if ext in SUPPORTED_EXTENSIONS or (
                        ext in AUDIO_EXTENSIONS and self._video_info is not None
                    ):
                        event.acceptProposedAction()
                        return
        event.ignore()

    def dropEvent(self, event: QDropEvent) -> None:
        media_paths: list[Path] = []
        audio_paths: list[Path] = []
        for url in event.mimeData().urls():
            if url.isLocalFile():
                p = Path(url.toLocalFile())
                ext = p.suffix.lower()
                if ext in AUDIO_EXTENSIONS:
                    audio_paths.append(p)
                elif ext in SUPPORTED_EXTENSIONS:
                    media_paths.append(p)
        if audio_paths and not media_paths and self._video_info is not None:
            event.acceptProposedAction()
            self._select_format_pill("mp4")
            self.load_mp4_audio_overlay(audio_paths[0])
            return
        if not media_paths:
            return
        event.acceptProposedAction()
        as_overlays = self._output_format == "gif" and self._base_asset is not None
        self.load_media_files(media_paths, add_as_overlays=as_overlays)
        if audio_paths and self._video_info is not None and self._output_format == "mp4":
            self.load_mp4_audio_overlay(audio_paths[0])

    def default_open_dir(self) -> Path:
        """Return the configured default folder for Open Video, or the OS fallback."""
        raw = str(self._settings.value(_DEFAULT_OPEN_DIR_KEY, "") or "").strip()
        if raw:
            candidate = Path(raw)
            if candidate.is_dir():
                return candidate
        return resolve_fallback_open_dir()

    def set_default_open_dir(self, folder: Optional[Path]) -> Path:
        """Persist a user-chosen default open folder (or clear to restore fallback)."""
        if folder is None:
            self._settings.remove(_DEFAULT_OPEN_DIR_KEY)
        else:
            resolved = Path(folder)
            if not resolved.is_dir():
                raise ValueError(f"Default folder does not exist: {resolved}")
            self._settings.setValue(_DEFAULT_OPEN_DIR_KEY, str(resolved))
        self._settings.sync()
        active = self.default_open_dir()
        self._refresh_default_open_dir_ui(active)
        self._yt_drawer.refresh_default_folder()
        return active

    def _refresh_default_open_dir_ui(self, active: Optional[Path] = None) -> None:
        folder = active if active is not None else self.default_open_dir()
        self._default_folder_btn.setToolTip(
            f"Default Open Video folder: {folder}\nClick to change."
        )
        self._open_btn.setToolTip(f"Open a video or image from {folder}")

    def _choose_default_open_folder(self) -> None:
        folder_str = QFileDialog.getExistingDirectory(
            self,
            "Select Default Video Folder",
            str(self.default_open_dir()),
        )
        if folder_str:
            chosen = self.set_default_open_dir(Path(folder_str))
            self._telemetry_label.setText(f"DEFAULT FOLDER · {chosen}")

    def _reset_default_open_folder(self) -> None:
        active = self.set_default_open_dir(None)
        self._telemetry_label.setText(f"DEFAULT FOLDER · {active}")

    def _choose_input_file(self) -> None:
        path_str, _ = QFileDialog.getOpenFileName(
            self,
            "Open Video or Image",
            str(self.default_open_dir()),
            "Media Files (*.mp4 *.mov *.mkv *.webm *.avi *.m4v *.gif *.png *.jpg *.jpeg *.webp *.bmp);;All Files (*)",
        )
        if path_str:
            self.load_media_files([Path(path_str)], add_as_overlays=False)

    def _choose_overlay_files(self) -> None:
        if self._base_asset is None:
            self._choose_input_file()
            return
        paths_str, _ = QFileDialog.getOpenFileNames(
            self,
            "Add Overlay Image(s) or Video(s)",
            str(self.default_open_dir()),
            "Media Files (*.png *.jpg *.jpeg *.webp *.bmp *.gif *.mp4 *.mov *.mkv *.webm *.avi *.m4v);;All Files (*)",
        )
        if paths_str:
            self.add_overlay_files([Path(p) for p in paths_str])

    def load_media_files(self, paths: list[Path], add_as_overlays: bool = False) -> None:
        """Load one or more image/GIF/video files into the editor or Meme Studio."""
        valid = [p for p in paths if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS]
        if not valid:
            return
        if self._worker is not None and self._worker.isRunning():
            QMessageBox.information(self, "Export Running", "Wait for or cancel the active export first.")
            return

        if add_as_overlays and self._base_asset is not None:
            self._select_format_pill("gif")
            self.add_overlay_files(valid)
            return

        if len(valid) == 1 and classify_media_path(valid[0]) != "image":
            self.load_video(valid[0])
            return

        # Pick first video/gif if mixed with images, otherwise first image as Base.
        base_idx = next((i for i, p in enumerate(valid) if classify_media_path(p) != "image"), 0)
        base_path = valid[base_idx]
        overlay_paths = [p for i, p in enumerate(valid) if i != base_idx]

        if classify_media_path(base_path) == "image":
            self.load_image(base_path)
        else:
            self.load_video(base_path)

        self._select_format_pill("gif")
        if overlay_paths:
            self.add_overlay_files(overlay_paths)

    def load_image(self, path: Path) -> None:
        """Load a static image (PNG, JPG, WebP, BMP) as the base canvas."""
        if self._worker is not None and self._worker.isRunning():
            QMessageBox.information(self, "Export Running", "Wait for or cancel the active export first.")
            return

        self._stop_still_playback()
        self._player.stop()
        self.clear_mp4_audio_overlay()
        self._player.setSource(QUrl())
        self.clear_mp4_audio_overlay()

        try:
            asset = probe_media_asset(path)
            reader = QImageReader(str(path))
            reader.setAutoTransform(True)
            img = reader.read()
            if img.isNull():
                img = QImage(str(path))
            if img.isNull():
                raise ValueError(f"Could not decode image: {path.name}")
        except Exception as exc:
            QMessageBox.critical(self, "Could Not Open Image", str(exc))
            return

        self._base_asset = asset
        self._video_info = VideoInfo(
            path=path,
            duration_sec=0.0,
            width=img.width(),
            height=img.height(),
            fps=30.0,
            has_audio=False,
            file_size_bytes=asset.file_size_bytes,
            video_codec="image",
        )
        self._meme_layers.clear()
        self._selected_layer_id = None
        self._video_widget.clear_meme_caches()
        self._active_crop = None
        self._duration_ms = 0
        self._output_customized = False
        self._result_widget.setVisible(False)
        self._status_label.setText("")
        self._crop_btn.setChecked(False)
        self._video_widget.set_crop_mode(False)
        self._video_widget.set_source_size(img.width(), img.height())
        self._video_widget.set_base_image(img)
        self._on_crop_changed(None)

        self._file_label.setText(path.name)
        self._file_label.setToolTip(str(path))
        self._title_bar.set_document_title(path.name)
        size_str = format_file_size(asset.file_size_bytes)
        meta_text = f"{img.width()}×{img.height()} · IMG · {size_str}"
        self._meta_badge.setText(meta_text)
        self._telemetry_label.setText(f"LOADED · {meta_text}")

        self._timeline.set_duration(0)
        self._in_badge.setText("In: 00:00.00")
        self._out_badge.setText("Out: 00:00.00")
        self._dur_badge.setText("Still Image")
        self._update_time_label(0)

        self._video_stack.setCurrentIndex(1)
        self._update_controls_enabled(True)
        self._meme_panel.set_export_format("png", emit=False)
        self._select_format_pill("gif")
        self._sync_meme_studio_state()
        self._refresh_default_output_path()

    def load_video(self, path: Path) -> None:
        """Probe and load a video (or image) file into the preview player and timeline."""
        if classify_media_path(path) == "image":
            self.load_image(path)
            return
        if self._worker is not None and self._worker.isRunning():
            QMessageBox.information(self, "Export Running", "Wait for or cancel the active export first.")
            return

        self._stop_still_playback()
        self._player.stop()
        self.clear_mp4_audio_overlay()
        try:
            info = probe_video(path)
            asset = MediaAssetInfo(
                path=path,
                kind=classify_media_path(path),
                width=info.width,
                height=info.height,
                duration_sec=info.duration_sec if info.duration_sec > 0 else 2.0,
                fps=info.fps,
                has_audio=(info.has_audio and classify_media_path(path) == "video"),
                file_size_bytes=info.file_size_bytes,
            )
        except Exception as exc:
            QMessageBox.critical(self, "Could Not Open Video", str(exc))
            return

        self._base_asset = asset
        self._video_info = info
        self._meme_layers.clear()
        self._selected_layer_id = None
        self._video_widget.clear_meme_caches()
        self._active_crop = None
        self._duration_ms = max(200, int(round(asset.duration_sec * 1000)))
        self._output_customized = False
        self._result_widget.setVisible(False)
        self._status_label.setText("")
        self._crop_btn.setChecked(False)
        self._video_widget.set_crop_mode(False)
        self._video_widget.set_base_image(None)
        self._video_widget.set_source_size(info.width, info.height)
        self._on_crop_changed(None)

        self._file_label.setText(path.name)
        self._file_label.setToolTip(str(path))
        self._title_bar.set_document_title(path.name)
        size_str = format_file_size(info.file_size_bytes)
        meta_text = f"{info.width}x{info.height} · {info.fps:.0f}fps · {size_str}"
        self._meta_badge.setText(meta_text)
        self._telemetry_label.setText(f"LOADED · {meta_text}")

        self._timeline.set_duration(self._duration_ms)
        self._on_trim_changed(0, self._duration_ms)

        self._video_stack.setCurrentIndex(1)
        self._player.setSource(QUrl.fromLocalFile(str(path)))
        self._player.pause()
        self._update_controls_enabled(True)
        if self._meme_panel.export_format() in ("png", "jpg"):
            self._meme_panel.set_export_format("gif", emit=False)
        self._sync_meme_studio_state()
        self._refresh_default_output_path()
        self._refresh_estimate_and_output()

    def add_overlay_files(self, paths: list[Path]) -> None:
        """Probe and append one or more image/GIF/video files as Meme Studio overlays."""
        if self._base_asset is None:
            self.load_media_files(paths, add_as_overlays=False)
            return

        stage_w, stage_h = self._current_stage_size()
        added_any = False
        for p in paths:
            if not p.is_file() or p.suffix.lower() not in SUPPORTED_EXTENSIONS:
                continue
            try:
                asset = probe_media_asset(p)
            except Exception as exc:
                QMessageBox.warning(self, "Could Not Add Overlay", f"{p.name}: {exc}")
                continue
            layer = build_media_overlay_layer(
                asset,
                stage_w=stage_w,
                stage_h=stage_h,
                index_offset=len(self._meme_layers),
            )
            self._meme_layers.append(layer)
            self._selected_layer_id = layer.id
            added_any = True

        if added_any:
            self._refresh_still_animation_timeline()
            self._sync_meme_studio_state()
            self._refresh_default_output_path()

    def add_text_overlay(self) -> None:
        """Append a new draggable TextOverlayLayer to the Meme Studio canvas."""
        if self._base_asset is None:
            return
        self._select_format_pill("gif")
        layer = build_text_overlay_layer(text="MEME TEXT", index_offset=len(self._meme_layers))
        self._meme_layers.append(layer)
        self._selected_layer_id = layer.id
        self._sync_meme_studio_state()

    def _current_stage_size(self) -> tuple[int, int]:
        if self._active_crop is not None:
            return self._active_crop.width, self._active_crop.height
        if self._base_asset is not None:
            return self._base_asset.width, self._base_asset.height
        return 1280, 720

    def _build_meme_composition(self) -> Optional[MemeComposition]:
        if self._base_asset is None:
            return None
        start_sec = self._timeline.in_ms() / 1000.0 if self._base_asset.kind != "image" else 0.0
        end_sec = self._timeline.out_ms() / 1000.0 if self._base_asset.kind != "image" else 0.0
        base = BaseLayer(
            path=self._base_asset.path,
            kind=self._base_asset.kind,
            width=self._base_asset.width,
            height=self._base_asset.height,
            duration_sec=self._base_asset.duration_sec,
            has_audio=self._base_asset.has_audio,
            fps=self._base_asset.fps,
            file_size_bytes=self._base_asset.file_size_bytes,
            crop=self._active_crop,
            start_sec=start_sec,
            end_sec=end_sec,
            top_bar_text=self._top_bar_text,
        )
        return MemeComposition(
            base=base,
            overlays=tuple(self._meme_layers),
            still_duration_sec=self._meme_panel.still_duration_sec(),
            classic_caption=self._current_gif_caption(),
        )

    def _sync_meme_studio_state(self) -> None:
        comp = self._build_meme_composition()
        is_anim = comp.has_animation() if comp is not None else False
        base_kind = self._base_asset.kind if self._base_asset is not None else "video"
        self._video_widget.set_meme_layers(self._meme_layers)
        self._video_widget.set_selected_layer(self._selected_layer_id)
        self._video_widget.set_top_bar(self._top_bar_text)
        self._meme_panel.set_layers(self._meme_layers, self._selected_layer_id, is_anim, base_kind)

        # If a static base just gained a video/GIF overlay, auto-switch from PNG to GIF.
        if (
            self._base_asset is not None
            and self._base_asset.kind == "image"
            and is_anim
            and self._meme_panel.export_format() in ("png", "jpg")
        ):
            self._meme_panel.set_export_format("gif", emit=False)
            self._refresh_default_output_path()

    def _refresh_still_animation_timeline(self) -> None:
        if self._base_asset is None or self._base_asset.kind != "image":
            return
        comp = self._build_meme_composition()
        if comp is not None and comp.has_animation():
            dur_ms = max(500, int(round(comp.effective_duration_sec() * 1000)))
            self._duration_ms = dur_ms
            self._timeline.set_duration(dur_ms)
            self._update_time_label(self._still_pos_ms)
            self._dur_badge.setText(f"Clip: {dur_ms / 1000.0:.2f}s")
            self._update_controls_enabled(True)
        else:
            self._stop_still_playback()
            self._duration_ms = 0
            self._timeline.set_duration(0)
            self._dur_badge.setText("Still Image")
            self._update_controls_enabled(True)

    def _on_canvas_layer_selected(self, layer_id: str) -> None:
        self._selected_layer_id = layer_id or None
        if not self._selected_layer_id:
            self._meme_panel.set_overlay_crop_checked(False)
        self._sync_meme_studio_state()

    def _on_panel_layer_selected(self, layer_id: str) -> None:
        self._selected_layer_id = layer_id or None
        self._video_widget.set_overlay_crop_mode(False)
        self._meme_panel.set_overlay_crop_checked(False)
        self._sync_meme_studio_state()

    def _on_layer_updated(self, updated_layer: OverlayLayer) -> None:
        for idx, existing in enumerate(self._meme_layers):
            if existing.id == updated_layer.id:
                self._meme_layers[idx] = updated_layer
                break
        self._refresh_still_animation_timeline()
        self._sync_meme_studio_state()

    def _on_layer_reordered(self, layer_id: str, delta: int) -> None:
        idx = next((i for i, l in enumerate(self._meme_layers) if l.id == layer_id), -1)
        if idx < 0:
            return
        new_idx = max(0, min(len(self._meme_layers) - 1, idx + delta))
        if new_idx != idx:
            item = self._meme_layers.pop(idx)
            self._meme_layers.insert(new_idx, item)
            self._sync_meme_studio_state()

    def _on_layer_deleted(self, layer_id: str) -> None:
        self._meme_layers = [l for l in self._meme_layers if l.id != layer_id]
        if self._selected_layer_id == layer_id:
            self._selected_layer_id = self._meme_layers[-1].id if self._meme_layers else None
            self._video_widget.set_overlay_crop_mode(False)
            self._meme_panel.set_overlay_crop_checked(False)
        self._refresh_still_animation_timeline()
        self._sync_meme_studio_state()

    def _delete_selected_layer_shortcut(self) -> None:
        if self._output_format == "gif" and self._selected_layer_id:
            self._on_layer_deleted(self._selected_layer_id)

    def _set_overlay_crop_mode(self, enabled: bool) -> None:
        if enabled and self._crop_btn.isChecked():
            self._crop_btn.setChecked(False)
        self._video_widget.set_overlay_crop_mode(enabled)
        self._meme_panel.set_overlay_crop_checked(self._video_widget.is_overlay_crop_mode())

    def _on_top_bar_changed(self, text: str) -> None:
        self._top_bar_text = text
        self._video_widget.set_top_bar(text)

    def _on_meme_export_format_changed(self, fmt: str) -> None:
        self._update_export_button_label()
        self._refresh_default_output_path()

    def _on_meme_settings_changed(self) -> None:
        self._refresh_still_animation_timeline()
        self._refresh_default_output_path()

    def _handle_escape(self) -> None:
        if self._video_widget.is_overlay_crop_mode():
            self._set_overlay_crop_mode(False)
        else:
            self._exit_crop_mode()

    def _update_controls_enabled(self, enabled: bool) -> None:
        is_image_only = (
            self._base_asset is not None
            and self._base_asset.kind == "image"
            and not any(
                isinstance(l, MediaOverlayLayer) and l.visible and l.kind != "image"
                for l in self._meme_layers
            )
        )
        can_play = enabled and not is_image_only
        can_trim = enabled and (self._base_asset is None or self._base_asset.kind != "image")

        for widget in (self._play_btn, self._step_back_btn, self._step_fwd_btn):
            widget.setEnabled(can_play)
        for widget in (self._set_in_btn, self._set_out_btn, self._reset_trim_btn):
            widget.setEnabled(can_trim)
        for widget in (self._crop_btn, self._clear_crop_btn, self._export_btn, self._browse_btn, self._output_edit):
            widget.setEnabled(enabled)

    def _stop_still_playback(self) -> None:
        self._still_playing = False
        self._still_timer.stop()
        self._still_pos_ms = 0

    def _on_still_timer_tick(self) -> None:
        if not self._still_playing or self._duration_ms <= 0:
            return
        self._still_pos_ms += self._still_timer.interval()
        if self._still_pos_ms >= self._duration_ms:
            if self._loop_check.isChecked():
                self._still_pos_ms = 0
            else:
                self._still_pos_ms = self._duration_ms
                self._still_playing = False
                self._still_timer.stop()
                self._video_widget.sync_overlay_playback(False, self._still_pos_ms)
                self._refresh_icons()
                self._play_btn.setText("Play")
        self._timeline.set_position(self._still_pos_ms)
        self._update_time_label(self._still_pos_ms)
        self._video_widget.set_playhead_sec(self._still_pos_ms / 1000.0)

    def _choose_mp4_audio_file(self) -> None:
        if self._video_info is None:
            QMessageBox.information(self, "No Video Loaded", "Open a video first before adding an audio overlay.")
            return
        path_str, _ = QFileDialog.getOpenFileName(
            self,
            "Select Audio or Video Track",
            str(self.default_open_dir()),
            "Audio & Video (*.mp3 *.wav *.m4a *.aac *.ogg *.flac *.opus *.wma *.mp4 *.mov *.mkv *.webm);;All Files (*)",
        )
        if path_str:
            self.load_mp4_audio_overlay(Path(path_str))

    def load_mp4_audio_overlay(self, path: Path) -> None:
        """Probe and attach an audio file (or video audio track) to the MP4 export."""
        if self._video_info is None:
            return
        try:
            ainfo = probe_audio(path)
        except Exception as exc:
            QMessageBox.critical(self, "Could Not Open Audio", str(exc))
            return

        self._mp4_audio_info = ainfo
        self._mp4_audio_player.stop()
        self._mp4_audio_player.setSource(QUrl.fromLocalFile(str(path)))
        self._mp4_audio_player.pause()

        aud_ms = max(200, int(round(ainfo.duration_sec * 1000))) if ainfo.duration_sec > 0 else 5000
        self._audio_timeline.set_duration(aud_ms)
        self._on_audio_trim_changed(0, aud_ms)
        self._audio_bar_label.setText(f"Audio: {path.name}")
        self._audio_timeline_row.setVisible(self._output_format == "mp4")

        dur_label = f"{ainfo.duration_sec:.1f}s" if ainfo.duration_sec > 0 else "audio"
        self._mp4_audio_label.setText(f"{path.name} ({dur_label})")
        self._mp4_audio_label.setToolTip(str(path))
        self._mp4_audio_clear_btn.setVisible(True)
        self._mp4_audio_opts_box.setVisible(True)
        if not self._video_info.has_audio:
            idx = self._mp4_audio_mode_combo.findData("replace")
            if idx >= 0:
                self._mp4_audio_mode_combo.setCurrentIndex(idx)
        self._refresh_estimate_and_output()
        self._sync_mp4_audio_preview(
            self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState,
            self._player.position(),
        )

    def clear_mp4_audio_overlay(self) -> None:
        """Detach any active MP4 audio overlay and restore normal preview audio."""
        self._mp4_audio_info = None
        self._mp4_audio_player.stop()
        self._mp4_audio_player.setSource(QUrl())
        self._audio_timeline.set_duration(0)
        self._audio_timeline_row.setVisible(False)
        self._mp4_audio_label.setText("None")
        self._mp4_audio_label.setToolTip("")
        self._mp4_audio_clear_btn.setVisible(False)
        self._mp4_audio_opts_box.setVisible(False)
        self._audio_output.setMuted(self._preview_muted or self._mute_export_check.isChecked())
        self._refresh_estimate_and_output()

    def _on_audio_player_duration_changed(self, duration_ms: int) -> None:
        if duration_ms > 0 and self._mp4_audio_info is not None and self._mp4_audio_info.duration_sec <= 0:
            self._audio_timeline.set_duration(duration_ms)
            self._on_audio_trim_changed(0, duration_ms)

    def _on_audio_trim_changed(self, in_ms: int, out_ms: int) -> None:
        self._audio_in_badge.setText(f"In: {format_timestamp_ms(in_ms)}")
        self._audio_out_badge.setText(f"Out: {format_timestamp_ms(out_ms)}")
        dur_sec = max(0.0, (out_ms - in_ms) / 1000.0)
        self._audio_dur_badge.setText(f"Audio: {dur_sec:.2f}s")
        self._sync_mp4_audio_preview(
            self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState,
            self._player.position(),
        )

    def _seek_audio_to_ms(self, ms: int) -> None:
        if self._mp4_audio_info is None:
            return
        self._audio_timeline.set_position(ms)
        self._mp4_audio_player.setPosition(ms)

    def _reset_audio_trim(self) -> None:
        if self._mp4_audio_info is None:
            return
        dur_ms = max(200, int(round(self._mp4_audio_info.duration_sec * 1000)))
        if self._mp4_audio_player.duration() > 0:
            dur_ms = self._mp4_audio_player.duration()
        self._audio_timeline.set_trim_points(0, dur_ms, emit=True)

    def _current_mp4_audio_overlay(self) -> Optional[AudioOverlay]:
        if self._mp4_audio_info is None:
            return None
        mode = str(self._mp4_audio_mode_combo.currentData() or "mix")
        return AudioOverlay(
            path=self._mp4_audio_info.path,
            mode="replace" if mode == "replace" else "mix",
            overlay_volume=self._mp4_audio_vol_slider.value() / 100.0,
            original_volume=1.0,
            video_offset_sec=float(self._mp4_audio_offset_spin.value()),
            audio_start_sec=self._audio_timeline.in_ms() / 1000.0,
            audio_end_sec=self._audio_timeline.out_ms() / 1000.0,
            loop=self._mp4_audio_loop_check.isChecked(),
        )

    def _on_mp4_audio_vol_changed(self, value: int) -> None:
        self._mp4_audio_vol_val.setText(f"{value}%")
        self._sync_mp4_audio_preview(
            self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState,
            self._player.position(),
        )

    def _on_mp4_audio_settings_changed(self, *_args) -> None:
        self._refresh_estimate_and_output()
        self._sync_mp4_audio_preview(
            self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState,
            self._player.position(),
        )

    def _on_preview_volume_changed(self, value: int) -> None:
        vol = value / 100.0
        self._audio_output.setVolume(vol)
        ov_factor = (self._mp4_audio_vol_slider.value() / 100.0) if self._mp4_audio_info else 1.0
        self._mp4_audio_output.setVolume(min(1.0, vol * ov_factor))

    def _sync_mp4_audio_preview(self, playing: bool, pos_ms: int) -> None:
        mute_video_flag = self._output_format == "mp4" and self._mute_export_check.isChecked()
        if (
            self._mp4_audio_info is None
            or self._output_format != "mp4"
            or (self._base_asset is not None and self._base_asset.kind == "image")
        ):
            self._audio_output.setMuted(self._preview_muted or mute_video_flag)
            if self._mp4_audio_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
                self._mp4_audio_player.pause()
            return

        overlay = self._current_mp4_audio_overlay()
        assert overlay is not None
        mute_orig = self._preview_muted or overlay.mode == "replace" or mute_video_flag
        self._audio_output.setMuted(mute_orig)
        self._mp4_audio_output.setMuted(self._preview_muted)
        base_vol = self._volume_slider.value() / 100.0
        self._mp4_audio_output.setVolume(min(1.0, base_vol * overlay.overlay_volume))

        self._mp4_audio_player.setLoops(QMediaPlayer.Loops.Once)
        in_ms = self._timeline.in_ms()
        offset_ms = int(round(overlay.video_offset_sec * 1000.0))
        audio_in_ms = self._audio_timeline.in_ms()
        audio_out_ms = self._audio_timeline.out_ms()
        slice_ms = max(200, audio_out_ms - audio_in_ms)
        rel_ms = (pos_ms - in_ms) - offset_ms

        if rel_ms < 0:
            self._audio_timeline.set_position(audio_in_ms)
            if self._mp4_audio_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
                self._mp4_audio_player.pause()
            return

        if rel_ms >= slice_ms:
            if overlay.loop:
                target_ms = audio_in_ms + (rel_ms % slice_ms)
            else:
                self._audio_timeline.set_position(audio_out_ms)
                if self._mp4_audio_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
                    self._mp4_audio_player.pause()
                return
        else:
            target_ms = audio_in_ms + rel_ms

        self._audio_timeline.set_position(target_ms)
        if abs(self._mp4_audio_player.position() - target_ms) > 200 or not playing:
            self._mp4_audio_player.setPosition(target_ms)

        if playing:
            if self._mp4_audio_player.playbackState() != QMediaPlayer.PlaybackState.PlayingState:
                self._mp4_audio_player.play()
        else:
            if self._mp4_audio_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
                self._mp4_audio_player.pause()

    def _toggle_play_pause(self) -> None:
        if self._video_info is None:
            return
        if self._base_asset is not None and self._base_asset.kind == "image":
            if self._duration_ms <= 0:
                return
            self._still_playing = not self._still_playing
            if self._still_playing:
                if self._still_pos_ms >= self._duration_ms:
                    self._still_pos_ms = 0
                self._still_timer.start()
                self._play_btn.setText("Pause")
                self._video_widget.sync_overlay_playback(True, self._still_pos_ms)
            else:
                self._still_timer.stop()
                self._play_btn.setText("Play")
                self._video_widget.sync_overlay_playback(False, self._still_pos_ms)
            self._refresh_icons()
            return

        if self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self._player.pause()
            self._video_widget.sync_overlay_playback(False, self._player.position())
            self._sync_mp4_audio_preview(False, self._player.position())
        else:
            pos = self._player.position()
            in_ms = self._timeline.in_ms()
            out_ms = self._timeline.out_ms()
            near_end = out_ms > in_ms and pos >= max(in_ms, out_ms - 60)
            if pos < in_ms or near_end:
                self._player.setPosition(in_ms)
                pos = in_ms
            self._player.play()
            self._video_widget.sync_overlay_playback(True, pos)
            self._sync_mp4_audio_preview(True, pos)

    def _step_by_ms(self, delta_ms: int) -> None:
        if self._video_info is None or self._duration_ms <= 0:
            return
        base_pos = self._still_pos_ms if (self._base_asset and self._base_asset.kind == "image") else self._player.position()
        target = max(0, min(self._duration_ms, base_pos + delta_ms))
        self._seek_to_ms(target)

    def _seek_to_ms(self, ms: int) -> None:
        if self._video_info is None:
            return
        if self._base_asset is not None and self._base_asset.kind == "image":
            self._still_pos_ms = max(0, min(self._duration_ms, ms))
            self._timeline.set_position(self._still_pos_ms)
            self._update_time_label(self._still_pos_ms)
            self._video_widget.set_playhead_sec(self._still_pos_ms / 1000.0)
            self._video_widget.sync_overlay_playback(self._still_playing, self._still_pos_ms)
            return

        if self._player.playbackState() == QMediaPlayer.PlaybackState.StoppedState:
            self._player.pause()
        player_ms = min(ms, max(0, self._duration_ms - 50)) if self._duration_ms > 50 else ms
        self._player.setPosition(player_ms)
        self._timeline.set_position(ms)
        self._update_time_label(ms)
        self._video_widget.set_playhead_sec(ms / 1000.0)
        is_playing = self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
        self._video_widget.sync_overlay_playback(is_playing, ms)
        self._sync_mp4_audio_preview(is_playing, ms)

    def _toggle_preview_mute(self) -> None:
        self._preview_muted = not self._preview_muted
        self._audio_output.setMuted(self._preview_muted)
        self._mp4_audio_output.setMuted(self._preview_muted)
        self._video_widget.set_overlay_preview_muted(self._preview_muted)
        self._mute_preview_btn.setText("Unmute" if self._preview_muted else "Mute")
        self._sync_mp4_audio_preview(
            self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState,
            self._player.position(),
        )
        self._refresh_icons()

    def _set_in_at_playhead(self) -> None:
        if self._video_info is None or (self._base_asset and self._base_asset.kind == "image"):
            return
        self._timeline.set_trim_points(self._player.position(), self._timeline.out_ms(), emit=True)

    def _set_out_at_playhead(self) -> None:
        if self._video_info is None or (self._base_asset and self._base_asset.kind == "image"):
            return
        self._timeline.set_trim_points(self._timeline.in_ms(), self._player.position(), emit=True)

    def _reset_trim(self) -> None:
        if self._video_info is None or (self._base_asset and self._base_asset.kind == "image"):
            return
        self._timeline.set_trim_points(0, self._duration_ms, emit=True)

    def _toggle_crop_mode(self) -> None:
        if self._video_info is None:
            return
        self._crop_btn.setChecked(not self._crop_btn.isChecked())

    def _exit_crop_mode(self) -> None:
        if self._crop_btn.isChecked():
            self._crop_btn.setChecked(False)

    def _on_crop_mode_toggled(self, enabled: bool) -> None:
        if enabled and self._video_info is None:
            self._crop_btn.setChecked(False)
            return
        if enabled and self._video_widget.is_overlay_crop_mode():
            self._set_overlay_crop_mode(False)
        self._video_widget.set_crop_mode(enabled)
        self._crop_btn.setText("Done Crop (C)" if enabled else "Crop (C)")
        self._refresh_icons()

    def _clear_crop(self) -> None:
        self._video_widget.set_crop_rect(None, emit=True)

    def _on_crop_changed(self, crop: Optional[CropRect]) -> None:
        self._active_crop = crop
        has_crop = crop is not None
        self._clear_crop_btn.setVisible(has_crop)
        self._crop_badge.setVisible(has_crop)
        if crop is not None:
            self._crop_badge.setText(f"Crop: {crop.width}×{crop.height}")
        self._refresh_estimate_and_output()

    def _on_trim_changed(self, in_ms: int, out_ms: int) -> None:
        if self._base_asset and self._base_asset.kind == "image":
            return
        self._in_badge.setText(f"In: {format_timestamp_ms(in_ms)}")
        self._out_badge.setText(f"Out: {format_timestamp_ms(out_ms)}")
        clip_sec = max(0.0, (out_ms - in_ms) / 1000.0)
        self._dur_badge.setText(f"Clip: {clip_sec:.2f}s")
        self._refresh_estimate_and_output()

    def _on_player_position_changed(self, pos_ms: int) -> None:
        self._timeline.set_position(pos_ms)
        self._update_time_label(pos_ms)
        self._video_widget.set_playhead_sec(pos_ms / 1000.0)
        is_playing = self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
        if (
            self._loop_check.isChecked()
            and is_playing
            and self._timeline.out_ms() > self._timeline.in_ms()
            and pos_ms >= self._timeline.out_ms()
        ):
            in_ms = self._timeline.in_ms()
            self._player.setPosition(in_ms)
            self._sync_mp4_audio_preview(True, in_ms)
            return
        if is_playing and self._mp4_audio_info is not None and self._output_format == "mp4":
            self._sync_mp4_audio_preview(True, pos_ms)

    def _on_player_duration_changed(self, duration_ms: int) -> None:
        if duration_ms > 0 and self._video_info is not None and (not self._base_asset or self._base_asset.kind != "image"):
            prev_duration = self._duration_ms
            was_full = self._timeline.in_ms() == 0 and self._timeline.out_ms() == prev_duration
            self._duration_ms = duration_ms
            if was_full:
                self._timeline.set_duration(duration_ms)
                self._on_trim_changed(0, duration_ms)
            self._update_time_label(self._player.position())

    def _on_playback_state_changed(self, state: QMediaPlayer.PlaybackState) -> None:
        is_playing = state == QMediaPlayer.PlaybackState.PlayingState
        self._play_btn.setText("Pause" if is_playing else "Play")
        self._play_btn.setIcon(
            make_monoline_icon("pause" if is_playing else "play", self._theme.text_primary)
        )
        if not (self._base_asset and self._base_asset.kind == "image"):
            self._video_widget.sync_overlay_playback(is_playing)
            self._sync_mp4_audio_preview(is_playing, self._player.position())

    def _on_media_status_changed(self, status: QMediaPlayer.MediaStatus) -> None:
        if self._video_info is None or (self._base_asset and self._base_asset.kind == "image"):
            return
        if status == QMediaPlayer.MediaStatus.LoadedMedia:
            if self._player.playbackState() == QMediaPlayer.PlaybackState.StoppedState:
                self._player.pause()
                self._player.setPosition(self._timeline.in_ms())
        elif status == QMediaPlayer.MediaStatus.EndOfMedia:
            if self._loop_check.isChecked():
                self._player.setPosition(self._timeline.in_ms())
                self._player.play()
            else:
                end_pos = max(0, self._duration_ms - 50)
                if self._timeline.out_ms() > 0:
                    end_pos = min(self._timeline.out_ms(), end_pos)
                self._player.pause()
                self._player.setPosition(end_pos)
                self._timeline.set_position(end_pos)
                self._update_time_label(end_pos)

    def _update_time_label(self, pos_ms: int) -> None:
        self._time_label.setText(
            f"{format_timestamp_ms(pos_ms)} / {format_timestamp_ms(self._duration_ms)}"
        )

    def _active_export_ext(self) -> MemeExportFormat:
        if self._output_format == "mp4":
            return "mp4"
        return self._meme_panel.export_format()

    def _update_export_button_label(self) -> None:
        fmt = self._active_export_ext()
        self._export_btn.setText(f"Export {fmt.upper()}")

    def _set_output_format(self, fmt: Literal["mp4", "gif"]) -> None:
        self._output_format = fmt
        self._options_stack.setCurrentIndex(0 if fmt == "mp4" else 1)
        self._video_widget.set_meme_mode(fmt == "gif")
        self._audio_timeline_row.setVisible(fmt == "mp4" and self._mp4_audio_info is not None)
        if fmt == "mp4":
            self._video_widget.set_overlay_crop_mode(False)
            self._meme_panel.set_overlay_crop_checked(False)
        self._update_export_button_label()
        self._refresh_default_output_path()
        self._sync_caption_preview()
        self._sync_mp4_audio_preview(
            self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState,
            self._player.position(),
        )

    def _current_gif_caption(self) -> Optional[GifCaption]:
        caption = GifCaption(
            top_text=self._gif_top_caption_edit.text(),
            bottom_text=self._gif_bottom_caption_edit.text(),
            size_scale=float(self._gif_caption_size_combo.currentData() or 1.0),
            uppercase=self._gif_caption_caps_check.isChecked(),
        )
        return caption if caption.is_active() else None

    def _sync_caption_preview(self) -> None:
        if self._output_format == "gif":
            self._video_widget.set_caption_preview(self._current_gif_caption())
        else:
            self._video_widget.set_caption_preview(None)

    def _selected_target_mb(self) -> Optional[float]:
        if self._size_buttons["original"].isChecked():
            return None
        if self._size_buttons["10"].isChecked():
            return 10.0
        if self._size_buttons["15"].isChecked():
            return 15.0
        if self._size_buttons["20"].isChecked():
            return 20.0
        return float(self._custom_mb_spin.value())

    def _on_mp4_size_changed(self) -> None:
        self._custom_mb_row.setVisible(self._size_buttons["custom"].isChecked())
        self._refresh_default_output_path()
        self._refresh_estimate_and_output()

    def _refresh_default_output_path(self) -> None:
        if self._video_info is None:
            return
        active_fmt = self._active_export_ext()
        ext = f".{active_fmt}"
        if self._output_customized and self._output_edit.text().strip():
            current = Path(self._output_edit.text().strip())
            self._output_edit.setText(str(current.with_suffix(ext)))
            return

        src = self._video_info.path
        suffix_tag = "_meme" if (self._output_format == "gif" and (self._meme_layers or self._top_bar_text.strip() or (self._base_asset and self._base_asset.kind == "image"))) else "_cut"
        if active_fmt == "mp4":
            target_mb = self._selected_target_mb() if self._output_format == "mp4" else self._meme_panel.mp4_target_mb()
            if target_mb is not None:
                suffix_tag = f"{suffix_tag}_{target_mb:g}mb"
        candidate = src.with_name(f"{src.stem}{suffix_tag}{ext}")
        if candidate.resolve() == src.resolve():
            candidate = src.with_name(f"{src.stem}_edited{ext}")
        self._output_edit.setText(str(candidate))

    def _on_output_edited(self, _text: str) -> None:
        self._output_customized = True

    def _choose_output_file(self) -> None:
        ext = self._active_export_ext()
        filter_map = {
            "mp4": "MP4 Video (*.mp4)",
            "gif": "GIF Animation (*.gif)",
            "png": "PNG Image (*.png)",
            "jpg": "JPEG Image (*.jpg)",
        }
        filt = filter_map.get(ext, "All Files (*)")
        start_path = self._output_edit.text().strip() or str(self.default_open_dir())
        path_str, _ = QFileDialog.getSaveFileName(self, "Save Exported File", start_path, filt)
        if path_str:
            chosen = Path(path_str)
            if chosen.suffix.lower() != f".{ext}":
                chosen = chosen.with_suffix(f".{ext}")
            self._output_customized = True
            self._output_edit.setText(str(chosen))

    def _refresh_estimate_and_output(self) -> None:
        if self._video_info is None or (self._base_asset and self._base_asset.kind == "image"):
            self._mp4_estimate_label.setText("")
            return

        clip_sec = max(0.2, (self._timeline.out_ms() - self._timeline.in_ms()) / 1000.0)
        target_mb = self._selected_target_mb()
        if target_mb is None:
            self._mp4_estimate_label.setText("High quality H.264 (CRF 20) — no size cap.")
            return

        mode, fixed_height = self._mp4_res_combo.currentData()
        effective_height = self._active_crop.height if self._active_crop is not None else self._video_info.height
        has_overlay = self._mp4_audio_info is not None
        plan = calculate_mp4_encoding_plan(
            duration_sec=clip_sec,
            target_mb=target_mb,
            has_audio=self._video_info.has_audio or has_overlay,
            mute_audio=self._mute_export_check.isChecked() and not has_overlay,
            source_height=effective_height,
            max_height=fixed_height,
            auto_scale=(mode == "auto"),
        )
        res_note = f"{plan.scale_height}p" if plan.scale_height else "Original res"
        audio_note = f"{plan.audio_kbps}k audio" if plan.audio_kbps > 0 else "no audio"
        warn = " (very tight bitrate!)" if plan.is_tight_budget else ""
        self._mp4_estimate_label.setText(
            f"2-Pass Plan: ~{plan.video_kbps}k video, {audio_note}, {res_note}{warn}"
        )

    def _start_export(self) -> None:
        if self._video_info is None:
            return
        out_str = self._output_edit.text().strip()
        if not out_str:
            QMessageBox.warning(self, "Missing Output Path", "Please choose where to save the file.")
            return

        active_fmt = self._active_export_ext()
        out_path = Path(out_str)
        expected_ext = f".{active_fmt}"
        if out_path.suffix.lower() != expected_ext:
            out_path = out_path.with_suffix(expected_ext)
            self._output_edit.setText(str(out_path))

        if out_path.resolve() == self._video_info.path.resolve() or (
            self._mp4_audio_info is not None and out_path.resolve() == self._mp4_audio_info.path.resolve()
        ):
            QMessageBox.warning(
                self,
                "Invalid Output Path",
                "Output file cannot overwrite a source file. Pick a different filename.",
            )
            return

        use_meme_pipeline = self._output_format == "gif" and (
            bool(self._meme_layers)
            or bool(self._top_bar_text.strip())
            or (self._base_asset is not None and self._base_asset.kind == "image")
            or active_fmt != "gif"
        )

        if use_meme_pipeline:
            comp = self._build_meme_composition()
            assert comp is not None
            still_sec = (
                self._player.position() / 1000.0
                if (self._base_asset is not None and self._base_asset.kind != "image")
                else 0.0
            )
            options = ExportOptions(
                input_path=self._video_info.path,
                output_path=out_path,
                start_sec=comp.base.start_sec,
                end_sec=max(comp.base.start_sec + 0.2, comp.base.end_sec or comp.effective_duration_sec()),
                output_format=active_fmt,
                video_info=self._video_info,
                crop=self._active_crop,
                target_size_mb=self._meme_panel.mp4_target_mb() if active_fmt == "mp4" else None,
                mute_audio=self._meme_panel.mp4_mute_audio(),
                gif_fps=int(self._gif_fps_combo.currentData()),
                gif_max_width=self._gif_width_combo.currentData(),
                gif_target_size_mb=self._gif_limit_combo.currentData(),
                gif_caption=self._current_gif_caption(),
                meme_composition=comp,
                still_frame_sec=still_sec,
            )
        else:
            if self._base_asset is not None and self._base_asset.kind == "image":
                QMessageBox.information(
                    self,
                    "Image Source",
                    "Static images export through the GIF / Meme tab.",
                )
                return
            start_sec = self._timeline.in_ms() / 1000.0
            end_sec = self._timeline.out_ms() / 1000.0
            if end_sec <= start_sec:
                QMessageBox.warning(self, "Invalid Trim Range", "Out point must be after In point.")
                return

            mode, fixed_height = self._mp4_res_combo.currentData()
            options = ExportOptions(
                input_path=self._video_info.path,
                output_path=out_path,
                start_sec=start_sec,
                end_sec=end_sec,
                output_format=self._output_format,
                video_info=self._video_info,
                crop=self._active_crop,
                target_size_mb=self._selected_target_mb(),
                max_height=fixed_height,
                auto_scale_for_target=(mode == "auto"),
                mute_audio=self._mute_export_check.isChecked(),
                audio_overlay=self._current_mp4_audio_overlay() if self._output_format == "mp4" else None,
                gif_fps=int(self._gif_fps_combo.currentData()),
                gif_max_width=self._gif_width_combo.currentData(),
                gif_target_size_mb=self._gif_limit_combo.currentData(),
                gif_caption=self._current_gif_caption(),
            )

        self._stop_still_playback()
        self._player.pause()
        self._mp4_audio_player.pause()
        self._video_widget.sync_overlay_playback(False)
        self._result_widget.setVisible(False)
        self._progress_bar.setValue(0)
        self._progress_bar.setVisible(True)
        self._status_label.setText("Starting export...")
        self._telemetry_label.setText("ENCODING · RUNNING")
        self._dial_spinner.set_active(True)
        self._set_exporting_state(True)

        self._worker = ExportWorker(options)
        self._worker.progress_changed.connect(self._on_export_progress)
        self._worker.finished_success.connect(self._on_export_success)
        self._worker.finished_error.connect(self._on_export_error)
        self._worker.start()

    def _cancel_export(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            self._status_label.setText("Cancelling...")
            self._cancel_btn.setEnabled(False)
            self._worker.cancel()

    def _set_exporting_state(self, exporting: bool) -> None:
        self._export_btn.setEnabled(not exporting and self._video_info is not None)
        self._open_btn.setEnabled(not exporting)
        self._cancel_btn.setVisible(exporting)
        self._cancel_btn.setEnabled(exporting)

    def _on_export_progress(self, pct: int, message: str) -> None:
        self._progress_bar.setValue(pct)
        self._status_label.setText(message)
        self._telemetry_label.setText(f"ENCODING · {pct}%")

    def _on_export_success(self, output_path: str, size_bytes: int) -> None:
        self._set_exporting_state(False)
        self._dial_spinner.set_active(False)
        self._progress_bar.setValue(100)
        self._status_label.setText("Done.")
        self._last_exported_path = Path(output_path)
        size_text = format_file_size(size_bytes)
        self._result_label.setText(f"Saved {self._last_exported_path.name} ({size_text})")
        self._telemetry_label.setText(f"READY · LAST EXPORT {size_text}")
        self._result_widget.setVisible(True)

    def _on_export_error(self, message: str) -> None:
        self._set_exporting_state(False)
        self._dial_spinner.set_active(False)
        self._progress_bar.setVisible(False)
        self._status_label.setText(message)
        self._telemetry_label.setText("READY")
        if message != "Export cancelled.":
            QMessageBox.critical(self, "Export Failed", message)

    def toggle_youtube_drawer(self) -> None:
        """Toggle visibility of the collapsible right-edge YouTube MP4 downloader drawer."""
        self._set_youtube_drawer_open(self._yt_drawer.isHidden())

    def _set_youtube_drawer_open(self, open_drawer: bool) -> None:
        if self._yt_edge_btn.isChecked() != open_drawer:
            self._yt_edge_btn.blockSignals(True)
            self._yt_edge_btn.setChecked(open_drawer)
            self._yt_edge_btn.blockSignals(False)

        self._yt_edge_btn.setText("›\nY\nT" if open_drawer else "‹\nY\nT")
        if (not self._yt_drawer.isHidden()) == open_drawer:
            if open_drawer:
                self._yt_drawer.focus_url_input()
            return

        if open_drawer:
            if self.isVisible() and not self.isMaximized() and self._yt_drawer_added_width == 0:
                delta = self._yt_drawer.width() + 8
                screen = self.screen()
                avail_w = screen.availableGeometry().width() if screen is not None else 99999
                if self.width() + delta <= avail_w:
                    self.resize(self.width() + delta, self.height())
                    self._yt_drawer_added_width = delta
            self._yt_drawer.setVisible(True)
            self._yt_drawer.focus_url_input()
        else:
            self._yt_drawer.setVisible(False)
            if self.isVisible() and not self.isMaximized() and self._yt_drawer_added_width > 0:
                target_w = max(self.minimumWidth(), self.width() - self._yt_drawer_added_width)
                self._yt_drawer_added_width = 0
                self.resize(target_w, self.height())

    def _load_downloaded_video(self, path: Path) -> None:
        if self._worker is not None and self._worker.isRunning():
            self._telemetry_label.setText(f"YT READY · {path.name} (export running)")
            return
        self.load_video(path)

    def _on_yt_download_state_changed(self, downloading: bool) -> None:
        self._yt_edge_btn.setProperty("downloading", downloading)
        style = self._yt_edge_btn.style()
        if style is not None:
            style.unpolish(self._yt_edge_btn)
            style.polish(self._yt_edge_btn)
        exporting = self._worker is not None and self._worker.isRunning()
        self._dial_spinner.set_active(downloading or exporting)
        if not downloading and not exporting:
            self._telemetry_label.setText("READY")

    def _on_yt_progress(self, pct: int, _status: str) -> None:
        exporting = self._worker is not None and self._worker.isRunning()
        if not exporting:
            self._telemetry_label.setText(f"YT DL · {pct}%")

    def _open_output_folder(self) -> None:
        if self._last_exported_path is None:
            return
        if os.name == "nt" and self._last_exported_path.exists():
            subprocess.Popen(["explorer", "/select,", str(self._last_exported_path)])
        else:
            folder = self._last_exported_path.parent
            if os.name == "nt":
                os.startfile(str(folder))  # type: ignore[attr-defined]

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self._taskbar_props_applied:
            app = QApplication.instance()
            if app is not None and app.platformName() == "windows":
                project_root = Path(__file__).resolve().parent.parent.parent
                self._taskbar_props_applied = apply_window_taskbar_metadata(
                    hwnd=int(self.winId()),
                    project_root=project_root,
                    icon_path=self._icon_path,
                )

    def closeEvent(self, event) -> None:
        self._stop_still_playback()
        if self._worker is not None and self._worker.isRunning():
            self._worker.cancel()
            self._worker.wait(3000)
        self._video_widget.clear_meme_caches()
        self._yt_drawer.shutdown()
        self._mp4_audio_player.stop()
        self._mp4_audio_player.setSource(QUrl())
        self._mp4_audio_player.setAudioOutput(None)
        self._player.stop()
        self._player.setSource(QUrl())
        self._player.setVideoOutput(None)
        self._player.setAudioOutput(None)
        super().closeEvent(event)
