"""Brand styles: one function per style returning a `Style` (its numbers from bsa.looks, how it rebuilds a kit
material, any lines of its own), and `apply` / `reapply`, which restyle a built scene in place: view, kit materials,
bevels, lights, world, ink lines, then the stamped values matched to the engine the scene renders with. Applying
again (the same style or another) leaves the scene as one application would: the same lights, modifiers, materials
and linesets, no ".001" names. Nothing here sets the render engine: that is always the user's choice."""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass

import bpy
import numpy as np

from bsa import colour, looks
from bsa.blender import bake, look
from bsa.kit import DEFAULT_CODE, Kit
from bsa.kit import STYLES as STYLE_NAMES

BEVEL = "bsa style bevel"
BEVEL_ANGLE_DEG = 30.0
SMOOTH_PREV = "bsa_style_smooth_prev"  # a mesh's face smoothing before a style bevel smoothed it
BOUNCES_PREV = "bsa_style_bounces_prev"
NO_WORLD_LIGHT_IN_CYCLES = ("blueprint", "toon", "pencil")
LINE_COLOUR = {"blueprint": "#F2F7FF", "pencil": "#34343A"}  # line styles drawn in their own colour, not the ink
REALISTIC_ROUGHNESS = {"plastic": 0.5, "metal": 0.3, "toy": look.TOY_ROUGHNESS, "matte": look.MATTE_ROUGHNESS,
                       "glass": 0.05, "emissive": 0.5, "toy_water": 0.05}  # fmt: skip
AS_LOOK = ("glass", "toy_water", "emissive")  # kinds every style here keeps as look.kit_material makes them
TOY_TINT, TOY_EDGE_BLEND = 0.35, 0.35
CLAY_ROUGHNESS, CLAY_SPECULAR, CLAY_BUMP, CLAY_BUMP_DISTANCE = 0.88, 0.25, 0.12, 0.02
TOON_INK_LIFT, TOON_INK_STEP, TOON_SET_STEP = 0.15, 0.6, 0.78  # the sampler's ink parts (#3A4152-style) and set step
BLUEPRINT_KEY_ALPHA, BLUEPRINT_ALPHA = 0.85, 0.22
BLUEPRINT_GRID = ((4.0, 0.035), (1.0, 0.012))  # (lines per metre, line width as a share of a cell): minor, major
BLUEPRINT_GRID_SHIFT = 0.0371  # the sampler's offset, so no grid line runs exactly along a face at a whole metre
HIDDEN_LINESET = "bsa hidden"
BLUEPRINT_HIDDEN_COLOUR, BLUEPRINT_HIDDEN_ALPHA, BLUEPRINT_DASH = (0.85, 0.9, 1.0), 0.45, (5, 4)


@dataclass
class Ctx:
    scene: object
    kit: Kit
    style: str
    size: float
    target: object
    key: frozenset[str]
    set_only: frozenset[str] = frozenset()  # names of the materials used only on set objects (bake.is_set)

    def scenery(self, mat) -> bool:
        """True for a material only set objects use: the styles here make it a matte Principled."""
        return mat.name in self.set_only


@dataclass(frozen=True)
class Style:
    name: str
    look: looks.StyleLook
    material: Callable  # (Material, stamp dict, Ctx, on a key part) -> None: rebuilds the node tree in place
    lines: Callable | None = None  # (Ctx) -> None: Freestyle beyond ink_lines, in linesets named "bsa <...>"


# --- material building blocks -------------------------------------------------------------------------------------


def _lin(hex_: str) -> list[float]:
    return [float(c) for c in colour.srgb_to_linear(colour.hex_to_srgb(hex_))]


def _set(node, name: str, value) -> None:
    if name in node.inputs:
        node.inputs[name].default_value = value


def _reset(mat):
    """Empty the material's node tree (in place: its name and users stay), opaque, back faces drawn."""
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()
    if hasattr(mat, "surface_render_method"):
        mat.surface_render_method = "DITHERED"
    if hasattr(mat, "blend_method"):
        try:
            mat.blend_method = "OPAQUE"
        except (TypeError, AttributeError):
            pass
    if hasattr(mat, "use_backface_culling"):
        mat.use_backface_culling = False
    if hasattr(getattr(mat, "cycles", None), "emission_sampling"):
        mat.cycles.emission_sampling = "AUTO"
    return nt


def _principled(mat):
    nt = _reset(mat)
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
    nt.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])
    return nt, bsdf


