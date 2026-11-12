"""Thumbnail layout and hero picking, pure (no bpy): where the hero frame is cut from, where it lands, and where the
title, kicker, fact and @handle cards sit on the vertical (1080x1920) and landscape (1280x720) thumbnails, so the text
keeps off the subject and out of the zones the platforms cover. `BLENDER thumbs` draws a Layout in the sequencer.
Pixels, origin at the top-left."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING

from bsa.fonts import BLENDER_BOX_H, BLENDER_LINE_H
from bsa.layout import Rect, overlap_area
from bsa.overlays import CHAR_W, est_lines, est_width

if TYPE_CHECKING:
    from bsa.cards import CardSpec
    from bsa.kit import Kit

VERTICAL = (1080, 1920)
LANDSCAPE = (1280, 720)
SIZES = {"1080x1920": VERTICAL, "1280x720": LANDSCAPE}
# Below the top 6 %, above the bottom 20 %, clear of the right 12 %, 48 px from the left.
V_SAFE = Rect(48, 115.2, 902.4, 1420.8)
ZOOMS = (1.0, 1.05, 1.10, 1.15)
COVER_LIMIT = 0.02  # text may cover at most this share of the hero box
PLACEMENTS = ("top band", "bottom band", "middle-left", "smaller title")
SMALLER = 0.8
V_SIZES = {"title": 112, "kicker": 52, "fact": 44, "handle": 36}
L_SIZES = {"title": 76, "kicker": 40, "handle": 30}
MIN_TITLE = {"1080x1920": 60, "1280x720": 72}
PAD = {"title": 28, "pill": 18}
GAP = 20
PANEL_W = 576
SAMPLE_EVERY = 5
MIN_APART = 30

COVERS_SUBJECT = "the title covers the subject"
NO_HERO = "no subject box at this frame, so the centre 60 % of the frame is used"
FROM_PREVIEW = "from the preview: remake after the final render"
TITLE_TOO_LONG = "the title doesn't fit beside the subject: shorten it"

_SRC_W, _SRC_H = VERTICAL  # the source frames are the short's 1080x1920 renders
# Where the hero's centre goes in the zoomed window: away from the cards.
_HERO_AT = {"top band": (0.5, 0.62), "bottom band": (0.5, 0.38), "middle-left": (0.68, 0.5),
            "smaller title": (0.5, 0.62)}  # fmt: skip
_MIDDLE_Y = 0.45 * 1920
_L_DST = Rect(PANEL_W, 0, LANDSCAPE[0] - PANEL_W, LANDSCAPE[1])
_L_X, _L_WRAP = 48, 480
_L_MARGIN = 48  # the landscape stack keeps this far from the panel's top and bottom
_WHY = ("the middle of the longest shot", "the hook shot's fullest frame",
        "the frame where the subject fills the most of the safe area")  # fmt: skip


@dataclass(frozen=True)
class ThumbText:
    title: tuple[str, ...]
    kicker: str | None
    fact: str | None


def _clean(value) -> str | None:
    text = value.strip() if isinstance(value, str) else ""
    return text or None


def text_from_post(p: dict) -> ThumbText | None:
    """The thumbnail text from a post block (bsa.post.fields), or None without a title."""
    th = p.get("thumbnail") if isinstance(p, dict) else None
    if not isinstance(th, dict):
        return None
    raw = th.get("title")
    lines = raw.split("\n") if isinstance(raw, str) else raw
    if not isinstance(lines, list):
        return None
    title = tuple(x.strip() for x in lines if isinstance(x, str) and x.strip())
    if not title:
        return None
    return ThumbText(title, _clean(th.get("kicker")), _clean(th.get("fact")))


@dataclass(frozen=True)
class Hero:
    shot: str
    frame: int
    crop: tuple | None = None
    why: str = ""


def parse_hero(spec: str) -> Hero:
    """'s03@289' as Hero("s03", 289)."""
    m = re.fullmatch(r"\s*([^@\s]+)@(\d+)\s*", str(spec))
    if not m:
        raise ValueError(f"hero {spec!r} must have the form SHOT@FRAME, like s03@289")
    return Hero(m.group(1), int(m.group(2)))


def check_heroes(heroes: list[Hero], spans: dict) -> list[str]:
    """What is wrong with the heroes: a shot that is not in the short, a frame outside its shot."""
    errors = []
    for h in heroes:
        span = spans.get(h.shot)
        if span is None:
            errors.append(f"no shot {h.shot!r} in short.json")
        elif not span.f0 <= h.frame <= span.f1:
            errors.append(f"{h.shot}@{h.frame} is not in shot {h.shot} (frames {span.f0} to {span.f1})")
    return errors


VISIBLE_ALPHA = 0.5  # a caption colour more see-through than this would hide the thumbnail title
HIDDEN_CAPTION_TITLE = "the captions are see-through (style.caption), so the thumbnail title is drawn opaque instead"


@dataclass(frozen=True)
class ThumbStyle:
    title: tuple
    title_box: tuple
    kicker: tuple
    kicker_box: tuple
    fact: tuple
    fact_box: tuple
    handle: tuple
    handle_box: tuple
    panel: tuple
    font: str | None = None
    handle_text: str | None = None
    line_h: float = BLENDER_LINE_H  # the font's line height and first line's box, in em (bsa.fonts.line_metrics)
    box_h: float = BLENDER_BOX_H
    kit: Kit | None = None  # the brand kit (bsa.kit.kit_for): its cards instead of the text cards
    notes: tuple = ()  # what the style changed and why, reported with the thumbs command's warnings


def style_for(short, s: dict) -> ThumbStyle:
    """The thumbnail's colours and font from the captions, the brand and the short's own style: the title in the
    caption colours, the kicker on the palette's accent, the fact white on the caption box (less see-through), the
    @handle in the badge colour, the landscape panel in the palette's background. With a brand kit (brand.style set)
    the cards are the kit's (`kit`)."""
    from bsa import kit as kit_mod
    from bsa.closing import ACCENT, BACKGROUND, on_colour
    from bsa.overlays import BADGE_COLOR, BOX_COLOR, caption_colours, hex_colour
    from bsa.settings import brand_for

    s = s or {}
    brand = brand_for(s, short)
    own = getattr(short, "style", None) or {}
    palette: dict = {}
    for src in (brand.get("palette"), own.get("palette")):
        if isinstance(src, dict):
            palette.update(src)
    text, box = caption_colours(short, s)
    notes = ()
    if text[3] < VISIBLE_ALPHA:  # captions hidden (the short draws its own cards): the title must still show
        if box[3] < VISIBLE_ALPHA:
            box = (*BOX_COLOR[:3], 0.85)
        text = on_colour(box)
        notes = (HIDDEN_CAPTION_TITLE,)
    accent = hex_colour(palette.get("accent")) or hex_colour(ACCENT)
    fact_box = (*BOX_COLOR[:3], 0.85)
    font = str(own.get("font") or brand.get("font") or "").strip() or None
    handle = str(s.get("handle") or "").strip().lstrip("@")
    return ThumbStyle(
        title=text, title_box=box, kicker=on_colour(accent), kicker_box=accent, fact=(1.0, 1.0, 1.0, 1.0),
        fact_box=fact_box, handle=BADGE_COLOR, handle_box=fact_box,
        panel=hex_colour(palette.get("background")) or hex_colour(BACKGROUND), font=font,
        handle_text="@" + handle if handle else None, kit=kit_mod.kit_for(s, short), notes=notes,
    )  # fmt: skip


