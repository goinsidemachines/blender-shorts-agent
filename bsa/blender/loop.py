"""Seamless loops: helpers for a build.py whose short ends back on its first frame (`loop` on, see bsa.presets), and
the arithmetic of the loop check. Pure Python and numpy, no bpy: importable from a build script and unit-testable
outside Blender. The part that reads a scene is bsa.blender.loopcheck.

Writing a loop: the value shown at frame `n_frames + 1` must be the value at frame 1, so write every cyclic value as a
function of `e = loop_phase(frame, n_frames)` (negative before the end, 0 on the last frame), turn things a whole
number of times (`whole_turns`) and start every motion already running at frame 1 (a loop that starts from rest and
ends moving jumps). `hermite_join` bends a move that does not return by itself back onto its start.

The check (`compare`): the value at frame n+1 is predicted from the last two frames, v(n+1) = v(n) + (v(n) - v(n-1)),
and compared with the value at frame 1 (the loop point is a step of one frame, the same as every step before it).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

POS_FRACTION = 0.005  # a position may miss by this share of the scene's size (its frame box diagonal)
ROT_DEG = 0.5  # a rotation by this many degrees
LENS_MM = 0.5  # the camera's focal length
SHAPE_KEY = 0.01  # a shape key's value
SCALE_FRACTION = 0.005  # a scale, as a share of itself
MAX_FINDINGS = 20  # listed findings; more are counted in one last line
MIN_FRAMES = 4  # a loop shorter than this has no frames to measure


def loop_phase(frame: int, n_frames: int) -> int:
    """Frames from the end: `frame - n_frames`, so -3 is three frames before the last frame, 0 is the last frame (and
    1 - n_frames the first). Write a cyclic value as a function of it and the end meets the start.

    >>> loop_phase(1, 60), loop_phase(60, 60)
    (-59, 0)
    """
    return frame - n_frames


def whole_turns(rate_turns_per_frame: float, frames: int) -> float:
    """The turn rate nearest to `rate_turns_per_frame` that makes a whole number of turns in `frames` frames, so a
    spinning part is where it started when the short repeats. A rate too slow for even one turn gives 0 (not
    turning): ask for at least 1 / frames.

    >>> whole_turns(0.021, 60)  # 1.26 turns in 60 frames -> 1 turn
    0.016666666666666666
    >>> whole_turns(-0.04, 60) * 60  # 2.4 turns backwards -> 2
    -2.0
    """
    if frames <= 0:
        raise ValueError(f"frames must be positive, got {frames!r}")
    return round(rate_turns_per_frame * frames) / frames


def hermite_join(v_end: float, d_end: float, v_start: float, d_start: float, t: float) -> float:
    """A cubic Hermite blend from `v_end` (slope `d_end`) to `v_start` (slope `d_start`) as `t` runs 0 to 1. Slopes are
    the change in value per unit of t, i.e. per whole blend, so for a blend over `k` frames pass the per-frame slope
    times `k`. The value is v_end at t=0 and v_start at t=1, and the slopes match at both ends: use it to bring a
    value that ends somewhere else back onto where the loop starts, without a visible kink at either side.
    Works on floats and on numpy arrays.

    >>> hermite_join(0.0, 0.0, 1.0, 0.0, 0.5)  # both ends at rest: an ease in and out, half way at t=0.5
    0.5
    """
    t2, t3 = t * t, t * t * t
    return ((2 * t3 - 3 * t2 + 1) * v_end + (t3 - 2 * t2 + t) * d_end
            + (-2 * t3 + 3 * t2) * v_start + (t3 - t2) * d_start)  # fmt: skip


# --------------------------------------------------------------------------- the comparison


@dataclass(frozen=True)
class Tolerances:
    pos: float  # metres (POS_FRACTION of the scene's frame box diagonal)
    rot_deg: float = ROT_DEG
    lens: float = LENS_MM
    shape: float = SHAPE_KEY
    scale: float = SCALE_FRACTION


def tolerances(diagonal: float) -> Tolerances:
    """The tolerances for a scene whose frame box diagonal is `diagonal` metres (never below 0.1 mm)."""
    return Tolerances(pos=max(POS_FRACTION * float(diagonal), 1e-4))


def decompose(matrix) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(location, 3x3 rotation, scale) of a 4x4 world matrix (columns are the axes; shear is ignored). A mirrored
    matrix (negative determinant) puts the flip on the X scale so the rotation stays a proper rotation."""
    m = np.asarray(matrix, dtype=np.float64)
    loc = m[:3, 3].copy()
    cols = m[:3, :3]
    scale = np.linalg.norm(cols, axis=0)
    safe = np.where(scale > 1e-12, scale, 1.0)
    rot = cols / safe
    if np.linalg.det(rot) < 0:
        rot[:, 0] *= -1.0
        scale[0] *= -1.0
    return loc, rot, scale


def rotation_error_deg(a, b) -> float:
    """The angle in degrees of the turn that takes rotation matrix `a` to `b`. A whole turn more or less is no
    difference at all (matrices, not Euler angles).

    >>> round(rotation_error_deg(np.eye(3), np.diag([-1.0, -1.0, 1.0])), 6)
    180.0
    """
    r = np.asarray(a).T @ np.asarray(b)
    return math.degrees(math.acos(min(1.0, max(-1.0, (np.trace(r) - 1.0) / 2.0))))


def _extrapolate_rotation(prev, last) -> np.ndarray:
    """The rotation one more step on from `last` when it came from `prev`: the step (last relative to prev) again."""
    prev, last = np.asarray(prev), np.asarray(last)
    return (last @ prev.T) @ last


