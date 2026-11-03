"""Scene sets in Blender: `base` builds a set's floor, walls, backdrop and world (before the subject: every set's
support surface is at z = 0), `dress` sizes them to what the scene's cameras see and places the props (after the
motion is baked, so they keep clear of it and of the cards on every frame), `clear` takes a set away and
`refresh_world` remakes its world for the style the scene shows. The rules for where props may go, and sets.json,
are bsa.setplace's (no Blender code there).

The four sets, recoloured from the kit (bsa.colour.set_colours):
- workbench: a wooden bench top and a pegboard wall behind the subject; tools on the board, small boxes and a jar on
  the bench.
- graph: graph-paper floor and wall; a ruler, a pencil and a set square.
- outdoor: ground, two layers of hills (set:backdrop) and a sky for camera rays only; trees and clouds.
- studio: a seamless cyclorama (the floor sweeping up into the back wall, one mesh) in the background colour, a
  paper_world backdrop (the stills' background check applies), no props.

The camera looks along +y: the back wall stands behind the subject (WALL_GAP of the subject's size behind its far
edge, never further than the view reaches) and the floor runs from the bottom of the view to it. Floors and walls
are only as big as the cameras see (plus GROW), never larger: a big floor reaches other scenes' objects. Nothing here
sets the render engine: that is always the user's choice."""

from __future__ import annotations

import math

import bmesh
import bpy
from mathutils import Matrix, Vector

from bsa import colour, looks, setplace
from bsa.blender import anchors, bake, look
from bsa.layout import H, Rect, W

GROW = 0.10  # the floor and walls are the view's footprint plus this share (half each side)
FAR_CLAMP = 30.0  # a view ray that never meets the floor (or meets it far off) is cut at this many subject sizes
PROVISIONAL = 6.0  # `base` makes a floor this many subject sizes square, until `dress` sizes it to the view
WALL_GAP = {"workbench": 0.25, "graph": 1.0, "studio": 0.6}  # the wall's distance behind the subject, x its size
CYC_RADIUS = 0.8  # the studio's sweep radius, as a share of its wall gap
BENCH_T = 0.035  # the bench top's thickness, x the subject's size
BENCH_FRONT = 0.2  # the bench top's front edge stands this far in front of the subject, x its size (a strip)
HILLS = {"near": (1.0, 0.35), "far": (2.5, 0.6)}  # distance behind the subject and height, x its size
SAMPLE_EVERY = 3  # dress looks at every 3rd frame of each shot, and its first and last
WALL_EPS = 0.002  # a wall prop stands this far off the board (no faces on the board's plane)
CLOUD_WHITE = 0.75  # a cloud is the sky's horizon colour this far toward white

SET_COLLECTION = "{} set"
PROPS_COLLECTION = "{} set props"
SIZE_KEY = "bsa_set_size"


# --- small builders -----------------------------------------------------------------------------------------------


def _collection(scene, pattern: str):
    name = pattern.format(scene.name)
    col = bpy.data.collections.get(name) or bpy.data.collections.new(name)
    if scene.collection.children.get(col.name) is None:
        scene.collection.children.link(col)
    return col


def _material(scene, key: str, hex_: str, pattern: str | None = None) -> tuple[object, bool]:
    """The scene's set material "<scene> set <key>" (made with kit_material, matte), reused while it is the same
    colour and pattern; (material, made now)."""
    name = f"{scene.name} set {key}"
    hex_ = colour.srgb_to_hex(colour.hex_to_srgb(hex_))
    old = bpy.data.materials.get(name)
    if old is not None:
        st = look.stamp(old)
        if st and st["colour"] == hex_ and st["pattern"] == pattern:
            return old, False
        if old.users == 0:
            bpy.data.materials.remove(old)
        else:
            old.name = f"{name} (old)"
    return look.kit_material(name, "matte", hex_, pattern=pattern), True


def _remove_object(obj) -> None:
    data = obj.data
    bpy.data.objects.remove(obj)
    if data is not None and data.users == 0 and isinstance(data, bpy.types.Mesh):
        bpy.data.meshes.remove(data)


def _object(scene, col, name: str, bm, mats, role: str, *, location=(0.0, 0.0, 0.0), rotation=(0.0, 0.0, 0.0),
            scale: float = 1.0):  # fmt: skip
    """The set object `name` in `col` with the geometry in `bm` (freed), made or reused (its old mesh replaced), the
    materials `mats` in its slots, tagged set:<role>."""
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    obj = bpy.data.objects.get(name)
    if obj is None:
        obj = bpy.data.objects.new(name, me)
    else:
        old = obj.data
        obj.data = me
        if old is not None and old.users == 0:
            bpy.data.meshes.remove(old)
        me.name = name
    if col.objects.get(obj.name) is None:
        col.objects.link(obj)
    for m in mats:
        me.materials.append(m)
    obj.location = location
    obj.rotation_euler = rotation
    obj.scale = (scale, scale, scale)
    bake.tag(obj, f"set:{role}")
    return obj


def _box(bm, lo, hi, mat: int = 0) -> None:
    """An axis-aligned box from corner `lo` to `hi` into `bm`, its faces using material slot `mat`."""
    made = bmesh.ops.create_cube(bm, size=1.0)
    for v in made["verts"]:
        v.co = Vector(lo[i] + (hi[i] - lo[i]) * (v.co[i] + 0.5) for i in range(3))
    for f in {f for v in made["verts"] for f in v.link_faces}:
        f.material_index = mat


def _cylinder(bm, radius: float, z0: float, z1: float, segments: int = 24, mat: int = 0, matrix=None) -> None:
    made = bmesh.ops.create_cone(bm, cap_ends=True, segments=segments, radius1=radius, radius2=radius, depth=z1 - z0)
    for v in made["verts"]:
        v.co.z += (z0 + z1) / 2
        if matrix is not None:
            v.co = matrix @ v.co
    for f in {f for v in made["verts"] for f in v.link_faces}:
        f.material_index = mat


