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
from bsa.overlays import NEAR_WRAP_PX, hex_colour

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


def measure(text: str, size: float, font) -> float:
    """The width of `text` at `size` px in `font` (its advance widths); for Blender's own font (None) and a font whose
    widths can't be read, BLENDER_CHAR_W per character (exact for Blender's own font, a guess for the other)."""
    em = fonts.advance_em(font, text) if font else None
    return (em if em is not None else len(text) * BLENDER_CHAR_W) * size


def wrap(text: str, size: float, font, width: float) -> list[str]:
    """`text` in lines no wider than `width`, greedy by words (a word wider than `width` gets its own line)."""
    lines: list[str] = []
    for para in str(text).split("\n"):
        line = ""
        for word in para.split():
            cand = f"{line} {word}" if line else word
            if not line or measure(cand, size, font) <= width:
                line = cand
            else:
                lines.append(line)
                line = word
        lines.append(line)
    return [ln for ln in lines if ln] or [""]


def _break(lines: list[str], size: float, font, width: float) -> list[str]:
    """`lines` with any line wider than `width` (a long word) broken by characters, so no line overflows its card."""
    out = []
    for line in lines:
        part = ""
        for ch in line:
            if part and measure(part + ch, size, font) > width:
                out.append(part.rstrip())
                part = ch.lstrip()
            else:
                part += ch
        out.append(part)
    return [ln for ln in out if ln] or [""]


def _lines(text: str, size: float, font, width: float) -> list[str]:
    return _break(wrap(text, size, font, width), size, font, width)


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


def _fit(text: str, size: int, font, width: float, floor: int = 1) -> int:
    """`size`, shrunk (to `floor` at least) so `text` fits in `width` on one line."""
    w = measure(text, size, font)
    return size if w <= width or w <= 0 else max(floor, int(size * width / w))


def _hexa(value: str, opacity: float) -> str:
    a = round(max(0.0, min(1.0, float(opacity))) * 255)
    return value if a >= 255 else f"{value}{a:02X}"


def _hex_of(rgba) -> str:
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


def caption(kit, text: str, font, width) -> CardSpec:
    """A caption box: the full `width` (the safe width at most), in the kit's caption colours, size and padding."""
    cap = kit.caption
    w, size, pad = _max_w(width), int(cap["size"]), cap["pad_px"]
    font, _ = font_for(text, font)
    lines = _lines(text, size, font, w - 2 * pad)
    total, ys = _stack([size] * len(lines), font)
    box_h = round(total + 2 * pad)
    off = (box_h - total) / 2
    runs = tuple(Run(ln, size, cap["color"], w / 2, off + y, "CENTER") for ln, y in zip(lines, ys, strict=True))
    return CardSpec("caption", w, box_h, box_h, _hexa(cap["box"], cap["opacity"]), None, 0, 0, cap["corner_px"], runs,
                    str(font) if font else None)  # fmt: skip


def title(kit, lines, highlight: int | None, font, width) -> CardSpec:
    """A title card: each line in the text colour (the `highlight` line bigger, in the highlight colour), shrunk to fit
    down to TITLE_MIN, then wrapped; as wide as its longest line (`width`, the safe width at most)."""
    maxw = _max_w(width)
    room = maxw - 2 * CARD_PAD
    font, _ = font_for(" ".join(lines), font)
    rows: list[tuple[str, int, str]] = []
    for i, line in enumerate(lines):
        big = i == highlight
        size = _fit(line, SIZES["title_highlight" if big else "title"], font, room, TITLE_MIN)
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


def pill(kit, kind: str, text: str, font, fill=None, colour=None, dot=None, wrap=NEAR_WRAP_PX) -> CardSpec:
    """A pill: a badge, category tag, step, label or measure label, its text wrapped at `wrap` px. A part label's
    `dot` (a colour) sits before the text. A category is filled in its kit colour with text that reads on it."""
    if kind not in PILL_KINDS:
        raise ValueError(f"{kind!r} is not a pill: use one of {', '.join(PILL_KINDS)}")
    size = SIZES[kind]
    if kind == "category" and fill is None:
        fill = kit.colour(text)
    fill = fill or kit.palette["card"]
    if colour is None:
        colour = (_hex_of(on_colour(hex_colour(fill))) if kind == "category" else
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


def bars(kit, bars: dict, font, width) -> CardSpec:
    """A value bars card: per item its label, a bar as long as its value (of `max`, else the biggest value) in its
    category colour, and its value text after the bar; the verdict under them in the highlight colour."""
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
    value_w = min(inner / 4, max([measure(bar_text(i), size, font) for i in items] or [0]))
    track = max(0.0, inner - label_w - DOT_GAP - value_w)
    runs, boxes, y = [], [], CARD_PAD
    for item, v in zip(items, values, strict=True):
        label = str(item.get("label") or "")
        ls = max(BAR_LABEL_MIN, _fit(label, size, font, label_w - DOT_GAP))
        lines = _lines(label, ls, font, label_w - DOT_GAP)
        total, ys = _stack([ls] * len(lines), font)
        row_h = max(BAR_H, total, text_height(1, size, font))
        cy = y + row_h / 2
        runs += [Run(ln, ls, kit.palette["text"], CARD_PAD, cy - total / 2 + c, "LEFT") for ln, c in zip(lines, ys,
                 strict=True)]  # fmt: skip
        length = track * min(1.0, v / top) if top > 0 else 0.0
        bx = CARD_PAD + label_w
        boxes.append((bx, cy - BAR_H / 2, length, BAR_H, kit.colour(item.get("category"))))
        text = bar_text(item)
        if text:
            runs.append(Run(text, _fit(text, size, font, value_w), kit.palette["text"], bx + length + DOT_GAP, cy,
                            "LEFT"))  # fmt: skip
        y += row_h + BAR_GAP
    if verdict:
        vs = SIZES["verdict"]
        lines = _lines(verdict, vs, font, inner)
        total, ys = _stack([vs] * len(lines), font)
        runs += [Run(ln, vs, kit.palette["highlight"], w / 2, y + c, "CENTER") for ln, c in zip(lines, ys, strict=True)]
        y += total + BAR_GAP
    box_h = round(y - BAR_GAP + CARD_PAD) if items or verdict else 2 * CARD_PAD
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
        size = _fit(name, SIZES["logo_name"], used, room)
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

