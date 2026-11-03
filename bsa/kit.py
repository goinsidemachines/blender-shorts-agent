"""The brand kit in settings: names, ranges, validation, migration of the old flat brand, and what to tell the user.

A kit is the structured part of the `brand` settings (and of a short's own `style`): a style name, a palette of six
roles, category colours, outline/shadow/corner sizes, caption box, logo, progress bar and scene set. Nothing here
draws or renders anything; bsa.cards and the Blender side read a Kit."""

from __future__ import annotations

import copy
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path

STYLES = ("realistic", "toy", "toon", "clay", "blueprint", "paper", "pencil")
STYLE_ALIASES = {"3d": "realistic", "flat": "toon"}
SETS = ("auto", "workbench", "graph", "outdoor", "studio", "none")
ROLES = ("background", "ink", "card", "text", "accent", "highlight")
LOGO_PLACES = ("top-left", "top-right")
RANGES = {"outline_px": (0, 12), "shadow_px": (0, 24), "corner_px": (0, 80), "pill_corner_px": (0, 80),
          "caption.size": (32, 120), "caption.opacity": (0.0, 1.0), "caption.corner_px": (0, 80),
          "caption.pad_px": (0, 60)}  # fmt: skip
BOOLS = ("progress", "outline_3d", "caption.draw")
# Handled elsewhere (mode, exposure, look) or prose Claude reads: never validated here.
PASS = ("mode", "exposure", "look", "material", "cards", "handle_place")
_PROSE = ("material", "cards", "handle_place")

# Offered at setup as the five physics categories' colours; never the owner's.
DEFAULT_CODE = {"force": "#E4572E", "energy": "#F2C14E", "motion": "#2BA84A", "heat": "#3A86FF", "fluid": "#9DB4C0"}

PLAIN = {
    "palette": {"background": "#14161A", "ink": "#050608", "card": "#050608", "text": "#FFFFFF", "accent": "#FFCC59",
                "highlight": "#FFCC59"},
    "categories": {}, "outline_px": 0, "shadow_px": 0, "corner_px": 16, "pill_corner_px": None, "font": None,
    "caption": {"color": "#FFFFFF", "box": "#050608", "size": 64, "opacity": 0.55, "corner_px": 16, "pad_px": 35,
                "draw": True},
    "logo": None, "progress": True, "set": "auto",
}  # fmt: skip

_KIT_KEYS = ("style", "palette", "categories", "outline_px", "shadow_px", "corner_px", "pill_corner_px", "font",
             "caption", "logo", "progress", "set")  # fmt: skip
_HEX = re.compile(r"#?([0-9a-fA-F]{6})")
_HEXA = re.compile(r"#?([0-9a-fA-F]{6}(?:[0-9a-fA-F]{2})?)")  # caption colours: 8 digits keep their alpha


@dataclass(frozen=True)
class Kit:
    style: str
    palette: dict
    categories: dict
    outline_px: int
    shadow_px: int
    corner_px: int
    pill_corner_px: int | None
    font: str | None
    caption: dict
    logo: dict | None
    progress: bool
    set: str

    def colour(self, category: str | None) -> str:
        """The category's hex, else the accent."""
        return self.categories.get(str(category or "").strip().lower()) or self.palette["accent"]


def style_name(value) -> str:
    name = str(value).strip().lower()
    name = STYLE_ALIASES.get(name, name)
    if name not in STYLES:
        raise ValueError(f"{value!r} is not a style: use one of {', '.join(STYLES)} (3d and flat are read as "
                         "realistic and toon)")  # fmt: skip
    return name


def _hex(key: str, value, alpha: bool = False) -> str:
    m = (_HEXA if alpha else _HEX).fullmatch(value.strip()) if isinstance(value, str) else None
    if not m:
        more = " (or 8 digits with alpha, like #EAF2F880)" if alpha else ""
        raise ValueError(f"brand.{key} must be a colour like #EAF2F8{more}, got {value!r}")
    return "#" + m.group(1).upper()


