"""Sample build for a one-take short: one pendulum swinging in a workshop, filmed in one continuous camera move.
Follows the build.py contract and its one-take pattern in skills/make-short/playbook/06-build.md.

The camera path is driven from the windows (bsa.timeline.window_frames: each shot's `at` on the camera path), never
from the narration: s01 pushes in from the whole pendulum to its bob with camera.move, then s02 and s03 track the
swinging bob with camera.follow. Each stretch starts where the last one ended, so the camera never cuts.
"""

import json
import math
import os
import sys
from pathlib import Path

if "--" in sys.argv:  # script mode: blender -b --python build.py -- PLUGIN_ROOT SHORT_DIR
    PLUGIN_ROOT, SHORT_DIR = sys.argv[sys.argv.index("--") + 1 :][:2]
    sys.path.insert(0, PLUGIN_ROOT)

import bmesh
import bpy
import numpy as np
from mathutils import Matrix, Vector

from bsa.blender import bake, camera, look
from bsa.blender import scene as bsa_scene
from bsa.fields.physics.systems import G, pendulum
from bsa.fields.physics.verify import period_from_series
from bsa.short import load_short
from bsa.timeline import timeline_hash, window_frames

STYLE = int(os.environ.get("BSA_STYLE", "0") or 0)
ENGINE = "eevee"  # the engine approved at the engine stage

SWING_RAD = 0.25  # release angle: a small swing, where the period is set by length alone
STAND_TOP = 1.26  # underside of the stand's crossbar, where the pendulum hangs (m)
LENGTH = 0.6  # pivot to the bob's centre (m)
BOB_RADIUS = 0.06
ROD_RADIUS = 0.004

STYLES = [
    {"wall": (0.045, 0.05, 0.06), "wood": (0.16, 0.075, 0.03), "brass": (0.92, 0.66, 0.30), "key": (1.0, 0.82, 0.6)},
    {"wall": (0.55, 0.58, 0.62), "wood": (0.55, 0.40, 0.25), "brass": (0.85, 0.66, 0.30), "key": (1.0, 0.97, 0.92)},
]


def swing(frames: np.ndarray, fps: int) -> np.ndarray:
    """The pendulum's angle (rad) at each frame, released at frame 1. Checked against the sourced period."""
    t_end = (frames[-1] - 1) / fps
    dt = 1 / 300
    sim = pendulum(LENGTH, SWING_RAD, dt=dt, n=max(math.ceil(t_end / dt) + 1, 1500))
    sourced = 2 * math.pi * math.sqrt(LENGTH / G)  # small-swing period (fact f-period)
    simulated = period_from_series(sim["t"], sim["theta"])
    if abs(simulated / sourced - 1) > 0.01:
        raise RuntimeError(f"pendulum model off: {simulated:.3f} s simulated vs {sourced:.3f} s sourced")
    return np.interp((frames - 1) / fps, sim["t"], sim["theta"])


def link(sc, obj):
    sc.collection.objects.link(obj)
    return obj


def mesh_object(sc, name: str, bm):
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    return link(sc, bpy.data.objects.new(name, me))


def box(bm, lo, hi) -> None:
    centre = [(a + b) / 2 for a, b in zip(lo, hi, strict=True)]
    size = [b - a for a, b in zip(lo, hi, strict=True)]
    res = bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=size, verts=res["verts"])
    bmesh.ops.translate(bm, vec=centre, verts=res["verts"])


def floor_and_wall(sc, style):
    """A floor (z = 0) sweeping up into a back wall: one smooth surface."""
    radius, wall_y, steps = 0.6, 0.9, 16
    profile = [(-4.0, 0.0)]
    for i in range(steps + 1):
        phi = math.pi / 2 * i / steps
        profile.append((wall_y - radius + radius * math.sin(phi), radius - radius * math.cos(phi)))
    profile.append((wall_y, 4.0))
    bm = bmesh.new()
    left = [bm.verts.new((-4.0, y, z)) for y, z in profile]
    right = [bm.verts.new((4.0, y, z)) for y, z in profile]
    for i in range(len(profile) - 1):
        bm.faces.new((left[i], left[i + 1], right[i + 1], right[i]))
    bm.normal_update()
    if next(iter(bm.faces)).normal.z < 0:  # the floor faces up (the stand rests on it)
        bmesh.ops.reverse_faces(bm, faces=bm.faces)
    me = bpy.data.meshes.new("Workshop floor")
    bm.to_mesh(me)
    bm.free()
    room = link(sc, bpy.data.objects.new("Workshop floor", me))
    look.smooth(room, 30)
    look.assign(room, look.material("Workshop wall", style["wall"], roughness=0.85, bump_scale=0.3))


def lights_and_world(sc, style):
    world = bpy.data.worlds.new(f"{sc.name} world")
    if world.node_tree is None:  # Blender < 5.0 makes worlds without nodes
        world.use_nodes = True
    bg = world.node_tree.nodes.get("Background")
    bg.inputs["Color"].default_value = (*style["wall"], 1.0)
    bg.inputs["Strength"].default_value = 0.4
    sc.world = world
    for name, at, energy, color in (("Key", (-1.6, -1.8, 2.4), 150.0, style["key"]),
                                    ("Rim", (1.4, 1.2, 2.0), 200.0, (0.75, 0.85, 1.0))):  # fmt: skip
        data = bpy.data.lights.new(f"{sc.name} {name}", "AREA")
        data.energy, data.color, data.size = energy, color, 1.0
        obj = link(sc, bpy.data.objects.new(f"{sc.name} {name}", data))
        obj.location = at
        obj.rotation_euler = (Vector((0.0, 0.0, 0.9)) - obj.location).to_track_quat("-Z", "Y").to_euler()