def _ellipsoid(bm, centre, radii, mat: int = 0, floor: float | None = None) -> None:
    """An ellipsoid; with `floor`, its points below that height flattened onto it (a mound on the ground)."""
    made = bmesh.ops.create_uvsphere(bm, u_segments=32, v_segments=16, radius=1.0)
    for v in made["verts"]:
        v.co = Vector(centre[i] + radii[i] * v.co[i] for i in range(3))
        if floor is not None and v.co.z < floor:
            v.co.z = floor
    for f in {f for v in made["verts"] for f in v.link_faces}:
        f.material_index = mat
        f.smooth = True


def _quad(bm, pts) -> None:
    bm.faces.new([bm.verts.new(p) for p in pts])


def _scaled(bm, k: float):
    """Geometry built in world units, held in an object scaled by `k`: its verts divided by `k`."""
    for v in bm.verts:
        v.co /= k
    return bm


def _pattern_scale(set_name: str, size: float) -> float:
    """The object scale a set surface is held at, so its pattern (look's dots every 4 cm, grid every 25 cm, wood
    grain) reads at the subject's size: about 25 dots or 12 grid squares across the subject."""
    if set_name == "graph":
        return max(size, 0.05) / 2.5
    return max(1.0, size / 0.35)


def _wood_scale(size: float) -> float:
    """The bench top's object scale: a fine grain, a few planks across the subject whatever its size."""
    return max(0.5, size / 8.0)


def _catcher(scene, kit) -> bool:
    """The studio sweep is a Cycles shadow catcher (the paper backdrop with shadows on it) except in the line styles,
    whose cel shadows the sweep draws itself."""
    return str(scene.get("bsa_style") or kit.style) not in looks.LINE_STYLES


# --- the sets' surfaces -------------------------------------------------------------------------------------------


def _layout(set_name: str, size: float, floor, wall_y: float, wall_h: float, wall_x=None) -> dict:
    x0, y0, x1, y1 = floor
    wx0, wx1 = wall_x or (x0, x1)
    return {"floor": [x0, y0, x1, y1], "wall_y": wall_y, "wall_h": wall_h, "wall_x": [min(wx0, x0), max(wx1, x1)]}


def _provisional(set_name: str, size: float) -> dict:
    h = PROVISIONAL * size / 2
    return _layout(set_name, size, (-h, -h, h, h), h, h)


def _surfaces(scene, kit, set_name: str, size: float, lay: dict) -> list:
    """Make (or reshape) the set's floor, wall and backdrop objects to the layout; returns them."""
    col = _collection(scene, SET_COLLECTION)
    cols = colour.set_colours(kit)
    x0, y0, x1, y1 = lay["floor"]
    wy, wh = lay["wall_y"], lay["wall_h"]
    wx0, wx1 = lay["wall_x"]
    k = _pattern_scale(set_name, size)
    name = f"{scene.name} set"
    made = []
    if set_name == "workbench":
        wood, _ = _material(scene, "bench", cols["wood"], "wood")
        board, _ = _material(scene, "wall", cols["wall"], "dots")
        bm = bmesh.new()
        _box(bm, (x0, y0, -BENCH_T * size), (x1, wy, 0.0))
        kw = _wood_scale(size)
        made.append(_object(scene, col, f"{name} floor", _scaled(bm, kw), [wood], "floor", scale=kw))
        bm = bmesh.new()
        _quad(bm, [(wx0, 0.0, 0.0), (wx1, 0.0, 0.0), (wx1, wh, 0.0), (wx0, wh, 0.0)])
        made.append(_object(scene, col, f"{name} wall", _scaled(bm, k), [board], "wall", location=(0.0, wy, 0.0),
                            rotation=(math.pi / 2, 0.0, 0.0), scale=k))  # fmt: skip
    elif set_name == "graph":
        paper, _ = _material(scene, "floor", cols["floor"], "grid")
        wall, _ = _material(scene, "wall", cols["wall"], "grid")
        bm = bmesh.new()
        _quad(bm, [(x0, y0, 0.0), (x1, y0, 0.0), (x1, wy, 0.0), (x0, wy, 0.0)])
        made.append(_object(scene, col, f"{name} floor", _scaled(bm, k), [paper], "floor", scale=k))
        bm = bmesh.new()
        _quad(bm, [(wx0, 0.0, 0.0), (wx1, 0.0, 0.0), (wx1, wh, 0.0), (wx0, wh, 0.0)])
        made.append(_object(scene, col, f"{name} wall", _scaled(bm, k), [wall], "wall", location=(0.0, wy, 0.0),
                            rotation=(math.pi / 2, 0.0, 0.0), scale=k))  # fmt: skip
    elif set_name == "studio":
        sweep, _ = _material(scene, "backdrop", cols["wall"])
        bm = bmesh.new()
        r = min(lay.get("radius", 0.0) or CYC_RADIUS * WALL_GAP["studio"] * size, wy - y0, wh)
        profile = [(y0, 0.0)]
        steps = 16
        for i in range(steps + 1):
            phi = math.pi / 2 * i / steps
            profile.append((wy - r + r * math.sin(phi), r - r * math.cos(phi)))
        profile.append((wy, wh))
        left = [bm.verts.new((wx0, y, z)) for y, z in profile]
        right = [bm.verts.new((wx1, y, z)) for y, z in profile]
        for i in range(len(profile) - 1):
            f = bm.faces.new((left[i], right[i], right[i + 1], left[i + 1]))
            f.smooth = True
        bm.normal_update()
        sweep_obj = _object(scene, col, f"{name} floor", bm, [sweep], "floor")
        sweep_obj.is_shadow_catcher = _catcher(scene, kit)  # Cycles only: EEVEE ignores it
        made.append(sweep_obj)
    elif set_name == "outdoor":
        ground, _ = _material(scene, "ground", cols["ground"])
        bm = bmesh.new()
        _quad(bm, [(x0, y0, 0.0), (x1, y0, 0.0), (x1, y1, 0.0), (x0, y1, 0.0)])
        made.append(_object(scene, col, f"{name} floor", bm, [ground], "floor"))
        for layer in ("far", "near"):
            mat, _ = _material(scene, f"hills {layer}", cols[f"hills_{layer}"])
            hx, hy, hh = lay["hills"][layer]
            made.append(_object(scene, col, f"{name} hills {layer}", _hills(hx, hy, hh, layer), [mat], "backdrop"))
    else:
        raise ValueError(f"unknown set {set_name!r}: use one of {', '.join(setplace.SETS)} or none")
    return made


