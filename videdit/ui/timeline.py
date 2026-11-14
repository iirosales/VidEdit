"""Custom dual-handle timeline scrubber widget for In/Out video trimming."""

from __future__ import annotations

from typing import Literal, Optional

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QMouseEvent, QPainter, QPen
from PySide6.QtWidgets import QWidget


class TrimTimelineWidget(QWidget):
    """Interactive timeline with draggable In/Out trim handles and a scrubbable playhead."""

    seek_requested = Signal(int)
    trim_changed = Signal(int, int)

    _PAD_X = 14
    _HANDLE_WIDTH = 12
    _MIN_CLIP_MS = 200

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(52)
        self.setMaximumHeight(56)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        self._duration_ms: int = 0
        self._position_ms: int = 0
        self._in_ms: int = 0
        self._out_ms: int = 0
        self._dragging: Optional[Literal["in", "out", "playhead"]] = None

    def set_duration(self, duration_ms: int) -> None:
        """Reset timeline bounds for a newly loaded video."""
        self._duration_ms = max(0, duration_ms)
        self._position_ms = 0
        self._in_ms = 0
        self._out_ms = self._duration_ms
        self.update()

    def set_position(self, position_ms: int) -> None:
        """Update playhead position without emitting seek_requested."""
        clamped = max(0, min(position_ms, self._duration_ms))
        if clamped != self._position_ms:
            self._position_ms = clamped
            self.update()

    def set_trim_points(self, in_ms: int, out_ms: int, emit: bool = False) -> None:
        """Set In/Out handles programmatically with safe clamping."""
        if self._duration_ms <= 0:
            return
        min_gap = min(self._MIN_CLIP_MS, self._duration_ms)
        in_clamped = max(0, min(in_ms, self._duration_ms - min_gap))
        out_clamped = max(in_clamped + min_gap, min(out_ms, self._duration_ms))
        self._in_ms = in_clamped
        self._out_ms = out_clamped
        self.update()
        if emit:
            self.trim_changed.emit(self._in_ms, self._out_ms)

    def in_ms(self) -> int:
        return self._in_ms

    def out_ms(self) -> int:
        return self._out_ms

    def _track_rect(self) -> QRectF:
        w = max(10.0, float(self.width() - 2 * self._PAD_X))
        h = 18.0
        y = (self.height() - h) / 2.0
        return QRectF(float(self._PAD_X), y, w, h)

    def _ms_to_x(self, ms: int) -> float:
        track = self._track_rect()
        if self._duration_ms <= 0:
            return track.left()
        ratio = max(0.0, min(1.0, ms / float(self._duration_ms)))
        return track.left() + ratio * track.width()

    def _x_to_ms(self, x: float) -> int:
        track = self._track_rect()
        if self._duration_ms <= 0 or track.width() <= 0:
            return 0
        ratio = max(0.0, min(1.0, (x - track.left()) / track.width()))
        return int(round(ratio * self._duration_ms))

    def _hit_test(self, x: float) -> Literal["in", "out", "playhead"]:
        in_x = self._ms_to_x(self._in_ms)
        out_x = self._ms_to_x(self._out_ms)
        dist_in = abs(x - in_x)
        dist_out = abs(x - out_x)
        hit_radius = self._HANDLE_WIDTH + 2

        if dist_in <= hit_radius and dist_in <= dist_out:
            return "in"
        if dist_out <= hit_radius:
            return "out"
        return "playhead"

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton or self._duration_ms <= 0:
            return
        x = event.position().x()
        self._dragging = self._hit_test(x)
        self._apply_drag(x)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        x = event.position().x()
        if self._dragging is not None:
            self._apply_drag(x)
            return

        if self._duration_ms > 0:
            target = self._hit_test(x)
            if target in ("in", "out"):
                self.setCursor(Qt.CursorShape.SizeHorCursor)
            else:
                self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = None

    def _apply_drag(self, x: float) -> None:
        if self._duration_ms <= 0:
            return
        ms = self._x_to_ms(x)
        min_gap = min(self._MIN_CLIP_MS, self._duration_ms)

        if self._dragging == "in":
            new_in = max(0, min(ms, self._out_ms - min_gap))
            if new_in != self._in_ms:
                self._in_ms = new_in
                self.trim_changed.emit(self._in_ms, self._out_ms)
            self._position_ms = self._in_ms
            self.seek_requested.emit(self._in_ms)
            self.update()
        elif self._dragging == "out":
            new_out = min(self._duration_ms, max(ms, self._in_ms + min_gap))
            if new_out != self._out_ms:
                self._out_ms = new_out
                self.trim_changed.emit(self._in_ms, self._out_ms)
            self._position_ms = self._out_ms
            self.seek_requested.emit(self._out_ms)
            self.update()
        else:
            self._position_ms = ms
            self.seek_requested.emit(ms)
            self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        track = self._track_rect()
        # Base inactive track
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#222631"))
        painter.drawRoundedRect(track, 5.0, 5.0)

        if self._duration_ms <= 0:
            painter.end()
            return

        in_x = self._ms_to_x(self._in_ms)
        out_x = self._ms_to_x(self._out_ms)
        pos_x = self._ms_to_x(self._position_ms)

        # Selected keep region
        keep_rect = QRectF(in_x, track.top(), max(2.0, out_x - in_x), track.height())
        painter.setBrush(QColor(88, 101, 242, 95))
        painter.drawRect(keep_rect)

        # Top/bottom accent border on kept span
        painter.setPen(QPen(QColor("#5865f2"), 2.0))
        painter.drawLine(int(in_x), int(track.top()), int(out_x), int(track.top()))
        painter.drawLine(int(in_x), int(track.bottom()), int(out_x), int(track.bottom()))

        # In handle (emerald green)
        self._draw_trim_handle(painter, in_x, track, QColor("#3ba55d"), is_left=True)
        # Out handle (warm amber)
        self._draw_trim_handle(painter, out_x, track, QColor("#faa61a"), is_left=False)

        # Playhead needle
        painter.setPen(QPen(QColor("#ffffff"), 2.0))
        painter.drawLine(int(pos_x), int(track.top() - 8), int(pos_x), int(track.bottom() + 8))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#ffffff"))
        painter.drawEllipse(QRectF(pos_x - 4.0, track.top() - 10.0, 8.0, 8.0))

        painter.end()

    def _draw_trim_handle(
        self,
        painter: QPainter,
        x: float,
        track: QRectF,
        color: QColor,
        is_left: bool,
    ) -> None:
        w = float(self._HANDLE_WIDTH)
        handle_rect = QRectF(
            x - (w if is_left else 0.0),
            track.top() - 4.0,
            w,
            track.height() + 8.0,
        )
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        painter.drawRoundedRect(handle_rect, 4.0, 4.0)

        # Subtle grip line
        painter.setPen(QPen(QColor("#111318"), 1.5))
        cx = handle_rect.center().x()
        painter.drawLine(
            int(cx),
            int(handle_rect.top() + 6),
            int(cx),
            int(handle_rect.bottom() - 6),
        )
