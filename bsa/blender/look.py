"""Material and surface helpers: principled materials, procedural bump, bevel, smooth shading, PBR image maps, and
the view: a brand's exposure and look, and a backdrop that shows as an exact colour (paper_world)."""

from __future__ import annotations

import math
import re
from collections.abc import Sequence
from pathlib import Path

import bpy
import numpy as np


def _principled(mat):
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
    nt.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])
    return nt, bsdf, out


def _set(bsdf, name: str, value) -> None:
    if name in bsdf.inputs:
        bsdf.inputs[name].default_value = value


def material(
    name: str,
    base_color: Sequence[float],
    roughness: float = 0.5,
    metallic: float = 0.0,
    kind: str = "plastic",
    bump_scale: float = 0.0,
):
    """Principled material. kind: plastic | metal | glass | emissive. bump_scale > 0 adds procedural micro-noise."""
    if kind not in ("plastic", "metal", "glass", "emissive"):
        raise ValueError(f"unknown material kind {kind!r}")
    color = tuple(base_color)
    if len(color) == 3:
        color = (*color, 1.0)
    mat = bpy.data.materials.new(name)
    nt, bsdf, _out = _principled(mat)
    _set(bsdf, "Base Color", color)
    _set(bsdf, "Roughness", roughness)
    _set(bsdf, "Metallic", 1.0 if kind == "metal" else metallic)
    if kind == "glass":
        _set(bsdf, "Transmission Weight", 1.0)
        _set(bsdf, "IOR", 1.45)
    elif kind == "emissive":
        _set(bsdf, "Emission Color", color)
        _set(bsdf, "Emission Strength", 5.0)
    if bump_scale > 0:
        noise = nt.nodes.new("ShaderNodeTexNoise")
        noise.inputs["Scale"].default_value = 400.0
        noise.inputs["Detail"].default_value = 8.0
        bump = nt.nodes.new("ShaderNodeBump")
        bump.inputs["Strength"].default_value = min(1.0, bump_scale)
        bump.inputs["Distance"].default_value = bump_scale * 0.01
        nt.links.new(noise.outputs["Fac"], bump.inputs["Height"])
        nt.links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])
    return mat


def assign(obj, mat) -> None:
    if obj.data.materials:
        obj.data.materials[0] = mat
    else:
        obj.data.materials.append(mat)


WATER_ALPHA = 0.4  # toy_water: still water (a tank, a glass) you see the parts inside through
STREAM_ALPHA = 0.7  # toy_water(stream=True): falling water, thin, so it needs more body to read as water


def toy_water(name: str, colour, alpha: float | None = None, stream: bool = False, roughness: float = 0.05,
              coat: float = 1.0):  # fmt: skip
    """Stylised water that works in EEVEE: a tinted Principled BSDF that is partly see-through (`alpha`, default
    WATER_ALPHA; STREAM_ALPHA with `stream=True` for a falling stream), with a clear coat for the wet shine, blended
    (`surface_render_method` BLENDED, or the older `blend_method` BLEND). Glass with an IOR needs ray-traced
    refraction in EEVEE and breaks behind other transparent parts; this does not refract, so parts inside show
    through tinted. `colour` is "#RRGGBB" or an sRGB (r, g, b) tuple of 0..1 floats."""
    from bsa import colour as colours

    srgb = colours.hex_to_srgb(colour) if isinstance(colour, str) else tuple(colour)[:3]
    a = (STREAM_ALPHA if stream else WATER_ALPHA) if alpha is None else float(alpha)
    if not 0.0 <= a <= 1.0:
        raise ValueError(f"alpha {a} is not between 0 and 1")
    mat = bpy.data.materials.new(name)
    _nt, bsdf, _out = _principled(mat)
    _set(bsdf, "Base Color", (*(float(c) for c in colours.srgb_to_linear(srgb)), 1.0))
    _set(bsdf, "Roughness", roughness)
    _set(bsdf, "Metallic", 0.0)
    _set(bsdf, "Alpha", a)
    for key in ("Coat Weight", "Clearcoat"):  # Blender 4.0+, then 3.x
        _set(bsdf, key, coat)
    for key in ("Coat Roughness", "Clearcoat Roughness"):
        _set(bsdf, key, 0.03)
    _set(bsdf, "IOR", 1.33)
    if hasattr(mat, "surface_render_method"):  # Blender 4.2+ (EEVEE Next)
        mat.surface_render_method = "BLENDED"
    if hasattr(mat, "blend_method"):  # older EEVEE (still present, but unused, in 4.2+)
        try:
            mat.blend_method = "BLEND"
        except (TypeError, AttributeError):
            pass
    if hasattr(mat, "use_backface_culling"):
        mat.use_backface_culling = False
    return mat


def bevel(obj, width_m: float, segments: int = 3):
    """Rounded hard edges: a Bevel modifier on edges sharper than its angle limit, shaded smooth with hardened
    normals, so the rounding shades smoothly and the flat faces stay flat. Works before or after `smooth`."""
    mod = obj.modifiers.new("Bevel", "BEVEL")
    mod.width = width_m
    mod.segments = segments
    mod.limit_method = "ANGLE"
    mod.harden_normals = True
    for poly in obj.data.polygons:
        poly.use_smooth = True
    return mod


