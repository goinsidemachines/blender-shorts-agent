"""Keyframe baking of precomputed physics series and of paths, and the tags the checks read."""

from __future__ import annotations

import difflib
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
# "contact:<other>[:<max depth m>][:moves]" (moves: the pair moves against each other by design), "overlay" (a glow,
# a guide line: not physical geometry), "overlay:may-exit" (an overlay that may leave the frame on purpose, such as
# a falling stream: no frame_edge warning), "surface" (an open fluid surface, such as water: things may cross it),
# "category:<name>" (the part's category in a brand kit: its label's dot takes that category's colour),
# "set:<role>" (scenery a set made: its floor, wall, backdrop or a prop; never a hero), "lines:off" (no ink
# lines drawn round it) and "fast" (a part that really moves more than the pop limit in one frame: a flick, a snap).
# An entry ending in ":" is a prefix (the tag goes on after it); every other entry is a whole tag.
MAY_EXIT = "overlay:may-exit"
LINES_OFF = "lines:off"
FAST = "fast"
SET_ROLES = ("floor", "wall", "backdrop", "prop")
SET_TAGS = tuple(f"set:{r}" for r in SET_ROLES)
TAGS = ("hero", "hero:", "grounded", "contact:", "overlay", MAY_EXIT, "surface", "category:", *SET_TAGS, LINES_OFF,
        FAST)
TAG_WORDS: tuple[str, ...] = tuple(t for t in TAGS if not t.endswith(":"))
TAG_PREFIXES: tuple[str, ...] = tuple(t for t in TAGS if t.endswith(":"))


def _hero_shape(t: str, rest: str) -> str | None:
    return None if any(i.strip() for i in rest.split(",")) else f"tag {t!r} lists no shot"


def _contact_shape(t: str, rest: str) -> str | None:
    return None if rest.split(":", 1)[0].strip() else f"tag {t!r} names no object"


def _category_shape(t: str, rest: str) -> str | None:
    return None if rest.strip() else f"tag {t!r} names no category"


# What a known tag must look like, by its TAGS entry: (the whole tag, the text after a prefix) -> why not, or None.
# A tag with no rule here is right as long as it is in TAG_WORDS or starts with one of TAG_PREFIXES.
TAG_SHAPES: dict[str, Callable[[str, str], str | None]] = {
    "hero:": _hero_shape,
    "contact:": _contact_shape,
    "category:": _category_shape,
}


def _is_tag_start(word: str) -> bool:
    return word in TAG_WORDS or word.startswith(TAG_PREFIXES)


def tag_problem(t: str) -> str | None:
    """None for a known tag of the right shape; else why not, with the nearest known tag for a typo."""
    if t in TAG_WORDS:
        rule = TAG_SHAPES.get(t)
        return rule(t, "") if rule else None
    for prefix in TAG_PREFIXES:
        if t.startswith(prefix):
            rule = TAG_SHAPES.get(prefix)
            return rule(t, t[len(prefix) :]) if rule else None
    if t.startswith("set:"):
        return f"unknown tag {t!r}: a set tag is one of {', '.join(SET_TAGS)}"
    close = difflib.get_close_matches(t, TAGS, n=1, cutoff=0.6)
    if not close and ":" in t:  # a long name after a misspelt prefix: compare the prefix alone
        close = difflib.get_close_matches(t.split(":", 1)[0] + ":", TAG_PREFIXES, n=1, cutoff=0.6)
    return f"unknown tag {t!r}" + (f" (did you mean {close[0]!r}?)" if close else "")


def set_role(obj) -> str | None:
    """The role of a set object ("floor", "wall", "backdrop" or "prop", from its "set:<role>" tag), or None."""
    for t in tags(obj):
        if t in SET_TAGS:
            return t[len("set:") :]
    return None


def is_set(obj) -> bool:
    """Whether the object is scenery a set made (tagged "set:<role>")."""
    return set_role(obj) is not None


def category(obj) -> str | None:
    """The name after the object's "category:" tag, lower case (its brand kit category), or None."""
    for t in tags(obj):
        if t.startswith("category:"):
            name = t[len("category:") :].strip().lower()
            return name or None
    return None


def is_overlay(obj) -> bool:
    """Whether the object is tagged "overlay" (or "overlay:may-exit"): a non-physical overlay (a glow halo, a distance
    line, an arrow) that the overlap, z-fighting, grounding and pop checks leave out; it must still stay inside the
    frame, unless it may exit."""
    t = tags(obj)
    return "overlay" in t or MAY_EXIT in t


def may_exit(obj) -> bool:
    """Whether the object is tagged "overlay:may-exit": an overlay that leaves the frame on purpose (water falling
    out of shot), which frame_edge does not warn about."""
    return MAY_EXIT in tags(obj)


def is_fast(obj) -> bool:
    """Whether the object is tagged "fast": it really moves more than the pop limit in one frame (a flick, a snap),
    so the pops check leaves it out; every other check still runs."""
    return FAST in tags(obj)


def is_surface(obj) -> bool:
    """Whether the object is tagged "surface": an open fluid surface (water moved by waves, a liquid level) that
    things may cross. The overlap and z-fighting checks leave it out while its mesh is open; a closed mesh is a
    solid and is checked whatever its tags."""
    return "surface" in tags(obj)


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
    """Merge tags ("hero", "hero:s03,s04", "grounded", "contact:<other>", "overlay", "surface", "set:<role>",
    "lines:off", "fast": see TAGS) into bsa_tags (comma-joined, no duplicates). An unknown or empty tag (a typo) is
    a ValueError saying why (tag_problem). Only a hero:<shot ids> tag may hold commas (between its shot ids). A set
    object (set:<role>) is never a hero: ValueError."""
    for t in new_tags:
        problem = tag_problem(t)
        if problem:
            raise ValueError(problem)
        ids = t[5:].split(",") if t.startswith("hero:") else None
        bad = not t or (ids is None and "," in t) or (ids is not None and not all(i.strip() and " " not in i for i in ids))
        if bad:
            raise ValueError(f"invalid tag {t!r}: must be non-empty and contain no comma (hero:s03,s04 lists shots)")
    merged = tags(obj)
    merged.extend(t for t in new_tags if t not in merged)
    if any(t in SET_TAGS for t in merged) and any(t == "hero" or t.startswith("hero:") for t in merged):
        raise ValueError(f"{getattr(obj, 'name', None) or 'object'}: set objects are scenery, never heroes")
    obj["bsa_tags"] = ",".join(merged)
