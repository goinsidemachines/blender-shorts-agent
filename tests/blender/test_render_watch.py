"""Inside Blender: the live render status follows a real (resumed) render through the render handlers."""

import json
import tempfile
import time
from pathlib import Path

import bpy

from bsa import freshness
from bsa import render_status as rs
from bsa.blender import render
from bsa.blender import scene as scene_mod
from bsa.blender.render_watch import Watch
from bsa.review import pages
from bsa.short import Short
from bsa.timeline import Span


def _scene(lit=True):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    sc = bpy.context.scene
    bpy.ops.mesh.primitive_cube_add()
    cube = bpy.context.object
    cube.keyframe_insert("location", frame=1)
    cube.location.x = 2.0
    cube.keyframe_insert("location", frame=6)
    cam = bpy.data.objects.new("Cam", bpy.data.cameras.new("Cam"))
    sc.collection.objects.link(cam)
    cam.location = (0, -8, 0)
    cam.rotation_euler = (1.5708, 0, 0)
    sc.camera = cam
    if lit:
        sun = bpy.data.objects.new("Sun", bpy.data.lights.new("Sun", "SUN"))
        sc.collection.objects.link(sun)
    else:
        world = bpy.data.worlds.new("Black")
        world.use_nodes = True
        bg = world.node_tree.nodes.get("Background")
        bg.inputs["Strength"].default_value = 0.0
        sc.world = world
    scene_mod.setup_scene(sc, Short(slug="t", title="t", length_s=1.0), "eevee", "preview")
    return sc


def _run(sc, sdir, shots, percent=10):
    """The same steps cmd_render takes, on one scene."""
    plan = []
    for sid, f0, f1 in shots:
        stamp = freshness.frames_stamp(Span(sid, f0, f1), "eevee", "fp", "preview")
        plan.append({"id": sid, "f0": f0, "f1": f1, **rs.frames_plan(sdir / "frames" / sid, f0, f1, stamp)})
    page = rs.page_path(sdir, "preview")
    tracker = rs.Tracker(rs.status_path(sdir, "preview"), short={"slug": "t", "title": "T", "dir": str(sdir)},
                         preset="preview", engine="eevee", shots=plan, stamp={"timeline": "h"},
                         render_page=lambda st: pages.render_status_page(rs.describe(st), page))  # fmt: skip
    with Watch(tracker, sdir, "preview") as watch:
        tracker.start()
        for sid, f0, f1 in shots:
            folder = sdir / "frames" / sid
            freshness.prepare_frames(folder, freshness.frames_stamp(Span(sid, f0, f1), "eevee", "fp", "preview"))
            tracker.begin_shot(sid)
            render.render_range(sc, f0, f1, folder, "preview", percent=percent, engine="eevee",
                                on_start=watch.on_start)  # fmt: skip
            tracker.end_shot(sid)
        tracker.finish("done")
    return json.loads(rs.status_path(sdir, "preview").read_text(encoding="utf-8"))


def test_status_follows_a_resumed_render():
    sc = _scene()
    sdir = Path(tempfile.mkdtemp())
    _run(sc, sdir, [("s01", 1, 2)])  # an earlier, interrupted run left frames 1-2 of s01
    s = _run(sc, sdir, [("s01", 1, 3), ("s02", 4, 6)])
    assert s["state"] == "done" and s["phase"] == "done"
    one, two = s["shots"]
    # s01's range changed (1-2 -> 1-3), so its old frames are stale: cleared and rendered again
    assert (one["reused"], one["stale"], one["rendered"], one["done"]) == (0, 2, 3, 3)
    assert (two["reused"], two["rendered"], two["done"]) == (0, 3, 3)
    s = _run(sc, sdir, [("s01", 1, 3), ("s02", 4, 6)])  # everything on disk: all reused
    assert s["frames"] == {"total": 6, "done": 6, "reused": 6, "rendered": 0, "stale": 0, "to_render": 0}
    (sdir / "frames" / "s02" / "0005.png").unlink()
    s = _run(sc, sdir, [("s01", 1, 3), ("s02", 4, 6)])
    assert s["shots"][1]["reused"] == 2 and s["shots"][1]["rendered"] == 1


