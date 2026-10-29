"""Frames of an edited MP4, read back at full size through Blender's sequencer (there is no ffmpeg to call).

The grab scene uses the Standard view transform: the video's pixels are already display-ready, and the default AgX
would tone-map them a second time, so the frames would look greyer and flatter than the video does."""

from __future__ import annotations

import math
import tempfile
from pathlib import Path

import bpy
import numpy as np

from bsa.blender.pixels import read_png
from bsa.blender.stills import _window_scene

SCENE = "BSA Grab"


def frame_at(seconds: float, fps: float, n_frames: int) -> int:
    """The video frame (1-based) on screen at `seconds`; ValueError outside the video."""
    length = n_frames / fps
    if not 0 <= seconds <= length:
        raise ValueError(f"{seconds:g} s is outside the video (0 to {length:.2f} s)")
    return min(n_frames, int(seconds * fps + 1e-6) + 1)


def sheet_frames(n_frames: int, count: int) -> list[int]:
    """`count` frames spread evenly over the video (the middle of each of `count` equal parts), without repeats."""
    return sorted({min(n_frames, int((i + 0.5) * n_frames / count) + 1) for i in range(max(1, count))})


def _scene(mp4: Path, size: tuple[int, int]):
    old = bpy.data.scenes.get(SCENE)
    if old is not None:
        bpy.data.scenes.remove(old)
    sc = bpy.data.scenes.new(SCENE)
    r = sc.render
    r.resolution_x, r.resolution_y, r.resolution_percentage = size[0], size[1], 100
    r.use_sequencer, r.use_compositing = True, False
    sc.view_settings.view_transform = "Standard"
    sc.view_settings.look = "None"
    sc.view_settings.exposure, sc.view_settings.gamma = 0.0, 1.0
    if hasattr(r.image_settings, "media_type"):
        r.image_settings.media_type = "IMAGE"
    r.image_settings.file_format = "PNG"
    r.image_settings.color_mode = "RGB"
    r.image_settings.color_depth = "8"
    r.use_file_extension = False
    strip = sc.sequence_editor_create().strips.new_movie("Video", str(mp4), 1, 1, fit_method="ORIGINAL")
    sc.frame_start, sc.frame_end = 1, max(1, strip.frame_final_duration)
    return sc


def _size(mp4: Path) -> tuple[int, int]:
    clip = bpy.data.movieclips.load(str(mp4))
    try:
        return int(clip.size[0]), int(clip.size[1])
    finally:
        bpy.data.movieclips.remove(clip)


def grab_frames(mp4: Path, frames: list[int], out_dir: Path, stem: str) -> list[Path]:
    """Each frame (1-based) of the video as `<out_dir>/<stem>-f0123.png` at the video's own size and colours."""
    mp4, out_dir = Path(mp4), Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    sc = _scene(mp4, _size(mp4))
    paths = []
    try:
        with _window_scene(sc):
            for f in frames:
                out = out_dir / f"{stem}-f{f:04d}.png"
                sc.render.filepath = str(out)
                sc.frame_set(f)
                bpy.ops.render.render(write_still=True, scene=sc.name)
                if not out.is_file() or out.stat().st_size == 0:
                    raise RuntimeError(f"grabbing frame {f} of {mp4.name} wrote nothing")
                paths.append(out)
    finally:
        bpy.data.scenes.remove(sc)
    return paths


def _write_png(path: Path, rgb: np.ndarray) -> None:
    """An (h, w, 3) uint8 array, top row first, as an 8-bit PNG (byte images are stored as given)."""
    h, w = rgb.shape[:2]
    rgba = np.concatenate([rgb, np.full((h, w, 1), 255, np.uint8)], axis=2)[::-1]
    img = bpy.data.images.new("bsa_sheet", w, h, alpha=False)
    try:
        img.pixels.foreach_set((rgba.astype(np.float32) / 255.0).ravel())
        img.filepath_raw = str(path)
        img.file_format = "PNG"
        img.save()
    finally:
        bpy.data.images.remove(img)


def contact_sheet(mp4: Path, frames: list[int], out_png: Path, columns: int = 4, scale: float = 0.25) -> Path:
    """The frames side by side, `columns` per row, each shrunk to `scale` (block-averaged), in order."""
    out_png = Path(out_png)
    out_png.parent.mkdir(parents=True, exist_ok=True)
    k = max(1, round(1 / scale))
    with tempfile.TemporaryDirectory(prefix="bsa-grab-") as tmp:
        tiles = []
        for p in grab_frames(mp4, frames, Path(tmp), "f"):
            px = read_png(p)[..., :3].astype(np.float32)
            th, tw = px.shape[0] // k, px.shape[1] // k
            tiles.append(px[: th * k, : tw * k].reshape(th, k, tw, k, 3).mean(axis=(1, 3)))
    th, tw = tiles[0].shape[:2]
    rows = math.ceil(len(tiles) / columns)
    sheet = np.zeros((rows * th, min(columns, len(tiles)) * tw if rows == 1 else columns * tw, 3), np.float32)
    for i, tile in enumerate(tiles):
        r, c = divmod(i, columns)
        sheet[r * th : (r + 1) * th, c * tw : (c + 1) * tw] = tile
    _write_png(out_png, np.rint(sheet).astype(np.uint8))
    return out_png
