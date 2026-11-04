"""Checks on what a viewer sees, from geometry alone (no render): text covering the subject, a subject too small for
its shot or a half of the frame left empty, and which objects act as the ground (for the camera-below-ground check).
Pure Python and numpy; bsa.blender.checks gathers the screen boxes per sampled frame and turns these into findings.

Screen boxes are pixels of the 1080x1920 frame, origin top-left (bsa.layout.Rect)."""

from __future__ import annotations

from bsa.image_checks import Finding
from bsa.layout import H, Rect, W, overlap_area, target_area

COVER_WARN = 0.25  # a caption, badge or label over more than this share of a subject's box on screen: warning
COVER_ERROR = 0.5  # over this share ...
COVER_ERROR_S = 0.5  # ... for at least this long: error (the subject can't be seen under the text)
SMALL_SHARE = 0.02  # the heroes' box on screen under this share of the frame: the subject is too small
EMPTY_REACH = 0.05  # a hero must reach this share of the frame height into a half for that half to hold it
MOST = 0.6  # "most of a shot": this share of the shot's sampled frames
GROUND_FLAT = 0.1  # a ground is at most this thick for its smaller horizontal side ...
GROUND_MIN_SIDE = 1.0  # ... at least this wide each way (metres) and as wide as the subject
GROUND_REACH = 0.05  # ... and its top no higher than this share of the subject's height above the subject's bottom
CAMERA_BELOW_TOL = 0.01  # metres: a camera this far under a ground's top surface is below the ground
EDGE_TOL = 0.001  # a hero's box this share or less outside the frame is inside (rounding)
EDGE_ERROR = 0.5  # more than this share of a hero's box outside the frame ...
EDGE_ERROR_S = 0.5  # ... for at least this long: error (the shot has lost its subject)
SWAP_FRAMES = 1  # a swap: the new part switched on at most this many frames from the old one switched off ...
SWAP_BOX_TOL = 0.02  # ... and its box on screen within this share of the frame's width (x) and height (y) of the old

KIND_NAMES = {"caption": "caption", "badge": "badge", "label": "label", "near": "label"}


def clip(r: Rect | None) -> Rect | None:
    """`r` cut to the frame, or None when nothing of it is on screen."""
    if r is None:
        return None
    x0, y0, x1, y1 = max(r.x, 0.0), max(r.y, 0.0), min(r.right, float(W)), min(r.bottom, float(H))
    return Rect(x0, y0, x1 - x0, y1 - y0) if x1 > x0 and y1 > y0 else None


def box_rect(b) -> Rect | None:
    """[x0, y0, x1, y1] (bsa.blender.anchors.screen_box) as a Rect cut to the frame, or None."""
    return None if not b else clip(Rect(b[0], b[1], b[2] - b[0], b[3] - b[1]))


def covered(subject: Rect, cover: Rect) -> float:
    """The share of `subject` (on screen) that `cover` hides."""
    area = subject.w * subject.h
    return overlap_area(subject, cover) / area if area > 0 else 0.0


def frame_spans(frames: list[int], step: int) -> str:
    out, start, prev = [], None, None
    for f in sorted(frames):
        if start is None:
            start = prev = f
        elif f - prev <= step:
            prev = f
        else:
            out.append((start, prev))
            start = prev = f
    if start is not None:
        out.append((start, prev))
    return ", ".join(str(a) if a == b else f"{a}-{b}" for a, b in out[:4]) + (" ..." if len(out) > 4 else "")


