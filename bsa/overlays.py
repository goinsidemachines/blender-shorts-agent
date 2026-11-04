"""Where the edit's text sits on screen, frame by frame: the caption boxes, each shot's badge and stacked labels in
the top-left corner, and near labels that follow their object. Pure (no bpy): the edit (bsa.blender.edit) places its
text strips from these, and the scene checks (bsa.blender.checks) compare the same boxes with the subject's box on
screen before anything renders. Pixels, origin at the top-left of the 1080x1920 frame."""

from __future__ import annotations

import math
import re

from bsa.layout import H, Rect, caption_rect, place_label, safe_rect

CAPTION_SIZE = 64
BADGE_SIZE = 30
LABEL_SIZE = 40
LINE_H = 1.25  # same line height as layout.caption_rect
CHAR_W = 0.56  # average glyph advance in em, for estimating wrapping
CAPTION_MARGIN = 0.018  # box margin as a fraction of the render height
SMALL_MARGIN = 0.010
STACK_GAP_PX = 12
NEAR_GAP_PX = 16  # a near label's box sits this far above its object's top
NEAR_WRAP_PX = 420  # near labels wrap narrower than the safe area
BOX_COLOR = (0.02, 0.025, 0.03, 0.55)  # the captions' dark see-through box
BADGE_COLOR = (1.0, 0.8, 0.35, 1.0)


def hex_colour(value, alpha: float = 1.0):
    """'#RRGGBB' or '#RRGGBBAA' as an RGBA tuple of 0..1 floats; None when it is not one. A colour the user gives
    as 6 digits is opaque (alpha 1, the default); 8 digits keep their own alpha."""
    text = str(value or "").strip().lstrip("#")
    if not re.fullmatch(r"[0-9a-fA-F]{6}([0-9a-fA-F]{2})?", text):
        return None
    rgba = [int(text[i : i + 2], 16) / 255 for i in range(0, len(text), 2)]
    return tuple(rgba) if len(rgba) == 4 else (*rgba, alpha)


def caption_colours(short, settings: dict) -> tuple[tuple, tuple]:
    """(text, box) colours of the captions: `brand.caption` `color` and `box` from settings (not for a short with its
    own look, "look": "fresh"), overridden by the short's own `style.caption`; white text on the dark see-through box
    (BOX_COLOR) when neither sets them. A `box` of 6 hex digits is opaque; 8 digits ("#14161A80") keep their alpha."""
    from bsa.settings import brand_for

    cap = {}
    for src in (brand_for(settings, short).get("caption"), (short.style or {}).get("caption")):
        if isinstance(src, dict):
            cap.update(src)
    text = hex_colour(cap.get("color") or cap.get("colour"), 1.0) or (1.0, 1.0, 1.0, 1.0)
    box = hex_colour(cap.get("box") or cap.get("box_color") or cap.get("box_colour"), 1.0) or BOX_COLOR
    return text, box


def est_lines(text: str, size_px: float, wrap_px: float) -> int:
    per_line = max(1, int(wrap_px / (size_px * CHAR_W)))
    return max(1, sum(max(1, math.ceil(len(part) / per_line)) for part in text.split("\n")))


def est_width(text: str, size_px: float, wrap_px: float) -> float:
    longest = max(len(part) for part in text.split("\n"))
    return min(wrap_px, longest * size_px * CHAR_W)


def text_rect(text: str, size: float, x: float, y: float, anchor: tuple[str, str], margin_px: float,
              wrap_px: float) -> Rect:  # fmt: skip
    """The estimated box (text plus its margin) of a text placed with its `anchor` ("LEFT"/"CENTER"/"RIGHT",
    "TOP"/"CENTER"/"BOTTOM") at pixel (x, y)."""
    n = est_lines(text, size, wrap_px)
    w, h = est_width(text, size, wrap_px), n * size * LINE_H
    left = {"LEFT": x, "CENTER": x - w / 2, "RIGHT": x - w}[anchor[0]]
    top = {"TOP": y, "CENTER": y - h / 2, "BOTTOM": y - h}[anchor[1]]
    return Rect(left - margin_px, top - margin_px, w + 2 * margin_px, h + 2 * margin_px)


