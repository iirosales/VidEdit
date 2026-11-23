"""Collapsible right-edge YouTube MP4 downloader drawer for VidEdit."""

from __future__ import annotations

import os
import platform
import subprocess
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QSettings, Qt, Signal
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from videdit.ui.icons import make_monoline_icon
from videdit.ui.theme import ThemeTokens
from videdit.youtube_downloader import (
    DEFAULT_YOUTUBE_DIR,
    QUALITY_OPTIONS,
    YouTubeDownloadWorker,
    check_dependencies,
    parse_urls,
)

_YT_DIR_KEY = "paths/yt_download_dir"
_YT_QUALITY_KEY = "youtube/quality"
_DEFAULT_OPEN_DIR_KEY = "paths/default_open_dir"


def resolve_default_youtube_dir(settings: QSettings) -> Path:
    """Resolve YouTube download folder: custom YT setting -> VidEdit Default Folder -> Downloads/YouTube_MP4."""
    yt_raw = str(settings.value(_YT_DIR_KEY, "") or "").strip()
    if yt_raw:
        return Path(yt_raw)

    open_raw = str(settings.value(_DEFAULT_OPEN_DIR_KEY, "") or "").strip()
    if open_raw:
        open_path = Path(open_raw)
        if open_path.is_dir():
            return open_path

    return DEFAULT_YOUTUBE_DIR


def open_folder_in_explorer(folder: Path) -> None:
    """Open a directory in the OS file manager, creating it if needed."""
    folder.mkdir(parents=True, exist_ok=True)
    system = platform.system()
    if system == "Windows":
        os.startfile(str(folder))  # type: ignore[attr-defined]
    elif system == "Darwin":
        subprocess.run(["open", str(folder)], check=False)
    else:
        subprocess.run(["xdg-open", str(folder)], check=False)


