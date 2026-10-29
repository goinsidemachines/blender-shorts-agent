"""Safe-area geometry for 1080x1920 shorts. Pixels, origin at the top-left."""

from __future__ import annotations

from dataclasses import dataclass

W, H = 1080, 1920
# Pixels kept clear of the Shorts, Reels and TikTok buttons and captions.
SAFE = {"left": 60, "right": 140, "top": 220, "bottom": 420}
CAPTION_LIFT_PX = 140  # caption bottom edge sits this far above the safe bottom


@dataclass(frozen=True)
class Rect:
    x: float
    y: float
    w: float
    h: float

    @property
    def right(self) -> float:
        return self.x + self.w

    @property
    def bottom(self) -> float:
        return self.y + self.h


def safe_rect() -> Rect:
    return Rect(SAFE["left"], SAFE["top"], W - SAFE["left"] - SAFE["right"], H - SAFE["top"] - SAFE["bottom"])


def inside_safe(r: Rect) -> bool:
    s = safe_rect()
    return r.x >= s.x and r.y >= s.y and r.right <= s.right and r.bottom <= s.bottom


def caption_rect(n_lines: int, size_px: int = 64, line_h: float = 1.25) -> Rect:
    """Caption block: full safe width, centred, bottom edge 140 px above the safe bottom."""
    s = safe_rect()
    h = n_lines * size_px * line_h
    return Rect(s.x, s.bottom - CAPTION_LIFT_PX - h, s.w, h)


CAPTION_BOX_MARGIN = 0.018  # the caption box's margin around its text, as a fraction of H (edit.CAPTION_MARGIN)


def caption_band(n_lines: int = 2, size_px: int = 64, line_h: float = 1.25) -> Rect:
    """Where the captions' boxes sit while a caption shows (a two-line card by default): rows about 1165-1395."""
    text = caption_rect(n_lines, size_px, line_h)
    m = CAPTION_BOX_MARGIN * H
    return Rect(text.x, text.y - m, text.w, text.h + 2 * m)


_DEFAULT = object()


def frame_area(safe: Rect | None = None, avoid=_DEFAULT, margin: float = 24) -> Rect:
    """The part of the frame to fit heroes in: the safe area (`safe`, default safe_rect()) without the band `avoid`
    (default: the caption band; None for none): a band in the lower half cuts the area off above it, one in the
    upper half below it; then `margin` px inside on every side."""
    s = safe or safe_rect()
    band = caption_band() if avoid is _DEFAULT else avoid
    top, bottom = s.y, s.bottom
    if band is not None:
        if band.y + band.h / 2 >= s.y + s.h / 2:
            bottom = min(bottom, band.y)
        else:
            top = max(top, band.bottom)
    return Rect(s.x + margin, top + margin, s.w - 2 * margin, bottom - top - 2 * margin)


DOWN_COST = 3.0  # a label moved down covers its own object: each pixel down counts as three sideways or up


def place_label(
    anchor: tuple[float, float], size: tuple[float, float], margin: float, gap: float, avoid: list[Rect] = (),
    prefer: list[Rect] = (),
) -> tuple[float, float]:  # fmt: skip
    """(centre x, text bottom y) for a label of text size (w, h) with a box `margin` around it, placed `gap` px
    above `anchor` (an object's top on screen), clamped inside the safe area and moved off every `avoid` rect: the
    caption boxes on screen at the time, the shot's badge and the labels already placed in that frame. `prefer`:
    rects to keep clear when a spot clear of them is found too (the screen boxes of the shot's other heroes): a spot
    free of both wins; else one free of `avoid` that covers the least of them.

    The label tries spots just above, below, left and right of whatever it overlaps (a few steps deep, so it can
    clear a badge and a neighbouring label together) and takes the free spot nearest its wanted place; moving down
    costs DOWN_COST per pixel because it covers the object. When no spot is free it takes the least overlap."""
    s = safe_rect()
    w, h = size

    def clamp(x: float, bottom: float) -> tuple[float, float]:
        x = min(max(x, s.x + w / 2 + margin + 1), s.right - w / 2 - margin - 1)
        return x, min(max(bottom, s.y + h + margin + 1), s.bottom - margin - 1)

    def box(x: float, bottom: float) -> Rect:
        return Rect(x - w / 2 - margin, bottom - h - margin, w + 2 * margin, h + 2 * margin)

    every = [*avoid, *prefer]

    def hits(spot, rects=every):
        return [r for r in rects if overlap_area(box(*spot), r) > 0]

    def soft(spot):
        return sum(overlap_area(box(*spot), r) for r in prefer)

    want = clamp(anchor[0], anchor[1] - gap - margin)

    def cost(spot):
        dy = spot[1] - want[1]
        return abs(spot[0] - want[0]) + (DOWN_COST * dy if dy > 0 else -dy)

    seen, frontier, free = {want}, [want], []
    for _ in range(4):
        nxt = []
        for spot in frontier:
            over = hits(spot)
            if not over:
                free.append(spot)
                continue
            x, bottom = spot
            for r in over:
                for c in ((x, r.y - margin - 1), (x, r.bottom + h + margin + 1),
                          (r.x - w / 2 - margin - 1, bottom), (r.right + w / 2 + margin + 1, bottom)):  # fmt: skip
                    c = clamp(*c)
                    if c not in seen:
                        seen.add(c)
                        nxt.append(c)
        frontier = nxt
    free += [spot for spot in frontier if not hits(spot)]
    if free:
        return min(free, key=cost)
    hard_free = [spot for spot in seen if not hits(spot, avoid)]
    if hard_free:
        return min(hard_free, key=lambda spot: (soft(spot), cost(spot)))
    return min(seen, key=lambda spot: (sum(overlap_area(box(*spot), r) for r in avoid), soft(spot), cost(spot)))


def overlap_area(a: Rect, b: Rect) -> float:
    w = min(a.right, b.right) - max(a.x, b.x)
    h = min(a.bottom, b.bottom) - max(a.y, b.y)
    return w * h if w > 0 and h > 0 else 0.0


END_BANDS = ("upper third", "caption zone", "middle", "lower")


def end_card_band(block: Rect, heroes: list[Rect], caption_top: float, gap: float = 16,
                  others: list[Rect] = ()) -> tuple[str, float, float]:  # fmt: skip
    """(band, dy, overlap) for the end card: `block` is the card's box placed in the upper third; it moves by dy to
    the first band that no hero box and no box in `others` (the other meshes on screen: rails, a stand) overlaps,
    else the first band no hero overlaps that covers the least of `others`, else the band with the least hero
    overlap (summed over `heroes`, every hero box on every end-card frame; `overlap` is that hero overlap). Bands in
    order: the upper third; the caption zone (bottom at the safe area's bottom: no caption shows during the end
    card, which starts after the last caption); the middle; low, just above `caption_top`."""
    s = safe_rect()
    dys = {
        "upper third": 0.0,
        "caption zone": (s.bottom - 1) - block.bottom,  # no caption shows during the hold: down to the safe bottom
        "middle": (s.y + s.h / 2) - (block.y + block.h / 2),
        "lower": (caption_top - gap) - block.bottom,
    }
    scores = []
    for name in END_BANDS:
        moved = Rect(block.x, block.y + dys[name], block.w, block.h)
        hero = sum(overlap_area(moved, h) for h in heroes)
        other = sum(overlap_area(moved, o) for o in others)
        if hero == 0 and other == 0:
            return name, dys[name], 0.0
        scores.append((hero, other, END_BANDS.index(name), name))
    hero, _, _, name = min(scores)
    return name, dys[name], hero
