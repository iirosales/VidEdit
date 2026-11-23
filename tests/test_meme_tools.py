"""Unit tests for Meme Studio data model, probing, WYSIWYG renderers, and FFmpeg commands."""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtGui import QColor, QImage

from videdit.ffmpeg_tools import CropRect, GifCaption, MemeFilterAssets, build_meme_export_cmds
from videdit.meme_project import (
    BaseLayer,
    MediaOverlayLayer,
    MemeComposition,
    NormalizedRect,
    TextOverlayLayer,
    apply_overlay_crop_rect,
    build_media_overlay_layer,
    classify_media_path,
    compute_top_bar_layout,
    probe_media_asset,
    render_classic_caption_png,
    render_shape_mask_png,
)


def test_classify_media_path() -> None:
    assert classify_media_path(Path("face.PNG")) == "image"
    assert classify_media_path(Path("meme.jpg")) == "image"
    assert classify_media_path(Path("sticker.webp")) == "image"
    assert classify_media_path(Path("react.gif")) == "gif"
    assert classify_media_path(Path("clip.mp4")) == "video"


def test_probe_media_asset_image(tmp_path: Path) -> None:
    img_path = tmp_path / "friend_face.png"
    img = QImage(320, 240, QImage.Format.Format_RGBA8888)
    img.fill(QColor("#3D7EFF"))
    assert img.save(str(img_path), "PNG")

    info = probe_media_asset(img_path)
    assert info.kind == "image"
    assert info.width == 320
    assert info.height == 240
    assert info.duration_sec == 0.0
    assert info.has_audio is False


def test_normalized_rect_and_overlay_crop() -> None:
    rect = NormalizedRect(-0.2, 0.9, 0.5, 0.5).clamped()
    assert rect.x == pytest.approx(0.0)
    assert rect.y == pytest.approx(0.5)
    assert rect.width == pytest.approx(0.5)
    assert rect.height == pytest.approx(0.5)

    layer = MediaOverlayLayer(
        id="ov1",
        name="face.png",
        path=Path("face.png"),
        kind="image",
        source_width=400,
        source_height=300,
        duration_sec=0.0,
        has_audio=False,
        rect=rect,
    )
    cropped = apply_overlay_crop_rect(layer, CropRect(11, 21, 101, 101))
    assert cropped.source_crop == CropRect(10, 20, 100, 100)
    assert cropped.effective_source_size() == (100, 100)


def test_build_media_overlay_layer_preserves_aspect() -> None:
    asset = probe_media_asset_dummy = type(
        "Dummy",
        (),
        {
            "path": Path("face.png"),
            "kind": "image",
            "width": 200,
            "height": 400,
            "duration_sec": 0.0,
            "has_audio": False,
        },
    )()
    layer = build_media_overlay_layer(asset, stage_w=1000, stage_h=1000)
    px, py, pw, ph = layer.rect.to_pixel_rect(1000, 1000)
    assert ph > pw
    assert abs((pw / ph) - 0.5) < 0.05


def test_render_shape_mask_png_circle(tmp_path: Path) -> None:
    mask_path = tmp_path / "circle_mask.png"
    render_shape_mask_png(100, 100, "circle", mask_path)
    assert mask_path.is_file()
    loaded = QImage(str(mask_path))
    assert loaded.width() == 100
    assert loaded.height() == 100
    # Center is opaque white, corner is transparent.
    assert loaded.pixelColor(50, 50).alpha() == 255
    assert loaded.pixelColor(1, 1).alpha() == 0


