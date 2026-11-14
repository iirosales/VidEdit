"""Custom frameless window chrome, damask dropstage, and Ctrl+K command palette."""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QKeyEvent, QMouseEvent, QPainter, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from videdit.ui.theme import ThemeTokens


class TitleBar(QFrame):
    """36px frameless desktop titlebar with centered serif title and custom window controls."""

    minimize_requested = Signal()
    maximize_requested = Signal()
    close_requested = Signal()

    def __init__(self, parent: QWidget, icon_path: Optional[Path] = None) -> None:
        super().__init__(parent)
        self.setObjectName("TitleBar")
        self.setFixedHeight(36)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 0, 0, 0)
        layout.setSpacing(8)

        # Left emblem / favicon + subtle balance spacer for centered title
        self._icon_label = QLabel()
        self._icon_label.setFixedSize(18, 18)
        if icon_path is not None and icon_path.is_file():
            pix = QIcon(str(icon_path)).pixmap(18, 18)
            self._icon_label.setPixmap(pix)
        layout.addWidget(self._icon_label)

        left_spacer = QWidget()
        left_spacer.setFixedWidth(108)
        layout.addWidget(left_spacer)

        self._title_label = QLabel("VIDEDIT")
        self._title_label.setObjectName("TitleBarText")
        self._title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._title_label, stretch=1)

        # Custom geometric window controls (44px wide x 35px high)
        self._min_btn = QPushButton("—")
        self._min_btn.setObjectName("WinBtn")
        self._min_btn.setFixedSize(44, 35)
        self._min_btn.setToolTip("Minimize")

        self._max_btn = QPushButton("□")
        self._max_btn.setObjectName("WinBtn")
        self._max_btn.setFixedSize(44, 35)
        self._max_btn.setToolTip("Maximize / Restore")

        self._close_btn = QPushButton("×")
        self._close_btn.setObjectName("WinCloseBtn")
        self._close_btn.setFixedSize(46, 35)
        self._close_btn.setToolTip("Close")

        layout.addWidget(self._min_btn)
        layout.addWidget(self._max_btn)
        layout.addWidget(self._close_btn)

        self._min_btn.clicked.connect(self.minimize_requested.emit)
        self._max_btn.clicked.connect(self.maximize_requested.emit)
        self._close_btn.clicked.connect(self.close_requested.emit)

    def set_document_title(self, subtitle: Optional[str] = None) -> None:
        if subtitle:
            self._title_label.setText(f"VIDEDIT  ·  {subtitle.upper()}")
        else:
            self._title_label.setText("VIDEDIT")

    def set_maximized_state(self, maximized: bool) -> None:
        self._max_btn.setText("❐" if maximized else "□")

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            win = self.window().windowHandle()
            if win is not None:
                win.startSystemMove()
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.maximize_requested.emit()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