def smooth(obj, angle_deg: float = 30) -> None:
    """Smooth shading, keeping edges sharper than angle_deg sharp."""
    mesh = obj.data
    for poly in mesh.polygons:
        poly.use_smooth = True
    if hasattr(mesh, "use_auto_smooth"):  # Blender < 4.1
        mesh.use_auto_smooth = True
        mesh.auto_smooth_angle = math.radians(angle_deg)
        return
    import bmesh  # 4.1+: sharpness is an explicit edge attribute

    bm = bmesh.new()
    bm.from_mesh(mesh)
    limit = math.radians(angle_deg)
    for e in bm.edges:
        if len(e.link_faces) == 2:
            e.smooth = e.calc_face_angle(0.0) <= limit
    bm.to_mesh(mesh)
    bm.free()


def set_look(scene, name: str) -> str:
    """Set the view transform's look by name, as Blender lists it ("AgX - Punchy") or short ("Punchy"); returns the
    name set. ValueError naming the looks there are when it is not one."""
    vs = scene.view_settings
    why = ""
    for cand in (name, f"{vs.view_transform} - {name}"):
        try:
            vs.look = cand
            return cand
        except TypeError as exc:  # its message lists the looks there are (a dynamic enum lists none in bl_rna)
            why = str(exc)
    found = re.search(r"not found in (\(.*\))", why)
    raise ValueError(f"no look {name!r} for {vs.view_transform}: use one of {found.group(1) if found else why}")


def brand_view(scene, settings: dict | None = None, short=None) -> dict:
    """Apply the brand's `brand.exposure` (stops) and `brand.look` (e.g. "Punchy") to the scene's view, when set;
    returns what was applied. A short whose look is fresh (its own `"look": "fresh"`, or `brand.mode` fresh) gets
    nothing from the brand. Without `short`, the look setup_scene stamped on the scene (`bsa_look`) is used."""
    from types import SimpleNamespace

    from bsa.settings import brand_for, load_settings

    if settings is None:
        settings = load_settings()
    if short is None and scene.get("bsa_look"):
        short = SimpleNamespace(look=str(scene["bsa_look"]))
    brand = brand_for(settings, short)
    out = {}
    if brand.get("exposure") is not None:
        scene.view_settings.exposure = float(brand["exposure"])
        out["exposure"] = scene.view_settings.exposure
    if brand.get("look"):
        out["look"] = set_look(scene, str(brand["look"]))
    return out


def view_srgb(scene, linear) -> np.ndarray:
    """What scene-linear RGB colours (N x 3) look like on screen (sRGB 0..1, 8-bit precision) under the scene's view
    transform, look, exposure and gamma: written through Blender's own colour management (Image.save_render) and
    read back."""
    import tempfile

    lin = np.atleast_2d(np.asarray(linear, dtype=np.float32))
    n = len(lin)
    img = bpy.data.images.new("bsa_view_probe", n, 1, float_buffer=True)
    r = scene.render.image_settings
    saved = (r.file_format, r.color_mode, r.color_depth)
    path = Path(tempfile.mkdtemp(prefix="bsa-view-")) / "probe.png"
    try:
        img.pixels.foreach_set(np.c_[lin, np.ones(n, np.float32)].ravel())
        r.file_format, r.color_mode, r.color_depth = "PNG", "RGB", "8"
        img.save_render(str(path), scene=scene)
        out = bpy.data.images.load(str(path), check_existing=False)
        try:
            buf = np.empty(n * out.channels, np.float32)
            out.pixels.foreach_get(buf)
            px = buf.reshape(n, out.channels)[:, :3]
        finally:
            bpy.data.images.remove(out)
    finally:
        bpy.data.images.remove(img)
        r.file_format, r.color_mode, r.color_depth = saved
        path.unlink(missing_ok=True)
    return px.astype(np.float64)


SEED_SPAN = 3.0  # the coarse search covers e**-3 .. e**3 times the target's own linear value, per channel
SEED_STEPS = 25  # 0.25 apart in log: 15625 colours, one probe image


def _seed(scene, target: np.ndarray) -> np.ndarray:
    """A start for the solve: the closest colour (ΔE) on a coarse log grid around the target's linear value, all
    in one batch through view_srgb. Looks like Punchy clip dark channels to 0, a flat region where Newton stalls."""
    from bsa.colour import delta_e, srgb_to_linear

    base = np.log(np.maximum(srgb_to_linear(target), 1e-4))
    steps = np.linspace(-SEED_SPAN, SEED_SPAN, SEED_STEPS)
    grid = np.stack(np.meshgrid(steps, steps, steps, indexing="ij"), -1).reshape(-1, 3) + base
    shown = view_srgb(scene, np.exp(grid))
    return grid[int(np.argmin([delta_e(target, s) for s in shown]))]


