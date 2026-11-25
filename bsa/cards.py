"""The kit's cards, pure: their sizes from the real font advances, what each kind of card holds and its shape as
pixels.

A card is a CardSpec: the size of its PNG, its box (fill, outline, shadow, corner), the text strips that go on it
(Runs, drawn by the sequencer in `font`) and its extra shapes (a dot, value bars, the logo image's place). Everything
but the text and the logo image is drawn here as RGBA pixels (shape_rgba); bsa.blender writes those to PNGs, cached by
cache_key. Sizes are pixels of the 1080 x 1920 frame (the reference card proportions times 1080 / 405), origin at the
card's top-left. No bpy: the CLI and the checks size the cards before anything is built."""

from __future__ import annotations

import hashlib
import json
import math
import platform
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np

from bsa import fonts
from bsa.closing import on_colour
from bsa.layout import safe_rect
from bsa.overlays import NEAR_WRAP_PX, hex_colour, marked, plain

CARD_VERSION = 2  # in every cache key: bump when the drawing changes

SIZES = {"badge": 30, "category": 34, "step": 34, "label": 40, "measure": 40, "logo_name": 36, "title": 90,
         "title_highlight": 120, "bar": 34, "verdict": 36, "lead": 56, "website": 84, "handle": 52, "thumb_fact": 44,
         "thumb_kicker": 52}  # fmt: skip
PILL_PAD = (32, 11)  # a pill's text padding: (left and right, top and bottom)
CARD_PAD = 48  # a title or bars card's padding
TITLE_MIN = 56  # a title line shrinks to fit down to this size, then wraps
LOGO_H = 90  # the logo badge's height
LOGO_IMG = 75  # the logo image's height in it
DOT_D = 30  # a part label's colour dot
DOT_GAP = 16  # between the dot and the text (and between a bar and its value, the logo and the name)
BAR_H = 28
BAR_GAP = 18
TIGHT_BAR_SCALE = 0.75  # `bars(tight=True)`: the bar thickness, times BAR_H
OUTLINE_REF_SIZE = 70  # the "no box" caption's stroke_px is for this size and scales with the caption size
SHADOW_DY_REF = 6  # its drop shadow: offset down, blur sigma and opacity (offset and sigma scale with the size too)
SHADOW_SIGMA_REF = 3.0
SHADOW_ALPHA = 0.6
BOLD_EXTRA_EM = 0.046  # use_bold (the sequencer's synthetic bold) widens each character by this, measured in Blender 5.3
TIGHT_GAP_SCALE = 0.5  # and the gap between rows, times BAR_GAP
BAR_LABEL_SHARE = 0.32  # of a bars card's inner width, for the labels
BAR_LABEL_MIN = 24  # a bar label shrinks to fit down to this size, then wraps (its row grows)
PROGRESS_H = 26
POINTER_DOT = 22
PILL_KINDS = ("badge", "category", "step", "label", "measure")
_RING_PX = 2  # the ink ring round a part label's dot
# Blender's own font (no font file), measured in Blender 5.2: monospaced, every advance 0.600 em; its text box (the
# font's bounding box, fonts.BLENDER_BOX_H) has 0.185 em above the cap tops but 0.275 em under the descenders, so
# its text is drawn BLENDER_TEXT_DROP em lower to centre the ink (cap tops to descenders) in the box.
BLENDER_CHAR_W = 0.60
BLENDER_TEXT_DROP = 0.045


@dataclass(frozen=True)
class Run:
    """A text strip on a card, in card pixels from the top-left: `anchor` "CENTER" (x is the middle) or "LEFT" (x is
    the left edge), vertically centred on `y`."""

    text: str
    size: int
    colour: str
    x: float
    y: float
    anchor: str


@dataclass(frozen=True)
class TextFx:
    """How a "no box" caption's text is drawn: bold, a `stroke` colour outline `stroke_px` thick and (when `shadow_dy`
    > 0) a soft shadow of the same colour moved `shadow_dy` down, blurred by `shadow_sigma`, at `shadow_alpha`."""

    stroke_px: int
    stroke: str
    shadow_dy: int
    shadow_sigma: float
    shadow_alpha: float
    bold: bool = True