class DropStageWidget(QWidget):
    """Empty video stage with subtle horological dial & damask lattice filigree."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._watermark_color = QColor(213, 205, 222, 10)
        self._cobalt_accent = QColor(61, 126, 255, 26)
        self._crimson_accent = QColor(224, 40, 74, 26)

    def apply_theme(self, theme: ThemeTokens) -> None:
        r, g, b, a = theme.watermark_rgba
        self._watermark_color = QColor(r, g, b, a)
        c1 = QColor(theme.accent_cobalt)
        c1.setAlpha(28)
        c2 = QColor(theme.accent_crimson)
        c2.setAlpha(28)
        self._cobalt_accent = c1
        self._crimson_accent = c2
        self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        w = float(self.width())
        h = float(self.height())
        cx = w / 2.0
        cy = h / 2.0

        # Tonal damask diamond lattice
        step = 48.0
        p.setPen(QPen(self._watermark_color, 1.0))
        x = -h
        while x < w + h:
            p.drawLine(QPointF(x, 0.0), QPointF(x + h, h))
            p.drawLine(QPointF(x + h, 0.0), QPointF(x, h))
            x += step

        # Concentric horological dial rings behind the drop callout
        outer_r = min(w, h) * 0.36
        if outer_r > 40.0:
            p.setPen(QPen(self._cobalt_accent, 1.0))
            p.drawEllipse(QPointF(cx, cy), outer_r, outer_r)
            p.setPen(QPen(self._crimson_accent, 1.0))
            p.drawEllipse(QPointF(cx, cy), outer_r * 0.84, outer_r * 0.84)

            p.setPen(QPen(self._watermark_color, 1.25))
            for idx in range(24):
                ang = math.radians(idx * 15.0)
                inner = outer_r * (0.86 if idx % 2 == 0 else 0.92)
                p.drawLine(
                    QPointF(cx + math.cos(ang) * inner, cy + math.sin(ang) * inner),
                    QPointF(cx + math.cos(ang) * outer_r, cy + math.sin(ang) * outer_r),
                )

            # Crown-like cardinal diamond vertices
            for deg in (0, 90, 180, 270):
                rad = math.radians(deg)
                vx = cx + math.cos(rad) * outer_r
                vy = cy + math.sin(rad) * outer_r
                diamond = QPolygonF(
                    [
                        QPointF(vx, vy - 4.0),
                        QPointF(vx + 4.0, vy),
                        QPointF(vx, vy + 4.0),
                        QPointF(vx - 4.0, vy),
                    ]
                )
                p.drawPolygon(diamond)

        p.end()


@dataclass(frozen=True)
class CommandEntry:
    """Action entry shown in the Ctrl+K Command Palette."""

    label: str
    shortcut: str
    callback: Callable[[], None]


class CommandPalette(QDialog):
    """Floating horological command dial triggered via Ctrl+K."""

    def __init__(self, parent: QWidget, commands: list[CommandEntry], theme: ThemeTokens) -> None:
        super().__init__(parent, Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
        self.setModal(True)
        self.setFixedWidth(480)
        self._commands = commands

        outer = QVBoxLayout(self)
        outer.setContentsMargins(1, 1, 1, 1)
        frame = QFrame()
        frame.setStyleSheet(
            f"QFrame {{"
            f"  background-color: {theme.bg_popover};"
            f"  border: 1px solid qlineargradient(x1:0, y1:0, x2:1, y2:0, "
            f"    stop:0 {theme.accent_cobalt}, stop:1 {theme.accent_crimson});"
            f"  border-radius: 3px;"
            f"}}"
        )
        outer.addWidget(frame)

        layout = QVBoxLayout(frame)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        header = QLabel("COMMAND DIAL")
        header.setObjectName("SectionTitle")
        layout.addWidget(header)

        self._search = QLineEdit()
        self._search.setPlaceholderText("Type a command (e.g., 10 MB, GIF, Theme, In, Out)...")
        layout.addWidget(self._search)

        self._list = QListWidget()
        self._list.setObjectName("CommandList")
        self._list.setMaximumHeight(280)
        layout.addWidget(self._list)

        self._search.textChanged.connect(self._populate)
        self._list.itemActivated.connect(self._run_selected)
        self._populate("")

    def _populate(self, query: str) -> None:
        self._list.clear()
        q = query.strip().lower()
        for entry in self._commands:
            if q and q not in entry.label.lower() and q not in entry.shortcut.lower():
                continue
            hint = f"    [{entry.shortcut}]" if entry.shortcut else ""
            item = QListWidgetItem(f"{entry.label}{hint}")
            item.setData(Qt.ItemDataRole.UserRole, entry)
            self._list.addItem(item)
        if self._list.count() > 0:
            self._list.setCurrentRow(0)

    def _run_selected(self, item: QListWidgetItem) -> None:
        entry: Optional[CommandEntry] = item.data(Qt.ItemDataRole.UserRole)
        self.accept()
        if entry is not None:
            entry.callback()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        key = event.key()
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            current = self._list.currentItem()
            if current is not None:
                self._run_selected(current)
                return
        elif key == Qt.Key.Key_Down and self._list.count() > 0:
            row = (self._list.currentRow() + 1) % self._list.count()
            self._list.setCurrentRow(row)
            return
        elif key == Qt.Key.Key_Up and self._list.count() > 0:
            row = (self._list.currentRow() - 1) % self._list.count()
            self._list.setCurrentRow(row)
            return
        super().keyPressEvent(event)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._search.setFocus()