def _solve_linear(scene, target: np.ndarray, iterations: int = 30) -> tuple[np.ndarray, np.ndarray]:
    """(scene-linear RGB, the sRGB it shows as) whose on-screen colour is closest to `target` (sRGB 0..1): Newton
    steps in log space with a finite-difference Jacobian, each step one batch through view_srgb, from the target's
    own linear value and, when that does not converge, again from the best point of a coarse grid (_seed)."""
    from bsa.colour import delta_e, srgb_to_linear

    best = _newton(scene, target, np.log(np.maximum(srgb_to_linear(target), 1e-4)), iterations)
    if np.abs(best[1] - target).max() >= 0.6 / 255:
        other = _newton(scene, target, _seed(scene, target), iterations)
        if delta_e(target, other[1]) < delta_e(target, best[1]):
            best = other
    return best


def _newton(scene, target: np.ndarray, x: np.ndarray, iterations: int) -> tuple[np.ndarray, np.ndarray]:
    """Damped Newton from log-linear `x`: a step that lands further (ΔE) than the best so far is halved from there."""
    from bsa.colour import delta_e

    h = 0.05
    best = None  # (linear, shown, ΔE, log x)
    scale = 1.0
    for _ in range(iterations):
        probe = np.exp(np.vstack([x, x + h * np.eye(3)]))
        shown = view_srgb(scene, probe)
        err = shown[0] - target
        d = delta_e(target, shown[0])
        if best is not None and d >= best[2]:
            scale *= 0.5
            if scale < 1 / 64:
                break
            x = best[3] + scale * (x - best[3])
            continue
        best = (probe[0], shown[0], d, x)
        if np.abs(err).max() < 0.6 / 255:
            break
        jac = (shown[1:] - shown[0]).T / h  # d shown / d log x
        step = np.linalg.lstsq(jac, -err, rcond=None)[0] if np.abs(jac).max() > 1e-9 else -err
        x = np.clip(x + scale * np.clip(step, -1.0, 1.0), np.log(1e-5), np.log(1e4))
    return best[0], best[1]


REACH_DELTA_E = 2.0  # paper_world warns when the closest colour the view shows is further than this (CIE76)


def paper_world(scene, hex_colour: str, light: float = 1.0, exposure: float | None = None,
                look: str | None = None, short=None) -> dict:  # fmt: skip
    """A plain backdrop that shows on screen as `hex_colour` (e.g. the brand's paper "#F3F2EE") under the scene's
    view transform (AgX greys and darkens a plain world of the same value). The colour the camera sees is solved
    numerically through Blender's own colour management; what lights the scene is kept separate (the hex as
    scene-linear colour times `light`), via a Light Path "Is Camera Ray" mix, so a bright backdrop does not
    over-light the models. `exposure` and `look` set the view first (default: `brand.exposure`, `brand.look` from
    the settings when set, else the scene's own; never for a short whose look is fresh: the short passed, or the
    one setup_scene stamped on the scene).

    Returns {"world_color" (scene-linear RGB), "srgb" (hex it shows as), "delta_e" (from the target, CIE76),
    "view"}, plus "warning" (also printed) when delta_e is over REACH_DELTA_E: the view can't reach that colour, so
    the stills check would find the background off. Say so to the user then, or change the exposure or look."""
    from bsa.colour import delta_e, hex_to_srgb, srgb_to_hex, srgb_to_linear

    view = brand_view(scene, short=short) if exposure is None and look is None else {}
    if exposure is not None:
        scene.view_settings.exposure = float(exposure)
        view["exposure"] = float(exposure)
    if look is not None:
        view["look"] = set_look(scene, look)
    target = np.array(hex_to_srgb(hex_colour))
    lin, shown = _solve_linear(scene, target)
    world = scene.world or bpy.data.worlds.new(f"{scene.name} paper")
    scene.world = world
    world["bsa_backdrop"] = "paper"  # the stills' background check applies (bsa.colour.flat_backdrop)
    world["bsa_paper"] = {"hex": hex_colour.upper(), "light": float(light)}  # a style change solves it again
    world.use_nodes = True
    nt = world.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputWorld")
    seen = nt.nodes.new("ShaderNodeBackground")
    seen.inputs["Color"].default_value = (*lin, 1.0)
    seen.inputs["Strength"].default_value = 1.0
    lit = nt.nodes.new("ShaderNodeBackground")
    lit.inputs["Color"].default_value = (*srgb_to_linear(target), 1.0)
    lit.inputs["Strength"].default_value = float(light)
    path = nt.nodes.new("ShaderNodeLightPath")
    mix = nt.nodes.new("ShaderNodeMixShader")
    nt.links.new(path.outputs["Is Camera Ray"], mix.inputs["Fac"])
    nt.links.new(lit.outputs["Background"], mix.inputs[1])
    nt.links.new(seen.outputs["Background"], mix.inputs[2])
    nt.links.new(mix.outputs["Shader"], out.inputs["Surface"])
    scene.render.film_transparent = False
    out = {"world_color": tuple(float(v) for v in lin), "srgb": srgb_to_hex(shown),
           "delta_e": round(delta_e(target, shown), 2), "view": view}  # fmt: skip
    if out["delta_e"] > REACH_DELTA_E:
        vs = scene.view_settings
        out["warning"] = (f"paper_world: the view ({vs.view_transform}, look {vs.look}, exposure {vs.exposure:+.2f}) "
                          f"can't reach {hex_colour.upper()}: the backdrop shows as {out['srgb']}, ΔE "
                          f"{out['delta_e']:.1f}. Change exposure= or look=, or accept {out['srgb']} as the "
                          "background")  # fmt: skip
        print(out["warning"])
    return out