@dataclass(frozen=True)
class CardSpec:
    """A card: its PNG is `w` x `h` (h = box_h + shadow_px), the box `w` x `box_h` at the top. `bars` holds
    (x, y, w, h, colour) boxes, `logo_rect` the logo image's (x, y, w, h), both in card pixels."""

    kind: str
    w: int
    h: int
    box_h: int
    fill: str | None
    outline: str | None
    outline_px: int
    shadow_px: int
    radius: float
    runs: tuple[Run, ...]
    font: str | None
    dot: str | None = None
    logo: str | None = None
    logo_rect: tuple | None = None
    bars: tuple = ()
    fx: TextFx | None = None  # a caption without a box: its text's outline and shadow (drawn from the text's shape)


# --------------------------------------------------------------------------- fonts and measuring


def card_font(kit, cache_dirs=None) -> Path | None:
    """The font the cards are drawn in: the kit's font (or what fonts.resolve uses instead), else the first installed
    OS fallback (fonts.FALLBACKS); None (Blender's own font) when nothing is found."""
    return card_font_and_note(kit, cache_dirs)[0]


def card_font_and_note(kit, cache_dirs=None) -> tuple[Path | None, str | None]:
    """(card_font, the note when the kit's font or its weight is missing and another is used), resolved once."""
    if kit.font:
        path, _, note = fonts.resolve(kit.font, cache_dirs=cache_dirs)
        return path, note
    dirs = fonts.font_dirs()
    files = fonts.font_files(dirs)
    for family in fonts.FALLBACKS.get(platform.system(), fonts.FALLBACKS["Linux"]):
        path = fonts.find_font_file(family, dirs, files)
        if path is not None:
            return path, None
    return None, None


def measure(text: str, size: float, font, bold: bool = False) -> float:
    """The width of `text` at `size` px in `font` (its advance widths); for Blender's own font (None) and a font whose
    widths can't be read, BLENDER_CHAR_W per character (exact for Blender's own font, a guess for the other). `bold`:
    as the sequencer draws it with use_bold, BOLD_EXTRA_EM wider per character."""
    em = fonts.advance_em(font, text) if font else None
    return ((em if em is not None else len(text) * BLENDER_CHAR_W) + (len(text) * BOLD_EXTRA_EM if bold else 0)) * size


def wrap(text: str, size: float, font, width: float, bold: bool = False) -> list[str]:
    """`text` in lines no wider than `width`, greedy by words (a word wider than `width` gets its own line)."""
    lines: list[str] = []
    for para in str(text).split("\n"):
        line = ""
        for word in para.split():
            cand = f"{line} {word}" if line else word
            if not line or measure(cand, size, font, bold) <= width:
                line = cand
            else:
                lines.append(line)
                line = word
        lines.append(line)
    return [ln for ln in lines if ln] or [""]


def _break(lines: list[str], size: float, font, width: float, bold: bool = False) -> list[str]:
    """`lines` with any line wider than `width` (a long word) broken by characters, so no line overflows its card."""
    out = []
    for line in lines:
        part = ""
        for ch in line:
            if part and measure(part + ch, size, font, bold) > width:
                out.append(part.rstrip())
                part = ch.lstrip()
            else:
                part += ch
        out.append(part)
    return [ln for ln in out if ln] or [""]


def _lines(text: str, size: float, font, width: float, bold: bool = False) -> list[str]:
    return _break(wrap(text, size, font, width, bold), size, font, width, bold)


def metrics(font) -> tuple[float, float]:
    """(line height, first line's box height) in em, as the sequencer draws `font` (Blender's own for None)."""
    m = fonts.line_metrics(font) if font else None
    return m if m is not None else (fonts.BLENDER_LINE_H, fonts.BLENDER_BOX_H)


