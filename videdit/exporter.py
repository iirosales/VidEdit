"""Asynchronous export worker running FFmpeg encoding jobs safely off the UI thread."""

from __future__ import annotations

import os
import subprocess
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Optional

from PySide6.QtCore import QThread, Signal
from PySide6.QtGui import QGuiApplication

from videdit.ffmpeg_tools import (
    CropRect,
    GifCaption,
    MemeFilterAssets,
    VideoInfo,
    build_gif_cmd,
    build_meme_export_cmds,
    build_mp4_quality_cmd,
    build_mp4_two_pass_cmds,
    calculate_mp4_encoding_plan,
    compute_meme_output_stage_size,
    find_ffmpeg_binaries,
    normalize_crop_rect,
    parse_ffmpeg_progress_line,
)
from videdit.meme_project import (
    MediaOverlayLayer,
    MemeComposition,
    TextOverlayLayer,
    render_classic_caption_png,
    render_shape_mask_png,
    render_text_overlay_png,
    render_top_white_bar_png,
)


@dataclass(frozen=True)
class ExportOptions:
    """User-selected export parameters."""

    input_path: Path
    output_path: Path
    start_sec: float
    end_sec: float
    output_format: Literal["mp4", "gif", "png", "jpg"]
    video_info: VideoInfo
    crop: Optional[CropRect] = None
    # MP4 options
    target_size_mb: Optional[float] = None  # None => high quality CRF
    max_height: Optional[int] = None        # None => Auto (when target size) or Original
    auto_scale_for_target: bool = True
    mute_audio: bool = False
    # GIF options
    gif_fps: int = 15
    gif_max_width: Optional[int] = 480
    gif_target_size_mb: Optional[float] = None
    gif_caption: Optional[GifCaption] = None
    # Meme Studio composition (optional)
    meme_composition: Optional[MemeComposition] = None
    still_frame_sec: float = 0.0


def _cleanup_passlogs(passlog_prefix: Path) -> None:
    """Delete temporary FFmpeg 2-pass log files."""
    parent = passlog_prefix.parent
    stem = passlog_prefix.name
    for path in parent.glob(f"{stem}*"):
        try:
            path.unlink()
        except OSError:
            pass


def run_ffmpeg_with_progress(
    cmd: list[str],
    duration_sec: float,
    on_progress: Optional[callable] = None,
    cancel_check: Optional[callable] = None,
    process_holder: Optional[list[subprocess.Popen]] = None,
) -> tuple[int, str]:
    """Execute an FFmpeg command, streaming `-progress pipe:1` updates."""
    creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        creationflags=creationflags,
    )
    if process_holder is not None:
        process_holder.append(proc)

    stderr_chunks: list[str] = []

    def _drain_stderr() -> None:
        if proc.stderr is None:
            return
        try:
            for chunk in proc.stderr:
                stderr_chunks.append(chunk)
        except ValueError:
            pass

    stderr_thread = threading.Thread(target=_drain_stderr, daemon=True)
    stderr_thread.start()

    try:
        if proc.stdout is not None:
            for line in proc.stdout:
                if cancel_check and cancel_check():
                    proc.terminate()
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait()
                    return -1, "Export cancelled by user."
                ratio = parse_ffmpeg_progress_line(line, duration_sec)
                if ratio is not None and on_progress is not None:
                    on_progress(ratio)

        returncode = proc.wait()
        stderr_thread.join(timeout=5)
        return returncode, "".join(stderr_chunks)
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        stderr_thread.join(timeout=2)
        if process_holder is not None and proc in process_holder:
            process_holder.remove(proc)


