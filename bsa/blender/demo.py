"""The demo machine every style is tested and sampled on: a lever on a fulcrum with a crate and a weight, and two
meshing gears on a stand, made with kit materials in the kit's colours, and a 9:16 camera. No floor, lights or world:
a style and a set add those."""

from __future__ import annotations

import math

import bmesh
import bpy
from mathutils import Vector

from bsa import colour
from bsa.blender import bake, look

MODULE = 0.09  # gear tooth size (metres)
GEAR_ANGLE_DEG = 35.0  # the small gear sits this far above the big one's horizontal


def _object(scene, name: str, bm):
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    obj = bpy.data.objects.new(name, me)
    scene.collection.objects.link(obj)
    return obj


def _box(scene, name, size, loc):
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    for v in bm.verts:
        v.co = Vector(c * s for c, s in zip(v.co, size))
    obj = _object(scene, name, bm)
    obj.location = loc
    return obj


def _cylinder(scene, name, radius, depth, loc, along_y=False):
    bm = bmesh.new()
    bmesh.ops.create_cone(bm, cap_ends=True, segments=48, radius1=radius, radius2=radius, depth=depth)
    obj = _object(scene, name, bm)
    if along_y:
        obj.rotation_euler = (math.radians(90), 0, 0)
    obj.location = loc
    return obj


def _extrude(scene, name, outline, depth, loc):
    """A flat outline in XZ, `depth` thick along Y."""
    bm = bmesh.new()
    front = [bm.verts.new((x, -depth / 2, z)) for x, z in outline]
    back = [bm.verts.new((x, depth / 2, z)) for x, z in outline]
    bm.faces.new(front[::-1])
    bm.faces.new(back)
    for i in range(len(outline)):
        j = (i + 1) % len(outline)
        bm.faces.new((front[i], front[j], back[j], back[i]))
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    obj = _object(scene, name, bm)
    obj.location = loc
    return obj


def _gear(scene, name, teeth, thick, loc, turn):
    pitch_r = MODULE * teeth / 2
    tip, root = pitch_r + MODULE, pitch_r - 1.25 * MODULE
    step = 2 * math.pi / teeth
    pts = []
    for k in range(teeth):
        for da, rr in ((-0.30, root), (-0.15, tip), (0.15, tip), (0.30, root)):
            a = k * step + da * step
            pts.append((rr * math.cos(a), rr * math.sin(a)))
    obj = _extrude(scene, name, pts, thick, loc)
    obj.rotation_euler = (0, -turn, 0)
    return obj, pitch_r


def lever(scene, kit) -> dict:
    """Build the demo machine in `scene` at z >= 0 with kit materials (crate "toy" in the kit's `spark` colour, lever
    `flow`, weight `grip`, gears, stand, fulcrum and axles matte) and `category:` tags, and a 9:16 camera (lens 50).
    Returns {"target", "size", "key", "probe"} for styles.apply and the look tests."""
    mats = {
        "Crate": look.kit_material("Crate", "toy", kit.colour("spark")),
        "Lever": look.kit_material("Lever", "plastic", kit.colour("flow")),
        "Weight": look.kit_material("Weight", "metal", kit.colour("grip")),
        "GearBig": look.kit_material("GearBig", "matte", kit.palette["highlight"]),
        "GearSmall": look.kit_material("GearSmall", "matte", kit.palette["accent"]),
        "Stand": look.kit_material("Stand", "matte", colour.mix(kit.palette["background"], kit.palette["ink"], 0.2)),
        "Ink": look.kit_material("Ink", "matte", kit.palette["ink"]),
    }
    fulcrum = _extrude(scene, "Fulcrum", [(-0.375, -0.31), (0.375, -0.31), (0, 0.31)], 0.5, (0, 0, 0.31))
    pivot = bpy.data.objects.new("LeverPivot", None)
    scene.collection.objects.link(pivot)
    pivot.location = (0, 0, 0.68)
    pivot.rotation_euler = (0, math.radians(11), 0)
    beam = _box(scene, "Lever", (3.6, 0.42, 0.12), (0, 0, 0))
    crate = _box(scene, "Crate", (0.62, 0.62, 0.62), (-1.38, 0, 0.37))
    weight = _cylinder(scene, "Weight", 0.26, 0.36, (1.42, 0, 0.24))
    for obj in (beam, crate, weight):
        obj.parent = pivot
    base = _box(scene, "StandBase", (2.7, 0.7, 0.22), (0.35, 1.5, 0.11))
    plate = _box(scene, "StandPlate", (2.5, 0.12, 2.75), (0.35, 1.72, 1.595))
    turn = math.radians(GEAR_ANGLE_DEG)
    c1 = Vector((-0.3, 1.5, 1.55))
    big, r1 = _gear(scene, "GearBig", 16, 0.16, c1, turn)
    c2 = c1 + Vector((math.cos(turn), 0, math.sin(turn))) * (r1 + MODULE * 10 / 2)
    small, _ = _gear(scene, "GearSmall", 10, 0.16, c2, turn + math.pi + math.pi / 10)
    axles = [_cylinder(scene, f"Axle{i}", r, 0.36, (c.x, 1.48, c.z), along_y=True)
             for i, (c, r) in enumerate(((c1, 0.11), (c2, 0.08)))]  # fmt: skip
    for obj, mat in ((crate, "Crate"), (beam, "Lever"), (weight, "Weight"), (big, "GearBig"), (small, "GearSmall"),
                     (base, "Stand"), (plate, "Stand"), (fulcrum, "Ink"), *((a, "Ink") for a in axles)):  # fmt: skip
        look.assign(obj, mats[mat])
    bake.tag(crate, "category:spark")
    bake.tag(beam, "category:flow")
    bake.tag(weight, "category:grip")

    cam_data = bpy.data.cameras.new("Camera")
    cam_data.lens = 50
    cam_data.sensor_fit = "HORIZONTAL"
    cam_data.sensor_width = 36
    cam_data.shift_y = -0.13
    cam = bpy.data.objects.new("Camera", cam_data)
    scene.collection.objects.link(cam)
    cam.location = (0.15, -6.3, 3.7)
    cam.rotation_euler = (Vector((0.15, 0.7, 1.15)) - cam.location).to_track_quat("-Z", "Y").to_euler()
    scene.camera = cam
    return {"target": "Lever", "size": 3.6, "key": ["Crate"], "probe": "Crate"}