def _hills(xs, y: float, h: float, layer: str):
    """A row of mounds along x from xs[0] to xs[1] at depth y, about `h` high (a fixed pattern of heights, so a set
    looks the same every build), their bottoms flattened just under the ground."""
    bm = bmesh.new()
    x0, x1 = xs
    width = max(x1 - x0, 1e-3)
    n = max(2, round(width / (2.2 * h)))
    step = width / n
    heights = (1.0, 0.8, 1.15, 0.9, 1.05, 0.85) if layer == "near" else (0.9, 1.1, 0.95, 1.2, 0.85, 1.0)
    for i in range(n + 1):
        cx = x0 + i * step + (0.25 * step if layer == "far" else 0.0)
        _ellipsoid(bm, (cx, y, -0.02 * h), (0.75 * step + 0.3 * h, 0.6 * h, heights[i % 6] * h), floor=-0.01 * h)
    return bm


# --- the world ----------------------------------------------------------------------------------------------------


def _strengths(style: str, sl) -> dict:
    from bsa.blender.styles import NO_WORLD_LIGHT_IN_CYCLES

    cycles = 0.0 if style in NO_WORLD_LIGHT_IN_CYCLES else sl.world * sl.cycles_trim
    return {"eevee": float(sl.world), "cycles": float(cycles)}


def _lin(hex_: str) -> tuple[float, float, float, float]:
    return (*[float(c) for c in colour.srgb_to_linear(colour.hex_to_srgb(hex_))], 1.0)


def refresh_world(scene, kit, warnings: list | None = None) -> None:
    """The set's world for the style the scene shows (its `bsa_style`, else the kit's): the studio a paper_world
    in looks.backdrop (a flat backdrop: the stills' background check applies); workbench and graph a plain background
    of the wall colour; outdoor a sky for camera rays only (a gradient from the horizon colour to the top colour, by
    the view direction's height) over a lighting background of the horizon colour. Blueprint and pencil, which draw
    on paper, show their paper instead. The camera sees the colour at strength 1; what lights the scene is a separate
    Background (a Light Path "Is Camera Ray" mix) whose strength is stamped per engine (`world["bsa_strength"]`, the
    styles' rule) and matched to the engine the scene renders with. Workbench, graph and outdoor worlds are stamped
    `world["bsa_backdrop"] = "set"`: environments, no background check. Never sets the engine."""
    from bsa.blender import styles

    set_name = scene.get("bsa_set")
    if kit is None or not set_name:
        return
    style = str(scene.get("bsa_style") or kit.style)
    sl = looks.TABLE[style]
    warnings = [] if warnings is None else warnings
    if set_name == "studio":
        sweep = bpy.data.objects.get(f"{scene.name} set floor")
        if sweep is not None:
            sweep.is_shadow_catcher = _catcher(scene, kit)
        styles.paper_backdrop(scene, kit, style, sl, warnings)
        look.match_engine(scene)
        return
    cols = colour.set_colours(kit)
    paper = style in ("blueprint", "pencil")
    name = f"{scene.name} set world"
    world = bpy.data.worlds.get(name) or bpy.data.worlds.new(name)
    scene.world = world
    for key in ("bsa_paper",):
        if key in world:
            del world[key]
    world["bsa_backdrop"] = "set"
    world.use_nodes = True
    nt = world.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputWorld")
    lit = nt.nodes.new("ShaderNodeBackground")
    lit.name = "Lit"
    seen = nt.nodes.new("ShaderNodeBackground")
    seen.name = "Seen"
    seen.inputs["Strength"].default_value = 1.0
    if paper:
        shown = lit_hex = looks.backdrop(style, kit)
        seen.inputs["Color"].default_value = _lin(shown)
    elif set_name == "outdoor":
        lit_hex = cols["sky_horizon"]
        coords = nt.nodes.new("ShaderNodeTexCoord")
        sep = nt.nodes.new("ShaderNodeSeparateXYZ")
        up = nt.nodes.new("ShaderNodeMapRange")  # the view direction's height: 0 at the horizon, 1 well above it
        up.inputs["From Min"].default_value = 0.0
        up.inputs["From Max"].default_value = 0.6
        along = nt.nodes.new("ShaderNodeCombineXYZ")
        grad = nt.nodes.new("ShaderNodeTexGradient")
        grad.gradient_type = "LINEAR"
        nt.links.new(coords.outputs["Generated"], sep.inputs["Vector"])
        nt.links.new(sep.outputs["Z"], up.inputs["Value"])
        nt.links.new(up.outputs["Result"], along.inputs["X"])
        nt.links.new(along.outputs["Vector"], grad.inputs["Vector"])
        sky = look._mix_rgba(nt, grad.outputs["Fac"], _lin(cols["sky_horizon"])[:3], _lin(cols["sky_top"])[:3])
        nt.links.new(sky, seen.inputs["Color"])
    else:
        lit_hex = cols["wall"]
        seen.inputs["Color"].default_value = _lin(cols["wall"])
    lit.inputs["Color"].default_value = _lin(lit_hex)
    path = nt.nodes.new("ShaderNodeLightPath")
    mix = nt.nodes.new("ShaderNodeMixShader")
    nt.links.new(path.outputs["Is Camera Ray"], mix.inputs["Fac"])
    nt.links.new(lit.outputs["Background"], mix.inputs[1])
    nt.links.new(seen.outputs["Background"], mix.inputs[2])
    nt.links.new(mix.outputs["Shader"], out.inputs["Surface"])
    world["bsa_strength"] = {"Lit": _strengths(style, sl)}
    scene.render.film_transparent = False
    look.match_engine(scene)


