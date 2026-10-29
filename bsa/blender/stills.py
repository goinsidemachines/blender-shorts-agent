"""Single-frame stills for the storyboard and preview pages."""

from __future__ import annotations

import contextlib
from pathlib import Path

import bpy

from bsa.blender.scene import apply_preset, preserved_render_state, resolve_engine


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
    scene, frame: int, out_jpg: Path, preset: str = "still", percent: int | None = None, engine: str | None = None
) -> Path:
    """Render one frame as JPEG (quality 85). `engine` is "cycles" | "eevee"; None uses the scene's current engine.

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
            ims.file_format = "JPEG"
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
