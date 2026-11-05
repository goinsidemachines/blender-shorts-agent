import tempfile
from pathlib import Path

import bpy

from bsa.blender import camtest, edit, grab
from bsa.blender.pixels import read_png


def _scene():
    """A cube and a camera keyed over frames 1-30, at 1080x1920 (the short's size)."""
    bpy.ops.wm.read_factory_settings(use_empty=True)
    sc = bpy.data.scenes[0]
    sc.render.resolution_x, sc.render.resolution_y = 1080, 1920
    bpy.ops.mesh.primitive_cube_add(size=1.0)
    cam_data = bpy.data.cameras.new("Cam")
    cam = bpy.data.objects.new("Cam", cam_data)
    sc.collection.objects.link(cam)
    sc.camera = cam
    cam.rotation_euler = (1.2, 0.0, 0.8)
    for f, x in ((1, 6.0), (30, 4.0)):
        cam.location = (x, -x, 3.0)
        cam.keyframe_insert("location", frame=f)
    return sc


def test_camtest_makes_an_mp4_and_a_sheet():
    sc = _scene()
    tmp = Path(tempfile.mkdtemp())
    frames = camtest.render_frames(sc, 1, 30, tmp / "frames", 35)
    assert [p.name for p in frames] == [f"{f:04d}.png" for f in range(1, 31)]
    px = read_png(frames[0])
    assert px.shape[:2] == (672, 378)
    rgb = px[..., :3].reshape(-1, 3).astype(int)
    assert (abs(rgb[:, 0] - rgb[:, 1]) < 4).all() and (abs(rgb[:, 1] - rgb[:, 2]) < 4).all()  # grey only
    mp4 = camtest.encode([(tmp / "frames", 1, 20), (tmp / "frames", 21, 30)], tmp / "camtest.mp4", 35, 30)
    info = edit.probe(mp4)
    assert (info["width"], info["height"], info["frames"]) == (378, 672, 30), info
    assert not info["has_audio"]
    assert "BSA Camtest" not in bpy.data.scenes  # the encode scene is removed again
    sheet = grab.contact_sheet(mp4, grab.sheet_frames(30, 12), tmp / "sheet.png")
    assert sheet.is_file() and read_png(sheet).shape[0] > 0


def _state(sc) -> dict:
    r, ims, vs, sh = sc.render, sc.render.image_settings, sc.view_settings, sc.display.shading
    out = {
        "engine": r.engine, "percent": r.resolution_percentage, "film": r.film_transparent, "path": r.filepath,
        "ext": r.use_file_extension, "fmt": ims.file_format, "mode": ims.color_mode, "depth": ims.color_depth,
        "start": sc.frame_start, "end": sc.frame_end, "current": sc.frame_current, "comp": r.use_compositing,
        "seq": r.use_sequencer, "view": vs.view_transform, "look": vs.look, "light": sh.light,
        "color": sh.color_type, "single": tuple(sh.single_color), "ff": (r.ffmpeg.format, r.ffmpeg.codec),
        "step": sc.frame_step, "exposure": vs.exposure, "gamma": vs.gamma, "cm": ims.color_management,
        "flags": (sh.show_shadows, sh.show_cavity, sh.show_xray, sh.show_object_outline),
    }  # fmt: skip
    if hasattr(ims, "media_type"):
        out["media"] = ims.media_type
    out["compositor"] = sc.compositing_node_group if hasattr(sc, "compositing_node_group") else sc.use_nodes
    return out