def _prepare_meme_filter_assets(
    composition: MemeComposition,
    stage_width: int,
    stage_height: int,
    tmp_dir: Path,
) -> MemeFilterAssets:
    """Render shape masks, white top bar, and text PNGs into tmp_dir."""
    shape_masks: dict[str, Path] = {}
    text_pngs: dict[str, Path] = {}
    top_bar_png: Optional[Path] = None
    top_bar_height = 0
    classic_caption_png: Optional[Path] = None

    has_qt_app = QGuiApplication.instance() is not None

    if composition.base.top_bar_text.strip() and has_qt_app:
        bar_path = tmp_dir / "top_bar.png"
        top_bar_png, top_bar_height = render_top_white_bar_png(
            stage_width=stage_width,
            text=composition.base.top_bar_text,
            output_path=bar_path,
            font_scale=composition.base.top_bar_font_scale,
        )

    for layer in composition.overlays:
        if not layer.visible:
            continue
        if isinstance(layer, MediaOverlayLayer) and layer.shape != "rect":
            _, _, pw, ph = layer.rect.to_pixel_rect(stage_width, stage_height)
            mask_path = tmp_dir / f"mask_{layer.id}.png"
            render_shape_mask_png(pw, ph, layer.shape, mask_path)
            shape_masks[layer.id] = mask_path
        elif isinstance(layer, TextOverlayLayer) and layer.text.strip() and has_qt_app:
            txt_path = tmp_dir / f"text_{layer.id}.png"
            render_text_overlay_png(layer, stage_width, stage_height, txt_path)
            text_pngs[layer.id] = txt_path

    if (
        composition.classic_caption is not None
        and composition.classic_caption.is_active()
        and has_qt_app
    ):
        cap_path = tmp_dir / "classic_caption.png"
        render_classic_caption_png(composition.classic_caption, stage_width, stage_height, cap_path)
        classic_caption_png = cap_path

    return MemeFilterAssets(
        top_bar_png=top_bar_png,
        top_bar_height=top_bar_height,
        shape_mask_pngs=shape_masks,
        text_overlay_pngs=text_pngs,
        classic_caption_png=classic_caption_png,
    )


