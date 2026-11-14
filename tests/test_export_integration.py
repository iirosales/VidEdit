"""End-to-end integration tests running real FFmpeg exports on synthetic clips."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from videdit.exporter import ExportOptions, execute_export
from videdit.ffmpeg_tools import find_ffmpeg_binaries, probe_video


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
