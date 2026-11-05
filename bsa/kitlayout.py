"""Where a brand kit's cards sit, frame by frame: the caption bars, the logo badge, the category tag, the corner stack
(badge, step, stacked labels and measures), the title card and value bars, near labels with their pointer lines and
dots, and the progress bar. Pure (no bpy): the edit (bsa.blender) places image strips from a KitOverlay, the checks
measure the same boxes (shot_rects) and framing keeps the subject under the top cards (top_band).

Today's anchors are kept: a caption bar's box ends where today's caption box does (its text bottom + the kit's pad),
the corner stack starts where today's badge does and near cards follow their object with overlays.follow. Every rect
is the card PNG's full rect in pixels of the 1080x1920 frame, origin top-left."""

from __future__ import annotations

import math
from dataclasses import dataclass, is_dataclass, replace
from pathlib import Path

from bsa import cards, overlays
from bsa.cards import CardSpec
from bsa.kit import logo_missing
from bsa.layout import CAPTION_LIFT_PX, SAFE, H, Rect, W, caption_band, overlap_area, safe_rect
from bsa.overlays import SMALL_MARGIN, STACK_GAP_PX

# Sequencer channels (a higher one draws over a lower one). "stacked", "pointer", "near" and "dot" add the label's
# index in shot.labels. Pointer lines (3-8) sit under every static card, so a line that crosses one passes behind it;
# the progress bar takes 9 (track), 10 (fill) and 11 (outline). Near cards and their dots draw over everything. The
# end card keeps 30 and up (today's text strips).
CH = {"pointer": 3, "progress": 9, "title": 12, "bars": 13, "caption": 14, "logo": 20, "category": 21, "badge": 22,
      "step": 23, "stacked": 24, "near": 50, "dot": 60}  # fmt: skip
# Labels per shot: stacked ones take 24 + index, which must stay under the end card's 30; pointers 3-8 stay under the
# progress bar's 9, near cards 50-55 and dots 60-65 then never meet either.
MAX_LABELS = 6


class LayoutError(ValueError):
    """A brand kit can't lay out a shot's cards; kit.errors names it before any edit runs."""


class TooManyLabels(LayoutError):
    """A shot has more labels than a brand kit draws (MAX_LABELS)."""


class CardsTooTall(LayoutError):
    """A shot's static top cards would reach the caption band (or the progress bar) even with its stacked labels moved
    to the right column."""


TITLE_GAP_PX = 24  # between the top cards and the title card, and between the title card and the bars
PROGRESS_DROP_PX = 36  # the progress bar's top under today's caption bottom


@dataclass(frozen=True)
class Placed:
    """A card on screen from frame f0 to f1: at `at` (static) or at `track[frame]` (a near card that follows its
    object). `text` is what it says (for the checks), `near` the object a near card belongs to."""

    spec: CardSpec
    f0: int
    f1: int
    channel: int
    at: Rect | None = None
    track: dict[int, Rect] | None = None
    text: str = ""
    near: str | None = None


@dataclass(frozen=True)
class Pointer:
    """A near card's pointer line (`spec`, length0 = spec.w) and the dot at its anchor end (channel CH["dot"] + the
    label's index): per frame ((x0, y0) on the card's edge, (x1, y1) the anchor), or None when the anchor is under the
    card."""

    f0: int
    f1: int
    channel: int
    spec: CardSpec
    dot: CardSpec
    ends: dict[int, tuple | None]


@dataclass(frozen=True)
class Progress:
    """The progress bar: the track at `at`, the fill at (at.x + outline_px, at.y + outline_px) showing
    round(fill.w * (f - f0 + 1) / (f1 - f0 + 1)) px at frame f."""

    track: CardSpec
    fill: CardSpec
    at: Rect
    f0: int
    f1: int
    channel: int


@dataclass
class KitOverlay:
    cards: list[Placed]
    pointers: list[Pointer]
    progress: Progress | None
    notes: list[str]


# --------------------------------------------------------------------------- helpers


def _edge() -> float:
    """Today's corner inset: SMALL_MARGIN * H, +1 px to keep float noise off the safe edge (overlays.corner_stack)."""
    return SMALL_MARGIN * H + 1


def _x_in_safe(x: float, w: float) -> float:
    s = safe_rect()
    return max(s.x, min(x, s.right - w))