# --- base and clear -----------------------------------------------------------------------------------------------


def _restyle(scene, kit) -> None:
    """A scene already styled gets its new set materials (and world) in its style."""
    if "bsa_style_args" in scene:
        from bsa.blender import styles

        styles.reapply(scene, kit, style=str(scene.get("bsa_style") or kit.style))


def base(scene, kit, set_name: str | None, size: float) -> dict:
    """Build the set `set_name` for a subject `size` metres across in the collection "<scene> set": its support
    surface with its top at z = 0 (a workbench's bench top, graph paper, the ground, the studio's floor), its walls
    and backdrop, provisionally PROVISIONAL x size square (`dress` sizes them to the cameras' view), in the kit's set
    colours, tagged set:floor / set:wall / set:backdrop; stamps scene["bsa_set"] and makes the world
    (`refresh_world`). A set already there is cleared first. Without a kit nothing at all: {}. With no set (None or
    "none") {}, and a set already there is taken away (`clear`) and the style's paper backdrop put back. Returns
    {"set", "objects": [names]}."""
    if kit is None:
        return {}
    if set_name in (None, "none"):
        if "bsa_set" in scene:
            clear(scene)
            if "bsa_style_args" in scene:
                _restyle(scene, kit)
            else:
                from bsa.blender import styles

                styles.paper_backdrop(scene, kit, kit.style, looks.TABLE[kit.style], [])
                look.match_engine(scene)
        return {}
    if set_name not in setplace.SETS:
        raise ValueError(f"unknown set {set_name!r}: use one of {', '.join(setplace.SETS)} or none")
    if "bsa_set" in scene:
        clear(scene)
    size = float(size)
    lay = _provisional(set_name, size)
    if set_name == "outdoor":
        lay["hills"] = {k: (lay["wall_x"], lay["wall_y"] * (0.6 if k == "near" else 1.0), hh * size)
                        for k, (_, hh) in HILLS.items()}  # fmt: skip
    made = _surfaces(scene, kit, set_name, size, lay)
    scene["bsa_set"] = set_name
    scene[SIZE_KEY] = size
    refresh_world(scene, kit)
    _restyle(scene, kit)
    return {"set": set_name, "objects": [o.name for o in made]}


def clear(scene) -> int:
    """Take the scene's set away: every set:* object in it, its "<scene> set" and "<scene> set props" collections,
    their unused meshes and set materials, and scene["bsa_set"]. Returns how many objects went."""
    gone = 0
    for obj in [o for o in scene.objects if bake.is_set(o)]:
        _remove_object(obj)
        gone += 1
    for pattern in (SET_COLLECTION, PROPS_COLLECTION):
        col = bpy.data.collections.get(pattern.format(scene.name))
        if col is not None:
            for obj in list(col.objects):
                _remove_object(obj)
                gone += 1
            bpy.data.collections.remove(col)
    prefix = f"{scene.name} set "
    for mat in [m for m in bpy.data.materials if m.name.startswith(prefix) and m.users == 0]:
        bpy.data.materials.remove(mat)
    for key in ("bsa_set", SIZE_KEY):
        if key in scene:
            del scene[key]
    return gone


# --- the cameras' view --------------------------------------------------------------------------------------------


def _clip_y(poly, y_max: float) -> list:
    """The polygon (x, y) cut to y <= y_max (Sutherland-Hodgman, one edge)."""
    out = []
    for i, p in enumerate(poly):
        q = poly[(i + 1) % len(poly)]
        pin, qin = p[1] <= y_max, q[1] <= y_max
        if pin:
            out.append(p)
        if pin != qin:
            t = (y_max - p[1]) / (q[1] - p[1])
            out.append((p[0] + t * (q[0] - p[0]), y_max))
    return out


def _ground_point(o: Vector, d: Vector, reach: float) -> tuple[float, float]:
    """Where the ray from `o` along `d` meets z = 0, cut at `reach` (horizontally from `o`); a ray that never meets
    it is cut at `reach` along its heading."""
    flat = Vector((d.x, d.y))
    if d.z < -1e-9:
        t = -o.z / d.z
        hit = Vector((o.x + t * d.x, o.y + t * d.y))
        if (hit - o.xy).length <= reach:
            return hit.x, hit.y
    if flat.length < 1e-9:
        return o.x, o.y
    p = o.xy + flat.normalized() * reach
    return p.x, p.y


