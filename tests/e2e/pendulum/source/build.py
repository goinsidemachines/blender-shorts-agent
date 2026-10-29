"""Sample build for the pendulum short: a pendulum stand in a workshop (shots s01, s02) and a clock escapement
(shot s03). Follows the build.py contract in skills/make-short/playbook/06-build.md.

Motion comes from bsa.fields.physics.systems.pendulum (full nonlinear model), checked against the sourced
small-swing period 2*pi*sqrt(L/g) before it is baked. The escape wheel's step-per-swing is simplified (shot s03
carries the "Motion simplified" badge).
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

from bsa.blender import bake, look
from bsa.blender import scene as bsa_scene
from bsa.fields.physics.systems import G, pendulum
from bsa.fields.physics.verify import period_from_series
from bsa.short import load_short
from bsa.timeline import timeline_hash

STYLE = int(os.environ.get("BSA_STYLE", "0") or 0)
ENGINE = "eevee"  # the engine approved at the engine stage

SWING_RAD = 0.2  # release angle: a small swing, where the period is set by length alone
STAND_TOP = 1.26  # underside of the stand's crossbar, where the pendulums hang (m)
# Workshop pendulums: (name, x, length m, bob radius m). A and B: same length, different weight; C: four times longer.
PENDULUMS = [("A", -0.35, 0.25, 0.040), ("B", -0.15, 0.25, 0.060), ("C", 0.20, 1.00, 0.050)]
CLOCK_LENGTH = 0.30
WHEEL_TEETH = 30
ROD_RADIUS = 0.004
BOARD_FRONT_Y = 0.0  # the clock backboard's front face
PIN_FRONT_Y = -0.037  # front end of the pivot pin and the wheel arbor, which stand out from the board
ANCHOR_HUB = 0.012  # half-size of the anchor's hub around the pivot; the clock rod hangs from its underside

# Style options for the story page's thumbnails: (wall, wood, brass, key light warmth).
STYLES = [
    {"wall": (0.045, 0.05, 0.06), "wood": (0.16, 0.075, 0.03), "brass": (0.92, 0.66, 0.30), "key": (1.0, 0.82, 0.6)},
    {"wall": (0.55, 0.58, 0.62), "wood": (0.55, 0.40, 0.25), "brass": (0.85, 0.66, 0.30), "key": (1.0, 0.97, 0.92)},
]


# --------------------------------------------------------------------------- physics


def swing(length_m: float, frames: np.ndarray, f0: int, fps: int) -> np.ndarray:
    """Pendulum angle (rad) at each frame; frame f0 is the release (t = 0). Checked against the sourced period."""
    t_end = (frames[-1] - f0) / fps
    dt = 1 / 300
    sim = pendulum(length_m, SWING_RAD, dt=dt, n=max(math.ceil(t_end / dt) + 1, 1500))
    sourced = 2 * math.pi * math.sqrt(length_m / G)  # small-swing period (fact f-period)
    simulated = period_from_series(sim["t"], sim["theta"])
    if abs(simulated / sourced - 1) > 0.01:
        raise RuntimeError(f"pendulum model off: {simulated:.3f} s simulated vs {sourced:.3f} s sourced")
    return np.interp((frames - f0) / fps, sim["t"], sim["theta"])


# --------------------------------------------------------------------------- geometry helpers


def link(sc, obj):
    sc.collection.objects.link(obj)
    return obj


def mesh_object(sc, name: str, bm) -> bpy.types.Object:
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    return link(sc, bpy.data.objects.new(name, me))


def box(bm, lo, hi) -> list:
    """Add an axis-aligned box (lo, hi corners) to bm; returns its vertices."""
    centre = [(a + b) / 2 for a, b in zip(lo, hi, strict=True)]
    size = [b - a for a, b in zip(lo, hi, strict=True)]
    res = bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=size, verts=res["verts"])
    bmesh.ops.translate(bm, vec=centre, verts=res["verts"])
    return res["verts"]


def cylinder(sc, name: str, radius: float, depth: float, axis: str = "Z", segments: int = 32):
    """Closed cylinder along `axis`, centred on its origin."""
    bm = bmesh.new()
    bmesh.ops.create_cone(bm, cap_ends=True, segments=segments, radius1=radius, radius2=radius, depth=depth)
    if axis == "Y":
        bmesh.ops.rotate(bm, verts=bm.verts, cent=(0, 0, 0), matrix=Matrix.Rotation(math.radians(90), 3, "X"))
    obj = mesh_object(sc, name, bm)
    look.smooth(obj, 30)
    return obj


def sphere(sc, name: str, radius: float, squash_y: float = 1.0):
    bm = bmesh.new()
    bmesh.ops.create_uvsphere(bm, u_segments=48, v_segments=24, radius=radius)
    if squash_y != 1.0:
        bmesh.ops.scale(bm, vec=(1.0, squash_y, 1.0), verts=bm.verts)
    obj = mesh_object(sc, name, bm)
    look.smooth(obj, 60)
    return obj


def cyclorama(sc, name: str, width: float, depth: float, height: float, wall_y: float, radius: float = 0.6,
              steps: int = 16):
    """Floor (z = 0, from y = -depth) sweeping up into a back wall at y = wall_y: one smooth surface."""
    profile = [(-depth, 0.0)]
    for i in range(steps + 1):
        phi = math.pi / 2 * i / steps
        profile.append((wall_y - radius + radius * math.sin(phi), radius - radius * math.cos(phi)))
    profile.append((wall_y, height))
    bm = bmesh.new()
    left = [bm.verts.new((-width / 2, y, z)) for y, z in profile]
    right = [bm.verts.new((width / 2, y, z)) for y, z in profile]
    faces = [bm.faces.new((left[i], left[i + 1], right[i + 1], right[i])) for i in range(len(profile) - 1)]
    bm.normal_update()
    if faces[0].normal.z < 0:  # the floor must face up (the stand's base rests on it)
        bmesh.ops.reverse_faces(bm, faces=bm.faces)
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    obj = link(sc, bpy.data.objects.new(name, me))
    look.smooth(obj, 30)
    return obj


def gear(sc, name: str, teeth: int, r_root: float, r_tip: float, thickness: float):
    """Escape-wheel style gear (sawtooth teeth) in the xz plane, thickness along y, centred on its origin."""
    outline = []
    step = 2 * math.pi / teeth
    for k in range(teeth):
        a = k * step
        outline += [(r_root, a), (r_tip, a + 0.55 * step), (r_tip * 0.97, a + 0.7 * step), (r_root, a + 0.85 * step)]
    bm = bmesh.new()
    front = [bm.verts.new((r * math.cos(a), -thickness / 2, r * math.sin(a))) for r, a in outline]
    back = [bm.verts.new((r * math.cos(a), thickness / 2, r * math.sin(a))) for r, a in outline]
    bm.faces.new(front)
    bm.faces.new(list(reversed(back)))
    n = len(outline)
    for i in range(n):
        j = (i + 1) % n
        bm.faces.new((front[i], front[j], back[j], back[i]))
    obj = mesh_object(sc, name, bm)
    look.smooth(obj, 30)
    return obj


# --------------------------------------------------------------------------- scene pieces


def lights_and_world(sc, style, key_at, rim_at, target, power=1.0, world_strength=0.4):
    """A warm key, a cool rim behind the subject, and the style's wall colour as a dim world (power scales both
    lights; a close-up needs less)."""
    world = bpy.data.worlds.new(f"{sc.name} world")
    if world.node_tree is None:  # Blender < 5.0 makes worlds without nodes
        world.use_nodes = True
    bg = world.node_tree.nodes.get("Background")
    bg.inputs["Color"].default_value = (*style["wall"], 1.0)
    bg.inputs["Strength"].default_value = world_strength
    sc.world = world
    for name, at, energy, color, size in (("Key", key_at, 150.0 * power, style["key"], 1.2),
                                          ("Rim", rim_at, 200.0 * power, (0.75, 0.85, 1.0), 0.6)):  # fmt: skip
        data = bpy.data.lights.new(f"{sc.name} {name}", "AREA")
        data.energy, data.color, data.size = energy, color, size
        obj = link(sc, bpy.data.objects.new(f"{sc.name} {name}", data))
        obj.location = at
        aim(obj, target)


def aim(obj, target) -> None:
    direction = Vector(target) - obj.location
    obj.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()


def camera(sc, name: str, lens: float = 50.0):
    data = bpy.data.cameras.new(name)
    data.lens = lens
    cam = link(sc, bpy.data.objects.new(name, data))
    sc.camera = cam
    return cam


def camera_key(cam, frame: int, at, target) -> None:
    cam.location = at
    aim(cam, target)
    cam.keyframe_insert("location", frame=frame)
    cam.keyframe_insert("rotation_euler", frame=frame)


def bake_swing(rod, thetas: np.ndarray, f0: int) -> None:
    bake.keyframe_series(rod, "rotation_euler", 1, thetas, frame0=f0)


def hang(sc, prefix: str, pivot, length: float, bob_radius: float, mats, rod_contact: str | None, squash_y=1.0,
         top: float = 0.0):
    """Rod hanging from `pivot` (its origin, so it swings about it) with the bob parented to it, its centre `length`
    below the pivot. The rod runs from `top` below the pivot to the bob's surface: its end sits where the rim of the
    rod meets the (y-squashed) sphere, so it neither stops short nor runs into the bob."""
    rim = ROD_RADIUS / (bob_radius * min(1.0, squash_y))
    end = length - bob_radius * math.sqrt(1.0 - rim * rim)
    rod = cylinder(sc, f"{prefix} rod", ROD_RADIUS, end - top)
    rod.data.transform(Matrix.Translation((0, 0, -(top + end) / 2)))  # origin at the pivot
    rod.location = pivot
    look.assign(rod, mats["steel"])
    if rod_contact:
        bake.tag(rod, f"contact:{rod_contact}")  # the rod's top end hangs against its underside
    bob = sphere(sc, f"{prefix} bob", bob_radius, squash_y)
    bob.parent = rod
    bob.location = (0, 0, -length)
    look.assign(bob, mats["brass"])
    return rod, bob


def materials(prefix: str, style) -> dict:
    return {
        "wood": look.material(f"{prefix} wood", style["wood"], roughness=0.55, bump_scale=0.15),
        "brass": look.material(f"{prefix} brass", style["brass"], roughness=0.28, kind="metal", bump_scale=0.05),
        "steel": look.material(f"{prefix} steel", (0.62, 0.63, 0.65), roughness=0.35, kind="metal"),
    }


# --------------------------------------------------------------------------- scenes


def build_workshop(sc, short, spans, fps):
    style = STYLES[STYLE % len(STYLES)]
    mats = materials("Workshop", style)
    room = cyclorama(sc, "Workshop floor", width=8.0, depth=4.0, height=4.0, wall_y=0.9)
    look.assign(room, look.material("Workshop wall", style["wall"], roughness=0.85, bump_scale=0.3))

    bm = bmesh.new()  # the stand: base, two posts, crossbar (one wooden piece)
    box(bm, (-0.56, -0.16, 0.0), (0.56, 0.16, 0.04))
    for x in (-0.52, 0.48):
        box(bm, (x, -0.02, 0.04), (x + 0.04, 0.02, STAND_TOP))
    box(bm, (-0.56, -0.025, STAND_TOP), (0.56, 0.025, STAND_TOP + 0.05))
    stand = mesh_object(sc, "Stand", bm)
    look.smooth(stand, 30)
    look.bevel(stand, 0.004)
    look.assign(stand, mats["wood"])
    bake.tag(stand, "grounded")

    f0 = min(spans[s.id][0] for s in short.shots if s.scene == sc.name)
    f1 = max(spans[s.id][1] for s in short.shots if s.scene == sc.name)
    frames = np.arange(f0, f1 + 1)
    for name, x, length, radius in PENDULUMS:
        rod, bob = hang(sc, f"Pendulum {name}", (x, 0.0, STAND_TOP), length, radius, mats, "Stand")
        bake_swing(rod, swing(length, frames, f0, fps), f0)
        if name in ("A", "B"):
            bake.tag(bob, "hero")

    cam = camera(sc, "Workshop camera")
    s01, s02 = spans["s01"], spans["s02"]
    close = (-0.24, 0.0, 1.08)  # pendulums A and B, the two of the same length
    wide = (0.0, 0.0, 0.70)  # the whole stand, with the long pendulum C
    camera_key(cam, s01[0], (-0.24, -1.5, 1.14), close)
    camera_key(cam, s01[1], (-0.24, -1.35, 1.12), close)  # a slow push-in on the hook
    camera_key(cam, s02[0] + 6, (-0.24, -1.35, 1.12), close)  # hold, then pull back to show all three
    camera_key(cam, min(s02[0] + 45, s02[1] - 1), (0.0, -3.1, 0.95), wide)
    camera_key(cam, s02[1], (0.0, -3.0, 0.95), wide)
    lights_and_world(sc, style, key_at=(-1.6, -1.8, 2.4), rim_at=(1.4, 1.2, 2.0), target=(0.0, 0.0, 0.9))


def build_clock(sc, short, spans, fps):
    style = STYLES[STYLE % len(STYLES)]
    mats = materials("Clock", style)
    pivot_z = 0.0
    board = mesh_object(sc, "Clock backboard", _board_bm())
    look.smooth(board, 30)
    look.bevel(board, 0.004)
    look.assign(board, mats["wood"])

    pin_length = BOARD_FRONT_Y - PIN_FRONT_Y  # from its front end back to the board's face, not into the board
    pin = cylinder(sc, "Pivot pin", 0.005, pin_length, axis="Y")
    pin.location = (0.0, PIN_FRONT_Y + pin_length / 2, pivot_z)
    look.assign(pin, mats["steel"])
    bake.tag(pin, "contact:Clock backboard")  # its back end stands on the board's front face

    wheel_z = pivot_z + 0.115
    arbor = cylinder(sc, "Wheel arbor", 0.005, pin_length, axis="Y")
    arbor.location = (0.0, PIN_FRONT_Y + pin_length / 2, wheel_z)
    look.assign(arbor, mats["steel"])
    bake.tag(arbor, "contact:Clock backboard")  # its back end stands on the board's front face
    wheel = gear(sc, "Escape wheel", WHEEL_TEETH, 0.062, 0.08, 0.005)
    wheel.location = (0.0, -0.022, wheel_z)
    look.bevel(wheel, 0.0006, segments=2)
    look.assign(wheel, mats["brass"])
    # The arbor runs through the wheel's hub (the bore is not modelled): as deep as the arbor's 5 mm radius.
    bake.tag(wheel, "hero", "contact:Wheel arbor:0.006")

    # The clock rod hangs from the anchor's hub (its parent-child partner), not from the pin.
    rod, _bob = hang(sc, "Clock pendulum", (0.0, -0.03, pivot_z), CLOCK_LENGTH, 0.045, mats, None, 0.35,
                     top=ANCHOR_HUB)
    anchor = mesh_object(sc, "Anchor", _anchor_bm())
    anchor.parent = rod
    look.smooth(anchor, 30)
    look.bevel(anchor, 0.0008, segments=2)
    look.assign(anchor, mats["steel"])
    # The anchor swings on the pin, which runs through its hub (the bore is not modelled): as deep as the pin's
    # 5 mm radius.
    bake.tag(anchor, "contact:Pivot pin:0.006")

    (f0, f1) = spans["s03"]
    frames = np.arange(f0, f1 + 1)
    thetas = swing(CLOCK_LENGTH, frames, f0, fps)
    bake_swing(rod, thetas, f0)
    # Simplified escapement: the wheel advances half a tooth each time the pendulum passes an end of its swing.
    ticks = np.cumsum(np.r_[0, np.diff(np.sign(np.gradient(thetas))) != 0])
    angle = -ticks * (math.pi / WHEEL_TEETH)
    eased = np.convolve(np.pad(angle, (2, 2), mode="edge"), np.ones(5) / 5, mode="valid")
    bake.keyframe_series(wheel, "rotation_euler", 1, eased, frame0=f0)

    cam = camera(sc, "Clock camera", lens=60.0)
    camera_key(cam, f0, (0.04, -1.45, -0.10), (0.0, 0.0, -0.12))  # wheel to bob, clear of the caption area
    camera_key(cam, f1, (0.02, -1.35, -0.10), (0.0, 0.0, -0.12))
    lights_and_world(sc, style, key_at=(-0.8, -1.0, 0.8), rim_at=(0.6, -0.3, 0.7), target=(0.0, 0.0, 0.0),
                     power=0.25)


def _board_bm():
    bm = bmesh.new()
    box(bm, (-0.32, 0.0, -0.62), (0.32, 0.03, 0.42))
    return bm


def _anchor_bm():
    """Anchor arms from the pivot up to two pallets either side of the escape wheel's lower rim (local to the rod's
    pivot), 2.5 mm in front of the wheel so they never touch it."""
    bm = bmesh.new()
    y0, y1 = -0.004, 0.004
    tuck = 0.0005  # the arms are a little thinner, so their faces sit inside the hub and pallets, not on their faces
    box(bm, (-ANCHOR_HUB, y0, -ANCHOR_HUB), (ANCHOR_HUB, y1, ANCHOR_HUB))  # hub around the pivot
    for side in (-1, 1):
        # each arm starts inside the hub, 1 cm out from the pivot, where the two arms are already apart
        arm = box(bm, (-0.004, y0 + tuck, 0.01), (0.004, y1 - tuck, 0.07))
        bmesh.ops.rotate(bm, verts=arm, cent=(0, 0, 0), matrix=Matrix.Rotation(side * 0.55, 3, "Y"))
        tip_x, tip_z = side * 0.07 * math.sin(0.55), 0.07 * math.cos(0.55)
        box(bm, (tip_x - 0.006, y0, tip_z - 0.004), (tip_x + 0.006, y1, tip_z + 0.012))  # pallet
    return bm


def build(short_dir):
    short_dir = Path(short_dir)
    short = load_short(short_dir / "source" / "short.json")
    tl = json.loads((short_dir / "source" / "timeline.json").read_text(encoding="utf-8"))
    spans = {s["id"]: (s["f0"], s["f1"]) for s in tl["shots"]}
    builders = {"Workshop": build_workshop, "Clock": build_clock}
    names = sorted({s.scene for s in short.shots})
    bsa_scene.remove_scenes(names)  # and their old objects, so a rebuild keeps exact names (contact tags use them)
    for name in names:
        sc = bpy.data.scenes.new(name)
        bsa_scene.setup_scene(sc, short, ENGINE, "preview")
        builders[name](sc, short, spans, short.fps)
        sc.frame_start, sc.frame_end = 1, tl["n_frames"]
        sc["bsa_timeline"] = timeline_hash(tl)  # the Blender commands refuse a scene built for another timing
    out = short_dir / "blend" / "short.blend"
    out.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(out))


if __name__ == "__main__" and "--" in sys.argv:
    build(SHORT_DIR)