def text_drop(font) -> float:
    """How far below its box centre (in em) a line is drawn so its ink is centred: BLENDER_TEXT_DROP for Blender's
    own font, 0 for a font file (centred on its bounding box: Segoe UI's ink is within 0.01 em of it)."""
    return 0.0 if font else BLENDER_TEXT_DROP


def text_height(n_lines: int, size: float, font) -> float:
    """How tall the sequencer draws `n_lines` of text at `size` px: the first line's box, then a line height each."""
    line_h, box_h = metrics(font)
    return (box_h + (n_lines - 1) * line_h) * size


def font_for(text: str, font) -> tuple[Path | None, str | None]:
    """(the font to draw `text` in, a note or None): `font` when it has every glyph, else the first OS font that has
    them (fonts.cover_font), with the same note the edit gives (edit._Texts)."""
    if not font:
        return None, None
    info = fonts.font_info_or_none(font)
    miss = fonts.missing_chars(text, info) if info else []
    if not miss:
        return Path(font), None
    chars = ", ".join(f"'{c}' (U+{ord(c):04X})" for c in miss)
    alt = fonts.cover_font(text, font)
    if alt is not None:
        return Path(alt), f"{text!r}: {info['family']} has no {chars}, so this text uses {fonts.family_of(alt)}"
    return Path(font), f"{text!r}: no installed font has {chars}; they show as boxes"


def fit(text: str, size: int, font, width: float, floor: int = 1) -> int:
    """`size`, shrunk (to `floor` at least) so `text` fits in `width` on one line."""
    w = measure(text, size, font)
    return size if w <= width or w <= 0 else max(floor, int(size * width / w))


def _hexa(value: str, opacity: float) -> str:
    """`value` ("#RRGGBB" or "#RRGGBBAA") with its alpha times `opacity`: one alpha, never a second appended."""
    rgb, own = value[:7], (int(value[7:9], 16) / 255 if len(value) == 9 else 1.0)
    a = round(max(0.0, min(1.0, float(opacity))) * own * 255)
    return rgb if a >= 255 else f"{rgb}{a:02X}"


def hex_of(rgba) -> str:
    """"#RRGGBB" of an RGBA (or RGB) tuple of 0..1 floats; the alpha is dropped."""
    return "#" + "".join(f"{round(c * 255):02X}" for c in rgba[:3])


def _stack(sizes: list[int], font) -> tuple[float, list[float]]:
    """(total height, each line's centre from the top) for lines of `sizes` stacked as the sequencer spaces them."""
    line_h, box_h = metrics(font)
    ys, y = [], box_h * sizes[0] / 2
    for i, s in enumerate(sizes):
        if i:
            y += line_h * (sizes[i - 1] + s) / 2
        ys.append(y)
    return y + box_h * sizes[-1] / 2, ys


def _max_w(width) -> int:
    return int(min(width, safe_rect().w))


# --------------------------------------------------------------------------- the builders


def outline_fx(kit) -> TextFx:
    """The text effects of a "no box" caption from the kit: the stroke (`caption.stroke_px` at OUTLINE_REF_SIZE,
    scaled with `caption.size`) and the shadow (`caption.shadow`), both in the caption box colour (the dark one)."""
    cap = kit.caption
    k = int(cap["size"]) / OUTLINE_REF_SIZE
    px = float(cap.get("stroke_px", 4))
    stroke = max(1, round(px * k)) if px > 0 else 0
    on = bool(cap.get("shadow", True))
    return TextFx(stroke, cap["box"][:7], max(1, round(SHADOW_DY_REF * k)) if on else 0, SHADOW_SIGMA_REF * k, SHADOW_ALPHA)


def caption_pad(kit) -> int:
    """The margin round a caption's text inside its card: `caption.pad_px` for a box; for a "no box" caption just
    what its stroke and shadow need, so the card is the text's area plus its effects."""
    if kit.caption.get("style") != "outline":
        return int(kit.caption["pad_px"])
    fx = outline_fx(kit)
    return fx.stroke_px + 1 + (fx.shadow_dy + math.ceil(2 * fx.shadow_sigma) if fx.shadow_dy else 0)