def image_material(name: str, maps: dict[str, Path]):
    """Material from downloaded PBR maps. Keys: base_color, roughness, normal, displacement. Images are packed."""
    mat = bpy.data.materials.new(name)
    nt, bsdf, out = _principled(mat)

    def load(key, colorspace):
        path = maps.get(key)
        if path is None:
            return None
        img = bpy.data.images.load(str(path))
        img.colorspace_settings.name = colorspace
        img.pack()
        node = nt.nodes.new("ShaderNodeTexImage")
        node.image = img
        return node

    base = load("base_color", "sRGB")
    if base:
        nt.links.new(base.outputs["Color"], bsdf.inputs["Base Color"])
    rough = load("roughness", "Non-Color")
    if rough:
        nt.links.new(rough.outputs["Color"], bsdf.inputs["Roughness"])
    normal = load("normal", "Non-Color")
    if normal:
        nm = nt.nodes.new("ShaderNodeNormalMap")
        nt.links.new(normal.outputs["Color"], nm.inputs["Color"])
        nt.links.new(nm.outputs["Normal"], bsdf.inputs["Normal"])
    disp = load("displacement", "Non-Color")
    if disp:
        dn = nt.nodes.new("ShaderNodeDisplacement")
        nt.links.new(disp.outputs["Color"], dn.inputs["Height"])
        nt.links.new(dn.outputs["Displacement"], out.inputs["Displacement"])
    return mat


SUN_ABOVE_M = 3.0  # subjects larger than this (metres) are lit with sun lamps: no falloff across them
AREA_DISTANCE = 8.0  # area lights stand this many subject sizes from its centre: under 30% falloff across it
AREA_SOFTNESS = 0.4  # an area light's size, as a share of its distance (soft shadows)
SUN_ANGLE_DEG = 12.0  # a sun lamp's angular size (soft shadows)
# The three-light rig: name, degrees around the subject from the camera (positive: to the camera's left), degrees
# above the horizon, share of the key's strength, colour.
RIG = (
    ("Key", 45.0, 40.0, 1.0, (1.0, 0.94, 0.86)),
    ("Fill", -55.0, 15.0, 0.35, (0.95, 0.97, 1.0)),
    ("Rim", 160.0, 35.0, 0.8, (0.8, 0.88, 1.0)),
)


def lights(scene, target, size: float, strength: float = 3.0, kind: str | None = None, rig=RIG) -> dict:
    """Light the subject at `target` (a point, or an object: the centre of its bounding box) that is `size` metres
    across (its largest dimension) with a key, a fill and a rim light, placed around the scene camera's view (from
    -Y looking +Y when there is no camera). Subjects larger than SUN_ABOVE_M get sun lamps: a sun has no falloff, so
    a big flat face is lit evenly (area lights scaled up for a 100 m tower stand too close, and light its top far
    more than its base). Smaller subjects get the area-light rig AREA_DISTANCE sizes away, its power set so the key
    lights the subject as a sun of `strength` (W/m2) would. Calling it again moves and updates the same three lights
    ("<scene> Key", "<scene> Fill", "<scene> Rim"). `kind="sun"` lights a subject of any size with suns. Returns
    {"kind": "sun" or "area", "distance": metres from the target (None for suns), "lights": [the three light
    objects]}. `rig` replaces RIG (same shape: name, degrees around, degrees up, share of the key, colour), e.g. a
    brand style's own lights."""
    if kind not in (None, "sun"):
        raise ValueError(f"unknown light kind {kind!r}: use None (chosen by size) or 'sun'")
    centre, back = _aim(scene, target)
    size = max(float(size), 0.01)
    sun = size > SUN_ABOVE_M or kind == "sun"
    distance = None if sun else AREA_DISTANCE * size
    made = []
    for name, around, up, share, colour in rig:
        direction = _direction(back, around, up)
        obj = _light_object(scene, f"{scene.name} {name}", "SUN" if sun else "AREA")
        data = obj.data
        data.color = colour
        if sun:
            data.energy = strength * share
            data.angle = math.radians(SUN_ANGLE_DEG)
            obj.location = centre + direction * size
        else:
            data.size = AREA_SOFTNESS * distance
            data.energy = strength * share * math.pi * distance * distance  # irradiance P / (pi d^2) at the target
            obj.location = centre + direction * distance
        obj.rotation_euler = (centre - obj.location).to_track_quat("-Z", "Y").to_euler()
        made.append(obj)
    return {"kind": "sun" if sun else "area", "distance": distance, "lights": made}