def _note(spec: CardSpec, font, notes: list[str]) -> None:
    """The cover font note of a card drawn in another font than `font` (the whole card's text, as it was sized)."""
    if not font or not spec.font or spec.font == str(Path(font)):
        return
    note = cards.font_for(" ".join(r.text for r in spec.runs), font)[1]
    if note and note not in notes:
        notes.append(note)


def _logo(kit, font) -> tuple[CardSpec | None, str | None]:
    """(the logo badge or None, the note when the kit has a logo that can't be drawn)."""
    if not kit.logo:
        return None, None
    spec = cards.logo_badge(kit, font, kit.logo.get("name"))
    if spec is not None:
        return spec, None
    return None, logo_missing(kit.logo) or (f"your logo file {kit.logo.get('file')} can't be read as a PNG, so no "
                                            "logo badge is drawn: set brand.logo.file again")  # fmt: skip


def caption_lines(short, tl, kit, font) -> int:
    """The most lines any caption of the short wraps to in the kit's caption size and `font`: 1 or 2 (a longer one
    still takes the two-line band, as today); 0 when no caption is drawn (the kit or the short's own
    style.caption.draw turns them off); 2 when unknown (no short, or one without caption text). It is the max across
    the whole short (conservative: one two-line caption gives every shot the two-line band). The texts are the
    short's narration lines cut into cards (bsa.timeline.chunk_caption) plus, with a timeline `tl`, the cards the
    edit really draws (build_timeline merges the pieces of a near-silent take into one card that can wrap longer).
    Validate has no timeline, so it measures the chunked texts only and can miss such a merged card."""
    if not kit.caption.get("draw", True):
        return 0
    if short is None:
        return 2
    if not overlays.captions_drawn(short):
        return 0
    from bsa.timeline import chunk_caption

    texts = [t for ln in getattr(short, "lines", None) or [] for t in chunk_caption(ln.text)]
    if tl is not None:
        texts += [c.text for c in tl.cards]
    if not texts:
        return 2
    width = safe_rect().w
    return max(1, min(2, max(len(cards.caption(kit, t, font, width).runs) for t in texts)))


def bottom_limit(kit, lines: int = 2) -> float:
    """The lowest row a static top card may reach: TITLE_GAP_PX above the caption band (`lines` caption lines in the
    kit's size; none when 0 or the kit draws no captions) and the progress bar, inside the safe area."""
    s = safe_rect()
    limits = [s.bottom]
    if lines > 0 and kit.caption.get("draw", True):
        limits.append(caption_band(lines, int(kit.caption["size"])).y - TITLE_GAP_PX)
    if kit.progress:
        limits.append(H - SAFE["bottom"] - CAPTION_LIFT_PX + PROGRESS_DROP_PX - TITLE_GAP_PX)
    return min(limits)


def _static(shot, kit, font, logo: CardSpec | None, notes: list[str], strict: bool = True, lines: int = 2,
            ) -> list[tuple[str, str, CardSpec, Rect, int]]:  # fmt: skip
    """The shot's static top cards as (kind, text, spec, rect, channel): the logo badge, the category tag, the corner
    stack (badge, step, stacked labels and measures), the title card and the value bars. `lines`: the caption lines
    the limit leaves room for (caption_lines). When they would reach bottom_limit, the layout gives way in this order
    (_variants): the stacked labels move to the right column (under the category tag); the title card shrinks to
    cards.TITLE_MIN; the bars tighten (half the row gap, bars 3/4 as thick); both; still over: CardsTooTall naming
    what to drop (`strict`), else the most compact layout (top_band frames under it)."""
    labels = getattr(shot, "labels", None) or []
    if len(labels) > MAX_LABELS:
        raise TooManyLabels(f"shot {shot.id} has {len(labels)} labels: a brand kit draws at most {MAX_LABELS} per shot "
                            "(more would share sequencer channels with the pointers and the end card): move some to "
                            "another shot")  # fmt: skip
    limit = bottom_limit(kit, lines)
    out = []
    for shrink, tight, right in _variants(shot):
        out = _lay(shot, kit, font, logo, right, shrink, tight)
        if _bottom(out) <= limit:
            break
    else:
        if strict and shot is not None:
            raise CardsTooTall(_too_tall(shot, kit, font, logo, _bottom(out) - limit, limit))
    for _, _, spec, _, _ in out:
        _note(spec, font, notes)
    return out


def _bottom(out) -> float:
    return max((r.bottom for *_, r, _ in out), default=0.0)


def _stacked_labels(shot) -> list:
    from bsa.short import label_near

    return [lb for lb in getattr(shot, "labels", None) or [] if not label_near(lb)]


