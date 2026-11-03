"""Colour maths for the look checks: hex, sRGB, linear and CIELAB, ΔE, and a frame's background colour. Pure numpy."""

from __future__ import annotations

import colorsys
import re

import numpy as np

DELTA_E_LIMIT = 5.0  # a still's background further than this (CIE76) from the brand background is warned
_WHITE = np.array([0.95047, 1.0, 1.08883])  # D65
_TO_XYZ = np.array([[0.4124564, 0.3575761, 0.1804375], [0.2126729, 0.7151522, 0.0721750],
                    [0.0193339, 0.1191920, 0.9503041]])  # fmt: skip


def hex_to_srgb(value: str) -> tuple[float, float, float]:
    """'#RRGGBB' (the # optional) as sRGB 0..1."""
    text = str(value or "").strip().lstrip("#")
    if not re.fullmatch(r"[0-9a-fA-F]{6}", text):
        raise ValueError(f"{value!r} is not a colour like #F3F2EE")
    return tuple(int(text[i : i + 2], 16) / 255 for i in (0, 2, 4))


def srgb_to_hex(rgb) -> str:
    return "#" + "".join(f"{round(min(max(float(c), 0.0), 1.0) * 255):02X}" for c in rgb[:3])


def mix(a: str, b: str, t: float) -> str:
    """a + (b - a) * t on the sRGB values (0..1, not linear light) of two hex colours, as upper-case hex."""
    ca, cb = hex_to_srgb(a), hex_to_srgb(b)
    return srgb_to_hex([x + (y - x) * t for x, y in zip(ca, cb)])


def _warm(value: str) -> str:
    """The colour with its hue moved into 20-45 degrees (the nearer end) and its saturation at least 0.35."""
    h, s, v = colorsys.rgb_to_hsv(*hex_to_srgb(value))
    deg = h * 360
    if not 20 <= deg <= 45:
        # distance round the hue circle to each end
        d20, d45 = abs((deg - 20 + 180) % 360 - 180), abs((deg - 45 + 180) % 360 - 180)
        deg = 20 if d20 <= d45 else 45
    return srgb_to_hex(colorsys.hsv_to_rgb(deg / 360, max(s, 0.35), v))


def set_colours(kit) -> dict[str, str]:
    """The scene set's colours derived from a kit: walls and floor off the background, wood off the accent (kept
    warm), ground and hills off the motion colour, sky off the heat colour (the default code where the kit has none)."""
    from bsa.kit import DEFAULT_CODE

    pal = kit.palette
    bg, ink, accent = pal["background"], pal["ink"], pal["accent"]
    light = srgb_to_lab(hex_to_srgb(bg))[0] > 50
    side = "#000000" if light else "#FFFFFF"
    motion = kit.categories.get("motion") or DEFAULT_CODE["motion"]
    heat = kit.categories.get("heat") or DEFAULT_CODE["heat"]
    wood = _warm(mix(mix(accent, bg, 0.35), ink, 0.12))
    return {
        "wall": srgb_to_hex(hex_to_srgb(bg)),
        "floor": mix(bg, side, 0.06),
        "pattern": mix(bg, side, 0.16),
        "wood": wood,
        "wood_dark": mix(wood, ink, 0.30),
        "ground": mix(motion, bg, 0.55),
        "hills_near": mix(motion, ink, 0.20),
        "hills_far": mix(motion, bg, 0.70),
        "sky_top": srgb_to_hex(hex_to_srgb(heat)),
        "sky_horizon": mix(heat, bg if light else "#FFFFFF", 0.65),
    }


def srgb_to_linear(rgb) -> np.ndarray:
    c = np.asarray(rgb, dtype=np.float64)
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def linear_to_srgb(lin) -> np.ndarray:
    c = np.clip(np.asarray(lin, dtype=np.float64), 0.0, None)
    return np.where(c <= 0.0031308, c * 12.92, 1.055 * c ** (1 / 2.4) - 0.055)