def _number(key: str, value):
    lo, hi = RANGES[key]
    bad = ValueError(f"brand.{key} must be a number from {lo:g} to {hi:g}, got {value!r}")
    if isinstance(value, bool):
        raise bad
    try:
        x = float(value)
    except (TypeError, ValueError):
        raise bad from None
    if not math.isfinite(x) or not lo <= x <= hi:
        raise bad
    return x if isinstance(lo, float) else round(x)


def _bool(key: str, value) -> bool:
    if isinstance(value, bool):
        return value
    word = str(value).strip().lower()
    if word in ("true", "false"):
        return word == "true"
    raise ValueError(f"brand.{key} must be true or false, got {value!r}")


def _logo_file(value, must_exist: bool) -> str:
    path = Path(str(value).strip()).expanduser().resolve()
    if path.suffix.lower() == ".svg":
        raise ValueError(f"brand.logo.file {path} is an SVG: save the logo as a PNG (cards draw PNG logos)")
    bad = ValueError(f"brand.logo.file must be a PNG file; {path} is not one")
    if path.suffix.lower() != ".png":
        raise bad
    if path.is_file() or must_exist:
        from bsa.image_checks import png_size

        try:
            png_size(path)
        except (OSError, ValueError):
            raise bad from None
    return str(path)


def _dict(key: str, value, what: str) -> dict:
    if not isinstance(value, dict):
        raise ValueError(f"brand.{key} must be an object of {what}, got {value!r}")  # noqa: TRY004 - callers catch ValueError
    return value


def check(key: str, value, *, _must_exist: bool = True):
    """`value` as stored for the setting `brand.{key}` (`key` is the path under brand; "" = the whole object). Keys in
    PASS and unknown top-level keys pass unchanged. ValueError for a value outside the kit's names and ranges."""
    if key == "":
        return {k: check(k, v, _must_exist=_must_exist) for k, v in _dict("", value, "settings").items()}
    top, _, rest = key.partition(".")
    if top in PASS or top not in _KIT_KEYS and top != "outline_3d":
        return value
    if key == "style":
        return style_name(value)
    if key == "set":
        word = str(value).strip().lower()
        if word not in SETS:
            raise ValueError(f"{value!r} is not a set: use one of {', '.join(SETS)}")
        return word
    if key in BOOLS:
        return _bool(key, value)
    if key == "font":
        text = str(value).strip() if value is not None else ""
        return text or None
    if key == "pill_corner_px":
        return None if value is None else _number(key, value)
    if key in RANGES:
        return _number(key, value)
    if top == "palette":
        if not rest:
            return {k: check(f"palette.{k}", v) for k, v in _dict(key, value, "colours").items()}
        name = rest.strip().lower()
        if name not in ROLES:
            raise ValueError(f"brand.palette has no {rest!r}: its colours are background, ink, card, text, accent "
                             "and highlight; category colours go in brand.categories")  # fmt: skip
        return _hex(f"palette.{name}", value)
    if top == "categories":
        if not rest:
            return {str(k).strip().lower(): check(f"categories.{k}", v) for k, v in _dict(key, value, "colours").items()}
        return _hex(key, value)
    if top == "caption":
        if not rest:
            return {k: check(f"caption.{k}", v) for k, v in _dict(key, value, "caption settings").items()}
        if rest in ("color", "box"):
            return _hex(key, value, alpha=True)
        return value  # an unknown caption key passes
    if top == "logo":
        if value is None and not rest:
            return None
        if not rest:
            if isinstance(value, str):
                value = {"file": value}
            return {k: check(f"logo.{k}", v, _must_exist=_must_exist) for k, v in _dict(key, value, "logo settings").items()}
        if rest == "file":
            return _logo_file(value, _must_exist)
        if rest == "place":
            word = str(value).strip().lower()
            if word not in LOGO_PLACES:
                raise ValueError("brand.logo.place must be top-left or top-right")
            return word
        if rest == "name":
            text = value.strip() if isinstance(value, str) else ""
            if not text or len(text) > 36:
                raise ValueError(f"brand.logo.name must be text of 1 to 36 characters, got {value!r}")
            return text
        return value
    return value


