"""Sanity checks to run a model against a sourced number before animating it."""

from __future__ import annotations

import numpy as np


def period_from_series(t, x) -> float:
    """Mean oscillation period (s) from upward zero crossings, linearly interpolated. Needs >= 2 crossings."""
    t = np.asarray(t, dtype=float)
    x = np.asarray(x, dtype=float)
    idx = np.nonzero((x[:-1] < 0) & (x[1:] >= 0))[0]
    if idx.size < 2:
        raise ValueError("need at least two upward zero crossings")
    tc = t[idx] - x[idx] * (t[idx + 1] - t[idx]) / (x[idx + 1] - x[idx])
    return float(np.mean(np.diff(tc)))


def energy_drift(E) -> float:
    """Largest |E - E[0]| relative to |E[0]|."""
    E = np.asarray(E, dtype=float)
    return float(np.max(np.abs(E - E[0])) / abs(E[0]))


def peak_reduction(a, b) -> float:
    """Fractional drop in peak |value| from baseline a to b: 1 - max|b| / max|a| (0.4 means 40 % lower)."""
    return float(1.0 - np.max(np.abs(b)) / np.max(np.abs(a)))
