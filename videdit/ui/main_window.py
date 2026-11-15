"""Main desktop window for VidEdit (Porcelain Clockwork Commander edition)."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Literal, Optional

from PySide6.QtCore import QSettings, Qt, QUrl
from PySide6.QtGui import QDragEnterEvent, QDropEvent, QIcon, QKeySequence, QMouseEvent, QShortcut
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSizeGrip,
    QSlider,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from videdit.exporter import ExportOptions, ExportWorker
from videdit.ffmpeg_tools import VideoInfo, calculate_mp4_encoding_plan, probe_video
from videdit.ui.chrome import CommandEntry, CommandPalette, DropStageWidget, TitleBar
from videdit.ui.icons import ClockworkSpinner, make_monoline_icon
from videdit.ui.theme import ThemeName, ThemeTokens, build_stylesheet, get_theme
from videdit.ui.timeline import TrimTimelineWidget
from videdit.win_taskbar import apply_window_taskbar_metadata

SUPPORTED_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v", ".gif"}
_RESIZE_MARGIN = 6


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

    def __init__(self, initial_theme: Optional[ThemeName] = None) -> None:
        super().__init__()
        self.setWindowTitle("VidEdit — Cut, GIF & Discord Compressor")
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Window)

        self._icon_path = _resolve_icon_path()
        if self._icon_path is not None:
            self.setWindowIcon(QIcon(str(self._icon_path)))

        self.resize(1160, 740)
        self.setMinimumSize(920, 640)
        self.setAcceptDrops(True)

        self._settings = QSettings("iirosales", "VidEdit")
        saved_theme = str(self._settings.value("ui/theme", "dark"))
        theme_key: ThemeName = initial_theme if initial_theme in ("light", "dark") else (
            "light" if saved_theme == "light" else "dark"
        )
        self._theme: ThemeTokens = get_theme(theme_key)

        self._video_info: Optional[VideoInfo] = None
        self._duration_ms: int = 0
        self._output_format: Literal["mp4", "gif"] = "mp4"
        self._output_customized: bool = False
        self._worker: Optional[ExportWorker] = None
        self._last_exported_path: Optional[Path] = None
        self._taskbar_props_applied: bool = False

        self._player = QMediaPlayer(self)
        self._audio_output = QAudioOutput(self)
        self._audio_output.setVolume(0.8)
        self._player.setAudioOutput(self._audio_output)

        self._build_ui()
        self._connect_signals()
        self._setup_shortcuts()
        self._update_controls_enabled(False)
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
        self._file_label = QLabel("Drop a video file here or click Open Video...")
        self._file_label.setObjectName("MutedLabel")
        self._meta_badge = QLabel("NO SOURCE")
        self._meta_badge.setObjectName("BadgeLabel")
        header.addWidget(self._open_btn)
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
        drop_title = QLabel("DRAG & DROP VIDEO")
        drop_title.setObjectName("DropTitle")
        drop_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        drop_sub = QLabel("MP4 · MOV · MKV · WebM · AVI · GIF")
        drop_sub.setObjectName("MutedLabel")
        drop_sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._drop_open_btn = QPushButton("Select Clip")
        self._drop_open_btn.setProperty("primary", True)
        drop_layout.addWidget(drop_title)
        drop_layout.addWidget(drop_sub)
        drop_layout.addSpacing(10)
        drop_layout.addWidget(self._drop_open_btn, alignment=Qt.AlignmentFlag.AlignCenter)

        self._video_widget = QVideoWidget()
        self._player.setVideoOutput(self._video_widget)

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

        trim_actions = QHBoxLayout()
        trim_actions.setSpacing(8)
        self._set_in_btn = QPushButton("Set In (I)")
        self._set_out_btn = QPushButton("Set Out (O)")
        self._reset_trim_btn = QPushButton("Reset")
        self._in_badge = QLabel("In: 00:00.00")
        self._in_badge.setObjectName("BadgeLabel")
        self._out_badge = QLabel("Out: 00:00.00")
        self._out_badge.setObjectName("BadgeLabel")
        self._dur_badge = QLabel("Clip: 0.00s")
        self._dur_badge.setObjectName("BadgeLabel")

        trim_actions.addWidget(self._set_in_btn)
        trim_actions.addWidget(self._set_out_btn)
        trim_actions.addWidget(self._reset_trim_btn)
        trim_actions.addStretch(1)
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
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 4, 0, 0)
        layout.setSpacing(10)

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
        self._mp4_res_combo.addItem("Auto (Recommended for Target Size)", ("auto", None))
        self._mp4_res_combo.addItem("Original Resolution", ("fixed", None))
        self._mp4_res_combo.addItem("1080p Max", ("fixed", 1080))
        self._mp4_res_combo.addItem("720p Max", ("fixed", 720))
        self._mp4_res_combo.addItem("480p Max", ("fixed", 480))
        layout.addWidget(self._mp4_res_combo)

        self._mute_export_check = QCheckBox("Remove Audio (Mute Export)")
        layout.addWidget(self._mute_export_check)

        self._mp4_estimate_label = QLabel("")
        self._mp4_estimate_label.setObjectName("MutedLabel")
        self._mp4_estimate_label.setWordWrap(True)
        layout.addWidget(self._mp4_estimate_label)

        return panel

    def _build_gif_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 4, 0, 0)
        layout.setSpacing(10)

        fps_lbl = QLabel("Frame Rate")
        fps_lbl.setObjectName("MutedLabel")
        layout.addWidget(fps_lbl)

        self._gif_fps_combo = QComboBox()
        for fps in (10, 15, 20, 24, 30):
            label = f"{fps} fps (Recommended)" if fps == 15 else f"{fps} fps"
            self._gif_fps_combo.addItem(label, fps)
        self._gif_fps_combo.setCurrentIndex(1)
        layout.addWidget(self._gif_fps_combo)

        width_lbl = QLabel("Max Width")
        width_lbl.setObjectName("MutedLabel")
        layout.addWidget(width_lbl)

        self._gif_width_combo = QComboBox()
        self._gif_width_combo.addItem("Original Width", None)
        self._gif_width_combo.addItem("720 px", 720)
        self._gif_width_combo.addItem("480 px (Recommended)", 480)
        self._gif_width_combo.addItem("360 px", 360)
        self._gif_width_combo.addItem("240 px", 240)
        self._gif_width_combo.setCurrentIndex(2)
        layout.addWidget(self._gif_width_combo)

        limit_lbl = QLabel("Auto-Fit Size Limit (Optional)")
        limit_lbl.setObjectName("MutedLabel")
        layout.addWidget(limit_lbl)

        self._gif_limit_combo = QComboBox()
        self._gif_limit_combo.addItem("No Size Limit", None)
        self._gif_limit_combo.addItem("10 MB Max (Discord)", 10.0)
        self._gif_limit_combo.addItem("15 MB Max", 15.0)
        self._gif_limit_combo.addItem("20 MB Max", 20.0)
        layout.addWidget(self._gif_limit_combo)

        gif_note = QLabel("Uses 2-pass palette optimization for sharper colors and smaller GIFs.")
        gif_note.setObjectName("MutedLabel")
        gif_note.setWordWrap(True)
        layout.addWidget(gif_note)

        return panel

    def _connect_signals(self) -> None:
        self._title_bar.minimize_requested.connect(self.showMinimized)
        self._title_bar.maximize_requested.connect(self._toggle_maximize)
        self._title_bar.close_requested.connect(self.close)

        self._open_btn.clicked.connect(self._choose_input_file)
        self._drop_open_btn.clicked.connect(self._choose_input_file)

        self._play_btn.clicked.connect(self._toggle_play_pause)
        self._step_back_btn.clicked.connect(lambda: self._step_by_ms(-100))
        self._step_fwd_btn.clicked.connect(lambda: self._step_by_ms(100))
        self._mute_preview_btn.clicked.connect(self._toggle_preview_mute)
        self._volume_slider.valueChanged.connect(lambda v: self._audio_output.setVolume(v / 100.0))

        self._set_in_btn.clicked.connect(self._set_in_at_playhead)
        self._set_out_btn.clicked.connect(self._set_out_at_playhead)
        self._reset_trim_btn.clicked.connect(self._reset_trim)

        self._timeline.seek_requested.connect(self._seek_to_ms)
        self._timeline.trim_changed.connect(self._on_trim_changed)

        self._player.positionChanged.connect(self._on_player_position_changed)
        self._player.durationChanged.connect(self._on_player_duration_changed)
        self._player.playbackStateChanged.connect(self._on_playback_state_changed)

        self._fmt_mp4_btn.clicked.connect(lambda: self._set_output_format("mp4"))
        self._fmt_gif_btn.clicked.connect(lambda: self._set_output_format("gif"))

        for btn in self._size_buttons.values():
            btn.clicked.connect(self._on_mp4_size_changed)
        self._custom_mb_spin.valueChanged.connect(self._on_mp4_size_changed)
        self._mp4_res_combo.currentIndexChanged.connect(self._refresh_estimate_and_output)
        self._mute_export_check.toggled.connect(self._refresh_estimate_and_output)

        self._browse_btn.clicked.connect(self._choose_output_file)
        self._output_edit.textEdited.connect(self._on_output_edited)
        self._export_btn.clicked.connect(self._start_export)
        self._cancel_btn.clicked.connect(self._cancel_export)
        self._open_folder_btn.clicked.connect(self._open_output_folder)

        self._cmd_palette_btn.clicked.connect(self.open_command_palette)
        self._theme_toggle_btn.clicked.connect(self.toggle_theme)

    def _setup_shortcuts(self) -> None:
        self._bind_shortcut("Space", self._toggle_play_pause)
        self._bind_shortcut("I", self._set_in_at_playhead)
        self._bind_shortcut("O", self._set_out_at_playhead)
        self._bind_shortcut("Left", lambda: self._step_by_ms(-100))
        self._bind_shortcut("Right", lambda: self._step_by_ms(100))
        self._bind_shortcut("Shift+Left", lambda: self._step_by_ms(-1000))
        self._bind_shortcut("Shift+Right", lambda: self._step_by_ms(1000))
        palette_sc = QShortcut(QKeySequence("Ctrl+K"), self)
        palette_sc.activated.connect(self.open_command_palette)

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
        self._drop_stage.apply_theme(self._theme)
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
        is_playing = self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
        self._play_btn.setIcon(make_monoline_icon("pause" if is_playing else "play", c))
        is_muted = self._audio_output.isMuted()
        self._mute_preview_btn.setIcon(make_monoline_icon("muted" if is_muted else "volume", c))
        theme_icon = "theme_light" if self._theme.name == "light" else "theme_dark"
        self._theme_toggle_btn.setIcon(make_monoline_icon(theme_icon, self._theme.text_muted, size=14))

    def build_command_entries(self) -> list[CommandEntry]:
        """Return commands exposed in the Ctrl+K Command Palette."""
        return [
            CommandEntry("Open Video File...", "Click", self._choose_input_file),
            CommandEntry("Play / Pause Preview", "Space", self._toggle_play_pause),
            CommandEntry("Set In Point at Playhead", "I", self._set_in_at_playhead),
            CommandEntry("Set Out Point at Playhead", "O", self._set_out_at_playhead),
            CommandEntry("Reset Trim Range", "", self._reset_trim),
            CommandEntry("Format: MP4 Video", "", lambda: self._select_format_pill("mp4")),
            CommandEntry("Format: GIF Animation", "", lambda: self._select_format_pill("gif")),
            CommandEntry("MP4 Preset: Original Quality", "", lambda: self._select_mp4_preset("original")),
            CommandEntry("MP4 Preset: 10 MB (Discord)", "", lambda: self._select_mp4_preset("10")),
            CommandEntry("MP4 Preset: 15 MB", "", lambda: self._select_mp4_preset("15")),
            CommandEntry("MP4 Preset: 20 MB", "", lambda: self._select_mp4_preset("20")),
            CommandEntry("Export Current Clip", "", self._start_export),
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
                if url.isLocalFile() and Path(url.toLocalFile()).suffix.lower() in SUPPORTED_EXTENSIONS:
                    event.acceptProposedAction()
                    return
        event.ignore()

    def dropEvent(self, event: QDropEvent) -> None:
        for url in event.mimeData().urls():
            if url.isLocalFile():
                path = Path(url.toLocalFile())
                if path.suffix.lower() in SUPPORTED_EXTENSIONS:
                    self.load_video(path)
                    event.acceptProposedAction()
                    return

    def _choose_input_file(self) -> None:
        path_str, _ = QFileDialog.getOpenFileName(
            self,
            "Open Video",
            "",
            "Video Files (*.mp4 *.mov *.mkv *.webm *.avi *.m4v *.gif);;All Files (*)",
        )
        if path_str:
            self.load_video(Path(path_str))

    def load_video(self, path: Path) -> None:
        """Probe and load a video file into the preview player and timeline."""
        if self._worker is not None and self._worker.isRunning():
            QMessageBox.information(self, "Export Running", "Wait for or cancel the active export first.")
            return

        self._player.stop()
        try:
            info = probe_video(path)
        except Exception as exc:
            QMessageBox.critical(self, "Could Not Open Video", str(exc))
            return

        self._video_info = info
        self._duration_ms = max(200, int(round(info.duration_sec * 1000)))
        self._output_customized = False
        self._result_widget.setVisible(False)
        self._status_label.setText("")

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
        self._refresh_default_output_path()
        self._refresh_estimate_and_output()

    def _update_controls_enabled(self, enabled: bool) -> None:
        for widget in (
            self._play_btn,
            self._step_back_btn,
            self._step_fwd_btn,
            self._set_in_btn,
            self._set_out_btn,
            self._reset_trim_btn,
            self._export_btn,
            self._browse_btn,
            self._output_edit,
        ):
            widget.setEnabled(enabled)

    def _toggle_play_pause(self) -> None:
        if self._video_info is None:
            return
        if self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self._player.pause()
        else:
            pos = self._player.position()
            if pos < self._timeline.in_ms() or pos >= self._timeline.out_ms():
                self._player.setPosition(self._timeline.in_ms())
            self._player.play()

    def _step_by_ms(self, delta_ms: int) -> None:
        if self._video_info is None or self._duration_ms <= 0:
            return
        target = max(0, min(self._duration_ms, self._player.position() + delta_ms))
        self._seek_to_ms(target)

    def _seek_to_ms(self, ms: int) -> None:
        if self._video_info is None:
            return
        self._player.setPosition(ms)
        self._timeline.set_position(ms)
        self._update_time_label(ms)

    def _toggle_preview_mute(self) -> None:
        muted = not self._audio_output.isMuted()
        self._audio_output.setMuted(muted)
        self._mute_preview_btn.setText("Unmute" if muted else "Mute")
        self._refresh_icons()

    def _set_in_at_playhead(self) -> None:
        if self._video_info is None:
            return
        self._timeline.set_trim_points(self._player.position(), self._timeline.out_ms(), emit=True)

    def _set_out_at_playhead(self) -> None:
        if self._video_info is None:
            return
        self._timeline.set_trim_points(self._timeline.in_ms(), self._player.position(), emit=True)

    def _reset_trim(self) -> None:
        if self._video_info is None:
            return
        self._timeline.set_trim_points(0, self._duration_ms, emit=True)

    def _on_trim_changed(self, in_ms: int, out_ms: int) -> None:
        self._in_badge.setText(f"In: {format_timestamp_ms(in_ms)}")
        self._out_badge.setText(f"Out: {format_timestamp_ms(out_ms)}")
        clip_sec = max(0.0, (out_ms - in_ms) / 1000.0)
        self._dur_badge.setText(f"Clip: {clip_sec:.2f}s")
        self._refresh_estimate_and_output()

    def _on_player_position_changed(self, pos_ms: int) -> None:
        self._timeline.set_position(pos_ms)
        self._update_time_label(pos_ms)
        if (
            self._loop_check.isChecked()
            and self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
            and self._timeline.out_ms() > self._timeline.in_ms()
            and pos_ms >= self._timeline.out_ms()
        ):
            self._player.setPosition(self._timeline.in_ms())

    def _on_player_duration_changed(self, duration_ms: int) -> None:
        if duration_ms > 0 and self._video_info is not None:
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

    def _update_time_label(self, pos_ms: int) -> None:
        self._time_label.setText(
            f"{format_timestamp_ms(pos_ms)} / {format_timestamp_ms(self._duration_ms)}"
        )

    def _set_output_format(self, fmt: Literal["mp4", "gif"]) -> None:
        self._output_format = fmt
        self._options_stack.setCurrentIndex(0 if fmt == "mp4" else 1)
        self._export_btn.setText("Export MP4" if fmt == "mp4" else "Export GIF")
        self._refresh_default_output_path()

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
        ext = ".mp4" if self._output_format == "mp4" else ".gif"
        if self._output_customized and self._output_edit.text().strip():
            current = Path(self._output_edit.text().strip())
            self._output_edit.setText(str(current.with_suffix(ext)))
            return

        src = self._video_info.path
        suffix_tag = "_cut"
        if self._output_format == "mp4":
            target_mb = self._selected_target_mb()
            if target_mb is not None:
                suffix_tag = f"_cut_{target_mb:g}mb"
        candidate = src.with_name(f"{src.stem}{suffix_tag}{ext}")
        if candidate.resolve() == src.resolve():
            candidate = src.with_name(f"{src.stem}_edited{ext}")
        self._output_edit.setText(str(candidate))

    def _on_output_edited(self, _text: str) -> None:
        self._output_customized = True

    def _choose_output_file(self) -> None:
        ext = "mp4" if self._output_format == "mp4" else "gif"
        filt = "MP4 Video (*.mp4)" if ext == "mp4" else "GIF Animation (*.gif)"
        start_path = self._output_edit.text().strip() or ""
        path_str, _ = QFileDialog.getSaveFileName(self, "Save Exported Clip", start_path, filt)
        if path_str:
            chosen = Path(path_str)
            if chosen.suffix.lower() != f".{ext}":
                chosen = chosen.with_suffix(f".{ext}")
            self._output_customized = True
            self._output_edit.setText(str(chosen))

    def _refresh_estimate_and_output(self) -> None:
        if self._video_info is None:
            self._mp4_estimate_label.setText("")
            return

        clip_sec = max(0.2, (self._timeline.out_ms() - self._timeline.in_ms()) / 1000.0)
        target_mb = self._selected_target_mb()
        if target_mb is None:
            self._mp4_estimate_label.setText("High quality H.264 (CRF 20) — no size cap.")
            return

        mode, fixed_height = self._mp4_res_combo.currentData()
        plan = calculate_mp4_encoding_plan(
            duration_sec=clip_sec,
            target_mb=target_mb,
            has_audio=self._video_info.has_audio,
            mute_audio=self._mute_export_check.isChecked(),
            source_height=self._video_info.height,
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

        out_path = Path(out_str)
        expected_ext = f".{self._output_format}"
        if out_path.suffix.lower() != expected_ext:
            out_path = out_path.with_suffix(expected_ext)
            self._output_edit.setText(str(out_path))

        if out_path.resolve() == self._video_info.path.resolve():
            QMessageBox.warning(
                self,
                "Invalid Output Path",
                "Output file cannot overwrite the source video. Pick a different filename.",
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
            target_size_mb=self._selected_target_mb(),
            max_height=fixed_height,
            auto_scale_for_target=(mode == "auto"),
            mute_audio=self._mute_export_check.isChecked(),
            gif_fps=int(self._gif_fps_combo.currentData()),
            gif_max_width=self._gif_width_combo.currentData(),
            gif_target_size_mb=self._gif_limit_combo.currentData(),
        )

        self._player.pause()
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
        if self._worker is not None and self._worker.isRunning():
            self._worker.cancel()
            self._worker.wait(3000)
        self._player.stop()
        self._player.setSource(QUrl())
        self._player.setVideoOutput(None)
        self._player.setAudioOutput(None)
        super().closeEvent(event)
