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


def lights(scene, target, size: float, strength: float = 3.0) -> dict:
    """Light the subject at `target` (a point, or an object: the centre of its bounding box) that is `size` metres
    across (its largest dimension) with a key, a fill and a rim light, placed around the scene camera's view (from
    -Y looking +Y when there is no camera). Subjects larger than SUN_ABOVE_M get sun lamps: a sun has no falloff, so
    a big flat face is lit evenly (area lights scaled up for a 100 m tower stand too close, and light its top far
    more than its base). Smaller subjects get the area-light rig AREA_DISTANCE sizes away, its power set so the key
    lights the subject as a sun of `strength` (W/m2) would. Calling it again moves and updates the same three lights
    ("<scene> Key", "<scene> Fill", "<scene> Rim"). Returns {"kind": "sun" or "area", "distance": metres from the
    target (None for suns), "lights": [the three light objects]}."""
    from mathutils import Matrix, Vector

    if hasattr(target, "bound_box"):
        corners = [target.matrix_world @ Vector(c) for c in target.bound_box]
        centre = sum(corners, Vector()) / len(corners)
    else:
        centre = Vector(target)
    size = max(float(size), 0.01)
    cam = scene.camera
    back = (cam.matrix_world.translation - centre) if cam is not None else Vector((0.0, -1.0, 0.0))
    back.z = 0.0
    back = back.normalized() if back.length > 1e-9 else Vector((0.0, -1.0, 0.0))
    sun = size > SUN_ABOVE_M
    distance = None if sun else AREA_DISTANCE * size
    made = []
    for name, around, up, share, colour in RIG:
        flat = Matrix.Rotation(math.radians(around), 3, "Z") @ back
        direction = (flat * math.cos(math.radians(up)) + Vector((0.0, 0.0, math.sin(math.radians(up))))).normalized()
        full = f"{scene.name} {name}"
        obj = bpy.data.objects.get(full)
        kind = "SUN" if sun else "AREA"
        if obj is None or obj.type != "LIGHT":
            obj = bpy.data.objects.new(full, bpy.data.lights.new(full, kind))
        if obj.data.type != kind:
            obj.data.type = kind
        if scene.collection.objects.get(obj.name) is None and obj.name not in scene.objects:
            scene.collection.objects.link(obj)
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