def coverage_findings(shot_id: str, samples: dict, step: int, fps: int) -> list[Finding]:
    """One finding per text and subject where a caption, badge or label covers more than COVER_WARN of the subject's
    box: an error when it covers more than COVER_ERROR for COVER_ERROR_S or longer. `samples`: {frame: {"subjects":
    {name: Rect on screen}, "texts": [(kind, text, near object or None, Rect)]}} at the shot's sampled frames,
    `step` frames apart. A near label is measured against its own object too."""
    hits: dict[tuple, dict] = {}
    for frame in sorted(samples):
        sample = samples[frame]
        subjects = {n: r for n, r in (sample.get("subjects") or {}).items() if r is not None}
        for kind, text, near, box in sample.get("texts") or []:
            for name, rect in subjects.items():
                share = covered(rect, box)
                if share <= COVER_WARN:
                    continue
                hit = hits.setdefault((kind, text, near, name), {"frames": [], "worst": (0.0, frame), "heavy": 0})
                hit["frames"].append(frame)
                hit["worst"] = max(hit["worst"], (share, frame))
                hit["heavy"] += share > COVER_ERROR
    out = []
    for (kind, text, near, name), hit in hits.items():
        share, frame = hit["worst"]
        error = hit["heavy"] * max(1, step) >= COVER_ERROR_S * fps
        whose = f"its own object {name!r}" if near == name else f"the subject {name!r}"
        what = KIND_NAMES.get(kind, kind)
        out.append(Finding(
            "covered", "error" if error else "warning", name, frame,
            f"the {what} {text!r} covers {100 * share:.0f}% of {whose} on screen in shot {shot_id!r} (frames "
            f"{frame_spans(hit['frames'], step)}): the viewer can't see it there. Frame the subject clear of the caption "
            "band and the badge corner (bsa.layout.target_area, camera.frame_objects' default band), shorten the text, or move the label"))  # fmt: skip
    return sorted(out, key=lambda f: f.severity != "error")


def edge_findings(shot_id: str, views: dict, step: int, fps: int) -> list[Finding]:
    """frame_edge findings: one per hero or overlay that is partly or wholly outside the camera frame at a sampled
    frame of a shot it is in (inside at one sampled frame at least), naming the largest share outside and its frame.
    An error for a hero (not an overlay) more than EDGE_ERROR outside, with what is left of it on screen under
    SMALL_SHARE of the frame, for EDGE_ERROR_S or longer: a nearly empty shot. A close-up or push-in that frames a
    part of a big hero (most of it outside, but the part fills the frame) stays a warning.
    `views`: {name: {"overlay": bool, "samples": [(frame, share of its box outside, 0 to 1[, share of the frame its
    visible part covers, or None when unknown])]}}, `step` frames apart. Without the third value the hero counts as
    lost when it is mostly outside."""
    out = []
    for name, v in views.items():
        samples = v["samples"]
        if not samples or all(s[1] >= 1 - EDGE_TOL for s in samples):
            continue  # never in this shot
        bad = [(s[0], s[1]) for s in samples if s[1] > EDGE_TOL]
        if not bad:
            continue
        frame, share = max(bad, key=lambda b: (b[1], -b[0]))
        heavy = sum(s[1] > EDGE_ERROR and _lost(s) for s in samples)
        overlay = bool(v.get("overlay"))
        error = not overlay and heavy * max(1, step) >= EDGE_ERROR_S * fps
        what = "overlay" if overlay else "hero"
        where = "outside" if share >= 1 - EDGE_TOL else "partly outside"
        then = "The shot has lost its subject: " if error else ""
        out.append(Finding(
            "frame_edge", "error" if error else "warning", name, frame,
            f"{what} {name!r} is {where} the camera frame in shot {shot_id!r}: {100 * share:.0f}% of {name!r} is "
            f"outside the camera frame at frame {frame} (outside at frames {frame_spans([f for f, _ in bad], step)}). "
            f"{then}{'r' if error else 'R'}eframe or move the camera (camera.move keys a smooth orbit or dolly that keeps it "
            "in the band), or tag only the true subject as hero"))  # fmt: skip
    return sorted(out, key=lambda f: f.severity != "error")


def _lost(sample) -> bool:
    """Whether a frame_edge sample's hero is lost from the shot: what is left of it on screen covers under
    SMALL_SHARE of the frame (unknown: not lost)."""
    if len(sample) < 3:
        return True
    return sample[2] is not None and sample[2] < SMALL_SHARE


def union(rects) -> Rect | None:
    rects = [r for r in rects if r is not None]
    if not rects:
        return None
    x0, y0 = min(r.x for r in rects), min(r.y for r in rects)
    return Rect(x0, y0, max(r.right for r in rects) - x0, max(r.bottom for r in rects) - y0)


