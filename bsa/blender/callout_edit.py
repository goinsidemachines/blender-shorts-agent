"""Callouts in the edit: a label plate that pops in at its cue frame, an arrow from it to the named part, both drawn
over everything else. Where they go is bsa.callout_layout; this draws them as sequencer strips (a text strip for the
plate, an image strip each for the arrow's shaft and head, offset, scale, rotation and alpha keyed every frame). The
same drawing serves a plain short and one with a brand kit."""

from __future__ import annotations

from pathlib import Path

from bsa.blender.cards import _image, _key, _write_rgba
from bsa.callout_layout import (
    CULPRIT_SHAFT_PX,
    HEAD_LEN,
    HEAD_W,
    MAX_PER_SHOT,
    OUTLINE_PX,
    SHAFT_PX,
    head_rgba,
    plan,
    shaft_rgba,
    visible_range,
)
from bsa.kitlayout import pointer_transform
from bsa.layout import H, W
from bsa.overlays import (
    BOX_COLOR,
    LABEL_SIZE,
    NEAR_WRAP_PX,
    SMALL_MARGIN,
    caption_highlight,
    caption_style,
    captions,
    hex_colour,
    shot_boxes,
)

CH_PLATE, CH_SHAFT, CH_HEAD = 70, 80, 90  # a shot's callouts take these channels + their index, over everything else
SHAFT_LEN = 64  # the shaft's image length (the edit stretches it)
DARK = (0.02, 0.025, 0.03, 1.0)


def _png(cache_dir: Path, name: str, pixels) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    png = cache_dir / name
    if not png.is_file() or png.stat().st_size == 0:
        _write_rgba(pixels, png)
    return png


def _arrow(seq, i: int, f0: int, f1: int, frames: dict, colour, outline, thick: int, cache_dir: Path) -> None:
    tag = "".join(f"{round(c * 255):02x}" for c in colour[:3]) + ("o" if outline else "")
    head_png = _png(cache_dir, f"callout_head_{tag}.png", head_rgba(colour, outline))
    shaft_png = _png(cache_dir, f"callout_shaft_{tag}_{thick}.png", shaft_rgba(colour, outline, thick, SHAFT_LEN))
    o = OUTLINE_PX if outline else 0
    shaft_h = thick + 2 * o
    zero = {"origin": (0.0, 0.5), "scale_x": 0.0, "scale_y": 1.0, "offset_x": 0.0, "offset_y": 0.0}
    shaft = _image(seq, "Callout_shaft", shaft_png, CH_SHAFT + i, f0, f1, zero)
    head = _image(seq, "Callout_head", head_png, CH_HEAD + i, f0, f1, zero)
    for f in range(f0, f1 + 1):
        fr = frames[f]
        for s in (shaft, head):
            s.blend_alpha = fr.alpha
            s.keyframe_insert("blend_alpha", frame=f)
        if fr.start is None:  # no arrow this frame (the part is off screen): the label stays
            for s in (shaft, head):
                _key(s.transform, {"scale_x": 0.0}, f)
            continue
        (sx, sy), (tx, ty) = fr.start, fr.tip
        dist = ((tx - sx) ** 2 + (ty - sy) ** 2) ** 0.5
        ux, uy = (tx - sx) / dist, (ty - sy) / dist
        hx, hy = tx - ux * HEAD_LEN, ty - uy * HEAD_LEN  # the head's base: the head is HEAD_LEN long, tip on the tip
        t = pointer_transform((hx, hy), (tx, ty), HEAD_LEN, HEAD_W, (W, H))
        _key(head.transform, {"offset_x": t["offset_x"], "offset_y": t["offset_y"], "scale_x": t["scale_x"],
                              "rotation": t["rotation"]}, f)  # fmt: skip
        length = dist - HEAD_LEN + 4  # the shaft runs under the head's base a little
        if length <= 0:
            _key(shaft.transform, {"scale_x": 0.0}, f)
            continue
        end = (sx + ux * length, sy + uy * length)
        t = pointer_transform((sx, sy), end, SHAFT_LEN, shaft_h, (W, H))
        _key(shaft.transform, {"offset_x": t["offset_x"], "offset_y": t["offset_y"], "scale_x": t["scale_x"],
                               "rotation": t["rotation"]}, f)  # fmt: skip