@dataclass
class Card:
    kind: str  # "title", "kicker", "fact" or "handle"
    text: str
    size: int
    rect: Rect  # the card's box: the text plus `pad` on every side
    colour: tuple
    box: tuple
    pad: float
    spec: CardSpec | None = None  # a brand kit's card (bsa.cards), drawn as an image; `rect` is its size


@dataclass
class Layout:
    size: tuple[int, int]
    src: Rect  # the part of the source frame shown
    dst: Rect  # where it lands in the thumbnail
    hero: Rect  # the hero box in thumbnail pixels
    cards: list[Card]
    placement: str
    panel: Rect | None
    panel_colour: tuple | None
    backdrop: str | None
    warnings: list[str]
    covered: float = 0.0  # the share of the hero box the cards cover


def _area(r: Rect) -> float:
    return max(r.w, 0) * max(r.h, 0)


def _clip(r: Rect, frame: Rect) -> Rect:
    x0, y0 = max(r.x, frame.x), max(r.y, frame.y)
    x1, y1 = min(r.right, frame.right), min(r.bottom, frame.bottom)
    return Rect(x0, y0, max(x1 - x0, 0), max(y1 - y0, 0))


def hero_box(boxes: list, frame=VERTICAL) -> tuple[Rect, list[str]]:
    """The union of the non-None [x0, y0, x1, y1] boxes, clipped to the frame; without any, the centre 60 % of the
    frame and the NO_HERO warning."""
    fw, fh = frame
    good = [b for b in boxes or [] if isinstance(b, list | tuple) and len(b) == 4]
    if good:
        x0, y0 = max(min(b[0] for b in good), 0), max(min(b[1] for b in good), 0)
        x1, y1 = min(max(b[2] for b in good), fw), min(max(b[3] for b in good), fh)
        if x1 > x0 and y1 > y0:
            return Rect(x0, y0, x1 - x0, y1 - y0), []
    return Rect(fw * 0.2, fh * 0.2, fw * 0.6, fh * 0.6), [NO_HERO]


def covered(cards: list[Card], hero: Rect) -> float:
    """The cards' summed overlap with the hero box, as a share of its area (0 for an empty box)."""
    area = _area(hero)
    return sum(overlap_area(c.rect, hero) for c in cards) / area if area > 0 else 0.0