def _shade(nt, bsdf, lin, pattern, *, bump: bool = True, emission: bool = False):
    """Base Color from the flat colour `lin`, or from `pattern` made from it (look's dots, grid, wood: kept in every
    style); returns the socket driving Base Color, or None when it is the flat colour."""
    _set(bsdf, "Base Color", (*lin, 1.0))
    if pattern is None:
        return None
    shade, height = look._PATTERN_NODES[pattern](nt, nt.nodes.new("ShaderNodeTexCoord"), lin)
    nt.links.new(shade, bsdf.inputs["Base Color"])
    if emission:
        nt.links.new(shade, bsdf.inputs["Emission Color"])
    if bump and height is not None:
        node = nt.nodes.new("ShaderNodeBump")
        node.inputs["Strength"].default_value = 0.08
        nt.links.new(height, node.inputs["Height"])
        nt.links.new(node.outputs["Normal"], bsdf.inputs["Normal"])
    return shade


def _coat(bsdf, weight: float, roughness: float) -> None:
    for key in ("Coat Weight", "Clearcoat"):
        _set(bsdf, key, weight)
    for key in ("Coat Roughness", "Clearcoat Roughness"):
        _set(bsdf, key, roughness)


def _kit_principled(mat, kind: str, hex_: str, pattern, roughness: float) -> None:
    """The Principled material look.kit_material makes for `kind`, in `hex_`, with `roughness`."""
    nt, bsdf = _principled(mat)
    lin = _lin(hex_)
    _shade(nt, bsdf, lin, pattern, emission=kind == "emissive")
    _set(bsdf, "Roughness", roughness)
    _set(bsdf, "Metallic", 1.0 if kind == "metal" else 0.0)
    if kind == "toy":
        _coat(bsdf, look.TOY_COAT, look.TOY_COAT_ROUGHNESS)
    elif kind == "glass":
        _set(bsdf, "Transmission Weight", 1.0)
        _set(bsdf, "IOR", 1.45)
    elif kind == "emissive":
        _set(bsdf, "Emission Color", (*lin, 1.0))
        _set(bsdf, "Emission Strength", 5.0)
    elif kind == "toy_water":
        _set(bsdf, "Alpha", look.WATER_ALPHA)
        _coat(bsdf, 1.0, 0.03)
        _set(bsdf, "IOR", 1.33)
        if hasattr(mat, "surface_render_method"):
            mat.surface_render_method = "BLENDED"
        if hasattr(mat, "blend_method"):
            try:
                mat.blend_method = "BLEND"
            except (TypeError, AttributeError):
                pass


def _common(mat, stamp: dict, hex_: str, ctx: Ctx) -> bool:
    """What every style here makes the same way: a material only set objects use (floor, wall, props) is a matte
    Principled of its colour, its pattern kept; glass, toy_water and emissive parts are made as look makes them. Every
    other part, whatever its kind (matte too), gets the style's own material. True when `mat` was made here."""
    if ctx.scenery(mat):
        _kit_principled(mat, "matte", hex_, stamp["pattern"], look.MATTE_ROUGHNESS)
        return True
    kind = stamp["kind"]
    if kind in AS_LOOK:
        _kit_principled(mat, kind, hex_, stamp["pattern"], REALISTIC_ROUGHNESS[kind])
        return True
    return False


# --- the styles ---------------------------------------------------------------------------------------------------


def realistic(kit: Kit) -> Style:
    """Today's look made from the kit: Principled per kind (plastic roughness 0.5, metal 0.3, toy plastic under a
    clear coat), the AgX view."""

    def material(mat, stamp, ctx, key):
        hex_ = looks.part_colour("realistic", stamp["colour"], ctx.kit, key)
        if _common(mat, stamp, hex_, ctx):
            return
        _kit_principled(mat, stamp["kind"], hex_, stamp["pattern"], REALISTIC_ROUGHNESS[stamp["kind"]])

    return Style("realistic", looks.TABLE["realistic"], material)


