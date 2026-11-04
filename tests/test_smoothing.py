import math

import numpy as np

from bsa.smoothing import gaussian


def test_gaussian_keeps_the_ends_and_lines_and_removes_a_spike():
    ramp = np.linspace(0.0, 19.0, 20)
    assert np.abs(gaussian(ramp, 3.0) - ramp).max() < 1e-9  # a straight line is unchanged

    spiky = ramp.copy()
    spiky[10] += 5.0
    out = gaussian(spiky, 3.0)
    assert abs(out[0] - spiky[0]) < 1e-9
    assert abs(out[-1] - spiky[-1]) < 1e-9
    assert out[10] - ramp[10] < 2.5, out[10]

    cols = np.c_[spiky, 2 * spiky, -spiky]  # (N, 3): each column on its own
    out3 = gaussian(cols, 3.0)
    assert out3.shape == cols.shape
    for i, k in enumerate((1, 2, -1)):
        assert np.abs(out3[:, i] - k * out).max() < 1e-9

    same = gaussian(spiky, 0)
    assert np.array_equal(same, spiky) and same is not spiky
    short = np.array([1.0, 5.0])
    assert np.array_equal(gaussian(short, 3.0), short)


def _old_ease(depth: np.ndarray, sigma: float) -> np.ndarray:
    """camera._ease_distances' smoothing as it was before bsa.smoothing (kept to pin camera.move's distances)."""
    k = np.arange(-math.ceil(3 * sigma), math.ceil(3 * sigma) + 1)
    w = np.exp(-0.5 * (k / sigma) ** 2)
    w /= w.sum()
    pad = min(len(k) // 2, len(depth) - 1)
    w = w[len(k) // 2 - pad : len(k) // 2 + pad + 1]
    w /= w.sum()
    return np.convolve(np.pad(depth, pad, mode="reflect", reflect_type="odd"), w, mode="valid")


def test_gaussian_gives_camera_moves_the_same_distances_as_before():
    rng = np.random.default_rng(7)
    for n in (3, 4, 7, 12, 51):
        depth = 4 + np.cumsum(rng.normal(0, 0.1, n))
        for every in (1, 2, 3, 8):
            sigma = 6.0 / every
            assert np.array_equal(gaussian(depth, sigma), _old_ease(depth, sigma)), (n, every)