def _execute_meme_export(
    options: ExportOptions,
    ffmpeg_path: str,
    emit: callable,
    cancel_check: Optional[callable],
    process_holder: Optional[list[subprocess.Popen]],
) -> Path:
    """Execute a multi-layer MemeComposition export to PNG, JPG, MP4, or GIF."""
    assert options.meme_composition is not None
    comp = options.meme_composition
    base_w, base_h = comp.base.cropped_size()
    duration_sec = comp.effective_duration_sec()

    with tempfile.TemporaryDirectory(prefix="videdit_meme_") as tmp_str:
        tmp_dir = Path(tmp_str)

        if options.output_format in ("png", "jpg"):
            stage_w, stage_h = compute_meme_output_stage_size(
                base_w, base_h, max_height=options.max_height
            )
            assets = _prepare_meme_filter_assets(comp, stage_w, stage_h, tmp_dir)
            emit(10, f"Rendering {options.output_format.upper()} meme...")
            cmds = build_meme_export_cmds(
                ffmpeg_path=ffmpeg_path,
                composition=comp,
                output_path=options.output_path,
                output_format=options.output_format,
                stage_width=stage_w,
                stage_height=stage_h,
                duration_sec=duration_sec,
                assets=assets,
                still_frame_sec=options.still_frame_sec,
            )
            rc, err = run_ffmpeg_with_progress(
                cmds[0],
                1.0,
                on_progress=lambda r: emit(10 + int(r * 88), f"Rendering {options.output_format.upper()} meme..."),
                cancel_check=cancel_check,
                process_holder=process_holder,
            )
            if rc != 0:
                raise RuntimeError(err.strip() or f"FFmpeg {options.output_format.upper()} export failed.")
            emit(100, "Meme export complete.")
            return options.output_path

        if options.output_format == "mp4":
            if options.target_size_mb is None:
                stage_w, stage_h = compute_meme_output_stage_size(
                    base_w, base_h, max_height=options.max_height
                )
                assets = _prepare_meme_filter_assets(comp, stage_w, stage_h, tmp_dir)
                emit(0, "Encoding MP4 meme (High Quality)...")
                cmds = build_meme_export_cmds(
                    ffmpeg_path=ffmpeg_path,
                    composition=comp,
                    output_path=options.output_path,
                    output_format="mp4",
                    stage_width=stage_w,
                    stage_height=stage_h,
                    duration_sec=duration_sec,
                    assets=assets,
                    mute_audio=options.mute_audio,
                )
                rc, err = run_ffmpeg_with_progress(
                    cmds[0],
                    duration_sec,
                    on_progress=lambda r: emit(int(r * 98), "Encoding MP4 meme (High Quality)..."),
                    cancel_check=cancel_check,
                    process_holder=process_holder,
                )
                if rc != 0:
                    raise RuntimeError(err.strip() or "FFmpeg MP4 meme export failed.")
                emit(100, "Meme export complete.")
                return options.output_path

            safety_factor = 0.93
            max_allowed_bytes = int(options.target_size_mb * 1_000_000)
            passlog_prefix = tmp_dir / "ffmpeg2pass"

            for attempt in range(2):
                plan = calculate_mp4_encoding_plan(
                    duration_sec=duration_sec,
                    target_mb=options.target_size_mb,
                    has_audio=comp.has_any_audio(),
                    mute_audio=options.mute_audio,
                    source_height=base_h,
                    max_height=options.max_height,
                    auto_scale=options.auto_scale_for_target,
                    safety_factor=safety_factor,
                )
                stage_w, stage_h = compute_meme_output_stage_size(
                    base_w, base_h, max_height=plan.scale_height
                )
                assets = _prepare_meme_filter_assets(comp, stage_w, stage_h, tmp_dir)
                pass1_cmd, pass2_cmd = build_meme_export_cmds(
                    ffmpeg_path=ffmpeg_path,
                    composition=comp,
                    output_path=options.output_path,
                    output_format="mp4",
                    stage_width=stage_w,
                    stage_height=stage_h,
                    duration_sec=duration_sec,
                    assets=assets,
                    mute_audio=options.mute_audio,
                    mp4_plan=plan,
                    passlog_prefix=passlog_prefix,
                )
                label_suffix = " (tightening fit)" if attempt > 0 else ""
                emit(0, f"Pass 1/2: Analyzing meme{label_suffix}...")
                rc1, err1 = run_ffmpeg_with_progress(
                    pass1_cmd,
                    duration_sec,
                    on_progress=lambda r: emit(int(r * 45), f"Pass 1/2: Analyzing meme{label_suffix}..."),
                    cancel_check=cancel_check,
                    process_holder=process_holder,
                )
                if rc1 != 0:
                    raise RuntimeError(err1.strip() or "FFmpeg Pass 1 failed.")

                emit(45, f"Pass 2/2: Compressing meme to {options.target_size_mb:g} MB{label_suffix}...")
                rc2, err2 = run_ffmpeg_with_progress(
                    pass2_cmd,
                    duration_sec,
                    on_progress=lambda r: emit(
                        45 + int(r * 53),
                        f"Pass 2/2: Compressing meme to {options.target_size_mb:g} MB{label_suffix}...",
                    ),
                    cancel_check=cancel_check,
                    process_holder=process_holder,
                )
                if rc2 != 0:
                    raise RuntimeError(err2.strip() or "FFmpeg Pass 2 failed.")

                if options.output_path.exists() and options.output_path.stat().st_size <= max_allowed_bytes:
                    break
                safety_factor = 0.80
                _cleanup_passlogs(passlog_prefix)

            emit(100, "Meme export complete.")
            return options.output_path

        # GIF export with optional auto-fit size ladder
        width_ladder = [options.gif_max_width, 480, 360, 240]
        fps_ladder = [options.gif_fps, 15, 12, 10]
        candidates: list[tuple[Optional[int], int]] = [(options.gif_max_width, options.gif_fps)]
        if options.gif_target_size_mb is not None:
            for w in width_ladder:
                for f in fps_ladder:
                    if (w is None or options.gif_max_width is None or w <= options.gif_max_width) and f <= options.gif_fps:
                        pair = (w, f)
                        if pair not in candidates:
                            candidates.append(pair)

        max_gif_bytes = int(options.gif_target_size_mb * 1_000_000) if options.gif_target_size_mb else None
        for idx, (cand_width, cand_fps) in enumerate(candidates):
            step_msg = (
                f"Generating GIF ({cand_width or 'orig'}px @ {cand_fps}fps)..."
                if idx == 0
                else f"Refitting GIF under {options.gif_target_size_mb:g} MB ({cand_width or 'orig'}px @ {cand_fps}fps)..."
            )
            emit(0, step_msg)
            stage_w, stage_h = compute_meme_output_stage_size(base_w, base_h, max_width=cand_width)
            assets = _prepare_meme_filter_assets(comp, stage_w, stage_h, tmp_dir)
            cmds = build_meme_export_cmds(
                ffmpeg_path=ffmpeg_path,
                composition=comp,
                output_path=options.output_path,
                output_format="gif",
                stage_width=stage_w,
                stage_height=stage_h,
                duration_sec=duration_sec,
                assets=assets,
                gif_fps=cand_fps,
            )
            rc, err = run_ffmpeg_with_progress(
                cmds[0],
                duration_sec,
                on_progress=lambda r: emit(int(r * 98), step_msg),
                cancel_check=cancel_check,
                process_holder=process_holder,
            )
            if rc != 0:
                raise RuntimeError(err.strip() or "FFmpeg GIF export failed.")

            if max_gif_bytes is None or options.output_path.stat().st_size <= max_gif_bytes:
                break

        emit(100, "GIF export complete.")
        return options.output_path