def toy(kit: Kit) -> Style:
    """Glossy toy plastic: roughness 0.28 under a clear coat (0.6, roughness 0.08), edges toned toward a 35% white tint
    of the same colour at grazing angles; the Standard view, so kit colours read true."""

    def material(mat, stamp, ctx, key):
        hex_ = looks.part_colour("toy", stamp["colour"], ctx.kit, key)
        if _common(mat, stamp, hex_, ctx):
            return
        nt, bsdf = _principled(mat)
        lin = _lin(hex_)
        shade = _shade(nt, bsdf, lin, stamp["pattern"])
        _set(bsdf, "Roughness", look.TOY_ROUGHNESS)
        _set(bsdf, "Metallic", 0.0)
        _set(bsdf, "Specular IOR Level", 0.5)
        _coat(bsdf, look.TOY_COAT, look.TOY_COAT_ROUGHNESS)
        weight = nt.nodes.new("ShaderNodeLayerWeight")
        weight.inputs["Blend"].default_value = TOY_EDGE_BLEND
        edge = nt.nodes.new("ShaderNodeMapRange")
        for name, value in (("From Min", 0.3), ("From Max", 1.0), ("To Min", 0.0), ("To Max", 0.6)):
            edge.inputs[name].default_value = value
        nt.links.new(weight.outputs["Facing"], edge.inputs["Value"])
        tinted = look._mix_rgba(nt, edge.outputs["Result"], lin, [c + (1.0 - c) * TOY_TINT for c in lin])
        if shade is not None:
            a = next(s for s in tinted.node.inputs if s.identifier == "A_Color")
            nt.links.new(shade, a)
        nt.links.new(tinted, bsdf.inputs["Base Color"])

    return Style("toy", looks.TABLE["toy"], material)


def clay(kit: Kit) -> Style:
    """Matte modelling clay: roughness 0.88, low specular, a faint object-space bump of noise and rings, colours
    warmed (looks.warm); AgX with a medium high contrast look."""

    def material(mat, stamp, ctx, key):
        hex_ = looks.part_colour("clay", stamp["colour"], ctx.kit, key)
        if _common(mat, stamp, hex_, ctx):
            return
        nt, bsdf = _principled(mat)
        _shade(nt, bsdf, _lin(hex_), stamp["pattern"], bump=False)
        _set(bsdf, "Roughness", CLAY_ROUGHNESS)
        _set(bsdf, "Metallic", 0.0)
        _set(bsdf, "Specular IOR Level", CLAY_SPECULAR)
        coords = nt.nodes.new("ShaderNodeTexCoord")
        noise = nt.nodes.new("ShaderNodeTexNoise")
        noise.inputs["Scale"].default_value = 18.0
        noise.inputs["Detail"].default_value = 6.0
        rings = nt.nodes.new("ShaderNodeTexWave")
        rings.wave_type = "RINGS"
        rings.inputs["Scale"].default_value = 9.0
        rings.inputs["Distortion"].default_value = 4.0
        rings.inputs["Detail"].default_value = 1.0
        height = look._math(nt, "MULTIPLY_ADD", 0.35)
        nt.links.new(coords.outputs["Object"], noise.inputs["Vector"])
        nt.links.new(coords.outputs["Object"], rings.inputs["Vector"])
        nt.links.new(rings.outputs["Fac"], height.inputs[0])
        nt.links.new(noise.outputs["Fac"], height.inputs[2])
        bump = nt.nodes.new("ShaderNodeBump")
        bump.inputs["Strength"].default_value = CLAY_BUMP
        bump.inputs["Distance"].default_value = CLAY_BUMP_DISTANCE
        nt.links.new(height.outputs["Value"], bump.inputs["Height"])
        nt.links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])

    return Style("clay", looks.TABLE["clay"], material)


def _outputs(nt, eevee, cycles) -> None:
    """One Material Output per engine: `eevee` (a shader socket) for EEVEE, `cycles` for Cycles."""
    for target, socket in (("EEVEE", eevee), ("CYCLES", cycles)):
        out = nt.nodes.new("ShaderNodeOutputMaterial")
        out.target = target
        nt.links.new(socket, out.inputs["Surface"])


def _emission(nt, lin, strength: float = 1.0):
    node = nt.nodes.new("ShaderNodeEmission")
    node.inputs["Color"].default_value = (*lin, 1.0)
    node.inputs["Strength"].default_value = strength
    return node


def _see_through(mat, nt, shader, alpha: float):
    """`shader` at `alpha` over what is behind it (a Transparent BSDF mix), blended in EEVEE."""
    clear = nt.nodes.new("ShaderNodeBsdfTransparent")
    mix = nt.nodes.new("ShaderNodeMixShader")
    mix.inputs["Fac"].default_value = alpha
    nt.links.new(clear.outputs["BSDF"], mix.inputs[1])
    nt.links.new(shader, mix.inputs[2])
    if hasattr(mat, "surface_render_method"):
        mat.surface_render_method = "BLENDED"
    return mix.outputs["Shader"]