def kit_from(brand: dict) -> Kit:
    """A Kit from a brand object, strict: the first bad value raises ValueError. PLAIN under the given keys."""
    merged = _plain()
    _apply(merged, check("", brand), strict=True)
    return _make(merged)


def _plain() -> dict:
    merged = copy.deepcopy(PLAIN)
    merged["style"] = None
    return merged


def _apply(merged: dict, layer: dict, prefix: str = "brand", problems: list | None = None, strict: bool = False) -> None:
    """Merge the kit keys of `layer` into `merged` key by key (dicts one level). Without `strict` a value failing
    `check` is left out and named in `problems`."""

    def put(path: str, value, *, into: dict | None, name: str) -> None:
        try:
            stored = check(path, value, _must_exist=strict)
        except ValueError as exc:
            if strict:
                raise
            problems.append(f"{prefix}.{path}: {exc}; it is ignored, so the plain value is used")
            return
        if into is None:
            merged[name] = stored
        else:
            into[name] = stored

    for key in _KIT_KEYS:
        if key not in layer:
            continue
        value = layer[key]
        if key == "logo" and isinstance(value, str):
            value = {"file": value}
        if key in ("palette", "categories", "caption", "logo") and isinstance(value, dict):
            if merged[key] is None:
                merged[key] = {}
            for sub, v in value.items():
                name = str(sub).strip().lower() if key in ("palette", "categories") else sub
                put(f"{key}.{sub}", v, into=merged[key], name=name)
        elif key == "logo" and value is None:
            merged["logo"] = None
        else:
            put(key, value, into=None, name=key)
    if isinstance(merged.get("logo"), dict) and not merged["logo"].get("file"):
        merged["logo"] = None


def _make(merged: dict) -> Kit:
    return Kit(
        style=merged["style"], palette=merged["palette"], categories=merged["categories"],
        outline_px=merged["outline_px"], shadow_px=merged["shadow_px"], corner_px=merged["corner_px"],
        pill_corner_px=merged["pill_corner_px"], font=merged["font"], caption=merged["caption"], logo=merged["logo"],
        progress=merged["progress"], set=merged["set"],
    )  # fmt: skip


def _legacy_split(brand: dict) -> dict:
    """The brand with the old flat shape read as a kit: non-role hex palette entries are categories (an explicit
    `categories` entry wins) and `brand.background` is `palette.background` (an explicit palette one wins)."""
    out = dict(brand)
    palette = brand.get("palette")
    if isinstance(palette, dict):
        keep, legacy = {}, {}
        for k, v in palette.items():
            if str(k).strip().lower() not in ROLES and isinstance(v, str) and _HEX.fullmatch(v.strip()):
                legacy[str(k).strip().lower()] = v
            else:
                keep[k] = v
        out["palette"] = keep
        if legacy:
            cats = brand.get("categories")
            out["categories"] = {**legacy, **(cats if isinstance(cats, dict) else {})}
    if "background" in brand and not (isinstance(out.get("palette"), dict) and "background" in out["palette"]):
        out["palette"] = {**(out.get("palette") if isinstance(out.get("palette"), dict) else {}),
                          "background": brand["background"]}  # fmt: skip
    return out


def _merged(settings: dict, short=None) -> tuple[dict, list[str]]:
    from bsa import settings as settings_mod

    merged, problems = _plain(), []
    try:
        brand = settings_mod.brand_for(settings, short)
    except Exception:  # noqa: BLE001 - a hand-edited settings file never crashes the readers
        brand = {}
    own = getattr(short, "style", None)
    for prefix, layer in (("brand", _legacy_split(brand) if isinstance(brand, dict) else {}),
                          ("style", own if isinstance(own, dict) else {})):  # fmt: skip
        if layer:
            _apply(merged, layer, prefix, problems)
    if merged["style"] not in STYLES:
        merged["style"] = None
    return merged, problems