def caption_size(short) -> int:
    return int(((getattr(short, "style", None) or {}).get("caption") or {}).get("size") or CAPTION_SIZE)


def captions_drawn(short) -> bool:
    """False when the short's own `style.caption.draw` is false: then no caption shows (the voice alone)."""
    draw = ((getattr(short, "style", None) or {}).get("caption") or {}).get("draw", True)
    return not (draw is False or (isinstance(draw, str) and draw.strip().lower() == "false"))


def captions(short, tl) -> list[tuple[int, int, Rect, Rect, float]]:
    """(f0, f1, band, box, text bottom y) per caption card: `band` is the full safe width the card may take (near
    labels keep off it), `box` the estimated box the text is drawn in (centred, as wide as its longest line).
    Empty when the short's `style.caption.draw` is false."""
    if not captions_drawn(short):
        return []
    safe = safe_rect()
    size = caption_size(short)
    wrap_px = safe.w - 2 * CAPTION_MARGIN * H
    cx = safe.x + safe.w / 2
    pad = CAPTION_MARGIN * H
    out = []
    for card in tl.cards:
        rect = caption_rect(est_lines(card.text, size, wrap_px), size, LINE_H)
        band = Rect(rect.x - pad, rect.y - pad, rect.w + 2 * pad, rect.h + 2 * pad)
        box = text_rect(card.text, size, cx, rect.bottom, ("CENTER", "BOTTOM"), pad, wrap_px)
        out.append((card.f0, card.f1, band, box, rect.bottom))
    return out


def corner_stack(shot) -> list[tuple[str, str, int, float, float]]:
    """The shot's badge and stacked (not near) labels, top-left of the safe area, one under the other:
    (kind "badge" or "label", text as drawn, its index in shot.labels (-1 for the badge), x, top y)."""
    safe = safe_rect()
    left = safe.x + SMALL_MARGIN * H + 1  # +1 px keeps float noise off the safe edge
    y = safe.y + SMALL_MARGIN * H + 1
    out = []
    if getattr(shot, "badge", None):
        out.append(("badge", shot.badge.upper(), -1, left, y))
        y += BADGE_SIZE * LINE_H + 2 * SMALL_MARGIN * H + STACK_GAP_PX
    from bsa.short import label_near, label_text

    for i, label in enumerate(getattr(shot, "labels", None) or []):
        if label_near(label):
            continue
        text = label_text(label)
        out.append(("label", text, i, left, y))
        n = est_lines(text, LABEL_SIZE, safe.w - 2 * SMALL_MARGIN * H)
        y += n * LABEL_SIZE * LINE_H + 2 * SMALL_MARGIN * H + STACK_GAP_PX
    return out


def corner_rect(kind: str, text: str, x: float, y: float) -> Rect:
    """The box of a badge or stacked label from corner_stack."""
    size = BADGE_SIZE if kind == "badge" else LABEL_SIZE
    return text_rect(text, size, x, y, ("LEFT", "TOP"), SMALL_MARGIN * H, safe_rect().w - 2 * SMALL_MARGIN * H)


def hero_rects(heroes: dict | None, frame: int, skip: str | None = None, anchor=None) -> list[Rect]:
    """The hero boxes on screen at `frame` from a shot's {"f0", "boxes": {name: [[x0, y0, x1, y1] or None, ...]}},
    leaving out `skip` and any box that holds the point `anchor`."""
    out = []
    if not isinstance(heroes, dict) or not isinstance(heroes.get("boxes"), dict):
        return out
    i = frame - int(heroes.get("f0", 1))
    for name, per_frame in heroes["boxes"].items():
        b = per_frame[i] if name != skip and isinstance(per_frame, list) and 0 <= i < len(per_frame) else None
        if not b or (anchor is not None and b[0] <= anchor[0] <= b[2] and b[1] <= anchor[1] <= b[3]):
            continue
        out.append(Rect(b[0], b[1], b[2] - b[0], b[3] - b[1]))
    return out


def near_label_size(text: str) -> tuple[float, float]:
    return est_width(text, LABEL_SIZE, NEAR_WRAP_PX), est_lines(text, LABEL_SIZE, NEAR_WRAP_PX) * LABEL_SIZE * LINE_H