def _cel(mat, lin, shadow, alpha: float | None = None) -> None:
    """The sampler's toon material: the colour `lin` where the sun reaches, the flat dark step `shadow` elsewhere,
    lit by nothing else, so the lit area shows the exact colour under the Standard view. EEVEE: Diffuse -> Shader to
    RGB -> a constant ramp at looks.RAMP_AT -> mix(shadow, colour) -> Emission. Cycles (no Shader to RGB there):
    Emission(shadow) + a Toon BSDF (diffuse) of colour - shadow, size looks.TOON_SIZE, smooth 0, which the calibrated
    sun (looks.cycles_energy) brings up to the colour exactly; the flat step glows for camera rays only, so a big cel
    floor or wall lights no other part (in a room it lifted the shadow side ΔE 4.5 off the dark step and EEVEE).
    `alpha` (glass, water): seen through at that alpha."""
    nt = _reset(mat)
    diffuse = nt.nodes.new("ShaderNodeBsdfDiffuse")
    to_rgb = nt.nodes.new("ShaderNodeShaderToRGB")
    ramp = nt.nodes.new("ShaderNodeValToRGB")
    ramp.color_ramp.interpolation = "CONSTANT"
    dark, light = ramp.color_ramp.elements
    dark.position, dark.color = 0.0, (0.0, 0.0, 0.0, 1.0)
    light.position, light.color = looks.RAMP_AT, (1.0, 1.0, 1.0, 1.0)
    nt.links.new(diffuse.outputs["BSDF"], to_rgb.inputs["Shader"])
    nt.links.new(to_rgb.outputs["Color"], ramp.inputs["Fac"])
    flat = look._mix_rgba(nt, ramp.outputs["Color"], shadow, lin)
    eevee = nt.nodes.new("ShaderNodeEmission")
    nt.links.new(flat, eevee.inputs["Color"])
    toon_bsdf = nt.nodes.new("ShaderNodeBsdfToon")
    toon_bsdf.component = "DIFFUSE"
    toon_bsdf.inputs["Color"].default_value = (*[max(0.0, c - s) for c, s in zip(lin, shadow)], 1.0)
    toon_bsdf.inputs["Size"].default_value = looks.TOON_SIZE
    toon_bsdf.inputs["Smooth"].default_value = 0.0
    add = nt.nodes.new("ShaderNodeAddShader")
    base = _emission(nt, shadow)
    path = nt.nodes.new("ShaderNodeLightPath")  # the flat step is for the camera: it lights no other part
    nt.links.new(path.outputs["Is Camera Ray"], base.inputs["Strength"])
    if hasattr(getattr(mat, "cycles", None), "emission_sampling"):
        mat.cycles.emission_sampling = "NONE"
    nt.links.new(base.outputs["Emission"], add.inputs[0])
    nt.links.new(toon_bsdf.outputs["BSDF"], add.inputs[1])
    shaders = [eevee.outputs["Emission"], add.outputs["Shader"]]
    if alpha is not None:
        shaders = [_see_through(mat, nt, s, alpha) for s in shaders]
    _outputs(nt, *shaders)


def _heat(kit: Kit) -> str:
    return kit.categories.get("heat") or DEFAULT_CODE["heat"]


def toon(kit: Kit) -> Style:
    """Cel shading: every part flat in its kit colour where the single sun reaches and a fixed dark step
    (looks.shadow_of: x 0.68, 15% toward the ink) where it does not, a material output per engine (`_cel`). Ink parts
    show the ink lifted 15% toward white (their dark step: the ink x 0.6), so their lines still read; set surfaces get
    the same cel material in their colour, a lighter step toward the heat colour (the sampler's x 0.78). Emissive parts
    glow flat; glass and water are cel at look.WATER_ALPHA. Ink lines from the style's line width."""
    ink = colour.srgb_to_hex(colour.hex_to_srgb(kit.palette["ink"]))

    def material(mat, stamp, ctx, key):
        hex_ = looks.part_colour("toon", stamp["colour"], ctx.kit, key)
        if ctx.scenery(mat):
            _cel(mat, _lin(hex_), _lin(looks.shadow_of(hex_, _heat(ctx.kit), TOON_SET_STEP)))
            return
        kind = stamp["kind"]
        if kind == "emissive":
            nt = _reset(mat)
            glow = _emission(nt, _lin(hex_)).outputs["Emission"]
            _outputs(nt, glow, glow)
            return
        alpha = look.WATER_ALPHA if kind in ("glass", "toy_water") else None
        if hex_ == ink:
            _cel(mat, _lin(colour.mix(ink, "#FFFFFF", TOON_INK_LIFT)), [c * TOON_INK_STEP for c in _lin(ink)], alpha)
            return
        _cel(mat, _lin(hex_), _lin(looks.shadow_of(hex_, ink)), alpha)

    return Style("toon", looks.TABLE["toon"], material)


