"""Custom video preview canvas with screenshot-style interactive crop overlay."""

from __future__ import annotations

from typing import Literal, Optional

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontDatabase,
    QFontMetricsF,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
)
from PySide6.QtMultimedia import QVideoFrame, QVideoSink
from PySide6.QtWidgets import QWidget

from videdit.ffmpeg_tools import (
    CropRect,
    GifCaption,
    compute_meme_caption_metrics,
    find_impact_font_path,
    normalize_crop_rect,
    wrap_meme_caption,
)
from videdit.ui.theme import DARK_THEME, ThemeTokens, resolve_font_family

DragHandle = Literal["new", "move", "tl", "t", "tr", "r", "br", "b", "bl", "l"]


class VideoPreviewWidget(QWidget):
    """QVideoSink-backed video surface supporting click-to-play and screenshot-style crop."""

    clicked = Signal()
    crop_changed = Signal(object)  # CropRect | None
    crop_mode_exit_requested = Signal()

    _HANDLE_HIT_RADIUS = 10.0
    _HANDLE_DRAW_HALF = 4.0
    _MIN_DRAG_PX = 8.0

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        self._sink = QVideoSink(self)
        self._sink.videoFrameChanged.connect(self._on_video_frame_changed)

        self._current_frame = QVideoFrame()
        self._source_size = QSize(0, 0)
        self._crop_mode = False
        self._crop_rect: Optional[CropRect] = None
        self._theme: ThemeTokens = DARK_THEME

        self._drag_mode: Optional[DragHandle] = None
        self._drag_origin = QPointF()
        self._drag_start_rect = QRectF()
        self._live_widget_rect: Optional[QRectF] = None
        self._caption_preview: Optional[GifCaption] = None
        self._impact_family: Optional[str] = None

    def videoSink(self) -> QVideoSink:
        """Expose the underlying QVideoSink so QMediaPlayer can target this widget."""
        return self._sink

    def apply_theme(self, theme: ThemeTokens) -> None:
        self._theme = theme
        self.update()

    def set_source_size(self, width: int, height: int) -> None:
        """Reset frame/crop state for a newly loaded source resolution."""
        self._source_size = QSize(max(0, width), max(0, height))
        self._current_frame = QVideoFrame()
        self._crop_rect = None
        self._live_widget_rect = None
        self._drag_mode = None
        self.update()

    def crop_rect(self) -> Optional[CropRect]:
        return self._crop_rect

    def set_crop_rect(self, crop: Optional[CropRect], emit: bool = False) -> None:
        sw, sh = self._effective_source_size()
        normalized = normalize_crop_rect(crop, sw, sh) if sw > 0 and sh > 0 else crop
        if normalized != self._crop_rect:
            self._crop_rect = normalized
            self._live_widget_rect = None
            self.update()
            if emit:
                self.crop_changed.emit(self._crop_rect)

    def is_crop_mode(self) -> bool:
        return self._crop_mode

    def set_crop_mode(self, enabled: bool) -> None:
        enabled = bool(enabled)
        if enabled == self._crop_mode:
            return
        self._crop_mode = enabled
        self._drag_mode = None
        self._live_widget_rect = None
        self._update_cursor(self.mapFromGlobal(self.cursor().pos()))
        self.update()

    def set_caption_preview(self, caption: Optional[GifCaption]) -> None:
        """Update the live meme caption preview overlay (or clear when None/inactive)."""
        next_caption = caption if (caption is not None and caption.is_active()) else None
        if next_caption != self._caption_preview:
            self._caption_preview = next_caption
            self.update()

    def caption_preview(self) -> Optional[GifCaption]:
        return self._caption_preview

    def _resolve_impact_family(self) -> str:
        if self._impact_family is not None:
            return self._impact_family
        impact_path = find_impact_font_path()
        if impact_path is not None:
            fid = QFontDatabase.addApplicationFont(str(impact_path))
            if fid >= 0:
                families = QFontDatabase.applicationFontFamilies(fid)
                if families:
                    self._impact_family = families[0]
                    return self._impact_family
        self._impact_family = resolve_font_family(("Impact", "Arial Black", "Arial"), "Arial")
        return self._impact_family

    def _on_video_frame_changed(self, frame: QVideoFrame) -> None:
        if frame.isValid():
            self._current_frame = frame
            if self._source_size.isEmpty() and not frame.size().isEmpty():
                self._source_size = frame.size()
            self.update()

    def _effective_source_size(self) -> tuple[int, int]:
        if not self._source_size.isEmpty():
            return self._source_size.width(), self._source_size.height()
        if self._current_frame.isValid() and not self._current_frame.size().isEmpty():
            sz = self._current_frame.size()
            return sz.width(), sz.height()
        return 0, 0

    def video_display_rect(self) -> QRectF:
        """Return the aspect-ratio-fitted rectangle where the video frame renders."""
        w = float(max(1, self.width()))
        h = float(max(1, self.height()))
        sw, sh = self._effective_source_size()
        if sw <= 0 or sh <= 0:
            return QRectF(0.0, 0.0, w, h)
        scale = min(w / float(sw), h / float(sh))
        dw = sw * scale
        dh = sh * scale
        return QRectF((w - dw) / 2.0, (h - dh) / 2.0, dw, dh)

    def _crop_to_widget_rect(self, crop: CropRect) -> QRectF:
        sw, sh = self._effective_source_size()
        target = self.video_display_rect()
        if sw <= 0 or sh <= 0:
            return target
        sx = target.width() / float(sw)
        sy = target.height() / float(sh)
        return QRectF(
            target.left() + crop.x * sx,
            target.top() + crop.y * sy,
            crop.width * sx,
            crop.height * sy,
        )

    def _widget_to_source_crop(self, wrect: QRectF) -> Optional[CropRect]:
        sw, sh = self._effective_source_size()
        target = self.video_display_rect()
        if sw <= 0 or sh <= 0 or target.width() <= 0 or target.height() <= 0:
            return None
        clipped = wrect.normalized().intersected(target)
        if clipped.width() < self._MIN_DRAG_PX or clipped.height() < self._MIN_DRAG_PX:
            return None
        sx = float(sw) / target.width()
        sy = float(sh) / target.height()
        raw = CropRect(
            x=int(round((clipped.left() - target.left()) * sx)),
            y=int(round((clipped.top() - target.top()) * sy)),
            width=int(round(clipped.width() * sx)),
            height=int(round(clipped.height() * sy)),
        )
        return normalize_crop_rect(raw, sw, sh)

    def _handle_points(self, rect: QRectF) -> dict[DragHandle, QPointF]:
        cx = rect.center().x()
        cy = rect.center().y()
        return {
            "tl": rect.topLeft(),
            "t": QPointF(cx, rect.top()),
            "tr": rect.topRight(),
            "r": QPointF(rect.right(), cy),
            "br": rect.bottomRight(),
            "b": QPointF(cx, rect.bottom()),
            "bl": rect.bottomLeft(),
            "l": QPointF(rect.left(), cy),
        }

    def _hit_test(self, pos: QPointF) -> DragHandle:
        if self._crop_rect is None:
            return "new"
        rect = self._crop_to_widget_rect(self._crop_rect)
        for handle, pt in self._handle_points(rect).items():
            if abs(pos.x() - pt.x()) <= self._HANDLE_HIT_RADIUS and abs(pos.y() - pt.y()) <= self._HANDLE_HIT_RADIUS:
                return handle
        if rect.contains(pos):
            return "move"
        return "new"

    def _cursor_for_handle(self, handle: DragHandle) -> Qt.CursorShape:
        if handle in ("tl", "br"):
            return Qt.CursorShape.SizeFDiagCursor
        if handle in ("tr", "bl"):
            return Qt.CursorShape.SizeBDiagCursor
        if handle in ("l", "r"):
            return Qt.CursorShape.SizeHorCursor
        if handle in ("t", "b"):
            return Qt.CursorShape.SizeVerCursor
        if handle == "move":
            return Qt.CursorShape.SizeAllCursor
        return Qt.CursorShape.CrossCursor

    def _update_cursor(self, pos: QPointF) -> None:
        if not self._crop_mode:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
            return
        self.setCursor(self._cursor_for_handle(self._hit_test(pos)))

    def _clamp_to_display(self, pos: QPointF) -> QPointF:
        target = self.video_display_rect()
        x = max(target.left(), min(target.right(), pos.x()))
        y = max(target.top(), min(target.bottom(), pos.y()))
        return QPointF(x, y)

    def _min_widget_extent(self) -> tuple[float, float]:
        sw, sh = self._effective_source_size()
        target = self.video_display_rect()
        if sw <= 0 or sh <= 0:
            return 16.0, 16.0
        min_w = max(12.0, 16.0 * (target.width() / float(sw)))
        min_h = max(12.0, 16.0 * (target.height() / float(sh)))
        return min(min_w, target.width()), min(min_h, target.height())

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return

        if not self._crop_mode:
            self.clicked.emit()
            event.accept()
            return

        sw, sh = self._effective_source_size()
        if sw <= 0 or sh <= 0:
            return

        pos = self._clamp_to_display(event.position())
        self._drag_mode = self._hit_test(pos)
        self._drag_origin = pos
        if self._crop_rect is not None and self._drag_mode != "new":
            self._drag_start_rect = self._crop_to_widget_rect(self._crop_rect)
            self._live_widget_rect = QRectF(self._drag_start_rect)
        else:
            self._drag_start_rect = QRectF(pos, pos)
            self._live_widget_rect = QRectF(pos, pos)
        self.update()
        event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        pos = event.position()
        if not self._crop_mode or self._drag_mode is None:
            self._update_cursor(pos)
            super().mouseMoveEvent(event)
            return

        clamped = self._clamp_to_display(pos)
        target = self.video_display_rect()
        min_w, min_h = self._min_widget_extent()
        r = QRectF(self._drag_start_rect)

        if self._drag_mode == "new":
            r = QRectF(self._drag_origin, clamped).normalized()
        elif self._drag_mode == "move":
            dx = clamped.x() - self._drag_origin.x()
            dy = clamped.y() - self._drag_origin.y()
            dx = max(target.left() - r.left(), min(target.right() - r.right(), dx))
            dy = max(target.top() - r.top(), min(target.bottom() - r.bottom(), dy))
            r.translate(dx, dy)
        else:
            if "l" in self._drag_mode:
                r.setLeft(min(clamped.x(), r.right() - min_w))
            if "r" in self._drag_mode:
                r.setRight(max(clamped.x(), r.left() + min_w))
            if "t" in self._drag_mode:
                r.setTop(min(clamped.y(), r.bottom() - min_h))
            if "b" in self._drag_mode:
                r.setBottom(max(clamped.y(), r.top() + min_h))

        self._live_widget_rect = r
        live_crop = self._widget_to_source_crop(r)
        if live_crop != self._crop_rect:
            self._crop_rect = live_crop
            self.crop_changed.emit(self._crop_rect)
        self.update()
        event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton or not self._crop_mode or self._drag_mode is None:
            super().mouseReleaseEvent(event)
            return

        final_rect = self._live_widget_rect
        self._drag_mode = None
        self._live_widget_rect = None
        if final_rect is not None:
            final_crop = self._widget_to_source_crop(final_rect)
            if final_crop != self._crop_rect:
                self._crop_rect = final_crop
                self.crop_changed.emit(self._crop_rect)
        self._update_cursor(event.position())
        self.update()
        event.accept()

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if self._crop_mode and event.button() == Qt.MouseButton.LeftButton:
            if self._crop_rect is not None and self._crop_to_widget_rect(self._crop_rect).contains(event.position()):
                self.crop_mode_exit_requested.emit()
                event.accept()
                return
        elif not self._crop_mode and event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor("#08070B"))

        target = self.video_display_rect()
        if self._current_frame.isValid():
            self._current_frame.paint(painter, target, QVideoFrame.PaintOptions())

        active_wrect = self._live_widget_rect
        if active_wrect is None and self._crop_rect is not None:
            active_wrect = self._crop_to_widget_rect(self._crop_rect)

        clipped_crop = active_wrect.intersected(target) if active_wrect is not None else None
        if self._caption_preview is not None:
            caption_region = clipped_crop if clipped_crop is not None else target
            self._paint_caption_overlay(painter, caption_region, self._caption_preview)

        if self._crop_mode and active_wrect is None:
            self._paint_crop_prompt(painter, target)
            return

        if clipped_crop is not None:
            self._paint_crop_overlay(painter, target, clipped_crop)

    def _paint_caption_overlay(
        self,
        painter: QPainter,
        region: QRectF,
        caption: GifCaption,
    ) -> None:
        if region.width() < 24.0 or region.height() < 24.0 or not caption.is_active():
            return

        font_px, border_w = compute_meme_caption_metrics(
            region.width(),
            region.height(),
            size_scale=caption.size_scale,
        )
        font = QFont(self._resolve_impact_family())
        font.setPixelSize(max(10, int(round(font_px))))
        fm = QFontMetricsF(font)
        line_step = font_px * 1.12
        stroke_pen = QPen(
            QColor("#000000"),
            max(2.0, float(border_w) * 1.6),
            Qt.PenStyle.SolidLine,
            Qt.PenCapStyle.RoundCap,
            Qt.PenJoinStyle.RoundJoin,
        )

        painter.save()
        painter.setClipRect(region)

        def draw_lines(lines: list[str], is_top: bool) -> None:
            total = len(lines)
            for idx, line in enumerate(lines):
                line_w = fm.horizontalAdvance(line)
                x = region.center().x() - line_w / 2.0
                if is_top:
                    baseline_y = region.top() + region.height() * 0.04 + fm.ascent() + idx * line_step
                else:
                    offset = total - 1 - idx
                    baseline_y = region.bottom() - region.height() * 0.04 - fm.descent() - offset * line_step
                path = QPainterPath()
                path.addText(x, baseline_y, font, line)
                painter.strokePath(path, stroke_pen)
                painter.fillPath(path, QColor("#FFFFFF"))

        top_lines = wrap_meme_caption(
            caption.top_text,
            region.width(),
            font_px,
            uppercase=caption.uppercase,
        )
        if top_lines:
            draw_lines(top_lines, is_top=True)

        bottom_lines = wrap_meme_caption(
            caption.bottom_text,
            region.width(),
            font_px,
            uppercase=caption.uppercase,
        )
        if bottom_lines:
            draw_lines(bottom_lines, is_top=False)

        painter.restore()

    def _paint_crop_prompt(self, painter: QPainter, target: QRectF) -> None:
        painter.fillRect(target, QColor(10, 9, 14, 72))
        border_pen = QPen(QColor(245, 243, 236, 140), 1.0, Qt.PenStyle.DashLine)
        painter.setPen(border_pen)
        painter.drawRect(target.adjusted(1.5, 1.5, -1.5, -1.5))

        hint = "Drag across video to crop · Esc to exit"
        font = QFont(self.font())
        font.setPointSize(10)
        font.setWeight(QFont.Weight.Medium)
        painter.setFont(font)
        fm = painter.fontMetrics()
        pill_w = fm.horizontalAdvance(hint) + 28
        pill_h = 30.0
        pill = QRectF(
            target.center().x() - pill_w / 2.0,
            target.center().y() - pill_h / 2.0,
            pill_w,
            pill_h,
        )
        painter.setPen(QPen(QColor(self._theme.border_hover), 1.0))
        painter.setBrush(QColor(17, 14, 22, 220))
        painter.drawRoundedRect(pill, 4.0, 4.0)
        painter.setPen(QColor("#F4F1F8"))
        painter.drawText(pill, Qt.AlignmentFlag.AlignCenter, hint)

    def _paint_crop_overlay(self, painter: QPainter, target: QRectF, crop_rect: QRectF) -> None:
        scrim_alpha = 165 if self._crop_mode else 115
        scrim = QColor(10, 9, 14, scrim_alpha)

        # Darken outside the kept crop region without obscuring inside pixels.
        painter.fillRect(QRectF(target.left(), target.top(), target.width(), max(0.0, crop_rect.top() - target.top())), scrim)
        painter.fillRect(QRectF(target.left(), crop_rect.bottom(), target.width(), max(0.0, target.bottom() - crop_rect.bottom())), scrim)
        painter.fillRect(QRectF(target.left(), crop_rect.top(), max(0.0, crop_rect.left() - target.left()), crop_rect.height()), scrim)
        painter.fillRect(QRectF(crop_rect.right(), crop_rect.top(), max(0.0, target.right() - crop_rect.right()), crop_rect.height()), scrim)

        if not self._crop_mode:
            painter.setPen(QPen(QColor(10, 9, 14, 150), 2.5))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(crop_rect)
            painter.setPen(QPen(QColor(245, 243, 236, 195), 1.0, Qt.PenStyle.DashLine))
            painter.drawRect(crop_rect)
            return

        # Rule-of-thirds guides inside active selection
        if crop_rect.width() > 36 and crop_rect.height() > 36:
            painter.setPen(QPen(QColor(245, 243, 236, 65), 1.0))
            for frac in (1.0 / 3.0, 2.0 / 3.0):
                x = crop_rect.left() + crop_rect.width() * frac
                y = crop_rect.top() + crop_rect.height() * frac
                painter.drawLine(QPointF(x, crop_rect.top()), QPointF(x, crop_rect.bottom()))
                painter.drawLine(QPointF(crop_rect.left(), y), QPointF(crop_rect.right(), y))

        # High-contrast dark halo + Porcelain Cream border
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(10, 9, 14, 180), 3.0))
        painter.drawRect(crop_rect)
        painter.setPen(QPen(QColor("#F5F3EC"), 1.5))
        painter.drawRect(crop_rect)

        # Corner brackets (Cobalt top-left / Crimson bottom-right accent) and 8 handles
        handle_half = self._HANDLE_DRAW_HALF
        for handle, pt in self._handle_points(crop_rect).items():
            box = QRectF(pt.x() - handle_half, pt.y() - handle_half, handle_half * 2.0, handle_half * 2.0)
            painter.setPen(QPen(QColor("#110E16"), 1.25))
            if handle in ("tl", "l", "t"):
                painter.setBrush(QColor(self._theme.accent_cobalt))
            elif handle in ("br", "r", "b"):
                painter.setBrush(QColor(self._theme.accent_crimson))
            else:
                painter.setBrush(QColor("#F5F3EC"))
            painter.drawRect(box)

        # Dimension badge
        if self._crop_rect is not None:
            label = f"{self._crop_rect.width} × {self._crop_rect.height}"
            font = QFont(self.font())
            font.setPointSize(9)
            font.setWeight(QFont.Weight.SemiBold)
            painter.setFont(font)
            fm = painter.fontMetrics()
            bw = float(fm.horizontalAdvance(label) + 14)
            bh = 20.0
            by = crop_rect.top() - bh - 6.0
            if by < target.top() + 4.0:
                by = crop_rect.top() + 6.0
            bx = max(target.left() + 4.0, min(target.right() - bw - 4.0, crop_rect.left()))
            badge = QRectF(bx, by, bw, bh)
            painter.setPen(QPen(QColor(self._theme.border_hover), 1.0))
            painter.setBrush(QColor(17, 14, 22, 225))
            painter.drawRoundedRect(badge, 3.0, 3.0)
            painter.setPen(QColor("#F4F1F8"))
            painter.drawText(badge, Qt.AlignmentFlag.AlignCenter, label)