class _View:
    """The scene camera at one frame: its view-projection matrix for a 1080x1920 frame and its 4 corner rays."""

    def __init__(self, cam_ev, depsgraph):
        proj = cam_ev.calc_matrix_camera(depsgraph, x=W, y=H)
        self.m = proj @ cam_ev.matrix_world.inverted()
        inv = self.m.inverted()
        eye = cam_ev.matrix_world.translation.copy()
        persp = cam_ev.data.type != "ORTHO"
        self.rays = []
        for nx, ny in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
            near = inv @ Vector((nx, ny, -1.0, 1.0))
            far = inv @ Vector((nx, ny, 1.0, 1.0))
            o, f = (eye if persp else near.xyz / near.w), far.xyz / far.w  # a perspective ray starts at the eye
            self.rays.append((o, (f - o).normalized()))

    def rect(self, points) -> Rect | None:
        """The screen rect (pixels, origin top-left) of the points in front of the camera, or None."""
        xs, ys = [], []
        for p in points:
            v = self.m @ Vector((p[0], p[1], p[2], 1.0))
            if v.w <= 1e-9:
                continue
            xs.append((v.x / v.w + 1) / 2 * W)
            ys.append((1 - (v.y / v.w + 1) / 2) * H)
        if not xs:
            return None
        return Rect(min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys))

    def footprint(self, reach: float) -> list:
        return [_ground_point(o, d, reach) for o, d in self.rays]

    def at_plane_y(self, y: float, reach: float) -> list:
        """(x, z) where the corner rays that head away from the camera meet the plane at depth `y` (z cut at reach)."""
        out = []
        for o, d in self.rays:
            if d.y > 1e-9 and y > o.y:
                t = (y - o.y) / d.y
                out.append((o.x + t * d.x, min(o.z + t * d.z, reach)))
        return out


def _grow(lo: float, hi: float) -> tuple[float, float]:
    g = (hi - lo) * GROW / 2
    return lo - g, hi + g


def _sized(set_name: str, size: float, views: list, subject) -> dict:
    """The set's layout from what the cameras see: the floor is the union of the views' footprints on z = 0 (far rays
    cut at FAR_CLAMP x size) up to the wall (or, outdoors, the far hills), plus GROW; the wall stands WALL_GAP x size
    behind the subject's far edge, but no further than the views reach, as wide and tall as they see there."""
    reach = FAR_CLAMP * size
    polys = [v.footprint(reach) for v in views]
    far = max(p[1] for poly in polys for p in poly)
    sub_far = subject[3] if subject else 0.0
    if set_name == "outdoor":
        gaps = {k: min(sub_far + d * size, far) for k, (d, _) in HILLS.items()}
        wall_y = gaps["far"]
    else:
        wall_y = min(sub_far + WALL_GAP[set_name] * size, far)
    front = -math.inf
    if set_name == "workbench" and subject:  # a bench is a strip from just in front of the subject to the wall
        front = subject[1] - BENCH_FRONT * size
    cut = [c for c in (_clip_y(poly, wall_y) for poly in polys) if c]
    if front > -math.inf:  # and in front: the same cut, mirrored
        cut = [[(x, -y) for x, y in _clip_y([(x, -y) for x, y in c], -front)] for c in cut]
    pts = [p for c in cut for p in c]
    plane = [q for v in views for q in v.at_plane_y(wall_y, reach)]
    if not pts:  # a close-up: the views meet the wall before the floor, so the floor is a strip at the wall's foot
        xs = [q[0] for q in plane] or ([subject[0], subject[2]] if subject else [0.0])
        pts = [(min(xs), wall_y - 0.01 * size), (max(xs), wall_y)]
    x0, x1 = _grow(min(p[0] for p in pts), max(p[0] for p in pts))
    y0, y1 = _grow(min(p[1] for p in pts), max(p[1] for p in pts))
    y0, y1 = max(y0, front), min(y1, wall_y)
    wall_h = max([q[1] for q in plane] + [0.0])
    wall_h = max(wall_h * (1 + GROW / 2), 0.5 * size)
    wx = (min([q[0] for q in plane] + [x0]), max([q[0] for q in plane] + [x1]))
    lay = _layout(set_name, size, (x0, y0, x1, y1), wall_y, wall_h, _grow(*wx))
    if set_name == "studio":
        lay["radius"] = min(CYC_RADIUS * (wall_y - sub_far), wall_y - y0, wall_h) if subject else 0.0
    if set_name == "outdoor":
        lay["hills"] = {}
        for k, (_, hh) in HILLS.items():
            at = [q for v in views for q in v.at_plane_y(gaps[k], reach)]
            hx = _grow(min([q[0] for q in at] + [x0]), max([q[0] for q in at] + [x1]))
            lay["hills"][k] = (hx, gaps[k], hh * size)
    return lay


# --- dress --------------------------------------------------------------------------------------------------------


def _subjects(scene, shot_id: str | None) -> list:
    """The shot's heroes, else every rendered mesh that is not set scenery or an overlay."""
    names = anchors.heroes(scene, shot_id) if shot_id else []
    objs = [scene.objects[n] for n in names if scene.objects.get(n) is not None]
    if objs:
        return objs
    return [o for o in scene.objects
            if o.type == "MESH" and not o.hide_render and not bake.is_set(o) and not bake.is_overlay(o)]  # fmt: skip


EDGE = "frame edge"
_FAR = 10 * max(W, H)


def _outside() -> list:
    """("frame edge", rect) for the four strips round the frame: a prop meeting one is cut by the frame's edge."""
    return [(EDGE, Rect(-_FAR, -_FAR, W + 2 * _FAR, _FAR)), (EDGE, Rect(-_FAR, H, W + 2 * _FAR, _FAR)),
            (EDGE, Rect(-_FAR, 0, _FAR, H)), (EDGE, Rect(W, 0, _FAR, H))]  # fmt: skip


def _shots(scene, short, tl) -> list:
    """[(shot or None, span or None, frames)] for the shots this scene shows; the scene's frame range without any."""
    out = []
    if short is not None and tl is not None:
        spans = {s.id: s for s in tl.shots}
        for shot in short.shots:
            span = spans.get(shot.id)
            if shot.scene == scene.name and span is not None:
                frames = sorted(set(range(span.f0, span.f1 + 1, SAMPLE_EVERY)) | {span.f0, span.f1})
                out.append((shot, span, frames))
    if not out:
        f0, f1 = scene.frame_start, scene.frame_end
        out.append((None, None, sorted(set(range(f0, f1 + 1, SAMPLE_EVERY)) | {f0, f1})))
    return out