def _aim(scene, target):
    """(the target's centre, the flat unit direction from it toward the camera; -Y when there is no camera)."""
    from mathutils import Vector

    if hasattr(target, "bound_box"):
        corners = [target.matrix_world @ Vector(c) for c in target.bound_box]
        centre = sum(corners, Vector()) / len(corners)
    else:
        centre = Vector(target)
    cam = scene.camera
    back = (cam.matrix_world.translation - centre) if cam is not None else Vector((0.0, -1.0, 0.0))
    back.z = 0.0
    back = back.normalized() if back.length > 1e-9 else Vector((0.0, -1.0, 0.0))
    return centre, back


def _direction(back, around: float, up: float):
    """The unit direction `around` degrees round from `back` (to the camera's left when positive), `up` above."""
    from mathutils import Matrix, Vector

    flat = Matrix.Rotation(math.radians(around), 3, "Z") @ back
    return (flat * math.cos(math.radians(up)) + Vector((0.0, 0.0, math.sin(math.radians(up))))).normalized()


def _light_object(scene, full: str, kind: str):
    """The light object `full` of type `kind` ("SUN", "AREA"), made or reused, linked to the scene."""
    obj = bpy.data.objects.get(full)
    if obj is None or obj.type != "LIGHT":
        obj = bpy.data.objects.new(full, bpy.data.lights.new(full, kind))
    if obj.data.type != kind:
        obj.data.type = kind
    if scene.collection.objects.get(obj.name) is None and obj.name not in scene.objects:
        scene.collection.objects.link(obj)
    return obj


# --- brand kit looks: stamped materials and lights, ink lines, engine matching ------------------------------------

KIT_KINDS = ("plastic", "metal", "glass", "emissive", "toy", "toy_water", "matte")
PATTERNS = (None, "dots", "grid", "wood")
TOY_ROUGHNESS, TOY_COAT, TOY_COAT_ROUGHNESS = 0.28, 0.6, 0.08
MATTE_ROUGHNESS = 0.6
_KIND_ROUGHNESS = {"plastic": 0.4, "metal": 0.3, "glass": 0.05, "emissive": 0.5}


def _mix_rgba(nt, factor, a, b):
    """A colour Mix node from `a` (factor 0) to `b` (factor 1), `factor` an output socket; returns its colour result.
    Sockets by identifier: the node's float, vector and colour sockets share the names "A", "B", "Result"."""
    mix = nt.nodes.new("ShaderNodeMix")
    mix.data_type = "RGBA"

    def sock(sockets, ident):
        return next(s for s in sockets if s.identifier == ident)

    nt.links.new(factor, sock(mix.inputs, "Factor_Float"))
    sock(mix.inputs, "A_Color").default_value = (*a, 1.0)
    sock(mix.inputs, "B_Color").default_value = (*b, 1.0)
    return sock(mix.outputs, "Result_Color")


def _math(nt, operation: str, b: float | None = None):
    node = nt.nodes.new("ShaderNodeMath")
    node.operation = operation
    if b is not None:
        node.inputs[1].default_value = b
    return node


def _vmath(nt, operation: str):
    node = nt.nodes.new("ShaderNodeVectorMath")
    node.operation = operation
    return node


def _wood(nt, coords, lin):
    """Wood grain: a distorted wave along the object's X between the colour x 0.72 and the colour, a little bump."""
    mapping = nt.nodes.new("ShaderNodeMapping")
    mapping.inputs["Scale"].default_value = (0.25, 2.5, 2.5)
    wave = nt.nodes.new("ShaderNodeTexWave")
    wave.inputs["Scale"].default_value = 2.0
    wave.inputs["Distortion"].default_value = 6.0
    wave.inputs["Detail"].default_value = 3.0
    nt.links.new(coords.outputs["Object"], mapping.inputs["Vector"])
    nt.links.new(mapping.outputs["Vector"], wave.inputs["Vector"])
    return _mix_rgba(nt, wave.outputs["Fac"], [c * 0.72 for c in lin], lin), wave.outputs["Fac"]


def _dots(nt, coords, lin):
    """Pegboard: dark round holes (x 0.35) on a 4 cm pitch, in the object's XY plane."""
    scale = _vmath(nt, "SCALE")
    scale.inputs["Scale"].default_value = 25.0
    frac = _vmath(nt, "FRACTION")
    centre = _vmath(nt, "SUBTRACT")
    centre.inputs[1].default_value = (0.5, 0.5, 0.5)
    sep = nt.nodes.new("ShaderNodeSeparateXYZ")
    flat = nt.nodes.new("ShaderNodeCombineXYZ")
    length = _vmath(nt, "LENGTH")
    hole = _math(nt, "LESS_THAN", 0.18)
    nt.links.new(coords.outputs["Object"], scale.inputs[0])
    nt.links.new(scale.outputs["Vector"], frac.inputs[0])
    nt.links.new(frac.outputs["Vector"], centre.inputs[0])
    nt.links.new(centre.outputs["Vector"], sep.inputs["Vector"])
    nt.links.new(sep.outputs["X"], flat.inputs["X"])
    nt.links.new(sep.outputs["Y"], flat.inputs["Y"])
    nt.links.new(flat.outputs["Vector"], length.inputs[0])
    nt.links.new(length.outputs["Value"], hole.inputs[0])
    return _mix_rgba(nt, hole.outputs["Value"], lin, [c * 0.35 for c in lin]), None