def _variants(shot) -> list[tuple[bool, bool, bool]]:
    """_static's layouts, gentlest first, as (title at TITLE_MIN, tight bars, stacked labels right): each step of the
    ladder with the labels on the left, then on the right (only when there are stacked labels); a step that changes
    nothing for this shot (no title card, no bars) is left out."""
    from bsa.short import bar_items, bar_verdict, title_lines

    has_title = shot is not None and bool(title_lines(shot)[0])
    has_bars = shot is not None and bool(bar_items(shot) or bar_verdict(shot))
    steps = [(False, False)]
    if has_title:
        steps.append((True, False))
    if has_bars:
        steps.append((False, True))
    if has_title and has_bars:
        steps.append((True, True))
    sides = (False, True) if shot is not None and _stacked_labels(shot) else (False,)
    return [(shrink, tight, right) for shrink, tight in steps for right in sides]


def _fits(shot, kit, font, logo, limit: float) -> bool:
    return any(_bottom(_lay(shot, kit, font, logo, right, shrink, tight)) <= limit
               for shrink, tight, right in _variants(shot))  # fmt: skip


def _names(names: list[str], word: str) -> str:
    """"a", "a or b", "a, b or c" (`word`: "and" or "or")."""
    return names[0] if len(names) == 1 else f"{', '.join(names[:-1])} {word} {names[-1]}"


def _too_tall(shot, kit, font, logo, over: float, limit: float) -> str:
    """The CardsTooTall message: how far the cards reach (squeezed as far as they go) and the parts whose removal
    alone makes them fit."""
    from bsa.short import label_near

    near_only = [lb for lb in getattr(shot, "labels", None) or [] if label_near(lb)]
    parts = [("the value bars", "bars", None), ("the title card", "title_card", None),
             ("the stacked labels", "labels", near_only), ("the step tag", "step", None), ("the badge", "badge", None),
             ("the category tag", "category", None)]  # fmt: skip
    present = [(name, key, empty) for name, key, empty in parts
               if (_stacked_labels(shot) if key == "labels" else getattr(shot, key, None))]  # fmt: skip
    one = [name for name, key, empty in present
           if is_dataclass(shot) and _fits(replace(shot, **{key: empty}), kit, font, logo, limit)]  # fmt: skip
    fix = (f"drop {one[0]}" if len(one) == 1 else f"drop {_names(one, 'or')}" if one
           else f"drop some of {_names([n for n, _, _ in present], 'and')}")  # fmt: skip
    return (f"shot {shot.id}'s top cards reach {math.ceil(over)} px into the captions (they end at row "
            f"{math.ceil(limit + over)}, the most is {math.floor(limit)}): {fix}, or move some of them to another "
            "shot")  # fmt: skip


def _lay(shot, kit, font, logo: CardSpec | None, labels_right: bool, small_title: bool = False, tight: bool = False,
         ) -> list[tuple[str, str, CardSpec, Rect, int]]:  # fmt: skip
    """_static's layout: stacked labels in the left corner stack, or in the right column with `labels_right`; the
    title card at cards.TITLE_MIN with `small_title`; the value bars tight with `tight`."""
    from bsa.short import (
        bar_items,
        bar_verdict,
        category_text,
        label_kind,
        label_near,
        label_text,
        step_text,
        title_lines,
    )

    s = safe_rect()
    left, right, y0 = s.x + _edge(), s.right - _edge(), s.y + _edge()
    out = []

    def put(kind, text, spec, x, y, channel) -> Rect:
        rect = Rect(_x_in_safe(x, spec.w), y, spec.w, spec.h)
        out.append((kind, text, spec, rect, channel))
        return rect

    stack_y = col_y = y0  # the left stack and the right column
    if logo is not None:
        top_right = (kit.logo or {}).get("place", "top-left") == "top-right"
        r = put("logo", str((kit.logo or {}).get("name") or ""), logo, right - logo.w if top_right else left, y0,
                CH["logo"])  # fmt: skip
        if top_right:
            col_y = r.bottom + STACK_GAP_PX
        else:
            stack_y = r.bottom + STACK_GAP_PX

    def stacked(kind, text, channel) -> None:
        nonlocal stack_y
        stack_y = put(kind, text, cards.pill(kit, kind, text, font), left, stack_y, channel).bottom + STACK_GAP_PX

    def column(kind, text, channel) -> None:  # right-aligned, moved under any card it would sit on
        nonlocal col_y
        spec = cards.pill(kit, kind, text, font)
        x = _x_in_safe(right - spec.w, spec.w)
        while hit := [r for *_, r, _ in out if overlap_area(Rect(x, col_y, spec.w, spec.h), r) > 0]:
            col_y = max(r.bottom for r in hit) + STACK_GAP_PX
        col_y = put(kind, text, spec, x, col_y, channel).bottom + STACK_GAP_PX

    labels = getattr(shot, "labels", None) or []
    if getattr(shot, "badge", None):
        stacked("badge", shot.badge.upper(), CH["badge"])
    if step_text(shot):
        stacked("step", step_text(shot), CH["step"])
    if not labels_right:
        for i, label in enumerate(labels):
            if not label_near(label):
                stacked(label_kind(label), label_text(label), CH["stacked"] + i)

    cat = getattr(shot, "category", None)
    if isinstance(cat, str) and cat.strip():
        column("category", category_text(cat), CH["category"])
    if labels_right:
        for i, label in enumerate(labels):
            if not label_near(label):
                column(label_kind(label), label_text(label), CH["stacked"] + i)

    y = _bottom(out) + TITLE_GAP_PX if out else y0
    lines, highlight = title_lines(shot)
    if lines:
        spec = cards.title(kit, list(lines), highlight, font, s.w, cards.TITLE_MIN if small_title else None)
        y = put("title", "\n".join(lines), spec, s.x + (s.w - spec.w) / 2, y, CH["title"]).bottom + TITLE_GAP_PX
    if bar_items(shot) or bar_verdict(shot):
        put("bars", bar_verdict(shot), cards.bars(kit, shot.bars, font, s.w, tight), s.x, y, CH["bars"])
    return out


