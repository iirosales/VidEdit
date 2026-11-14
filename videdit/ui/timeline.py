"""Custom dual-handle timeline scrubber widget with heterochromatic In/Out markers."""

from __future__ import annotations

from typing import Literal, Optional

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QLinearGradient, QMouseEvent, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QWidget

from videdit.ui.theme import DARK_THEME, ThemeTokens


class TrimTimelineWidget(QWidget):
    """Interactive timeline with Cobalt In / Crimson Out handles and a diamond-tip playhead."""

    seek_requested = Signal(int)
    trim_changed = Signal(int, int)

    _PAD_X = 14
    _HANDLE_WIDTH = 10
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
        self._theme: ThemeTokens = DARK_THEME

    def apply_theme(self, theme: ThemeTokens) -> None:
        """Update timeline palette from active ThemeTokens."""
        self._theme = theme
        self.update()

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
        hit_radius = self._HANDLE_WIDTH + 3

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

        t = self._theme
        track = self._track_rect()

        # Recessed architectural track with 1px border
        painter.setPen(QPen(QColor(t.border_subtle), 1.0))
        painter.setBrush(QColor(t.bg_recessed))
        painter.drawRoundedRect(track, 2.0, 2.0)

        # Subtle horological ruler ticks along track
        tick_pen = QPen(QColor(t.border_hover), 1.0)
        painter.setPen(tick_pen)
        for idx in range(1, 12):
            tx = track.left() + (track.width() * idx) / 12.0
            tick_h = 6.0 if idx % 3 == 0 else 3.0
            painter.drawLine(QPointF(tx, track.bottom() - tick_h), QPointF(tx, track.bottom() - 1.0))

        if self._duration_ms <= 0:
            painter.end()
            return

        in_x = self._ms_to_x(self._in_ms)
        out_x = self._ms_to_x(self._out_ms)
        pos_x = self._ms_to_x(self._position_ms)

        # Heterochromatic Cobalt-to-Crimson gradient fill for kept span
        span_w = max(2.0, out_x - in_x)
        keep_rect = QRectF(in_x, track.top(), span_w, track.height())
        fill_grad = QLinearGradient(in_x, 0.0, out_x, 0.0)
        fill_grad.setColorAt(0.0, QColor(*t.timeline_span_start))
        fill_grad.setColorAt(1.0, QColor(*t.timeline_span_end))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(fill_grad)
        painter.drawRect(keep_rect)

        # Split Cobalt-to-Crimson top & bottom hairline borders
        edge_grad = QLinearGradient(in_x, 0.0, out_x, 0.0)
        edge_grad.setColorAt(0.0, QColor(t.accent_cobalt))
        edge_grad.setColorAt(1.0, QColor(t.accent_crimson))
        painter.setPen(QPen(edge_grad, 1.75))
        painter.drawLine(QPointF(in_x, track.top()), QPointF(out_x, track.top()))
        painter.drawLine(QPointF(in_x, track.bottom()), QPointF(out_x, track.bottom()))

        # Heterochromatic In (Cobalt) and Out (Crimson) handles
        self._draw_trim_handle(painter, in_x, track, QColor(t.accent_cobalt), is_left=True)
        self._draw_trim_handle(painter, out_x, track, QColor(t.accent_crimson), is_left=False)

        # Sharp playhead needle with diamond crown vertex
        needle_color = QColor(t.color_primary)
        painter.setPen(QPen(needle_color, 1.5))
        painter.drawLine(QPointF(pos_x, track.top() - 7.0), QPointF(pos_x, track.bottom() + 7.0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(needle_color)
        diamond = QPolygonF(
            [
                QPointF(pos_x, track.top() - 12.0),
                QPointF(pos_x + 4.0, track.top() - 8.0),
                QPointF(pos_x, track.top() - 4.0),
                QPointF(pos_x - 4.0, track.top() - 8.0),
            ]
        )
        painter.drawPolygon(diamond)

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
        painter.setPen(QPen(QColor(self._theme.bg_canvas), 1.0))
        painter.setBrush(color)
        painter.drawRoundedRect(handle_rect, 2.0, 2.0)

        # Fine mechanical grip tick
        painter.setPen(QPen(QColor("#FAF9FC"), 1.2))
        cx = handle_rect.center().x()
        painter.drawLine(
            QPointF(cx, handle_rect.top() + 6.0),
            QPointF(cx, handle_rect.bottom() - 6.0),
        )
