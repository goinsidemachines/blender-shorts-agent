"""The camera test's Blender side: shots rendered in Workbench grey, then one MP4 with Blender's own encoder.

Like the world mask (stills.world_mask), the Workbench pass is a draft view of the camera path, never a picture:
it is one of the two places the plugin switches the render engine, and the user's engine and every other setting it
touches are restored afterwards, also when the render fails. The .blend is never saved."""

from __future__ import annotations

import contextlib
from pathlib import Path

import bpy

from bsa.blender import edit
from bsa.blender.scene import preserved_render_state
from bsa.blender.stills import _window_scene

GREY = (0.8, 0.8, 0.8)
SCENE = "BSA Camtest"


@contextlib.contextmanager
def workbench_pass(scene, percent: int):
    """Inside: `scene` in Workbench at `percent`, studio light, one grey, PNG RGB 8-bit, no compositor or sequencer,
    the Standard view, film not transparent. Afterwards every one of those settings, the output path, the image and
    video settings and the frame range and current frame are as they were (preserved_render_state restores the
    engine and resolution percentage)."""
    r = scene.render
    ims = r.image_settings
    ff = r.ffmpeg
    vs = scene.view_settings
    sh = scene.display.shading
    media = getattr(ims, "media_type", None)
    saved = {
        "film": r.film_transparent, "path": r.filepath, "ext": r.use_file_extension,
        "fmt": ims.file_format, "mode": ims.color_mode, "depth": ims.color_depth,
        "ffmpeg": (ff.format, ff.codec), "frames": (scene.frame_start, scene.frame_end, scene.frame_current),
        "comp": r.use_compositing, "seq": r.use_sequencer, "view": (vs.view_transform, vs.look),
        "shading": (sh.light, sh.color_type, tuple(sh.single_color)),
    }  # fmt: skip
    try:
        with _window_scene(scene), preserved_render_state(scene):
            r.engine = "BLENDER_WORKBENCH"
            r.resolution_percentage = percent
            r.film_transparent = False
            r.use_compositing = r.use_sequencer = False
            vs.view_transform = "Standard"
            vs.look = "None"
            sh.light = "STUDIO"
            sh.color_type = "SINGLE"
            sh.single_color = GREY
            if media is not None:
                ims.media_type = "IMAGE"
            ims.file_format = "PNG"
            ims.color_mode = "RGB"
            ims.color_depth = "8"
            yield
    finally:
        r.film_transparent, r.filepath, r.use_file_extension = saved["film"], saved["path"], saved["ext"]
        if media is not None:
            ims.media_type = media
        ims.file_format = saved["fmt"]
        ims.color_mode = saved["mode"]
        ims.color_depth = saved["depth"]
        ff.format, ff.codec = saved["ffmpeg"]
        r.use_compositing, r.use_sequencer = saved["comp"], saved["seq"]
        vs.view_transform = saved["view"][0]
        vs.look = saved["view"][1]
        sh.light, sh.color_type, sh.single_color = saved["shading"]
        scene.frame_start, scene.frame_end = saved["frames"][0], saved["frames"][1]
        scene.frame_set(saved["frames"][2])


def render_frames(scene, f0: int, f1: int, folder: Path, percent: int) -> list[Path]:
    """Frames f0..f1 of `scene` in Workbench grey as ####.png in `folder` (one animation render). Raises
    RuntimeError when a frame is missing or empty."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    with workbench_pass(scene, percent):
        scene.render.filepath = str(folder) + "/"
        scene.render.use_file_extension = True
        scene.frame_start, scene.frame_end = f0, f1
        bpy.ops.render.render(animation=True, scene=scene.name)
    paths = [folder / f"{f:04d}.png" for f in range(f0, f1 + 1)]
    bad = [p.name for p in paths if not p.is_file() or p.stat().st_size == 0]
    if bad:
        raise RuntimeError(f"the camera test of scene {scene.name!r} left missing or empty frames: {', '.join(bad[:5])}")
    return paths


def encode(parts: list[tuple[Path, int, int]], out_mp4: Path, percent: int, fps: int) -> Path:
    """`parts` ((folder, f0, f1): frames ####.png) back to back as one silent H.264 MP4 at `percent` of 1080x1920,
    through the edit's own output settings and Blender's encoder, in a temporary scene removed afterwards."""
    old = bpy.data.scenes.get(SCENE)
    if old is not None:
        bpy.data.scenes.remove(old)
    sc = bpy.data.scenes.new(SCENE)
    try:
        edit._configure_output(sc, Path(out_mp4), "preview")
        sc.render.resolution_percentage = percent
        sc.render.fps, sc.render.fps_base = int(fps), 1.0
        sc.render.ffmpeg.audio_codec = "NONE"
        strips = sc.sequence_editor_create().strips
        at = 1
        for i, (folder, f0, f1) in enumerate(parts):
            s = strips.new_image(f"Part {i + 1}", str(Path(folder) / f"{f0:04d}.png"), 1, at, fit_method="FIT")
            for f in range(f0 + 1, f1 + 1):
                s.elements.append(f"{f:04d}.png")
            at += f1 - f0 + 1
        sc.frame_start, sc.frame_end = 1, max(1, at - 1)
        return edit.encode(sc)
    finally:
        bpy.data.scenes.remove(sc)