def _grid(nt, coords, lin):
    """Graph paper: lighter lines (30% toward white) every 25 cm and every metre, in the object's XY plane (the style
    sampler's grid)."""
    acc = None
    for every, width in ((4.0, 0.035), (1.0, 0.012)):
        scale = _vmath(nt, "SCALE")
        scale.inputs["Scale"].default_value = every
        frac = _vmath(nt, "FRACTION")
        sep = nt.nodes.new("ShaderNodeSeparateXYZ")
        nt.links.new(coords.outputs["Object"], scale.inputs[0])
        nt.links.new(scale.outputs["Vector"], frac.inputs[0])
        nt.links.new(frac.outputs["Vector"], sep.inputs["Vector"])
        for axis in ("X", "Y"):
            line = _math(nt, "LESS_THAN", width)
            nt.links.new(sep.outputs[axis], line.inputs[0])
            if acc is None:
                acc = line
                continue
            top = _math(nt, "MAXIMUM")
            nt.links.new(acc.outputs["Value"], top.inputs[0])
            nt.links.new(line.outputs["Value"], top.inputs[1])
            acc = top
    return _mix_rgba(nt, acc.outputs["Value"], lin, [c + (1.0 - c) * 0.3 for c in lin]), None


_PATTERN_NODES = {"wood": _wood, "dots": _dots, "grid": _grid}


def kit_material(name: str, kind: str, colour: str, pattern: str | None = None):
    """A brand kit material in `colour` ("#RRGGBB"): realistic Principled, made as `material` / `toy_water` make
    them, of one of KIT_KINDS ("toy": a glossy toy plastic, roughness 0.28 under a clear coat; "matte": roughness
    0.6), with an optional pattern from PATTERNS made from the colour ("dots" pegboard, "grid" graph lines, "wood"
    grain). Stamped `mat["bsa_kit"]` (read it with `stamp`) so a brand style can rebuild it in its own look; with no
    style it is what the realistic style keeps. ValueError for an unknown kind or pattern."""
    from bsa import colour as colours

    if kind not in KIT_KINDS:
        raise ValueError(f"unknown kit material kind {kind!r}: use one of {', '.join(KIT_KINDS)}")
    if pattern not in PATTERNS:
        raise ValueError(f"unknown pattern {pattern!r}: use one of None, {', '.join(p for p in PATTERNS if p)}")
    hex_ = colours.srgb_to_hex(colours.hex_to_srgb(colour)).upper()
    lin = [float(c) for c in colours.srgb_to_linear(colours.hex_to_srgb(hex_))]
    if kind == "toy_water":
        mat = toy_water(name, hex_)
    elif kind in ("toy", "matte"):
        mat = material(name, lin, roughness=TOY_ROUGHNESS if kind == "toy" else MATTE_ROUGHNESS)
    else:
        mat = material(name, lin, roughness=_KIND_ROUGHNESS[kind], kind=kind)
    nt = mat.node_tree
    bsdf = next(n for n in nt.nodes if n.type == "BSDF_PRINCIPLED")
    if kind == "toy":
        for key in ("Coat Weight", "Clearcoat"):  # Blender 4.0+, then 3.x
            _set(bsdf, key, TOY_COAT)
        for key in ("Coat Roughness", "Clearcoat Roughness"):
            _set(bsdf, key, TOY_COAT_ROUGHNESS)
    if pattern is not None:
        shade, height = _PATTERN_NODES[pattern](nt, nt.nodes.new("ShaderNodeTexCoord"), lin)
        nt.links.new(shade, bsdf.inputs["Base Color"])
        if kind == "emissive":
            nt.links.new(shade, bsdf.inputs["Emission Color"])
        if height is not None:
            bump = nt.nodes.new("ShaderNodeBump")
            bump.inputs["Strength"].default_value = 0.08
            nt.links.new(height, bump.inputs["Height"])
            nt.links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])
    mat["bsa_kit"] = {"kind": kind, "colour": hex_, "pattern": pattern or ""}
    return mat


def stamp(mat) -> dict | None:
    """A kit material's stamp {"kind", "colour" ("#RRGGBB"), "pattern" (None when it has none)}, or None when `mat`
    was not made by kit_material."""
    if mat is None or "bsa_kit" not in mat:
        return None
    raw = mat["bsa_kit"]
    return {"kind": str(raw["kind"]), "colour": str(raw["colour"]), "pattern": str(raw["pattern"]) or None}