def framing_findings(shot_id: str, boxes: dict, skip=frozenset()) -> list[Finding]:
    """Warnings for a shot whose subject (the union of its heroes' boxes on screen, cut to the frame) is under
    SMALL_SHARE of the frame, or (not small) leaves the top or bottom half of the frame empty (reaches less than
    EMPTY_REACH of the frame height into it), for MOST of its sampled frames. The halves split at the centre of the
    default framing band (bsa.layout.target_area, where camera.frame_objects / frame_box put the subject), not at
    H/2: a subject centred in that band always reaches both. `boxes`: {frame: [Rect or None per hero]}; frames in
    `skip` (the shot's `wide`) and frames with no hero on screen are not counted."""
    seen = {f: union(clip(r) for r in rs) for f, rs in boxes.items() if f not in skip}
    seen = {f: u for f, u in seen.items() if u is not None}
    if not seen:
        return []
    n = len(seen)
    out = []
    small = {f: u.w * u.h / (W * H) for f, u in seen.items() if u.w * u.h < SMALL_SHARE * W * H}
    if len(small) >= MOST * n:
        worst = min(small, key=small.get)
        out.append(Finding(
            "framing", "warning", None, worst,
            f"the subject is small in shot {shot_id!r}: its heroes cover {100 * small[worst]:.1f}% of the frame at "
            f"frame {worst} (under {100 * SMALL_SHARE:.0f}% in {len(small)} of {n} sampled frames). Move the camera in "
            "or use a longer lens; if the shot is a deliberate wide or establishing shot, give it \"wide\": true (or "
            "the reason) in short.json"))  # fmt: skip
    reach = EMPTY_REACH * H
    band = target_area()
    mid = band.y + band.h / 2  # the band's own centre (about row 731), not H/2
    for half, empty in (("top", lambda u: u.y > mid - reach), ("bottom", lambda u: u.bottom < mid + reach)):
        frames = [f for f, u in seen.items() if empty(u) and f not in small]  # a small subject is reported as such
        if len(frames) >= MOST * n:
            out.append(Finding(
                "framing", "warning", None, frames[0],
                f"the {half} half of the framing band has no hero in {len(frames)} of {n} sampled frames of shot {shot_id!r}: "
                "the subject sits in one half and the other is empty. Centre it in the band between the top cards and "
                "the captions (bsa.layout.target_area, where camera.frame_objects / frame_box frame by default) or "
                "move the camera; give the shot \"wide\": true (or the reason) if the "
                "space is deliberate"))  # fmt: skip
    return out


def is_ground(lo, hi, subject_lo, subject_hi) -> bool:
    """Does a mesh with world box [lo, hi] act as ground for a subject with box [subject_lo, subject_hi]: roughly
    horizontal and flat (thickness at most GROUND_FLAT of its smaller horizontal side), at least GROUND_MIN_SIDE and
    the subject's width each way, under the subject's footprint, with its top no higher than GROUND_REACH of the
    subject's height above the subject's bottom?"""
    sx, sy = hi[0] - lo[0], hi[1] - lo[1]
    side = min(sx, sy)
    wide = max(subject_hi[0] - subject_lo[0], subject_hi[1] - subject_lo[1])
    if side < max(GROUND_MIN_SIDE, wide) or hi[2] - lo[2] > GROUND_FLAT * side:
        return False
    under = lo[0] <= subject_hi[0] and subject_lo[0] <= hi[0] and lo[1] <= subject_hi[1] and subject_lo[1] <= hi[1]
    reach = GROUND_REACH * max(subject_hi[2] - subject_lo[2], 0.0) + CAMERA_BELOW_TOL
    return under and hi[2] <= subject_lo[2] + reach


def swap_problem(on: int, off: int | None, new: Rect | None, old: Rect | None) -> str | None:
    """What is wrong with a part switched on at frame `on` taking over from one switched off at `off` (None: never in
    the shot), `new` and `old` their boxes on screen (None: behind the camera, not compared), or None for a clean
    swap: the same frame (within SWAP_FRAMES) and the same place on screen (within SWAP_BOX_TOL)."""
    if off is None:
        return "it is never switched off in this shot"
    if abs(off - on) > SWAP_FRAMES:
        return f"it is switched off at frame {off}, {abs(off - on)} frames apart"
    if new is None or old is None:
        return None
    dx = max(abs(new.x - old.x), abs(new.right - old.right))
    dy = max(abs(new.y - old.y), abs(new.bottom - old.bottom))
    if dx <= SWAP_BOX_TOL * W and dy <= SWAP_BOX_TOL * H:
        return None
    pct = round(100 * max(dx / W, dy / H))
    return f"their boxes on screen differ by {pct}% of the frame"
