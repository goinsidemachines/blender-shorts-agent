"""Fonts for the edit's text: a family name found in the OS font folders, else a font that ships with the OS, else
Blender's default. Pure (no bpy), so the CLI can say whether a style font is installed before anything is built."""

from __future__ import annotations

import bisect
import os
import platform
import re
from pathlib import Path

# Tried in order when the style font is not installed (then Blender's default font).
FALLBACKS = {
    "Windows": ("Segoe UI", "Arial"),
    "Darwin": ("Helvetica Neue", "Helvetica"),
    "Linux": ("DejaVu Sans", "Liberation Sans"),
}
# Clean sans families worth suggesting when installed, after the OS's own fallbacks.
_SUGGEST = ("Bahnschrift", "Calibri", "Verdana", "Tahoma", "Trebuchet MS", "Avenir Next", "SF Pro Display", "Noto Sans",
            "Open Sans", "Roboto", "Inter", "Montserrat", "Lato", "Source Sans 3", "Ubuntu")  # fmt: skip
SUGGEST_MAX = 8


def font_dirs() -> list[Path]:
    """The OS font folders, including per-user ones (Windows installs a user's fonts under LOCALAPPDATA)."""
    system = platform.system()
    if system == "Windows":
        dirs = [Path(os.environ.get("WINDIR") or "C:/Windows") / "Fonts"]
        if os.environ.get("LOCALAPPDATA"):
            dirs.append(Path(os.environ["LOCALAPPDATA"]) / "Microsoft" / "Windows" / "Fonts")
        return dirs
    if system == "Darwin":
        return [Path("/Library/Fonts"), Path("/System/Library/Fonts"), Path.home() / "Library/Fonts"]
    return [Path("/usr/share/fonts"), Path("/usr/local/share/fonts"), Path.home() / ".local/share/fonts",
            Path.home() / ".fonts"]  # fmt: skip


