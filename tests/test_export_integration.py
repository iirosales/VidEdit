"""End-to-end integration tests running real FFmpeg exports on synthetic clips."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication

from videdit.exporter import ExportOptions, execute_export
from videdit.ffmpeg_tools import CropRect, GifCaption, find_ffmpeg_binaries, probe_video
from videdit.meme_project import (
    BaseLayer,
    MediaOverlayLayer,
    MemeComposition,
    NormalizedRect,
    TextOverlayLayer,
    compute_top_bar_layout,
)


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


def test_integration_meme_image_overlay_circle_crop_and_topbar_png(tmp_path: Path) -> None:
    ffmpeg_path, _ = find_ffmpeg_binaries()
    if not ffmpeg_path:
        pytest.skip("ffmpeg not available")
    app = QApplication.instance() or QApplication([])

    base_path = tmp_path / "meme_base.png"
    base_img = QImage(640, 480, QImage.Format.Format_RGBA8888)
    base_img.fill(QColor(20, 40, 180))  # Deep blue background
    assert base_img.save(str(base_path), "PNG")

    face_path = tmp_path / "friend_face.png"
    face_img = QImage(200, 200, QImage.Format.Format_RGBA8888)
    face_img.fill(QColor(230, 50, 40))  # Bright red face patch
    assert face_img.save(str(face_path), "PNG")

    comp = MemeComposition(
        base=BaseLayer(
            path=base_path,
            kind="image",
            width=640,
            height=480,
            duration_sec=0.0,
            has_audio=False,
            top_bar_text="Me and the boys",
        ),
        overlays=(
            MediaOverlayLayer(
                id="face1",
                name="friend_face.png",
                path=face_path,
                kind="image",
                source_width=200,
                source_height=200,
                duration_sec=0.0,
                has_audio=False,
                rect=NormalizedRect(0.25, 0.25, 0.5, 0.5),
                source_crop=CropRect(20, 20, 160, 160),
                shape="circle",
            ),
            TextOverlayLayer(
                id="txt1",
                text="BOTTOM",
                rect=NormalizedRect(0.2, 0.8, 0.6, 0.15),
            ),
        ),
    )

    out_png = tmp_path / "meme_out.png"
    dummy_info = probe_video(base_path)
    result = execute_export(
        ExportOptions(
            input_path=base_path,
            output_path=out_png,
            start_sec=0.0,
            end_sec=1.0,
            output_format="png",
            video_info=dummy_info,
            meme_composition=comp,
        )
    )
    assert result.exists()
    out_img = QImage(str(result))
    bar_h, _, _ = compute_top_bar_layout(640, "Me and the boys")
    assert out_img.width() == 640
    assert out_img.height() == 480 + bar_h

    # Top white bar corner pixel is white.
    assert out_img.pixelColor(8, 8).red() > 240
    assert out_img.pixelColor(8, 8).green() > 240

    # Center of circle face overlay is red (from friend_face.png).
    center_px = out_img.pixelColor(320, bar_h + 240)
    assert center_px.red() > 190 and center_px.blue() < 80

    # Corner outside circle mask inside overlay bounding box remains base blue!
    corner_ov_px = out_img.pixelColor(164, bar_h + 124)
    assert corner_ov_px.blue() > 140 and corner_ov_px.red() < 60
    app.processEvents()


def test_integration_meme_video_with_face_overlay_mp4_and_gif(
    sample_video: Path, tmp_path: Path
) -> None:
    app = QApplication.instance() or QApplication([])
    info = probe_video(sample_video)

    face_path = tmp_path / "face.png"
    face_img = QImage(160, 160, QImage.Format.Format_RGBA8888)
    face_img.fill(QColor(40, 220, 60))
    assert face_img.save(str(face_path), "PNG")

    comp = MemeComposition(
        base=BaseLayer(
            path=sample_video,
            kind="video",
            width=info.width,
            height=info.height,
            duration_sec=info.duration_sec,
            has_audio=info.has_audio,
            fps=info.fps,
            start_sec=0.5,
            end_sec=2.5,
        ),
        overlays=(
            MediaOverlayLayer(
                id="face1",
                name="face.png",
                path=face_path,
                kind="image",
                source_width=160,
                source_height=160,
                duration_sec=0.0,
                has_audio=False,
                rect=NormalizedRect(0.35, 0.35, 0.3, 0.3),
                shape="circle",
                start_sec=0.2,
                end_sec=1.8,
            ),
            TextOverlayLayer(
                id="txt1",
                text="WHO DID THIS",
                rect=NormalizedRect(0.1, 0.05, 0.8, 0.18),
            ),
        ),
    )

    mp4_out = tmp_path / "meme_vid.mp4"
    res_mp4 = execute_export(
        ExportOptions(
            input_path=sample_video,
            output_path=mp4_out,
            start_sec=0.5,
            end_sec=2.5,
            output_format="mp4",
            video_info=info,
            meme_composition=comp,
        )
    )
    assert res_mp4.exists()
    mp4_info = probe_video(res_mp4)
    assert mp4_info.duration_sec == pytest.approx(2.0, abs=0.25)
    assert mp4_info.has_audio is True

    gif_out = tmp_path / "meme_vid.gif"
    res_gif = execute_export(
        ExportOptions(
            input_path=sample_video,
            output_path=gif_out,
            start_sec=0.5,
            end_sec=2.0,
            output_format="gif",
            video_info=info,
            gif_fps=12,
            gif_max_width=480,
            meme_composition=comp,
        )
    )
    assert res_gif.exists()
    assert res_gif.read_bytes()[:6] in (b"GIF87a", b"GIF89a")
    out_gif_info = probe_video(res_gif)
    assert out_gif_info.width == 480
    app.processEvents()


def test_integration_gif_base_with_cropped_circle_chromakey_overlay(tmp_path: Path) -> None:
    """Regression: cropped + circle-masked + chroma-keyed image overlay on a GIF preserves chroma key."""
    ffmpeg_path, _ = find_ffmpeg_binaries()
    if not ffmpeg_path:
        pytest.skip("ffmpeg not available")
    app = QApplication.instance() or QApplication([])

    # Create a solid blue animated GIF base (480x360, 1.5s)
    base_gif = tmp_path / "base.gif"
    subprocess.run(
        [
            ffmpeg_path,
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=#1830C8:size=480x360:rate=10:duration=1.5",
            str(base_gif),
        ],
        capture_output=True,
        check=True,
    )
    base_info = probe_video(base_gif)

    # Create an overlay image: bright green (#00FF00) background with a red (#E62828) face square in the middle
    ov_path = tmp_path / "greenscreen_face.png"
    ov_img = QImage(240, 240, QImage.Format.Format_RGBA8888)
    ov_img.fill(QColor(0, 255, 0))
    for y in range(80, 160):
        for x in range(80, 160):
            ov_img.setPixelColor(x, y, QColor(230, 40, 40))
    assert ov_img.save(str(ov_path), "PNG")

    comp = MemeComposition(
        base=BaseLayer(
            path=base_gif,
            kind="gif",
            width=base_info.width,
            height=base_info.height,
            duration_sec=base_info.duration_sec or 1.5,
            has_audio=False,
            fps=base_info.fps,
            start_sec=0.0,
            end_sec=1.5,
        ),
        overlays=(
            MediaOverlayLayer(
                id="gs1",
                name="greenscreen_face.png",
                path=ov_path,
                kind="image",
                source_width=240,
                source_height=240,
                duration_sec=0.0,
                has_audio=False,
                rect=NormalizedRect(0.25, 0.1667, 0.5, 0.6667),  # 240x240 centered in 480x360
                source_crop=CropRect(20, 20, 200, 200),
                shape="circle",
                chroma_key_color="#00FF00",
                chroma_similarity=0.25,
            ),
        ),
    )

    out_gif = tmp_path / "exported_chromakey.gif"
    res = execute_export(
        ExportOptions(
            input_path=base_gif,
            output_path=out_gif,
            start_sec=0.0,
            end_sec=1.5,
            output_format="gif",
            video_info=base_info,
            gif_fps=10,
            gif_max_width=480,
            meme_composition=comp,
        )
    )
    assert res.exists()
    frame0 = QImage(str(res))
    assert frame0.width() == 480
    assert frame0.height() == 360

    # Center of overlay -> red face remains visible.
    center = frame0.pixelColor(240, 180)
    assert center.red() > 180 and center.green() < 90

    # Green screen ring inside the circle (e.g., ~65px left of center) -> keyed out, showing base blue!
    ring_pt = frame0.pixelColor(175, 180)
    assert ring_pt.blue() > 150 and ring_pt.green() < 90

    app.processEvents()
