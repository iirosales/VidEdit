"""End-to-end integration tests running real FFmpeg exports on synthetic clips."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from videdit.exporter import ExportOptions, execute_export
from videdit.ffmpeg_tools import CropRect, GifCaption, find_ffmpeg_binaries, probe_video


@pytest.fixture()
def sample_video(tmp_path: Path) -> Path:
    """Generate a synthetic 6-second 1280x720 H.264 + AAC test video."""
    ffmpeg_path, _ = find_ffmpeg_binaries()
    if not ffmpeg_path:
        pytest.skip("ffmpeg not available")

    out = tmp_path / "sample_source.mp4"
    cmd = [
        ffmpeg_path,
        "-y",
        "-f",
        "lavfi",
        "-i",
        "testsrc2=size=1280x720:rate=30:duration=6",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=440:sample_rate=44100:duration=6",
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        str(out),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stderr
    return out


def test_integration_trim_mp4_high_quality(sample_video: Path, tmp_path: Path) -> None:
    info = probe_video(sample_video)
    out = tmp_path / "trimmed.mp4"

    opts = ExportOptions(
        input_path=sample_video,
        output_path=out,
        start_sec=1.5,
        end_sec=4.0,
        output_format="mp4",
        video_info=info,
        target_size_mb=None,
    )
    result = execute_export(opts)
    assert result.exists()
    out_info = probe_video(result)
    assert out_info.duration_sec == pytest.approx(2.5, abs=0.25)
    assert out_info.has_audio is True


def test_integration_compress_mp4_under_target_size(sample_video: Path, tmp_path: Path) -> None:
    info = probe_video(sample_video)
    out = tmp_path / "compressed_1mb.mp4"

    opts = ExportOptions(
        input_path=sample_video,
        output_path=out,
        start_sec=0.0,
        end_sec=5.0,
        output_format="mp4",
        video_info=info,
        target_size_mb=1.0,
    )
    result = execute_export(opts)
    assert result.exists()
    assert result.stat().st_size <= 1_000_000
    out_info = probe_video(result)
    assert out_info.duration_sec == pytest.approx(5.0, abs=0.25)


def test_integration_export_gif(sample_video: Path, tmp_path: Path) -> None:
    info = probe_video(sample_video)
    out = tmp_path / "clip.gif"

    opts = ExportOptions(
        input_path=sample_video,
        output_path=out,
        start_sec=1.0,
        end_sec=3.0,
        output_format="gif",
        video_info=info,
        gif_fps=15,
        gif_max_width=480,
    )
    result = execute_export(opts)
    assert result.exists()
    assert result.read_bytes()[:6] in (b"GIF87a", b"GIF89a")
    out_info = probe_video(result)
    assert out_info.width == 480


def test_integration_export_cropped_mp4_and_gif(sample_video: Path, tmp_path: Path) -> None:
    info = probe_video(sample_video)

    mp4_out = tmp_path / "cropped.mp4"
    execute_export(
        ExportOptions(
            input_path=sample_video,
            output_path=mp4_out,
            start_sec=0.5,
            end_sec=2.0,
            output_format="mp4",
            video_info=info,
            crop=CropRect(x=101, y=51, width=401, height=301),
            target_size_mb=None,
        )
    )
    mp4_info = probe_video(mp4_out)
    assert mp4_info.width == 400
    assert mp4_info.height == 300

    gif_out = tmp_path / "cropped.gif"
    execute_export(
        ExportOptions(
            input_path=sample_video,
            output_path=gif_out,
            start_sec=0.5,
            end_sec=2.0,
            output_format="gif",
            video_info=info,
            crop=CropRect(x=120, y=80, width=320, height=240),
            gif_fps=12,
            gif_max_width=None,
        )
    )
    gif_info = probe_video(gif_out)
    assert gif_info.width == 320
    assert gif_info.height == 240


def test_integration_export_gif_with_meme_captions(tmp_path: Path) -> None:
    ffmpeg_path, _ = find_ffmpeg_binaries()
    if not ffmpeg_path:
        pytest.skip("ffmpeg not available")

    # Neutral gray source makes rendered white/black Impact captions unmistakable.
    src = tmp_path / "gray_src.mp4"
    subprocess.run(
        [
            ffmpeg_path,
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=gray:size=640x360:rate=15:duration=1.5",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            str(src),
        ],
        capture_output=True,
        check=True,
    )
    info = probe_video(src)
    out = tmp_path / "meme.gif"

    result = execute_export(
        ExportOptions(
            input_path=src,
            output_path=out,
            start_sec=0.0,
            end_sec=1.0,
            output_format="gif",
            video_info=info,
            gif_fps=12,
            gif_max_width=480,
            gif_caption=GifCaption(
                top_text="WHEN THE BUILD: 100%",
                bottom_text="IT'S ALIVE; [OK]",
                size_scale=1.0,
                uppercase=True,
            ),
        )
    )
    assert result.exists()
    assert result.read_bytes()[:6] in (b"GIF87a", b"GIF89a")
    out_info = probe_video(result)
    assert out_info.width == 480
    assert out_info.height == 270

    # Extract raw RGB24 of frame 0 to verify bright white + black outline pixels exist in top/bottom bands.
    frame_proc = subprocess.run(
        [
            ffmpeg_path,
            "-v",
            "error",
            "-i",
            str(result),
            "-frames:v",
            "1",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "pipe:1",
        ],
        capture_output=True,
        check=True,
    )
    raw = frame_proc.stdout
    assert len(raw) == 480 * 270 * 3
    row_stride = 480 * 3

    def band_min_max_luma(y_start: int, y_end: int) -> tuple[int, int]:
        band = raw[y_start * row_stride : y_end * row_stride]
        # Sample every pixel's first channel (grayscale/white/black/gray all have R==G==B).
        vals = band[0::3]
        return min(vals), max(vals)

    top_min, top_max = band_min_max_luma(5, 65)
    mid_min, mid_max = band_min_max_luma(110, 160)
    bot_min, bot_max = band_min_max_luma(205, 265)

    # Top and bottom bands contain both black outline (< 40) and white fill (> 220).
    assert top_min < 40 and top_max > 220
    assert bot_min < 40 and bot_max > 220
    # Middle band remains untouched gray (~128).
    assert 90 < mid_min and mid_max < 170
