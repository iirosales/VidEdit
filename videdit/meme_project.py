"""Data model, media probing, and WYSIWYG asset renderers for Meme Studio."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal, Optional, Union

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontDatabase,
    QFontMetricsF,
    QGuiApplication,
    QImage,
    QImageReader,
    QPainter,
    QPainterPath,
    QPen,
)

from videdit.ffmpeg_tools import (
    CropRect,
    GifCaption,
    compute_meme_caption_metrics,
    find_impact_font_path,
    normalize_crop_rect,
    probe_video,
    wrap_meme_caption,
)

MediaKind = Literal["image", "video", "gif"]
OverlayShape = Literal["rect", "circle", "rounded"]
CaptionStyle = Literal["impact", "clean", "pill"]
TextAlign = Literal["left", "center", "right"]

IMAGE_EXTENSIONS: frozenset[str] = frozenset({".png", ".jpg", ".jpeg", ".webp", ".bmp"})
VIDEO_EXTENSIONS: frozenset[str] = frozenset({".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"})
GIF_EXTENSIONS: frozenset[str] = frozenset({".gif"})
ALL_MEDIA_EXTENSIONS: frozenset[str] = IMAGE_EXTENSIONS | VIDEO_EXTENSIONS | GIF_EXTENSIONS


def new_layer_id() -> str:
    """Return a short unique identifier for an overlay or text layer."""
    return uuid.uuid4().hex[:10]


def classify_media_path(path: Path) -> MediaKind:
    """Classify a media file path into image, gif, or video."""
    ext = path.suffix.lower()
    if ext in IMAGE_EXTENSIONS:
        return "image"
    if ext in GIF_EXTENSIONS:
        return "gif"
    return "video"


def even_dim(value: int, minimum: int = 2) -> int:
    """Round an integer down to an even number >= minimum for yuv420p compatibility."""
    return max(minimum, (int(value) // 2) * 2)


@dataclass(frozen=True)
class NormalizedRect:
    """Rectangle in normalized 0..1 coordinates relative to the base media stage."""

    x: float
    y: float
    width: float
    height: float

    def clamped(self, min_extent: float = 0.04) -> NormalizedRect:
        """Clamp position and size inside [0, 1] bounds."""
        w = max(min_extent, min(1.0, float(self.width)))
        h = max(min_extent, min(1.0, float(self.height)))
        x = max(0.0, min(1.0 - w, float(self.x)))
        y = max(0.0, min(1.0 - h, float(self.y)))
        return NormalizedRect(x=x, y=y, width=w, height=h)

    def to_pixel_rect(self, stage_width: int, stage_height: int) -> tuple[int, int, int, int]:
        """Convert normalized rect into integer (x, y, width, height) in stage pixels."""
        sw = max(2, int(stage_width))
        sh = max(2, int(stage_height))
        pw = max(4, min(sw, int(round(self.width * sw))))
        ph = max(4, min(sh, int(round(self.height * sh))))
        px = max(0, min(sw - pw, int(round(self.x * sw))))
        py = max(0, min(sh - ph, int(round(self.y * sh))))
        return px, py, pw, ph


@dataclass(frozen=True)
class MediaAssetInfo:
    """Probed metadata for any image, GIF, or video file."""

    path: Path
    kind: MediaKind
    width: int
    height: int
    duration_sec: float
    fps: float
    has_audio: bool
    file_size_bytes: int


def probe_media_asset(path: Path, ffprobe_path: Optional[str] = None) -> MediaAssetInfo:
    """Probe dimensions, duration, and audio presence for an image, GIF, or video."""
    kind = classify_media_path(path)
    size_bytes = path.stat().st_size if path.is_file() else 0

    if kind == "image":
        reader = QImageReader(str(path))
        reader.setAutoTransform(True)
        sz = reader.size()
        if sz.isValid() and sz.width() > 0 and sz.height() > 0:
            return MediaAssetInfo(
                path=path,
                kind="image",
                width=sz.width(),
                height=sz.height(),
                duration_sec=0.0,
                fps=30.0,
                has_audio=False,
                file_size_bytes=size_bytes,
            )
        img = QImage(str(path))
        if not img.isNull() and img.width() > 0 and img.height() > 0:
            return MediaAssetInfo(
                path=path,
                kind="image",
                width=img.width(),
                height=img.height(),
                duration_sec=0.0,
                fps=30.0,
                has_audio=False,
                file_size_bytes=size_bytes,
            )
        # Fallback to ffprobe if Qt image reader doesn't recognize the codec.
        vinfo = probe_video(path, ffprobe_path=ffprobe_path)
        return MediaAssetInfo(
            path=path,
            kind="image",
            width=vinfo.width,
            height=vinfo.height,
            duration_sec=0.0,
            fps=30.0,
            has_audio=False,
            file_size_bytes=vinfo.file_size_bytes,
        )

    vinfo = probe_video(path, ffprobe_path=ffprobe_path)
    duration = vinfo.duration_sec
    if kind == "gif" and duration <= 0.0:
        duration = 2.0
    return MediaAssetInfo(
        path=path,
        kind=kind,
        width=vinfo.width,
        height=vinfo.height,
        duration_sec=duration,
        fps=vinfo.fps,
        has_audio=(vinfo.has_audio and kind == "video"),
        file_size_bytes=vinfo.file_size_bytes,
    )


@dataclass(frozen=True)
class BaseLayer:
    """Base background image, GIF, or video of a meme composition."""

    path: Path
    kind: MediaKind
    width: int
    height: int
    duration_sec: float
    has_audio: bool
    fps: float = 30.0
    file_size_bytes: int = 0
    crop: Optional[CropRect] = None
    start_sec: float = 0.0
    end_sec: float = 0.0
    top_bar_text: str = ""
    top_bar_font_scale: float = 1.0

    def cropped_size(self) -> tuple[int, int]:
        """Return even-aligned (width, height) of the base media after optional crop."""
        norm = normalize_crop_rect(self.crop, self.width, self.height)
        if norm is not None:
            return even_dim(norm.width), even_dim(norm.height)
        return even_dim(self.width), even_dim(self.height)


@dataclass(frozen=True)
class MediaOverlayLayer:
    """Draggable, resizable, croppable, maskable image/GIF/video overlay layer."""

    id: str
    name: str
    path: Path
    kind: MediaKind
    source_width: int
    source_height: int
    duration_sec: float
    has_audio: bool
    rect: NormalizedRect
    source_crop: Optional[CropRect] = None
    shape: OverlayShape = "rect"
    flip_h: bool = False
    opacity: float = 1.0
    visible: bool = True
    mute_audio: bool = False
    chroma_key_color: Optional[str] = None
    chroma_similarity: float = 0.25
    start_sec: Optional[float] = None
    end_sec: Optional[float] = None
    loop_video: bool = True

    def effective_source_size(self) -> tuple[int, int]:
        """Return source dimensions after applying source_crop."""
        norm = normalize_crop_rect(self.source_crop, self.source_width, self.source_height)
        if norm is not None:
            return norm.width, norm.height
        return max(2, self.source_width), max(2, self.source_height)

    def is_visible_at(self, time_sec: float) -> bool:
        """Return True if this layer is enabled and active at time_sec."""
        if not self.visible:
            return False
        if self.start_sec is not None and time_sec < self.start_sec:
            return False
        if self.end_sec is not None and time_sec > self.end_sec:
            return False
        return True


@dataclass(frozen=True)
class TextOverlayLayer:
    """Freeform draggable and resizable caption text layer."""

    id: str
    text: str
    rect: NormalizedRect
    style: CaptionStyle = "impact"
    font_scale: float = 1.0
    text_color: str = "#FFFFFF"
    outline_color: str = "#000000"
    uppercase: bool = True
    align: TextAlign = "center"
    visible: bool = True
    start_sec: Optional[float] = None
    end_sec: Optional[float] = None

    def is_visible_at(self, time_sec: float) -> bool:
        """Return True if this text layer is enabled and active at time_sec."""
        if not self.visible or not self.text.strip():
            return False
        if self.start_sec is not None and time_sec < self.start_sec:
            return False
        if self.end_sec is not None and time_sec > self.end_sec:
            return False
        return True


OverlayLayer = Union[MediaOverlayLayer, TextOverlayLayer]


@dataclass(frozen=True)
class MemeComposition:
    """Complete layered meme specification (base + overlays + optional captions)."""

    base: BaseLayer
    overlays: tuple[OverlayLayer, ...] = ()
    still_duration_sec: float = 5.0
    classic_caption: Optional[GifCaption] = None

    def has_animation(self) -> bool:
        """Return True when the base or any visible media overlay is video/GIF."""
        if self.base.kind != "image":
            return True
        return any(
            isinstance(layer, MediaOverlayLayer) and layer.visible and layer.kind != "image"
            for layer in self.overlays
        )

    def effective_duration_sec(self) -> float:
        """Return the output clip duration in seconds for animated exports."""
        if self.base.kind != "image":
            span = self.base.end_sec - self.base.start_sec
            return max(0.2, span if span > 0 else max(0.2, self.base.duration_sec))

        # Static base with animated overlay(s): match longest active overlay or still_duration_sec.
        max_overlay = 0.0
        for layer in self.overlays:
            if isinstance(layer, MediaOverlayLayer) and layer.visible and layer.kind != "image":
                if layer.end_sec is not None and layer.end_sec > 0:
                    max_overlay = max(max_overlay, layer.end_sec)
                elif layer.duration_sec > 0:
                    max_overlay = max(max_overlay, layer.duration_sec)
        if max_overlay > 0.0:
            return max(0.5, min(60.0, max_overlay))
        return max(0.5, float(self.still_duration_sec))

    def has_any_audio(self) -> bool:
        """Return True if base or any visible unmuted video overlay has an audio track."""
        if self.base.kind == "video" and self.base.has_audio:
            return True
        return any(
            isinstance(layer, MediaOverlayLayer)
            and layer.visible
            and layer.kind == "video"
            and layer.has_audio
            and not layer.mute_audio
            for layer in self.overlays
        )


def make_centered_overlay_rect(
    src_w: int,
    src_h: int,
    stage_w: int,
    stage_h: int,
    target_fraction: float = 0.36,
    index_offset: int = 0,
) -> NormalizedRect:
    """Create a well-proportioned centered NormalizedRect preserving overlay aspect ratio."""
    sw = max(1.0, float(stage_w))
    sh = max(1.0, float(stage_h))
    ow = max(1.0, float(src_w))
    oh = max(1.0, float(src_h))

    max_w_px = sw * target_fraction
    max_h_px = sh * target_fraction
    scale = min(max_w_px / ow, max_h_px / oh)
    norm_w = max(0.08, min(0.85, (ow * scale) / sw))
    norm_h = max(0.08, min(0.85, (oh * scale) / sh))

    shift = (index_offset % 5) * 0.035
    x = max(0.02, min(0.98 - norm_w, (1.0 - norm_w) / 2.0 + shift))
    y = max(0.02, min(0.98 - norm_h, (1.0 - norm_h) / 2.0 + shift))
    return NormalizedRect(x=x, y=y, width=norm_w, height=norm_h).clamped()


def build_media_overlay_layer(
    asset: MediaAssetInfo,
    stage_w: int,
    stage_h: int,
    index_offset: int = 0,
) -> MediaOverlayLayer:
    """Create a new MediaOverlayLayer sized proportionally to the base stage."""
    rect = make_centered_overlay_rect(
        asset.width,
        asset.height,
        stage_w,
        stage_h,
        index_offset=index_offset,
    )
    return MediaOverlayLayer(
        id=new_layer_id(),
        name=asset.path.name,
        path=asset.path,
        kind=asset.kind,
        source_width=asset.width,
        source_height=asset.height,
        duration_sec=asset.duration_sec,
        has_audio=asset.has_audio,
        rect=rect,
    )


def build_text_overlay_layer(
    text: str = "TEXT",
    style: CaptionStyle = "impact",
    index_offset: int = 0,
) -> TextOverlayLayer:
    """Create a default draggable TextOverlayLayer."""
    y = min(0.75, 0.12 + (index_offset % 4) * 0.16)
    return TextOverlayLayer(
        id=new_layer_id(),
        text=text,
        rect=NormalizedRect(x=0.12, y=y, width=0.76, height=0.18),
        style=style,
        font_scale=1.0,
        text_color="#FFFFFF",
        outline_color="#000000",
        uppercase=(style == "impact"),
        align="center",
    )


def apply_overlay_crop_rect(
    layer: MediaOverlayLayer,
    crop: Optional[CropRect],
) -> MediaOverlayLayer:
    """Return a copy of layer with normalized source_crop."""
    norm = normalize_crop_rect(crop, layer.source_width, layer.source_height, min_size=8)
    return replace(layer, source_crop=norm)


# ---------------------------------------------------------------------------
# WYSIWYG Qt Rendering Helpers (shared by live canvas and FFmpeg export)
# ---------------------------------------------------------------------------

_IMPACT_FAMILY_CACHE: Optional[str] = None


def resolve_meme_font_family(style: CaptionStyle) -> str:
    """Resolve the font family for a given meme caption style."""
    global _IMPACT_FAMILY_CACHE
    if style == "impact":
        if _IMPACT_FAMILY_CACHE is not None:
            return _IMPACT_FAMILY_CACHE
        if QGuiApplication.instance() is not None:
            impact_path = find_impact_font_path()
            if impact_path is not None:
                fid = QFontDatabase.addApplicationFont(str(impact_path))
                if fid >= 0:
                    fams = QFontDatabase.applicationFontFamilies(fid)
                    if fams:
                        _IMPACT_FAMILY_CACHE = fams[0]
                        return _IMPACT_FAMILY_CACHE
            installed = set(QFontDatabase.families())
            for cand in ("Impact", "Arial Black", "Arial"):
                if cand in installed:
                    _IMPACT_FAMILY_CACHE = cand
                    return cand
        return "Impact"

    if QGuiApplication.instance() is not None:
        installed = set(QFontDatabase.families())
        for cand in ("Segoe UI", "Inter", "Arial", "Helvetica"):
            if cand in installed:
                return cand
    return "Arial"


def _wrap_text_for_font(text: str, font: QFont, max_width: float, uppercase: bool) -> list[str]:
    """Wrap multi-paragraph text to fit within max_width using QFontMetricsF."""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    if uppercase:
        normalized = normalized.upper()

    fm = QFontMetricsF(font)
    budget = max(20.0, max_width)
    lines: list[str] = []

    for para in normalized.split("\n"):
        cleaned = " ".join(para.split())
        if not cleaned:
            continue
        words = cleaned.split(" ")
        current = ""
        for word in words:
            candidate = f"{current} {word}".strip() if current else word
            if fm.horizontalAdvance(candidate) <= budget:
                current = candidate
                continue
            if current:
                lines.append(current)
                current = ""
            if fm.horizontalAdvance(word) <= budget:
                current = word
            else:
                chunk = ""
                for ch in word:
                    if chunk and fm.horizontalAdvance(chunk + ch) > budget:
                        lines.append(chunk)
                        chunk = ch
                    else:
                        chunk += ch
                current = chunk
        if current:
            lines.append(current)
    return lines


def compute_top_bar_layout(
    stage_width: int,
    text: str,
    font_scale: float = 1.0,
) -> tuple[int, float, list[str]]:
    """Compute (bar_height_px, font_px, wrapped_lines) for the white top meme bar."""
    cleaned = text.strip()
    if not cleaned:
        return 0, 14.0, []

    w = max(64, int(stage_width))
    scale = max(0.5, min(2.0, float(font_scale)))
    font_px = max(14.0, min(58.0, w * 0.052 * scale))

    if QGuiApplication.instance() is not None:
        font = QFont(resolve_meme_font_family("clean"))
        font.setPixelSize(int(round(font_px)))
        font.setBold(True)
        lines = _wrap_text_for_font(cleaned, font, w * 0.90, uppercase=False)
    else:
        approx_chars = max(8, int((w * 0.90) / (font_px * 0.56)))
        lines = [cleaned[i : i + approx_chars] for i in range(0, len(cleaned), approx_chars)]

    if not lines:
        return 0, font_px, []

    pad_y = max(12.0, font_px * 0.55)
    line_step = font_px * 1.24
    raw_h = int(round(pad_y * 2.0 + len(lines) * line_step))
    return even_dim(max(36, raw_h)), font_px, lines


def paint_top_white_bar(
    painter: QPainter,
    target_rect: QRectF,
    text: str,
    font_scale: float = 1.0,
) -> None:
    """Paint a classic white meme caption bar into target_rect."""
    if not text.strip() or target_rect.width() < 20 or target_rect.height() < 16:
        return

    painter.save()
    painter.fillRect(target_rect, QColor("#FFFFFF"))

    scale = max(0.5, min(2.0, float(font_scale)))
    font_px = max(11.0, min(58.0, target_rect.width() * 0.052 * scale))
    font = QFont(resolve_meme_font_family("clean"))
    font.setPixelSize(max(10, int(round(font_px))))
    font.setBold(True)
    lines = _wrap_text_for_font(text, font, target_rect.width() * 0.90, uppercase=False)
    if not lines:
        painter.restore()
        return

    fm = QFontMetricsF(font)
    line_step = font_px * 1.24
    total_h = len(lines) * line_step
    start_y = target_rect.center().y() - total_h / 2.0 + fm.ascent() + (line_step - fm.height()) * 0.2

    painter.setFont(font)
    painter.setPen(QColor("#111111"))
    pad_x = target_rect.width() * 0.05
    for idx, line in enumerate(lines):
        baseline_y = start_y + idx * line_step
        painter.drawText(QRectF(target_rect.left() + pad_x, baseline_y - fm.ascent(), target_rect.width() - 2 * pad_x, line_step), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, line)
    painter.restore()


def render_top_white_bar_png(
    stage_width: int,
    text: str,
    output_path: Path,
    font_scale: float = 1.0,
) -> tuple[Path, int]:
    """Render the white top meme bar to a PNG file and return (path, bar_height_px)."""
    bar_h, _, _ = compute_top_bar_layout(stage_width, text, font_scale=font_scale)
    if bar_h <= 0:
        raise ValueError("Top white bar text is empty.")
    w = even_dim(max(16, int(stage_width)))
    img = QImage(w, bar_h, QImage.Format.Format_RGBA8888)
    img.fill(QColor("#FFFFFF"))
    painter = QPainter(img)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    paint_top_white_bar(painter, QRectF(0.0, 0.0, float(w), float(bar_h)), text, font_scale=font_scale)
    painter.end()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(str(output_path), "PNG")
    return output_path, bar_h


def build_shape_clip_path(rect: QRectF, shape: OverlayShape) -> QPainterPath:
    """Return a QPainterPath for rect, circle/oval, or rounded-corner cutouts."""
    path = QPainterPath()
    if shape == "circle":
        path.addEllipse(rect)
    elif shape == "rounded":
        radius = min(rect.width(), rect.height()) * 0.18
        path.addRoundedRect(rect, radius, radius)
    else:
        path.addRect(rect)
    return path


def render_shape_mask_png(
    width: int,
    height: int,
    shape: OverlayShape,
    output_path: Path,
) -> Path:
    """Render an alpha/grayscale mask PNG (white opaque inside, transparent outside)."""
    w = max(2, int(width))
    h = max(2, int(height))
    img = QImage(w, h, QImage.Format.Format_RGBA8888)
    img.fill(QColor(0, 0, 0, 0))
    painter = QPainter(img)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(255, 255, 255, 255))
    path = build_shape_clip_path(QRectF(0.0, 0.0, float(w), float(h)), shape)
    painter.drawPath(path)
    painter.end()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(str(output_path), "PNG")
    return output_path


def paint_text_overlay(
    painter: QPainter,
    box_rect: QRectF,
    layer: TextOverlayLayer,
    stage_height: float,
) -> None:
    """Paint a TextOverlayLayer inside box_rect with WYSIWYG styling."""
    if not layer.text.strip() or box_rect.width() < 12 or box_rect.height() < 12:
        return

    scale = max(0.4, min(2.5, float(layer.font_scale)))
    font_px = max(12.0, min(box_rect.height() * 0.72, stage_height * 0.095 * scale))
    font = QFont(resolve_meme_font_family(layer.style))
    font.setPixelSize(max(10, int(round(font_px))))
    if layer.style == "clean":
        font.setBold(True)

    lines = _wrap_text_for_font(layer.text, font, box_rect.width() * 0.92, uppercase=layer.uppercase)
    if not lines:
        return

    fm = QFontMetricsF(font)
    # Shrink slightly if wrapped lines exceed box height.
    line_step = font_px * 1.14
    while len(lines) * line_step > box_rect.height() * 0.94 and font_px > 12.0:
        font_px = max(12.0, font_px * 0.85)
        font.setPixelSize(int(round(font_px)))
        fm = QFontMetricsF(font)
        line_step = font_px * 1.14
        lines = _wrap_text_for_font(layer.text, font, box_rect.width() * 0.92, uppercase=layer.uppercase)

    painter.save()
    painter.setClipRect(box_rect)

    total_h = len(lines) * line_step
    top_y = box_rect.center().y() - total_h / 2.0

    if layer.style == "pill":
        pill_pad_x = max(8.0, font_px * 0.35)
        pill_pad_y = max(4.0, font_px * 0.20)
        max_lw = max(fm.horizontalAdvance(ln) for ln in lines)
        pill_w = min(box_rect.width(), max_lw + pill_pad_x * 2.0)
        pill_h = min(box_rect.height(), total_h + pill_pad_y * 2.0)
        if layer.align == "left":
            px = box_rect.left()
        elif layer.align == "right":
            px = box_rect.right() - pill_w
        else:
            px = box_rect.center().x() - pill_w / 2.0
        pill_rect = QRectF(px, box_rect.center().y() - pill_h / 2.0, pill_w, pill_h)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(15, 13, 20, 210))
        painter.drawRoundedRect(pill_rect, max(6.0, font_px * 0.22), max(6.0, font_px * 0.22))

    stroke_w = max(2.0, font_px * 0.14) if layer.style == "impact" else max(1.5, font_px * 0.08)
    stroke_pen = QPen(
        QColor(layer.outline_color),
        stroke_w,
        Qt.PenStyle.SolidLine,
        Qt.PenCapStyle.RoundCap,
        Qt.PenJoinStyle.RoundJoin,
    )
    fill_color = QColor(layer.text_color)

    for idx, line in enumerate(lines):
        lw = fm.horizontalAdvance(line)
        if layer.align == "left":
            x = box_rect.left() + box_rect.width() * 0.04
        elif layer.align == "right":
            x = box_rect.right() - box_rect.width() * 0.04 - lw
        else:
            x = box_rect.center().x() - lw / 2.0
        baseline_y = top_y + fm.ascent() + idx * line_step

        if layer.style == "pill":
            painter.setFont(font)
            painter.setPen(fill_color)
            painter.drawText(QRectF(box_rect.left(), baseline_y - fm.ascent(), box_rect.width(), line_step), Qt.AlignmentFlag.AlignCenter if layer.align == "center" else (Qt.AlignmentFlag.AlignLeft if layer.align == "left" else Qt.AlignmentFlag.AlignRight), line)
        else:
            path = QPainterPath()
            path.addText(x, baseline_y, font, line)
            painter.strokePath(path, stroke_pen)
            painter.fillPath(path, fill_color)

    painter.restore()


def render_text_overlay_png(
    layer: TextOverlayLayer,
    stage_width: int,
    stage_height: int,
    output_path: Path,
) -> Path:
    """Render a transparent full-stage PNG containing a single TextOverlayLayer."""
    sw = max(16, int(stage_width))
    sh = max(16, int(stage_height))
    img = QImage(sw, sh, QImage.Format.Format_RGBA8888)
    img.fill(QColor(0, 0, 0, 0))
    px, py, pw, ph = layer.rect.to_pixel_rect(sw, sh)
    painter = QPainter(img)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    paint_text_overlay(painter, QRectF(float(px), float(py), float(pw), float(ph)), layer, float(sh))
    painter.end()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(str(output_path), "PNG")
    return output_path


def paint_classic_caption(
    painter: QPainter,
    region: QRectF,
    caption: GifCaption,
) -> None:
    """Paint classic top/bottom Impact meme captions inside region with exact WYSIWYG metrics."""
    if region.width() < 24.0 or region.height() < 24.0 or not caption.is_active():
        return

    font_px, border_w = compute_meme_caption_metrics(
        region.width(),
        region.height(),
        size_scale=caption.size_scale,
    )
    font = QFont(resolve_meme_font_family("impact"))
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


def render_classic_caption_png(
    caption: GifCaption,
    stage_width: int,
    stage_height: int,
    output_path: Path,
) -> Path:
    """Render classic top/bottom Impact caption into a transparent full-stage PNG."""
    sw = max(16, int(stage_width))
    sh = max(16, int(stage_height))
    img = QImage(sw, sh, QImage.Format.Format_RGBA8888)
    img.fill(QColor(0, 0, 0, 0))
    painter = QPainter(img)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    paint_classic_caption(painter, QRectF(0.0, 0.0, float(sw), float(sh)), caption)
    painter.end()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(str(output_path), "PNG")
    return output_path