_RIG_NAMES = ("Key", "Fill", "Rim", "Top", "Sun")


def kit_lights(scene, target, size: float, strength: float, *, single_sun: bool = False, sun: bool = False,
               sun_angle_deg: float = SUN_ANGLE_DEG, cycles_scale: float = 1.0, rig=RIG) -> dict:  # fmt: skip
    """A brand style's lights: the `lights` rig (suns with `sun=True`; with `sun=False` as `lights` picks by size:
    the area rig, suns over SUN_ABOVE_M; each sun `sun_angle_deg` across), or with `single_sun` one white sun
    "<scene> Sun" from the key's direction, `sun_angle_deg` across, of `strength`. This scene's other "<scene> Key/Fill/Rim/Top/Sun" lights are removed. Each light is stamped
    `light["bsa_energy"] = {"eevee": e, "cycles": e * cycles_scale}` and set for the scene's engine now (match_engine
    sets it again for the engine a render uses). `rig` (default RIG) is the style's own rig for `lights`; the
    single sun comes from its first light's direction. Returns {"kind", "distance", "lights"} as `lights` does."""
    if single_sun:
        centre, back = _aim(scene, target)
        _name, around, up, _share, _colour = rig[0]
        obj = _light_object(scene, f"{scene.name} Sun", "SUN")
        obj.data.color = (1.0, 1.0, 1.0)
        obj.data.energy = float(strength)
        obj.data.angle = math.radians(sun_angle_deg)
        obj.location = centre + _direction(back, around, up) * max(float(size), 0.01)
        obj.rotation_euler = (centre - obj.location).to_track_quat("-Z", "Y").to_euler()
        out = {"kind": "sun", "distance": None, "lights": [obj]}
    else:
        out = lights(scene, target, size, strength, kind="sun" if sun else None, rig=rig)
        if out["kind"] == "sun":
            for obj in out["lights"]:
                obj.data.angle = math.radians(sun_angle_deg)
    keep = {o.name for o in out["lights"]}
    for name in _RIG_NAMES:
        old = bpy.data.objects.get(f"{scene.name} {name}")
        if old is not None and old.name not in keep:
            data = old.data
            bpy.data.objects.remove(old)
            if data is not None and data.users == 0:
                bpy.data.lights.remove(data)
    engine = _engine(scene)
    for obj in out["lights"]:
        e = float(obj.data.energy)
        obj["bsa_energy"] = {"eevee": e, "cycles": e * float(cycles_scale)}
        obj.data.energy = float(obj["bsa_energy"][engine])
    return out


INK_LINESET = "bsa ink"
INK_PREV = "bsa_ink_prev"
CREASE_DEG = 140.0
_NO_LINE_ROLES = ("floor", "wall", "backdrop")


def _freestyle_state(scene) -> dict:
    """Everything ink_lines changes on the scene and its view layers, to put back when the ink goes off."""
    return {
        "use_freestyle": bool(scene.render.use_freestyle),
        "thickness_mode": scene.render.line_thickness_mode,
        "layers": {
            layer.name: {
                "use_freestyle": bool(layer.use_freestyle),
                "mode": layer.freestyle_settings.mode,
                "crease_angle": float(layer.freestyle_settings.crease_angle),
                "show_render": {ls.name: bool(ls.show_render) for ls in layer.freestyle_settings.linesets
                                if ls.name != INK_LINESET},
            }
            for layer in scene.view_layers
        },
    }  # fmt: skip


def _ink_off(scene) -> None:
    """Undo ink_lines: remove "bsa ink" (and its line style when nothing else uses it) and "<scene> no lines", and put
    the stamped Freestyle state back. Nothing at all when the ink was never on."""
    prev = scene.get(INK_PREV)
    has_ink = any(layer.freestyle_settings.linesets.get(INK_LINESET) for layer in scene.view_layers)
    if prev is None and not has_ink:
        return
    for layer in scene.view_layers:
        fs = layer.freestyle_settings
        ls = fs.linesets.get(INK_LINESET)
        if ls is not None:
            style = ls.linestyle
            fs.linesets.remove(ls)
            if style is not None and style.users == 0:
                bpy.data.linestyles.remove(style)
    col = bpy.data.collections.get(f"{scene.name} no lines")
    if col is not None:
        bpy.data.collections.remove(col)
    if prev is None:
        return
    scene.render.use_freestyle = bool(prev["use_freestyle"])
    scene.render.line_thickness_mode = str(prev["thickness_mode"])
    for layer in scene.view_layers:
        was = prev["layers"].get(layer.name)
        if was is None:
            continue
        layer.use_freestyle = bool(was["use_freestyle"])
        fs = layer.freestyle_settings
        fs.mode = str(was["mode"])
        fs.crease_angle = float(was["crease_angle"])
        for ls in fs.linesets:
            if ls.name in was["show_render"]:
                ls.show_render = bool(was["show_render"][ls.name])
    del scene[INK_PREV]


