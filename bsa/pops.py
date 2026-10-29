"""The pops rule: a frame (or two) whose move is far larger than the moves on both sides of it.

A pop is a velocity discontinuity: the object jumps and then carries on as before (a teleport, a keyed jump, a
one-frame snap or glitch). It is not a fast frame in an otherwise still track: a ball that falls fast and then
hangs almost still, or a still ball struck into motion, changes speed once and keeps the new speed, so one of the
frames next to the change moves as far as the change itself.

For each run of 1 to POP_MAX_RUN consecutive frames, the run is a pop when every move in it is over POP_FACTOR
times the larger move just outside the run (moves under MOVE_EPS, 0.1 mm, count as standing still), and over the
floor: POP_FLOOR (5 mm) or POP_SIZE_FRACTION of the object's size, whichever is larger, so sub-millimetre jitter
and steps too small to see on the object are not pops. Moves are only measured between consecutive frames.

A shot's first and last frames have no move on one side inside the shot; a track may carry one frame before and
after the frames judged (`judged`), so how the object came into the shot (a ball already swinging when the shot
cuts in) is a reference for the first moves without being judged itself (a cut may move anything).

Pure numpy: no bpy, so it is tested outside Blender.
"""

from __future__ import annotations

import itertools

import numpy as np

POP_FACTOR = 8.0
POP_FLOOR = 0.005
POP_SIZE_FRACTION = 0.1
POP_MAX_RUN = 2
MOVE_EPS = 1e-4

__all__ = ["MOVE_EPS", "POP_FACTOR", "POP_FLOOR", "POP_MAX_RUN", "POP_SIZE_FRACTION", "find_pops", "pop_floor"]


def pop_floor(size: float) -> float:
    """The smallest move that can be a pop on an object of this size (its largest dimension, m)."""
    return max(POP_FLOOR, POP_SIZE_FRACTION * max(0.0, float(size)))


def _segments(track) -> list[list[tuple[int, float]]]:
    """Runs of (frame, distance moved since the frame before) over consecutive frames."""
    segs: list[list[tuple[int, float]]] = []
    cur: list[tuple[int, float]] = []
    for (f0, p0), (f1, p1) in itertools.pairwise(track):
        if f1 == f0 + 1:
            cur.append((f1, float(np.linalg.norm(np.asarray(p1, float) - np.asarray(p0, float)))))
        elif cur:
            segs.append(cur)
            cur = []
    if cur:
        segs.append(cur)
    return segs


def find_pops(track, size: float, judged: tuple[int, int] | None = None) -> list[tuple[int, float, float]]:
    """(frame, distance moved into that frame, limit) for each popping frame of a track of (frame, position).

    `judged` (first, last): only moves into frames first+1..last can be pops; moves outside (into `first`, or from
    a frame before the shot or after it) are references only. Default: the whole track."""
    floor = pop_floor(size)
    found: dict[int, tuple[float, float]] = {}
    for seg in _segments(sorted(track, key=lambda fp: fp[0])):
        d = [m for _, m in seg]
        n = len(d)
        for run in range(1, POP_MAX_RUN + 1):
            for i in range(n - run + 1):
                outside = [d[j] for j in (i - 1, i + run) if 0 <= j < n]
                ref = max([MOVE_EPS, *outside])
                limit = max(POP_FACTOR * ref, floor)
                frames = [seg[k][0] for k in range(i, i + run)]
                if judged and not all(judged[0] < f <= judged[1] for f in frames):
                    continue
                if min(d[i : i + run]) > limit:
                    for k in range(i, i + run):
                        found.setdefault(seg[k][0], (d[k], limit))
    return [(f, m, lim) for f, (m, lim) in sorted(found.items())]
