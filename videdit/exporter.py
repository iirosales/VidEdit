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

from videdit.ffmpeg_tools import (
    CropRect,
    VideoInfo,
    build_gif_cmd,
    build_mp4_quality_cmd,
    build_mp4_two_pass_cmds,
    calculate_mp4_encoding_plan,
    find_ffmpeg_binaries,
    normalize_crop_rect,
    parse_ffmpeg_progress_line,
)


@dataclass(frozen=True)
class ExportOptions:
    """User-selected export parameters."""

    input_path: Path
    output_path: Path
    start_sec: float
    end_sec: float
    output_format: Literal["mp4", "gif"]
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

    duration_sec = max(0.1, options.end_sec - options.start_sec)
    normalized_crop = normalize_crop_rect(
        options.crop,
        options.video_info.width,
        options.video_info.height,
    )
    effective_height = (
        normalized_crop.height if normalized_crop is not None else options.video_info.height
    )
    options.output_path.parent.mkdir(parents=True, exist_ok=True)

    def emit(pct: int, msg: str) -> None:
        if on_progress is not None:
            on_progress(pct, msg)

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