def _captions(short, tl, kit, font, notes: list[str], frames=None) -> tuple[list, list[Placed]]:
    """(overlays.captions rows, the caption bars): none when the short or the kit turns captions off. With `frames`
    only the bars showing at one of them."""
    if not kit.caption.get("draw", True):
        return [], []
    rows = overlays.captions(short, tl)  # [] when the short's own style turns them off
    if not rows:
        return [], []
    s = safe_rect()
    pad = kit.caption["pad_px"]
    out = []
    for card, (f0, f1, _, _, bottom) in zip(tl.cards, rows, strict=True):
        if frames is not None and not any(f0 <= f <= f1 for f in frames):
            continue
        spec = cards.caption(kit, card.text, font, s.w)
        _note(spec, font, notes)
        out.append(Placed(spec, f0, f1, CH["caption"], at=Rect(s.x, bottom + pad - spec.box_h, spec.w, spec.h),
                          text=card.text))  # fmt: skip
    return rows, out


def _anchor_points(points: dict, frames, f0: int) -> dict[int, tuple[float, float]]:
    """The anchor point per frame as overlays.follow reads it (a frame without one keeps the last)."""
    s = safe_rect()
    pts = list(points.get("points") or [])
    p0 = int(points.get("f0", f0))
    last = next((p for p in pts if p is not None), None) or [s.x + s.w / 2, s.y]
    out = {}
    for frame in frames:
        i = frame - p0
        p = pts[i] if 0 <= i < len(pts) and pts[i] is not None else last
        last = p
        out[frame] = (float(p[0]), float(p[1]))
    return out


def _near(shot, span, kit, font, anchors: dict | None, rows, fixed: list[Rect], heroes, frames, strict: bool,
          notes: list[str]) -> list[tuple[Placed, Pointer]]:  # fmt: skip
    """The shot's near labels and near measures, each following its object (overlays.follow with the card's size),
    with its pointer line. A near label without anchors: ValueError when `strict`, else left out."""
    from bsa.short import label_anchor, label_kind, label_near, label_text

    placed: dict = {}
    out = []
    for i, label in enumerate(getattr(shot, "labels", None) or []):
        near = label_near(label)
        if not near:
            continue
        text, kind = label_text(label), label_kind(label)
        points = (anchors or {}).get(label_anchor(label))
        if points is None:
            if strict:
                raise ValueError(f"no anchors for label {text!r} near {near!r} in shot {shot.id}: run BLENDER "
                                 "anchors SHORT")  # fmt: skip
            continue
        dot = kit.colour(points.get("category")) if kind == "label" else None
        spec = cards.pill(kit, kind, text, font, dot=dot)
        _note(spec, font, notes)
        spots = overlays.follow(text, points, span.f0, span.f1, rows, fixed, placed, heroes, near, frames,
                                size=(spec.w, spec.h))  # fmt: skip
        track = {f: Rect(x - spec.w / 2, bottom - spec.h, spec.w, spec.h) for f, (x, bottom, _) in spots.items()}
        anchor = _anchor_points(points, spots, span.f0)
        ends = {}
        for f, rect in track.items():
            start = pointer_end(rect, anchor[f])
            ends[f] = None if start is None else (start, anchor[f])
        length0 = max([math.dist(*e) for e in ends.values() if e is not None] or [1.0])
        f0, f1 = (min(track), max(track)) if track else (span.f0, span.f1)
        out.append((Placed(spec, f0, f1, CH["near"] + i, track=track, text=text, near=near),
                    Pointer(f0, f1, CH["pointer"] + i, cards.pointer(kit, math.ceil(length0)), cards.dot(kit), ends)))
    return out


