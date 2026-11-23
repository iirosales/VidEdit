"""FFmpeg / FFprobe discovery, media probing, bitrate math, and command builders."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Optional

if TYPE_CHECKING:
    from videdit.meme_project import MemeComposition


def _windows_creation_flags() -> int:
    """Return CREATE_NO_WINDOW on Windows so subprocesses never flash a console."""
    if os.name == "nt":
        return subprocess.CREATE_NO_WINDOW
    return 0


def find_ffmpeg_binaries() -> tuple[Optional[str], Optional[str]]:
    """Locate ffmpeg and ffprobe executables on PATH or standard WinGet locations."""
    ffmpeg_path = shutil.which("ffmpeg")
    ffprobe_path = shutil.which("ffprobe")

    if ffmpeg_path and ffprobe_path:
        return ffmpeg_path, ffprobe_path

    if os.name == "nt":
        local_app = os.environ.get("LOCALAPPDATA", "")
        if local_app:
            links_dir = Path(local_app) / "Microsoft" / "WinGet" / "Links"
            if not ffmpeg_path and (links_dir / "ffmpeg.exe").is_file():
                ffmpeg_path = str(links_dir / "ffmpeg.exe")
            if not ffprobe_path and (links_dir / "ffprobe.exe").is_file():
                ffprobe_path = str(links_dir / "ffprobe.exe")

            if not ffmpeg_path or not ffprobe_path:
                pkg_root = Path(local_app) / "Microsoft" / "WinGet" / "Packages"
                if pkg_root.is_dir():
                    for candidate in pkg_root.glob("Gyan.FFmpeg*/*/bin/ffmpeg.exe"):
                        if not ffmpeg_path and candidate.is_file():
                            ffmpeg_path = str(candidate)
                        probe_cand = candidate.with_name("ffprobe.exe")
                        if not ffprobe_path and probe_cand.is_file():
                            ffprobe_path = str(probe_cand)
                        if ffmpeg_path and ffprobe_path:
                            break

    return ffmpeg_path, ffprobe_path


@dataclass(frozen=True)
class VideoInfo:
    """Metadata probed from an input media file."""

    path: Path
    duration_sec: float
    width: int
    height: int
    fps: float
    has_audio: bool
    file_size_bytes: int
    video_codec: str


@dataclass(frozen=True)
class CropRect:
    """Pixel crop rectangle in source video coordinates."""

    x: int
    y: int
    width: int
    height: int

    def to_ffmpeg_filter(self) -> str:
        """Return an FFmpeg crop filter expression."""
        return f"crop={self.width}:{self.height}:{self.x}:{self.y}"


@dataclass(frozen=True)
class GifCaption:
    """Meme-style top/bottom caption settings for GIF export."""

    top_text: str = ""
    bottom_text: str = ""
    size_scale: float = 1.0
    uppercase: bool = True

    def is_active(self) -> bool:
        """Return True when either top or bottom caption contains text."""
        return bool(self.top_text.strip() or self.bottom_text.strip())


def normalize_crop_rect(
    crop: Optional[CropRect],
    source_width: int,
    source_height: int,
    min_size: int = 16,
) -> Optional[CropRect]:
    """Clamp and even-align a crop rect to source bounds; return None if full-frame."""
    if crop is None or source_width <= 0 or source_height <= 0:
        return None

    min_w = min(source_width, max(2, min_size))
    min_h = min(source_height, max(2, min_size))

    x = max(0, min(int(crop.x), source_width - min_w))
    y = max(0, min(int(crop.y), source_height - min_h))
    w = max(min_w, min(int(crop.width), source_width - x))
    h = max(min_h, min(int(crop.height), source_height - y))

    x = (x // 2) * 2
    y = (y // 2) * 2
    w = max(2, (min(w, source_width - x) // 2) * 2)
    h = max(2, (min(h, source_height - y) // 2) * 2)

    if x == 0 and y == 0 and w >= source_width - 1 and h >= source_height - 1:
        return None

    return CropRect(x=x, y=y, width=w, height=h)


def _parse_fps(rate_str: str) -> float:
    """Parse an ffprobe rational frame-rate string such as '30000/1001'."""
    if not rate_str or rate_str == "0/0":
        return 30.0
    if "/" in rate_str:
        num_s, den_s = rate_str.split("/", 1)
        try:
            num = float(num_s)
            den = float(den_s)
            return num / den if den > 0 else 30.0
        except ValueError:
            return 30.0
    try:
        val = float(rate_str)
        return val if val > 0 else 30.0
    except ValueError:
        return 30.0


def parse_ffprobe_json(raw_json: str, file_path: Path) -> VideoInfo:
    """Parse ffprobe JSON output into a VideoInfo instance."""
    data = json.loads(raw_json)
    fmt = data.get("format", {})
    streams = data.get("streams", [])

    video_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video_stream is None:
        raise ValueError(f"No video stream found in {file_path.name}")

    has_audio = any(s.get("codec_type") == "audio" for s in streams)

    duration_raw = fmt.get("duration") or video_stream.get("duration") or "0"
    try:
        duration_sec = max(0.0, float(duration_raw))
    except (TypeError, ValueError):
        duration_sec = 0.0

    size_raw = fmt.get("size")
    if size_raw is not None:
        try:
            file_size_bytes = int(size_raw)
        except (TypeError, ValueError):
            file_size_bytes = file_path.stat().st_size if file_path.exists() else 0
    else:
        file_size_bytes = file_path.stat().st_size if file_path.exists() else 0

    width = int(video_stream.get("width") or 0)
    height = int(video_stream.get("height") or 0)
    fps = _parse_fps(video_stream.get("avg_frame_rate") or video_stream.get("r_frame_rate") or "30/1")
    video_codec = str(video_stream.get("codec_name") or "unknown")

    return VideoInfo(
        path=file_path,
        duration_sec=duration_sec,
        width=width,
        height=height,
        fps=fps,
        has_audio=has_audio,
        file_size_bytes=file_size_bytes,
        video_codec=video_codec,
    )


def probe_video(file_path: Path, ffprobe_path: Optional[str] = None) -> VideoInfo:
    """Run ffprobe on a video file and return structured VideoInfo."""
    if ffprobe_path is None:
        _, ffprobe_path = find_ffmpeg_binaries()
    if not ffprobe_path:
        raise FileNotFoundError("ffprobe executable was not found.")

    cmd = [
        ffprobe_path,
        "-v",
        "quiet",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(file_path),
    ]
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=_windows_creation_flags(),
        check=False,
    )
    if proc.returncode != 0 or not proc.stdout.strip():
        err = proc.stderr.strip() or "Failed to inspect video metadata."
        raise RuntimeError(f"ffprobe failed: {err}")

    return parse_ffprobe_json(proc.stdout, file_path)


@dataclass(frozen=True)
class Mp4Plan:
    """Calculated 2-pass encoding parameters for a target MP4 size."""

    video_kbps: int
    audio_kbps: int
    scale_height: Optional[int]
    target_bytes: int
    is_tight_budget: bool


def calculate_mp4_encoding_plan(
    duration_sec: float,
    target_mb: float,
    has_audio: bool,
    mute_audio: bool,
    source_height: int,
    max_height: Optional[int] = None,
    auto_scale: bool = True,
    safety_factor: float = 0.93,
) -> Mp4Plan:
    """Compute video/audio bitrates and optional resolution cap to stay under target_mb."""
    if target_mb <= 0:
        raise ValueError("target_mb must be positive.")

    safe_duration = max(duration_sec, 0.2)
    # Use decimal MB (1_000_000 bytes) with a safety margin so output fits under both MB and MiB limits.
    target_bytes = int(target_mb * 1_000_000 * safety_factor)
    total_kbps = (target_bytes * 8.0) / (safe_duration * 1000.0)

    # Reserve ~1.5% (minimum 8 kbps) for MP4 container muxing overhead.
    overhead_kbps = max(8.0, total_kbps * 0.015)
    usable_kbps = max(48.0, total_kbps - overhead_kbps)

    if not has_audio or mute_audio:
        audio_kbps = 0
    else:
        audio_kbps = 24
        for tier in (128, 96, 64, 48, 32):
            if usable_kbps - tier >= usable_kbps * 0.72 and usable_kbps - tier >= 48:
                audio_kbps = tier
                break
        else:
            audio_kbps = max(24, int(usable_kbps * 0.22))

    video_kbps = max(36, int(usable_kbps - audio_kbps))

    scale_height: Optional[int] = None
    if max_height is not None and max_height > 0:
        if source_height <= 0 or source_height > max_height:
            scale_height = max_height
    elif auto_scale and source_height > 0:
        if video_kbps < 450 and source_height > 480:
            scale_height = 480
        elif video_kbps < 950 and source_height > 720:
            scale_height = 720
        elif video_kbps < 2200 and source_height > 1080:
            scale_height = 1080

    is_tight_budget = video_kbps < 250
    return Mp4Plan(
        video_kbps=video_kbps,
        audio_kbps=audio_kbps,
        scale_height=scale_height,
        target_bytes=target_bytes,
        is_tight_budget=is_tight_budget,
    )


def _video_scale_filter(scale_height: Optional[int]) -> str:
    """Build a video scale filter ensuring even dimensions for libx264 yuv420p."""
    if scale_height is not None and scale_height > 0:
        return f"scale=-2:'min(ih,{scale_height})'"
    return "scale=trunc(iw/2)*2:trunc(ih/2)*2"


def _build_mp4_vf(scale_height: Optional[int], crop: Optional[CropRect] = None) -> str:
    """Compose optional source crop followed by even-dimension scale filter."""
    parts: list[str] = []
    if crop is not None:
        parts.append(crop.to_ffmpeg_filter())
    parts.append(_video_scale_filter(scale_height))
    return ",".join(parts)


def build_mp4_quality_cmd(
    ffmpeg_path: str,
    input_path: Path,
    output_path: Path,
    start_sec: float,
    duration_sec: float,
    has_audio: bool,
    mute_audio: bool,
    scale_height: Optional[int] = None,
    crf: int = 20,
    crop: Optional[CropRect] = None,
) -> list[str]:
    """Build a single-pass high-quality H.264 MP4 trim command."""
    cmd = [
        ffmpeg_path,
        "-y",
        "-ss",
        f"{start_sec:.3f}",
        "-i",
        str(input_path),
        "-t",
        f"{duration_sec:.3f}",
        "-vf",
        _build_mp4_vf(scale_height, crop=crop),
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        str(crf),
        "-pix_fmt",
        "yuv420p",
    ]
    if has_audio and not mute_audio:
        cmd.extend(["-c:a", "aac", "-b:a", "192k"])
    else:
        cmd.append("-an")

    cmd.extend(["-movflags", "+faststart", "-progress", "pipe:1", "-nostats", str(output_path)])
    return cmd


def build_mp4_two_pass_cmds(
    ffmpeg_path: str,
    input_path: Path,
    output_path: Path,
    start_sec: float,
    duration_sec: float,
    plan: Mp4Plan,
    passlog_prefix: Path,
    crop: Optional[CropRect] = None,
) -> tuple[list[str], list[str]]:
    """Build Pass 1 and Pass 2 FFmpeg commands for size-targeted MP4 compression."""
    common_input = [
        ffmpeg_path,
        "-y",
        "-ss",
        f"{start_sec:.3f}",
        "-i",
        str(input_path),
        "-t",
        f"{duration_sec:.3f}",
        "-vf",
        _build_mp4_vf(plan.scale_height, crop=crop),
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-b:v",
        f"{plan.video_kbps}k",
        "-pix_fmt",
        "yuv420p",
        "-passlogfile",
        str(passlog_prefix),
    ]

    pass1_cmd = [
        *common_input,
        "-pass",
        "1",
        "-an",
        "-f",
        "mp4",
        "-progress",
        "pipe:1",
        "-nostats",
        os.devnull,
    ]

    pass2_cmd = [
        *common_input,
        "-pass",
        "2",
    ]
    if plan.audio_kbps > 0:
        pass2_cmd.extend(["-c:a", "aac", "-b:a", f"{plan.audio_kbps}k"])
    else:
        pass2_cmd.append("-an")

    pass2_cmd.extend(["-movflags", "+faststart", "-progress", "pipe:1", "-nostats", str(output_path)])
    return pass1_cmd, pass2_cmd


def find_impact_font_path() -> Optional[Path]:
    """Locate the Impact TrueType font file if installed on the system."""
    candidates: list[Path] = []
    if os.name == "nt":
        windir = Path(os.environ.get("WINDIR", r"C:\Windows"))
        candidates.extend([windir / "Fonts" / "impact.ttf", windir / "Fonts" / "Impact.ttf"])
        local_app = os.environ.get("LOCALAPPDATA", "")
        if local_app:
            candidates.append(Path(local_app) / "Microsoft" / "Windows" / "Fonts" / "impact.ttf")
    else:
        candidates.extend(
            [
                Path("/System/Library/Fonts/Supplemental/Impact.ttf"),
                Path("/Library/Fonts/Impact.ttf"),
                Path.home() / "Library" / "Fonts" / "Impact.ttf",
                Path("/usr/share/fonts/truetype/msttcorefonts/Impact.ttf"),
                Path("/usr/share/fonts/truetype/msttcorefonts/impact.ttf"),
                Path.home() / ".local" / "share" / "fonts" / "Impact.ttf",
            ]
        )
    for path in candidates:
        if path.is_file():
            return path
    return None


def _escape_chars(text: str, chars: str) -> str:
    return "".join(("\\" + ch) if ch in chars else ch for ch in text)


def escape_ffmpeg_drawtext(text: str) -> str:
    """Escape text for FFmpeg filtergraph drawtext option values (two-level parser)."""
    level1 = _escape_chars(text, "\\:'")
    return _escape_chars(level1, "\\',;[]= ")


def escape_ffmpeg_filter_path(path: Path) -> str:
    """Escape a filesystem path for a single-quoted FFmpeg drawtext fontfile value."""
    posix = str(path).replace("\\", "/")
    escaped = posix.replace(":", "\\:").replace("'", "\\'")
    return f"'{escaped}'"


def compute_meme_caption_metrics(
    width: float,
    height: float,
    size_scale: float = 1.0,
) -> tuple[float, int]:
    """Compute (font_px, border_w) for classic meme Impact captions."""
    safe_w = max(1.0, float(width))
    safe_h = max(1.0, float(height))
    scale = max(0.5, min(2.0, float(size_scale)))
    font_px = max(12.0, min(safe_h * 0.12, safe_w * 0.085) * scale)
    border_w = max(2, min(8, int(round(font_px / 10.0))))
    return font_px, border_w


def _estimate_impact_char_width(ch: str, font_px: float) -> float:
    if ch == " ":
        return font_px * 0.24
    if ch in "Iil1|!.,;:'\"":
        return font_px * 0.28
    if ch in "WMmw@%&":
        return font_px * 0.78
    if ch.islower():
        return font_px * 0.44
    return font_px * 0.52


def _estimate_impact_text_width(text: str, font_px: float) -> float:
    return sum(_estimate_impact_char_width(ch, font_px) for ch in text)


def wrap_meme_caption(
    text: str,
    max_width_px: float,
    font_px: float,
    uppercase: bool = True,
) -> list[str]:
    """Wrap meme caption text into centered lines fitting within ~90% of max_width_px."""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    if uppercase:
        normalized = normalized.upper()

    line_budget = max(font_px * 2.0, float(max_width_px) * 0.90)
    wrapped: list[str] = []

    for paragraph in normalized.split("\n"):
        cleaned = " ".join(paragraph.split())
        if not cleaned:
            continue
        words = cleaned.split(" ")
        current = ""
        for word in words:
            candidate = f"{current} {word}".strip() if current else word
            if _estimate_impact_text_width(candidate, font_px) <= line_budget:
                current = candidate
                continue

            if current:
                wrapped.append(current)
                current = ""

            if _estimate_impact_text_width(word, font_px) <= line_budget:
                current = word
            else:
                # Hard-split an extra-long single token so it never clips off-frame.
                chunk = ""
                for ch in word:
                    if chunk and _estimate_impact_text_width(chunk + ch, font_px) > line_budget:
                        wrapped.append(chunk)
                        chunk = ch
                    else:
                        chunk += ch
                current = chunk
        if current:
            wrapped.append(current)

    return wrapped


def _estimate_gif_output_size(
    max_width: Optional[int],
    source_width: Optional[int],
    source_height: Optional[int],
) -> tuple[int, int]:
    if source_width and source_width > 0 and source_height and source_height > 0:
        out_w = min(source_width, max_width) if (max_width and max_width > 0) else source_width
        out_h = max(2, int(round(source_height * (out_w / float(source_width)))))
        return out_w, out_h
    est_w = max_width if (max_width and max_width > 0) else 480
    est_h = max(2, int(round(est_w * 9.0 / 16.0)))
    return est_w, est_h


def _build_gif_caption_filters(
    caption: Optional[GifCaption],
    max_width: Optional[int],
    source_width: Optional[int],
    source_height: Optional[int],
) -> list[str]:
    if caption is None or not caption.is_active():
        return []

    out_w, out_h = _estimate_gif_output_size(max_width, source_width, source_height)
    scale = max(0.5, min(2.0, float(caption.size_scale)))
    est_font_px, border_w = compute_meme_caption_metrics(out_w, out_h, size_scale=scale)

    impact_path = find_impact_font_path()
    font_opt = (
        f"fontfile={escape_ffmpeg_filter_path(impact_path)}"
        if impact_path is not None
        else "font='Impact'"
    )
    h_coef = 0.12 * scale
    w_coef = 0.085 * scale
    fs_expr = f"max(12,min(h*{h_coef:.4f},w*{w_coef:.4f}))"

    filters: list[str] = []

    def append_line(line: str, y_expr: str) -> None:
        esc_text = escape_ffmpeg_drawtext(line)
        filters.append(
            f"drawtext={font_opt}:expansion=none:text={esc_text}:"
            f"fontcolor=white:bordercolor=black:borderw={border_w}:"
            f"fontsize='{fs_expr}':x='(w-text_w)/2':y='{y_expr}'"
        )

    top_lines = wrap_meme_caption(caption.top_text, out_w, est_font_px, uppercase=caption.uppercase)
    for idx, line in enumerate(top_lines):
        y_expr = f"h*0.04+{idx}*({fs_expr}*1.12)"
        append_line(line, y_expr)

    bottom_lines = wrap_meme_caption(caption.bottom_text, out_w, est_font_px, uppercase=caption.uppercase)
    num_bottom = len(bottom_lines)
    for idx, line in enumerate(bottom_lines):
        offset_from_bottom = num_bottom - idx
        y_expr = f"h*0.96-{offset_from_bottom}*({fs_expr}*1.12)"
        append_line(line, y_expr)

    return filters


def build_gif_cmd(
    ffmpeg_path: str,
    input_path: Path,
    output_path: Path,
    start_sec: float,
    duration_sec: float,
    fps: int = 15,
    max_width: Optional[int] = 480,
    crop: Optional[CropRect] = None,
    caption: Optional[GifCaption] = None,
    source_width: Optional[int] = None,
    source_height: Optional[int] = None,
) -> list[str]:
    """Build a 2-pass palettegen/paletteuse GIF export command with optional meme captions."""
    width_expr = f"'min(iw,{max_width})'" if max_width and max_width > 0 else "iw"
    crop_prefix = f"{crop.to_ffmpeg_filter()}," if crop is not None else ""
    caption_filters = _build_gif_caption_filters(
        caption=caption,
        max_width=max_width,
        source_width=source_width,
        source_height=source_height,
    )
    caption_suffix = f",{','.join(caption_filters)}" if caption_filters else ""
    filter_complex = (
        f"[0:v]{crop_prefix}fps={fps},scale={width_expr}:-1:flags=lanczos{caption_suffix},"
        f"split[s0][s1];"
        f"[s0]palettegen=stats_mode=diff[p];"
        f"[s1][p]paletteuse=dither=bayer:bayer_scale=5:diff_mode=rectangle"
    )
    return [
        ffmpeg_path,
        "-y",
        "-ss",
        f"{start_sec:.3f}",
        "-i",
        str(input_path),
        "-t",
        f"{duration_sec:.3f}",
        "-filter_complex",
        filter_complex,
        "-loop",
        "0",
        "-progress",
        "pipe:1",
        "-nostats",
        str(output_path),
    ]


def parse_ffmpeg_progress_line(line: str, duration_sec: float) -> Optional[float]:
    """Parse a line from ffmpeg `-progress pipe:1` into a 0.0..1.0 ratio."""
    stripped = line.strip()
    if stripped == "progress=end":
        return 1.0
    if stripped.startswith("out_time_us="):
        raw = stripped.split("=", 1)[1]
        try:
            out_us = int(raw)
        except ValueError:
            return None
        if duration_sec <= 0:
            return None
        ratio = (out_us / 1_000_000.0) / duration_sec
        return max(0.0, min(1.0, ratio))
    if stripped.startswith("out_time_ms="):
        raw = stripped.split("=", 1)[1]
        try:
            # Note: ffmpeg historically emits microseconds under the out_time_ms key.
            out_us = int(raw)
        except ValueError:
            return None
        if duration_sec <= 0:
            return None
        ratio = (out_us / 1_000_000.0) / duration_sec
        return max(0.0, min(1.0, ratio))
    return None


def _even_size(val: int, minimum: int = 2) -> int:
    return max(minimum, (int(val) // 2) * 2)


def compute_meme_output_stage_size(
    base_width: int,
    base_height: int,
    max_width: Optional[int] = None,
    max_height: Optional[int] = None,
) -> tuple[int, int]:
    """Compute even-aligned target stage dimensions respecting optional max_width/max_height."""
    bw = max(2, int(base_width))
    bh = max(2, int(base_height))
    scale = 1.0
    if max_width is not None and max_width > 0 and bw > max_width:
        scale = min(scale, float(max_width) / float(bw))
    if max_height is not None and max_height > 0 and bh > max_height:
        scale = min(scale, float(max_height) / float(bh))
    out_w = _even_size(max(2, int(round(bw * scale))))
    out_h = _even_size(max(2, int(round(bh * scale))))
    return out_w, out_h


def _ffmpeg_colorkey_hex(color_str: str) -> str:
    cleaned = color_str.strip().lstrip("#")
    if cleaned.lower().startswith("0x"):
        cleaned = cleaned[2:]
    if len(cleaned) != 6:
        return "0x00FF00"
    return f"0x{cleaned.upper()}"


def _format_enable_expr(
    start_sec: Optional[float],
    end_sec: Optional[float],
    duration_sec: float,
) -> str:
    if start_sec is None and end_sec is None:
        return ""
    s = max(0.0, float(start_sec)) if start_sec is not None else 0.0
    e = min(duration_sec, float(end_sec)) if end_sec is not None else duration_sec
    if e <= s:
        e = min(duration_sec, s + 0.05)
    return f":enable='between(t,{s:.3f},{e:.3f})'"


@dataclass(frozen=True)
class MemeFilterAssets:
    """Pre-rendered PNG masks and text assets consumed by build_meme_export_cmds."""

    top_bar_png: Optional[Path] = None
    top_bar_height: int = 0
    shape_mask_pngs: Optional[dict[str, Path]] = None
    text_overlay_pngs: Optional[dict[str, Path]] = None
    classic_caption_png: Optional[Path] = None


def build_meme_export_cmds(
    ffmpeg_path: str,
    composition: "MemeComposition",
    output_path: Path,
    output_format: Literal["mp4", "gif", "png", "jpg"],
    stage_width: int,
    stage_height: int,
    duration_sec: float,
    assets: Optional[MemeFilterAssets] = None,
    mute_audio: bool = False,
    mp4_plan: Optional[Mp4Plan] = None,
    passlog_prefix: Optional[Path] = None,
    crf: int = 20,
    gif_fps: int = 15,
    still_frame_sec: float = 0.0,
) -> list[list[str]]:
    """Build FFmpeg command(s) for exporting a multi-layer MemeComposition."""
    from videdit.meme_project import MediaOverlayLayer, TextOverlayLayer

    resolved_assets = assets or MemeFilterAssets()
    shape_masks = resolved_assets.shape_mask_pngs or {}
    text_pngs = resolved_assets.text_overlay_pngs or {}

    base = composition.base
    is_animated_out = output_format in ("mp4", "gif")
    safe_dur = max(0.2, float(duration_sec))
    sw = _even_size(stage_width)
    sh = _even_size(stage_height)
    bar_h = _even_size(resolved_assets.top_bar_height, minimum=0) if resolved_assets.top_bar_png else 0

    input_args: list[str] = []
    next_input_idx = 0

    def add_input(flags: list[str]) -> int:
        nonlocal next_input_idx
        idx = next_input_idx
        input_args.extend(flags)
        next_input_idx += 1
        return idx

    # Input 0: Base layer
    if base.kind == "image":
        if is_animated_out:
            base_idx = add_input(["-loop", "1", "-t", f"{safe_dur:.3f}", "-i", str(base.path)])
        else:
            base_idx = add_input(["-i", str(base.path)])
    else:
        if is_animated_out:
            base_idx = add_input(
                ["-ss", f"{max(0.0, base.start_sec):.3f}", "-t", f"{safe_dur:.3f}", "-i", str(base.path)]
            )
        else:
            seek_at = max(0.0, still_frame_sec)
            base_idx = add_input(["-ss", f"{seek_at:.3f}", "-i", str(base.path)])

    filter_parts: list[str] = []

    # Prepare base stream -> [base0]
    base_ops: list[str] = []
    norm_base_crop = normalize_crop_rect(base.crop, base.width, base.height)
    if norm_base_crop is not None:
        base_ops.append(norm_base_crop.to_ffmpeg_filter())
    base_ops.append(f"scale={sw}:{sh}:flags=lanczos")
    if is_animated_out:
        target_fps = int(gif_fps) if output_format == "gif" else 30
        base_ops.append(f"fps={target_fps}")
    base_ops.append("format=rgba")
    if bar_h > 0:
        base_ops.append(f"pad=iw:ih+{bar_h}:0:{bar_h}:color=white")
    filter_parts.append(f"[{base_idx}:v]{','.join(base_ops)}[cur0]")

    cur_label = "cur0"
    stage_counter = 1

    # Top white meme bar text PNG
    if bar_h > 0 and resolved_assets.top_bar_png is not None:
        bar_idx = add_input(["-i", str(resolved_assets.top_bar_png)])
        next_label = f"cur{stage_counter}"
        stage_counter += 1
        filter_parts.append(
            f"[{cur_label}][{bar_idx}:v]overlay=x=0:y=0:eof_action=repeat[{next_label}]"
        )
        cur_label = next_label

    audio_labels: list[str] = []
    if output_format == "mp4" and base.kind == "video" and base.has_audio and not mute_audio:
        filter_parts.append(f"[{base_idx}:a]atrim=0:{safe_dur:.3f},asetpts=PTS-STARTPTS[abase]")
        audio_labels.append("[abase]")

    # Composite visible overlays in z-order
    for layer in composition.overlays:
        if not layer.visible:
            continue

        if isinstance(layer, MediaOverlayLayer):
            if layer.kind == "image":
                if is_animated_out:
                    ov_idx = add_input(["-loop", "1", "-t", f"{safe_dur:.3f}", "-i", str(layer.path)])
                else:
                    ov_idx = add_input(["-i", str(layer.path)])
            else:
                if is_animated_out:
                    ov_flags = ["-stream_loop", "-1"] if layer.loop_video else []
                    ov_flags.extend(["-t", f"{safe_dur:.3f}", "-i", str(layer.path)])
                    ov_idx = add_input(ov_flags)
                else:
                    ov_idx = add_input(["-i", str(layer.path)])

            px, py, pw, ph = layer.rect.to_pixel_rect(sw, sh)
            ov_ops: list[str] = []
            norm_ov_crop = normalize_crop_rect(
                layer.source_crop,
                layer.source_width,
                layer.source_height,
                min_size=8,
            )
            if norm_ov_crop is not None:
                ov_ops.append(norm_ov_crop.to_ffmpeg_filter())
            ov_ops.append(f"scale={pw}:{ph}:flags=lanczos")
            if layer.flip_h:
                ov_ops.append("hflip")
            ov_ops.append("format=rgba")
            if layer.chroma_key_color:
                sim = max(0.01, min(0.60, float(layer.chroma_similarity)))
                ov_ops.append(f"colorkey={_ffmpeg_colorkey_hex(layer.chroma_key_color)}:{sim:.2f}:0.12")
            if layer.opacity < 0.999:
                op = max(0.05, min(1.0, float(layer.opacity)))
                ov_ops.append(f"colorchannelmixer=aa={op:.3f}")
            if is_animated_out and layer.kind != "image" and layer.start_sec is not None and layer.start_sec > 0:
                ov_ops.append(f"setpts=PTS-STARTPTS+{layer.start_sec:.3f}/TB")

            ov_pre = f"ovpre{stage_counter}"
            filter_parts.append(f"[{ov_idx}:v]{','.join(ov_ops)}[{ov_pre}]")

            mask_path = shape_masks.get(layer.id)
            if layer.shape != "rect" and mask_path is not None:
                if is_animated_out:
                    mask_idx = add_input(["-loop", "1", "-t", f"{safe_dur:.3f}", "-i", str(mask_path)])
                else:
                    mask_idx = add_input(["-i", str(mask_path)])
                ov_rgb = f"ovrgb{stage_counter}"
                ov_asrc = f"ovasrc{stage_counter}"
                ov_alpha = f"ova{stage_counter}"
                mask_gray = f"maskg{stage_counter}"
                comb_alpha = f"comba{stage_counter}"
                ov_ready = f"ovready{stage_counter}"
                # Multiply existing alpha (PNG alpha, colorkey, opacity) with the shape cutout mask
                # so alphamerge never overwrites chroma-key or opacity transparency.
                filter_parts.append(f"[{ov_pre}]split[{ov_rgb}][{ov_asrc}]")
                filter_parts.append(f"[{ov_asrc}]alphaextract,format=gray[{ov_alpha}]")
                filter_parts.append(f"[{mask_idx}:v]scale={pw}:{ph},format=rgba,alphaextract,format=gray[{mask_gray}]")
                filter_parts.append(f"[{ov_alpha}][{mask_gray}]blend=all_mode=multiply,format=gray[{comb_alpha}]")
                filter_parts.append(f"[{ov_rgb}][{comb_alpha}]alphamerge,format=rgba[{ov_ready}]")
            else:
                ov_ready = ov_pre

            enable_expr = (
                _format_enable_expr(layer.start_sec, layer.end_sec, safe_dur)
                if is_animated_out
                else ""
            )
            next_label = f"cur{stage_counter}"
            eof_mode = "repeat" if layer.kind == "image" or not layer.loop_video else "pass"
            filter_parts.append(
                f"[{cur_label}][{ov_ready}]overlay=x={px}:y={py + bar_h}:eof_action={eof_mode}{enable_expr}[{next_label}]"
            )
            cur_label = next_label

            if (
                output_format == "mp4"
                and layer.kind == "video"
                and layer.has_audio
                and not layer.mute_audio
                and not mute_audio
            ):
                alabel = f"aov{stage_counter}"
                delay_ms = int(round(max(0.0, layer.start_sec or 0.0) * 1000.0))
                a_ops = [f"atrim=0:{safe_dur:.3f}", "asetpts=PTS-STARTPTS"]
                if delay_ms > 0:
                    a_ops.append(f"adelay={delay_ms}|{delay_ms}")
                filter_parts.append(f"[{ov_idx}:a]{','.join(a_ops)}[{alabel}]")
                audio_labels.append(f"[{alabel}]")

            stage_counter += 1

        elif isinstance(layer, TextOverlayLayer) and layer.text.strip():
            txt_png = text_pngs.get(layer.id)
            if txt_png is not None:
                txt_idx = add_input(["-i", str(txt_png)])
                enable_expr = (
                    _format_enable_expr(layer.start_sec, layer.end_sec, safe_dur)
                    if is_animated_out
                    else ""
                )
                next_label = f"cur{stage_counter}"
                filter_parts.append(
                    f"[{cur_label}][{txt_idx}:v]overlay=x=0:y={bar_h}:eof_action=repeat{enable_expr}[{next_label}]"
                )
                cur_label = next_label
                stage_counter += 1

    # Classic top/bottom Impact caption (if set)
    if composition.classic_caption is not None and composition.classic_caption.is_active():
        if resolved_assets.classic_caption_png is not None:
            cap_idx = add_input(["-i", str(resolved_assets.classic_caption_png)])
            next_label = f"cur{stage_counter}"
            filter_parts.append(
                f"[{cur_label}][{cap_idx}:v]overlay=x=0:y={bar_h}:eof_action=repeat[{next_label}]"
            )
            cur_label = next_label
            stage_counter += 1
        else:
            cap_filters = _build_gif_caption_filters(
                caption=composition.classic_caption,
                max_width=sw,
                source_width=sw,
                source_height=sh,
            )
            if cap_filters:
                next_label = f"cur{stage_counter}"
                filter_parts.append(f"[{cur_label}]{','.join(cap_filters)}[{next_label}]")
                cur_label = next_label
                stage_counter += 1

    # Final output formatting in filtergraph
    if output_format == "gif":
        filter_parts.append(
            f"[{cur_label}]fps={int(gif_fps)},split[s0][s1];"
            f"[s0]palettegen=stats_mode=diff[p];"
            f"[s1][p]paletteuse=dither=bayer:bayer_scale=5:diff_mode=rectangle[vout]"
        )
    elif output_format == "mp4":
        filter_parts.append(f"[{cur_label}]format=yuv420p[vout]")
    elif output_format == "jpg":
        filter_parts.append(f"[{cur_label}]format=yuvj420p[vout]")
    else:
        filter_parts.append(f"[{cur_label}]format=rgba[vout]")

    # Audio mixing for MP4
    map_audio: list[str] = []
    if output_format == "mp4" and audio_labels:
        if len(audio_labels) == 1:
            lbl = audio_labels[0]
            map_audio = ["-map", lbl]
        else:
            filter_parts.append(
                f"{''.join(audio_labels)}amix=inputs={len(audio_labels)}:duration=first:dropout_transition=0[aout]"
            )
            map_audio = ["-map", "[aout]"]

    filter_complex = ";".join(filter_parts)
    base_cmd = [ffmpeg_path, "-y", *input_args, "-filter_complex", filter_complex]

    if output_format in ("png", "jpg"):
        cmd = [*base_cmd, "-map", "[vout]", "-frames:v", "1"]
        if output_format == "jpg":
            cmd.extend(["-q:v", "2"])
        cmd.extend(["-progress", "pipe:1", "-nostats", str(output_path)])
        return [cmd]

    if output_format == "gif":
        cmd = [
            *base_cmd,
            "-map",
            "[vout]",
            "-t",
            f"{safe_dur:.3f}",
            "-loop",
            "0",
            "-progress",
            "pipe:1",
            "-nostats",
            str(output_path),
        ]
        return [cmd]

    # MP4 export (single-pass CRF or 2-pass size-targeted)
    if mp4_plan is None:
        cmd = [
            *base_cmd,
            "-map",
            "[vout]",
            *map_audio,
            "-t",
            f"{safe_dur:.3f}",
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            str(crf),
            "-pix_fmt",
            "yuv420p",
        ]
        if map_audio:
            cmd.extend(["-c:a", "aac", "-b:a", "192k"])
        else:
            cmd.append("-an")
        cmd.extend(["-movflags", "+faststart", "-progress", "pipe:1", "-nostats", str(output_path)])
        return [cmd]

    if passlog_prefix is None:
        raise ValueError("passlog_prefix is required for 2-pass MP4 export.")

    pass1_cmd = [
        *base_cmd,
        "-map",
        "[vout]",
        "-t",
        f"{safe_dur:.3f}",
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-b:v",
        f"{mp4_plan.video_kbps}k",
        "-pix_fmt",
        "yuv420p",
        "-passlogfile",
        str(passlog_prefix),
        "-pass",
        "1",
        "-an",
        "-f",
        "mp4",
        "-progress",
        "pipe:1",
        "-nostats",
        os.devnull,
    ]
    pass2_cmd = [
        *base_cmd,
        "-map",
        "[vout]",
        *map_audio,
        "-t",
        f"{safe_dur:.3f}",
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-b:v",
        f"{mp4_plan.video_kbps}k",
        "-pix_fmt",
        "yuv420p",
        "-passlogfile",
        str(passlog_prefix),
        "-pass",
        "2",
    ]
    if map_audio and mp4_plan.audio_kbps > 0:
        pass2_cmd.extend(["-c:a", "aac", "-b:a", f"{mp4_plan.audio_kbps}k"])
    else:
        pass2_cmd.append("-an")
    pass2_cmd.extend(["-movflags", "+faststart", "-progress", "pipe:1", "-nostats", str(output_path)])
    return [pass1_cmd, pass2_cmd]
