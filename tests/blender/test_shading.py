import math
import tempfile
from pathlib import Path

import bpy
from mathutils import Vector

from bsa.blender import bake, look, pixels, shading


def _scene():
    """A grey 30 x 40 m wall (a hero) facing a portrait camera 80 m in front of it, a dim world, Cycles."""
    bpy.ops.wm.read_factory_settings(use_empty=True)
    sc = bpy.context.scene
    sc.render.engine = "CYCLES"
    sc.cycles.samples = 16
    sc.cycles.use_denoising = False
    sc.render.resolution_x, sc.render.resolution_y, sc.render.resolution_percentage = 1080, 1920, 10
    sc.view_settings.view_transform = "Standard"
    world = bpy.data.worlds.new("w")
    if world.node_tree is None:
        world.use_nodes = True
    world.node_tree.nodes["Background"].inputs["Strength"].default_value = 0.05
    sc.world = world
    cam = bpy.data.objects.new("Cam", bpy.data.cameras.new("Cam"))
    sc.collection.objects.link(cam)
    cam.location = (0, -80, 20)
    cam.rotation_euler = (math.radians(90), 0, 0)
    sc.camera = cam
    bpy.ops.mesh.primitive_plane_add(size=1, location=(0, 0, 20), rotation=(math.radians(90), 0, 0))
    wall = bpy.context.object
    wall.name = "Wall"
    wall.scale = (30, 40, 1)
    mat = look.material("grey", (0.5, 0.5, 0.5, 1.0), roughness=0.8)
    look.assign(wall, mat)
    bake.tag(wall, "hero")
    return sc, wall


def _render(sc):
    out = Path(tempfile.mkdtemp()) / "0001.png"
    sc.render.filepath = str(out)
    sc.render.image_settings.file_format = "PNG"
    bpy.ops.render.render(write_still=True, scene=sc.name)
    return out


def _lights(sc):
    return [o for o in sc.objects if o.type == "LIGHT"]


def test_lights_use_suns_for_big_subjects_and_far_area_lights_for_small_ones():
    sc, _ = _scene()
    big = look.lights(sc, (0, 0, 48), 97.0)
    assert big["kind"] == "sun" and big["distance"] is None
    assert sorted(o.data.type for o in _lights(sc)) == ["SUN"] * 3
    small = look.lights(sc, (0, 0, 0.5), 1.0)  # again: the same three lights, now area lights far enough away
    assert small["kind"] == "area" and len(_lights(sc)) == 3
    assert sorted(o.data.type for o in _lights(sc)) == ["AREA"] * 3
    bpy.context.view_layer.update()
    for o in small["lights"]:
        assert abs((o.location - Vector((0, 0, 0.5))).length - look.AREA_DISTANCE) < 1e-4
        aim = o.matrix_world.to_3x3() @ Vector((0, 0, -1))
        assert aim.dot((Vector((0, 0, 0.5)) - o.location).normalized()) > 0.999  # each points at the subject
    key = next(o for o in small["lights"] if o.name.endswith("Key"))
    assert key.location.y < 0 and key.location.z > 0.5  # in front of the subject, from the camera's side, above


def test_flat_faces_are_the_heros_seen_planes():
    sc, wall = _scene()
    faces = shading.flat_faces(sc, 1, (108, 192))
    assert [n for n, _, _ in faces] == ["Wall"], faces
    rows, cols = faces[0][1], faces[0][2]
    assert len(rows) > 100 and min(rows) >= 0 and max(rows) < 192 and max(cols) < 108
    bpy.ops.mesh.primitive_cube_add(size=8, location=(0, -20, 20))  # hides the wall's middle
    hidden = shading.flat_faces(sc, 1, (108, 192))
    assert len(hidden[0][1]) < len(rows)
    wall["bsa_tags"] = ""
    assert shading.flat_faces(sc, 1, (108, 192)) == []  # not a hero: not checked


def test_close_area_lights_shade_a_tall_wall_and_sun_lamps_do_not():
    sc, wall = _scene()
    data = bpy.data.lights.new("Close", "AREA")
    data.energy, data.size = 6000.0, 4.0
    light = bpy.data.objects.new("Close", data)
    sc.collection.objects.link(light)
    light.location = (0, -12, 42)  # a rig scaled up for the wall, too close: its top far brighter than its base
    light.rotation_euler = (Vector((0, 0, 20)) - light.location).to_track_quat("-Z", "Y").to_euler()
    png = _render(sc)
    found = shading.frame_shading(sc, [(1, png)], None, pixels.read_png)
    assert [(f.check, f.obj) for f in found] == [("shading", "Wall")], found
    assert "light at the top" in found[0].message
    bpy.data.objects.remove(light)
    look.lights(sc, wall, 40.0)
    found = shading.frame_shading(sc, [(1, _render(sc))], None, pixels.read_png)
    assert found == [], found