def _label_tracks(scene, shots) -> tuple[dict, dict]:
    """Empty per-shot tracks to fill at the dressed frames (_track_labels): ({shot: {object: {"f0", "points",
    "category"}}} for each near label's object, {shot: {"f0", "boxes": {hero: [...]}}}), with a slot per frame of the
    shot, as overlays.shot_boxes reads anchors and heroes."""
    from bsa.short import label_near

    near_at, heroes_at = {}, {}
    for shot, span, _ in shots:
        if shot is None:
            continue
        n = span.f1 - span.f0 + 1
        for label in shot.labels or []:
            name = label_near(label)
            obj = scene.objects.get(name) if name else None
            if obj is not None:
                near_at.setdefault(shot.id, {})[name] = {"f0": span.f0, "points": [None] * n,
                                                        "category": bake.category(obj)}  # fmt: skip
        heroes_at[shot.id] = {"f0": span.f0, "boxes": {h: [None] * n for h in anchors.heroes(scene, shot.id)}}
    return near_at, heroes_at


def _track_labels(scene, cam_ev, dg, frame, shot_id, near_at, heroes_at) -> None:
    """Fill the near labels' anchor points and the heroes' screen boxes of shot `shot_id` at `frame`."""
    heroes = heroes_at.get(shot_id)
    if heroes is None:
        return
    i = frame - heroes["f0"]
    for name, rec in near_at.get(shot_id, {}).items():
        rec["points"][i] = anchors.screen_top(scene, cam_ev, scene.objects[name], dg)
    for name, boxes in heroes["boxes"].items():
        boxes[i] = anchors.screen_box(scene, cam_ev, scene.objects[name], dg)


def _corners(box_min, box_max, matrix) -> list:
    return [matrix @ Vector((x, y, z)) for x in (box_min[0], box_max[0]) for y in (box_min[1], box_max[1])
            for z in (box_min[2], box_max[2])]  # fmt: skip


def _prop_matrix(c: setplace.Candidate, wall_y: float | None = None) -> Matrix:
    """Where a prop at candidate `c` stands: a floor prop's base centre at (x, y, 0) turned about z; a wall prop just
    in front of the board (its back WALL_EPS off it) turned about the board's normal; a sky prop at its centre."""
    dy = c.prop.dims[1]
    if c.prop.zone == "floor":
        return Matrix.Translation((c.x, c.y, 0.0)) @ Matrix.Rotation(c.rot, 4, "Z")
    if c.prop.zone == "wall":
        y = (c.y if wall_y is None else wall_y) - dy / 2 - WALL_EPS
        return Matrix.Translation((c.x, y, c.z)) @ Matrix.Rotation(c.rot, 4, "Y")
    return Matrix.Translation((c.x, c.y, c.z))


def _local_box(prop: setplace.Prop) -> tuple:
    dx, dy, dz = prop.dims
    if prop.zone == "floor":
        return (-dx / 2, -dy / 2, 0.0), (dx / 2, dy / 2, dz)
    return (-dx / 2, -dy / 2, -dz / 2), (dx / 2, dy / 2, dz / 2)


def _prop_mesh(prop: setplace.Prop):
    """The prop's geometry from primitives inside its local box (floor props stand on z = 0, others are centred);
    material slot 0 the main colour, 1 the second (a tree's canopy, a tool's head)."""
    dx, dy, dz = prop.dims
    bm = bmesh.new()
    lo, hi = _local_box(prop)
    s = prop.shape
    if s == "toolbox":
        _box(bm, (-dx / 2, -dy / 2, 0.0), (dx / 2, dy / 2, 0.72 * dz))
        _box(bm, (-0.3 * dx, -0.08 * dy, 0.72 * dz), (0.3 * dx, 0.08 * dy, dz), mat=1)
    elif s == "jar":
        _cylinder(bm, min(dx, dy) / 2, 0.0, 0.85 * dz)
        _cylinder(bm, 0.42 * min(dx, dy), 0.85 * dz, dz, mat=1)
    elif s in ("box", "ruler"):
        _box(bm, lo, hi)
    elif s == "hammer":
        _box(bm, (-0.12 * dx, -dy / 2, -dz / 2), (0.12 * dx, dy / 2, 0.3 * dz))
        _box(bm, (-dx / 2, -dy / 2, 0.3 * dz), (dx / 2, dy / 2, dz / 2), mat=1)
    elif s == "wrench":
        _box(bm, (-0.18 * dx, -dy / 2, -0.35 * dz), (0.18 * dx, dy / 2, 0.3 * dz))
        _box(bm, (-dx / 2, -dy / 2, 0.3 * dz), (dx / 2, dy / 2, dz / 2), mat=1)
        _box(bm, (-0.35 * dx, -dy / 2, -dz / 2), (0.35 * dx, dy / 2, -0.35 * dz), mat=1)
    elif s == "pliers":
        _box(bm, (-dx / 2, -dy / 2, -dz / 2), (-0.1 * dx, dy / 2, 0.05 * dz))
        _box(bm, (0.1 * dx, -dy / 2, -dz / 2), (dx / 2, dy / 2, 0.05 * dz))
        _box(bm, (-0.3 * dx, -dy / 2, 0.05 * dz), (0.3 * dx, dy / 2, dz / 2), mat=1)
    elif s == "pencil":
        r = min(dy, dz) / 2
        along_x = Matrix.Translation((-dx / 2, 0.0, r)) @ Matrix.Rotation(math.pi / 2, 4, "Y")
        _cylinder(bm, r, 0.0, 0.82 * dx, segments=6, matrix=along_x)
        tip = bmesh.ops.create_cone(bm, cap_ends=True, segments=6, radius1=r, radius2=0.0, depth=0.18 * dx)
        for v in tip["verts"]:
            v.co.z += 0.82 * dx + 0.09 * dx
            v.co = along_x @ v.co
        for f in {f for v in tip["verts"] for f in v.link_faces}:
            f.material_index = 1
    elif s == "set_square":
        pts = [(-dx / 2, -dy / 2), (dx / 2, -dy / 2), (-dx / 2, dy / 2)]
        bot = [bm.verts.new((x, y, 0.0)) for x, y in pts]
        top = [bm.verts.new((x, y, dz)) for x, y in pts]
        bm.faces.new(bot[::-1])
        bm.faces.new(top)
        for i in range(3):
            j = (i + 1) % 3
            bm.faces.new((bot[i], bot[j], top[j], top[i]))
    elif s == "tree":
        _cylinder(bm, 0.09 * dx, 0.0, 0.5 * dz, segments=12)
        _ellipsoid(bm, (0.0, 0.0, 0.66 * dz), (dx / 2, dy / 2, 0.34 * dz), mat=1)
    elif s == "cloud":
        _ellipsoid(bm, (0.0, 0.0, 0.0), (dx / 2, dy / 2, dz / 2))
    else:
        _box(bm, lo, hi)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    return bm


