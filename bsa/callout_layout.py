"""Where a callout's label and arrow are, frame by frame (no bpy): the label pops in at its cue, follows its part on
screen as a near label does (bsa.overlays.follow places it: inside the safe area, off the captions, the badge, the
other labels and where it can the heroes), fades out after it, and an arrow runs from the label's edge to the part.
Frames where the part is off screen or outside the safe area drop the arrow only; the label stays."""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from bsa.kitlayout import pointer_end
from bsa.layout import Rect, safe_rect
from bsa.overlays import follow

POP_FRAMES = 8  # the label scales 0.6 -> 1.05 -> 1.0 and fades in over this many frames from the cue
POP_FROM, POP_PEAK, POP_AT = 0.6, 1.05, 0.6  # start scale, overshoot, and the share of the pop that reaches it
FADE_FRAMES = 6  # and fades out over this many from f_off
CALLOUT_GAP_PX = 120  # the label's box sits this far above its part (a near label's is 16): room for the arrow
TIP_GAP_PX = 6  # the arrow's tip stops this far short of the part
HEAD_LEN, HEAD_W = 36, 32  # the arrow head, px
SHAFT_PX, CULPRIT_SHAFT_PX = 6, 10  # the shaft's thickness (the culprit's arrow is thicker)
OUTLINE_PX = 3  # the dark edge of an arrow in caption style "outline"
MAX_PER_SHOT = 10  # sequencer channels the edit gives one shot's callouts


@dataclass
class Frame:
    x: float  # label centre x
    bottom: float  # label text bottom y
    box: Rect
    alpha: float
    scale: float
    start: tuple[float, float] | None  # the arrow's tail (on the label's edge) and its tip, None: no arrow this frame
    tip: tuple[float, float] | None


def visible_range(cue: dict, span) -> tuple[int, int]:
    """The first and last frame the label is on screen: from f_on to the end of the fade after f_off, never past the
    shot's last frame (nothing leaks into the next shot)."""
    f_on = min(max(int(cue["f_on"]), span.f0), span.f1)
    return f_on, min(int(cue["f_off"]) + FADE_FRAMES - 1, span.f1)


def pop(frame: int, cue: dict) -> tuple[float, float]:
    """(alpha 0..1, scale) of the label at `frame`: the pop-in from f_on, the fade out from f_off."""
    t = (frame - int(cue["f_on"]) + 1) / POP_FRAMES
    alpha = min(max(t, 0.0), 1.0)
    if t >= 1.0:
        scale = 1.0
    elif t < POP_AT:
        scale = POP_FROM + (POP_PEAK - POP_FROM) * (max(t, 0.0) / POP_AT)
    else:
        scale = POP_PEAK - (POP_PEAK - 1.0) * ((t - POP_AT) / (1.0 - POP_AT))
    if frame >= int(cue["f_off"]):
        alpha *= max(0.0, 1.0 - (frame - int(cue["f_off"]) + 1) / (FADE_FRAMES + 1))  # never a drawn frame at 0
    return alpha, scale


def arrow_ends(box: Rect, point, gap: float = TIP_GAP_PX):
    """(tail on the label's edge, tip `gap` short of `point`), or None when there is no arrow: no point, the point
    outside the safe area, inside the label's box or too close for an arrow."""
    if point is None:
        return None
    s = safe_rect()
    if not (s.x <= point[0] <= s.right and s.y <= point[1] <= s.bottom):
        return None
    start = pointer_end(box, point)
    if start is None:
        return None
    dx, dy = point[0] - start[0], point[1] - start[1]
    dist = math.hypot(dx, dy)
    if dist <= gap + 1:
        return None
    return start, (point[0] - dx / dist * gap, point[1] - dy / dist * gap)


def plan(cue: dict, span, points: dict, caption_bands=(), fixed=(), placed: dict | None = None,
         heroes: dict | None = None, near: str | None = None) -> dict[int, Frame]:  # fmt: skip
    """{frame: Frame} for one callout over its visible frames. `points`: {"f0", "points"} of its part on screen (the
    recorded anchors); the rest as bsa.overlays.follow (`placed` is added to, so the next callout keeps off this)."""
    f0, f1 = visible_range(cue, span)
    frames = range(f0, f1 + 1)
    spots = follow(cue["text"], points, span.f0, span.f1, caption_bands, fixed, placed, heroes, near, frames,
                   gap=CALLOUT_GAP_PX)
    pts, p0 = points.get("points") or [], int(points.get("f0", span.f0))
    out = {}
    for frame in frames:
        x, bottom, box = spots[frame]
        i = frame - p0
        point = pts[i] if 0 <= i < len(pts) else None
        ends = arrow_ends(box, point)
        alpha, scale = pop(frame, cue)
        out[frame] = Frame(x, bottom, box, alpha, scale, *(ends or (None, None)))
    return out


# --------------------------------------------------------------------------- the arrow's pixels


def _rgb(colour) -> tuple[int, int, int]:
    return tuple(round(c * 255) for c in colour[:3])


def head_rgba(colour, outline=None, length: int = HEAD_LEN, width: int = HEAD_W) -> np.ndarray:
    """An (h, w, 4) uint8 straight-alpha arrow head pointing right (base at the left edge, tip at the right middle):
    `colour` filled, with a dark `outline` colour OUTLINE_PX round it when given (the image is the same size: the
    outline is drawn inside it)."""
    h, w = width, length
    y = np.abs(np.arange(h)[:, None] + 0.5 - h / 2)
    x = np.arange(w)[None, :] + 0.5
    half = (h / 2) * (1 - x / w)  # the triangle's half width at each column
    inner = half - (OUTLINE_PX if outline is not None else 0)
    out = np.zeros((h, w, 4), np.uint8)
    cover = np.clip(half - y + 0.5, 0, 1)
    out[..., :3] = _rgb(outline if outline is not None else colour)
    if outline is not None:
        fill = np.clip(inner - y + 0.5 - 1, 0, 1)
        for k, v in enumerate(_rgb(colour)):
            out[..., k] = np.where(fill > 0.5, v, out[..., k])
    out[..., 3] = (cover * 255).astype(np.uint8)
    return out


def shaft_rgba(colour, outline=None, thickness: int = SHAFT_PX, length: int = 64) -> np.ndarray:
    """An (h, length, 4) uint8 straight-alpha bar, `thickness` tall (the arrow's shaft, stretched along its length by
    the edit); with `outline`, a dark edge OUTLINE_PX above and below it."""
    o = OUTLINE_PX if outline is not None else 0
    out = np.zeros((thickness + 2 * o, length, 4), np.uint8)
    if o:
        out[..., :3], out[..., 3] = _rgb(outline), 255
    out[o : o + thickness, :, :3] = _rgb(colour)
    out[o : o + thickness, :, 3] = 255
    return out