def test_settings_thumbnails_and_page():
    sc = _scene()
    sdir = Path(tempfile.mkdtemp())
    s = _run(sc, sdir, [("s01", 1, 3)], percent=20)
    st = s["settings"]
    assert st["engine"] == "eevee" and st["resolution"] == [1080, 1920] and st["percent"] == 20
    assert st["samples"] >= 1 and st["motion_blur"] is False and st["device"] == "GPU"
    assert "PNG" in st["output"]
    sh = s["shots"][0]
    assert sh["thumb"] == "render-status/preview-s01.jpg" and sh["checked"] >= 1
    thumb = sdir / "review" / sh["thumb"]
    assert thumb.is_file() and thumb.stat().st_size > 0
    img = bpy.data.images.load(str(thumb))
    assert img.size[0] == 216
    assert s["checks"]["frames_checked"] >= 1 and s["checks"]["errors"] == 0
    assert s["avg_s_per_frame"] > 0 and s["overhead_s"] >= 0
    html = rs.page_path(sdir, "preview").read_text(encoding="utf-8")
    assert "Done" in html and "render-status/preview-s01.jpg" in html
    for lst in (bpy.app.handlers.render_pre, bpy.app.handlers.render_write, bpy.app.handlers.render_cancel):
        assert not any(getattr(fn, "__self__", None).__class__ is Watch for fn in lst)


def test_a_black_frame_is_caught_live():
    sc = _scene(lit=False)
    sdir = Path(tempfile.mkdtemp())
    s = _run(sc, sdir, [("s01", 1, 2)])
    assert s["checks"]["errors"] >= 1, s["checks"]
    f = s["checks"]["findings"][0]
    assert f["check"] == "black_frame" and f["shot"] == "s01" and f["frame"] == 1
    assert s["shots"][0]["errors"] >= 1


def test_overhead_is_small_on_a_full_size_frame():
    """Status writes plus the budgeted checks stay well under 5% of the render time (here an EEVEE frame at full
    size, about the cheapest real frame there is)."""
    sc = _scene()
    sdir = Path(tempfile.mkdtemp())
    t0 = time.perf_counter()
    s = _run(sc, sdir, [("s01", 1, 24)], percent=100)
    wall = time.perf_counter() - t0
    print(f"overhead {s['overhead_s']:.3f} s of {wall:.2f} s ({100 * s['overhead_s'] / wall:.2f}%), "
          f"checks {s['checks']['check_s']:.3f} s, "
          f"{s['checks']['frames_checked']} checked, {s['avg_s_per_frame']:.3f} s/frame")  # fmt: skip
    # the first frame is always checked (0.2-0.3 s at full size, once per render); after it the checks keep to
    # their 3% budget, and the rest (status writes, stuck-frame keys) is per frame
    first = s["checks"]["check_s"] if s["checks"]["frames_checked"] == 1 else 0.3
    assert s["overhead_s"] - first < 0.05 * wall


def test_flicker_is_caught_by_the_live_checks():
    """A bright card that shows for one frame only, in the upper part of the picture: the live checks (imbuf's rows
    run bottom up) report flicker at that frame, placed in the upper half."""
    from bsa.blender.render_watch import check_frame
    from bsa.image_checks import Flicker

    sc = _scene()
    bpy.ops.mesh.primitive_plane_add(size=1.5, location=(0, -4, 1.0), rotation=(1.5708, 0, 0))
    card = bpy.context.object
    mat = bpy.data.materials.new("Glow")
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    out = nodes["Material Output"]
    emit = nodes.new("ShaderNodeEmission")
    emit.inputs["Strength"].default_value = 5.0
    mat.node_tree.links.new(emit.outputs[0], out.inputs["Surface"])
    card.data.materials.append(mat)
    for frame, hidden in ((1, True), (2, True), (3, False), (4, True)):
        card.hide_render = hidden
        card.keyframe_insert("hide_render", frame=frame)
    sdir = Path(tempfile.mkdtemp())
    frames = render.render_range(sc, 1, 5, sdir / "frames", "preview", percent=10, engine="eevee")
    flicker, found = Flicker(), []
    for f, png in enumerate(frames, start=1):
        found += check_frame(png, f, sdir / "thumb.jpg", flicker)[0]
    hits = [f for f in found if f.check == "flicker"]
    assert [f.frame for f in hits] == [3], found
    x0, y0, x1, y1 = hits[0].box
    assert (y0 + y1) / 2 < 0.5 and x0 < 0.5 < x1, hits[0].box  # its glow lights the floor below it too (ray tracing)