def _prop_colours(set_name: str, shape: str, cols: dict) -> list[tuple[str, str]]:
    """(material key, colour) per slot: the set's own colours."""
    sky = colour.mix(cols["sky_horizon"], "#FFFFFF", CLOUD_WHITE)
    table = {
        "toolbox": [("wood dark", cols["wood_dark"]), ("pattern", cols["pattern"])],
        "jar": [("pattern", cols["pattern"]), ("wood dark", cols["wood_dark"])],
        "box": [("wood", cols["wood"])],
        "hammer": [("wood", cols["wood"]), ("wood dark", cols["wood_dark"])],
        "wrench": [("wood dark", cols["wood_dark"]), ("wood dark", cols["wood_dark"])],
        "pliers": [("wood dark", cols["wood_dark"]), ("pattern", cols["pattern"])],
        "ruler": [("wood", cols["wood"])],
        "pencil": [("wood", cols["wood"]), ("wood dark", cols["wood_dark"])],
        "set_square": [("pattern", cols["pattern"])],
        "tree": [("wood dark", cols["wood_dark"]), ("hills near", cols["hills_near"])],
        "cloud": [("cloud", sky)],
    }
    return table.get(shape, [("pattern", cols["pattern"])])


def _clear_props(scene) -> None:
    col = bpy.data.collections.get(PROPS_COLLECTION.format(scene.name))
    if col is None:
        return
    for obj in list(col.objects):
        _remove_object(obj)


def _build_props(scene, kit, set_name: str, kept: list, wall_y: float) -> tuple[list, bool]:
    """The kept props as objects "<scene> <prop>" in "<scene> set props", tagged set:prop; (objects, any material
    made now)."""
    col = _collection(scene, PROPS_COLLECTION)
    cols = colour.set_colours(kit)
    made_any = False
    objs = []
    for c in kept:
        mats = []
        for key, hex_ in _prop_colours(set_name, c.prop.shape, cols):
            mat, made = _material(scene, key, hex_)
            mats.append(mat)
            made_any = made_any or made
        obj = _object(scene, col, f"{scene.name} {c.prop.name}", _prop_mesh(c.prop), mats, "prop")
        obj.matrix_world = _prop_matrix(c, wall_y)
        obj["bsa_prop"] = c.prop.name
        objs.append(obj)
    return objs, made_any


def _r(v: float) -> float:
    return round(float(v), 6)


def _from_saved(entry: dict, set_name: str, size: float, skip) -> list:
    """The saved props (but those named in `skip`) as candidates."""
    by_name = {p.name: p for p in setplace.props(set_name, size)}
    out = []
    for p in entry["props"]:
        if p["name"] in skip or p["name"] not in by_name:
            continue
        base_prop = by_name[p["name"]]
        prop = setplace.Prop(base_prop.name, base_prop.shape, base_prop.zone, tuple(float(d) for d in p["dims"]))
        out.append(setplace.Candidate(prop, *[float(v) for v in p["at"]], float(p["rot"])))
    return out