def _flags(text: str) -> list[bool]:
    """Whether each non-space character of `text` (with its *highlight* asterisks) is highlighted, in order."""
    return [hl for piece, hl in marked(text) for ch in piece if not ch.isspace()]


def _runs(lines: list[str], flags: list[bool], ys: list[float], off: float, size: int, w: float, font, colour: str,
          highlight: str, bold: bool = False) -> tuple[Run, ...]:  # fmt: skip
    """The caption's runs: a line is one centred Run, or, when it holds highlighted words, one left-anchored Run per
    stretch of same-coloured words, each at its measured x (the line stays centred as a whole)."""
    runs, at = [], 0
    for ln, y in zip(lines, ys, strict=True):
        per = []  # each character's flag: a space between two highlighted characters is highlighted too
        for ch in ln:
            if ch.isspace():
                per.append(None)
            else:
                per.append(flags[at] if at < len(flags) else False)
                at += 1
        for i, f in enumerate(per):
            if f is None:
                before = next((per[j] for j in range(i - 1, -1, -1) if per[j] is not None), False)
                after = next((per[j] for j in range(i + 1, len(per)) if per[j] is not None), False)
                per[i] = before and after
        if not any(per):
            runs.append(Run(ln, size, colour, w / 2, off + y, "CENTER"))
            continue
        left, i = w / 2 - measure(ln, size, font, bold) / 2, 0
        while i < len(ln):
            j = i
            while j + 1 < len(ln) and per[j + 1] == per[i]:
                j += 1
            text = ln[i : j + 1].strip()
            if text:
                start = i + len(ln[i : j + 1]) - len(ln[i : j + 1].lstrip())
                runs.append(Run(text, size, highlight if per[i] else colour, left + measure(ln[:start], size, font, bold),
                                off + y, "LEFT"))
            i = j + 1
    return tuple(runs)


def caption(kit, text: str, font, width) -> CardSpec:
    """A caption card: in the kit's caption colours, size and padding. Style "box" (the default): the box, the full
    `width` (the safe width at most), the text wrapped inside the padding. Style "outline": no box, the card as wide
    as the text (one line when it fits the safe width) with just the margin its stroke and shadow need. A word in
    asterisks (`every *crossing*`) is drawn in `caption.highlight` (else the palette accent), the asterisks not."""
    cap = kit.caption
    outline = cap.get("style") == "outline"
    size, pad, maxw = int(cap["size"]), caption_pad(kit), _max_w(width)
    shown = plain(text)
    font, _ = font_for(shown, font)
    lines = _lines(shown, size, font, maxw - 2 * pad, outline)
    w = min(maxw, math.ceil(max(measure(ln, size, font, True) for ln in lines)) + 2 * pad) if outline else maxw
    total, ys = _stack([size] * len(lines), font)
    box_h = round(total + 2 * pad)
    off = (box_h - total) / 2
    runs = _runs(lines, _flags(text), ys, off, size, w, font, cap["color"], cap.get("highlight") or kit.palette["accent"],
                 outline)
    if outline:
        return CardSpec("caption", w, box_h, box_h, None, None, 0, 0, 0, runs, str(font) if font else None,
                        fx=outline_fx(kit))  # fmt: skip
    return CardSpec("caption", w, box_h, box_h, _hexa(cap["box"], cap["opacity"]), None, 0, 0, cap["corner_px"], runs,
                    str(font) if font else None)  # fmt: skip