def build_workshop(sc, short, n_frames):
    style = STYLES[STYLE % len(STYLES)]
    floor_and_wall(sc, style)
    wood = look.material("Workshop wood", style["wood"], roughness=0.55, bump_scale=0.15)
    brass = look.material("Workshop brass", style["brass"], roughness=0.28, kind="metal", bump_scale=0.05)
    steel = look.material("Workshop steel", (0.62, 0.63, 0.65), roughness=0.35, kind="metal")

    bm = bmesh.new()  # the stand: base, two posts, crossbar (one wooden piece)
    box(bm, (-0.4, -0.16, 0.0), (0.4, 0.16, 0.04))
    for x in (-0.36, 0.32):
        box(bm, (x, -0.02, 0.04), (x + 0.04, 0.02, STAND_TOP))
    box(bm, (-0.4, -0.025, STAND_TOP), (0.4, 0.025, STAND_TOP + 0.05))
    stand = mesh_object(sc, "Stand", bm)
    look.smooth(stand, 30)
    look.bevel(stand, 0.004)
    look.assign(stand, wood)
    bake.tag(stand, "grounded")

    # The rod hangs from the crossbar (its origin is the pivot, so it swings about it); the bob is its child.
    rim = ROD_RADIUS / BOB_RADIUS
    end = LENGTH - BOB_RADIUS * math.sqrt(1.0 - rim * rim)  # the rod ends where it meets the bob's surface
    bm = bmesh.new()
    bmesh.ops.create_cone(bm, cap_ends=True, segments=24, radius1=ROD_RADIUS, radius2=ROD_RADIUS, depth=end)
    rod = mesh_object(sc, "Pendulum rod", bm)
    rod.data.transform(Matrix.Translation((0, 0, -end / 2)))
    rod.location = (0.0, 0.0, STAND_TOP)
    look.smooth(rod, 30)
    look.assign(rod, steel)
    bake.tag(rod, "contact:Stand")  # its top end hangs against the crossbar's underside
    bm = bmesh.new()
    bmesh.ops.create_uvsphere(bm, u_segments=48, v_segments=24, radius=BOB_RADIUS)
    bob = mesh_object(sc, "Pendulum bob", bm)
    bob.parent = rod
    bob.location = (0, 0, -LENGTH)
    look.smooth(bob, 60)
    look.assign(bob, brass)
    bake.tag(bob, "hero")
    bake.keyframe_series(rod, "rotation_euler", 1, swing(np.arange(1, n_frames + 1), short.fps), frame0=1)

    lights_and_world(sc, style)
    return rod, bob


def drive_camera(sc, short, rod, bob):
    """The one take, per window: frames come from window_frames (the same rounding as the timeline's shot spans),
    never from line times, so redoing the narration never moves the camera."""
    windows = window_frames(short)
    (a0, a1), (b0, b1), (c0, c1) = windows["s01"], windows["s02"], windows["s03"]
    data = bpy.data.cameras.new("Workshop camera")
    data.lens = 50.0
    cam = link(sc, bpy.data.objects.new("Workshop camera", data))
    sc.camera = cam
    cam.location = (0.6, -3.2, 1.1)
    cam.rotation_euler = (Vector((0.0, 0.0, 0.9)) - cam.location).to_track_quat("-Z", "Y").to_euler()
    # s01: the whole pendulum through its swing, then a push-in to the bob
    camera.frame_objects(cam, [rod, bob], frames=list(range(a0, a1 + 1, 6)), key=a0, shot="s01")
    camera.move(cam, [rod, bob], a0, a1, orbit=-15, to=[bob], shot="s01")
    # s02 and s03: the camera stays where the push-in ended and turns to keep the swinging bob in the band
    for shot, (f0, f1) in (("s02", (b0, b1)), ("s03", (c0, c1))):
        sc.frame_set(f0)  # the pose the last stretch ended on: follow starts from the camera's pose now
        camera.follow(cam, bob, f0, f1, mode="track", shot=shot)


def build(short_dir):
    short_dir = Path(short_dir)
    short = load_short(short_dir / "source" / "short.json")
    tl = json.loads((short_dir / "source" / "timeline.json").read_text(encoding="utf-8"))
    names = sorted({s.scene for s in short.shots})
    bsa_scene.remove_scenes(names)  # and their old objects, so a rebuild keeps exact names (contact tags use them)
    (name,) = names  # a one-take short: one scene, one camera
    sc = bpy.data.scenes.new(name)
    bsa_scene.setup_scene(sc, short, ENGINE, "preview")
    rod, bob = build_workshop(sc, short, tl["n_frames"])
    drive_camera(sc, short, rod, bob)
    sc.frame_start, sc.frame_end = 1, tl["n_frames"]
    sc["bsa_timeline"] = timeline_hash(tl)  # the Blender commands refuse a scene built for another timing
    out = short_dir / "blend" / "short.blend"
    out.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(out))


if __name__ == "__main__" and "--" in sys.argv:
    build(SHORT_DIR)
