"""Custom video & meme preview canvas with crop and interactive multi-layer overlays."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Literal, Optional

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QUrl, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontDatabase,
    QFontMetricsF,
    QImage,
    QImageReader,
    QMouseEvent,
    QMovie,
    QPainter,
    QPainterPath,
    QPen,
)
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer, QVideoFrame, QVideoSink
from PySide6.QtWidgets import QWidget

from videdit.ffmpeg_tools import (
    CropRect,
    GifCaption,
    compute_meme_caption_metrics,
    find_impact_font_path,
    normalize_crop_rect,
    wrap_meme_caption,
)
from videdit.meme_project import (
    MediaOverlayLayer,
    NormalizedRect,
    OverlayLayer,
    TextOverlayLayer,
    build_shape_clip_path,
    compute_top_bar_layout,
    paint_classic_caption,
    paint_text_overlay,
    paint_top_white_bar,
)
from videdit.ui.theme import DARK_THEME, ThemeTokens, resolve_font_family

DragHandle = Literal["new", "move", "tl", "t", "tr", "r", "br", "b", "bl", "l"]


class _OverlayVideoCache:
    """Lightweight QMediaPlayer + QVideoSink pair for previewing a video overlay."""

    def __init__(self, path: Path, on_frame) -> None:
        self.path = path
        self.sink = QVideoSink()
        self.audio = QAudioOutput()
        self.audio.setVolume(0.7)
        self.player = QMediaPlayer()
        self.player.setAudioOutput(self.audio)
        self.player.setVideoOutput(self.sink)
        self.player.setLoops(QMediaPlayer.Loops.Infinite)
        self.current_frame = QVideoFrame()
        self._on_frame = on_frame
        self.sink.videoFrameChanged.connect(self._handle_frame)
        self.player.setSource(QUrl.fromLocalFile(str(path)))
        self.player.pause()

    def _handle_frame(self, frame: QVideoFrame) -> None:
        if frame.isValid():
            self.current_frame = frame
            self._on_frame()

    def dispose(self) -> None:
        self.player.stop()
        self.player.setSource(QUrl())
        self.player.setVideoOutput(None)
        self.player.setAudioOutput(None)


def _apply_chroma_key_to_image(image: QImage, color_hex: str, similarity: float) -> QImage:
    """Return a copy of image with pixels close to color_hex made transparent."""
    if image.isNull():
        return image
    target = QColor(color_hex)
    if not target.isValid():
        return image
    # Scale down very large images for responsive live preview chroma-keying.
    preview_img = image
    if max(image.width(), image.height()) > 640:
        preview_img = image.scaled(
            640,
            640,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
    rgba = preview_img.convertToFormat(QImage.Format.Format_RGBA8888)
    tr, tg, tb = target.red(), target.green(), target.blue()
    thresh_sq = int(((max(0.01, min(0.6, similarity)) * 442.0) ** 2))
    soft_sq = int(thresh_sq * 1.45)

    bits = rgba.bits()
    buf = memoryview(bits).cast("B")
    total_bytes = rgba.sizeInBytes()
    for i in range(0, total_bytes, 4):
        dr = buf[i] - tr
        dg = buf[i + 1] - tg
        db = buf[i + 2] - tb
        dist_sq = dr * dr + dg * dg + db * db
        if dist_sq <= thresh_sq:
            buf[i + 3] = 0
        elif dist_sq < soft_sq and soft_sq > thresh_sq:
            alpha_factor = (dist_sq - thresh_sq) / float(soft_sq - thresh_sq)
            buf[i + 3] = int(buf[i + 3] * alpha_factor)
    return rgba


class VideoPreviewWidget(QWidget):
    """QVideoSink-backed preview surface supporting crop and Meme Studio overlays."""

    clicked = Signal()
    crop_changed = Signal(object)  # CropRect | None
    crop_mode_exit_requested = Signal()
    layer_selected = Signal(str)  # layer_id or ""
    overlay_changed = Signal(object)  # updated OverlayLayer
    overlay_crop_exit_requested = Signal()

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
        self._base_image: Optional[QImage] = None
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

        # Meme Studio state
        self._meme_mode: bool = False
        self._meme_layers: list[OverlayLayer] = []
        self._selected_layer_id: Optional[str] = None
        self._overlay_crop_mode: bool = False
        self._top_bar_text: str = ""
        self._top_bar_font_scale: float = 1.0
        self._playhead_sec: float = 0.0
        self._is_animated_playback: bool = False
        self._preview_muted: bool = False

        self._image_caches: dict[str, tuple[Path, QImage]] = {}
        self._chroma_caches: dict[tuple[str, str, float], QImage] = {}
        self._gif_caches: dict[str, tuple[Path, QMovie]] = {}
        self._video_caches: dict[str, _OverlayVideoCache] = {}

        self._drag_layer_id: Optional[str] = None
        self._drag_layer_handle: Optional[DragHandle] = None
        self._drag_layer_start_norm: Optional[NormalizedRect] = None
        self._drag_layer_start_crop: Optional[CropRect] = None
        self._snap_center_x: bool = False
        self._snap_center_y: bool = False

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
        self._base_image = None
        self._crop_rect = None
        self._live_widget_rect = None
        self._drag_mode = None
        self.update()

    def set_base_image(self, image: Optional[QImage]) -> None:
        """Set or clear a static QImage as the base canvas media."""
        self._base_image = image
        if image is not None and not image.isNull():
            self._source_size = image.size()
            self._current_frame = QVideoFrame()
        self.update()

    def set_meme_mode(self, enabled: bool) -> None:
        """Enable or disable Meme Studio interactive overlay editing on the canvas."""
        enabled = bool(enabled)
        if enabled == self._meme_mode:
            return
        self._meme_mode = enabled
        if not enabled:
            self._overlay_crop_mode = False
            self._drag_layer_id = None
            self._drag_layer_handle = None
        self._update_cursor(self.mapFromGlobal(self.cursor().pos()))
        self.update()

    def is_meme_mode(self) -> bool:
        return self._meme_mode

    def set_top_bar(self, text: str, font_scale: float = 1.0) -> None:
        """Update the optional white top meme header preview."""
        if text != self._top_bar_text or abs(font_scale - self._top_bar_font_scale) > 1e-4:
            self._top_bar_text = text
            self._top_bar_font_scale = font_scale
            self.update()

    def set_playhead_sec(self, sec: float) -> None:
        """Update current playhead seconds for time-windowed overlay visibility."""
        self._playhead_sec = max(0.0, float(sec))
        if self._meme_mode and any(
            getattr(layer, "start_sec", None) is not None or getattr(layer, "end_sec", None) is not None
            for layer in self._meme_layers
        ):
            self.update()

    def set_overlay_preview_muted(self, muted: bool) -> None:
        self._preview_muted = bool(muted)
        for cache in self._video_caches.values():
            cache.audio.setMuted(self._preview_muted)

    def set_meme_layers(self, layers: tuple[OverlayLayer, ...] | list[OverlayLayer]) -> None:
        """Update Meme Studio layers and sync/prune cached decoders."""
        self._meme_layers = list(layers)
        active_ids = {layer.id for layer in self._meme_layers}

        for lid in list(self._image_caches):
            if lid not in active_ids:
                self._image_caches.pop(lid, None)
        for key in list(self._chroma_caches):
            if key[0] not in active_ids:
                self._chroma_caches.pop(key, None)
        for lid in list(self._gif_caches):
            if lid not in active_ids:
                _, movie = self._gif_caches.pop(lid)
                movie.stop()
        for lid in list(self._video_caches):
            if lid not in active_ids:
                cache = self._video_caches.pop(lid)
                cache.dispose()

        for layer in self._meme_layers:
            if not isinstance(layer, MediaOverlayLayer):
                continue
            if layer.kind == "image":
                cached = self._image_caches.get(layer.id)
                if cached is None or cached[0] != layer.path:
                    reader = QImageReader(str(layer.path))
                    reader.setAutoTransform(True)
                    img = reader.read()
                    if img.isNull():
                        img = QImage(str(layer.path))
                    self._image_caches[layer.id] = (layer.path, img)
            elif layer.kind == "gif":
                cached_gif = self._gif_caches.get(layer.id)
                if cached_gif is None or cached_gif[0] != layer.path:
                    if cached_gif is not None:
                        cached_gif[1].stop()
                    movie = QMovie(str(layer.path))
                    movie.frameChanged.connect(self.update)
                    movie.start()
                    self._gif_caches[layer.id] = (layer.path, movie)
            elif layer.kind == "video":
                cached_vid = self._video_caches.get(layer.id)
                if cached_vid is None or cached_vid.path != layer.path:
                    if cached_vid is not None:
                        cached_vid.dispose()
                    vcache = _OverlayVideoCache(layer.path, self.update)
                    vcache.audio.setMuted(self._preview_muted or layer.mute_audio)
                    if self._is_animated_playback:
                        vcache.player.play()
                    self._video_caches[layer.id] = vcache
                else:
                    cached_vid.audio.setMuted(self._preview_muted or layer.mute_audio)

        if self._selected_layer_id and self._selected_layer_id not in active_ids:
            self._selected_layer_id = None
            self._overlay_crop_mode = False
        self.update()

    def clear_meme_caches(self) -> None:
        """Dispose all cached overlay images, GIF movies, and video players."""
        self._meme_layers.clear()
        self._selected_layer_id = None
        self._overlay_crop_mode = False
        self._image_caches.clear()
        self._chroma_caches.clear()
        for _, movie in self._gif_caches.values():
            movie.stop()
        self._gif_caches.clear()
        for vcache in self._video_caches.values():
            vcache.dispose()
        self._video_caches.clear()
        self.update()

    def sync_overlay_playback(self, playing: bool, position_ms: Optional[int] = None) -> None:
        """Keep video/GIF overlays synced with base transport playback."""
        self._is_animated_playback = playing
        for vcache in self._video_caches.values():
            if position_ms is not None and not playing:
                dur = vcache.player.duration()
                if dur > 0:
                    vcache.player.setPosition(position_ms % dur)
            if playing:
                vcache.player.play()
            else:
                vcache.player.pause()
        for _, movie in self._gif_caches.values():
            movie.setPaused(not playing and self._base_image is None)

    def set_selected_layer(self, layer_id: Optional[str]) -> None:
        next_id = layer_id or None
        if next_id != self._selected_layer_id:
            self._selected_layer_id = next_id
            if next_id is None:
                self._overlay_crop_mode = False
            self.update()

    def selected_layer_id(self) -> Optional[str]:
        return self._selected_layer_id

    def set_overlay_crop_mode(self, enabled: bool) -> None:
        enabled = bool(enabled)
        if enabled and not self._selected_media_layer():
            enabled = False
        if enabled != self._overlay_crop_mode:
            self._overlay_crop_mode = enabled
            self._drag_layer_handle = None
            self._update_cursor(self.mapFromGlobal(self.cursor().pos()))
            self.update()

    def is_overlay_crop_mode(self) -> bool:
        return self._overlay_crop_mode

    def _selected_media_layer(self) -> Optional[MediaOverlayLayer]:
        if not self._selected_layer_id:
            return None
        for layer in self._meme_layers:
            if layer.id == self._selected_layer_id and isinstance(layer, MediaOverlayLayer):
                return layer
        return None

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
        if self._base_image is not None and not self._base_image.isNull():
            sz = self._base_image.size()
            return sz.width(), sz.height()
        if self._current_frame.isValid() and not self._current_frame.size().isEmpty():
            sz = self._current_frame.size()
            return sz.width(), sz.height()
        return 0, 0

    def _effective_top_bar_source_height(self) -> int:
        if not self._meme_mode or not self._top_bar_text.strip():
            return 0
        sw, _ = self._effective_source_size()
        if sw <= 0:
            return 0
        bar_h, _, _ = compute_top_bar_layout(sw, self._top_bar_text, font_scale=self._top_bar_font_scale)
        return bar_h

    def video_display_rect(self) -> QRectF:
        """Return the aspect-ratio-fitted rectangle where the base media renders."""
        w = float(max(1, self.width()))
        h = float(max(1, self.height()))
        sw, sh = self._effective_source_size()
        if sw <= 0 or sh <= 0:
            return QRectF(0.0, 0.0, w, h)
        bar_h = self._effective_top_bar_source_height()
        total_h = sh + bar_h
        scale = min(w / float(sw), h / float(total_h))
        dw = sw * scale
        dh = sh * scale
        bar_dw_h = bar_h * scale
        top_y = (h - (dh + bar_dw_h)) / 2.0 + bar_dw_h
        return QRectF((w - dw) / 2.0, top_y, dw, dh)

    def _top_bar_display_rect(self) -> Optional[QRectF]:
        bar_h = self._effective_top_bar_source_height()
        if bar_h <= 0:
            return None
        sw, sh = self._effective_source_size()
        if sw <= 0 or sh <= 0:
            return None
        media = self.video_display_rect()
        bar_dw_h = media.height() * (float(bar_h) / float(sh))
        return QRectF(media.left(), media.top() - bar_dw_h, media.width(), bar_dw_h)

    def _active_media_stage_rect(self) -> QRectF:
        """Return the media stage rect (cropped if base crop is active)."""
        target = self.video_display_rect()
        active_wrect = self._live_widget_rect
        if active_wrect is None and self._crop_rect is not None:
            active_wrect = self._crop_to_widget_rect(self._crop_rect)
        if active_wrect is not None:
            clipped = active_wrect.intersected(target)
            if clipped.width() >= 8.0 and clipped.height() >= 8.0:
                return clipped
        return target

    def _layer_widget_rect(self, layer: OverlayLayer, stage: QRectF) -> QRectF:
        return QRectF(
            stage.left() + layer.rect.x * stage.width(),
            stage.top() + layer.rect.y * stage.height(),
            layer.rect.width * stage.width(),
            layer.rect.height * stage.height(),
        )

    def _widget_rect_to_norm(self, wrect: QRectF, stage: QRectF) -> NormalizedRect:
        if stage.width() <= 0 or stage.height() <= 0:
            return NormalizedRect(0.25, 0.25, 0.4, 0.4)
        return NormalizedRect(
            x=(wrect.left() - stage.left()) / stage.width(),
            y=(wrect.top() - stage.top()) / stage.height(),
            width=wrect.width() / stage.width(),
            height=wrect.height() / stage.height(),
        ).clamped()

    def _hit_test_meme_layers(self, pos: QPointF, stage: QRectF) -> tuple[Optional[str], Optional[DragHandle]]:
        if self._overlay_crop_mode and self._selected_layer_id:
            sel = next((l for l in self._meme_layers if l.id == self._selected_layer_id), None)
            if sel is not None and isinstance(sel, MediaOverlayLayer) and sel.visible:
                wrect = self._layer_widget_rect(sel, stage)
                for handle, pt in self._handle_points(wrect).items():
                    if abs(pos.x() - pt.x()) <= self._HANDLE_HIT_RADIUS and abs(pos.y() - pt.y()) <= self._HANDLE_HIT_RADIUS:
                        return sel.id, handle
                if wrect.contains(pos):
                    return sel.id, "move"
                return None, None

        # Check selected layer handles first
        if self._selected_layer_id:
            for layer in reversed(self._meme_layers):
                if layer.id == self._selected_layer_id and layer.visible:
                    wrect = self._layer_widget_rect(layer, stage)
                    for handle, pt in self._handle_points(wrect).items():
                        if abs(pos.x() - pt.x()) <= self._HANDLE_HIT_RADIUS and abs(pos.y() - pt.y()) <= self._HANDLE_HIT_RADIUS:
                            return layer.id, handle
                    break

        # Check bodies top-to-bottom in z-order
        for layer in reversed(self._meme_layers):
            if not layer.visible:
                continue
            wrect = self._layer_widget_rect(layer, stage)
            if wrect.contains(pos):
                return layer.id, "move"
        return None, None

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
        if self._crop_mode:
            self.setCursor(self._cursor_for_handle(self._hit_test(pos)))
            return
        if self._meme_mode and self._meme_layers:
            stage = self._active_media_stage_rect()
            _, handle = self._hit_test_meme_layers(pos, stage)
            if handle is not None:
                self.setCursor(self._cursor_for_handle(handle))
                return
        self.setCursor(Qt.CursorShape.PointingHandCursor)

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

        if self._crop_mode:
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
            return

        if self._meme_mode and self._meme_layers:
            stage = self._active_media_stage_rect()
            pos = event.position()
            hit_id, handle = self._hit_test_meme_layers(pos, stage)
            if hit_id is not None and handle is not None:
                layer = next((l for l in self._meme_layers if l.id == hit_id), None)
                if layer is not None:
                    self._selected_layer_id = hit_id
                    self._drag_layer_id = hit_id
                    self._drag_layer_handle = handle
                    self._drag_origin = pos
                    self._drag_start_rect = self._layer_widget_rect(layer, stage)
                    self._drag_layer_start_norm = layer.rect
                    if isinstance(layer, MediaOverlayLayer):
                        self._drag_layer_start_crop = layer.source_crop or CropRect(
                            0, 0, layer.source_width, layer.source_height
                        )
                    else:
                        self._drag_layer_start_crop = None
                    self.layer_selected.emit(hit_id)
                    self.update()
                    event.accept()
                    return

            if self._selected_layer_id is not None:
                self._selected_layer_id = None
                self._overlay_crop_mode = False
                self.layer_selected.emit("")
                self.update()
                event.accept()
                return

        self.clicked.emit()
        event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        pos = event.position()
        if self._crop_mode and self._drag_mode is not None:
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
            return

        if self._meme_mode and self._drag_layer_id is not None and self._drag_layer_handle is not None:
            self._handle_meme_layer_drag(pos, event.modifiers())
            event.accept()
            return

        self._update_cursor(pos)
        super().mouseMoveEvent(event)

    def _handle_meme_layer_drag(self, pos: QPointF, modifiers: Qt.KeyboardModifier) -> None:
        idx = next((i for i, l in enumerate(self._meme_layers) if l.id == self._drag_layer_id), -1)
        if idx < 0:
            return
        layer = self._meme_layers[idx]
        stage = self._active_media_stage_rect()
        r = QRectF(self._drag_start_rect)
        handle = self._drag_layer_handle
        min_w = max(18.0, stage.width() * 0.04)
        min_h = max(18.0, stage.height() * 0.04)

        self._snap_center_x = False
        self._snap_center_y = False

        if self._overlay_crop_mode and isinstance(layer, MediaOverlayLayer) and handle != "move":
            # Per-overlay crop: drag handles inward/outward within the overlay's source bounds.
            start_crop = self._drag_layer_start_crop or CropRect(0, 0, layer.source_width, layer.source_height)
            start_wrect = self._drag_start_rect
            if start_wrect.width() <= 1 or start_wrect.height() <= 1:
                return
            dx_px = pos.x() - self._drag_origin.x()
            dy_px = pos.y() - self._drag_origin.y()
            px_to_src_x = float(start_crop.width) / start_wrect.width()
            px_to_src_y = float(start_crop.height) / start_wrect.height()

            left_src = float(start_crop.x)
            right_src = float(start_crop.x + start_crop.width)
            top_src = float(start_crop.y)
            bot_src = float(start_crop.y + start_crop.height)

            new_r = QRectF(start_wrect)
            if "l" in handle:
                cand_l = max(0.0, min(right_src - 16.0, left_src + dx_px * px_to_src_x))
                new_r.setLeft(start_wrect.left() + (cand_l - left_src) / px_to_src_x)
                left_src = cand_l
            if "r" in handle:
                cand_r = min(float(layer.source_width), max(left_src + 16.0, right_src + dx_px * px_to_src_x))
                new_r.setRight(start_wrect.right() + (cand_r - right_src) / px_to_src_x)
                right_src = cand_r
            if "t" in handle:
                cand_t = max(0.0, min(bot_src - 16.0, top_src + dy_px * px_to_src_y))
                new_r.setTop(start_wrect.top() + (cand_t - top_src) / px_to_src_y)
                top_src = cand_t
            if "b" in handle:
                cand_b = min(float(layer.source_height), max(top_src + 16.0, bot_src + dy_px * px_to_src_y))
                new_r.setBottom(start_wrect.bottom() + (cand_b - bot_src) / px_to_src_y)
                bot_src = cand_b

            new_crop = normalize_crop_rect(
                CropRect(
                    x=int(round(left_src)),
                    y=int(round(top_src)),
                    width=int(round(right_src - left_src)),
                    height=int(round(bot_src - top_src)),
                ),
                layer.source_width,
                layer.source_height,
                min_size=8,
            )
            new_norm = self._widget_rect_to_norm(new_r.intersected(stage), stage)
            updated = replace(layer, source_crop=new_crop, rect=new_norm)
            self._meme_layers[idx] = updated
            self.overlay_changed.emit(updated)
            self.update()
            return

        if handle == "move":
            dx = pos.x() - self._drag_origin.x()
            dy = pos.y() - self._drag_origin.y()
            dx = max(stage.left() - r.left(), min(stage.right() - r.right(), dx))
            dy = max(stage.top() - r.top(), min(stage.bottom() - r.bottom(), dy))
            r.translate(dx, dy)

            # Snap to center lines when close (within 6px)
            if abs(r.center().x() - stage.center().x()) <= 6.0:
                r.moveLeft(stage.center().x() - r.width() / 2.0)
                self._snap_center_x = True
            if abs(r.center().y() - stage.center().y()) <= 6.0:
                r.moveTop(stage.center().y() - r.height() / 2.0)
                self._snap_center_y = True
        else:
            cx = max(stage.left(), min(stage.right(), pos.x()))
            cy = max(stage.top(), min(stage.bottom(), pos.y()))
            keep_aspect = (
                isinstance(layer, MediaOverlayLayer)
                and handle in ("tl", "tr", "bl", "br")
                and not (modifiers & Qt.KeyboardModifier.ShiftModifier)
                and self._drag_start_rect.height() > 0
            )
            aspect = self._drag_start_rect.width() / max(1.0, self._drag_start_rect.height())

            if "l" in handle:
                r.setLeft(min(cx, r.right() - min_w))
            if "r" in handle:
                r.setRight(max(cx, r.left() + min_w))
            if "t" in handle:
                r.setTop(min(cy, r.bottom() - min_h))
            if "b" in handle:
                r.setBottom(max(cy, r.top() + min_h))

            if keep_aspect:
                cur_w = r.width()
                cur_h = cur_w / aspect
                if "t" in handle:
                    r.setTop(max(stage.top(), r.bottom() - cur_h))
                else:
                    r.setBottom(min(stage.bottom(), r.top() + cur_h))
                # Re-adjust width if vertical clamp hit stage boundary
                actual_h = r.height()
                actual_w = actual_h * aspect
                if "l" in handle:
                    r.setLeft(max(stage.left(), r.right() - actual_w))
                else:
                    r.setRight(min(stage.right(), r.left() + actual_w))

        updated = replace(layer, rect=self._widget_rect_to_norm(r, stage))
        self._meme_layers[idx] = updated
        self.overlay_changed.emit(updated)
        self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._meme_mode and self._drag_layer_id is not None:
            self._drag_layer_id = None
            self._drag_layer_handle = None
            self._drag_layer_start_norm = None
            self._drag_layer_start_crop = None
            self._snap_center_x = False
            self._snap_center_y = False
            self._update_cursor(event.position())
            self.update()
            event.accept()
            return

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
        elif self._overlay_crop_mode and event.button() == Qt.MouseButton.LeftButton:
            self.overlay_crop_exit_requested.emit()
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
        top_bar_rect = self._top_bar_display_rect()
        if top_bar_rect is not None:
            paint_top_white_bar(painter, top_bar_rect, self._top_bar_text, font_scale=self._top_bar_font_scale)

        if self._base_image is not None and not self._base_image.isNull():
            painter.drawImage(target, self._base_image)
        elif self._current_frame.isValid():
            self._current_frame.paint(painter, target, QVideoFrame.PaintOptions())

        active_wrect = self._live_widget_rect
        if active_wrect is None and self._crop_rect is not None:
            active_wrect = self._crop_to_widget_rect(self._crop_rect)

        clipped_crop = active_wrect.intersected(target) if active_wrect is not None else None
        media_stage = clipped_crop if clipped_crop is not None else target

        if self._meme_mode and self._meme_layers:
            self._paint_meme_overlays(painter, media_stage)

        if self._caption_preview is not None:
            self._paint_caption_overlay(painter, media_stage, self._caption_preview)

        if self._crop_mode and active_wrect is None:
            self._paint_crop_prompt(painter, target)
            return

        if clipped_crop is not None:
            self._paint_crop_overlay(painter, target, clipped_crop)

    def _get_overlay_visual(self, layer: MediaOverlayLayer) -> tuple[Optional[QImage], Optional[QVideoFrame]]:
        if layer.kind == "image":
            cached = self._image_caches.get(layer.id)
            img = cached[1] if cached is not None else None
            if img is not None and not img.isNull() and layer.chroma_key_color:
                key = (layer.id, layer.chroma_key_color.upper(), round(layer.chroma_similarity, 2))
                ck = self._chroma_caches.get(key)
                if ck is None:
                    ck = _apply_chroma_key_to_image(img, layer.chroma_key_color, layer.chroma_similarity)
                    self._chroma_caches[key] = ck
                return ck, None
            return img, None
        if layer.kind == "gif":
            cached_gif = self._gif_caches.get(layer.id)
            if cached_gif is not None:
                img = cached_gif[1].currentImage()
                if not img.isNull() and layer.chroma_key_color:
                    return _apply_chroma_key_to_image(img, layer.chroma_key_color, layer.chroma_similarity), None
                return img, None
            return None, None
        vcache = self._video_caches.get(layer.id)
        if vcache is not None and vcache.current_frame.isValid():
            if layer.chroma_key_color:
                img = vcache.current_frame.toImage()
                return _apply_chroma_key_to_image(img, layer.chroma_key_color, layer.chroma_similarity), None
            return None, vcache.current_frame
        return None, None

    def _paint_meme_overlays(self, painter: QPainter, stage: QRectF) -> None:
        if stage.width() < 12 or stage.height() < 12:
            return

        painter.save()
        painter.setClipRect(stage)

        for layer in self._meme_layers:
            if not layer.is_visible_at(self._playhead_sec):
                continue
            wrect = self._layer_widget_rect(layer, stage)

            if isinstance(layer, MediaOverlayLayer):
                img, vframe = self._get_overlay_visual(layer)
                painter.save()
                painter.setOpacity(max(0.05, min(1.0, layer.opacity)))
                clip_path = build_shape_clip_path(wrect, layer.shape)
                painter.setClipPath(clip_path, Qt.ClipOperation.IntersectClip)

                if layer.flip_h:
                    cx = wrect.center().x()
                    painter.translate(cx, 0.0)
                    painter.scale(-1.0, 1.0)
                    painter.translate(-cx, 0.0)

                if img is not None and not img.isNull():
                    src_rect = QRectF(0.0, 0.0, float(img.width()), float(img.height()))
                    if layer.source_crop is not None:
                        sx = float(img.width()) / max(1.0, float(layer.source_width))
                        sy = float(img.height()) / max(1.0, float(layer.source_height))
                        src_rect = QRectF(
                            layer.source_crop.x * sx,
                            layer.source_crop.y * sy,
                            layer.source_crop.width * sx,
                            layer.source_crop.height * sy,
                        )
                    painter.drawImage(wrect, img, src_rect)
                elif vframe is not None and vframe.isValid():
                    if layer.source_crop is not None:
                        fimg = vframe.toImage()
                        src_rect = QRectF(
                            float(layer.source_crop.x),
                            float(layer.source_crop.y),
                            float(layer.source_crop.width),
                            float(layer.source_crop.height),
                        )
                        painter.drawImage(wrect, fimg, src_rect)
                    else:
                        vframe.paint(painter, wrect, QVideoFrame.PaintOptions())
                else:
                    painter.fillRect(wrect, QColor(42, 35, 51, 160))

                painter.restore()
            elif isinstance(layer, TextOverlayLayer):
                paint_text_overlay(painter, wrect, layer, stage.height())

        # Snap guides while dragging
        if self._snap_center_x:
            painter.setPen(QPen(QColor(self._theme.accent_cobalt), 1.0, Qt.PenStyle.DashLine))
            painter.drawLine(QPointF(stage.center().x(), stage.top()), QPointF(stage.center().x(), stage.bottom()))
        if self._snap_center_y:
            painter.setPen(QPen(QColor(self._theme.accent_cobalt), 1.0, Qt.PenStyle.DashLine))
            painter.drawLine(QPointF(stage.left(), stage.center().y()), QPointF(stage.right(), stage.center().y()))

        painter.restore()

        # Paint selection frame & handles on top (outside stage clip so handles stay crisp)
        if self._selected_layer_id and not self._crop_mode:
            sel = next((l for l in self._meme_layers if l.id == self._selected_layer_id), None)
            if sel is not None and sel.visible:
                sel_rect = self._layer_widget_rect(sel, stage)
                painter.save()
                border_col = QColor(self._theme.accent_crimson if self._overlay_crop_mode else self._theme.accent_cobalt)
                painter.setPen(QPen(QColor(10, 9, 14, 180), 2.5))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawRect(sel_rect)
                painter.setPen(QPen(border_col, 1.5, Qt.PenStyle.DashLine if self._overlay_crop_mode else Qt.PenStyle.SolidLine))
                painter.drawRect(sel_rect)

                hh = self._HANDLE_DRAW_HALF
                for handle, pt in self._handle_points(sel_rect).items():
                    box = QRectF(pt.x() - hh, pt.y() - hh, hh * 2.0, hh * 2.0)
                    painter.setPen(QPen(QColor("#110E16"), 1.25))
                    if self._overlay_crop_mode:
                        painter.setBrush(QColor(self._theme.accent_crimson))
                    elif handle in ("tl", "tr", "bl", "br"):
                        painter.setBrush(QColor(self._theme.accent_cobalt))
                    else:
                        painter.setBrush(QColor("#F5F3EC"))
                    painter.drawRect(box)

                if self._overlay_crop_mode:
                    badge_txt = "Overlay Crop · Drag handles · Esc to exit"
                    font = QFont(self.font())
                    font.setPointSize(9)
                    font.setWeight(QFont.Weight.SemiBold)
                    painter.setFont(font)
                    fm = painter.fontMetrics()
                    bw = float(fm.horizontalAdvance(badge_txt) + 14)
                    bh = 20.0
                    by = max(stage.top() + 4.0, sel_rect.top() - bh - 6.0)
                    bx = max(stage.left() + 4.0, min(stage.right() - bw - 4.0, sel_rect.left()))
                    badge = QRectF(bx, by, bw, bh)
                    painter.setPen(QPen(QColor(self._theme.accent_crimson), 1.0))
                    painter.setBrush(QColor(17, 14, 22, 225))
                    painter.drawRoundedRect(badge, 3.0, 3.0)
                    painter.setPen(QColor("#F4F1F8"))
                    painter.drawText(badge, Qt.AlignmentFlag.AlignCenter, badge_txt)

                painter.restore()

    def _paint_caption_overlay(
        self,
        painter: QPainter,
        region: QRectF,
        caption: GifCaption,
    ) -> None:
        paint_classic_caption(painter, region, caption)

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
