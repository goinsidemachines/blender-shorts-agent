"""Each style's numbers: view transform, light, Cycles trims, line widths and colour rules, plus the ΔE tolerances the
look checks use. Pure maths on hex colours and numbers; the Blender side (bsa.styles) reads them. Values come from the
owner-approved style sampler; a style that misses a tolerance is fixed (light, trim), never by raising a tolerance."""

from __future__ import annotations

import math
from dataclasses import dataclass

from bsa import colour
from bsa.kit import DEFAULT_CODE

LINE_STYLES = ("toon", "blueprint", "pencil")
STANDARD = ("toy", "toon", "blueprint", "paper", "pencil")

RAMP_AT = 0.25
TOON_SIZE = 0.874
PENCIL_TOON_SIZE = 0.95
CYCLES_TOON_K = {"toon": 0.92, "pencil": 0.915}
OUTLINE_3D_PX = 2.0
FREESTYLE_REF_H = 480


@dataclass(frozen=True)
class StyleLook:
    view: str
    agx_look: str | None
    strength: float
    world: float
    single_sun: bool
    sun_angle_deg: float
    cycles_trim: float
    line_px: float
    bevel_share: float
    bevel_segments: int
    bounces: int | None


TABLE = {
    "realistic": StyleLook("AgX", None, 3.0, 0.35, False, 12, 0.93, 0, 0.02, 2, None),
    "toy": StyleLook("Standard", None, 1.3, 0.6, False, 12, 0.83, 0, 0.08, 5, None),
    "toon": StyleLook("Standard", None, 4.0, 0.08, True, 0.5, 1.0, 4.0, 0, 0, 0),
    "clay": StyleLook("AgX", "Medium High Contrast", 3.2, 0.3, False, 12, 0.92, 0, 0.12, 6, None),
    "blueprint": StyleLook("Standard", None, 0.0, 1.0, False, 0, 1.0, 3.0, 0, 0, None),
    "paper": StyleLook("Standard", None, 2.1, 0.45, True, 1.5, 0.9, 0, 0.04, 1, None),
    "pencil": StyleLook("Standard", None, 3.0, 0.25, True, 4.0, 1.0, 3.5, 0, 0, 0),
}  # fmt: skip

LIT_DELTA_E = {"realistic": 15, "toy": 8, "toon": 2, "clay": 12, "blueprint": 6, "paper": 8, "pencil": 6}
ENGINE_DELTA_E = {"realistic": 6, "toy": 6, "toon": 3, "clay": 6, "blueprint": 2, "paper": 6, "pencil": 4}


def toon_sun_energy(size: float, smooth: float, k: float) -> float:
    """The sun strength that makes a toon-diffuse lit face show its colour exactly in Cycles (flat pdf over the cone)."""
    a = min((size + smooth) * math.pi / 2, math.pi / 2)
    pdf = 0.5 / math.pi / (1 - math.cos(a))
    return k / pdf


def cycles_energy(style: str, eevee: float, size_m: float) -> float:
    """The key light's strength in Cycles for the strength EEVEE uses (`size_m`: the light's size, kept for callers)."""
    if style == "toon":
        return toon_sun_energy(TOON_SIZE, 0.0, CYCLES_TOON_K["toon"])
    if style == "pencil":
        return toon_sun_energy(PENCIL_TOON_SIZE, 0.0, CYCLES_TOON_K["pencil"])
    return eevee * TABLE[style].cycles_trim


def line_px(style: str, kit) -> float:
    """Line width on a 1920-tall frame: the kit's outline_px for a style with lines (or any style with outline_3d),
    the style's own width for a line style with outline_px 0, the 3D outline width for outline_3d, else 0."""
    own = TABLE[style].line_px
    if kit.outline_px > 0 and (own > 0 or kit.outline_3d):
        return kit.outline_px
    if own > 0:
        return own
    return OUTLINE_3D_PX if kit.outline_3d else 0


def freestyle_thickness(px: float) -> float:
    """Freestyle RELATIVE-mode thickness: the same share of the frame at any resolution percentage."""
    return px * FREESTYLE_REF_H / 1920


def _lin(hex_: str) -> list[float]:
    return [float(v) for v in colour.srgb_to_linear(colour.hex_to_srgb(hex_))]


def _hex(lin) -> str:
    return colour.srgb_to_hex(colour.linear_to_srgb(lin))


def _toward(a, b, t: float) -> list[float]:
    return [x + (y - x) * t for x, y in zip(a, b)]


def warm(hex_: str) -> str:
    """Clay's warming: in linear light 18% toward its grey, then 8% toward #E8C9A6."""
    c = _lin(hex_)
    g = sum(c) / 3
    return _hex(_toward(_toward(c, [g, g, g], 0.18), _lin("#E8C9A6"), 0.08))


def blueprint_paper(kit) -> str:
    """The kit's heat colour (the default code's where it has none) in linear light x 0.42: a dark paper blue."""
    heat = kit.categories.get("heat") or DEFAULT_CODE["heat"]
    return _hex([v * 0.42 for v in _lin(heat)])


def backdrop(style: str, kit) -> str:
    if style == "blueprint":
        return blueprint_paper(kit)
    if style == "pencil":
        return "#F5F2EA"
    bg = colour.srgb_to_hex(colour.hex_to_srgb(kit.palette["background"]))
    return warm(bg) if style == "clay" else bg


def part_colour(style: str, hex_: str, kit, key: bool) -> str:
    hex_ = colour.srgb_to_hex(colour.hex_to_srgb(hex_))
    if style == "clay":
        return warm(hex_)
    if style == "paper":
        return colour.mix(hex_, "#FFFFFF", 0.12)
    if style == "pencil":
        return colour.mix(kit.palette["accent"], "#FFFFFF", 0.25) if key else colour.mix(backdrop(style, kit), "#FFFFFF", 0.5)
    if style == "blueprint":
        return kit.palette["accent"] if key else colour.mix(blueprint_paper(kit), "#FFFFFF", 0.75)
    return hex_


def shadow_of(hex_: str, ink: str, k: float = 0.68) -> str:
    """Toon's dark step: the colour in linear light x k, then 15% toward the ink."""
    return _hex(_toward([v * k for v in _lin(hex_)], _lin(ink), 0.15))


def lit_expect(style: str, hex_: str, kit, key: bool) -> str:
    """What a lit face square to the key light shows. Blueprint fills are composited over the paper (key 0.85,
    fill 0.22 alpha)."""
    c = part_colour(style, hex_, kit, key)
    if style == "blueprint":
        a = 0.85 if key else 0.22
        return _hex(_toward(_lin(blueprint_paper(kit)), _lin(c), a))
    return c