def test_camtest_restores_every_setting_it_touches():
    sc = _scene()
    tmp = Path(tempfile.mkdtemp())
    blend = tmp / "user.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(blend))
    bpy.ops.wm.open_mainfile(filepath=str(blend))
    stamp = blend.stat().st_mtime_ns
    sc = bpy.context.scene
    r = sc.render
    r.engine = "CYCLES"
    r.resolution_percentage = 80
    r.film_transparent = True
    r.filepath = "//renders/shot_"
    r.use_file_extension = True
    if hasattr(r.image_settings, "media_type"):
        r.image_settings.media_type = "VIDEO"
    r.image_settings.file_format = "FFMPEG"
    r.ffmpeg.format, r.ffmpeg.codec = "QUICKTIME", "PNG"
    sc.view_settings.view_transform = "AgX"
    sc.view_settings.look = "AgX - Punchy"
    r.use_compositing = True
    if hasattr(sc, "compositing_node_group"):  # a compositor (Blender 5)
        sc.compositing_node_group = bpy.data.node_groups.new("Comp", "CompositorNodeTree")
    else:
        sc.use_nodes = True
    r.use_sequencer = False
    sc.display.shading.light = "FLAT"
    sc.display.shading.color_type = "MATERIAL"
    sc.display.shading.single_color = (0.1, 0.5, 0.2)
    sc.display.shading.show_shadows = sc.display.shading.show_cavity = True
    sc.display.shading.show_xray = sc.display.shading.show_object_outline = True
    sc.view_settings.exposure, sc.view_settings.gamma = 0.5, 1.2
    r.image_settings.color_management = "OVERRIDE"
    sc.frame_start, sc.frame_end, sc.frame_step = 5, 20, 2
    sc.frame_set(7)
    before = _state(sc)
    assert before["engine"] == "CYCLES" and before["view"] == "AgX" and before["film"] and before["look"] != "None"
    assert before["compositor"]

    with camtest.workbench_pass(sc, 35):
        assert r.engine == "BLENDER_WORKBENCH" and r.resolution_percentage == 35
        assert not r.film_transparent and not r.use_compositing and not r.use_sequencer
        assert sc.view_settings.view_transform == "Standard"
        assert (sc.display.shading.light, sc.display.shading.color_type) == ("STUDIO", "SINGLE")
        assert r.image_settings.file_format == "PNG" and r.image_settings.color_management == "FOLLOW_SCENE"
        assert (sc.frame_step, sc.view_settings.exposure, sc.view_settings.gamma) == (1, 0.0, 1.0)
        assert not any((sc.display.shading.show_shadows, sc.display.shading.show_xray))
        sc.frame_start, sc.frame_end = 1, 2
        sc.frame_set(2)
    assert _state(sc) == before

    try:
        with camtest.workbench_pass(sc, 50):
            r.filepath = "//elsewhere/"
            raise RuntimeError("render failed")
    except RuntimeError:
        pass
    else:
        raise AssertionError("the error was swallowed")
    assert _state(sc) == before

    camtest.render_frames(sc, 1, 3, tmp / "frames", 20)  # a real pass on this scene
    assert _state(sc) == before
    assert blend.stat().st_mtime_ns == stamp  # the .blend is never saved


def test_camtest_restores_the_rest_when_one_restore_fails():
    sc = _scene()
    sc.render.engine = "CYCLES"
    sc.view_settings.exposure = 0.5
    sc.frame_start, sc.frame_end = 5, 20

    class Stuck:
        """A setting that changes during the pass and can't be set back."""

        n = 0

        @property
        def value(self):
            Stuck.n += 1
            return Stuck.n

        @value.setter
        def value(self, v):
            raise RuntimeError("can't restore this one")

    real = camtest._touched
    camtest._touched = lambda scene: [(Stuck(), "value"), *real(scene)]
    try:
        before = _state(sc)
        try:
            with camtest.workbench_pass(sc, 35):
                pass
        except RuntimeError as exc:
            assert "can't restore" in str(exc)
        else:
            raise AssertionError("the failed restore was swallowed")
        assert _state(sc) == before  # every other setting came back
        try:
            with camtest.workbench_pass(sc, 35):
                raise ValueError("render failed")
        except ValueError:
            pass  # the render's own error wins over the failed restore
        assert _state(sc) == before
    finally:
        camtest._touched = real