def _grid_paper(mat, paper) -> None:
    """The sampler's blueprint grid paper: flat emission of `paper` (linear) with lines 10% toward white every 25 cm
    (width 0.035 of a cell) and 22% toward white every metre (0.012), in object space on all three axes."""
    nt = _reset(mat)
    coords = nt.nodes.new("ShaderNodeTexCoord")
    masks = []
    for every, width in BLUEPRINT_GRID:
        shift = look._vmath(nt, "ADD")
        shift.inputs[1].default_value = (BLUEPRINT_GRID_SHIFT,) * 3
        scale = look._vmath(nt, "SCALE")
        scale.inputs["Scale"].default_value = every
        frac = look._vmath(nt, "FRACTION")
        sep = nt.nodes.new("ShaderNodeSeparateXYZ")
        nt.links.new(coords.outputs["Object"], shift.inputs[0])
        nt.links.new(shift.outputs["Vector"], scale.inputs[0])
        nt.links.new(scale.outputs["Vector"], frac.inputs[0])
        nt.links.new(frac.outputs["Vector"], sep.inputs["Vector"])
        acc = None
        for axis in ("X", "Y", "Z"):
            line = look._math(nt, "LESS_THAN", width)
            nt.links.new(sep.outputs[axis], line.inputs[0])
            if acc is not None:
                top = look._math(nt, "MAXIMUM")
                nt.links.new(acc.outputs["Value"], top.inputs[0])
                nt.links.new(line.outputs["Value"], top.inputs[1])
                line = top
            acc = line
        masks.append(acc.outputs["Value"])
    white = [1.0, 1.0, 1.0]
    minor = look._mix_rgba(nt, masks[0], paper, looks._toward(paper, white, 0.10))
    major = look._mix_rgba(nt, masks[1], paper, looks._toward(paper, white, 0.22))
    nt.links.new(minor, next(s for s in major.node.inputs if s.identifier == "A_Color"))
    glow = nt.nodes.new("ShaderNodeEmission")
    nt.links.new(major, glow.inputs["Color"])
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    nt.links.new(glow.outputs["Emission"], out.inputs["Surface"])


def blueprint(kit: Kit) -> Style:
    """A technical drawing on blue grid paper: every part a flat see-through fill (emission over a Transparent BSDF,
    one output for both engines, blended in EEVEE): key parts the accent at alpha 0.85, the rest the paper 75% toward
    white at 0.22. Set surfaces are the grid paper. No lights; the world is the paper. Lines white-blue with contours,
    and hidden edges dashed (`_blueprint_lines`)."""

    def material(mat, stamp, ctx, key):
        if ctx.scenery(mat):
            _grid_paper(mat, _lin(looks.blueprint_paper(ctx.kit)))
            return
        nt = _reset(mat)
        fill = _emission(nt, _lin(looks.part_colour("blueprint", stamp["colour"], ctx.kit, key)))
        shader = _see_through(mat, nt, fill.outputs["Emission"], BLUEPRINT_KEY_ALPHA if key else BLUEPRINT_ALPHA)
        out = nt.nodes.new("ShaderNodeOutputMaterial")
        nt.links.new(shader, out.inputs["Surface"])

    return Style("blueprint", looks.TABLE["blueprint"], material, lines=_blueprint_lines)


def _blueprint_lines(ctx: Ctx) -> None:
    """Contours on the ink lineset, and "bsa hidden": the hidden edges dashed 5/4, faint (alpha 0.45), half as thick,
    in the sampler's pale blue, leaving out what the ink leaves out."""
    for layer in ctx.scene.view_layers:
        fs = layer.freestyle_settings
        ink = fs.linesets.get(look.INK_LINESET)
        if ink is None:
            continue
        ink.select_contour = True
        hidden = fs.linesets.get(HIDDEN_LINESET) or fs.linesets.new(HIDDEN_LINESET)
        hidden.show_render = True
        hidden.select_by_visibility = True
        hidden.visibility = "HIDDEN"
        hidden.select_by_edge_types = True
        hidden.select_silhouette = True
        hidden.select_border = True
        hidden.select_crease = True
        hidden.select_by_collection = True
        hidden.collection = ink.collection
        hidden.collection_negation = "EXCLUSIVE"
        if hidden.linestyle is None or hidden.linestyle.name != HIDDEN_LINESET:
            old = hidden.linestyle
            hidden.linestyle = bpy.data.linestyles.get(HIDDEN_LINESET) or bpy.data.linestyles.new(HIDDEN_LINESET)
            if old is not None and old.users == 0:
                bpy.data.linestyles.remove(old)
        style = hidden.linestyle
        style.color = BLUEPRINT_HIDDEN_COLOUR
        style.alpha = BLUEPRINT_HIDDEN_ALPHA
        style.thickness = ink.linestyle.thickness / 2
        style.use_dashed_line = True
        style.dash1, style.gap1 = BLUEPRINT_DASH