def title(kit, lines, highlight: int | None, font, width, size: int | None = None) -> CardSpec:
    """A title card: each line in the text colour (the `highlight` line bigger, in the highlight colour), shrunk to fit
    down to TITLE_MIN, then wrapped; as wide as its longest line (`width`, the safe width at most). `size` sets the
    plain lines' size (SIZES["title"] when None), the highlight line keeping its proportion to it."""
    maxw = _max_w(width)
    room = maxw - 2 * CARD_PAD
    font, _ = font_for(" ".join(lines), font)
    base = SIZES["title"] if size is None else size
    big_size = round(base * SIZES["title_highlight"] / SIZES["title"])
    rows: list[tuple[str, int, str]] = []
    for i, line in enumerate(lines):
        big = i == highlight
        size = fit(line, big_size if big else base, font, room, min(TITLE_MIN, base))
        colour = kit.palette["highlight" if big else "text"]
        rows += [(ln, size, colour) for ln in _lines(line, size, font, room)]
    text_w = max(measure(t, s, font) for t, s, _ in rows)
    w = min(maxw, round(text_w + 2 * CARD_PAD))
    total, ys = _stack([s for _, s, _ in rows], font)
    box_h = round(total + 2 * CARD_PAD)
    runs = tuple(Run(t, s, c, w / 2, CARD_PAD + y, "CENTER") for (t, s, c), y in zip(rows, ys, strict=True))
    return _boxed(kit, "title", w, box_h, kit.palette["card"], kit.corner_px, runs, font)


def _boxed(kit, kind, w, box_h, fill, radius, runs, font, **extra) -> CardSpec:
    """A card with the kit's ink outline and shadow."""
    return CardSpec(kind, w, box_h + kit.shadow_px, box_h, fill, kit.palette["ink"], kit.outline_px, kit.shadow_px,
                    radius, tuple(runs), str(font) if font else None, **extra)  # fmt: skip


def pill(kit, kind: str, text: str, font, fill=None, colour=None, dot=None, wrap=NEAR_WRAP_PX,
         size: int | None = None) -> CardSpec:  # fmt: skip
    """A pill: a badge, category tag, step, label or measure label, its text wrapped at `wrap` px, at `size` px
    (SIZES[kind] when None). A part label's `dot` (a colour) sits before the text. A category is filled in its kit
    colour with text that reads on it."""
    if kind not in PILL_KINDS:
        raise ValueError(f"{kind!r} is not a pill: use one of {', '.join(PILL_KINDS)}")
    size = SIZES[kind] if size is None else size
    if kind == "category" and fill is None:
        fill = kit.colour(text)
    fill = fill or kit.palette["card"]
    if colour is None:
        colour = (hex_of(on_colour(hex_colour(fill))) if kind == "category" else
                  kit.palette["highlight" if kind == "measure" else "text"])  # fmt: skip
    extra = DOT_D + DOT_GAP if dot else 0
    room = min(wrap, safe_rect().w - 2 * PILL_PAD[0] - extra)
    font, _ = font_for(text, font)
    lines = _lines(text, size, font, room)
    text_w = max(measure(ln, size, font) for ln in lines)
    w = round(text_w + 2 * PILL_PAD[0] + extra)
    total, ys = _stack([size] * len(lines), font)
    box_h = round(total + 2 * PILL_PAD[1])
    off = (box_h - total) / 2
    x, anchor = (PILL_PAD[0] + extra, "LEFT") if dot else (w / 2, "CENTER")
    runs = [Run(ln, size, colour, x, off + y, anchor) for ln, y in zip(lines, ys, strict=True)]
    radius = kit.pill_corner_px if kit.pill_corner_px is not None else box_h / 2
    return _boxed(kit, kind, w, box_h, fill, radius, runs, font, dot=dot)