# --------------------------------------------------------------------------- the layout


def build(short, tl, kit, font, anchors: dict | None = None, label_heroes: dict | None = None) -> KitOverlay:
    """Every kit card of the short, frame by frame. `anchors`: {shot: {object: {"f0", "points", "category"?}}} (from
    source/anchors.json) for near labels; `label_heroes`: {shot: {"f0", "boxes"}} (near cards keep off them).
    ValueError for a near label without anchors. A logo that can't be drawn: no badge, a note in `notes`."""
    notes: list[str] = []
    rows, caps = _captions(short, tl, kit, font, notes)
    logo, note = _logo(kit, font)
    if note:
        notes.append(note)
    ov = KitOverlay(cards=list(caps), pointers=[], progress=None, notes=notes)
    spans = {s.id: s for s in tl.shots}
    logo_at = None
    nlines = caption_lines(short, tl, kit, font)
    for shot in short.shots:
        span = spans.get(shot.id)
        if span is None:
            continue
        static = _static(shot, kit, font, logo, notes, lines=nlines)
        for kind, text, spec, rect, channel in static:
            if kind == "logo":
                logo_at = rect
            else:
                ov.cards.append(Placed(spec, span.f0, span.f1, channel, at=rect, text=text))
        heroes = (label_heroes or {}).get(shot.id)
        for card, ptr in _near(shot, span, kit, font, (anchors or {}).get(shot.id), rows, [r for *_, r, _ in static],
                               heroes, None, True, notes):  # fmt: skip
            ov.cards.append(card)
            ov.pointers.append(ptr)
    if logo is not None and tl.n_frames >= 1:
        logo_at = logo_at or next(r for k, _, _, r, _ in _static(None, kit, font, logo, []) if k == "logo")
        ov.cards.append(Placed(logo, 1, tl.n_frames, CH["logo"], at=logo_at, text=str(kit.logo.get("name") or "")))
    if kit.progress and tl.n_frames >= 1:
        s = safe_rect()
        track, fill = cards.progress_track(kit, s.w), cards.progress_fill(kit, s.w)
        at = Rect(s.x, H - SAFE["bottom"] - CAPTION_LIFT_PX + PROGRESS_DROP_PX, track.w, track.h)
        ov.progress = Progress(track, fill, at, 1, tl.n_frames, CH["progress"])
    return ov


def top_band(short, shot, kit, font) -> Rect | None:
    """The rows the shot's static top cards take (logo, category, corner stack, title card, bars), full safe width
    from the safe top: pass it as `top=` to camera.frame_objects. None when the shot has none."""
    static = _static(shot, kit, font, _logo(kit, font)[0], [], strict=False,
                     lines=caption_lines(short, None, kit, font))
    if not static:
        return None
    s = safe_rect()
    return Rect(s.x, s.y, s.w, max(r.bottom for *_, r, _ in static) - s.y)


