"""Unit tests for FFmpeg probing, bitrate planning, command building, and progress parsing."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from videdit.ffmpeg_tools import (
    CropRect,
    GifCaption,
    build_gif_cmd,
    build_mp4_quality_cmd,
    build_mp4_two_pass_cmds,
    calculate_mp4_encoding_plan,
    escape_ffmpeg_drawtext,
    normalize_crop_rect,
    parse_ffmpeg_progress_line,
    parse_ffprobe_json,
    wrap_meme_caption,
)


def test_parse_ffprobe_json_extracts_metadata() -> None:
    payload = {
        "format": {"duration": "12.500", "size": "5242880"},
        "streams": [
            {
                "codec_type": "video",
                "codec_name": "h264",
                "width": 1920,
                "height": 1080,
                "avg_frame_rate": "60000/1001",
            },
            {
                "codec_type": "audio",
                "codec_name": "aac",
            },
        ],
    }
    info = parse_ffprobe_json(json.dumps(payload), Path("clip.mp4"))
    assert info.duration_sec == pytest.approx(12.5)
    assert info.width == 1920
    assert info.height == 1080
    assert info.fps == pytest.approx(59.94, rel=1e-2)
    assert info.has_audio is True
    assert info.file_size_bytes == 5242880
    assert info.video_codec == "h264"


def test_parse_ffprobe_json_raises_without_video_stream() -> None:
    payload = {"format": {"duration": "3.0"}, "streams": [{"codec_type": "audio"}]}
    with pytest.raises(ValueError, match="No video stream"):
        parse_ffprobe_json(json.dumps(payload), Path("audio_only.mp3"))


@pytest.mark.parametrize("target_mb", [10.0, 15.0, 20.0])
@pytest.mark.parametrize("duration_sec", [5.0, 30.0, 90.0])
def test_calculate_mp4_plan_fits_within_target_budget(target_mb: float, duration_sec: float) -> None:
    plan = calculate_mp4_encoding_plan(
        duration_sec=duration_sec,
        target_mb=target_mb,
        has_audio=True,
        mute_audio=False,
        source_height=1080,
    )
    estimated_bytes = ((plan.video_kbps + plan.audio_kbps) * 1000.0 / 8.0) * duration_sec
    assert estimated_bytes <= target_mb * 1_000_000
    assert plan.video_kbps >= 36
    assert plan.audio_kbps > 0


def test_calculate_mp4_plan_auto_scales_low_bitrate_1080p() -> None:
    # 120s into 10 MB -> tight bitrate -> should step down from 1080p
    plan = calculate_mp4_encoding_plan(
        duration_sec=120.0,
        target_mb=10.0,
        has_audio=True,
        mute_audio=False,
        source_height=1080,
        max_height=None,
        auto_scale=True,
    )
    assert plan.scale_height in (720, 480)


def test_calculate_mp4_plan_respects_mute_audio() -> None:
    plan = calculate_mp4_encoding_plan(
        duration_sec=20.0,
        target_mb=10.0,
        has_audio=True,
        mute_audio=True,
        source_height=720,
    )
    assert plan.audio_kbps == 0


def test_build_mp4_two_pass_cmds_structure() -> None:
    plan = calculate_mp4_encoding_plan(
        duration_sec=10.0,
        target_mb=10.0,
        has_audio=True,
        mute_audio=False,
        source_height=1080,
    )
    p1, p2 = build_mp4_two_pass_cmds(
        ffmpeg_path="ffmpeg",
        input_path=Path("in.mp4"),
        output_path=Path("out.mp4"),
        start_sec=1.5,
        duration_sec=10.0,
        plan=plan,
        passlog_prefix=Path("passlog"),
    )
    assert "-pass" in p1 and p1[p1.index("-pass") + 1] == "1"
    assert "-an" in p1
    assert p1[-1] == os.devnull

    assert "-pass" in p2 and p2[p2.index("-pass") + 1] == "2"
    assert "-movflags" in p2 and "+faststart" in p2
    assert p2[-1] == "out.mp4"


def test_build_mp4_quality_cmd_muted() -> None:
    cmd = build_mp4_quality_cmd(
        ffmpeg_path="ffmpeg",
        input_path=Path("in.mp4"),
        output_path=Path("out.mp4"),
        start_sec=0.0,
        duration_sec=4.0,
        has_audio=True,
        mute_audio=True,
    )
    assert "-an" in cmd
    assert "-crf" in cmd


def test_build_gif_cmd_palettegen_filter() -> None:
    cmd = build_gif_cmd(
        ffmpeg_path="ffmpeg",
        input_path=Path("in.mp4"),
        output_path=Path("out.gif"),
        start_sec=2.0,
        duration_sec=3.5,
        fps=15,
        max_width=480,
    )
    fc = cmd[cmd.index("-filter_complex") + 1]
    assert "fps=15" in fc
    assert "min(iw,480)" in fc
    assert "palettegen=stats_mode=diff" in fc
    assert "paletteuse=" in fc
    assert cmd[-1] == "out.gif"


def test_parse_ffmpeg_progress_line() -> None:
    assert parse_ffmpeg_progress_line("out_time_us=2500000\n", 10.0) == pytest.approx(0.25)
    assert parse_ffmpeg_progress_line("out_time_us=15000000\n", 10.0) == pytest.approx(1.0)
    assert parse_ffmpeg_progress_line("progress=end\n", 10.0) == pytest.approx(1.0)
    assert parse_ffmpeg_progress_line("frame=12\n", 10.0) is None


def test_normalize_crop_rect_and_ffmpeg_filters() -> None:
    assert normalize_crop_rect(CropRect(0, 0, 1280, 720), 1280, 720) is None

    odd_crop = normalize_crop_rect(CropRect(101, 51, 303, 205), 1280, 720)
    assert odd_crop == CropRect(100, 50, 302, 204)

    mp4_cmd = build_mp4_quality_cmd(
        ffmpeg_path="ffmpeg",
        input_path=Path("in.mp4"),
        output_path=Path("out.mp4"),
        start_sec=0.0,
        duration_sec=2.0,
        has_audio=True,
        mute_audio=False,
        crop=odd_crop,
    )
    vf = mp4_cmd[mp4_cmd.index("-vf") + 1]
    assert vf.startswith("crop=302:204:100:50,")

    gif_cmd = build_gif_cmd(
        ffmpeg_path="ffmpeg",
        input_path=Path("in.mp4"),
        output_path=Path("out.gif"),
        start_sec=0.0,
        duration_sec=2.0,
        fps=15,
        max_width=None,
        crop=odd_crop,
    )
    fc = gif_cmd[gif_cmd.index("-filter_complex") + 1]
    assert fc.startswith("[0:v]crop=302:204:100:50,fps=15,")


def test_meme_caption_wrapping_and_escaping() -> None:
    assert not GifCaption().is_active()
    assert not GifCaption(top_text="   ", bottom_text="\t").is_active()
    assert GifCaption(top_text="hello").is_active()

    lines = wrap_meme_caption(
        "when the code compiles on the very first try without errors",
        max_width_px=360,
        font_px=30.0,
        uppercase=True,
    )
    assert len(lines) >= 2
    assert all(line == line.upper() for line in lines)

    mixed = wrap_meme_caption("Keep Case Here", max_width_px=480, font_px=24.0, uppercase=False)
    assert mixed == ["Keep Case Here"]

    long_token = wrap_meme_caption("A" * 60, max_width_px=240, font_px=24.0, uppercase=True)
    assert len(long_token) >= 2
    assert "".join(long_token) == "A" * 60

    escaped = escape_ffmpeg_drawtext("IT'S: 100% [OK]; A=B\\C")
    assert "expansion" not in escaped
    assert "\\:" in escaped
    assert "\\=" in escaped


def test_build_gif_cmd_with_meme_captions() -> None:
    caption = GifCaption(
        top_text="Top line: 100%",
        bottom_text="Bottom's [done]",
        size_scale=1.28,
        uppercase=True,
    )
    cmd = build_gif_cmd(
        ffmpeg_path="ffmpeg",
        input_path=Path("in.mp4"),
        output_path=Path("out.gif"),
        start_sec=0.0,
        duration_sec=2.0,
        fps=15,
        max_width=480,
        caption=caption,
        source_width=1280,
        source_height=720,
    )
    fc = cmd[cmd.index("-filter_complex") + 1]
    assert fc.count("drawtext=") == 2
    assert "impact" in fc.lower()
    assert "fontcolor=white" in fc
    assert "bordercolor=black" in fc
    assert "expansion=none" in fc
    assert "x='(w-text_w)/2'" in fc
    # Captions must run after scale and before palette split.
    assert fc.index("scale='min(iw,480)'") < fc.index("drawtext=") < fc.index("split[s0][s1]")