def bars(kit, bars: dict, font, width, tight: bool = False) -> CardSpec:
    """A value bars card: per item its label, a bar as long as its value (of `max`, else the biggest value) in its
    category colour, and its value text after the bar; the verdict under them in the highlight colour. `tight`
    halves the gap between rows and draws the bars TIGHT_BAR_SCALE as thick (a shot short of room)."""
    from bsa.short import bar_text

    items = [i for i in (bars or {}).get("items") or [] if isinstance(i, dict)]
    verdict = str((bars or {}).get("verdict") or "").strip()
    values = [_value(i.get("value")) for i in items]
    top = (bars or {}).get("max")
    top = float(top) if isinstance(top, int | float) and not isinstance(top, bool) and top > 0 else max(values or [0])
    w = _max_w(width)
    inner = w - 2 * CARD_PAD
    label_w = inner * BAR_LABEL_SHARE
    every = " ".join([*(str(i.get("label") or "") for i in items), *(bar_text(i) for i in items), verdict])
    font, _ = font_for(every, font)
    size = SIZES["bar"]
    bar_h, gap = (BAR_H * TIGHT_BAR_SCALE, BAR_GAP * TIGHT_GAP_SCALE) if tight else (BAR_H, BAR_GAP)
    value_w = min(inner / 4, max([measure(bar_text(i), size, font) for i in items] or [0]))
    track = max(0.0, inner - label_w - DOT_GAP - value_w)
    runs, boxes, y = [], [], CARD_PAD
    for item, v in zip(items, values, strict=True):
        label = str(item.get("label") or "")
        ls = max(BAR_LABEL_MIN, fit(label, size, font, label_w - DOT_GAP))
        lines = _lines(label, ls, font, label_w - DOT_GAP)
        total, ys = _stack([ls] * len(lines), font)
        row_h = max(bar_h, total, text_height(1, size, font))
        cy = y + row_h / 2
        runs += [Run(ln, ls, kit.palette["text"], CARD_PAD, cy - total / 2 + c, "LEFT") for ln, c in zip(lines, ys,
                 strict=True)]  # fmt: skip
        length = track * min(1.0, v / top) if top > 0 else 0.0
        bx = CARD_PAD + label_w
        boxes.append((bx, cy - bar_h / 2, length, bar_h, kit.colour(item.get("category"))))
        text = bar_text(item)
        if text:
            runs.append(Run(text, fit(text, size, font, value_w), kit.palette["text"], bx + length + DOT_GAP, cy,
                            "LEFT"))  # fmt: skip
        y += row_h + gap
    if verdict:
        vs = SIZES["verdict"]
        lines = _lines(verdict, vs, font, inner)
        total, ys = _stack([vs] * len(lines), font)
        runs += [Run(ln, vs, kit.palette["highlight"], w / 2, y + c, "CENTER") for ln, c in zip(lines, ys, strict=True)]
        y += total + gap
    box_h = round(y - gap + CARD_PAD) if items or verdict else 2 * CARD_PAD
    return _boxed(kit, "bars", w, box_h, kit.palette["card"], kit.corner_px, runs, font, bars=tuple(boxes))


def _value(v) -> float:
    """A bar's value as a number, 0 when it isn't one (short.validate is where a bad value is an error)."""
    if isinstance(v, bool) or not isinstance(v, int | float) or not math.isfinite(v):
        return 0.0
    return max(0.0, float(v))


def logo_badge(kit, font, name: str | None) -> CardSpec | None:
    """The user's logo (brand.logo.file) LOGO_IMG tall, keeping its aspect, with their `name` after it when given;
    None without a readable logo file."""
    from bsa.image_checks import png_size

    file = (kit.logo or {}).get("file")
    try:
        lw, lh = png_size(Path(file)) if file else (0, 0)
    except (OSError, ValueError):
        lw = lh = 0
    if not lw or not lh:
        return None
    side = (LOGO_H - LOGO_IMG) / 2
    img_w = min(LOGO_IMG * lw / lh, safe_rect().w - 2 * side)  # a very wide logo is scaled down to the safe width
    img_h = img_w * lh / lw
    rect = (side, (LOGO_H - img_h) / 2, img_w, img_h)
    runs, used = [], font
    w = side + img_w + side
    name = str(name or "").strip()
    x = side + img_w + DOT_GAP
    room = safe_rect().w - x - PILL_PAD[0]
    if name and room >= SIZES["logo_name"]:  # no room for a name: the logo alone
        used, _ = font_for(name, font)
        size = fit(name, SIZES["logo_name"], used, room)
        runs.append(Run(name, size, kit.palette["text"], x, LOGO_H / 2, "LEFT"))
        w = x + measure(name, size, used) + PILL_PAD[0]
    return _boxed(kit, "logo", min(_max_w(safe_rect().w), round(w)), LOGO_H, kit.palette["card"], kit.corner_px, runs,
                  used, logo=str(file), logo_rect=rect)  # fmt: skip