def _no_lines(scene, exclude):
    """The collection "<scene> no lines" (made, and linked under the scene, when missing) holding exactly what the ink
    lines leave out: `exclude`, every object tagged lines:off or overlay, and every set floor, wall and backdrop."""
    from bsa.blender import bake

    name = f"{scene.name} no lines"
    col = bpy.data.collections.get(name) or bpy.data.collections.new(name)
    if scene.collection.children.get(col.name) is None:
        scene.collection.children.link(col)
    want = {o.name for o in exclude}
    for obj in scene.objects:
        if bake.LINES_OFF in bake.tags(obj) or bake.is_overlay(obj) or bake.set_role(obj) in _NO_LINE_ROLES:
            want.add(obj.name)
    for obj in list(col.objects):
        if obj.name not in want:
            col.objects.unlink(obj)
    for obj_name in sorted(want):
        obj = bpy.data.objects.get(obj_name)
        if obj is not None and col.objects.get(obj_name) is None:
            col.objects.link(obj)
    return col


def ink_lines(scene, colour: str, px: float, *, crease: bool = True, exclude=()) -> object:
    """Ink lines drawn by Freestyle at render time, adding no geometry: one lineset "bsa ink" on each of the scene's
    view layers (visible silhouette, border, external contour and, with `crease`, creases sharper than 140 degrees)
    in `colour` ("#RRGGBB"), `px` wide on a 1920-tall frame (RELATIVE thickness, so a smaller render keeps the same
    share). What is in "<scene> no lines" gets no lines (`exclude`, lines:off, overlay, set floor, wall, backdrop).
    Calling it again reuses the lineset and the collection. While the lines are on, the view layers' other linesets
    (a default "LineSet" would draw lines on what is left out) do not render. The first call stamps the Freestyle
    state it found (`scene["bsa_ink_prev"]`); `px <= 0` puts that state back exactly, removes "bsa ink", its line
    style and "<scene> no lines", and does nothing at all when the lines were never on (returns None). Returns the
    lineset."""
    from bsa import colour as colours
    from bsa import looks

    if px <= 0:
        _ink_off(scene)
        return None
    if INK_PREV not in scene:
        scene[INK_PREV] = _freestyle_state(scene)
    scene.render.use_freestyle = True
    scene.render.line_thickness_mode = "RELATIVE"
    col = _no_lines(scene, exclude)
    ink = [float(c) for c in colours.srgb_to_linear(colours.hex_to_srgb(colour))]
    first = None
    for layer in scene.view_layers:
        layer.use_freestyle = True
        fs = layer.freestyle_settings
        fs.mode = "EDITOR"
        fs.crease_angle = math.radians(CREASE_DEG)
        ls = fs.linesets.get(INK_LINESET) or fs.linesets.new(INK_LINESET)
        for other in fs.linesets:  # off while the ink is on; _ink_off renders them again as they were
            if other.name != INK_LINESET:
                other.show_render = False
        ls.show_render = True
        ls.select_by_visibility = True
        ls.visibility = "VISIBLE"
        ls.select_by_edge_types = True
        ls.select_silhouette = True
        ls.select_border = True
        ls.select_external_contour = True
        ls.select_crease = bool(crease)
        ls.select_by_collection = True
        ls.collection = col
        ls.collection_negation = "EXCLUSIVE"
        if ls.linestyle is None:
            ls.linestyle = bpy.data.linestyles.new(INK_LINESET)
        ls.linestyle.color = ink
        ls.linestyle.thickness = looks.freestyle_thickness(px)
        first = first or ls
    return first


def _engine(scene) -> str:
    from bsa.blender.scene import current_engine

    return current_engine(scene)


def match_engine(scene) -> str:
    """Use the stamped values for the engine the scene renders with, read from the scene and never set: each light's
    energy from its `bsa_energy` stamp, each world Background node's strength from `world["bsa_strength"]` ({node
    name: {"eevee", "cycles"}}), and in Cycles `max_bounces` from `scene["bsa_style_bounces"]`; then stamps
    `scene["bsa_style_engine"]`. Unstamped lights and worlds are left alone: a short without a brand style is
    unchanged (and gets no stamp). Any engine but Cycles (EEVEE, Workbench) is matched as "eevee". Returns
    "eevee" or "cycles"."""
    engine = _engine(scene)
    touched = False
    for obj in scene.objects:
        if obj.type == "LIGHT" and "bsa_energy" in obj:
            obj.data.energy = float(obj["bsa_energy"][engine])
            touched = True
    world = scene.world
    if world is not None and "bsa_strength" in world and world.node_tree is not None:
        for node_name, values in world["bsa_strength"].items():
            node = world.node_tree.nodes.get(node_name)
            if node is not None and "Strength" in node.inputs:
                node.inputs["Strength"].default_value = float(values[engine])
        touched = True
    if "bsa_style_bounces" in scene:
        touched = True
        if engine == "cycles":
            scene.cycles.max_bounces = int(scene["bsa_style_bounces"])
    if touched:
        scene["bsa_style_engine"] = engine
    return engine