def _title_size(lines: tuple[str, ...], base: float, wrap: float, minimum: int) -> int:
    """The title size: `base`, shrunk so the widest line fits `wrap`, never below `minimum`."""
    longest = max(len(x) for x in lines)
    fit = wrap / (longest * CHAR_W) if longest else base
    return max(int(min(base, fit)), minimum)


def _card(style: ThumbStyle, kind: str, text: str, size: int, pad: float, wrap: float, x: float = 0.0,
          y: float = 0.0) -> Card:  # fmt: skip
    """A card at (x, y) in the style's colours for `kind`, its text wrapped at `wrap` (the card is `wrap` + 2 * pad
    wide at most) and as tall as the sequencer draws its box in the style's font: the first line the font's
    bounding box, each further line its line height (bsa.fonts.line_metrics). With a brand kit, the kit's card
    (_kit_card)."""
    if style.kit is not None:
        return _kit_card(style, kind, text, size, pad, wrap, x, y)
    w = est_width(text, size, wrap)
    h = (style.box_h + (est_lines(text, size, wrap) - 1) * style.line_h) * size
    return Card(kind, text, size, Rect(x, y, w + 2 * pad, h + 2 * pad), getattr(style, kind),
                getattr(style, kind + "_box"), pad)  # fmt: skip


def _kit_card(style: ThumbStyle, kind: str, text: str, size: int, pad: float, wrap: float, x: float,
              y: float) -> Card:  # fmt: skip
    """The kit's card for `kind` at (x, y), no wider than `wrap` + 2 * pad (as the text card it stands for): the
    title a title card (a second line highlighted) at `size`, the kicker a category pill in the accent, the fact a
    measure pill, the @handle the logo badge (its name, else the handle, beside the logo) when the kit has a readable
    logo, else a label pill. `colour` and `box` are its (first) text colour and its fill, for the thumbnail's checks."""
    from bsa import cards
    from bsa.overlays import hex_colour

    k, font = style.kit, style.font
    width = wrap + 2 * pad
    room = width - 2 * cards.PILL_PAD[0]
    spec = None
    if kind == "title":
        lines = text.split("\n")
        spec = cards.title(k, lines, 1 if len(lines) == 2 else None, font, width, size=size)
    elif kind == "kicker":
        spec = cards.pill(k, "category", text, font, fill=k.palette["accent"], wrap=room, size=size)
    elif kind == "fact":
        spec = cards.pill(k, "measure", text, font, wrap=room, size=size)
    elif kind == "handle":
        if k.logo:
            spec = cards.logo_badge(k, font, k.logo.get("name") or text)
            if spec is not None and spec.w > width:  # too wide for the room (the landscape panel): the logo alone
                spec = cards.logo_badge(k, font, None)
            if spec is not None and spec.w > width:
                spec = None
        if spec is None:
            spec = cards.pill(k, "label", text, font, wrap=room, size=size)
    if spec is None:
        raise ValueError(f"no kit card for {kind!r}")
    colour = spec.runs[0].colour if spec.runs else k.palette["text"]
    return Card(kind, text, size, Rect(x, y, spec.w, spec.h), hex_colour(colour), hex_colour(spec.fill), pad, spec)


def _moved(c: Card, x: float, y: float) -> Card:
    return Card(c.kind, c.text, c.size, Rect(x, y, c.rect.w, c.rect.h), c.colour, c.box, c.pad, c.spec)


def _v_cards(text: ThumbText, style: ThumbStyle, placement: str) -> list[Card]:
    k = SMALLER if placement == "smaller title" else 1.0
    t_wrap = V_SAFE.w - 2 * PAD["title"]
    p_wrap = V_SAFE.w - 2 * PAD["pill"]
    t_size = _title_size(text.title, V_SIZES["title"] * k, t_wrap, MIN_TITLE["1080x1920"])
    stack = [_card(style, "title", "\n".join(text.title), t_size, PAD["title"], t_wrap)]
    if text.kicker:
        stack.append(_card(style, "kicker", text.kicker, round(V_SIZES["kicker"] * k), PAD["pill"], p_wrap))
    if text.fact:
        stack.append(_card(style, "fact", text.fact, round(V_SIZES["fact"] * k), PAD["pill"], p_wrap))
    out, top = [], V_SAFE.y
    if style.handle_text:
        badge = _card(style, "handle", style.handle_text, round(V_SIZES["handle"] * k), PAD["pill"], p_wrap,
                      V_SAFE.x, V_SAFE.y)  # fmt: skip
        out.append(badge)
        top = badge.rect.bottom + GAP
    total = sum(c.rect.h for c in stack) + GAP * (len(stack) - 1)
    y = top
    if placement == "middle-left":
        y = max(min(_MIDDLE_Y - total / 2, V_SAFE.bottom - total), top)
    elif placement == "bottom band":
        y = max(V_SAFE.bottom - total, top)
    for c in stack:
        x = V_SAFE.x if placement == "middle-left" else V_SAFE.x + (V_SAFE.w - c.rect.w) / 2
        out.append(_moved(c, x, y))
        y += c.rect.h + GAP
    return out