def progress_track(kit, width) -> CardSpec:
    """The progress bar's track: card fill, ink outline, round ends."""
    w = _max_w(width)
    ink = kit.palette["ink"]
    return CardSpec("progress_track", w, PROGRESS_H, PROGRESS_H, kit.palette["card"], ink, kit.outline_px, 0,
                    PROGRESS_H / 2, (), None)  # fmt: skip


def progress_fill(kit, width) -> CardSpec:
    """The progress bar's fill: the accent, inset by the track's outline (the edit crops it as the short plays)."""
    o = kit.outline_px
    w, h = _max_w(width) - 2 * o, PROGRESS_H - 2 * o
    return CardSpec("progress_fill", w, h, h, kit.palette["accent"], None, 0, 0, h / 2, (), None)


def pointer(kit, length) -> CardSpec:
    """A part label's pointer line: ink, max(outline_px, 3) thick, `length` long."""
    t = max(kit.outline_px, 3)
    return CardSpec("pointer", max(1, round(length)), t, t, kit.palette["ink"], None, 0, 0, t / 2, (), None)


def dot(kit) -> CardSpec:
    """The ink dot at a pointer line's part end."""
    d = POINTER_DOT
    return CardSpec("dot", d, d, d, kit.palette["ink"], None, 0, 0, d / 2, (), None)


# --------------------------------------------------------------------------- the shapes as pixels


def _sdf(px, py, x, y, w, h, r):
    """Signed distance (negative inside) from pixel centres to a rounded rectangle."""
    hw, hh = w / 2, h / 2
    r = max(0.0, min(r, hw, hh))
    qx = np.abs(px - (x + hw)) - hw + r
    qy = np.abs(py - (y + hh)) - hh + r
    return np.hypot(np.maximum(qx, 0), np.maximum(qy, 0)) + np.minimum(np.maximum(qx, qy), 0) - r


def _cover(d):
    return np.clip(0.5 - d, 0.0, 1.0)


def _over(img, colour: str, alpha) -> None:
    """Composite `colour` with per-pixel `alpha` over `img` (straight alpha floats), in place."""
    rgba = hex_colour(colour)
    if rgba is None:
        return
    sa = alpha * rgba[3]
    da = img[..., 3]
    out_a = sa + da * (1 - sa)
    safe = np.where(out_a > 0, out_a, 1)
    for c in range(3):
        img[..., c] = np.where(out_a > 0, (rgba[c] * sa + img[..., c] * da * (1 - sa)) / safe, 0)
    img[..., 3] = out_a


def shape_rgba(spec: CardSpec) -> np.ndarray:
    """The card's shapes as an (h, w, 4) uint8 straight-alpha image, top row first: the shadow (the box moved down
    shadow_px, in ink), the fill, the outline ring inside the box edge, the dot ringed in ink, the bars. Signed-distance
    edges with one pixel of antialiasing. Not the text or the logo image."""
    ys, xs = np.mgrid[0 : spec.h, 0 : spec.w].astype(np.float64) + 0.5
    img = np.zeros((spec.h, spec.w, 4))
    ink = spec.outline or "#000000"
    box = _sdf(xs, ys, 0, 0, spec.w, spec.box_h, spec.radius)
    if spec.shadow_px > 0:
        _over(img, ink, _cover(_sdf(xs, ys, 0, spec.shadow_px, spec.w, spec.box_h, spec.radius)))
    if spec.fill:
        _over(img, spec.fill, _cover(box))
    if spec.outline and spec.outline_px > 0:
        _over(img, spec.outline, _cover(box) - _cover(box + spec.outline_px))
    if spec.dot:
        cx, cy = PILL_PAD[0] + DOT_D / 2, spec.box_h / 2
        d = np.hypot(xs - cx, ys - cy) - DOT_D / 2
        _over(img, spec.dot, _cover(d))
        _over(img, ink, _cover(d) - _cover(d + _RING_PX))
    for x, y, w, h, colour in spec.bars:
        if w > 0:
            _over(img, colour, _cover(_sdf(xs, ys, x, y, w, h, h / 2)))
    return np.clip(np.round(img * 255), 0, 255).astype(np.uint8)