def _squash(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _font_files(dirs: list[Path]) -> list[Path]:
    out = []
    for folder in dirs:
        if folder.is_dir():
            out += [p for p in folder.rglob("*") if p.suffix.lower() in (".ttf", ".otf", ".ttc")]
    return out


def font_files(dirs: list[Path]) -> list[Path]:
    """Every .ttf/.otf/.ttc file in `dirs` (list once, pass to find_font_file / cover_font)."""
    return _font_files(dirs)


def find_font_file(name: str, dirs: list[Path], files: list[Path] | None = None) -> Path | None:
    """The .ttf/.otf file for a family name, matched ignoring case, spaces and punctuation ("Segoe UI" ->
    segoeui.ttf). The plain file wins over a "Regular" one, which wins over the other weights."""
    want = _squash(name)
    if not want:
        return None
    best = None
    for path in files if files is not None else _font_files(dirs):
        stem = _squash(path.stem)
        if not stem.startswith(want):
            continue
        rest = stem[len(want) :]
        key = (0 if not rest else 1 if rest in ("regular", "book", "normal") else 2, len(rest), path.name.lower())
        if best is None or key < best[0]:
            best = (key, path)
    return best[1] if best else None


def resolve(name: str, dirs: list[Path] | None = None, system: str | None = None, cache_dirs: list[Path] | None = None):
    """(font file or None, family used or None, note or None) for the style font `name` (a family, a family with a
    weight "Family@600", or a file path). The note says which font is used instead when `name` is not installed.
    A weight is looked up in `cache_dirs` too (default: font_caches()), see resolve_weight."""
    if Path(name).is_file():
        return Path(name), Path(name).stem, None
    family, weight = parse_weight(name)
    if weight is not None:
        return resolve_weight(family, weight, dirs, system, cache_dirs)
    dirs = font_dirs() if dirs is None else dirs
    files = _font_files(dirs)
    path = find_font_file(name, dirs, files)
    if path is not None:
        return path, name, None
    for family in FALLBACKS.get(system or platform.system(), FALLBACKS["Linux"]):
        path = find_font_file(family, dirs, files)
        if path is not None:
            return path, family, f"font {name!r} is not installed, so the text uses {family}"
    return None, None, f"font {name!r} is not installed, so the text uses Blender's default font"


def suggestions(dirs: list[Path] | None = None, system: str | None = None) -> list[str]:
    """A short list of installed clean sans families, the OS's own fallbacks first."""
    dirs = font_dirs() if dirs is None else dirs
    files = _font_files(dirs)
    names = (*FALLBACKS.get(system or platform.system(), FALLBACKS["Linux"]), *_SUGGEST)
    return [n for n in dict.fromkeys(names) if find_font_file(n, dirs, files)][:SUGGEST_MAX]


# --------------------------------------------------------------------------- font files: cmap, name, OS/2, fvar
#
# A small stdlib reader for the sfnt tables that decide what a font draws: `cmap` (which characters have a glyph),
# `fvar` (a variable font: Blender loads only its default instance, often the Light weight), `name` and `OS/2`.

# Searched in order for a font that has every glyph a text needs, after the OS fallbacks (FALLBACKS). Names are
# family names or file stems (find_font_file matches both: "seguisym" is Segoe UI Symbol's file).
GLYPH_FALLBACKS = {
    "Windows": ("seguisym", "Cambria", "seguiemj", "Arial Unicode", "Nirmala UI", "msgothic"),
    "Darwin": ("Apple Symbols", "Menlo", "Arial Unicode", "STIXGeneral", "Apple Color Emoji"),
    "Linux": ("Noto Sans Symbols", "Noto Sans Symbols2", "Noto Sans Math", "FreeSans", "Noto Sans"),
}
WEIGHT_NAMES = {100: "Thin", 200: "ExtraLight", 300: "Light", 400: "Regular", 500: "Medium", 600: "SemiBold",
                700: "Bold", 800: "ExtraBold", 900: "Black"}  # fmt: skip
_INFO_CACHE: dict = {}
_INVISIBLE = frozenset((0x200B, 0x200C, 0x200D, 0xFEFF))  # zero-width characters: nothing to draw


def weight_name(weight: float | None) -> str:
    if weight is None:
        return "unknown"
    return WEIGHT_NAMES[min(WEIGHT_NAMES, key=lambda w: abs(w - float(weight)))]


def _u16(b: bytes, o: int) -> int:
    if o + 2 > len(b):
        raise IndexError("read past the end of a table")
    return int.from_bytes(b[o : o + 2], "big")


def _u32(b: bytes, o: int) -> int:
    if o + 4 > len(b):
        raise IndexError("read past the end of a table")
    return int.from_bytes(b[o : o + 4], "big")


def _fixed(b: bytes, o: int) -> float:
    return int.from_bytes(b[o : o + 4], "big", signed=True) / 65536


def _tables(data: bytes, index: int = 0) -> dict[str, bytes]:
    base = 0
    if data[:4] == b"ttcf":  # a collection: the `index`-th font
        if index >= _u32(data, 8):
            raise ValueError("font collection has fewer fonts")
        base = _u32(data, 12 + 4 * index)
    tag = data[base : base + 4]
    if tag not in (b"\x00\x01\x00\x00", b"OTTO", b"true", b"typ1"):
        raise ValueError("not a TrueType or OpenType font")
    out = {}
    for i in range(_u16(data, base + 4)):
        rec = base + 12 + 16 * i
        name = data[rec : rec + 4].decode("latin-1")
        off, length = _u32(data, rec + 8), _u32(data, rec + 12)
        if off + length > len(data):
            raise ValueError(f"table {name!r} runs past the end of the file")
        out[name] = data[off : off + length]
    return out


def _merge_ranges(ranges: list[tuple[int, int]]) -> list[tuple[int, int]]:
    out: list[list[int]] = []
    for lo, hi in sorted(ranges):
        if out and lo <= out[-1][1] + 1:
            out[-1][1] = max(out[-1][1], hi)
        else:
            out.append([lo, hi])
    return [(a, b) for a, b in out]


def _cmap4(t: bytes, o: int) -> list[tuple[int, int]]:
    n = _u16(t, o + 6) // 2
    ends, starts = o + 14, o + 16 + 2 * n
    deltas, ranges = starts + 2 * n, starts + 4 * n
    cps = []
    for i in range(n):
        end, start = _u16(t, ends + 2 * i), _u16(t, starts + 2 * i)
        delta, ro = _u16(t, deltas + 2 * i), _u16(t, ranges + 2 * i)
        for c in range(start, min(end, 0xFFFE) + 1):
            if ro == 0:
                gid = (c + delta) & 0xFFFF
            else:
                at = ranges + 2 * i + ro + 2 * (c - start)
                gid = _u16(t, at) if at + 2 <= len(t) else 0
                gid = (gid + delta) & 0xFFFF if gid else 0
            if gid:
                cps.append((c, c))
    return cps


def _cmap12(t: bytes, o: int) -> list[tuple[int, int]]:
    out = []
    for i in range(_u32(t, o + 12)):
        g = o + 16 + 12 * i
        start, end, gid = _u32(t, g), _u32(t, g + 4), _u32(t, g + 8)
        if gid == 0:  # the first codepoint maps to .notdef
            start += 1
        if start <= end:
            out.append((start, end))
    return out


def _cmap(t: bytes) -> list[tuple[int, int]]:
    """Covered codepoints as merged (first, last) ranges, from the best Unicode subtable (format 12, else 4)."""
    subs = {}
    for i in range(_u16(t, 2)):
        rec = 4 + 8 * i
        subs[(_u16(t, rec), _u16(t, rec + 2))] = _u32(t, rec + 4)
    for keys, fmt, reader in ((((3, 10), (0, 4), (0, 6)), 12, _cmap12), (((3, 1), (0, 3), (0, 1), (0, 0)), 4, _cmap4)):
        for key in keys:
            o = subs.get(key)
            if o is not None and _u16(t, o) == fmt:
                return _merge_ranges(reader(t, o))
    raise ValueError("the font has no Unicode cmap")


def _names(t: bytes) -> dict[int, str]:
    out: dict[int, tuple[int, str]] = {}
    count, strings = _u16(t, 2), _u16(t, 4)
    for i in range(count):
        rec = 6 + 12 * i
        pid, eid, lang, nid, length, off = (_u16(t, rec + 2 * k) for k in range(6))
        raw = t[strings + off : strings + off + length]
        if pid == 3 and eid in (0, 1, 10):
            text, rank = raw.decode("utf-16-be", "replace"), 0 if lang == 0x409 else 1
        elif pid == 0:
            text, rank = raw.decode("utf-16-be", "replace"), 2
        elif pid == 1 and eid == 0:
            text, rank = raw.decode("latin-1"), 3
        else:
            continue
        if nid not in out or rank < out[nid][0]:
            out[nid] = (rank, text)
    return {k: v for k, (_, v) in out.items()}


def _fvar(t: bytes, names: dict[int, str]) -> tuple[list[dict], str | None]:
    axes_at, n_axes, axis_size = _u16(t, 4), _u16(t, 8), _u16(t, 10)
    n_inst, inst_size = _u16(t, 12), _u16(t, 14)
    axes = []
    for i in range(n_axes):
        a = axes_at + i * axis_size
        axes.append({"tag": t[a : a + 4].decode("latin-1"), "min": _fixed(t, a + 4), "default": _fixed(t, a + 8),
                     "max": _fixed(t, a + 12)})  # fmt: skip
    default = None
    for i in range(n_inst):
        rec = axes_at + n_axes * axis_size + i * inst_size
        coords = [_fixed(t, rec + 4 + 4 * k) for k in range(n_axes)]
        if all(abs(c - a["default"]) < 1e-3 for c, a in zip(coords, axes, strict=True)):
            default = names.get(_u16(t, rec))
            break
    return axes, default


def font_info(path, index: int = 0) -> dict:
    """{file, family, subfamily, weight, variable, axes, default_weight, default_instance, ranges} of a .ttf, .otf or
    .ttc file (`ranges`: the codepoints it has glyphs for). ValueError when it is not a font this reader understands."""
    path = Path(path)
    st = path.stat()
    key = (str(path.resolve()), index, st.st_size, st.st_mtime_ns)
    if key in _INFO_CACHE:
        return _INFO_CACHE[key]
    try:
        tables = _tables(path.read_bytes(), index)
        if "cmap" not in tables:
            raise ValueError("the font has no cmap table")
        names = _names(tables["name"]) if "name" in tables else {}
        weight = _u16(tables["OS/2"], 4) if len(tables.get("OS/2", b"")) >= 6 else None
        info = {"file": str(path), "family": names.get(16) or names.get(1) or path.stem,
                "subfamily": names.get(17) or names.get(2) or "", "weight": weight, "variable": "fvar" in tables,
                "axes": [], "default_weight": weight, "default_instance": None,
                "ranges": _cmap(tables["cmap"])}  # fmt: skip
        if info["variable"]:
            info["axes"], info["default_instance"] = _fvar(tables["fvar"], names)
            wght = next((a for a in info["axes"] if a["tag"] == "wght"), None)
            if wght is not None:
                info["default_weight"] = round(wght["default"])
            info["default_instance"] = info["default_instance"] or weight_name(info["default_weight"])
    except (IndexError, KeyError, UnicodeDecodeError) as exc:
        raise ValueError(f"{path.name}: not a font this reader understands ({exc})") from None
    _INFO_CACHE[key] = info
    return info


def font_info_or_none(path, index: int = 0) -> dict | None:
    try:
        return font_info(path, index)
    except (OSError, ValueError):
        return None


# How Blender's sequencer sizes a text strip's box (measured in Blender 5.2 for several fonts and sizes): the first
# line is the font's bounding box tall ((head yMax - yMin) / unitsPerEm, times the size), each further line adds the
# font's line height (FreeType's: (ascender - descender + lineGap) / unitsPerEm from hhea, or from OS/2's typo metrics
# when the font sets USE_TYPO_METRICS or hhea has none). Blender's built-in font, measured: 1.16 and 1.41.
BLENDER_LINE_H = 1.16
BLENDER_BOX_H = 1.41


def _s16(b: bytes, o: int) -> int:
    return int.from_bytes(b[o : o + 2], "big", signed=True) if o + 2 <= len(b) else 0


def line_metrics(path, index: int = 0) -> tuple[float, float] | None:
    """(line height, first line's box height), in em, of a .ttf, .otf or .ttc font as the sequencer draws its text
    (see BLENDER_LINE_H); None when the file can't be read as a font."""
    try:
        tables = _tables(Path(path).read_bytes(), index)
        head, hhea, os2 = tables["head"], tables.get("hhea", b""), tables.get("OS/2", b"")
        upm = _u16(head, 18)
        if not upm:
            return None
        asc, desc, gap = _s16(hhea, 4), _s16(hhea, 6), _s16(hhea, 8)
        if len(os2) >= 74 and ((_u16(os2, 62) & 0x80) or (asc == 0 and desc == 0)):
            asc, desc, gap = _s16(os2, 68), _s16(os2, 70), _s16(os2, 72)
        line, box = (asc - desc + gap) / upm, (_s16(head, 42) - _s16(head, 38)) / upm
    except (OSError, ValueError, IndexError, KeyError):
        return None
    return (line, box) if line > 0 and box > 0 else None


def covers(info: dict, ch: str) -> bool:
    """Whether the font has a glyph for character `ch`."""
    cp = ord(ch)
    ranges = info["ranges"]
    i = bisect.bisect_right(ranges, (cp, 0x10FFFF)) - 1
    return i >= 0 and ranges[i][0] <= cp <= ranges[i][1]


def _needs_glyph(ch: str) -> bool:
    return not ch.isspace() and ord(ch) >= 0x20 and ord(ch) not in _INVISIBLE


def missing_chars(text: str, info: dict) -> list[str]:
    """The characters of `text` (in order, once each, never whitespace) the font has no glyph for."""
    return [ch for ch in dict.fromkeys(text) if _needs_glyph(ch) and not covers(info, ch)]


def missing_glyphs(text: str, path) -> list[str]:
    """missing_chars for a font file."""
    return missing_chars(text, font_info(path))


def static_weights(name: str, dirs: list[Path], files: list[Path] | None = None) -> list[dict]:
    """Installed static (non-variable) files of family `name`: [{weight, subfamily, file}] by weight."""
    want = _squash(name)
    out = []
    for path in files if files is not None else _font_files(dirs):
        if not _squash(path.stem).startswith(want):
            continue
        info = font_info_or_none(path)
        if info is None or info["variable"] or _squash(info["family"]) != want:
            continue
        out.append({"weight": info["weight"], "subfamily": info["subfamily"], "file": str(path)})
    return sorted(out, key=lambda d: (d["weight"] or 0, d["file"]))


def variable_warning(info: dict, name: str, cached: list[int] | None = None) -> str | None:
    """Why a variable font draws the wrong weight in Blender, and what to do; None for a static font. `cached`: the
    static weights of the family already downloaded (cached_weights): then the advice is to name one."""
    if not info.get("variable"):
        return None
    w = info.get("default_weight")
    family = info.get("family") or name
    why = (f"{name} is a variable font: Blender loads only its default instance ({info.get('default_instance')}, "
           f"weight {w}), so captions and 3D text come out {weight_name(w)}, not the weight you may expect.")
    if cached:
        return (f"{why} A static weight is already downloaded: set style.font to \"{family}@{pick_weight(cached)}\" "
                "(the family name alone loads the variable font).")  # fmt: skip
    return (f"{why} Use a static weight instead: an installed static file of {family}, or plan one with CLI assets "
            f"plan SHORT \"font:{family}@600\" (it downloads only after the user's yes).")  # fmt: skip


def brand_font_check(name: str, dirs: list[Path] | None = None, system: str | None = None,
                     cache_dirs: list[Path] | None = None) -> dict:  # fmt: skip
    """What `settings set brand.font NAME` reports, once, at setup: the same check as `CLI fonts NAME` (installed,
    variable, default weight, the installed static weights) with a `warning` worded for the brand setting."""
    name = str(name or "").strip()
    dirs = font_dirs() if dirs is None else dirs
    family, weight = parse_weight(name)
    if weight is not None:  # "Family@600": a static file of that weight, installed or downloaded
        path, _used, note = resolve_weight(family, weight, dirs, system, cache_dirs)
        found = note is None and path is not None
        return {"name": name, "installed": found, "file": str(path) if found else None,
                "warning": None if found else f"{note}: every brand short would use that until the weight is there"}
    path = Path(name) if name and Path(name).is_file() else (find_font_file(name, dirs) if name else None)
    out = {"name": name, "installed": path is not None, "file": str(path) if path else None, "warning": None}
    if path is None:
        note = resolve(name, dirs, system)[2] if name else None
        out["warning"] = (f"{note}: every brand short would use that instead. Install {name}, or set brand.font to "
                          "an installed font (CLI fonts lists a few).") if note else None  # fmt: skip
        return out
    info = font_info_or_none(path)
    if info is None:
        out["warning"] = f"{path.name} is not a font file Blender can read: set brand.font to another font"
        return out
    out.update(family=info["family"], variable=info["variable"], default_weight=info["default_weight"],
               default_instance=info["default_instance"])  # fmt: skip
    if info["variable"]:
        family = info["family"] or name
        statics = static_weights(family, dirs)
        out["static_weights"] = statics
        cached = cached_weights(family, cache_dirs)
        use = (f"settings set brand.font to one of the installed static weights listed here (as \"{family}@<weight>\", "
               "or its file path)"
               if statics else
               f"a static weight is already downloaded: settings set brand.font \"{family}@{pick_weight(cached)}\""
               if cached else
               f"download a static weight: at the first short, plan it with CLI assets plan SHORT "
               f"\"font:{family}@600\" (only after the user's yes), then settings set brand.font \"{family}@600\", which "
               "finds the download")  # fmt: skip
        out["warning"] = (f"{name} is a variable font: Blender loads only its default instance "
                          f"({info.get('default_instance')}, weight {info['default_weight']}), so every brand short's "
                          f"captions and 3D text come out {weight_name(info['default_weight'])}. Fix it once in the "
                          f"brand: {use}.")  # fmt: skip
    return out


def _is_brand_font(name: str, path: Path, settings: dict, short) -> bool:
    """Whether the short's style font is the brand's own font (brand.font) and the short follows the brand: then a
    problem with it is the brand's, said once at setup, not the short's."""
    from bsa import settings as bsa_settings

    brand_font = str(bsa_settings.brand_for(settings or {}, short).get("font") or "").strip()
    if not brand_font:
        return False
    if _squash(brand_font) == _squash(name):
        return True
    brand_path = Path(brand_font) if Path(brand_font).is_file() else None
    try:
        return brand_path is not None and brand_path.resolve() == Path(path).resolve()
    except OSError:
        return False


def _candidates(system: str | None) -> tuple[str, ...]:
    key = system or platform.system()
    return (*FALLBACKS.get(key, FALLBACKS["Linux"]), *GLYPH_FALLBACKS.get(key, GLYPH_FALLBACKS["Linux"]))


def cover_font(text: str, main, dirs: list[Path] | None = None, system: str | None = None,
               files: list[Path] | None = None) -> Path | None:  # fmt: skip
    """The font to draw `text` in: `main` when it has every glyph, else the first OS font that has them all
    (FALLBACKS, then GLYPH_FALLBACKS), else None (no installed font has them all)."""
    info = font_info_or_none(main) if main else None
    if info is not None and not missing_chars(text, info):
        return Path(main)
    dirs = font_dirs() if dirs is None else dirs
    files = _font_files(dirs) if files is None else files
    for name in _candidates(system):
        path = find_font_file(name, dirs, files)
        cand = font_info_or_none(path) if path else None
        if cand is not None and not missing_chars(text, cand):
            return path
    return None


def text_items(short, settings: dict) -> list[tuple[str, str]]:
    """(where, text) for everything the edit draws: captions (each line), labels, badges (upper case, as drawn) and
    the end card (its line and the @handle) when there is a handle."""
    from bsa.short import label_text

    items = [(f"line {ln.id}", ln.text) for ln in short.lines]
    for s in short.shots:
        items += [(f"shot {s.id} label", label_text(lb)) for lb in s.labels]
        if s.badge:
            items.append((f"shot {s.id} badge", s.badge.upper()))
    handle = str(settings.get("handle") or "").strip().lstrip("@")
    if handle:
        line = str(settings.get("end_card", "Follow for more") or "").strip()
        if line:
            items.append(("end card", line))
        items.append(("end card", "@" + handle))
    return items


def describe(path, name: str, text: str | None = None) -> dict:
    """What `CLI fonts NAME` adds for an installed font: family, weight, whether it is variable (with a warning and
    the installed static weights of the family) and, for `text`, the characters it has no glyph for."""
    info = font_info_or_none(path)
    if info is None:
        return {"readable": False}
    out = {"family": info["family"], "subfamily": info["subfamily"], "weight": info["weight"],
           "variable": info["variable"], "default_weight": info["default_weight"],
           "default_instance": info["default_instance"], "warning": variable_warning(info, name)}  # fmt: skip
    if info["variable"]:
        out["static_weights"] = static_weights(info["family"], font_dirs())
    if text is not None:
        out["missing_glyphs"] = missing_chars(text, info)
    return out


def family_of(path) -> str:
    info = font_info_or_none(path)
    return info["family"] if info else Path(path).stem


def glyph_warnings(short, settings: dict, dirs: list[Path] | None = None, system: str | None = None,
                   cache_dirs: list[Path] | None = None) -> list[str]:  # fmt: skip
    """Warnings about the short's style font: a weight ("Family@600") that is neither installed nor cached, a
    variable font (the wrong weight in Blender), and every character of the captions, labels, badges and end card it
    has no glyph for, with the font the edit draws that text in instead."""
    name = str((short.style or {}).get("font") or "").strip()
    if not name:
        return []
    dirs = font_dirs() if dirs is None else dirs
    files = _font_files(dirs)
    path, used, note = resolve(name, dirs, system, cache_dirs)
    weight_note = note if note and parse_weight(name)[1] is not None and not Path(name).is_file() else None
    info = font_info_or_none(path) if path else None
    if info is None:
        return [weight_note] if weight_note else []
    shown = used or name
    out = [weight_note] if weight_note else []
    family = info.get("family") or shown
    cached = cached_weights(family, cache_dirs) if info.get("variable") else []
    vw = None if weight_note else variable_warning(info, shown, cached)  # the weight note already says what to do
    if vw and _is_brand_font(name, path, settings, short):  # the brand's font: point at the setting, briefly
        if cached:
            out.append(f"{shown} (brand.font) is a variable font, so captions come out "
                       f"{weight_name(info.get('default_weight'))}; a static weight is already downloaded: settings "
                       f"set brand.font \"{family}@{pick_weight(cached)}\"")  # fmt: skip
        else:
            out.append(f"{shown} (brand.font) is a variable font, so captions come out "
                       f"{weight_name(info.get('default_weight'))}: fix it once with settings set brand.font to a "
                       f"static weight, \"{family}@600\" or a file (CLI fonts \"{family}\" lists them)")  # fmt: skip
    elif vw:
        out.append(vw)
    for where, text in text_items(short, settings):
        miss = missing_chars(text, info)
        if not miss:
            continue
        chars = ", ".join(f"'{c}' (U+{ord(c):04X})" for c in miss)
        alt = cover_font(text, path, dirs, system, files)
        then = (f"the edit draws this text in {family_of(alt)} instead" if alt else
                "no installed font has them, so they would show as boxes: change the text")  # fmt: skip
        out.append(f"{where}: font {shown} has no glyph for {chars} in {text!r}; {then}")
    return out


# --------------------------------------------------------------------------- a font with a weight: "Family@600"
#
# A brand font often exists only as a variable font, which Blender draws in its default instance (often Light).
# `brand.font` and `style.font` can name a static weight instead, "Fredoka@600": an installed static file of that
# weight, else the one `CLI assets` downloaded into `<workspace>/.assets-cache/fonts/<Family>-<weight>.ttf`.

_WEIGHTED = re.compile(r"^\s*(.+?)\s*@\s*(\d{3})\s*$")


def parse_weight(name: str) -> tuple[str, int | None]:
    """("Fredoka", 600) for "Fredoka@600" (a weight from 100 to 900); (name, None) for anything else."""
    m = _WEIGHTED.match(str(name or ""))
    if not m or not 100 <= int(m.group(2)) <= 900:
        return name, None
    return m.group(1), int(m.group(2))


def cached_font_name(family: str, weight: int) -> str:
    """The file name `CLI assets download` gives a static weight (bsa.assets.gfonts): "OpenSans-700.ttf"."""
    return f"{re.sub(r'[^A-Za-z0-9]', '', family)}-{weight}.ttf"


def font_caches(short_dir=None) -> list[Path]:
    """The `.assets-cache` folders a weighted font may have been downloaded to: the short's workspace (its parent
    folder), then the workspace setting's."""
    out = []
    if short_dir is not None:
        out.append(Path(short_dir).parent / ".assets-cache")
    try:
        from bsa import settings

        ws = settings.load_settings().get("workspace")
    except Exception:  # noqa: BLE001 - no readable settings: only the short's own workspace
        ws = None
    if ws:
        out.append(Path(ws) / ".assets-cache")
    return list(dict.fromkeys(out))


def weight_file(family: str, weight: int, dirs: list[Path] | None = None, files: list[Path] | None = None,
                cache_dirs: list[Path] | None = None) -> Path | None:  # fmt: skip
    """An installed static (non-variable) file of `family` at exactly `weight`, else the cached download of it in
    one of `cache_dirs` (default: font_caches()), else None."""
    dirs = font_dirs() if dirs is None else dirs
    for row in static_weights(family, dirs, files):
        if row["weight"] == weight:
            return Path(row["file"])
    for cache in font_caches() if cache_dirs is None else cache_dirs:
        path = Path(cache) / "fonts" / cached_font_name(family, weight)
        if path.is_file():
            return path
    return None


_CACHED = re.compile(r"^(.+)-(\d{3})\.ttf$", re.IGNORECASE)


def cached_weights(family: str, cache_dirs: list[Path] | None = None) -> list[int]:
    """The static weights of `family` that `CLI assets` already downloaded into one of `cache_dirs` (default:
    font_caches()), as <cache>/fonts/<Family>-<weight>.ttf: sorted, without repeats."""
    want = cached_font_name(family, 100)[: -len("-100.ttf")].lower()
    found = set()
    for cache in font_caches() if cache_dirs is None else cache_dirs:
        folder = Path(cache) / "fonts"
        if not folder.is_dir():
            continue
        for path in folder.iterdir():
            m = _CACHED.match(path.name)
            if m and m.group(1).lower() == want and 100 <= int(m.group(2)) <= 900 and path.is_file():
                found.add(int(m.group(2)))
    return sorted(found)


def pick_weight(weights: list[int], want: int = 600) -> int:
    """The weight of `weights` closest to `want` (the heavier one on a tie)."""
    return min(weights, key=lambda w: (abs(w - want), -w))


def weight_hint(family: str, weight: int) -> str:
    """How to get a static weight that is missing."""
    return (f"get a static file of that weight: plan one with CLI assets plan SHORT \"font:{family}@{weight}\" (it "
            f"downloads only after the user's yes, into <workspace>/.assets-cache/fonts/, where \"{family}@{weight}\" "
            "finds it), or install one")  # fmt: skip


def resolve_weight(family: str, weight: int, dirs: list[Path] | None = None, system: str | None = None,
                   cache_dirs: list[Path] | None = None):  # fmt: skip
    """resolve() for "Family@600": (the static file, "Family SemiBold", None); when no static file of that weight is
    installed or cached, the family as installed (or the OS fallback) with a note saying how to get the weight."""
    dirs = font_dirs() if dirs is None else dirs
    files = _font_files(dirs)
    path = weight_file(family, weight, dirs, files, cache_dirs)
    if path is not None:
        return path, f"{family} {weight_name(weight)}", None
    path, used, _ = resolve(family, dirs, system)
    if path is not None and used == family:
        info = font_info_or_none(path)
        if info is None:
            drawn = "its own weight"
        elif info["variable"]:
            drawn = f"{info['default_instance']}, weight {info['default_weight']}"
        else:
            drawn = f"weight {info['weight']}"
        return path, used, (f"font {family} has no static weight {weight} installed or downloaded, so the text uses "
                            f"{path.name} ({drawn}) instead: {weight_hint(family, weight)}")  # fmt: skip
    instead = used or "Blender's default font"
    return path, used, (f"font {family} at weight {weight} is not installed or downloaded, so the text uses "
                        f"{instead}: {weight_hint(family, weight)}")  # fmt: skip


def report(name: str, text: str | None = None, dirs: list[Path] | None = None,
           cache_dirs: list[Path] | None = None) -> dict:  # fmt: skip
    """What `CLI fonts NAME` says about NAME (a family, "Family@600" or a file): whether it is installed, its file
    and describe()'s details; for a weight, `requested_weight`, and a `warning` when that weight is missing."""
    family, weight = parse_weight(name)
    dirs = font_dirs() if dirs is None else dirs
    if Path(name).is_file() or weight is None:
        path = Path(name) if Path(name).is_file() else find_font_file(name, dirs)
        out = {"name": name, "installed": path is not None, "file": str(path) if path else None}
        return {**out, **(describe(path, name, text) if path else {})}
    path = weight_file(family, weight, dirs, None, cache_dirs)
    out = {"name": name, "requested_weight": weight, "installed": path is not None,
           "file": str(path) if path else None}  # fmt: skip
    if path is not None:
        return {**out, **describe(path, name, text)}
    have = find_font_file(family, dirs)
    only = f" (only {have.name})" if have else ""
    out["warning"] = f"{family} has no static weight {weight} installed or downloaded{only}: {weight_hint(family, weight)}"
    if have is not None:
        out["family_file"] = str(have)
    return out