def _clamp(v: float, lo: float, hi: float) -> float:
    return min(max(v, lo), hi)


def _place(cx: float, cy: float, w: float, h: float, ax: float = 0.5, ay: float = 0.5) -> Rect:
    """A w x h window with the point (cx, cy) at (ax, ay) of it, clamped inside the source frame."""
    w, h = min(w, _SRC_W), min(h, _SRC_H)
    return Rect(_clamp(cx - ax * w, 0, _SRC_W - w), _clamp(cy - ay * h, 0, _SRC_H - h), w, h)


def _crop_rect(crop) -> Rect:
    x0, y0, x1, y1 = (float(v) for v in crop)
    return Rect(x0, y0, x1 - x0, y1 - y0)


def _crop_window(crop, aspect: float) -> Rect:
    """The crop grown to `aspect` (w / h) around its centre, clamped inside the source frame."""
    c = _crop_rect(crop)
    w, h = c.w, c.h
    if h <= 0 or w / h > aspect:
        h = w / aspect
    else:
        w = h * aspect
    if w > _SRC_W:
        w, h = _SRC_W, _SRC_W / aspect
    if h > _SRC_H:
        w, h = _SRC_H * aspect, _SRC_H
    return _place(c.x + c.w / 2, c.y + c.h / 2, w, h)


def _mapped(hero: Rect, src: Rect, dst: Rect) -> Rect:
    """The hero box in thumbnail pixels, clipped to where the frame lands."""
    s = dst.w / src.w
    r = Rect(dst.x + (hero.x - src.x) * s, dst.y + (hero.y - src.y) * s, hero.w * s, hero.h * s)
    return _clip(r, dst)


def vertical(text: ThumbText, hero: Rect, style: ThumbStyle, crop: tuple | None = None) -> Layout:
    """The 1080x1920 thumbnail: the hero frame scaled to fill, zoomed up to 15 % so the cards (title, kicker, fact,
    and the @handle badge top-left) clear the hero box. Placements in PLACEMENTS order, zooms in ZOOMS order inside
    each; the first that covers at most COVER_LIMIT of the hero wins, else the least covered with COVERS_SUBJECT."""
    dst = Rect(0, 0, *VERTICAL)
    tries = []
    for placement in PLACEMENTS:
        cards = _v_cards(text, style, placement)
        ax, ay = _HERO_AT[placement]
        for z in (1.0,) if crop else ZOOMS:
            if crop:
                src = _crop_window(crop, VERTICAL[0] / VERTICAL[1])
            else:
                src = _place(hero.x + hero.w / 2, hero.y + hero.h / 2, _SRC_W / z, _SRC_H / z, ax, ay)
            box = _mapped(hero, src, dst)
            cover = covered(cards, box)
            lay = Layout(VERTICAL, src, dst, box, cards, placement, None, None, None, [], cover)
            if cover <= COVER_LIMIT:
                return lay
            tries.append((cover, len(tries), lay))
    best = min(tries, key=lambda t: t[:2])[2]
    best.warnings.append(COVERS_SUBJECT)
    return best


def landscape(text: ThumbText, hero: Rect, style: ThumbStyle, crop: tuple | None = None,
              backdrop: str | None = None) -> Layout:  # fmt: skip
    """The 1280x720 thumbnail: the hero cut out around its box (or the crop) on the right, the title, kicker and
    @handle left-aligned on a panel in the palette's background on the left, the block centred vertically.
    `backdrop` (an image to show instead of the plain panel) is only carried."""
    t_wrap = _L_WRAP - 2 * PAD["title"]
    p_wrap = _L_WRAP - 2 * PAD["pill"]
    t_size = _title_size(text.title, L_SIZES["title"], t_wrap, MIN_TITLE["1280x720"])
    title = "\n".join(text.title)
    cards = [_card(style, "title", title, t_size, PAD["title"], t_wrap)]
    if text.kicker:
        cards.append(_card(style, "kicker", text.kicker, L_SIZES["kicker"], PAD["pill"], p_wrap))
    if style.handle_text:
        cards.append(_card(style, "handle", style.handle_text, L_SIZES["handle"], PAD["pill"], p_wrap))
    room = LANDSCAPE[1] - 2 * _L_MARGIN

    def total() -> float:
        return sum(c.rect.h for c in cards) + GAP * (len(cards) - 1)

    while total() > room and cards[0].size > MIN_TITLE["1280x720"]:  # a wrapped title gets smaller first
        cards[0] = _card(style, "title", title, cards[0].size - 1, PAD["title"], t_wrap)
    warnings = [TITLE_TOO_LONG] if total() > room else []
    y = max((LANDSCAPE[1] - total()) / 2, 0)
    placed = []
    for c in cards:
        placed.append(_moved(c, _L_X, y))
        y += c.rect.h + GAP
    b = _crop_rect(crop) if crop else hero
    aspect = _L_DST.w / _L_DST.h
    w = _clamp(max(b.w, b.h * aspect) * 1.25, 432, _SRC_W)
    src = _place(b.x + b.w / 2, b.y + b.h / 2, w, w / aspect)
    panel = Rect(0, 0, PANEL_W, LANDSCAPE[1])
    box = _mapped(hero, src, _L_DST)
    cover = covered(placed, box)
    if cover > COVER_LIMIT:  # cannot happen while the cards stay on the panel
        warnings.append(COVERS_SUBJECT)
    return Layout(LANDSCAPE, src, _L_DST, box, placed, "left panel", panel, style.panel, backdrop, warnings, cover)


