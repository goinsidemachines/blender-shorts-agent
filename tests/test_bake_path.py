import math
from itertools import pairwise

import pytest

from bsa.blender.bake import path_positions


def _dist(a, b):
    return math.dist(a, b)


def test_ends_on_the_first_and_last_points():
    pts = [(0, 0, 0), (10, 0, 0), (10, 1, 0)]
    out = path_positions(pts, 31)
    assert len(out) == 31
    assert out[0] == pytest.approx(pts[0]) and out[-1] == pytest.approx(pts[-1])


def test_speed_follows_one_easing_over_the_whole_length():
    """A long leg and a short leg: the distance along the path follows ease(t) * total length, so speed does not
    jump at the corner (an eased parameter per leg, or per point, races on long legs and crawls on short ones)."""
    pts = [(0, 0, 0), (10, 0, 0), (10, 1, 0)]
    n = 61
    out = path_positions(pts, n, ease="linear")
    steps = [_dist(a, b) for a, b in pairwise(out)]
    total = 11.0
    for s in steps:  # chords: a step across the corner is a little shorter than the arc
        assert s == pytest.approx(total / (n - 1), rel=0.3)
    assert sum(steps) == pytest.approx(total, rel=0.01)
    smooth = path_positions(pts, n)  # smoothstep: slow at both ends, never a jump between neighbouring frames
    steps = [_dist(a, b) for a, b in pairwise(smooth)]
    mid = steps[5:-5]
    assert all(b / a < 1.5 and a / b < 1.5 for a, b in pairwise(mid)), steps
    assert steps[0] < steps[n // 2] / 10


def test_positions_lie_on_the_path_at_their_arc_length():
    pts = [(0, 0, 0), (3, 0, 0), (3, 4, 0)]  # 3 m then 4 m
    out = path_positions(pts, 8, ease=lambda t: t)  # every 1 m of the 7 m path
    assert out[3] == pytest.approx((3, 0, 0))
    assert out[5] == pytest.approx((3, 2, 0))


def test_rejects_a_bad_path_or_easing():
    with pytest.raises(ValueError):
        path_positions([(0, 0, 0)], 10)
    with pytest.raises(ValueError):
        path_positions([(0, 0, 0), (1, 0, 0)], 1)
    with pytest.raises(ValueError):
        path_positions([(0, 0, 0), (1, 0, 0)], 10, ease="bouncy")
    still = path_positions([(1, 2, 3), (1, 2, 3)], 4)  # zero length: stays put
    assert all(p == pytest.approx((1, 2, 3)) for p in still)