def add(short, tl, seq, texts, settings: dict, callouts: dict, near_anchors: dict | None, label_heroes: dict | None,
        kit=None, kit_font=None, cache_dir: Path | None = None) -> None:  # fmt: skip
    """Draw every callout. `callouts`: {"cues": bsa.callouts.load(...), "anchors": {shot: {str(i): {"f0",
    "points"}}}}; `near_anchors` and `label_heroes` are the edit's own (the near labels the callouts keep off, the
    heroes they keep off where they can). With a brand `kit` (and its card `kit_font`) the kit's cards are what they
    keep off. The plate is the near labels' dark plate and the arrow the caption highlight colour; a culprit has the
    highlight colour as its plate and a thicker arrow; caption style "outline" gives the label no plate and the arrow
    a dark edge. The arrow images are cached in `cache_dir`."""
    highlight = hex_colour(caption_highlight(short, settings)) or (0.18, 0.77, 1.0, 1.0)
    outline_style = caption_style(short, settings) == "outline"
    spans = {s.id: s for s in tl.shots}
    for shot in short.shots:
        cues = (callouts.get("cues") or {}).get(shot.id) or []
        if not cues:
            continue
        if len(cues) > MAX_PER_SHOT:
            raise ValueError(f"shot {shot.id} has {len(cues)} callouts; the edit draws at most {MAX_PER_SHOT} per shot")
        span = spans[shot.id]
        heroes = (label_heroes or {}).get(shot.id)
        rows = shot_boxes(short, tl, shot, span, (near_anchors or {}).get(shot.id), heroes, kit=kit, font=kit_font)
        if kit is None:
            bands = captions(short, tl)
        else:
            bands = [(f, f, r) for f, items in rows.items() for kind, _, _, r in items if kind == "caption"]
        placed = {f: [r for kind, _, _, r in items if kind != "caption"] for f, items in rows.items()}
        for cue in cues:
            points = ((callouts.get("anchors") or {}).get(shot.id) or {}).get(str(cue["i"]))
            if points is None:
                raise ValueError(f"no anchors for callout {cue['text']!r} near {cue['near']!r} in shot {shot.id}: run "
                                 "BLENDER anchors SHORT")  # fmt: skip
            frames = plan(cue, span, points, bands, (), placed, heroes, cue["near"])
            f0, f1 = visible_range(cue, span)
            culprit = bool(cue.get("culprit"))
            i = int(cue["i"])
            if outline_style:
                strip = texts.add(cue["text"], f0, f1, CH_PLATE + i, LABEL_SIZE, (0.5, 0.5), ("CENTER", "BOTTOM"),
                                  SMALL_MARGIN, color=highlight if culprit else (1, 1, 1, 1), bold=False,
                                  wrap_px=NEAR_WRAP_PX, box=BOX_COLOR, outline=True)  # fmt: skip
            else:
                strip = texts.add(cue["text"], f0, f1, CH_PLATE + i, LABEL_SIZE, (0.5, 0.5), ("CENTER", "BOTTOM"),
                                  SMALL_MARGIN, color=DARK if culprit else (1, 1, 1, 1), bold=False,
                                  wrap_px=NEAR_WRAP_PX, box=highlight if culprit else BOX_COLOR)  # fmt: skip
            for f in range(f0, f1 + 1):
                fr = frames[f]
                strip.location = (fr.x / W, 1.0 - fr.bottom / H)
                strip.font_size = max(1, round(LABEL_SIZE * fr.scale))
                strip.blend_alpha = fr.alpha
                for name in ("location", "font_size", "blend_alpha"):
                    strip.keyframe_insert(name, frame=f)
            _arrow(seq, i, f0, f1, frames, highlight, DARK if outline_style else None,
                   CULPRIT_SHAFT_PX if culprit else SHAFT_PX, cache_dir)  # fmt: skip