def _r(r: Rect | None) -> list | None:
    return None if r is None else [r.x, r.y, r.w, r.h]


def _card_dict(c: Card) -> dict:
    d = {"kind": c.kind, "text": c.text, "size": c.size, "rect": _r(c.rect), "colour": list(c.colour),
         "box": list(c.box), "pad": c.pad}  # fmt: skip
    if c.spec is not None:
        d["card"] = c.spec.kind
    return d


def layout_dict(lay: Layout) -> dict:
    """A Layout as JSON: Rects as [x, y, w, h], colours as lists."""
    return {
        "size": list(lay.size),
        "src": _r(lay.src),
        "dst": _r(lay.dst),
        "hero": _r(lay.hero),
        "cards": [_card_dict(c) for c in lay.cards],
        "placement": lay.placement,
        "panel": _r(lay.panel),
        "panel_colour": None if lay.panel_colour is None else list(lay.panel_colour),
        "backdrop": lay.backdrop,
        "warnings": list(lay.warnings),
        "covered": lay.covered,
    }


def sample_frames(f0: int, f1: int, every: int = SAMPLE_EVERY) -> list[int]:
    """f0, every `every`th frame after it, and f1."""
    out = list(range(f0, f1 + 1, max(int(every), 1)))
    if out and out[-1] != f1:
        out.append(f1)
    return out


def fill(box: Rect | None) -> float:
    """How much of the vertical safe box the subject box fills (0 to 1)."""
    return 0.0 if box is None else overlap_area(box, V_SAFE) / _area(V_SAFE)


def _middle(span, flagged: set[int]) -> int | None:
    mid = (span.f0 + span.f1) // 2
    good = [f for f in range(span.f0, span.f1 + 1) if f not in flagged]
    return min(good, key=lambda f: (abs(f - mid), f)) if good else None


def _fullest(spans: list, flagged: set[int], fills: dict) -> tuple | None:
    """(span, frame) of the sampled unflagged frame with the largest fill (ties: the earliest), or None."""
    best = None
    for span in spans:
        for f in sample_frames(span.f0, span.f1):
            if f in flagged or f not in fills:
                continue
            if best is None or fills[f] > best[0] or (fills[f] == best[0] and f < best[2]):
                best = (fills[f], span, f)
    return None if best is None else (best[1], best[2])


def pick_heroes(spans: list, flagged: set[int], fills: dict[int, float], hook: str) -> list[Hero]:
    """Up to three hero frames, each with its reason: the middle of the longest shot, the hook shot's fullest
    frame, the frame where the subject fills the most of the safe area (in a shot not used yet when there is one).
    Flagged frames are never picked; a pick within MIN_APART frames of an earlier one is dropped."""
    picks: list[Hero] = []

    def add(shot: str, frame: int | None, why: str) -> None:
        if frame is not None and all(abs(frame - p.frame) >= MIN_APART for p in picks):
            picks.append(Hero(shot, frame, None, why))

    if spans:
        longest = max(enumerate(spans), key=lambda t: (t[1].f1 - t[1].f0, t[0]))[1]
        add(longest.id, _middle(longest, flagged), _WHY[0])
    hook_span = next((s for s in spans if s.id == hook), None)
    if hook_span is not None:
        got = _fullest([hook_span], flagged, fills)
        add(hook_span.id, got[1] if got else _middle(hook_span, flagged), _WHY[1])
    used = {p.shot for p in picks}
    got = _fullest([s for s in spans if s.id not in used], flagged, fills) or _fullest(spans, flagged, fills)
    if got:
        add(got[0].id, got[1], _WHY[2])
    return picks


