"""What the check stage says about a short's callouts, apart from the scene (no bpy): a near label and a callout on
the same object in one shot, more than one culprit, a cue that was only estimated, and, from where each label and
arrow land (bsa.callout_layout, placed as the edit places them), a part outside the safe area at its cue or a label
that still overlaps a caption or another label. Findings are (shot id, severity, object, frame, message) rows;
bsa_blender turns them into check rows."""

from __future__ import annotations

from bsa import callouts
from bsa.callout_layout import plan, visible_range
from bsa.layout import Rect, safe_rect
from bsa.overlays import captions, shot_boxes
from bsa.short import label_near

Row = tuple[str, str, str | None, int | None, str]  # shot id, severity, object, frame, message


def duplicates(short) -> list[Row]:
    """A near label and a callout on the same object in one shot: both are drawn, the callout alone says it better."""
    out: list[Row] = []
    for shot in short.shots:
        calls = callouts.callouts_of(shot)
        near = {label_near(lb): lb for lb in getattr(shot, "labels", None) or [] if label_near(lb)}
        for c in calls:
            if c.near in near:
                msg = (f"shot {shot.id}: {c.near!r} has a near label and the callout {c.text!r}: both are drawn. "
                       "Keep the callout only")
                out.append((shot.id, "warning", c.near, None, msg))
    return out


def culprits(short) -> list[Row]:
    """bsa.callouts.warnings (more than one culprit), at the first culprit's shot, so check and validate say it once."""
    lines = callouts.warnings(short)
    if not lines:
        return []
    first = next(s.id for s in short.shots if any(c.culprit for c in callouts.callouts_of(s)))
    return [(first, "warning", None, None, line) for line in lines]


def estimated(cues: dict[str, list[dict]]) -> list[str]:
    """One note per cue whose frame is only estimated (no spoken word time yet): it may move once the voice is timed."""
    return [f"shot {sid} callout {c['text']!r} is timed from the reading pace, not the voice: its frame ({c['f_on']}) "
            "may move after narrate" for sid, rows in cues.items() for c in rows if c.get("estimated")]  # fmt: skip


def _hits(a: Rect, b: Rect) -> bool:
    return a.x < b.right and b.x < a.right and a.y < b.bottom and b.y < a.bottom


def _run(frames: list[int]) -> str:
    return f"frame {frames[0]}" if frames[0] == frames[-1] else f"frames {frames[0]} to {frames[-1]}"


def layout(short, tl, cues: dict[str, list[dict]], anchors: dict, kit=None, font=None) -> list[Row]:
    """The label and arrow of every callout placed as the edit places them (bsa.blender.callout_edit.add): the part
    off screen or outside the safe area at its cue frame (the arrow is dropped there, the label stays), and any frame
    the label's box still overlaps a caption, another text of the shot or an earlier callout's label (the layout
    keeps off them where it can; this is where it cannot). `anchors`: anchors.json's content ("shots" the near
    labels', "heroes", "callouts")."""
    out: list[Row] = []
    spans = {s.id: s for s in tl.shots}
    safe = safe_rect()
    for shot in short.shots:
        shot_cues = cues.get(shot.id) or []
        if not shot_cues:
            continue
        span = spans[shot.id]
        heroes = (anchors.get("heroes") or {}).get(shot.id)
        rows = shot_boxes(short, tl, shot, span, (anchors.get("shots") or {}).get(shot.id), heroes, kit=kit, font=font)
        if kit is None:
            bands = captions(short, tl)
        else:
            bands = [(f, f, r) for f, items in rows.items() for kind, _, _, r in items if kind == "caption"]
        others = {f: [(kind, r) for kind, _, _, r in items] for f, items in rows.items()}
        placed = {f: [r for kind, _, _, r in items if kind != "caption"] for f, items in rows.items()}
        earlier: dict[int, list[tuple[str, Rect]]] = {}
        for cue in shot_cues:
            points = ((anchors.get("callouts") or {}).get(shot.id) or {}).get(str(cue["i"]))
            if points is None:
                continue
            f_on, _ = visible_range(cue, span)
            pts, p0 = points.get("points") or [], int(points.get("f0", span.f0))
            k = f_on - p0
            point = pts[k] if 0 <= k < len(pts) else None
            if point is None:
                msg = (f"shot {shot.id} callout {cue['text']!r}: {cue['near']!r} is not on screen at frame {f_on} "
                       "(its cue): the label is drawn, the arrow is dropped")
                out.append((shot.id, "warning", cue["near"], f_on, msg))
            elif not (safe.x <= point[0] <= safe.right and safe.y <= point[1] <= safe.bottom):
                msg = (f"shot {shot.id} callout {cue['text']!r}: {cue['near']!r} is outside the safe area at frame "
                       f"{f_on} (its cue): the label is drawn, the arrow is dropped")
                out.append((shot.id, "warning", cue["near"], f_on, msg))
            frames = plan(cue, span, points, bands, (), placed, heroes, cue["near"])
            clash: dict[str, list[int]] = {}
            for f, fr in frames.items():
                for kind, r in [*[("caption", c[3]) for c in bands if c[0] <= f <= c[1]],
                                *[(k, r) for k, r in others.get(f, []) if k != "caption"], *earlier.get(f, [])]:  # fmt: skip
                    if _hits(fr.box, r):
                        clash.setdefault({"caption": "a caption", "near": "a near label", "label": "a label",
                                          "badge": "the badge", "callout": "another callout"}.get(kind, kind),
                                         []).append(f)  # fmt: skip
                earlier.setdefault(f, []).append(("callout", fr.box))
            for what, fs in clash.items():
                out.append((shot.id, "warning", cue["near"], fs[0],
                            f"shot {shot.id} callout {cue['text']!r}: its label overlaps {what} at {_run(fs)}"))
    return out