def test_build_meme_export_cmds_image_with_circle_face_and_topbar(tmp_path: Path) -> None:
    base = BaseLayer(
        path=Path("meme.png"),
        kind="image",
        width=800,
        height=600,
        duration_sec=0.0,
        has_audio=False,
        top_bar_text="When your friend joins the call",
    )
    face = MediaOverlayLayer(
        id="face1",
        name="friend.png",
        path=Path("friend.png"),
        kind="image",
        source_width=300,
        source_height=300,
        duration_sec=0.0,
        has_audio=False,
        rect=NormalizedRect(0.3, 0.2, 0.25, 0.33),
        source_crop=CropRect(20, 20, 260, 260),
        shape="circle",
        flip_h=True,
        opacity=0.9,
        chroma_key_color="#00FF00",
        chroma_similarity=0.3,
    )
    txt = TextOverlayLayer(
        id="txt1",
        text="BRUH",
        rect=NormalizedRect(0.1, 0.75, 0.8, 0.2),
    )
    comp = MemeComposition(base=base, overlays=(face, txt))
    assert not comp.has_animation()

    bar_h, _, _ = compute_top_bar_layout(800, base.top_bar_text)
    assets = MemeFilterAssets(
        top_bar_png=tmp_path / "topbar.png",
        top_bar_height=bar_h,
        shape_mask_pngs={"face1": tmp_path / "mask_face1.png"},
        text_overlay_pngs={"txt1": tmp_path / "text_txt1.png"},
    )
    cmds = build_meme_export_cmds(
        ffmpeg_path="ffmpeg",
        composition=comp,
        output_path=tmp_path / "out.png",
        output_format="png",
        stage_width=800,
        stage_height=600,
        duration_sec=5.0,
        assets=assets,
    )
    assert len(cmds) == 1
    cmd = cmds[0]
    fc = cmd[cmd.index("-filter_complex") + 1]
    assert "pad=iw:ih+" in fc
    assert "crop=260:260:20:20" in fc
    assert "hflip" in fc
    assert "colorkey=0x00FF00:0.30" in fc
    assert "alphamerge" in fc
    assert "-frames:v" in cmd


def test_build_meme_export_cmds_static_base_with_video_overlay_loops(tmp_path: Path) -> None:
    base = BaseLayer(
        path=Path("bg.png"),
        kind="image",
        width=640,
        height=360,
        duration_sec=0.0,
        has_audio=False,
    )
    vid_ov = MediaOverlayLayer(
        id="v1",
        name="react.mp4",
        path=Path("react.mp4"),
        kind="video",
        source_width=320,
        source_height=240,
        duration_sec=2.5,
        has_audio=True,
        rect=NormalizedRect(0.5, 0.5, 0.4, 0.4),
        start_sec=0.5,
        end_sec=2.0,
    )
    comp = MemeComposition(base=base, overlays=(vid_ov,))
    assert comp.has_animation()
    assert comp.effective_duration_sec() == pytest.approx(2.0)

    cmds = build_meme_export_cmds(
        ffmpeg_path="ffmpeg",
        composition=comp,
        output_path=tmp_path / "out.gif",
        output_format="gif",
        stage_width=480,
        stage_height=270,
        duration_sec=2.0,
        gif_fps=15,
    )
    assert len(cmds) == 1
    cmd = cmds[0]
    fc = cmd[cmd.index("-filter_complex") + 1]
    assert "-loop" in cmd
    assert "enable='between(t,0.500,2.000)'" in fc
    assert "palettegen=stats_mode=diff" in fc


def test_render_classic_caption_png_respects_size_scale_on_portrait(tmp_path: Path) -> None:
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])

    def rendered_ink_height(scale: float) -> int:
        out = tmp_path / f"cap_{scale}.png"
        render_classic_caption_png(
            GifCaption(bottom_text="HELLO", size_scale=scale, uppercase=True),
            stage_width=600,
            stage_height=1000,
            output_path=out,
        )
        img = QImage(str(out))
        ys = [
            y
            for y in range(img.height())
            if any(img.pixelColor(x, y).alpha() > 0 for x in range(0, img.width(), 4))
        ]
        assert ys
        return max(ys) - min(ys)

    h_small = rendered_ink_height(0.78)
    h_med = rendered_ink_height(1.0)
    h_large = rendered_ink_height(1.28)
    assert h_small < h_med < h_large
    # Portrait 600x1000 uses width-constrained 600*0.085 = 51px base -> Medium should stay ~45-70px tall, not >90px.
    assert h_med < 75
    app.processEvents()
