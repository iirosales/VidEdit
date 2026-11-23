"""YouTube MP4 download backend powered by yt-dlp and VidEdit's FFmpeg discovery."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

from PySide6.QtCore import QThread, Signal

from videdit.ffmpeg_tools import find_ffmpeg_binaries

try:
    import yt_dlp
except ImportError:  # pragma: no cover - handled gracefully when yt-dlp is absent
    yt_dlp = None


QUALITY_OPTIONS: dict[str, Optional[int]] = {
    "Best Available (up to 4K+)": None,
    "2160p (4K)": 2160,
    "1440p (2K)": 1440,
    "1080p (Full HD)": 1080,
    "720p (HD)": 720,
}

DEFAULT_YOUTUBE_DIR = Path.home() / "Downloads" / "YouTube_MP4"


class DownloadCancelled(Exception):
    """Raised inside yt-dlp hooks when the user cancels an active download."""


@dataclass(frozen=True)
class DownloadResult:
    """Outcome summary for a batch of YouTube MP4 downloads."""

    saved_paths: list[Path]
    failed_count: int
    cancelled: bool
    errors: list[str]


def check_dependencies() -> tuple[bool, Optional[str]]:
    """Return (has_yt_dlp, ffmpeg_path_or_none)."""
    ffmpeg_path, _ = find_ffmpeg_binaries()
    return yt_dlp is not None, ffmpeg_path


def parse_urls(raw_text: str) -> list[str]:
    """Extract unique, non-empty URLs from multiline or whitespace-separated text."""
    seen: set[str] = set()
    urls: list[str] = []
    for line in raw_text.splitlines():
        for token in line.strip().split():
            cleaned = token.strip()
            if cleaned and cleaned not in seen:
                seen.add(cleaned)
                urls.append(cleaned)
    return urls


def build_format_selector(max_height: Optional[int], has_ffmpeg: bool) -> str:
    """Build a yt-dlp format selector favoring H.264/AAC MP4-compatible streams."""
    if not has_ffmpeg:
        if max_height:
            return f"best[ext=mp4][height<={max_height}]/best[height<={max_height}]/best"
        return "best[ext=mp4]/best"

    if max_height:
        return (
            f"bestvideo[height<={max_height}][vcodec^=avc1]+bestaudio[ext=m4a]/"
            f"bestvideo[height<={max_height}][ext=mp4]+bestaudio[ext=m4a]/"
            f"bestvideo[height<={max_height}]+bestaudio/"
            f"best[height<={max_height}]/best"
        )

    return (
        "bestvideo[vcodec^=avc1]+bestaudio[ext=m4a]/"
        "bestvideo[ext=mp4]+bestaudio[ext=m4a]/"
        "bestvideo+bestaudio/best"
    )


def _collect_entry_paths(entry: Any, ydl: Any, out: list[Path], seen: set[str]) -> None:
    """Extract final file paths from a yt-dlp info dictionary or playlist entry."""
    if not isinstance(entry, dict):
        return

    nested_entries = entry.get("entries")
    if nested_entries is not None:
        for child in nested_entries:
            _collect_entry_paths(child, ydl, out, seen)
        return

    candidates: list[str] = []
    req = entry.get("requested_downloads")
    if isinstance(req, list):
        for item in req:
            if isinstance(item, dict) and item.get("filepath"):
                candidates.append(str(item["filepath"]))

    for key in ("filepath", "_filename"):
        if entry.get(key):
            candidates.append(str(entry[key]))

    if not candidates and hasattr(ydl, "prepare_filename"):
        try:
            prepared = Path(str(ydl.prepare_filename(entry)))
            mp4_candidate = prepared.with_suffix(".mp4")
            if mp4_candidate.is_file():
                candidates.append(str(mp4_candidate))
            elif prepared.is_file():
                candidates.append(str(prepared))
        except Exception:
            pass

    for raw in candidates:
        path = Path(raw)
        key = str(path)
        if key not in seen and path.is_file():
            seen.add(key)
            out.append(path)


def download_videos(
    urls: list[str],
    output_dir: Path,
    max_height: Optional[int] = None,
    allow_playlists: bool = False,
    cancel_event: Optional[threading.Event] = None,
    on_log: Optional[Callable[[str], None]] = None,
    on_progress: Optional[Callable[[int, str], None]] = None,
    on_file_saved: Optional[Callable[[Path], None]] = None,
) -> DownloadResult:
    """Download one or more YouTube URLs as MP4 files using yt-dlp."""
    has_yt_dlp, ffmpeg_path = check_dependencies()
    if not has_yt_dlp:
        raise RuntimeError(
            "yt-dlp is not installed. Run VidEdit.bat or install with: pip install yt-dlp"
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    has_ffmpeg = ffmpeg_path is not None

    def log(msg: str) -> None:
        if on_log is not None:
            on_log(msg)

    def report_progress(pct: int, status: str) -> None:
        if on_progress is not None:
            on_progress(max(0, min(100, pct)), status)

    if not has_ffmpeg:
        log("Warning: FFmpeg was not found; downloads may be limited to single-stream MP4.")

    format_selector = build_format_selector(max_height, has_ffmpeg)
    total = len(urls)
    saved_paths: list[Path] = []
    seen_paths: set[str] = set()
    errors: list[str] = []
    failed = 0
    cancelled = False

    for idx, url in enumerate(urls, start=1):
        if cancel_event is not None and cancel_event.is_set():
            cancelled = True
            log("Download cancelled.")
            break

        log(f"[{idx}/{total}] Fetching: {url}")
        report_progress(0, f"[{idx}/{total}] Fetching info...")

        def progress_hook(d: dict[str, Any]) -> None:
            if cancel_event is not None and cancel_event.is_set():
                raise DownloadCancelled()

            status = d.get("status")
            if status == "downloading":
                downloaded = d.get("downloaded_bytes") or 0
                total_bytes = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                pct = int((downloaded / total_bytes) * 100.0) if total_bytes > 0 else 0
                speed = str(d.get("_speed_str", "") or "").strip()
                eta = str(d.get("_eta_str", "") or "").strip()
                parts = [f"[{idx}/{total}] {pct}%"]
                if speed:
                    parts.append(speed)
                if eta:
                    parts.append(f"ETA {eta}")
                report_progress(pct, " · ".join(parts))
            elif status == "finished":
                report_progress(99, f"[{idx}/{total}] Merging MP4...")

        def postprocessor_hook(d: dict[str, Any]) -> None:
            if cancel_event is not None and cancel_event.is_set():
                raise DownloadCancelled()
            if d.get("status") == "finished":
                info_dict = d.get("info_dict") or {}
                raw_path = info_dict.get("filepath") or d.get("filepath")
                if raw_path:
                    path = Path(str(raw_path))
                    key = str(path)
                    if key not in seen_paths and path.is_file():
                        seen_paths.add(key)
                        saved_paths.append(path)
                        if on_file_saved is not None:
                            on_file_saved(path)

        ydl_opts: dict[str, Any] = {
            "format": format_selector,
            "outtmpl": str(output_dir / "%(title).180B [%(id)s].%(ext)s"),
            "noplaylist": not allow_playlists,
            "progress_hooks": [progress_hook],
            "postprocessor_hooks": [postprocessor_hook],
            "quiet": True,
            "no_warnings": True,
            "windowsfilenames": True,
            "retries": 3,
            "fragment_retries": 3,
        }
        if ffmpeg_path is not None:
            ydl_opts["ffmpeg_location"] = ffmpeg_path
            ydl_opts["merge_output_format"] = "mp4"

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:  # type: ignore[union-attr]
                info = ydl.extract_info(url, download=True)
                before_count = len(saved_paths)
                _collect_entry_paths(info, ydl, saved_paths, seen_paths)
                for newly_found in saved_paths[before_count:]:
                    if on_file_saved is not None:
                        on_file_saved(newly_found)
                title = info.get("title", url) if isinstance(info, dict) else url
                log(f"Saved: {title}")
        except DownloadCancelled:
            cancelled = True
            log("Download cancelled.")
            break
        except Exception as exc:
            failed += 1
            err_msg = str(exc).strip() or "Download failed."
            errors.append(err_msg)
            log(f"Failed ({url}): {err_msg}")

    if not cancelled and failed == 0:
        report_progress(100, f"Done ({len(saved_paths)} saved).")

    return DownloadResult(
        saved_paths=saved_paths,
        failed_count=failed,
        cancelled=cancelled,
        errors=errors,
    )


class YouTubeDownloadWorker(QThread):
    """QThread worker running yt-dlp downloads without blocking the VidEdit UI."""

    progress_changed = Signal(int, str)
    log_message = Signal(str)
    file_downloaded = Signal(str)
    finished_result = Signal(list, int, bool, list)
    dependency_error = Signal(str)

    def __init__(
        self,
        urls: list[str],
        output_dir: Path,
        max_height: Optional[int] = None,
        allow_playlists: bool = False,
    ) -> None:
        super().__init__()
        self.urls = list(urls)
        self.output_dir = output_dir
        self.max_height = max_height
        self.allow_playlists = allow_playlists
        self._cancel_event = threading.Event()

    def cancel(self) -> None:
        """Signal the running download to abort cleanly."""
        self._cancel_event.set()

    def run(self) -> None:
        try:
            result = download_videos(
                urls=self.urls,
                output_dir=self.output_dir,
                max_height=self.max_height,
                allow_playlists=self.allow_playlists,
                cancel_event=self._cancel_event,
                on_log=self.log_message.emit,
                on_progress=self.progress_changed.emit,
                on_file_saved=lambda p: self.file_downloaded.emit(str(p)),
            )
            self.finished_result.emit(
                [str(p) for p in result.saved_paths],
                result.failed_count,
                result.cancelled,
                result.errors,
            )
        except Exception as exc:
            self.dependency_error.emit(str(exc))
