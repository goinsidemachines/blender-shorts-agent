"""The pops rule (bsa.pops): a velocity discontinuity, not a fast frame against a mostly resting object."""

import math

import numpy as np

from bsa.pops import find_pops


def test_simulated_cradle_at_30_fps_has_no_pops_until_a_ball_is_teleported():
    """Five pendulum balls (bsa.fields.physics.contact): one swings in, the far one flies out, over and over,
    sampled at video rate. Every ball's track is pop-free; a 3 cm teleport of a resting ball is a pop."""
    from bsa.fields.physics.contact import contact_chain, hertz_stiffness

    r, n, g, length = 0.0125, 5, 9.81, 0.14
    m = 4 / 3 * math.pi * r**3 * 7850
    rest = np.arange(n) * 2 * r

    def external(t, x, v):
        return -m * g / length * (x - rest) - 1e-3 * v

    x0 = rest.copy()
    x0[0] -= length * math.sin(math.radians(35))
    out = contact_chain([m] * n, x0, [0.0] * n, [r] * n, stiffness=hertz_stiffness(r, r, 2e11, 0.3) * 1e-3,
                        restitution=0.97, t_end=4.0, external=external, sample_dt=1 / 30)  # fmt: skip
    assert len(out["impacts"]) >= 10
    for b in range(n):
        track = [(i + 1, np.array([x, 0.0, 0.0])) for i, x in enumerate(out["x"][:, b])]
        assert find_pops(track, size=2 * r) == [], b
    still = [(i + 1, np.array([rest[2] + (0.03 if i >= 60 else 0.0), 0.0, 0.0])) for i in range(120)]
    assert [f for f, _, _ in find_pops(still, size=2 * r)] == [61]


def _track(xs, f0=1):
    return [(f0 + i, np.array([x, 0.0, 0.0])) for i, x in enumerate(xs)]


def _frames(found):
    return [f for f, _, _ in found]


def test_teleport_in_steady_motion_is_a_pop():
    xs = [0.02 * i + (2.0 if i >= 14 else 0.0) for i in range(48)]
    assert _frames(find_pops(_track(xs), size=1.0)) == [15]


def test_still_object_snapping_once_is_a_pop():
    xs = [0.3 + (2.0 if i >= 14 else 0.0) for i in range(48)]
    assert _frames(find_pops(_track(xs), size=1.0)) == [15]


def test_one_frame_glitch_out_and_back_is_a_pop():
    xs = [0.01 * i for i in range(30)]
    xs[12] += 0.5  # one frame off its path, then back
    assert _frames(find_pops(_track(xs), size=0.5)) == [13, 14]


def test_keyed_jump_over_two_frames_is_a_pop():
    xs = [0.01 * i for i in range(30)]
    for i in range(12, 30):
        xs[i] += 0.3 if i == 12 else 0.6  # a jump split over two frames, then the same slow motion
    assert _frames(find_pops(_track(xs), size=0.2)) == [13, 14]


def test_jump_on_the_first_moving_frame_is_a_pop():
    xs = [0.0] + [1.0 + 0.01 * i for i in range(20)]
    assert _frames(find_pops(_track(xs), size=0.2)) == [2]


def test_eased_multi_leg_path_has_no_pops():
    n = 48
    t = [0.5 - 0.5 * math.cos(math.pi * i / (n - 1)) for i in range(n)]  # one easing over 6 m + 0.4 m
    pts = []
    for u in t:
        s = u * 6.4
        pts.append([-3 + s, 2, 1] if s <= 6 else [3, 2, 1 + (s - 6)])
    track = [(i + 1, np.array(p)) for i, p in enumerate(pts)]
    assert find_pops(track, size=0.1) == []


def test_fast_fall_then_near_rest_is_not_a_pop():
    """A ball released from a swing falls fast (about 19 mm a frame), stops dead in a collision, then hangs almost
    still (sub-millimetre a frame): the old median rule called its own fall a pop."""
    g, length, fps = 9.81, 0.12, 30
    theta, omega, xs = math.radians(45), 0.0, []
    for _ in range(18):  # pendulum swing down to the bottom
        for _ in range(100):
            omega -= g / length * math.sin(theta) * (1 / fps / 100)
            theta += omega * (1 / fps / 100)
            if theta <= 0:
                theta, omega = 0.0, 0.0
        xs.append(-length * math.sin(theta))
    xs += [xs[-1] + 0.0002 * math.sin(i / 3) for i in range(80)]  # nearly at rest: sub-mm wobble
    found = find_pops(_track(xs), size=0.025)
    assert found == [], found


def test_collision_handing_velocity_to_the_next_ball_is_not_a_pop():
    """The struck ball is still, then leaves at the speed the first one arrived with: a step in speed, not a pop."""
    v = 0.019  # m a frame
    first = [-v * (20 - i) for i in range(20)] + [0.0] * 40  # arrives fast, stops dead
    second = [0.025] * 20 + [0.025 + v * 0.4] + [0.025 + v * (0.4 + i) for i in range(1, 40)]
    assert find_pops(_track(first), size=0.025) == []
    assert find_pops(_track(second), size=0.025) == []


def test_tiny_jitter_on_a_resting_object_is_not_a_pop():
    rng = np.random.default_rng(1)
    xs = list(0.00005 * rng.standard_normal(60))  # 0.05 mm noise
    assert find_pops(_track(xs), size=0.025) == []


def test_jump_under_the_size_floor_is_not_a_pop():
    xs = [0.0] * 10 + [0.002] * 10  # a 2 mm step on a 1 m object
    assert find_pops(_track(xs), size=1.0) == []
    xs = [0.0] * 10 + [0.3] * 10  # 30 cm on the same object is
    assert _frames(find_pops(_track(xs), size=1.0)) == [11]


def test_gaps_in_frames_are_not_moves():
    track = _track([0.0] * 5) + _track([5.0] * 5, f0=20)  # not consecutive: no move measured across the gap
    assert find_pops(track, size=0.1) == []


def test_motion_carried_in_from_before_the_shot_is_a_reference_not_a_pop():
    """A shot cuts in on a ball already swinging fast; it stops dead two frames later. Judged alone the two fast
    frames look like a jump; with the frame before the shot as a reference they are its swing."""
    xs = [0.0, 0.0297, 0.0460, 0.0599, 0.0615, 0.0631, 0.0644]  # frame 975 (before the shot), then 976...
    track = _track(xs, f0=975)
    assert find_pops(track, size=0.025, judged=(976, 981)) == []
    teleport = _track([0.0, 0.0, 0.0, 0.5, 0.5, 0.5], f0=975)  # a still object snapping inside the shot still pops
    assert _frames(find_pops(teleport, size=0.025, judged=(976, 980))) == [978]
    cut = _track([0.0, 0.5, 0.5, 0.5], f0=975)  # the move into the shot's first frame is the cut: not judged
    assert find_pops(cut, size=0.025, judged=(976, 978)) == []


def test_limit_reported_is_above_the_jump_distance_threshold():
    xs = [0.02 * i + (2.0 if i >= 14 else 0.0) for i in range(48)]
    ((frame, dist, limit),) = find_pops(_track(xs), size=1.0)
    assert frame == 15 and dist > limit and abs(dist - 2.02) < 1e-6
