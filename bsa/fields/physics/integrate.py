"""Fixed-step Runge-Kutta 4 integrator (numpy only)."""

from __future__ import annotations

from collections.abc import Callable

import numpy as np


def rk4(f: Callable[[float, np.ndarray], np.ndarray], y0, dt: float, n: int, t0: float = 0.0) -> np.ndarray:
    """Integrate y' = f(t, y) for n steps of size dt (seconds).

    Returns an array of shape (n + 1, len(y0)); row k is the state at t0 + k*dt.
    """
    if n < 0 or dt <= 0:
        raise ValueError("need n >= 0 and dt > 0")
    y = np.atleast_1d(np.asarray(y0, dtype=float))
    out = np.empty((n + 1, y.size))
    out[0] = y
    t = t0
    for i in range(n):
        k1 = np.asarray(f(t, y), dtype=float)
        k2 = np.asarray(f(t + dt / 2, y + dt / 2 * k1), dtype=float)
        k3 = np.asarray(f(t + dt / 2, y + dt / 2 * k2), dtype=float)
        k4 = np.asarray(f(t + dt, y + dt * k3), dtype=float)
        y = y + dt / 6 * (k1 + 2 * k2 + 2 * k3 + k4)
        t = t0 + (i + 1) * dt
        out[i + 1] = y
    return out