def follow(text: str, points: dict, f0: int, f1: int, caption_bands, fixed=(), placed: dict | None = None,
           heroes: dict | None = None, near: str | None = None, frames=None,
           size: tuple[float, float] | None = None) -> dict[int, tuple[float, float, Rect]]:  # fmt: skip
    """{frame: (centre x, text bottom y, box)} of a near label at each frame of its shot (`frames`, default f0..f1):
    just above its object's top on screen (`points`: {"f0", "points": [[x, y] or None per frame]}), inside the safe
    area and never on a caption shown at that frame (`caption_bands`: (f0, f1, band) or captions() rows), the shot's
    badge and stacked labels (`fixed`) or the near labels already placed in that frame (`placed`: {frame: [Rect]},
    which this label's own box is added to). It keeps off the shot's heroes too where it can (`heroes`: the shot's
    {"f0", "boxes"}), except its own object `near` and a hero whose box holds the anchor (the assembly the part
    belongs to). Frames where the object is behind the camera keep the last position. `size`: the label's (w, h) in
    px instead of the estimate from its text (a kit card's size; its box is still SMALL_MARGIN * H around it)."""
    w, h = near_label_size(text) if size is None else size
    m = SMALL_MARGIN * H
    pts = list(points.get("points") or [])
    p0 = int(points.get("f0", f0))
    first = next((p for p in pts if p is not None), None)
    last = first or [safe_rect().x + safe_rect().w / 2, safe_rect().y]
    placed = {} if placed is None else placed
    out = {}
    for frame in (range(f0, f1 + 1) if frames is None else frames):
        i = frame - p0
        p = pts[i] if 0 <= i < len(pts) and pts[i] is not None else last
        last = p
        avoid = [c[2] for c in caption_bands if c[0] <= frame <= c[1]] + list(fixed) + placed.get(frame, [])
        prefer = hero_rects(heroes, frame, skip=near, anchor=p)
        x, bottom = place_label((p[0], p[1]), (w, h), m, NEAR_GAP_PX, avoid, prefer)
        box = Rect(x - w / 2 - m, bottom - h - m, w + 2 * m, h + 2 * m)
        placed.setdefault(frame, []).append(box)
        out[frame] = (x, bottom, box)
    return out


def shot_boxes(short, tl, shot, span, anchors: dict | None = None, heroes: dict | None = None,
               frames=None, kit=None, font=None) -> dict[int, list[tuple[str, str, str | None, Rect]]]:  # fmt: skip
    """{frame: [(kind, text, near object or None, box)]}: every text box the edit puts on screen at each frame of
    `shot` (`frames`, default all of its span): "caption", "badge", "label" (stacked) and "near" labels.
    `anchors`: {anchor key (bsa.short.label_anchor): {"f0", "points"}} for this shot's near labels (a near label without them is left out);
    `heroes`: this shot's {"f0", "boxes"} (near labels keep off them, as in the edit). With a brand `kit` (and its
    card `font`) the kit's cards instead: bsa.kitlayout.shot_rects."""
    if kit is not None:
        from bsa import kitlayout

        return kitlayout.shot_rects(short, tl, shot, span, kit, font, anchors, heroes, frames)
    from bsa.short import label_anchor, label_near, label_text

    frames = list(range(span.f0, span.f1 + 1) if frames is None else frames)
    caps = captions(short, tl)
    corner = [(kind, text, corner_rect(kind, text, x, y)) for kind, text, _, x, y in corner_stack(shot)]
    out: dict[int, list] = {f: [] for f in frames}
    for f in frames:
        out[f] += [("caption", tl.cards[i].text, None, c[3]) for i, c in enumerate(caps) if c[0] <= f <= c[1]]
        out[f] += [(kind, text, None, rect) for kind, text, rect in corner]
    placed: dict = {}
    for label in getattr(shot, "labels", None) or []:
        near = label_near(label)
        points = (anchors or {}).get(label_anchor(label)) if near else None
        if points is None:
            continue
        text = label_text(label)
        spots = follow(text, points, span.f0, span.f1, caps, [r for _, _, r in corner], placed, heroes, near, frames)
        for f, (_, _, box) in spots.items():
            out[f].append(("near", text, near, box))
    return out