def execute_export(
    options: ExportOptions,
    ffmpeg_path: Optional[str] = None,
    on_progress: Optional[callable] = None,
    cancel_check: Optional[callable] = None,
    process_holder: Optional[list[subprocess.Popen]] = None,
) -> Path:
    """Perform the full export synchronously (called by ExportWorker or tests)."""
    if ffmpeg_path is None:
        ffmpeg_path, _ = find_ffmpeg_binaries()
    if not ffmpeg_path:
        raise FileNotFoundError("ffmpeg executable was not found.")

    options.output_path.parent.mkdir(parents=True, exist_ok=True)

    def emit(pct: int, msg: str) -> None:
        if on_progress is not None:
            on_progress(pct, msg)

    if options.meme_composition is not None:
        return _execute_meme_export(
            options=options,
            ffmpeg_path=ffmpeg_path,
            emit=emit,
            cancel_check=cancel_check,
            process_holder=process_holder,
        )

    duration_sec = max(0.1, options.end_sec - options.start_sec)
    normalized_crop = normalize_crop_rect(
        options.crop,
        options.video_info.width,
        options.video_info.height,
    )
    effective_width = (
        normalized_crop.width if normalized_crop is not None else options.video_info.width
    )
    effective_height = (
        normalized_crop.height if normalized_crop is not None else options.video_info.height
    )

    if options.output_format == "mp4":
        if options.target_size_mb is None:
            emit(0, "Encoding MP4 (High Quality)...")
            cmd = build_mp4_quality_cmd(
                ffmpeg_path=ffmpeg_path,
                input_path=options.input_path,
                output_path=options.output_path,
                start_sec=options.start_sec,
                duration_sec=duration_sec,
                has_audio=options.video_info.has_audio,
                mute_audio=options.mute_audio,
                scale_height=options.max_height,
                crop=normalized_crop,
            )
            rc, err = run_ffmpeg_with_progress(
                cmd,
                duration_sec,
                on_progress=lambda r: emit(int(r * 98), "Encoding MP4 (High Quality)..."),
                cancel_check=cancel_check,
                process_holder=process_holder,
            )
            if rc != 0:
                raise RuntimeError(err.strip() or "FFmpeg MP4 export failed.")
            emit(100, "Export complete.")
            return options.output_path

        # Size-targeted 2-pass MP4 compression
        safety_factor = 0.93
        max_allowed_bytes = int(options.target_size_mb * 1_000_000)

        with tempfile.TemporaryDirectory(prefix="videdit_pass_") as tmp_dir:
            passlog_prefix = Path(tmp_dir) / "ffmpeg2pass"

            for attempt in range(2):
                plan = calculate_mp4_encoding_plan(
                    duration_sec=duration_sec,
                    target_mb=options.target_size_mb,
                    has_audio=options.video_info.has_audio,
                    mute_audio=options.mute_audio,
                    source_height=effective_height,
                    max_height=options.max_height,
                    auto_scale=options.auto_scale_for_target,
                    safety_factor=safety_factor,
                )
                pass1_cmd, pass2_cmd = build_mp4_two_pass_cmds(
                    ffmpeg_path=ffmpeg_path,
                    input_path=options.input_path,
                    output_path=options.output_path,
                    start_sec=options.start_sec,
                    duration_sec=duration_sec,
                    plan=plan,
                    passlog_prefix=passlog_prefix,
                    crop=normalized_crop,
                )

                label_suffix = " (tightening fit)" if attempt > 0 else ""
                emit(0, f"Pass 1/2: Analyzing video{label_suffix}...")
                rc1, err1 = run_ffmpeg_with_progress(
                    pass1_cmd,
                    duration_sec,
                    on_progress=lambda r: emit(int(r * 45), f"Pass 1/2: Analyzing video{label_suffix}..."),
                    cancel_check=cancel_check,
                    process_holder=process_holder,
                )
                if rc1 != 0:
                    raise RuntimeError(err1.strip() or "FFmpeg Pass 1 failed.")

                emit(45, f"Pass 2/2: Compressing to {options.target_size_mb:g} MB{label_suffix}...")
                rc2, err2 = run_ffmpeg_with_progress(
                    pass2_cmd,
                    duration_sec,
                    on_progress=lambda r: emit(
                        45 + int(r * 53),
                        f"Pass 2/2: Compressing to {options.target_size_mb:g} MB{label_suffix}...",
                    ),
                    cancel_check=cancel_check,
                    process_holder=process_holder,
                )
                if rc2 != 0:
                    raise RuntimeError(err2.strip() or "FFmpeg Pass 2 failed.")

                if options.output_path.exists() and options.output_path.stat().st_size <= max_allowed_bytes:
                    break
                # Retry once with tighter safety factor if muxer overshot target on very short clips.
                safety_factor = 0.80
                _cleanup_passlogs(passlog_prefix)

        emit(100, "Export complete.")
        return options.output_path

    # GIF export with optional auto-fit size ladder
    width_ladder = [options.gif_max_width, 480, 360, 240]
    fps_ladder = [options.gif_fps, 15, 12, 10]

    candidates: list[tuple[Optional[int], int]] = [(options.gif_max_width, options.gif_fps)]
    if options.gif_target_size_mb is not None:
        for w in width_ladder:
            for f in fps_ladder:
                if (w is None or options.gif_max_width is None or w <= options.gif_max_width) and f <= options.gif_fps:
                    pair = (w, f)
                    if pair not in candidates:
                        candidates.append(pair)

    max_gif_bytes = int(options.gif_target_size_mb * 1_000_000) if options.gif_target_size_mb else None

    for idx, (cand_width, cand_fps) in enumerate(candidates):
        step_msg = (
            f"Generating GIF ({cand_width or 'orig'}px @ {cand_fps}fps)..."
            if idx == 0
            else f"Refitting GIF under {options.gif_target_size_mb:g} MB ({cand_width or 'orig'}px @ {cand_fps}fps)..."
        )
        emit(0, step_msg)
        cmd = build_gif_cmd(
            ffmpeg_path=ffmpeg_path,
            input_path=options.input_path,
            output_path=options.output_path,
            start_sec=options.start_sec,
            duration_sec=duration_sec,
            fps=cand_fps,
            max_width=cand_width,
            crop=normalized_crop,
            caption=options.gif_caption,
            source_width=effective_width,
            source_height=effective_height,
        )
        rc, err = run_ffmpeg_with_progress(
            cmd,
            duration_sec,
            on_progress=lambda r: emit(int(r * 98), step_msg),
            cancel_check=cancel_check,
            process_holder=process_holder,
        )
        if rc != 0:
            raise RuntimeError(err.strip() or "FFmpeg GIF export failed.")

        if max_gif_bytes is None or options.output_path.stat().st_size <= max_gif_bytes:
            break

    emit(100, "GIF export complete.")
    return options.output_path


class ExportWorker(QThread):
    """QThread wrapper around execute_export for non-blocking UI updates."""

    progress_changed = Signal(int, str)
    finished_success = Signal(str, int)
    finished_error = Signal(str)

    def __init__(self, options: ExportOptions) -> None:
        super().__init__()
        self.options = options
        self._cancelled = False
        self._active_procs: list[subprocess.Popen] = []

    def cancel(self) -> None:
        """Signal cancellation and terminate any running FFmpeg child process."""
        self._cancelled = True
        for proc in list(self._active_procs):
            try:
                proc.terminate()
            except OSError:
                pass

    def run(self) -> None:
        try:
            out_path = execute_export(
                options=self.options,
                on_progress=self.progress_changed.emit,
                cancel_check=lambda: self._cancelled,
                process_holder=self._active_procs,
            )
            if self._cancelled:
                self.finished_error.emit("Export cancelled.")
                return
            size_bytes = out_path.stat().st_size if out_path.exists() else 0
            self.finished_success.emit(str(out_path), size_bytes)
        except Exception as exc:
            if self._cancelled:
                self.finished_error.emit("Export cancelled.")
            else:
                self.finished_error.emit(str(exc))