def srgb_to_lab(rgb) -> tuple[float, float, float]:
    xyz = _TO_XYZ @ srgb_to_linear(np.asarray(rgb, dtype=np.float64)[:3]) / _WHITE
    f = np.where(xyz > (6 / 29) ** 3, np.cbrt(xyz), xyz / (3 * (6 / 29) ** 2) + 4 / 29)
    return float(116 * f[1] - 16), float(500 * (f[0] - f[1])), float(200 * (f[1] - f[2]))


def _rgb(value):
    return hex_to_srgb(value) if isinstance(value, str) else tuple(value)


def delta_e(a, b) -> float:
    """CIE76 ΔE between two colours (hex strings or sRGB 0..1 triples): about 2 is just noticeable, 5 clearly."""
    return float(np.linalg.norm(np.subtract(srgb_to_lab(_rgb(a)), srgb_to_lab(_rgb(b)))))


def dominant_colour(pixels: np.ndarray) -> tuple[float, float, float]:
    """The frame's most common colour (sRGB 0..1), the background of a frame on a plain backdrop: pixels binned 16
    levels a channel, the fullest bin's mean."""
    rgb = pixels[..., :3].reshape(-1, 3).astype(np.float64)
    if pixels.dtype == np.uint8:
        rgb /= 255.0
    bins = np.minimum((rgb * 16).astype(np.int64), 15)
    keys = bins[:, 0] * 256 + bins[:, 1] * 16 + bins[:, 2]
    top = np.bincount(keys).argmax()
    return tuple(float(v) for v in rgb[keys == top].mean(axis=0))


def background_source(short, settings: dict) -> tuple[str, str] | None:
    """(hex, whose) of the intended background: the short's style.palette.background (or style.background), "the
    short's background"; else the brand's brand.palette.background (or brand.background), "the brand background",
    unless the short has its own look (`"look": "fresh"`, bsa.settings.brand_for). None when neither says."""
    from bsa.settings import brand_for

    style = short.style if isinstance(getattr(short, "style", None), dict) else {}
    for src, whose in ((style, "the short's background"), (brand_for(settings, short), "the brand background")):
        palette = src.get("palette") if isinstance(src.get("palette"), dict) else {}
        for value in (palette.get("background"), src.get("background")):
            if isinstance(value, str) and value.strip():
                return value.strip(), whose
    return None


def background_hex(short, settings: dict) -> str | None:
    """The intended background's hex (background_source), or None."""
    found = background_source(short, settings)
    return found[0] if found else None


def flat_backdrop(world, transparent: bool = False) -> bool:
    """True when a scene's world shows as one flat colour: a paper_world backdrop (stamped "bsa_backdrop": "paper"),
    a plain colour, or no world at all. A world fed by a texture (a sky, an HDRI, a gradient) is an environment, and a
    transparent film shows none: the background check and its paper_world advice do not apply to those."""
    if transparent or (world is not None and world.get("bsa_backdrop") == "set"):  # a scene set's environment
        return False
    if world is None or world.get("bsa_backdrop") == "paper" or not world.use_nodes or world.node_tree is None:
        return True
    return not any(str(n.bl_idname).startswith("ShaderNodeTex") for n in world.node_tree.nodes)


def background_warning(pixels: np.ndarray, want_hex: str, where: str, limit: float = DELTA_E_LIMIT,
                       whose: str = "the brand background") -> str | None:  # fmt: skip
    """A warning when a frame's background (its dominant colour) is more than `limit` ΔE from `want_hex` (`whose`
    names it: "the brand background" or "the short's background")."""
    got = dominant_colour(pixels)
    d = delta_e(want_hex, got)
    if d <= limit:
        return None
    view = "brand.exposure / brand.look" if "brand" in whose else "paper_world's exposure= and look="
    return (f"{where}: the background renders {srgb_to_hex(got)}, ΔE {d:.0f} from {whose} "
            f"{want_hex.upper()}: set the world with bsa.blender.look.paper_world(scene, \"{want_hex}\") (it solves "
            f"the colour through the view transform), or {view}")  # fmt: skip
