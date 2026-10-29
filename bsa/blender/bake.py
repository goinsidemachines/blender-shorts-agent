"""Keyframe baking of precomputed physics series and of paths, and the tags the checks read."""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from itertools import pairwise

EASES: dict[str, Callable[[float], float]] = {
    "linear": lambda t: t,
    "smooth": lambda t: t * t * (3.0 - 2.0 * t),  # smoothstep: starts and stops at rest
    "sine": lambda t: 0.5 - 0.5 * math.cos(math.pi * t),
}


def keyframe_series(
    obj, data_path: str, index: int, values: Sequence[float], frame0: int = 1, step: int = 1
) -> None:
    """One key per `step` frames from frame0, linear interpolation. index < 0 means a scalar property."""
    for i, v in enumerate(values):
        frame = frame0 + i * step
        if index < 0:
            setattr(obj, data_path, float(v))
            obj.keyframe_insert(data_path, frame=frame)
        else:
            getattr(obj, data_path)[index] = float(v)
            obj.keyframe_insert(data_path, index=index, frame=frame)
    for fc in _fcurves(obj.animation_data.action):
        if fc.data_path == data_path and (index < 0 or fc.array_index == index):
            for kp in fc.keyframe_points:
                kp.interpolation = "LINEAR"


def path_positions(
    points: Sequence[Sequence[float]], n: int, ease: str | Callable[[float], float] = "smooth"
) -> list[tuple[float, float, float]]:
    """n positions along the polyline `points`, one per frame, spaced by arc length: frame i sits at distance
    ease(i / (n - 1)) * total length along the whole path. One easing over the whole length, so a long leg and a
    short leg move at the same speed and the speed does not jump at a corner (easing one parameter per leg, or
    across the points, races on long legs and crawls on short ones, and the pops check flags it)."""
    pts = [tuple(float(c) for c in p) for p in points]
    if len(pts) < 2 or n < 2:
        raise ValueError("a path needs at least 2 points and 2 frames")
    fn = EASES.get(ease) if isinstance(ease, str) else ease
    if fn is None:
        raise ValueError(f"unknown ease {ease!r}: use one of {sorted(EASES)} or a function of t in [0, 1]")
    legs = [math.dist(a, b) for a, b in pairwise(pts)]
    total = sum(legs)
    out = []
    for i in range(n):
        s = min(max(fn(i / (n - 1)), 0.0), 1.0) * total
        k = 0
        while k < len(legs) - 1 and s > legs[k]:
            s -= legs[k]
            k += 1
        u = min(s / legs[k], 1.0) if legs[k] > 0 else 0.0
        a, b = pts[k], pts[k + 1]
        out.append(tuple(a[j] + (b[j] - a[j]) * u for j in range(3)))
    out[-1] = pts[-1]
    return out


def keyframe_path(
    obj, points: Sequence[Sequence[float]], frame0: int, frame1: int, ease: str | Callable[[float], float] = "smooth"
) -> None:
    """Key obj.location on every frame from frame0 to frame1 along the polyline `points` by arc length, with one
    easing ("smooth", "sine", "linear" or a function of t in [0, 1]) over the whole length (see path_positions).
    Use it for anything that travels along a multi-leg path: effects, particles, a camera target."""
    positions = path_positions(points, frame1 - frame0 + 1, ease)
    for axis in range(3):
        keyframe_series(obj, "location", axis, [p[axis] for p in positions], frame0=frame0)


def _fcurves(action):
    if hasattr(action, "fcurves"):  # Blender < 5.0
        return list(action.fcurves)
    out = []  # 5.x slotted actions
    for layer in action.layers:
        for strip in layer.strips:
            for bag in strip.channelbags:
                out.extend(bag.fcurves)
    return out


# Every tag the checks read (07-checks.md documents each): "hero" / "hero:<shot ids>", "grounded",
# "contact:<other>[:<max depth m>][:moves]" (moves: the pair moves against each other by design), and "overlay" (a glow, a guide line: not physical geometry).
TAGS = ("hero", "grounded", "contact:", "overlay")


def _is_tag_start(word: str) -> bool:
    return word in ("hero", "grounded", "overlay") or word.startswith(("contact:", "hero:"))


def is_overlay(obj) -> bool:
    """Whether the object is tagged "overlay": a non-physical overlay (a glow halo, a distance line, an arrow) that the
    overlap, z-fighting, grounding and pop checks leave out; it must still stay inside the frame."""
    return "overlay" in tags(obj)


def tags(obj) -> list[str]:
    """The object's bsa_tags as a list (empty when none). A per-shot hero tag keeps its shot list: "hero:s03,s04"."""
    out: list[str] = []
    for word in (t for t in obj.get("bsa_tags", "").split(",") if t):
        if out and out[-1].startswith("hero:") and not _is_tag_start(word):
            out[-1] += "," + word  # another shot of the hero:... list before it
        else:
            out.append(word)
    return out


def is_hero(obj, shot_id: str | None = None) -> bool:
    """Whether the object is a hero in shot `shot_id` ("hero" = every shot, "hero:s03,s04" = those shots); with no
    shot, whether it is a hero in any shot."""
    for t in tags(obj):
        if t == "hero":
            return True
        if t.startswith("hero:") and (shot_id is None or shot_id in t[5:].split(",")):
            return True
    return False


def tag(obj, *new_tags: str) -> None:
    """Merge tags ("hero", "hero:s03,s04", "grounded", "contact:<other>", "overlay") into bsa_tags (comma-joined, no
    duplicates). Only a hero:<shot ids> tag may hold commas (between its shot ids)."""
    for t in new_tags:
        ids = t[5:].split(",") if t.startswith("hero:") else None
        bad = not t or (ids is None and "," in t) or (ids is not None and not all(i.strip() and " " not in i for i in ids))
        if bad:
            raise ValueError(f"invalid tag {t!r}: must be non-empty and contain no comma (hero:s03,s04 lists shots)")
    merged = tags(obj)
    merged.extend(t for t in new_tags if t not in merged)
    obj["bsa_tags"] = ",".join(merged)