def dress(scene, kit, short, tl, sdir, *, save: bool = True, replace: bool | list = False) -> dict:
    """Size the scene's set to what its cameras see and place its props (after the motion is baked).

    The frames: every SAMPLE_EVERY-th frame of each shot this scene shows (`short`'s shots whose `scene` is this one,
    their spans in `tl`), plus each shot's first and last (the scene's frame range without any). The subject: the
    shot's heroes, else every rendered mesh that is not scenery or an overlay. (1) The floor and walls are reshaped
    to the union of every view's footprint on z = 0 (`_sized`), plus GROW, never larger; the wall moves to the floor's
    far edge. (2) The props: the saved arrangement (setplace.reusable: only while the storyboard's approval of its
    current version stands and the entry is valid), but the names in `replace`; else (or `replace=True`) the seeded
    candidates (setplace.candidates) placed (setplace.place) clear of the subject's screen boxes (plus
    SUBJECT_MARGIN_PX) and of the kit's cards, captions and near labels (setplace.text_rects, each label at
    its part's top on screen, as the edit places it) on every frame. (3) The kept props
    are built from primitives in the set's colours, tagged set:prop, in "<scene> set props". (4) With `save` (and a
    short folder `sdir`) the arrangement goes to sets.json (setplace.save_scene). `sdir` None: nothing is read or
    saved. Without a kit, or a scene with no set (or set "none"), nothing at all: {}.

    Returns {"set", "floor": [x0, y0, x1, y1], "kept": [prop names], "dropped": [{"name", "why"}], "reused",
    "warnings"}: a saved arrangement that can't be trusted is placed again and its reason is a warning."""
    from bsa import cards, fonts, kitlayout

    set_name = scene.get("bsa_set")
    if kit is None or not set_name:
        return {}
    set_name = str(set_name)
    size = float(scene.get(SIZE_KEY, 1.0))
    warnings: list[str] = []
    shots = _shots(scene, short, tl)
    frames = sorted({f for _, _, fs in shots for f in fs})
    frame_shot = {f: (shot.id if shot else None) for shot, _, fs in shots for f in fs}
    views: dict[int, _View] = {}
    subj_rects: dict[int, list[Rect]] = {}
    box = [math.inf, math.inf, -math.inf, -math.inf]
    near_at, heroes_at = _label_tracks(scene, shots)

    def look_at(frame, cam_ev, dg):
        views[frame] = _View(cam_ev, dg)
        subj_rects[frame] = []
        _track_labels(scene, cam_ev, dg, frame, frame_shot[frame], near_at, heroes_at)
        for obj in _subjects(scene, frame_shot[frame]):
            ev = obj.evaluated_get(dg)
            pts = _corners(Vector(ev.bound_box[0]), Vector(ev.bound_box[6]), ev.matrix_world)
            for p in pts:
                box[0], box[1] = min(box[0], p.x), min(box[1], p.y)
                box[2], box[3] = max(box[2], p.x), max(box[3], p.y)
            b = anchors.screen_box(scene, cam_ev, obj, dg)
            if b is not None:
                subj_rects[frame].append(Rect(b[0], b[1], b[2] - b[0], b[3] - b[1]))

    anchors._at_frames(scene, frames, look_at)
    subject = tuple(box) if box[0] < math.inf else None
    lay = _sized(set_name, size, list(views.values()), subject)
    _surfaces(scene, kit, set_name, size, lay)
    floor = [_r(v) for v in lay["floor"]]
    wall_y = lay["wall_y"]
    place_floor = (floor[0], floor[1], floor[2], wall_y if set_name != "outdoor" else floor[3])

    texts: dict[int, list] = {f: [] for f in frames}
    if short is not None and tl is not None:
        font = cards.card_font(kit, fonts.font_caches(sdir))
        for shot, span, fs in shots:
            if shot is None:
                continue
            try:
                for f, rects in setplace.text_rects(short, tl, shot, span, kit, font, fs, near_at.get(shot.id),
                                                    heroes_at.get(shot.id)).items():  # fmt: skip
                    texts.setdefault(f, []).extend(rects)
            except kitlayout.LayoutError as exc:
                warnings.append(f"shot {shot.id!r}: {exc}; its cards were left out of the props' placement")
    else:
        for f in frames:
            texts[f] = [("covered", r) for r in setplace.covered_rects()]
    for f in frames:  # a prop is whole in the frame or not there: never cut by its edge
        texts[f] = texts[f] + _outside()

    slug = getattr(short, "slug", None) or "sample"
    skip = set(replace) if isinstance(replace, (list, tuple, set)) else set()
    entry, why = (None, None)
    if sdir is not None and replace is not True:
        entry, why = setplace.reusable(sdir, scene.name, set_name, size)
        if why:
            warnings.append(why)
    footprint = subject or (0.0, 0.0, 0.0, 0.0)
    cands = setplace.candidates(set_name, slug, scene.name, size, footprint, place_floor)

    def project(c, f):
        lo, hi = _local_box(c.prop)
        return views[f].rect(_corners(lo, hi, _prop_matrix(c, wall_y)))

    if entry is not None:
        reused_c = _from_saved(entry, set_name, size, skip)
        names = [p.name for p in setplace.props(set_name, size)]
        redo = [i for i, n in enumerate(names) if n in skip]
        tries = [[c for c in cands[i] if not setplace._clash(c, reused_c)] for i in redo]
        new_kept, new_dropped = setplace.place(tries, project, frames, subj_rects, texts)
        spotless = [d for d in setplace.dropped_without_spots(set_name, size, cands) if d["name"] in skip]
        new_dropped += [d for d in spotless if d["name"] not in {x["name"] for x in new_dropped}]
        old_dropped = [d for d in entry["dropped"] if d["name"] not in skip]
        kept, dropped = reused_c + new_kept, old_dropped + new_dropped
    else:
        kept, dropped = setplace.place(cands, project, frames, subj_rects, texts)
        dropped += setplace.dropped_without_spots(set_name, size, cands)
    order = {p.name: i for i, p in enumerate(setplace.props(set_name, size))}
    kept.sort(key=lambda c: order.get(c.prop.name, 99))
    dropped.sort(key=lambda d: order.get(d["name"], 99))

    _clear_props(scene)
    _objs, made = _build_props(scene, kit, set_name, kept, wall_y)
    if made:
        _restyle(scene, kit)
    saved_props = []
    for c in kept:
        y = wall_y if c.prop.zone == "wall" else c.y
        saved_props.append({"name": c.prop.name, "at": [_r(c.x), _r(y), _r(c.z)], "rot": _r(c.rot),
                            "dims": [_r(d) for d in c.prop.dims]})  # fmt: skip
    if save and sdir is not None:
        setplace.save_scene(sdir, scene.name, {"set": set_name, "floor": floor, "props": saved_props,
                                               "dropped": dropped}, slug=getattr(short, "slug", None))  # fmt: skip
    return {"set": set_name, "floor": floor, "kept": [c.prop.name for c in kept], "dropped": dropped,
            "reused": entry is not None, "warnings": warnings}  # fmt: skip
