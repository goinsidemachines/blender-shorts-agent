"""Smoothing for keyed paths: a Gaussian that keeps the first and last values and leaves a straight line alone.

Pure numpy (no Blender): camera.move eases its dolly distance with it, camera.follow its position and aim.
"""

from __future__ import annotations

import math

import numpy as np


def gaussian(values: np.ndarray, sigma: float) -> np.ndarray:
    """`values` smoothed along axis 0 with a Gaussian of `sigma` samples (cut at 3 sigma). The ends are padded by odd
    reflection through the first and last values, so those stay put and a straight line comes back unchanged; near
    a short series' ends the kernel is cut to what the padding can give and renormalised. `sigma <= 0`, or fewer than
    3 samples, returns a copy. An (N, k) array is smoothed per column."""
    v = np.array(values, dtype=np.float64)
    n = len(v)
    if sigma <= 0 or n < 3:
        return v
    half = math.ceil(3 * sigma)
    k = np.arange(-half, half + 1)
    w = np.exp(-0.5 * (k / sigma) ** 2)
    w /= w.sum()
    pad = min(half, n - 1)  # odd reflection needs n - 1 samples past each end
    w = w[half - pad : half + pad + 1]
    w /= w.sum()
    if v.ndim == 1:
        return np.convolve(np.pad(v, pad, mode="reflect", reflect_type="odd"), w, mode="valid")
    padded = np.pad(v, [(pad, pad)] + [(0, 0)] * (v.ndim - 1), mode="reflect", reflect_type="odd")
    flat = padded.reshape(len(padded), -1)
    out = np.stack([np.convolve(flat[:, j], w, mode="valid") for j in range(flat.shape[1])], axis=1)
    return out.reshape((n,) + v.shape[1:])
