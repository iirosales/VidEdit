"""Meme Studio sidebar panel hosted inside the GIF / Meme tab."""

from __future__ import annotations

from dataclasses import replace
from typing import Literal, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from videdit.meme_project import (
    CaptionStyle,
    MediaKind,
    MediaOverlayLayer,
    OverlayLayer,
    OverlayShape,
    TextOverlayLayer,
)
from videdit.ui.icons import make_monoline_icon
from videdit.ui.theme import DARK_THEME, ThemeTokens

MemeExportFormat = Literal["gif", "mp4", "png", "jpg"]


class MemePanel(QWidget):
    """Scrollable Meme Studio & GIF export sidebar panel."""

    add_media_requested = Signal()
    add_text_requested = Signal()
    layer_selected = Signal(str)
    layer_updated = Signal(object)  # OverlayLayer
    layer_reordered = Signal(str, int)  # layer_id, delta (+1 forward, -1 backward)
    layer_deleted = Signal(str)
    overlay_crop_toggled = Signal(bool)
    top_bar_changed = Signal(str)
    classic_caption_changed = Signal()
    meme_format_changed = Signal(str)
    settings_changed = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._theme: ThemeTokens = DARK_THEME
        self._layers: list[OverlayLayer] = []
        self._selected_id: Optional[str] = None
        self._is_animated: bool = True
        self._base_kind: MediaKind = "video"
        self._export_format: MemeExportFormat = "gif"
        self._updating_ui: bool = False

        self._build_ui()
        self._connect_signals()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 2, 0, 0)
        root.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 2, 4, 4)
        layout.setSpacing(8)

        # 1. Layers & Overlays
        layer_hdr = QLabel("MEME LAYERS (FACES · CLIPS · TEXT)")
        layer_hdr.setObjectName("MutedLabel")
        layout.addWidget(layer_hdr)

        add_row = QHBoxLayout()
        add_row.setSpacing(6)
        self._add_media_btn = QPushButton("+ Add Image / Vid")
        self._add_media_btn.setToolTip("Overlay friend's face, PNG, GIF, or video clip(s)")
        self._add_text_btn = QPushButton("+ Text Box")
        self._add_text_btn.setToolTip("Add a draggable freeform text box")
        add_row.addWidget(self._add_media_btn, stretch=1)
        add_row.addWidget(self._add_text_btn)
        layout.addLayout(add_row)

        self._layer_list = QListWidget()
        self._layer_list.setObjectName("DownloadedClipsList")
        self._layer_list.setMaximumHeight(96)
        self._layer_list.setVisible(False)
        layout.addWidget(self._layer_list)

        self._layer_actions_row = QWidget()
        la_layout = QHBoxLayout(self._layer_actions_row)
        la_layout.setContentsMargins(0, 0, 0, 0)
        la_layout.setSpacing(4)
        self._layer_up_btn = QPushButton("Up")
        self._layer_down_btn = QPushButton("Down")
        self._layer_vis_btn = QPushButton("Hide")
        self._layer_del_btn = QPushButton("Delete")
        self._layer_del_btn.setProperty("danger", True)
        for btn in (self._layer_up_btn, self._layer_down_btn, self._layer_vis_btn, self._layer_del_btn):
            btn.setProperty("pill", True)
            la_layout.addWidget(btn)
        self._layer_actions_row.setVisible(False)
        layout.addWidget(self._layer_actions_row)

        # 2. Contextual Selected Layer Inspector
        self._inspector_frame = QFrame()
        self._inspector_frame.setObjectName("Card")
        insp_layout = QVBoxLayout(self._inspector_frame)
        insp_layout.setContentsMargins(8, 8, 8, 8)
        insp_layout.setSpacing(6)

        self._inspector_stack = QStackedWidget()
        self._inspector_stack.addWidget(self._build_media_inspector())
        self._inspector_stack.addWidget(self._build_text_inspector())
        insp_layout.addWidget(self._inspector_stack)
        self._inspector_frame.setVisible(False)
        layout.addWidget(self._inspector_frame)

        # 3. Top White Bar & Classic Impact Captions
        header_lbl = QLabel("Top White Meme Bar")
        header_lbl.setObjectName("MutedLabel")
        layout.addWidget(header_lbl)

        self._top_bar_edit = QLineEdit()
        self._top_bar_edit.setPlaceholderText("Optional white header above media...")
        self._top_bar_edit.setClearButtonEnabled(True)
        layout.addWidget(self._top_bar_edit)

        caption_lbl = QLabel("Classic Captions (Impact)")
        caption_lbl.setObjectName("MutedLabel")
        layout.addWidget(caption_lbl)

        self.gif_top_caption_edit = QLineEdit()
        self.gif_top_caption_edit.setPlaceholderText("Top text (optional)")
        self.gif_top_caption_edit.setClearButtonEnabled(True)
        layout.addWidget(self.gif_top_caption_edit)

        self.gif_bottom_caption_edit = QLineEdit()
        self.gif_bottom_caption_edit.setPlaceholderText("Bottom text (optional)")
        self.gif_bottom_caption_edit.setClearButtonEnabled(True)
        layout.addWidget(self.gif_bottom_caption_edit)

        caption_opts_row = QHBoxLayout()
        caption_opts_row.setSpacing(8)
        self.gif_caption_size_combo = QComboBox()
        self.gif_caption_size_combo.setMinimumHeight(28)
        self.gif_caption_size_combo.addItem("Size: Small", 0.78)
        self.gif_caption_size_combo.addItem("Size: Medium", 1.0)
        self.gif_caption_size_combo.addItem("Size: Large", 1.28)
        self.gif_caption_size_combo.setCurrentIndex(1)
        caption_opts_row.addWidget(self.gif_caption_size_combo, stretch=1)

        self.gif_caption_caps_check = QCheckBox("ALL CAPS")
        self.gif_caption_caps_check.setChecked(True)
        caption_opts_row.addWidget(self.gif_caption_caps_check)
        layout.addLayout(caption_opts_row)

        # 4. Output Format & Export Settings
        out_fmt_lbl = QLabel("Meme Output Format")
        out_fmt_lbl.setObjectName("MutedLabel")
        layout.addWidget(out_fmt_lbl)

        fmt_row = QHBoxLayout()
        fmt_row.setSpacing(4)
        self._out_fmt_group = QButtonGroup(self)
        self._out_fmt_buttons: dict[MemeExportFormat, QPushButton] = {}
        for fmt_key, label in (("gif", "GIF"), ("mp4", "MP4"), ("png", "PNG"), ("jpg", "JPG")):
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setProperty("pill", True)
            if fmt_key == "gif":
                btn.setChecked(True)
            self._out_fmt_group.addButton(btn)
            self._out_fmt_buttons[fmt_key] = btn
            fmt_row.addWidget(btn)
        layout.addLayout(fmt_row)

        self._export_sub_stack = QStackedWidget()
        self._export_sub_stack.addWidget(self._build_gif_export_subpanel())   # 0: GIF
        self._export_sub_stack.addWidget(self._build_mp4_export_subpanel())   # 1: MP4
        self._export_sub_stack.addWidget(self._build_still_export_subpanel()) # 2: PNG / JPG
        layout.addWidget(self._export_sub_stack)

        self._still_dur_row = QWidget()
        sd_layout = QHBoxLayout(self._still_dur_row)
        sd_layout.setContentsMargins(0, 0, 0, 0)
        sd_layout.addWidget(QLabel("Still Image Duration:"))
        self._still_dur_spin = QDoubleSpinBox()
        self._still_dur_spin.setRange(1.0, 30.0)
        self._still_dur_spin.setDecimals(1)
        self._still_dur_spin.setSingleStep(0.5)
        self._still_dur_spin.setValue(5.0)
        self._still_dur_spin.setSuffix(" s")
        sd_layout.addWidget(self._still_dur_spin, stretch=1)
        self._still_dur_row.setVisible(False)
        layout.addWidget(self._still_dur_row)

        layout.addStretch(1)
        scroll.setWidget(content)
        root.addWidget(scroll)

    def _build_media_inspector(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        title = QLabel("Selected Overlay")
        title.setObjectName("MutedLabel")
        layout.addWidget(title)

        crop_flip_row = QHBoxLayout()
        crop_flip_row.setSpacing(6)
        self._ov_crop_btn = QPushButton("Crop Overlay")
        self._ov_crop_btn.setCheckable(True)
        self._ov_crop_btn.setProperty("pill", True)
        self._ov_reset_crop_btn = QPushButton("Reset Crop")
        self._ov_reset_crop_btn.setVisible(False)
        self._ov_flip_btn = QPushButton("Flip H")
        self._ov_flip_btn.setCheckable(True)
        self._ov_flip_btn.setProperty("pill", True)
        crop_flip_row.addWidget(self._ov_crop_btn, stretch=1)
        crop_flip_row.addWidget(self._ov_reset_crop_btn)
        crop_flip_row.addWidget(self._ov_flip_btn)
        layout.addLayout(crop_flip_row)

        shape_row = QHBoxLayout()
        shape_row.setSpacing(4)
        self._shape_group = QButtonGroup(self)
        self._shape_buttons: dict[OverlayShape, QPushButton] = {}
        for shape_key, label in (("rect", "Rect"), ("circle", "Circle"), ("rounded", "Rounded")):
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setProperty("pill", True)
            self._shape_group.addButton(btn)
            self._shape_buttons[shape_key] = btn
            shape_row.addWidget(btn)
        layout.addLayout(shape_row)

        op_row = QHBoxLayout()
        op_row.setSpacing(6)
        op_lbl = QLabel("Opacity")
        op_lbl.setObjectName("MutedLabel")
        self._ov_opacity_slider = QSlider(Qt.Orientation.Horizontal)
        self._ov_opacity_slider.setRange(10, 100)
        self._ov_opacity_slider.setValue(100)
        self._ov_opacity_val = QLabel("100%")
        self._ov_opacity_val.setObjectName("MutedLabel")
        op_row.addWidget(op_lbl)
        op_row.addWidget(self._ov_opacity_slider, stretch=1)
        op_row.addWidget(self._ov_opacity_val)
        layout.addLayout(op_row)

        # Chroma Key / Green Screen
        self._ov_chroma_check = QCheckBox("Green Screen / Chroma Key")
        layout.addWidget(self._ov_chroma_check)

        self._ov_chroma_opts = QWidget()
        ck_layout = QHBoxLayout(self._ov_chroma_opts)
        ck_layout.setContentsMargins(0, 0, 0, 0)
        ck_layout.setSpacing(6)
        self._ov_chroma_color = QComboBox()
        self._ov_chroma_color.addItem("Green", "#00FF00")
        self._ov_chroma_color.addItem("Blue", "#0000FF")
        self._ov_chroma_color.addItem("Black", "#000000")
        self._ov_chroma_color.addItem("White", "#FFFFFF")
        self._ov_chroma_tol = QSlider(Qt.Orientation.Horizontal)
        self._ov_chroma_tol.setRange(5, 55)
        self._ov_chroma_tol.setValue(25)
        self._ov_chroma_tol.setToolTip("Key color tolerance")
        ck_layout.addWidget(self._ov_chroma_color)
        ck_layout.addWidget(self._ov_chroma_tol, stretch=1)
        self._ov_chroma_opts.setVisible(False)
        layout.addWidget(self._ov_chroma_opts)

        # Optional Timing & Video Overlay Mute
        self._ov_timing_box = QWidget()
        tm_layout = QVBoxLayout(self._ov_timing_box)
        tm_layout.setContentsMargins(0, 0, 0, 0)
        tm_layout.setSpacing(4)

        self._ov_timed_check = QCheckBox("Limit Time Window (Pop-in)")
        tm_layout.addWidget(self._ov_timed_check)

        self._ov_time_range_row = QWidget()
        tr_layout = QHBoxLayout(self._ov_time_range_row)
        tr_layout.setContentsMargins(0, 0, 0, 0)
        tr_layout.setSpacing(6)
        self._ov_start_spin = QDoubleSpinBox()
        self._ov_start_spin.setRange(0.0, 600.0)
        self._ov_start_spin.setDecimals(1)
        self._ov_start_spin.setSuffix("s")
        self._ov_end_spin = QDoubleSpinBox()
        self._ov_end_spin.setRange(0.1, 600.0)
        self._ov_end_spin.setDecimals(1)
        self._ov_end_spin.setValue(3.0)
        self._ov_end_spin.setSuffix("s")
        tr_layout.addWidget(QLabel("In:"))
        tr_layout.addWidget(self._ov_start_spin, stretch=1)
        tr_layout.addWidget(QLabel("Out:"))
        tr_layout.addWidget(self._ov_end_spin, stretch=1)
        self._ov_time_range_row.setVisible(False)
        tm_layout.addWidget(self._ov_time_range_row)

        self._ov_mute_check = QCheckBox("Mute Overlay Audio")
        tm_layout.addWidget(self._ov_mute_check)
        layout.addWidget(self._ov_timing_box)

        return panel

    def _build_text_inspector(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        title = QLabel("Selected Text Box")
        title.setObjectName("MutedLabel")
        layout.addWidget(title)

        self._txt_edit = QLineEdit()
        self._txt_edit.setPlaceholderText("Type caption text...")
        self._txt_edit.setClearButtonEnabled(True)
        layout.addWidget(self._txt_edit)

        style_row = QHBoxLayout()
        style_row.setSpacing(4)
        self._txt_style_group = QButtonGroup(self)
        self._txt_style_buttons: dict[CaptionStyle, QPushButton] = {}
        for skey, label in (("impact", "Impact"), ("clean", "Clean"), ("pill", "Dark Pill")):
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setProperty("pill", True)
            self._txt_style_group.addButton(btn)
            self._txt_style_buttons[skey] = btn
            style_row.addWidget(btn)
        layout.addLayout(style_row)

        sz_row = QHBoxLayout()
        sz_row.setSpacing(6)
        self._txt_size_slider = QSlider(Qt.Orientation.Horizontal)
        self._txt_size_slider.setRange(50, 220)
        self._txt_size_slider.setValue(100)
        self._txt_caps_check = QCheckBox("CAPS")
        self._txt_caps_check.setChecked(True)
        sz_row.addWidget(QLabel("Size"))
        sz_row.addWidget(self._txt_size_slider, stretch=1)
        sz_row.addWidget(self._txt_caps_check)
        layout.addLayout(sz_row)

        col_row = QHBoxLayout()
        col_row.setSpacing(6)
        col_row.addWidget(QLabel("Color"))
        self._txt_color_combo = QComboBox()
        self._txt_color_combo.addItem("White", "#FFFFFF")
        self._txt_color_combo.addItem("Yellow", "#FFE135")
        self._txt_color_combo.addItem("Red", "#FF3B5C")
        self._txt_color_combo.addItem("Black", "#111111")
        col_row.addWidget(self._txt_color_combo, stretch=1)
        layout.addLayout(col_row)

        return panel

    def _build_gif_export_subpanel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 2, 0, 0)
        layout.setSpacing(6)

        rate_width_box = QWidget()
        rate_width_box.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        rate_width_grid = QGridLayout(rate_width_box)
        rate_width_grid.setContentsMargins(0, 0, 0, 0)
        rate_width_grid.setHorizontalSpacing(8)
        rate_width_grid.setVerticalSpacing(4)

        fps_lbl = QLabel("Frame Rate")
        fps_lbl.setObjectName("MutedLabel")
        rate_width_grid.addWidget(fps_lbl, 0, 0)

        width_lbl = QLabel("Max Width")
        width_lbl.setObjectName("MutedLabel")
        rate_width_grid.addWidget(width_lbl, 0, 1)

        self.gif_fps_combo = QComboBox()
        self.gif_fps_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.gif_fps_combo.setMinimumContentsLength(8)
        self.gif_fps_combo.setMinimumHeight(28)
        for fps in (10, 15, 20, 24, 30):
            label = f"{fps} fps (Rec.)" if fps == 15 else f"{fps} fps"
            self.gif_fps_combo.addItem(label, fps)
        self.gif_fps_combo.setCurrentIndex(1)
        rate_width_grid.addWidget(self.gif_fps_combo, 1, 0)

        self.gif_width_combo = QComboBox()
        self.gif_width_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.gif_width_combo.setMinimumContentsLength(8)
        self.gif_width_combo.setMinimumHeight(28)
        self.gif_width_combo.addItem("Original", None)
        self.gif_width_combo.addItem("720 px", 720)
        self.gif_width_combo.addItem("480 px (Rec.)", 480)
        self.gif_width_combo.addItem("360 px", 360)
        self.gif_width_combo.addItem("240 px", 240)
        self.gif_width_combo.setCurrentIndex(2)
        rate_width_grid.addWidget(self.gif_width_combo, 1, 1)

        rate_width_grid.setColumnStretch(0, 1)
        rate_width_grid.setColumnStretch(1, 1)
        layout.addWidget(rate_width_box)

        limit_lbl = QLabel("Auto-Fit Size Limit (Optional)")
        limit_lbl.setObjectName("MutedLabel")
        layout.addWidget(limit_lbl)

        self.gif_limit_combo = QComboBox()
        self.gif_limit_combo.setMinimumHeight(28)
        self.gif_limit_combo.addItem("No Size Limit", None)
        self.gif_limit_combo.addItem("10 MB Max (Discord)", 10.0)
        self.gif_limit_combo.addItem("15 MB Max", 15.0)
        self.gif_limit_combo.addItem("20 MB Max", 20.0)
        layout.addWidget(self.gif_limit_combo)

        return panel

    def _build_mp4_export_subpanel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 2, 0, 0)
        layout.setSpacing(6)

        lbl = QLabel("Target MP4 Size (Discord)")
        lbl.setObjectName("MutedLabel")
        layout.addWidget(lbl)

        self._meme_mp4_size_combo = QComboBox()
        self._meme_mp4_size_combo.addItem("High Quality (CRF 20)", None)
        self._meme_mp4_size_combo.addItem("10 MB Max (Discord)", 10.0)
        self._meme_mp4_size_combo.addItem("15 MB Max", 15.0)
        self._meme_mp4_size_combo.addItem("20 MB Max", 20.0)
        layout.addWidget(self._meme_mp4_size_combo)

        self._meme_mp4_mute_check = QCheckBox("Mute Audio")
        layout.addWidget(self._meme_mp4_mute_check)
        return panel

    def _build_still_export_subpanel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 2, 0, 0)
        note = QLabel("Exports a crisp full-resolution still meme frame.")
        note.setObjectName("MutedLabel")
        note.setWordWrap(True)
        layout.addWidget(note)
        return panel

    def _connect_signals(self) -> None:
        self._add_media_btn.clicked.connect(self.add_media_requested.emit)
        self._add_text_btn.clicked.connect(self.add_text_requested.emit)
        self._layer_list.currentRowChanged.connect(self._on_list_row_changed)

        self._layer_up_btn.clicked.connect(lambda: self._emit_reorder(1))
        self._layer_down_btn.clicked.connect(lambda: self._emit_reorder(-1))
        self._layer_vis_btn.clicked.connect(self._toggle_selected_visibility)
        self._layer_del_btn.clicked.connect(self._delete_selected)

        # Media overlay inspector signals
        self._ov_crop_btn.toggled.connect(self._on_ov_crop_toggled)
        self._ov_reset_crop_btn.clicked.connect(self._reset_selected_overlay_crop)
        self._ov_flip_btn.toggled.connect(self._emit_media_update)
        for shape_key, btn in self._shape_buttons.items():
            btn.clicked.connect(lambda _=False, s=shape_key: self._on_shape_clicked(s))
        self._ov_opacity_slider.valueChanged.connect(self._on_opacity_changed)
        self._ov_chroma_check.toggled.connect(self._on_chroma_toggled)
        self._ov_chroma_color.currentIndexChanged.connect(self._emit_media_update)
        self._ov_chroma_tol.valueChanged.connect(self._emit_media_update)
        self._ov_timed_check.toggled.connect(self._on_timed_toggled)
        self._ov_start_spin.valueChanged.connect(self._emit_media_update)
        self._ov_end_spin.valueChanged.connect(self._emit_media_update)
        self._ov_mute_check.toggled.connect(self._emit_media_update)

        # Text inspector signals
        self._txt_edit.textChanged.connect(self._emit_text_update)
        for skey, btn in self._txt_style_buttons.items():
            btn.clicked.connect(lambda _=False, s=skey: self._on_txt_style_clicked(s))
        self._txt_size_slider.valueChanged.connect(self._emit_text_update)
        self._txt_caps_check.toggled.connect(self._emit_text_update)
        self._txt_color_combo.currentIndexChanged.connect(self._emit_text_update)

        # Classic & top bar
        self._top_bar_edit.textChanged.connect(self.top_bar_changed.emit)
        self.gif_top_caption_edit.textChanged.connect(lambda _t: self.classic_caption_changed.emit())
        self.gif_bottom_caption_edit.textChanged.connect(lambda _t: self.classic_caption_changed.emit())
        self.gif_caption_size_combo.currentIndexChanged.connect(lambda _i: self.classic_caption_changed.emit())
        self.gif_caption_caps_check.toggled.connect(lambda _c: self.classic_caption_changed.emit())

        # Format & export settings
        for fmt_key, btn in self._out_fmt_buttons.items():
            btn.clicked.connect(lambda _=False, f=fmt_key: self.set_export_format(f, emit=True))
        self.gif_fps_combo.currentIndexChanged.connect(lambda _i: self.settings_changed.emit())
        self.gif_width_combo.currentIndexChanged.connect(lambda _i: self.settings_changed.emit())
        self.gif_limit_combo.currentIndexChanged.connect(lambda _i: self.settings_changed.emit())
        self._meme_mp4_size_combo.currentIndexChanged.connect(lambda _i: self.settings_changed.emit())
        self._meme_mp4_mute_check.toggled.connect(lambda _c: self.settings_changed.emit())
        self._still_dur_spin.valueChanged.connect(lambda _v: self.settings_changed.emit())

    def apply_theme(self, theme: ThemeTokens) -> None:
        self._theme = theme
        c = theme.text_primary
        self._add_media_btn.setIcon(make_monoline_icon("image", c))
        self._add_text_btn.setIcon(make_monoline_icon("text", c))
        self._layer_up_btn.setIcon(make_monoline_icon("arrow_up", c, size=14))
        self._layer_down_btn.setIcon(make_monoline_icon("arrow_down", c, size=14))
        self._layer_del_btn.setIcon(make_monoline_icon("trash", c, size=14))
        self._ov_flip_btn.setIcon(make_monoline_icon("flip_h", c, size=14))
        self._ov_crop_btn.setIcon(make_monoline_icon("crop", c, size=14))

    def set_export_format(self, fmt: MemeExportFormat, emit: bool = False) -> None:
        self._export_format = fmt
        for key, btn in self._out_fmt_buttons.items():
            btn.setChecked(key == fmt)
        if fmt == "gif":
            self._export_sub_stack.setCurrentIndex(0)
        elif fmt == "mp4":
            self._export_sub_stack.setCurrentIndex(1)
        else:
            self._export_sub_stack.setCurrentIndex(2)
        self._refresh_still_duration_visibility()
        if emit:
            self.meme_format_changed.emit(fmt)

    def export_format(self) -> MemeExportFormat:
        return self._export_format

    def still_duration_sec(self) -> float:
        return float(self._still_dur_spin.value())

    def mp4_target_mb(self) -> Optional[float]:
        return self._meme_mp4_size_combo.currentData()

    def mp4_mute_audio(self) -> bool:
        return self._meme_mp4_mute_check.isChecked()

    def set_top_bar_text(self, text: str) -> None:
        self._updating_ui = True
        try:
            self._top_bar_edit.setText(text)
        finally:
            self._updating_ui = False

    def set_overlay_crop_checked(self, checked: bool) -> None:
        self._updating_ui = True
        try:
            self._ov_crop_btn.setChecked(checked)
        finally:
            self._updating_ui = False

    def _refresh_still_duration_visibility(self) -> None:
        show = (
            self._base_kind == "image"
            and not self._is_animated
            and self._export_format in ("gif", "mp4")
        )
        self._still_dur_row.setVisible(show)

    def set_layers(
        self,
        layers: list[OverlayLayer] | tuple[OverlayLayer, ...],
        selected_id: Optional[str],
        is_animated: bool,
        base_kind: MediaKind,
    ) -> None:
        self._layers = list(layers)
        self._selected_id = selected_id
        self._is_animated = is_animated
        self._base_kind = base_kind

        self._updating_ui = True
        try:
            self._layer_list.clear()
            has_layers = len(self._layers) > 0
            self._layer_list.setVisible(has_layers)
            self._layer_actions_row.setVisible(has_layers)

            # Display top-most layer first in the list
            selected_row = -1
            for row_idx, layer in enumerate(reversed(self._layers)):
                eye = "" if layer.visible else "[hidden] "
                if isinstance(layer, MediaOverlayLayer):
                    tag = "VID" if layer.kind == "video" else ("GIF" if layer.kind == "gif" else "IMG")
                    label = f"{eye}[{tag}] {layer.name}"
                else:
                    preview = " ".join(layer.text.split())[:22] or "(empty)"
                    label = f"{eye}[TXT] {preview}"
                item = QListWidgetItem(label)
                item.setData(Qt.ItemDataRole.UserRole, layer.id)
                self._layer_list.addItem(item)
                if layer.id == selected_id:
                    selected_row = row_idx

            if selected_row >= 0:
                self._layer_list.setCurrentRow(selected_row)
            else:
                self._layer_list.clearSelection()

            sel_layer = next((l for l in self._layers if l.id == selected_id), None)
            self._populate_inspector(sel_layer)
            self._refresh_still_duration_visibility()
        finally:
            self._updating_ui = False

    def _populate_inspector(self, layer: Optional[OverlayLayer]) -> None:
        if layer is None:
            self._inspector_frame.setVisible(False)
            for btn in (self._layer_up_btn, self._layer_down_btn, self._layer_vis_btn, self._layer_del_btn):
                btn.setEnabled(False)
            return

        for btn in (self._layer_up_btn, self._layer_down_btn, self._layer_vis_btn, self._layer_del_btn):
            btn.setEnabled(True)
        self._layer_vis_btn.setText("Hide" if layer.visible else "Show")
        self._inspector_frame.setVisible(True)

        if isinstance(layer, MediaOverlayLayer):
            self._inspector_stack.setCurrentIndex(0)
            self._ov_reset_crop_btn.setVisible(layer.source_crop is not None)
            self._ov_flip_btn.setChecked(layer.flip_h)
            for shape_key, btn in self._shape_buttons.items():
                btn.setChecked(shape_key == layer.shape)
            pct = int(round(layer.opacity * 100))
            self._ov_opacity_slider.setValue(pct)
            self._ov_opacity_val.setText(f"{pct}%")

            has_ck = bool(layer.chroma_key_color)
            self._ov_chroma_check.setChecked(has_ck)
            self._ov_chroma_opts.setVisible(has_ck)
            if layer.chroma_key_color:
                idx = self._ov_chroma_color.findData(layer.chroma_key_color.upper())
                if idx >= 0:
                    self._ov_chroma_color.setCurrentIndex(idx)
            self._ov_chroma_tol.setValue(int(round(layer.chroma_similarity * 100)))

            self._ov_timing_box.setVisible(self._is_animated)
            has_time = layer.start_sec is not None or layer.end_sec is not None
            self._ov_timed_check.setChecked(has_time)
            self._ov_time_range_row.setVisible(has_time)
            self._ov_start_spin.setValue(float(layer.start_sec or 0.0))
            self._ov_end_spin.setValue(float(layer.end_sec if layer.end_sec is not None else 3.0))
            self._ov_mute_check.setVisible(layer.kind == "video" and layer.has_audio)
            self._ov_mute_check.setChecked(layer.mute_audio)
        else:
            self._inspector_stack.setCurrentIndex(1)
            self._ov_crop_btn.setChecked(False)
            self._txt_edit.setText(layer.text)
            for skey, btn in self._txt_style_buttons.items():
                btn.setChecked(skey == layer.style)
            self._txt_size_slider.setValue(int(round(layer.font_scale * 100)))
            self._txt_caps_check.setChecked(layer.uppercase)
            idx = self._txt_color_combo.findData(layer.text_color.upper())
            if idx >= 0:
                self._txt_color_combo.setCurrentIndex(idx)

    def _current_layer(self) -> Optional[OverlayLayer]:
        if not self._selected_id:
            return None
        return next((l for l in self._layers if l.id == self._selected_id), None)

    def _on_list_row_changed(self, row: int) -> None:
        if self._updating_ui:
            return
        if row < 0:
            self.layer_selected.emit("")
            return
        item = self._layer_list.item(row)
        if item is not None:
            lid = str(item.data(Qt.ItemDataRole.UserRole) or "")
            self.layer_selected.emit(lid)

    def _emit_reorder(self, delta: int) -> None:
        if self._selected_id:
            self.layer_reordered.emit(self._selected_id, delta)

    def _toggle_selected_visibility(self) -> None:
        layer = self._current_layer()
        if layer is not None:
            self.layer_updated.emit(replace(layer, visible=not layer.visible))

    def _delete_selected(self) -> None:
        if self._selected_id:
            self.layer_deleted.emit(self._selected_id)

    def _on_ov_crop_toggled(self, checked: bool) -> None:
        if self._updating_ui:
            return
        self.overlay_crop_toggled.emit(checked)

    def _reset_selected_overlay_crop(self) -> None:
        layer = self._current_layer()
        if isinstance(layer, MediaOverlayLayer) and layer.source_crop is not None:
            self.layer_updated.emit(replace(layer, source_crop=None))

    def _on_shape_clicked(self, shape: OverlayShape) -> None:
        if self._updating_ui:
            return
        for key, btn in self._shape_buttons.items():
            btn.setChecked(key == shape)
        layer = self._current_layer()
        if isinstance(layer, MediaOverlayLayer):
            self.layer_updated.emit(replace(layer, shape=shape))

    def _on_opacity_changed(self, val: int) -> None:
        self._ov_opacity_val.setText(f"{val}%")
        if not self._updating_ui:
            self._emit_media_update()

    def _on_chroma_toggled(self, checked: bool) -> None:
        self._ov_chroma_opts.setVisible(checked)
        if not self._updating_ui:
            self._emit_media_update()

    def _on_timed_toggled(self, checked: bool) -> None:
        self._ov_time_range_row.setVisible(checked)
        if not self._updating_ui:
            self._emit_media_update()

    def _emit_media_update(self, *_args) -> None:
        if self._updating_ui:
            return
        layer = self._current_layer()
        if not isinstance(layer, MediaOverlayLayer):
            return
        chroma_col = str(self._ov_chroma_color.currentData()) if self._ov_chroma_check.isChecked() else None
        start_val = float(self._ov_start_spin.value()) if self._ov_timed_check.isChecked() else None
        end_val = float(self._ov_end_spin.value()) if self._ov_timed_check.isChecked() else None
        if start_val is not None and end_val is not None and end_val <= start_val:
            end_val = start_val + 0.2
        updated = replace(
            layer,
            flip_h=self._ov_flip_btn.isChecked(),
            opacity=self._ov_opacity_slider.value() / 100.0,
            chroma_key_color=chroma_col,
            chroma_similarity=self._ov_chroma_tol.value() / 100.0,
            start_sec=start_val,
            end_sec=end_val,
            mute_audio=self._ov_mute_check.isChecked(),
        )
        self.layer_updated.emit(updated)

    def _on_txt_style_clicked(self, style: CaptionStyle) -> None:
        if self._updating_ui:
            return
        for key, btn in self._txt_style_buttons.items():
            btn.setChecked(key == style)
        self._updating_ui = True
        self._txt_caps_check.setChecked(style == "impact")
        self._updating_ui = False
        self._emit_text_update()

    def _emit_text_update(self, *_args) -> None:
        if self._updating_ui:
            return
        layer = self._current_layer()
        if not isinstance(layer, TextOverlayLayer):
            return
        active_style: CaptionStyle = "impact"
        for key, btn in self._txt_style_buttons.items():
            if btn.isChecked():
                active_style = key
                break
        updated = replace(
            layer,
            text=self._txt_edit.text(),
            style=active_style,
            font_scale=self._txt_size_slider.value() / 100.0,
            uppercase=self._txt_caps_check.isChecked(),
            text_color=str(self._txt_color_combo.currentData() or "#FFFFFF"),
        )
        self.layer_updated.emit(updated)