def shot_rects(short, tl, shot, span, kit, font, anchors: dict | None = None, heroes: dict | None = None,
               frames=None) -> dict[int, list[tuple[str, str, str | None, Rect]]]:  # fmt: skip
    """overlays.shot_boxes for a kit: {frame: [(kind, text, near object or None, rect)]} of every card on screen at
    each frame of `shot` (`frames`, default its span): "caption", "logo", "category", "badge", "step", "label" and
    "measure" (stacked), "title", "bars" and "near" (near labels and measures). `anchors`: {object: {"f0", "points"}}
    for this shot (a near label without them is left out); `heroes`: this shot's {"f0", "boxes"}."""
    frames = list(range(span.f0, span.f1 + 1) if frames is None else frames)
    notes: list[str] = []
    rows, caps = _captions(short, tl, kit, font, notes, frames)
    static = _static(shot, kit, font, _logo(kit, font)[0], notes, lines=caption_lines(short, tl, kit, font))
    out: dict[int, list] = {f: [] for f in frames}
    for f in frames:
        out[f] += [("caption", p.text, None, p.at) for p in caps if p.f0 <= f <= p.f1]
        out[f] += [(kind, text, None, rect) for kind, text, _, rect, _ in static]
    for card, _ in _near(shot, span, kit, font, anchors, rows, [r for *_, r, _ in static], heroes, frames, False,
                         notes):  # fmt: skip
        for f, rect in card.track.items():
            out[f].append(("near", card.text, card.near, rect))
    return out


# --------------------------------------------------------------------------- pointer maths


def pointer_end(box: Rect, point) -> tuple[float, float] | None:
    """Where a pointer line leaves `box` for `point`: None when the point is inside the box; else on its edge: x the
    left edge (point left of the box), the right edge (right of it) or the centre; y the middle when the point is
    beside the box, else the bottom (point below) or the top (above)."""
    px, py = point
    if box.x <= px <= box.right and box.y <= py <= box.bottom:
        return None
    x = box.x if px < box.x else box.right if px > box.right else box.x + box.w / 2
    y = box.y + box.h / 2 if box.y <= py <= box.bottom else box.bottom if py > box.bottom else box.y
    return (x, y)


def pointer_transform(start, end, length0: float, thickness: float, frame=(W, H)) -> dict:
    """The transform of a `length0` x `thickness` px image strip (fit "ORIGINAL", transform.origin (0.0, 0.5)) that
    puts its left-middle on `start` and its right end on `end` (px from the top-left): offset_x, offset_y, scale_x
    and rotation (radians, counter-clockwise, y up). The sequencer maps an image pixel p (from the bottom-left) to
    pivot + offset + (frame - in_size) / 2 + R(scale * (p - pivot)), the pivot being origin * in_size."""
    fw, fh = frame
    sx, sy = float(start[0]), fh - float(start[1])
    ex, ey = float(end[0]), fh - float(end[1])
    pivot_y = thickness / 2
    return {"origin": (0.0, 0.5), "offset_x": sx - (fw - length0) / 2, "offset_y": sy - pivot_y - (fh - thickness) / 2,
            "scale_x": math.hypot(ex - sx, ey - sy) / length0, "scale_y": 1.0,
            "rotation": math.atan2(ey - sy, ex - sx)}  # fmt: skip


# --------------------------------------------------------------------------- the kit preview


SAMPLE_BARS = {"items": [{"label": "Short arm", "value": 1}, {"label": "Long arm", "value": 3}],
               "verdict": "3x the lift"}  # fmt: skip


def sample(kit, font) -> KitOverlay:
    """One frame (frame 1) of a fixed made-up arrangement, to preview a kit: the logo (if set), a category tag (the
    kit's first category, else "Force"), a badge, a step tag, a title card, a part label pointing at (700, 400), a
    stacked measure, value bars, a caption and the progress bar at 40 %: its Progress runs f0 = 0 to f1 = 4, so at
    frame 1 the fill shows (1 - 0 + 1) / (4 - 0 + 1) = 2 / 5 of its width (no f0 >= 1 gives exactly 40 % at frame 1);
    every other card shows at frame 1."""
    from bsa.short import Short, Shot
    from bsa.timeline import Card, Span, Timeline

    category = next(iter(kit.categories), None) or "force"
    shot = Shot(id="sample", title="Sample", picture="", scene="", badge="Simplified", step=[2, 4], category=category,
                title_card={"lines": ["Lever lifts more"], "highlight": 0}, bars=SAMPLE_BARS,
                labels=[{"text": "Pivot", "near": "Pivot"}, {"text": "1.4 m", "kind": "measure"}])  # fmt: skip
    short = Short(slug="sample", title="Sample", length_s=1, shots=[shot])
    tl = Timeline(30, 1, [Span("sample", 1, 1)], [], [Card("Your captions look like this.", "sample", 1, 1)])
    anchors = {"sample": {"Pivot": {"f0": 1, "points": [[700, 400]], "category": category}}}
    ov = build(short, tl, kit, font, anchors)
    if ov.progress is not None:  # frame 1 of 0..4: 2 / 5 = 40 %
        ov.progress = replace(ov.progress, f0=0, f1=4)
    return ov