def _shift(a, dy: int, dx: int):
    """`a` moved down `dy` and right `dx` pixels (zeros come in at the far edge, nothing wraps)."""
    out = np.zeros_like(a)
    h, w = a.shape
    out[max(dy, 0) : h + min(dy, 0), max(dx, 0) : w + min(dx, 0)] = a[max(-dy, 0) : h - max(dy, 0),
                                                                       max(-dx, 0) : w - max(dx, 0)]  # fmt: skip
    return out


def _dilate(a, r: int):
    """`a` (0..1 coverage) grown by `r` pixels all round: the maximum under a disc, its edge softened by a pixel."""
    out = a.copy()
    for dy in range(-r, r + 1):
        for dx in range(-r, r + 1):
            if (dy or dx) and math.hypot(dy, dx) <= r + 0.25:
                out = np.maximum(out, _shift(a, dy, dx))
    soft = out / 2 + (_shift(out, 1, 0) + _shift(out, -1, 0) + _shift(out, 0, 1) + _shift(out, 0, -1)) / 8
    return np.maximum(a, soft)


def _blur(a, sigma: float):
    """A gaussian blur of `a`, separable, edges treated as empty."""
    if sigma <= 0:
        return a
    n = math.ceil(3 * sigma)
    k = np.exp(-0.5 * (np.arange(-n, n + 1) / sigma) ** 2)
    k /= k.sum()
    for axis in (0, 1):
        pad = [(0, 0), (0, 0)]
        pad[axis] = (n, n)
        p = np.pad(a, pad)
        a = sum(w * np.take(p, range(i, i + a.shape[axis]), axis=axis) for i, w in enumerate(k))
    return a


def fx_shape_rgba(spec: CardSpec, glyph) -> np.ndarray:
    """A "no box" caption's shapes as an (h, w, 4) uint8 straight-alpha image: the soft shadow, then the outline,
    both from `glyph`, the text's coverage ((h, w) floats 0..1, as Blender drew it). The text itself goes on top."""
    fx = spec.fx
    img = np.zeros((spec.h, spec.w, 4))
    glyph = np.clip(np.asarray(glyph, dtype=np.float64), 0.0, 1.0)
    stroke = _dilate(glyph, fx.stroke_px) if fx.stroke_px > 0 else glyph
    if fx.shadow_dy > 0:
        _over(img, fx.stroke, np.clip(_blur(_shift(stroke, fx.shadow_dy, 0), fx.shadow_sigma) * fx.shadow_alpha, 0, 1))
    if fx.stroke_px > 0:
        _over(img, fx.stroke, stroke)
    return np.clip(np.round(img * 255), 0, 255).astype(np.uint8)


def _stamp(path) -> list | None:
    try:
        st = Path(path).stat()
    except (OSError, TypeError):
        return None
    return [st.st_size, st.st_mtime_ns]


def cache_key(spec: CardSpec) -> str:
    """20 hex characters naming the card's PNG: its spec, its font and logo files' size and mtime, CARD_VERSION."""
    data = json.dumps({"spec": asdict(spec), "font": _stamp(spec.font) if spec.font else None,
                       "logo": _stamp(spec.logo) if spec.logo else None, "version": CARD_VERSION},
                      sort_keys=True)  # fmt: skip
    return hashlib.sha256(data.encode()).hexdigest()[:20]

