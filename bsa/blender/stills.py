"""Single-frame stills for the storyboard and preview pages."""

from __future__ import annotations

import contextlib
import shutil
import tempfile
from pathlib import Path

import bpy
import numpy as np

from bsa.blender import look
from bsa.blender.scene import apply_preset, preserved_render_state, resolve_engine

MASK_PERCENT = 10  # the world-mask pass renders at this resolution percentage: shapes, not detail


@contextlib.contextmanager
def _window_scene(scene):
    """Show `scene` in the window while rendering. A still renders the window scene's evaluated state, so a scene that
    is not the window's would render as it was last evaluated (its first frame), not at the frame set."""
    wm = bpy.context.window_manager
    win = bpy.context.window or (wm.windows[0] if wm and wm.windows else None)
    if win is None or win.scene == scene:
        yield
        return
    previous = win.scene
    win.scene = scene
    try:
        yield
    finally:
        win.scene = previous


def render_still(
    scene, frame: int, out_jpg: Path, preset: str = "still", percent: int | None = None, engine: str | None = None,
    file_format: str = "JPEG",
) -> Path:
    """Render one frame as JPEG (quality 85), or as an 8-bit PNG with `file_format="PNG"`. `engine` is "cycles" |
    "eevee"; None uses the scene's current engine.

    The scene's render and output settings are restored afterwards.
    """
    out_jpg = Path(out_jpg)
    out_jpg.parent.mkdir(parents=True, exist_ok=True)
    engine = resolve_engine(scene, engine)
    r = scene.render
    ims = r.image_settings
    saved = (r.filepath, r.use_file_extension, ims.file_format, ims.color_depth, scene.frame_current)
    try:
        with _window_scene(scene), preserved_render_state(scene):
            apply_preset(scene, engine, preset, percent)
            look.match_engine(scene)  # a brand style's lights and world for the engine this still renders with
            ims.file_format = file_format
            if file_format == "PNG":
                ims.color_depth = "8"
            else:
                ims.quality = 85
            ims.color_mode = "RGB"
            r.use_file_extension = False
            r.filepath = str(out_jpg)
            scene.frame_set(frame)
            bpy.ops.render.render(write_still=True, scene=scene.name)
    finally:
        r.filepath, r.use_file_extension = saved[0], saved[1]
        ims.file_format = saved[2]
        ims.color_mode = "RGB"
        ims.color_depth = saved[3]
        scene.frame_set(saved[4])
    return out_jpg


def world_mask(scene, frame: int, shape: tuple[int, int]) -> np.ndarray:
    """Where the world shows in `scene` at `frame`, as a bool array of `shape` (the still's height, width): True where
    no object covers the background.

    One quick Workbench pass (MASK_PERCENT, film transparent, PNG RGBA, no compositor or sequencer, which could
    repaint the alpha) to a temp file; a pixel with alpha 0 is world, so a semi-transparent part (glass, a glow)
    counts as not world wherever it covers. This is the only place the plugin switches the render engine: it is a
    measuring pass, never a picture, and the user's engine and every other setting it touches are restored
    afterwards, also when the render fails. No device or samples are set, the temp file is deleted and the .blend
    is never saved."""
    from bsa.blender.pixels import read_png

    r = scene.render
    ims = r.image_settings
    saved = (r.film_transparent, r.filepath, r.use_file_extension, ims.file_format, ims.color_mode, ims.color_depth,
             scene.frame_current, r.use_compositing, r.use_sequencer)  # fmt: skip
    tmp = Path(tempfile.mkdtemp(prefix="bsa-mask-"))
    try:
        with _window_scene(scene), preserved_render_state(scene):
            r.engine = "BLENDER_WORKBENCH"
            r.resolution_percentage = MASK_PERCENT
            r.film_transparent = True
            r.use_compositing = r.use_sequencer = False
            ims.file_format = "PNG"
            ims.color_mode = "RGBA"
            ims.color_depth = "8"
            r.use_file_extension = False
            r.filepath = str(tmp / "mask.png")
            scene.frame_set(frame)
            bpy.ops.render.render(write_still=True, scene=scene.name)
            alpha = read_png(tmp / "mask.png")[..., 3]
    finally:
        r.film_transparent, r.filepath, r.use_file_extension = saved[0], saved[1], saved[2]
        ims.file_format = saved[3]
        ims.color_mode = saved[4]
        ims.color_depth = saved[5]
        scene.frame_set(saved[6])
        r.use_compositing, r.use_sequencer = saved[7], saved[8]
        shutil.rmtree(tmp, ignore_errors=True)
    h, w = shape
    mh, mw = alpha.shape
    rows = np.arange(h) * mh // h
    cols = np.arange(w) * mw // w
    return (alpha == 0)[rows[:, None], cols[None, :]]