def kit_for(settings: dict, short=None) -> Kit | None:
    """The kit a short uses: PLAIN, then the brand that applies, then the short's own `style` (kit keys only), merged
    key by key. A value failing `check` is left out (the plain one stays). None unless a valid style is set. Never
    raises."""
    try:
        merged, _ = _merged(settings, short)
        return _make(merged) if merged["style"] else None
    except Exception:  # noqa: BLE001
        return None


def problems(settings: dict, short=None) -> list[str]:
    """What kit_for left out of the stored kit, and a logo file that is gone."""
    try:
        merged, out = _merged(settings, short)
        shots = list(getattr(short, "shots", None) or [])
    except Exception:  # noqa: BLE001
        return []
    if merged["style"] is None:  # no kit: shorts render as today, so only the kit-only fields are reported
        return [f"shot {s.id} has a title card, category, step or value bars, which only show with a brand kit "
                "(brand.style): set one up or remove them" for s in shots if _has_card_fields(s)]  # fmt: skip
    names = sorted(merged["categories"])
    for s in shots:
        cat = getattr(s, "category", None)
        if isinstance(cat, str) and cat.strip() and cat.strip().lower() not in merged["categories"]:
            out.append(f"shot {s.id} category {cat!r} is not in your kit's categories ({', '.join(names) or 'none'}): "
                       "it shows in the accent colour")  # fmt: skip
    note = logo_missing(merged.get("logo"))
    return out + [note] if note else out


def errors(settings: dict, short=None) -> list[str]:
    """What stops the edit drawing the short's kit cards (CLI validate and the edit refuse it): a shot with more
    labels than a kit draws (bsa.kitlayout.MAX_LABELS). None without a kit: today's text labels have no such limit."""
    if short is None or kit_for(settings, short) is None:
        return []
    from bsa.kitlayout import MAX_LABELS

    out = []
    for s in getattr(short, "shots", None) or []:
        n = len(getattr(s, "labels", None) or [])
        if n > MAX_LABELS:
            out.append(f"shot {s.id} has {n} labels: a brand kit draws at most {MAX_LABELS} per shot: move some to "
                       "another shot or say them in the narration")  # fmt: skip
    return out


def logo_missing(logo: dict | None) -> str | None:
    """The note for a kit logo whose file is gone (moved or deleted after setup), else None."""
    if logo and not Path(logo["file"]).is_file():
        return f"your logo file {logo['file']} is missing, so no logo badge is drawn: set brand.logo.file again"
    return None


def _has_card_fields(shot) -> bool:
    return any(getattr(shot, k, None) for k in ("title_card", "category", "step", "bars"))


def advice(settings: dict) -> list[str]:
    brand = (settings or {}).get("brand")
    brand = brand if isinstance(brand, dict) else {}
    out = []
    palette = brand.get("palette") if isinstance(brand.get("palette"), dict) else {}
    legacy = {str(k).strip().lower(): v for k, v in palette.items()
              if str(k).strip().lower() not in ROLES and isinstance(v, str) and _HEX.fullmatch(v.strip())}  # fmt: skip
    if legacy:
        names = list(legacy)
        keys = ", ".join(f"brand.palette.{n}" for n in names)
        moved = json.dumps({n: _hex("categories." + n, legacy[n]) for n in names}, ensure_ascii=False)
        out.append(f"{keys} {'is' if len(names) == 1 else 'are'} read as the category "
                   f"{'colour' if len(names) == 1 else 'colours'} {', '.join(names)} once brand.style is set: move it "
                   f"with settings set brand.categories '{moved}'")  # fmt: skip
    out += [f"brand.{k} is a note Claude reads; the kit's values are what the cards use" for k in _PROSE if k in brand]
    if brand.get("mode") == "brand" and not brand.get("style"):
        out.append("brand.style is not set, so shorts keep today's text captions and labels: the brand question in "
                   "00-first-run.md sets it")  # fmt: skip
    return out


def json_scalar(key: str) -> bool:
    """A brand.* key whose value is a JSON number, boolean or null on the command line."""
    path = key.removeprefix("brand.")
    return path in RANGES or path in BOOLS or path == "pill_corner_px"