def _later(name: str):
    def make(kit: Kit) -> Style:
        raise NotImplementedError(f"the {name} style is not built yet")

    return make


STYLES: dict[str, Callable[[Kit], Style]] = {name: _later(name) for name in STYLE_NAMES}
STYLES.update({"realistic": realistic, "toy": toy, "clay": clay, "toon": toon, "blueprint": blueprint})


# --- apply --------------------------------------------------------------------------------------------------------


def _slots(scene):
    for obj in scene.objects:
        for slot in getattr(obj, "material_slots", ()):
            if slot.material is not None:
                yield obj, slot


def _kit_meshes(scene) -> list:
    return [o for o in scene.objects
            if o.type == "MESH" and any(look.stamp(s.material) for s in o.material_slots if s.material)]  # fmt: skip


def _bounds(objs):
    from mathutils import Vector

    pts = [o.matrix_world @ Vector(c) for o in objs for c in o.bound_box]
    if not pts:
        return Vector((0.0, 0.0, 0.0)), 1.0
    lo = Vector(min(p[i] for p in pts) for i in range(3))
    hi = Vector(max(p[i] for p in pts) for i in range(3))
    return (lo + hi) / 2, max(max(hi - lo), 0.01)


def _target(scene, target):
    """(what kit_lights aims at, what bsa_style_args stores: an object name or [x, y, z])."""
    if target is None:
        centre, _ = _bounds(_kit_meshes(scene))
        return tuple(centre), [float(v) for v in centre]
    if isinstance(target, str):
        obj = bpy.data.objects.get(target)
        if obj is None:
            raise ValueError(f"no object {target!r} to aim the style's lights at")
        return obj, obj.name
    if hasattr(target, "bound_box"):
        return target, target.name
    point = [float(v) for v in target]
    return tuple(point), point


def _brand(scene, warnings: list) -> dict:
    """The brand settings that apply to this scene's short (none for a fresh one); {} when unreadable."""
    from types import SimpleNamespace

    try:
        from bsa.settings import brand_for, load_settings

        short = SimpleNamespace(look=str(scene["bsa_look"])) if scene.get("bsa_look") else None
        return brand_for(load_settings(), short)
    except Exception as exc:  # noqa: BLE001 - a hand-edited settings file never stops a style
        warnings.append(f"the settings could not be read ({exc}), so brand.look and brand.exposure were not applied")
        return {}


def _view(scene, name: str, sl: looks.StyleLook, warnings: list) -> str:
    vs = scene.view_settings
    vs.view_transform = sl.view
    brand = _brand(scene, warnings)
    if brand.get("exposure") is not None:
        vs.exposure = float(brand["exposure"])
    want = str(brand.get("look") or "").strip()
    vs.look = "None"
    if sl.view == "AgX":
        if want:
            try:
                look.set_look(scene, want)
                return sl.view
            except ValueError as exc:
                warnings.append(f"brand.look: {exc}; the {name} style's own look is used")
        if sl.agx_look:
            look.set_look(scene, sl.agx_look)
    elif want:
        warnings.append(f"brand.look {want!r} is skipped: the {name} style uses the {sl.view} view, which has no "
                        "looks")  # fmt: skip
    return sl.view


def _key_copies(scene, key: frozenset) -> None:
    """A stamped material on both key and other objects is copied for the key objects ("<name> key"), so a style can
    colour the key part apart."""
    users: dict[str, set] = {}
    for obj, slot in _slots(scene):
        if look.stamp(slot.material) is not None:
            users.setdefault(slot.material.name, set()).add(obj.name in key)
    for obj, slot in _slots(scene):
        mat = slot.material
        if obj.name not in key or users.get(mat.name) != {True, False}:
            continue
        copy = bpy.data.materials.get(f"{mat.name} key")
        if copy is None:
            copy = mat.copy()
            copy.name = f"{mat.name} key"
        slot.link = "OBJECT"  # a linked duplicate's mesh is shared: the copy goes on this object only
        slot.material = copy


