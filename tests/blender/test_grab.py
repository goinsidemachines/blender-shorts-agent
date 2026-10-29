import tempfile
from pathlib import Path

import bpy
import numpy as np

from bsa.blender import grab
from bsa.blender.pixels import read_png

W, H, FPS = 108, 192, 30
RED = (0.8, 0.25, 0.1)  # sRGB values as stored in the PNG frames
BLUE = (0.1, 0.3, 0.7)


def _png(path: Path, color) -> None:
    img = bpy.data.images.new("tmp_color", W, H)
    img.pixels.foreach_set(np.tile([*color, 1.0], W * H).astype(np.float32))
    img.filepath_raw = str(path)
    img.file_format = "PNG"
    img.save()
    bpy.data.images.remove(img)


def _mp4() -> Path:
    """A 20-frame MP4 at 30 fps: frames 1-10 red, 11-20 blue, encoded the way the edit encodes (Standard view)."""
    bpy.ops.wm.read_factory_settings(use_empty=True)
    tmp = Path(tempfile.mkdtemp())
    for i in range(1, 21):
        _png(tmp / f"{i:04d}.png", RED if i <= 10 else BLUE)
    sc = bpy.data.scenes.new("Enc")
    r = sc.render
    r.resolution_x, r.resolution_y, r.resolution_percentage, r.fps = W, H, 100, FPS
    sc.view_settings.view_transform = "Standard"
    sc.frame_start, sc.frame_end = 1, 20
    seq = sc.sequence_editor_create()
    s = seq.strips.new_image("f", str(tmp / "0001.png"), 1, 1, fit_method="FIT")
    for i in range(2, 21):
        s.elements.append(f"{i:04d}.png")
    if hasattr(r.image_settings, "media_type"):
        r.image_settings.media_type = "VIDEO"
    r.image_settings.file_format = "FFMPEG"
    r.ffmpeg.format, r.ffmpeg.codec, r.ffmpeg.constant_rate_factor = "MPEG4", "H264", "PERC_LOSSLESS"
    r.filepath = str(tmp / "clip.mp4")
    bpy.ops.render.render(animation=True, scene=sc.name)
    return tmp / "clip.mp4"


def _mean(png: Path):
    return read_png(png)[..., :3].reshape(-1, 3).mean(axis=0) / 255.0


def test_frames_for_seconds():
    assert grab.frame_at(0.0, 30, 20) == 1
    assert grab.frame_at(0.34, 30, 20) == 11
    assert grab.frame_at(1.0 / 3, 30, 20) == 11  # exactly a frame's start time shows that frame
    assert grab.frame_at(0.666, 30, 20) == 20
    for bad in (-0.1, 0.7):
        try:
            grab.frame_at(bad, 30, 20)
        except ValueError as exc:
            assert "0.67" in str(exc)
        else:
            raise AssertionError(f"{bad} s accepted")
    assert grab.sheet_frames(20, 4) == [3, 8, 13, 18]
    assert grab.sheet_frames(3, 12) == [1, 2, 3]


def test_grab_keeps_the_videos_colours_at_full_size():
    mp4 = _mp4()
    out = mp4.parent / "grabs"
    paths = grab.grab_frames(mp4, [5, 15], out, "clip")
    assert [p.name for p in paths] == ["clip-f0005.png", "clip-f0015.png"]
    for p, want in zip(paths, (RED, BLUE), strict=True):
        px = read_png(p)
        assert px.shape[:2] == (H, W)
        got = _mean(p)
        assert np.abs(got - np.array(want)).max() < 0.04, (p.name, got, want)  # AgX would wash these out
    assert "BSA Grab" not in bpy.data.scenes  # the grab scene is removed again


def test_contact_sheet_tiles_the_frames():
    mp4 = _mp4()
    sheet = grab.contact_sheet(mp4, [5, 15, 5], mp4.parent / "sheet.png", columns=2, scale=0.5)
    px = read_png(sheet)
    th, tw = H // 2, W // 2
    assert px.shape[:2] == (2 * th, 2 * tw)
    tl = px[: th, : tw, :3].reshape(-1, 3).mean(axis=0) / 255.0
    tr = px[: th, tw :, :3].reshape(-1, 3).mean(axis=0) / 255.0
    assert np.abs(tl - np.array(RED)).max() < 0.05 and np.abs(tr - np.array(BLUE)).max() < 0.05
    assert px[th:, tw:, :3].max() < 40  # the empty last tile stays dark
