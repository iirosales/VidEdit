"""Razor-thin monoline geometric icons and horological clockwork spinner."""

from __future__ import annotations

import math
from typing import Optional

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import QWidget


def _new_pen(color: str, width: float = 1.25) -> QPen:
    pen = QPen(QColor(color), width)
    pen.setCapStyle(Qt.PenCapStyle.FlatCap)
    pen.setJoinStyle(Qt.PenJoinStyle.MiterJoin)
    return pen


def make_monoline_icon(name: str, color: str, size: int = 16) -> QIcon:
    """Render a crisp 1.25px mitered geometric icon into a QIcon."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    p = QPainter(pixmap)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(_new_pen(color))
    p.setBrush(Qt.BrushStyle.NoBrush)

    m = 3.0
    s = float(size)
    mid = s / 2.0

    if name == "play":
        poly = QPolygonF([QPointF(m + 1.5, m), QPointF(s - m, mid), QPointF(m + 1.5, s - m)])
        p.drawPolygon(poly)
    elif name == "pause":
        p.drawRect(QRectF(m + 1.0, m + 0.5, 2.5, s - 2 * m - 1.0))
        p.drawRect(QRectF(s - m - 3.5, m + 0.5, 2.5, s - 2 * m - 1.0))
    elif name == "step_back":
        p.drawLine(QPointF(m + 1.5, m + 1.0), QPointF(m + 1.5, s - m - 1.0))
        p.drawPolyline(
            QPolygonF([QPointF(s - m - 1.0, m + 1.0), QPointF(m + 4.5, mid), QPointF(s - m - 1.0, s - m - 1.0)])
        )
    elif name == "step_fwd":
        p.drawLine(QPointF(s - m - 1.5, m + 1.0), QPointF(s - m - 1.5, s - m - 1.0))
        p.drawPolyline(
            QPolygonF([QPointF(m + 1.0, m + 1.0), QPointF(s - m - 4.5, mid), QPointF(m + 1.0, s - m - 1.0)])
        )
    elif name == "open":
        p.drawPolygon(
            QPolygonF(
                [
                    QPointF(m, m + 1.5),
                    QPointF(mid - 1.0, m + 1.5),
                    QPointF(mid + 1.0, m + 3.5),
                    QPointF(s - m, m + 3.5),
                    QPointF(s - m, s - m),
                    QPointF(m, s - m),
                ]
            )
        )
    elif name == "volume":
        p.drawPolygon(
            QPolygonF(
                [
                    QPointF(m, mid - 2.0),
                    QPointF(m + 2.5, mid - 2.0),
                    QPointF(mid + 0.5, m + 0.5),
                    QPointF(mid + 0.5, s - m - 0.5),
                    QPointF(m + 2.5, mid + 2.0),
                    QPointF(m, mid + 2.0),
                ]
            )
        )
        p.drawLine(QPointF(mid + 3.0, mid - 2.5), QPointF(mid + 3.0, mid + 2.5))
        p.drawLine(QPointF(s - m, m + 1.5), QPointF(s - m, s - m - 1.5))
    elif name == "muted":
        p.drawPolygon(
            QPolygonF(
                [
                    QPointF(m, mid - 2.0),
                    QPointF(m + 2.5, mid - 2.0),
                    QPointF(mid + 0.5, m + 0.5),
                    QPointF(mid + 0.5, s - m - 0.5),
                    QPointF(m + 2.5, mid + 2.0),
                    QPointF(m, mid + 2.0),
                ]
            )
        )
        p.drawLine(QPointF(mid + 2.5, mid - 2.5), QPointF(s - m, mid + 2.5))
        p.drawLine(QPointF(s - m, mid - 2.5), QPointF(mid + 2.5, mid + 2.5))
    elif name == "theme_light":
        # Crowned sun / dial
        p.drawEllipse(QPointF(mid, mid), 3.2, 3.2)
        for deg in range(0, 360, 45):
            rad = math.radians(deg)
            p.drawLine(
                QPointF(mid + math.cos(rad) * 4.8, mid + math.sin(rad) * 4.8),
                QPointF(mid + math.cos(rad) * 6.5, mid + math.sin(rad) * 6.5),
            )
    elif name == "theme_dark":
        # Crescent + diamond star
        p.drawArc(QRectF(m + 0.5, m + 0.5, s - 2 * m - 1.0, s - 2 * m - 1.0), 40 * 16, 230 * 16)
        p.drawLine(QPointF(s - m - 2.0, m + 1.5), QPointF(s - m - 2.0, m + 4.5))
        p.drawLine(QPointF(s - m - 3.5, m + 3.0), QPointF(s - m - 0.5, m + 3.0))

    p.end()
    return QIcon(pixmap)


class ClockworkSpinner(QWidget):
    """12-tick horological watch dial indicator for the status bar."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFixedSize(18, 18)
        self._active = False
        self._angle = 0.0
        self._dial_color = QColor("#A597B2")
        self._hand_color = QColor("#3D7EFF")

        self._timer = QTimer(self)
        self._timer.setInterval(45)
        self._timer.timeout.connect(self._tick)

    def set_colors(self, dial_color: str, hand_color: str) -> None:
        self._dial_color = QColor(dial_color)
        self._hand_color = QColor(hand_color)
        self.update()

    def set_active(self, active: bool) -> None:
        self._active = active
        if active:
            self._timer.start()
        else:
            self._timer.stop()
            self._angle = 0.0
        self.update()

    def _tick(self) -> None:
        self._angle = (self._angle + 18.0) % 360.0
        self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        cx = self.width() / 2.0
        cy = self.height() / 2.0
        r = min(cx, cy) - 2.0

        # 12 minute ticks
        p.setPen(QPen(self._dial_color, 1.0))
        for idx in range(12):
            ang = math.radians(idx * 30.0)
            inner = r - (2.2 if idx % 3 == 0 else 1.3)
            p.drawLine(
                QPointF(cx + math.cos(ang) * inner, cy + math.sin(ang) * inner),
                QPointF(cx + math.cos(ang) * r, cy + math.sin(ang) * r),
            )

        # Rotating watch hand
        hand_ang = math.radians(self._angle - 90.0)
        p.setPen(QPen(self._hand_color, 1.35))
        p.drawLine(
            QPointF(cx, cy),
            QPointF(cx + math.cos(hand_ang) * (r - 1.5), cy + math.sin(hand_ang) * (r - 1.5)),
        )
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(self._hand_color)
        p.drawEllipse(QPointF(cx, cy), 1.4, 1.4)
        p.end()
