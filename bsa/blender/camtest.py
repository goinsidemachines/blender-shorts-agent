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


def _touched(scene) -> list[tuple[object, str]]:
    """Every (owner, property) a camera test pass may change on the user's scene, in the order they are restored:
    the media type before the file format, the view transform before its look (Blender resets the dependent
    enum). Properties this Blender lacks are left out."""
    r = scene.render
    ims, ff, vs, sh = r.image_settings, r.ffmpeg, scene.view_settings, scene.display.shading
    props = [(r, "film_transparent"), (r, "filepath"), (r, "use_file_extension"), (ims, "media_type"),
             (ims, "file_format"), (ims, "color_mode"), (ims, "color_depth"), (ims, "color_management"),
             (ff, "format"), (ff, "codec"), (r, "use_compositing"), (r, "use_sequencer"), (vs, "view_transform"),
             (vs, "look"), (vs, "exposure"), (vs, "gamma"), (sh, "light"), (sh, "color_type"), (sh, "single_color"),
             (sh, "show_shadows"), (sh, "show_cavity"), (sh, "show_xray"), (sh, "show_object_outline"),
             (scene, "frame_start"), (scene, "frame_end"), (scene, "frame_step")]  # fmt: skip
    return [(o, a) for o, a in props if hasattr(o, a)]


def _value(v):
    return tuple(v) if hasattr(v, "__len__") and not isinstance(v, str) else v


@contextlib.contextmanager
def workbench_pass(scene, percent: int):
    """Inside: `scene` in Workbench at `percent`, studio light, one grey, no shadows, cavity, x-ray or outline, PNG
    RGB 8-bit following the scene's colour management, no compositor or sequencer, the Standard view at exposure 0
    and gamma 1, film not transparent, every frame (step 1). Afterwards every one of those settings, the output path,
    the image and video settings and the frame range and current frame are as they were (preserved_render_state
    restores the engine and resolution percentage). Each one is restored even when another fails to; the first
    failure is raised after all were tried (the render's own error, when there was one, wins)."""
    r = scene.render
    ims, vs, sh = r.image_settings, scene.view_settings, scene.display.shading
    saved = [(o, a, _value(getattr(o, a))) for o, a in _touched(scene)]
    current = scene.frame_current
    failed = False
    try:
        with _window_scene(scene), preserved_render_state(scene):
            r.engine = "BLENDER_WORKBENCH"
            r.resolution_percentage = percent
            r.film_transparent = False
            r.use_compositing = r.use_sequencer = False
            vs.view_transform = "Standard"
            vs.look = "None"
            vs.exposure, vs.gamma = 0.0, 1.0
            sh.light = "STUDIO"
            sh.color_type = "SINGLE"
            sh.single_color = GREY
            for flag in ("show_shadows", "show_cavity", "show_xray", "show_object_outline"):
                if hasattr(sh, flag):
                    setattr(sh, flag, False)
            if hasattr(ims, "media_type"):
                ims.media_type = "IMAGE"
            ims.file_format = "PNG"
            ims.color_mode = "RGB"
            ims.color_depth = "8"
            if hasattr(ims, "color_management"):
                ims.color_management = "FOLLOW_SCENE"
            scene.frame_step = 1
            yield
    except BaseException:
        failed = True
        raise
    finally:
        errors = []
        for o, a, v in saved:
            try:
                if _value(getattr(o, a)) != v:
                    setattr(o, a, v)
            except Exception as exc:  # noqa: BLE001 - the others are restored first, then the first is raised
                errors.append(exc)
        try:
            scene.frame_set(current)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)
        if errors and not failed:
            raise errors[0]


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
