"""A 1-D chain of bodies that collide with their neighbours: Hertz contact with restitution losses.

Any collision short can use it: a Newton's cradle, balls in a row, a train of carriages buffering, beads on a wire.
Bodies sit on one line in order (body i between i-1 and i+1, never passing each other); neighbours i, i+1 touch when
the gap between their surfaces closes, x[i+1] - x[i] < r[i] + r[i+1]. The contact force is

    F = k d^1.5 (1 + a d' / v_in)         (Hunt and Crossley, Trans. ASME J. Appl. Mech. 42, 440 (1975))

d is the overlap, d' its rate, v_in the closing speed when the contact began, and k the Hertz stiffness
(hertz_stiffness: k = 4/3 E* sqrt(R*) for two spheres). The damping `a` is solved from the asked coefficient of
restitution e (exactly: for this model e depends on `a` alone), so a two-body impact leaves at e times the speed it
came in with, and the energy it loses is 1/2 mu v_in^2 (1 - e^2) (mu = m1 m2 / (m1 + m2)). The force only pushes.

Integration is semi-implicit Euler: momentum is conserved to rounding (the contact forces are equal and opposite),
and with e = 1 the energy to about 1e-3 over an impact at the default steps. Steel is so stiff that a real contact
lasts about 0.1 ms: a softer `stiffness` (1e-3 of steel's, say) gives contacts of about a millisecond, still far
shorter than a video frame, with the same outcome of each impact and a much faster run (say so in notes.md).

SI units: m, kg, N, s.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from functools import lru_cache

import numpy as np

EXPONENT = 1.5
STEPS_PER_CONTACT = 200  # default contact step: the shortest expected contact over this many steps
TOUCH_EPS = 1e-12  # m: overlaps below this are rounding in where touching bodies were placed

__all__ = ["contact_chain", "contact_time", "hertz_stiffness"]


def hertz_stiffness(r1: float, r2: float, e_mod: float, nu: float, e_mod2: float | None = None,
                    nu2: float | None = None) -> float:  # fmt: skip
    """Hertz stiffness k (N/m^1.5) of two spheres of radii r1, r2 (m; float("inf") for a flat), Young's moduli
    (Pa) and Poisson ratios (the second body's default to the first's): F = k d^1.5."""
    e2 = e_mod if e_mod2 is None else e_mod2
    n2 = nu if nu2 is None else nu2
    e_star = 1.0 / ((1 - nu**2) / e_mod + (1 - n2**2) / e2)
    inv = (1.0 / r1 if math.isfinite(r1) else 0.0) + (1.0 / r2 if math.isfinite(r2) else 0.0)
    return 4.0 / 3.0 * e_star * math.sqrt(1.0 / inv)


def contact_time(mu: float, k: float, v: float) -> float:
    """Duration (s) of an elastic Hertz contact of reduced mass mu (kg), stiffness k, closing speed v (m/s)."""
    return 3.2145 * (mu / k) ** 0.4 * max(v, 1e-9) ** -0.2


def _rebound(a: float) -> float:
    """Restitution of the dimensionless contact s'' = -s^1.5 (1 + a s'), entered at s' = 1."""
    s, ds, h = 0.0, 1.0, 2e-3
    while True:
        f = -max(s, 0.0) ** EXPONENT * (1 + a * ds)
        ds += min(f, 0.0) * h
        s += ds * h
        if s < 0.0:
            return max(-ds, 0.0)
        if ds < -1.0:  # not reachable for a >= 0; a guard
            return 1.0


@lru_cache(maxsize=64)
def _damping_for(e: float) -> float:
    """The Hunt-Crossley `a` (times v_in) that gives restitution e: solved by bisection on the dimensionless
    contact."""
    if e >= 1.0:
        return 0.0
    lo, hi = 0.0, 1.0
    while _rebound(hi) > e:
        hi *= 2.0
    for _ in range(50):
        mid = 0.5 * (lo + hi)
        lo, hi = (mid, hi) if _rebound(mid) > e else (lo, mid)
    return 0.5 * (lo + hi)


def contact_chain(masses, x0, v0, radii, *, stiffness, restitution: float = 1.0, t_end: float,
                  external: Callable | None = None, sample_dt: float = 1 / 600, dt_contact: float | None = None,
                  dt_free: float | None = None) -> dict:  # fmt: skip
    """Simulate bodies on a line colliding with their neighbours (module docstring).

    masses (kg), x0 (centres, m, in order along the line), v0 (m/s), radii (m: half of each body's length along the
    line); `stiffness` k of every contact (N/m^1.5, a number or one per neighbour pair); `restitution` e in (0, 1];
    `external(t, x, v) -> forces (N)` for anything else (gravity on pendulums, drag, a spring to a wall).

    Returns {"t" (n,), "x" (n, N), "v" (n, N), "kinetic" (n,) J, "impacts": [...]} sampled every `sample_dt`, where
    each impact is {"t" (s, when the contact began), "t_end", "pair" (i, i+1), "v_in" (closing speed m/s), "v_out"
    (separating speed), "energy" (J: 1/2 mu v_in^2, the energy of the relative motion it stops; use it for loudness),
    "energy_lost" (J, turned to heat in this contact), "peak_force" (N)}, in time order."""
    m = np.asarray(masses, float)
    x = np.asarray(x0, float).copy()
    v = np.asarray(v0, float).copy()
    r = np.asarray(radii, float)
    n = len(m)
    if not (len(x) == len(v) == len(r) == n) or n < 2:
        raise ValueError("masses, x0, v0 and radii must have the same length (at least 2 bodies)")
    if not 0.0 < restitution <= 1.0:
        raise ValueError(f"restitution must be in (0, 1], got {restitution}")
    k = np.broadcast_to(np.asarray(stiffness, float), (n - 1,)).copy()
    reach = r[:-1] + r[1:]
    mu = m[:-1] * m[1:] / (m[:-1] + m[1:])
    a_hc = _damping_for(float(restitution))
    if dt_contact is None:
        v_ref = max(2.0 * float(np.max(np.abs(v))), 0.1)
        dt_contact = float(np.min([contact_time(mu[i], k[i], v_ref) for i in range(n - 1)])) / STEPS_PER_CONTACT
    if dt_free is None:
        dt_free = min(sample_dt / 4, 50 * dt_contact)
    v_in = np.zeros(n - 1)  # closing speed of each open contact, when it began
    open_at = [None] * (n - 1)  # the impact dict of each open contact
    impacts: list[dict] = []
    ts, xs, vs = [0.0], [x.copy()], [v.copy()]
    t, next_sample = 0.0, sample_dt

    while t < t_end - 1e-12:
        gaps = np.diff(x) - reach
        closing = np.maximum(-np.diff(v), 0.0)
        near = np.any(gaps < 0) or np.any((closing > 0) & (gaps < closing * dt_free * 3))
        dt = min(dt_contact if near else dt_free, t_end - t)
        d = np.where(-gaps > TOUCH_EPS, -gaps, 0.0)  # bodies placed just touching (float rounding) are not in contact
        dd = -np.diff(v)  # overlap rate
        touching = d > 0
        f = np.zeros(n - 1)
        if touching.any():
            for i in np.flatnonzero(touching):
                if open_at[i] is None:  # a contact begins
                    v_in[i] = max(dd[i], 1e-9)
                    open_at[i] = {"t": t, "pair": (int(i), int(i) + 1), "v_in": float(dd[i]),
                                  "energy": float(0.5 * mu[i] * dd[i] ** 2), "energy_lost": 0.0, "peak_force": 0.0}
            elastic = k * d**EXPONENT
            f = np.where(touching, np.maximum(elastic * (1 + a_hc * dd / np.maximum(v_in, 1e-9)), 0.0), 0.0)
        force = np.zeros(n)
        force[:-1] -= f
        force[1:] += f
        if external is not None:
            force += np.asarray(external(t, x, v), float)
        v = v + force / m * dt
        x = x + v * dt
        t += dt
        for i in np.flatnonzero(touching):
            hit = open_at[i]
            damp = f[i] - k[i] * d[i] ** EXPONENT  # the damping part of the force did this work on the overlap
            hit["energy_lost"] += float(damp * dd[i] * dt)
            hit["peak_force"] = max(hit["peak_force"], float(f[i]))
        now = (np.diff(x) - reach) < -TOUCH_EPS
        for i in range(n - 1):
            if open_at[i] is not None and not now[i]:
                hit = open_at[i]
                hit["t_end"] = t
                hit["v_out"] = float(max(v[i + 1] - v[i], 0.0))
                impacts.append(hit)
                open_at[i] = None
        if t >= next_sample - 1e-12 or t >= t_end - 1e-12:
            ts.append(t)
            xs.append(x.copy())
            vs.append(v.copy())
            next_sample += sample_dt
    for i, hit in enumerate(open_at):  # still in contact at the end
        if hit is not None:
            impacts.append({**hit, "t_end": t, "v_out": float(max(v[i + 1] - v[i], 0.0))})
    vs_arr = np.array(vs)
    impacts.sort(key=lambda h: (h["t"], h["pair"]))
    return {"t": np.array(ts), "x": np.array(xs), "v": vs_arr, "kinetic": 0.5 * (vs_arr**2) @ m, "impacts": impacts}