def _materials(scene, st: Style, ctx: Ctx) -> tuple[int, list[str]]:
    _key_copies(scene, ctx.key)
    on_key: dict = {}
    on_set: dict = {}
    kept: set[str] = set()
    for obj, slot in _slots(scene):
        mat = slot.material
        if look.stamp(mat) is None:
            kept.add(mat.name)
            continue
        on_key[mat] = on_key.get(mat, False) or obj.name in ctx.key
        on_set[mat.name] = on_set.get(mat.name, True) and bake.is_set(obj)
    ctx.set_only = frozenset(name for name, only in on_set.items() if only)
    for mat, key in on_key.items():
        st.material(mat, look.stamp(mat), ctx, key)
    return len(on_key), sorted(kept)


def _smooth(mesh, on: bool) -> None:
    """Smooth every face, keeping the mesh's own smoothing to put back (SMOOTH_PREV); `on=False` puts it back."""
    n = len(mesh.polygons)
    if on:
        if SMOOTH_PREV not in mesh:
            flags = np.zeros(n, dtype=bool)
            mesh.polygons.foreach_get("use_smooth", flags)
            mesh[SMOOTH_PREV] = flags.astype(np.int32).tolist()
        mesh.polygons.foreach_set("use_smooth", np.ones(n, dtype=bool))
    elif SMOOTH_PREV in mesh:
        prev = np.asarray(mesh[SMOOTH_PREV], dtype=bool)
        if prev.size == n:
            mesh.polygons.foreach_set("use_smooth", prev)
        del mesh[SMOOTH_PREV]
    mesh.update()


def _bevels(scene, sl: looks.StyleLook) -> None:
    kit_meshes = {o.name for o in _kit_meshes(scene)}
    for obj in scene.objects:
        if obj.type != "MESH":
            continue
        old = obj.modifiers.get(BEVEL)
        if old is not None:
            obj.modifiers.remove(old)
        smallest = min(obj.dimensions)
        wanted = (sl.bevel_share > 0 and obj.name in kit_meshes and smallest > 1e-6
                  and not any(m.type == "BEVEL" for m in obj.modifiers)
                  and not (bake.is_overlay(obj) or bake.is_surface(obj) or bake.is_set(obj)))  # fmt: skip
        if not wanted:
            if old is not None:
                _smooth(obj.data, False)
            continue
        _smooth(obj.data, True)
        mod = obj.modifiers.new(BEVEL, "BEVEL")
        mod.width = sl.bevel_share * smallest
        mod.segments = sl.bevel_segments
        mod.limit_method = "ANGLE"
        mod.angle_limit = math.radians(BEVEL_ANGLE_DEG)
        mod.use_clamp_overlap = True
        mod.harden_normals = True


def _bounces(scene, sl: looks.StyleLook) -> None:
    cycles = getattr(scene, "cycles", None)
    if sl.bounces is None:
        if "bsa_style_bounces" in scene:
            del scene["bsa_style_bounces"]
        if BOUNCES_PREV in scene:
            if cycles is not None:
                cycles.max_bounces = int(scene[BOUNCES_PREV])
            del scene[BOUNCES_PREV]
        return
    if BOUNCES_PREV not in scene and cycles is not None:
        scene[BOUNCES_PREV] = int(cycles.max_bounces)
    scene["bsa_style_bounces"] = int(sl.bounces)


def _world(scene, kit: Kit, name: str, sl: looks.StyleLook, warnings: list) -> None:
    if "bsa_set" in scene:
        try:
            from bsa.blender import sets
        except ImportError:  # the scene sets come in a later round of work
            return
        sets.refresh_world(scene, kit)
        return
    if scene.world is None or "bsa_paper" not in scene.world:
        own = f"{scene.name} paper"  # the user's own world is left as made, unassigned
        scene.world = bpy.data.worlds.get(own) or bpy.data.worlds.new(own)
    vs = scene.view_settings
    made = look.paper_world(scene, looks.backdrop(name, kit), light=sl.world, exposure=vs.exposure, look=vs.look)
    if made.get("warning"):
        warnings.append(made["warning"])
    nt = scene.world.node_tree
    mix = next(n for n in nt.nodes if n.type == "MIX_SHADER")
    lit = mix.inputs[1].links[0].from_node
    cycles = 0.0 if name in NO_WORLD_LIGHT_IN_CYCLES else sl.world * sl.cycles_trim
    scene.world["bsa_strength"] = {lit.name: {"eevee": float(sl.world), "cycles": float(cycles)}}


