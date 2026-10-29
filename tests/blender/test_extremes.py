import math
import tempfile
from pathlib import Path

import bpy

from bsa.blender import bake, extremes
from bsa.blender import scene as scene_mod
from bsa.short import Short


def _scene():
    """Rod (hero in s01 only) slides along x: 0 at frame 1, max 2 at frame 6, min -2 at frame 16, back to 0 at 21.
    Its child Head turns about z between frames 1 and 21. Box is no hero and moves too."""
    bpy.ops.wm.read_factory_settings(use_empty=True)
    sc = bpy.context.scene
    scene_mod.setup_scene(sc, Short(slug="t", title="t", length_s=1.0), "eevee", "still")
    rod = bpy.data.objects.new("Rod", bpy.data.meshes.new("Rod"))
    sc.collection.objects.link(rod)
    bake.keyframe_series(rod, "location", 0, [2 * math.sin(2 * math.pi * (f - 1) / 20) for f in range(1, 22)])
    bake.tag(rod, "hero:s01")
    head = bpy.data.objects.new("Head", bpy.data.meshes.new("Head"))
    sc.collection.objects.link(head)
    head.parent = rod
    bake.keyframe_series(head, "rotation_euler", 2, [0.0, 1.0, 0.5], frame0=1, step=10)
    box = bpy.data.objects.new("Box", bpy.data.meshes.new("Box"))
    sc.collection.objects.link(box)
    bake.keyframe_series(box, "location", 2, [0.0, 5.0], frame0=1, step=20)
    cam = bpy.data.objects.new("Cam", bpy.data.cameras.new("Cam"))
    sc.collection.objects.link(cam)
    cam.location, cam.rotation_euler = (0, -8, 0), (1.5708, 0, 0)
    sc.camera = cam
    return sc


def test_extreme_frames_of_heroes_and_their_moving_parts():
    sc = _scene()
    got = extremes.extreme_frames(sc, "s01", 1, 21)
    assert got[6] == ["Rod location[0] max"], got
    assert got[16] == ["Rod location[0] min"], got
    assert "Head rotation_euler[2] max" in got[11] and "Head rotation_euler[2] min" in got[1], got
    assert not any("Box" in why for whys in got.values() for why in whys)
    assert extremes.extreme_frames(sc, "s02", 1, 21) == {}  # Rod is a hero in s01 only
    part = extremes.extreme_frames(sc, "s01", 8, 21)  # within the shot only: x falls from frame 8 to its min at 16
    assert part[16] == ["Rod location[0] min"] and part[8] == ["Rod location[0] max"], part


def test_render_extremes_writes_one_still_per_frame():
    sc = _scene()
    out = Path(tempfile.mkdtemp())
    rows = extremes.render_extremes(sc, "s01", 1, 21, out, percent=5)
    frames = [r["frame"] for r in rows]
    assert frames == sorted(set(frames)) and 6 in frames and 16 in frames
    for r in rows:
        assert Path(r["path"]).name == f"s01-f{r['frame']:04d}.jpg" and Path(r["path"]).stat().st_size > 0
        assert r["why"]


def test_extreme_frames_follow_a_hero_moved_by_an_untagged_parent_or_constraint():
    """A hero with no keys of its own, swung by a keyed (untagged) pivot, or moved by a constraint, still has
    extremes: its world position and turn at the ends of the swing."""
    bpy.ops.wm.read_factory_settings(use_empty=True)
    sc = bpy.context.scene
    pivot = bpy.data.objects.new("Pivot", None)
    sc.collection.objects.link(pivot)
    pivot.location = (0, 0, 2)
    bake.keyframe_series(pivot, "rotation_euler", 1, [0.5 * math.sin(2 * math.pi * (f - 1) / 20) for f in range(1, 22)])
    ball = bpy.data.objects.new("Ball", bpy.data.meshes.new("Ball"))
    sc.collection.objects.link(ball)
    ball.parent = pivot
    ball.location = (0, 0, -1.5)
    bake.tag(ball, "hero")
    got = extremes.extreme_frames(sc, "s01", 1, 21)
    flat = [w for whys in got.values() for w in whys]
    assert any(w.startswith("Ball world location[0]") and w.endswith("max") for w in got.get(6, [])) or any(
        w.startswith("Ball world location[0]") and w.endswith("max") for w in got.get(16, [])), got
    assert any("Pivot" in w for w in flat), got  # names what moves it
    assert not any(w.startswith("Pivot ") for w in flat), got  # the pivot itself is no hero

    rider = bpy.data.objects.new("Rider", bpy.data.meshes.new("Rider"))
    sc.collection.objects.link(rider)
    bake.tag(rider, "hero")
    con = rider.constraints.new("COPY_LOCATION")
    con.target = ball
    got = extremes.extreme_frames(sc, "s01", 1, 21)
    assert any(w.startswith("Rider world location") for whys in got.values() for w in whys), got
    assert sc.frame_current == 1