class YouTubeDrawerWidget(QFrame):
    """Compact, collapsible YouTube MP4 downloader side drawer."""

    close_requested = Signal()
    load_video_requested = Signal(Path)
    download_state_changed = Signal(bool)
    progress_summary = Signal(int, str)

    def __init__(self, settings: QSettings, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("SidebarCard")
        self.setFixedWidth(296)

        self._settings = settings
        self._worker: Optional[YouTubeDownloadWorker] = None
        self._saved_paths: list[Path] = []
        self._has_yt_dlp = False

        self._build_ui()
        self._connect_signals()
        self._load_settings()
        self._refresh_dependency_banner()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        # Header row
        header = QHBoxLayout()
        header.setSpacing(6)
        title = QLabel("YOUTUBE MP4")
        title.setObjectName("SectionTitle")
        self._batch_toggle_btn = QPushButton("Multi")
        self._batch_toggle_btn.setCheckable(True)
        self._batch_toggle_btn.setProperty("pill", True)
        self._batch_toggle_btn.setToolTip("Expand for multiple links or playlists")
        self._close_btn = QPushButton("×")
        self._close_btn.setFixedSize(26, 26)
        self._close_btn.setToolTip("Hide YouTube Downloader")

        header.addWidget(title, stretch=1)
        header.addWidget(self._batch_toggle_btn)
        header.addWidget(self._close_btn)
        layout.addLayout(header)

        # Dependency notice (hidden unless yt-dlp is missing)
        self._dep_label = QLabel(
            "yt-dlp is missing. Launch via VidEdit.bat or run: pip install yt-dlp"
        )
        self._dep_label.setObjectName("MutedLabel")
        self._dep_label.setWordWrap(True)
        self._dep_label.setVisible(False)
        layout.addWidget(self._dep_label)

        # Link input toolbar
        url_bar = QHBoxLayout()
        url_bar.setSpacing(6)
        self._url_caption = QLabel("Video Link")
        self._url_caption.setObjectName("MutedLabel")
        self._paste_btn = QPushButton("Paste")
        self._clear_btn = QPushButton("Clear")
        url_bar.addWidget(self._url_caption, stretch=1)
        url_bar.addWidget(self._paste_btn)
        url_bar.addWidget(self._clear_btn)
        layout.addLayout(url_bar)

        # Compact single-URL input
        self._url_edit = QLineEdit()
        self._url_edit.setPlaceholderText("Paste YouTube URL...")
        self._url_edit.setClearButtonEnabled(True)
        layout.addWidget(self._url_edit)

        # Expandable batch + playlist section
        self._batch_widget = QWidget()
        batch_layout = QVBoxLayout(self._batch_widget)
        batch_layout.setContentsMargins(0, 0, 0, 0)
        batch_layout.setSpacing(6)
        self._batch_edit = QPlainTextEdit()
        self._batch_edit.setPlaceholderText("Paste multiple YouTube links (one per line)...")
        self._batch_edit.setFixedHeight(84)
        self._playlist_check = QCheckBox("Download full playlist if present")
        batch_layout.addWidget(self._batch_edit)
        batch_layout.addWidget(self._playlist_check)
        self._batch_widget.setVisible(False)
        layout.addWidget(self._batch_widget)

        # Quality selector
        qual_lbl = QLabel("Max Quality")
        qual_lbl.setObjectName("MutedLabel")
        layout.addWidget(qual_lbl)

        self._quality_combo = QComboBox()
        for label in QUALITY_OPTIONS:
            self._quality_combo.addItem(label)
        layout.addWidget(self._quality_combo)

        # Output folder
        save_lbl = QLabel("Save Folder")
        save_lbl.setObjectName("MutedLabel")
        layout.addWidget(save_lbl)

        dir_row = QHBoxLayout()
        dir_row.setSpacing(6)
        self._output_dir_edit = QLineEdit()
        self._output_dir_edit.setPlaceholderText("Download folder...")
        self._browse_dir_btn = QPushButton("...")
        self._browse_dir_btn.setFixedWidth(34)
        self._browse_dir_btn.setToolTip("Choose download folder")
        self._open_dir_btn = QPushButton("Open")
        self._open_dir_btn.setToolTip("Open download folder")
        dir_row.addWidget(self._output_dir_edit, stretch=1)
        dir_row.addWidget(self._browse_dir_btn)
        dir_row.addWidget(self._open_dir_btn)
        layout.addLayout(dir_row)

        # Action buttons
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        self._download_btn = QPushButton("Download MP4")
        self._download_btn.setProperty("primary", True)
        self._cancel_btn = QPushButton("Cancel")
        self._cancel_btn.setProperty("danger", True)
        self._cancel_btn.setVisible(False)
        btn_row.addWidget(self._download_btn, stretch=1)
        btn_row.addWidget(self._cancel_btn)
        layout.addLayout(btn_row)

        # Slim progress + status
        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setValue(0)
        self._progress_bar.setVisible(False)
        layout.addWidget(self._progress_bar)

        self._status_label = QLabel("Paste a YouTube link to download & load in VidEdit.")
        self._status_label.setObjectName("MutedLabel")
        self._status_label.setWordWrap(True)
        layout.addWidget(self._status_label)

        # Downloaded clips picker (shown for multi-video batches or deferred loads)
        self._clips_container = QWidget()
        clips_layout = QVBoxLayout(self._clips_container)
        clips_layout.setContentsMargins(0, 4, 0, 0)
        clips_layout.setSpacing(6)
        clips_lbl = QLabel("Downloaded Clips")
        clips_lbl.setObjectName("MutedLabel")
        self._clips_list = QListWidget()
        self._clips_list.setObjectName("DownloadedClipsList")
        self._clips_list.setMaximumHeight(96)
        self._load_clip_btn = QPushButton("Load Selected in VidEdit")
        clips_layout.addWidget(clips_lbl)
        clips_layout.addWidget(self._clips_list)
        clips_layout.addWidget(self._load_clip_btn)
        self._clips_container.setVisible(False)
        layout.addWidget(self._clips_container)

        layout.addStretch(1)

    def _connect_signals(self) -> None:
        self._close_btn.clicked.connect(self.close_requested.emit)
        self._batch_toggle_btn.toggled.connect(self._on_batch_toggled)
        self._paste_btn.clicked.connect(self._paste_clipboard)
        self._clear_btn.clicked.connect(self._clear_inputs)
        self._url_edit.returnPressed.connect(self._start_download)
        self._quality_combo.currentTextChanged.connect(self._save_quality)
        self._browse_dir_btn.clicked.connect(self._browse_output_dir)
        self._open_dir_btn.clicked.connect(self._open_output_dir)
        self._output_dir_edit.editingFinished.connect(self._save_custom_dir_from_edit)
        self._download_btn.clicked.connect(self._start_download)
        self._cancel_btn.clicked.connect(self._cancel_download)
        self._load_clip_btn.clicked.connect(self._load_selected_clip)
        self._clips_list.itemDoubleClicked.connect(lambda _item: self._load_selected_clip())

    def _load_settings(self) -> None:
        saved_quality = str(self._settings.value(_YT_QUALITY_KEY, "Best Available (up to 4K+)") or "")
        idx = self._quality_combo.findText(saved_quality)
        if idx >= 0:
            self._quality_combo.setCurrentIndex(idx)
        self.refresh_default_folder()

    def _refresh_dependency_banner(self) -> None:
        has_yt_dlp, _ = check_dependencies()
        self._has_yt_dlp = has_yt_dlp
        self._dep_label.setVisible(not has_yt_dlp)

    def apply_theme(self, theme: ThemeTokens) -> None:
        """Update icons to match the active VidEdit theme."""
        c = theme.text_primary
        self._paste_btn.setIcon(make_monoline_icon("paste", c, size=14))
        self._download_btn.setIcon(make_monoline_icon("download", theme.color_primary_text, size=15))

    def is_downloading(self) -> bool:
        """Return True while a YouTube download worker is active."""
        return self._worker is not None and self._worker.isRunning()

    def refresh_default_folder(self) -> None:
        """Refresh the displayed save folder unless the user set a custom YouTube folder."""
        self._output_dir_edit.setText(str(resolve_default_youtube_dir(self._settings)))

    def focus_url_input(self) -> None:
        """Focus the active URL field when the drawer opens."""
        if self._batch_toggle_btn.isChecked():
            self._batch_edit.setFocus()
        else:
            self._url_edit.setFocus()
            self._url_edit.selectAll()

    def _on_batch_toggled(self, batch_mode: bool) -> None:
        if batch_mode:
            single_text = self._url_edit.text().strip()
            if single_text and not self._batch_edit.toPlainText().strip():
                self._batch_edit.setPlainText(single_text)
            self._url_caption.setText("Video Links")
        else:
            lines = parse_urls(self._batch_edit.toPlainText())
            if lines and not self._url_edit.text().strip():
                self._url_edit.setText(lines[0])
            self._url_caption.setText("Video Link")

        self._url_edit.setVisible(not batch_mode)
        self._batch_widget.setVisible(batch_mode)

    def _paste_clipboard(self) -> None:
        clipboard = QApplication.clipboard()
        clip = clipboard.text().strip() if clipboard is not None else ""
        if not clip:
            return
        parsed = parse_urls(clip)
        if len(parsed) > 1 and not self._batch_toggle_btn.isChecked():
            self._batch_toggle_btn.setChecked(True)

        if self._batch_toggle_btn.isChecked():
            existing = self._batch_edit.toPlainText().strip()
            combined = f"{existing}\n{clip}" if existing else clip
            self._batch_edit.setPlainText(combined)
        else:
            self._url_edit.setText(clip)

    def _clear_inputs(self) -> None:
        self._url_edit.clear()
        self._batch_edit.clear()

    def _save_quality(self, label: str) -> None:
        self._settings.setValue(_YT_QUALITY_KEY, label)

    def _browse_output_dir(self) -> None:
        start_dir = self._output_dir_edit.text().strip() or str(resolve_default_youtube_dir(self._settings))
        chosen = QFileDialog.getExistingDirectory(self, "Select YouTube Download Folder", start_dir)
        if chosen:
            self._output_dir_edit.setText(chosen)
            self._settings.setValue(_YT_DIR_KEY, chosen)
            self._settings.sync()

    def _save_custom_dir_from_edit(self) -> None:
        raw = self._output_dir_edit.text().strip()
        if raw:
            self._settings.setValue(_YT_DIR_KEY, raw)
            self._settings.sync()
        else:
            self._settings.remove(_YT_DIR_KEY)
            self._settings.sync()
            self.refresh_default_folder()

    def _open_output_dir(self) -> None:
        target = Path(self._output_dir_edit.text().strip() or str(resolve_default_youtube_dir(self._settings)))
        open_folder_in_explorer(target.expanduser())

    def _current_urls(self) -> list[str]:
        if self._batch_toggle_btn.isChecked():
            return parse_urls(self._batch_edit.toPlainText())
        return parse_urls(self._url_edit.text())

    def _set_busy(self, busy: bool) -> None:
        self._download_btn.setEnabled(not busy)
        self._url_edit.setEnabled(not busy)
        self._batch_edit.setEnabled(not busy)
        self._batch_toggle_btn.setEnabled(not busy)
        self._quality_combo.setEnabled(not busy)
        self._browse_dir_btn.setEnabled(not busy)
        self._output_dir_edit.setEnabled(not busy)
        self._cancel_btn.setVisible(busy)
        self._cancel_btn.setEnabled(busy)
        self.download_state_changed.emit(busy)

    def _start_download(self) -> None:
        if self.is_downloading():
            return
        self._refresh_dependency_banner()
        if not self._has_yt_dlp:
            QMessageBox.critical(
                self,
                "Missing yt-dlp",
                "yt-dlp is required for YouTube downloads.\n\n"
                "Launch VidEdit via VidEdit.bat to install it automatically, "
                "or run:\n  pip install yt-dlp",
            )
            return

        urls = self._current_urls()
        if not urls:
            QMessageBox.information(self, "No Link Entered", "Paste a YouTube link first.")
            self.focus_url_input()
            return

        output_raw = self._output_dir_edit.text().strip()
        output_dir = (
            Path(output_raw).expanduser()
            if output_raw
            else resolve_default_youtube_dir(self._settings)
        )
        max_height = QUALITY_OPTIONS.get(self._quality_combo.currentText())
        allow_playlists = self._batch_toggle_btn.isChecked() and self._playlist_check.isChecked()

        self._saved_paths.clear()
        self._clips_list.clear()
        self._clips_container.setVisible(False)
        self._progress_bar.setValue(0)
        self._progress_bar.setVisible(True)
        self._status_label.setText("Starting download...")
        self._set_busy(True)

        self._worker = YouTubeDownloadWorker(
            urls=urls,
            output_dir=output_dir,
            max_height=max_height,
            allow_playlists=allow_playlists,
        )
        self._worker.progress_changed.connect(self._on_progress)
        self._worker.file_downloaded.connect(self._on_file_downloaded)
        self._worker.finished_result.connect(self._on_finished)
        self._worker.dependency_error.connect(self._on_dependency_error)
        self._worker.start()

    def _cancel_download(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            self._status_label.setText("Cancelling...")
            self._cancel_btn.setEnabled(False)
            self._worker.cancel()

    def _on_progress(self, pct: int, status: str) -> None:
        self._progress_bar.setValue(pct)
        self._status_label.setText(status)
        self.progress_summary.emit(pct, status)

    def _on_file_downloaded(self, raw_path: str) -> None:
        path = Path(raw_path)
        if path not in self._saved_paths:
            self._saved_paths.append(path)

    def _on_finished(
        self,
        saved_paths_raw: list[str],
        failed_count: int,
        cancelled: bool,
        errors: list[str],
    ) -> None:
        self._set_busy(False)
        paths = [Path(p) for p in saved_paths_raw if Path(p).is_file()]

        if cancelled:
            self._status_label.setText(f"Cancelled ({len(paths)} saved).")
            if paths:
                self._populate_clips_list(paths)
            return

        if len(paths) == 1 and failed_count == 0:
            self._progress_bar.setValue(100)
            clip_path = paths[0]
            self._status_label.setText(f"Loaded: {clip_path.name}")
            self._populate_clips_list(paths)
            self.load_video_requested.emit(clip_path)
            return

        if paths:
            self._progress_bar.setValue(100 if failed_count == 0 else self._progress_bar.value())
            self._populate_clips_list(paths)
            if failed_count == 0:
                self._status_label.setText(f"Saved {len(paths)} clips. Select one to load.")
            else:
                self._status_label.setText(f"Saved {len(paths)} clip(s), {failed_count} failed.")
            return

        self._progress_bar.setVisible(False)
        detail = errors[0] if errors else "Download failed."
        self._status_label.setText(f"Failed: {detail}")
        QMessageBox.warning(self, "YouTube Download Failed", detail)

    def _on_dependency_error(self, message: str) -> None:
        self._set_busy(False)
        self._progress_bar.setVisible(False)
        self._status_label.setText(message)
        self._refresh_dependency_banner()
        QMessageBox.critical(self, "YouTube Download Error", message)

    def _populate_clips_list(self, paths: list[Path]) -> None:
        self._clips_list.clear()
        for path in paths:
            item = QListWidgetItem(path.name)
            item.setData(Qt.ItemDataRole.UserRole, str(path))
            item.setToolTip(str(path))
            self._clips_list.addItem(item)
        if self._clips_list.count() > 0:
            self._clips_list.setCurrentRow(0)
        self._clips_container.setVisible(self._clips_list.count() > 0)

    def _load_selected_clip(self) -> None:
        item = self._clips_list.currentItem()
        if item is None and self._clips_list.count() > 0:
            item = self._clips_list.item(0)
        if item is None:
            return
        raw_path = item.data(Qt.ItemDataRole.UserRole)
        if raw_path:
            path = Path(str(raw_path))
            if path.is_file():
                self.load_video_requested.emit(path)

    def shutdown(self) -> None:
        """Stop any running download thread before closing the window."""
        if self._worker is not None and self._worker.isRunning():
            self._worker.cancel()
            self._worker.wait(3000)