def _extra_lines_off(scene) -> None:
    """Remove a previous style's own linesets ("bsa <...>" but the ink) and their unused line styles."""
    for layer in scene.view_layers:
        fs = layer.freestyle_settings
        for ls in [x for x in fs.linesets if x.name.startswith("bsa ") and x.name != look.INK_LINESET]:
            style = ls.linestyle
            ls.collection = None  # removing a lineset takes a user from its collection that setting it never added
            fs.linesets.remove(ls)
            if style is not None and style.users == 0:
                bpy.data.linestyles.remove(style)


def apply(scene, kit: Kit | None, target=None, size: float | None = None, key=(), style: str | None = None) -> dict:
    """Restyle the built `scene` in `kit`'s style (or `style`): the view, every kit_material material (rebuilt in
    place), style bevels, the lights (aimed at `target`, an object, its name or a point, for a subject `size`
    metres across; the key parts' names in `key`), the world, ink lines, then the values for the engine the scene
    renders with. Without a kit nothing is touched: {"style": None}. Returns {"style", "view", "lights", "restyled",
    "kept", "lines", "warnings"}."""
    if kit is None:
        return {"style": None}
    name = style or kit.style
    if name not in STYLES:
        raise ValueError(f"{name!r} is not a style: use one of {', '.join(STYLES)}")
    st = STYLES[name](kit)
    sl = st.look
    warnings: list[str] = []
    key = frozenset(str(k) for k in key)
    for layer in scene.view_layers:  # world matrices up to date: the lights aim from the camera at the target
        layer.update()
    aim, stored = _target(scene, target)
    size = float(size) if size is not None else _bounds(_kit_meshes(scene))[1]
    scene["bsa_style_args"] = {"target": stored, "size": size, "key": sorted(key)}
    scene["bsa_style"] = name
    ctx = Ctx(scene=scene, kit=kit, style=name, size=size, target=aim, key=key)
    view = _view(scene, name, sl, warnings)
    restyled, kept = _materials(scene, st, ctx)
    _bevels(scene, sl)
    if sl.strength > 0:
        lit = look.kit_lights(scene, aim, size, sl.strength, single_sun=sl.single_sun,
                              sun=scene.get("bsa_set") == "outdoor", sun_angle_deg=sl.sun_angle_deg,
                              cycles_scale=sl.cycles_trim, rig=looks.RIGS.get(name, look.RIG))  # fmt: skip
    else:
        lit = {"lights": look.lights_off(scene)}  # a style lit by nothing
    if name in ("toon", "pencil"):
        for obj in lit["lights"]:
            e = float(obj["bsa_energy"]["eevee"])
            obj["bsa_energy"] = {"eevee": e, "cycles": looks.cycles_energy(name, e, size)}
    _bounces(scene, sl)
    _world(scene, kit, name, sl, warnings)
    _extra_lines_off(scene)
    px = looks.line_px(name, kit)
    lines = look.ink_lines(scene, LINE_COLOUR.get(name, kit.palette["ink"]), px) is not None
    for layer in scene.view_layers:  # contours are a style's own (blueprint's): off unless its lines put them on
        ink = layer.freestyle_settings.linesets.get(look.INK_LINESET)
        if ink is not None:
            ink.select_contour = False
    if name == "blueprint" and not key:
        warnings.append("blueprint shows its key part in the accent colour: pass key=[...] to styles.apply")
    if st.lines is not None:
        st.lines(ctx)
        lines = True
    look.match_engine(scene)
    if kept:
        warnings.append(f"these materials were not made with look.kit_material, so the {name} style left them as "
                        f"made: {', '.join(kept)}")  # fmt: skip
    return {"style": name, "view": view, "lights": [o.name for o in lit["lights"]], "restyled": restyled,
            "kept": kept, "lines": lines, "warnings": warnings}  # fmt: skip


def reapply(scene, kit: Kit, style: str | None = None) -> dict:
    """`apply` again with the target, size and key parts the scene was first styled with (another style with
    `style`). ValueError when the scene was never styled."""
    args = scene.get("bsa_style_args")
    if args is None:
        raise ValueError("this scene was never styled: call styles.apply first")
    target = args["target"]
    target = str(target) if isinstance(target, str) else [float(v) for v in target]
    return apply(scene, kit, target=target, size=float(args["size"]), key=[str(k) for k in args["key"]], style=style)
