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
# Cycles toon-sun trims, measured on the CPU with the flat steps glowing for camera rays only (styles._cel): toon's
# lit face shows its colour exactly at 1.0 (the sampler's 0.92 made up for its cel floor and wall lighting the parts);
# pencil's Cycles lit face matches EEVEE's exactly at 1.0 too (ΔE 0.00 between; the sampler's 0.915 made up for the
# same).
CYCLES_TOON_K = {"toon": 1.0, "pencil": 1.0}
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
    "realistic": StyleLook("AgX", "Medium High Contrast", 3.0, 0.35, False, 12, 0.83, 0, 0.02, 2, None),
    "toy": StyleLook("Standard", None, 1.3, 0.6, False, 12, 0.83, 0, 0.08, 5, None),
    "toon": StyleLook("Standard", None, 4.0, 0.08, True, 0.5, 1.0, 4.0, 0, 0, 0),
    "clay": StyleLook("AgX", "Medium High Contrast", 3.2, 0.3, False, 12, 0.85, 0, 0.12, 6, None),
    "blueprint": StyleLook("Standard", None, 0.0, 1.0, False, 0, 1.0, 3.0, 0, 0, None),
    "paper": StyleLook("Standard", None, 2.1, 0.45, True, 1.5, 0.9, 0, 0.04, 1, None),
    "pencil": StyleLook("Standard", None, 3.0, 0.25, True, 4.0, 1.0, 3.5, 0, 0, 0),
}  # fmt: skip

WHITE = (1.0, 1.0, 1.0)
# Each style's light rig, from the style sampler's lights, as look.lights reads a rig: name, degrees around the subject
# from the camera's side (look.lights' sense), degrees above the horizon, share of the key's strength, colour. A style
# without one uses look.RIG. These are the owner-approved rigs: never move them to satisfy a test.
RIGS = {
    "realistic": (("Key", -36.0, 36.0, 1.0, WHITE), ("Fill", 48.0, 17.0, 0.29, WHITE),
                  ("Rim", 150.0, 51.0, 0.69, WHITE)),
    "toy": (("Key", -25.0, 40.0, 1.0, WHITE), ("Fill", 42.0, 15.0, 0.7, WHITE), ("Top", 0.0, 87.0, 0.33, WHITE)),
    "clay": (("Key", -36.0, 41.0, 1.0, (1.0, 0.95, 0.88)), ("Fill", 48.0, 17.0, 0.25, WHITE)),
    "toon": (("Sun", -32.0, 38.0, 1.0, WHITE),),  # the sampler's sun (52, 0, -32): its single sun's direction
    "paper": (("Sun", -35.0, 42.0, 1.0, WHITE),),  # the sampler's sun (48, 0, -35)
    "pencil": (("Sun", -35.0, 40.0, 1.0, WHITE),),  # the sampler's sun (50, 0, -35)
}  # fmt: skip

# The lit-face checks run twice, Cycles on the CPU. In a room (the demo with the sampler's floor and wall, what shorts
# ship with): ROOM_LIT_DELTA_E per engine and ROOM_ENGINE_DELTA_E, each the owner-approved sampler's own measurement
# on its crate's lit face plus 3. Sampler measured (lit face vs its kit colour; EEVEE, Cycles, between): realistic
# 14.9, 26.6, 11.9; toy 13.8, 17.5, 4.2; clay 5.9, 6.0, 4.2. Without a floor (the demo alone, lit by the world):
# LIT_DELTA_E and ENGINE_DELTA_E, tight, pinning the materials' kit colours; toy's are its approved rig measured on the
# floorless demo plus 3 (EEVEE 11.1, Cycles 14.3, between 5.2). Paper: the sampler itself misses the planned 8 with
# the test kit (its own render_style.py with that kit's background and spark colour, CPU: room EEVEE 9.05, Cycles
# 7.99 with no margin, between 1.76; floorless 9.05, 11.95, 2.91), so its limits are those plus 3. Never raise
# these and never move an approved rig to pass them: tune the lights within the approved look instead.
ROOM_LIT_DELTA_E = {"realistic": {"eevee": 17.9, "cycles": 29.6}, "toy": {"eevee": 16.8, "cycles": 20.5},
                    "clay": {"eevee": 8.9, "cycles": 9.0}, "toon": 2, "blueprint": 6,
                    "paper": {"eevee": 12.1, "cycles": 11.0}, "pencil": 6}  # fmt: skip
ROOM_ENGINE_DELTA_E = {"realistic": 14.9, "toy": 7.2, "clay": 7.2, "toon": 3, "blueprint": 2, "paper": 6, "pencil": 4}
LIT_DELTA_E = {"realistic": 15, "toy": {"eevee": 14.1, "cycles": 17.3}, "toon": 2, "clay": 12, "blueprint": 6,
               "paper": {"eevee": 12.1, "cycles": 15.0}, "pencil": 6}  # fmt: skip
ENGINE_DELTA_E = {"realistic": 6, "toy": 8.2, "toon": 3, "clay": 6, "blueprint": 2, "paper": 6, "pencil": 4}


def lit_limit(style: str, engine: str, room: bool = False) -> float:
    """The most a lit face may differ from lit_expect (CIE76) for `style` rendered with `engine`, in a room or not."""
    limit = (ROOM_LIT_DELTA_E if room else LIT_DELTA_E)[style]
    return float(limit[engine] if isinstance(limit, dict) else limit)


def engine_limit(style: str, room: bool = False) -> float:
    """The most the same lit face may differ between EEVEE and Cycles (CIE76)."""
    return float((ROOM_ENGINE_DELTA_E if room else ENGINE_DELTA_E)[style])


def toon_sun_energy(size: float, smooth: float, k: float) -> float:
    """The sun strength that makes a toon-diffuse lit face show its colour exactly in Cycles (flat pdf over the
    cone)."""
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


def _lmix(a: str, b: str, t: float) -> str:
    """a + (b - a) * t in linear light, as hex (the sampler's mixc)."""
    return _hex(_toward(_lin(a), _lin(b), t))


def part_colour(style: str, hex_: str, kit, key: bool) -> str:
    hex_ = colour.srgb_to_hex(colour.hex_to_srgb(hex_))
    if style == "clay":
        return warm(hex_)
    if style == "paper":
        return _lmix(hex_, "#FFFFFF", 0.12)
    if style == "pencil":
        return _lmix(kit.palette["accent"], "#FFFFFF", 0.25) if key else _lmix(backdrop(style, kit), "#FFFFFF", 0.5)
    if style == "blueprint":
        return kit.palette["accent"] if key else _lmix(blueprint_paper(kit), "#FFFFFF", 0.75)
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