def flagged_frames(sdir, run_key: str) -> set[int]:
    """Every `frame` and `redo_frames` of the findings in review/frame-checks-<run_key>.json, as `BLENDER
    frame-check` writes it: {"errors", "warnings", ["redo_all",] "findings": [{"check", "severity", "obj", "frame",
    "message", "shot", ["redo_frames", "redo"]}]} (frames of the edited video; `frame` None for a finding without
    one)."""
    try:
        data = json.loads((Path(sdir) / "review" / f"frame-checks-{run_key}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    rows = data.get("findings") if isinstance(data, dict) else None
    out: set[int] = set()
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        f = row.get("frame")
        if isinstance(f, int) and not isinstance(f, bool):
            out.add(f)
        redo = row.get("redo_frames")
        if isinstance(redo, list):
            out.update(x for x in redo if isinstance(x, int) and not isinstance(x, bool))
    return out


# --------------------------------------------------------------------------- the candidates and their files

THUMBS_JSON = "review/thumbs.json"
DIR = "review/thumbs"
WHY_POST = "named in short.json"
WHY_FLAG = "named with --hero"


def files_for(n: int) -> dict[str, str]:
    """{size: the candidate's PNG, relative to the short's folder}."""
    return {size: f"{DIR}/{n}-{size}.png" for size in SIZES}


def _empty() -> dict:
    return {"candidates": [], "chosen": None}


def load(sdir) -> dict:
    """review/thumbs.json: {"candidates": [rows], "chosen": None or the chosen candidate}; empty when missing.
    Raises bsa.review.store.StoreDamaged when it is damaged."""
    from bsa.review import store

    data = store.read_json(Path(sdir) / THUMBS_JSON, _empty(), expect=dict)
    return {**_empty(), **data}


def _chosen_n(chosen) -> int | None:
    n = chosen.get("n") if isinstance(chosen, dict) else chosen
    return n if isinstance(n, int) and not isinstance(n, bool) else None


def put_candidates(sdir, rows: list[dict], only: int | None = None) -> dict:
    """Write the candidates into review/thumbs.json (under its lock): all of them, or with `only` just row `only`
    (taken from `rows`, put in order). Remaking the chosen candidate (or all of them) clears `chosen`: its files in
    out/ stay, but they are no longer this candidate's. Returns the new store."""
    from bsa.review import store

    def change(data: dict) -> dict:
        data = {**_empty(), **data}
        if only is None:  # every candidate made again, the chosen one too
            data["candidates"] = sorted(rows, key=lambda r: r["n"])
            data["chosen"] = None
        else:
            new = next((r for r in rows if r["n"] == only), None)
            if new is None:
                raise ValueError(f"no candidate {only} among the rows to put: {[r.get('n') for r in rows]}")
            keep = [c for c in data["candidates"] if isinstance(c, dict) and c.get("n") != only]
            data["candidates"] = sorted([*keep, new], key=lambda r: r["n"])
            if _chosen_n(data["chosen"]) == only:
                data["chosen"] = None
        return data

    path = Path(sdir) / THUMBS_JSON
    path.parent.mkdir(parents=True, exist_ok=True)
    return store.update_json(path, _empty(), change, expect=dict)


def _dumps(data, crlf: bool) -> bytes:
    """JSON as the store and save_short write it (indent 2, not ASCII-escaped), with the file's own line ends."""
    text = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    return (text.replace("\n", "\r\n") if crlf else text).encode("utf-8")


def _read_short_json(path: Path) -> tuple[dict, bool]:
    """source/short.json as a raw dict and whether its lines end in CRLF; ValueError when it can't be read."""
    from bsa.review import store

    try:
        raw = store.read_bytes(path)
        data = json.loads(raw.decode("utf-8"))
    except FileNotFoundError:
        raise ValueError("source/short.json is missing") from None
    except (OSError, UnicodeDecodeError, ValueError) as e:
        raise ValueError(f"source/short.json can't be read: {e}") from None
    if not isinstance(data, dict):
        raise ValueError("source/short.json can't be read: it is not an object")  # noqa: TRY004 - a refusal
    return data, b"\r\n" in raw


LOCK_S = 5.0  # how long choose waits for the review/thumbs.json and short.json locks


def choose(sdir, n: int, by: str, now: float | None = None) -> dict:
    """Make candidate `n` the short's thumbnail: copy both its sizes to out/ (postpack.OUT_FILES), record
    review/thumbs.json `chosen` {"n", "by", "at"} and short.json `post.thumbnail.chosen` (that one key, nothing else).
    All or nothing: both locks (review/thumbs.json, then short.json) are taken and short.json read before anything is
    written, and a write that fails puts back what was there. Two choices at once never leave out/ and the records
    naming different candidates. ValueError for a candidate that isn't there, a missing file or an unreadable
    short.json; TimeoutError when a lock stays held."""
    import time

    from bsa import jobstate, postpack
    from bsa.review import store

    if not isinstance(n, int) or isinstance(n, bool):
        raise ValueError("The thumbnail choice must be a whole number")  # noqa: TRY004 - a refusal
    if by not in ("page", "chat"):
        raise ValueError(f"unknown author {by!r}; expected page or chat")
    sdir = Path(sdir)
    path = sdir / THUMBS_JSON
    short_path = sdir / "source" / "short.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    with store.locked(path, timeout_s=LOCK_S), store.locked(short_path, timeout_s=LOCK_S):
        data = {**_empty(), **store.read_json(path, _empty(), expect=dict)}
        row = next((c for c in data["candidates"] if isinstance(c, dict) and c.get("n") == n), None)
        if row is None:
            raise ValueError(f"There is no thumbnail candidate {n}")
        files = files_for(n)
        for rel in files.values():
            if not (sdir / rel).is_file():
                raise ValueError(f"Thumbnail candidate {n} has no file {rel}: make it again (BLENDER thumbs SHORT "
                                 f"--only {n})")  # fmt: skip
        short, crlf = _read_short_json(short_path)
        post = short.get("post") if isinstance(short.get("post"), dict) else {}
        th = post.get("thumbnail") if isinstance(post.get("thumbnail"), dict) else {}
        short["post"] = {**post, "thumbnail": {**th, "chosen": n}}
        data["chosen"] = {"n": n, "by": by, "at": time.time() if now is None else now}
        writes = [(sdir / rel, store.read_bytes(sdir / files[size])) for size, rel in postpack.OUT_FILES.items()]
        writes += [(path, json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8")),
                   (short_path, _dumps(short, crlf))]  # fmt: skip
        before = {}  # what each written file held (None: it wasn't there), to put back if a later write fails
        for target, _ in writes:
            try:
                before[target] = store.read_bytes(target)
            except FileNotFoundError:
                before[target] = None
        (sdir / "out").mkdir(parents=True, exist_ok=True)
        done = []
        try:
            for target, body in writes:
                jobstate.write_atomic(target, body)
                done.append(target)
        except BaseException:
            for target in reversed(done):
                old = before[target]
                if old is None:
                    target.unlink(missing_ok=True)
                else:
                    jobstate.write_atomic(target, old)
            raise
    return {"chosen": n, "files": list(postpack.OUT_FILES.values())}


def with_font(style: ThumbStyle, text: ThumbText, cache_dirs: list[Path] | None = None, dirs: list[Path] | None = None,
              system: str | None = None) -> tuple[ThumbStyle, list[str]]:  # fmt: skip
    """The style with the font file the thumbnail's text is drawn in and that font's line metrics, so the layout
    and the render use the same file: the style font (bsa.fonts.resolve: an installed fallback when it is missing),
    or, when it lacks a character of the text, the first OS font that has them all (bsa.fonts.cover_font). Without a
    style font, Blender's own font and its measured metrics. Also the notes on what is used instead.

    With a brand kit (_kit_font), the kit's card font, as the edit's cards use."""
    from bsa import fonts

    if style.kit is not None:
        style, notes = _kit_font(style, text, cache_dirs)
        return style, [*style.notes, *notes]
    if not style.font:
        return style, list(style.notes)
    path, _, note = fonts.resolve(style.font, dirs, system, cache_dirs)
    notes = [note] if note else []
    words = " ".join([*text.title, text.kicker or "", text.fact or "", style.handle_text or ""])
    info = fonts.font_info_or_none(path) if path else None
    missing = fonts.missing_chars(words, info) if info else []
    if missing:
        alt = fonts.cover_font(words, path, dirs, system)
        chars = ", ".join(f"'{c}'" for c in missing)
        if alt is not None:
            notes.append(f"{info['family']} has no {chars}, so the thumbnail uses {fonts.family_of(alt)}")
            path = alt
        else:
            notes.append(f"no installed font has {chars}; they show as boxes on the thumbnail")
    line_h, box_h = (fonts.line_metrics(path) if path else None) or (BLENDER_LINE_H, BLENDER_BOX_H)
    return replace(style, font=str(path) if path else None, line_h=line_h, box_h=box_h), [*style.notes, *notes]


def _kit_font(style: ThumbStyle, text: ThumbText, cache_dirs) -> tuple[ThumbStyle, list[str]]:
    """The style with the kit's card font (bsa.cards.card_font_and_note; `style.font` when it is already a font file,
    as a caller that resolved it sets) and its line metrics, and the notes: the font's own, each text a card draws in
    a cover font (bsa.cards.font_for, as the card itself picks it) and a logo the badge can't draw."""
    from bsa import cards
    from bsa.kit import logo_missing

    k = style.kit
    if style.font and Path(style.font).is_file():
        path, note = Path(style.font), None
    else:
        path, note = cards.card_font_and_note(k, cache_dirs)
    notes = [note] if note else []
    for words in (" ".join(text.title), text.kicker, text.fact, style.handle_text):
        got = cards.font_for(words, path)[1] if words else None
        if got:
            notes.append(got)
    if k.logo and cards.logo_badge(k, path, None) is None:
        notes.append(logo_missing(k.logo) or f"your logo file {k.logo.get('file')} can't be read as a PNG, so no "
                     "logo badge is drawn: set brand.logo.file again")  # fmt: skip
    line_h, box_h = cards.metrics(path)
    return replace(style, font=str(path) if path else None, line_h=line_h, box_h=box_h), notes


def heroes_from_post(p: dict) -> list[Hero]:
    """The heroes short.json post.thumbnail names (bsa.post.fields), in order."""
    th = p.get("thumbnail") if isinstance(p, dict) else None
    out = []
    for h in (th.get("heroes") if isinstance(th, dict) else None) or []:
        if isinstance(h, dict) and isinstance(h.get("shot"), str) and isinstance(h.get("frame"), int):
            crop = h.get("crop")
            out.append(Hero(h["shot"], h["frame"], tuple(crop) if isinstance(crop, list | tuple) else None, WHY_POST))
    return out


def source_frame(sdir, shot: str, frame: int) -> tuple[Path, str] | None:
    """(the frame's PNG, "final" or "preview"): the final render's when there is one, else the preview's."""
    for preset in ("final", "preview"):
        png = Path(sdir) / "frames" / preset / shot / f"{frame:04d}.png"
        if png.is_file() and png.stat().st_size > 0:
            return png, preset
    return None


def flag_key(sdir) -> str:
    """Which frame checks flag frames for the hero picker: the final's when it has been checked, else the preview's."""
    return "final" if (Path(sdir) / "review" / "frame-checks-final.json").is_file() else "preview"


# --------------------------------------------------------------------------- how the sequencer draws a Layout

_CENTRED = ("top band", "bottom band", "smaller title")


def strip_placement(src: Rect, dst: Rect, in_size: tuple[int, int], frame: tuple[int, int],
                    src_frame: tuple[int, int] = VERTICAL) -> dict:  # fmt: skip
    """The transform and crop of an image strip (fit "ORIGINAL": centred at its own size) that shows `src` (pixels of
    a `src_frame` sized frame, origin top-left) of an image `in_size` big on `dst` of a `frame` sized render.

    The sequencer maps an image pixel p (from the bottom-left) to pivot + offset + (frame - in_size) / 2 + scale *
    (p - pivot), the pivot being origin * in_size; the crop (whole pixels from each side) only hides, never moves. The
    crop keeps the whole source rect (it rounds outwards), so no seam shows at the edges."""
    import math

    iw, ih = in_size
    fw, fh = frame
    kx, ky = iw / src_frame[0], ih / src_frame[1]
    x0, x1 = src.x * kx, src.right * kx
    y0, y1 = ih - src.bottom * ky, ih - src.y * ky  # from the bottom
    sx, sy = dst.w / (x1 - x0), dst.h / (y1 - y0)
    dx0, dy0 = dst.x, fh - dst.bottom
    crop = {"left": max(math.floor(x0 + 1e-6), 0), "right": max(math.floor(iw - x1 + 1e-6), 0),
            "bottom": max(math.floor(y0 + 1e-6), 0), "top": max(math.floor(ih - y1 + 1e-6), 0)}  # fmt: skip
    return {"in_size": (iw, ih), "frame": (fw, fh), "origin": (0.0, 0.0), "scale_x": sx, "scale_y": sy,
            "offset_x": dx0 - sx * x0 - (fw - iw) / 2, "offset_y": dy0 - sy * y0 - (fh - ih) / 2,
            "crop": crop}  # fmt: skip


def card_strips(lay: Layout) -> list[dict]:
    """One text strip per card, for edit._Texts.add: text, size, location (normalised by the thumbnail's size,
    from the bottom-left) and anchor, box margin (the card's pad as a share of the thumbnail's width: Blender sizes
    a text strip's box margin by the image width, checked in Blender 5.2), wrap width in px, colours, and whether
    it has a box (only one that shows: alpha > 0). Cards of a band are centred on their box, the rest are left
    aligned in it; the text wraps where the layout wrapped it. A brand kit's cards (with a `spec`) are images, not
    text strips: none for them."""
    fw, fh = lay.size
    wrap = V_SAFE.w if tuple(lay.size) == VERTICAL else _L_WRAP
    out = []
    for c in lay.cards:
        if c.spec is not None:
            continue
        centred = lay.placement in _CENTRED and c.kind != "handle"
        x = c.rect.x + c.rect.w / 2 if centred else c.rect.x + c.pad
        y = c.rect.y + c.pad
        out.append({"kind": c.kind, "text": c.text, "size": c.size, "loc": (x / fw, 1.0 - y / fh),
                    "anchor": ("CENTER" if centred else "LEFT", "TOP"), "margin": c.pad / fw,
                    "wrap_px": wrap - 2 * c.pad, "colour": tuple(c.colour), "box": tuple(c.box),
                    "use_box": len(c.box) < 4 or c.box[3] > 0, "bold": c.kind != "fact"})  # fmt: skip
    return out
