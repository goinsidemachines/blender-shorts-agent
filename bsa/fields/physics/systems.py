"""Ready-made oscillator models. SI units throughout: m, kg, N, s, rad.

`n` is the number of RK4 steps; every output array has n + 1 samples, at t = k*dt.
"""

from __future__ import annotations

import math
from collections.abc import Callable

import numpy as np

from .integrate import rk4

G = 9.81  # m/s^2


def _force_fn(force, dt: float, n: int) -> Callable[[float], float]:
    """Callable f(t) -> N, or an array sampled at dt (linearly interpolated; needs n + 1 samples)."""
    if force is None:
        return lambda t: 0.0
    if callable(force):
        return force
    arr = np.asarray(force, dtype=float)
    if arr.size < n + 1:
        raise ValueError(f"force array needs at least n + 1 = {n + 1} samples, got {arr.size}")
    grid = np.arange(arr.size) * dt
    return lambda t: float(np.interp(t, grid, arr))


def pendulum(length_m: float, theta0_rad: float, damping: float = 0.0, *, dt: float = 1 / 300, n: int = 6000) -> dict:
    """Full nonlinear pendulum, released from rest. theta'' = -(g/L) sin(theta) - damping * theta'.

    `damping` is a linear coefficient in 1/s. Returns t (s), theta (rad), omega (rad/s).
    """
    g_l = G / length_m

    def f(t, y):
        return np.array([y[1], -g_l * math.sin(y[0]) - damping * y[1]])

    y = rk4(f, [theta0_rad, 0.0], dt, n)
    return {"t": np.arange(n + 1) * dt, "theta": y[:, 0], "omega": y[:, 1]}


def mass_spring_damper(m: float, k: float, c: float, x0: float, v0: float = 0.0, force=None, *, dt: float, n: int) -> dict:
    """m x'' + c x' + k x = force(t). m kg, k N/m, c N*s/m, x0 m, v0 m/s.

    `force`: None, callable f(t) -> N, or array sampled at dt. Returns t, x (m), v (m/s).
    """
    fn = _force_fn(force, dt, n)

    def f(t, y):
        return np.array([y[1], (fn(t) - c * y[1] - k * y[0]) / m])

    y = rk4(f, [x0, v0], dt, n)
    return {"t": np.arange(n + 1) * dt, "x": y[:, 0], "v": y[:, 1]}


def tuned_mass_damper(
    M: float, K: float, zeta_s: float, mass_ratio: float, force, *, dt: float, n: int, tuned: bool = True
) -> dict:
    """Structure (modal mass M kg, stiffness K N/m, damping ratio zeta_s) with a tuned mass damper.

    Damper mass m = mass_ratio * M, Den Hartog tuning: f_opt = 1/(1+mu), zeta_opt = sqrt(3mu / (8(1+mu)^3)).
    `force`: callable f(t) -> N on the structure, or array sampled at dt. Starts at rest.
    tuned=False means no damper at all: the structure responds bare and the damper's mass is ignored. It is not a
    locked damper (a locked one would add its mass and lower the period), so label that side "no damper".

    Returns t, xs (structure displacement, m), xd (damper absolute displacement, m), vr (damper velocity
    relative to the structure, m/s) and pull (force the damper exerts on the structure, N; zero with no damper).
    """
    mu = mass_ratio
    m = mu * M
    ws = math.sqrt(K / M)
    wd = ws / (1 + mu)
    zd = math.sqrt(3 * mu / (8 * (1 + mu) ** 3))
    kd, cd = m * wd**2, 2 * zd * m * wd
    cs = 2 * zeta_s * M * ws
    fn = _force_fn(force, dt, n)

    def f(t, y):
        xs, vs, r, vr = y
        pull = (cd * vr + kd * r) if tuned else 0.0
        a_s = (fn(t) - cs * vs - K * xs + pull) / M
        a_r = (-(cd * vr + kd * r) / m - a_s) if tuned else 0.0
        return np.array([vs, a_s, vr if tuned else 0.0, a_r])

    y = rk4(f, [0.0, 0.0, 0.0, 0.0], dt, n)
    r, vr = y[:, 2], y[:, 3]
    pull = (cd * vr + kd * r) if tuned else np.zeros(n + 1)
    return {"t": np.arange(n + 1) * dt, "xs": y[:, 0], "xd": y[:, 0] + r, "vr": vr, "pull": pull}