def channels_of(matrix, label: str = "") -> dict[str, tuple[str, object]]:
    """{channel name: (kind, value)} of a world matrix: position, rotation and scale; `label` prefixes the names
    ("bone 'Arm' ")."""
    loc, rot, scale = decompose(matrix)
    return {f"{label}position": ("pos", loc), f"{label}rotation": ("rot", rot), f"{label}scale": ("scale", scale)}


def _gap(kind: str, predicted, actual) -> float:
    """How far `predicted` is from `actual`, in the kind's own unit (metres, degrees, mm, shape value, share)."""
    if kind == "rot":
        return rotation_error_deg(predicted, actual)
    if kind == "scale":
        a = np.asarray(actual, dtype=np.float64)
        return float(np.max(np.abs(np.asarray(predicted) - a) / np.maximum(np.abs(a), 1e-6)))
    if kind == "pos":
        return float(np.linalg.norm(np.asarray(predicted) - np.asarray(actual)))
    return abs(float(predicted) - float(actual))


def _limit(kind: str, tol: Tolerances) -> float:
    return {"pos": tol.pos, "rot": tol.rot_deg, "scale": tol.scale, "lens": tol.lens, "focus": tol.pos,
            "shape": tol.shape}[kind]  # fmt: skip


def _unit(kind: str) -> str:
    return {"pos": "m", "rot": "degrees", "scale": "of its size", "lens": "mm", "focus": "m", "shape": ""}[kind]


def _predict(kind: str, prev, last):
    if kind == "rot":
        return _extrapolate_rotation(prev, last)
    return np.asarray(last, dtype=np.float64) * 2 - np.asarray(prev, dtype=np.float64) if kind in ("pos", "scale") \
        else 2 * float(last) - float(prev)  # fmt: skip


def _step(kind: str, a, b):
    """The step from `a` to `b` as a value of its own, to compare two steps (velocity)."""
    if kind == "rot":
        return np.asarray(b) @ np.asarray(a).T
    if kind in ("pos", "scale"):
        return np.asarray(b, dtype=np.float64) - np.asarray(a, dtype=np.float64)
    return float(b) - float(a)


def _step_gap(kind: str, s1, s2) -> float:
    if kind == "rot":
        return rotation_error_deg(s1, s2)
    if kind == "scale":
        return float(np.max(np.abs(np.asarray(s1) - np.asarray(s2))))
    if kind == "pos":
        return float(np.linalg.norm(np.asarray(s1) - np.asarray(s2)))
    return abs(float(s1) - float(s2))


def compare(samples: dict[int, dict], n_frames: int, tol: Tolerances, first: int = 1) -> list[dict]:
    """The loop mismatches. `samples`: {frame: {(object, channel): (kind, value)}} at the frames `first`, `first + 1`,
    `n_frames - 1` and `n_frames` (kinds: pos, rot, scale, lens, focus, shape; see `channels_of`). For each channel
    present at all those frames: its value at frame n+1 is predicted from the last two frames and compared with its
    value at `first` ("jump"); when that holds, the step out of the first frame is compared with the step into it
    ("kink": the speed or turn rate changes at the loop point). Returns [{"obj", "channel", "what", "gap", "limit",
    "unit", "kind"}], in a stable order; [] when the loop is seamless."""
    f0, f1, p, last = first, first + 1, n_frames - 1, n_frames
    if n_frames - first + 1 < MIN_FRAMES:
        return []
    out: list[dict] = []
    for key in sorted(samples[f0], key=lambda k: (str(k[0]), k[1])):
        if not all(key in samples[f] for f in (f1, p, last)):
            continue
        kind = samples[f0][key][0]
        v0, v1, vp, vl = (samples[f][key][1] for f in (f0, f1, p, last))
        limit = _limit(kind, tol)
        gap = _gap(kind, _predict(kind, vp, vl), v0)
        row = {"obj": key[0], "channel": key[1], "kind": kind, "limit": limit, "unit": _unit(kind)}
        if gap > limit:
            out.append({**row, "what": "jump", "gap": gap})
            continue
        if kind in ("pos", "rot", "scale", "lens", "focus"):  # a step's size is a speed: its tolerance is the same
            kink = _step_gap(kind, _step(kind, vp, vl), _step(kind, v0, v1))
            if kink > limit:
                out.append({**row, "what": "kink", "gap": kink})
    return out


def describe(m: dict, n_frames: int, first: int = 1) -> str:
    """One finding's text for a `compare` row."""
    name = f"{m['obj']!r} {m['channel']}" if m["obj"] != "camera" else f"camera {m['channel']}"
    amount = f"{m['gap']:.3g} {m['unit']}".strip()
    if m["what"] == "jump":
        return (f"loop: {name} would jump by {amount} when the short repeats: continuing its motion past frame "
                f"{n_frames} (from frames {n_frames - 1}-{n_frames}) does not land on its value at frame {first} "
                f"(allowed {m['limit']:.3g} {m['unit']}). Write it as a function of loop_phase (bsa.blender.loop) so "
                "the end meets the start.").replace("  ", " ")  # fmt: skip
    return (f"loop: {name} changes speed at the loop point by {amount} per frame (allowed {m['limit']:.3g}): the "
            f"step from frame {n_frames - 1} to {n_frames} differs from the step from frame {first} to {first + 1}. "
            "Start the motion already running, at the speed it ends with.")  # fmt: skip


def cap(rows: list[dict], most: int = MAX_FINDINGS) -> tuple[list[dict], int]:
    """(the rows to list, how many were left out)."""
    return rows[:most], max(0, len(rows) - most)
