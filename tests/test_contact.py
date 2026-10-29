"""A 1-D chain of bodies with Hertz contacts and restitution losses (bsa.fields.physics.contact), and placing a
clip per impact with a gain from its energy (bsa.audio.place_clips)."""

import math

import numpy as np
import pytest

from bsa.audio import place_clips
from bsa.fields.physics.contact import contact_chain, hertz_stiffness

R = 0.0125
M = 4 / 3 * math.pi * R**3 * 7850  # a 25 mm steel ball, about 64 g
K = hertz_stiffness(R, R, 2e11, 0.3) * 1e-3  # softened steel: contacts of about a millisecond


def _momentum(out, masses):
    return out["v"] @ np.asarray(masses)


def test_hertz_stiffness_of_two_equal_steel_spheres():
    # k = 4/3 E* sqrt(R*), E* = E / (2 (1 - nu^2)), R* = R / 2
    e_star = 2e11 / (2 * (1 - 0.3**2))
    assert hertz_stiffness(R, R, 2e11, 0.3) == pytest.approx(4 / 3 * e_star * math.sqrt(R / 2))


def test_elastic_head_on_collision_of_equal_balls_swaps_velocities():
    out = contact_chain([M, M], [0.0, 2 * R + 0.001], [0.5, 0.0], [R, R], stiffness=K, restitution=1.0, t_end=0.02)
    assert out["v"][-1] == pytest.approx([0.0, 0.5], abs=2e-3)
    p = _momentum(out, [M, M])
    assert np.max(np.abs(p - p[0])) < 1e-9  # equal and opposite forces: momentum to rounding
    ke = out["kinetic"]
    assert abs(ke[-1] - ke[0]) / ke[0] < 1e-3
    (hit,) = out["impacts"]
    assert hit["pair"] == (0, 1) and hit["v_in"] == pytest.approx(0.5, rel=1e-3)
    assert 0.0015 < hit["t"] < 0.0025 and hit["energy_lost"] == pytest.approx(0.0, abs=1e-5)
    assert hit["energy"] == pytest.approx(0.5 * (M / 2) * 0.5**2, rel=1e-3)  # relative-motion energy at impact


@pytest.mark.parametrize("e", [0.9, 0.7])
def test_restitution_sets_the_rebound_and_the_energy_lost(e):
    out = contact_chain([M, M], [0.0, 2 * R + 0.001], [0.5, 0.0], [R, R], stiffness=K, restitution=e, t_end=0.02)
    v1, v2 = out["v"][-1]
    assert (v2 - v1) / 0.5 == pytest.approx(e, abs=0.02)
    p = _momentum(out, [M, M])
    assert np.max(np.abs(p - p[0])) < 1e-9
    (hit,) = out["impacts"]
    mu = M / 2
    assert hit["energy_lost"] == pytest.approx(0.5 * mu * 0.5**2 * (1 - e**2), rel=0.06)
    assert hit["v_out"] == pytest.approx(e * 0.5, abs=0.01)
    assert out["kinetic"][0] - out["kinetic"][-1] == pytest.approx(hit["energy_lost"], rel=5e-3)


def test_unequal_masses_follow_the_elastic_formulas():
    m1, m2 = 2 * M, M
    out = contact_chain([m1, m2], [0.0, 2 * R + 0.001], [0.4, 0.0], [R, R], stiffness=K, t_end=0.02)
    assert out["v"][-1] == pytest.approx([0.4 * (m1 - m2) / (m1 + m2), 0.4 * 2 * m1 / (m1 + m2)], abs=3e-3)


def test_cradle_row_hands_the_energy_to_the_last_ball():
    """Five touching balls, the first struck in at 0.5 m/s: momentum is conserved and nearly all the energy leaves
    with the last ball (a Hertz chain is not a perfect row of two-ball collisions: a little stays behind)."""
    x0 = [-0.001] + [2 * R * i for i in range(1, 5)]
    out = contact_chain([M] * 5, x0, [0.5, 0, 0, 0, 0], [R] * 5, stiffness=K, restitution=1.0, t_end=0.03)
    p = _momentum(out, [M] * 5)
    assert np.max(np.abs(p - p[0])) < 1e-9
    v = out["v"][-1]
    ke = 0.5 * M * v**2
    assert ke[4] / ke.sum() > 0.9 and v[4] == pytest.approx(0.5, abs=0.04)
    assert abs(ke.sum() - out["kinetic"][0]) / out["kinetic"][0] < 2e-3
    pairs = [h["pair"] for h in out["impacts"]]
    assert pairs[:4] == [(0, 1), (1, 2), (2, 3), (3, 4)]
    assert all(a["t"] <= b["t"] for a, b in zip(out["impacts"], out["impacts"][1:], strict=False))


def test_external_force_brings_a_pendulum_ball_back_for_a_second_hit():
    """Small-angle pendulums (restoring force -m g / L x about each rest place): the first ball swings back in.
    Impacts are reported per contact, with sampled positions at sample_dt."""
    g, length = 9.81, 0.14
    rest = np.array([0.0, 2 * R])

    def external(t, x, v):
        return -np.array([M, M]) * g / length * (x - rest)

    out = contact_chain([M, M], [-0.03, 2 * R], [0.0, 0.0], [R, R], stiffness=K, restitution=0.95, t_end=1.2,
                        external=external, sample_dt=1 / 600)  # fmt: skip
    assert len(out["impacts"]) >= 2
    period = 2 * math.pi * math.sqrt(length / g)
    gaps = np.diff([h["t"] for h in out["impacts"]])
    assert gaps[0] == pytest.approx(period / 2, rel=0.05)  # the struck ball swings out and back: half a period
    assert out["x"].shape == (len(out["t"]), 2) and out["t"][1] - out["t"][0] == pytest.approx(1 / 600, rel=0.05)


def test_contact_chain_validates_its_inputs():
    with pytest.raises(ValueError, match="same length"):
        contact_chain([M, M], [0.0], [0.0, 0.0], [R, R], stiffness=K, t_end=0.01)
    with pytest.raises(ValueError, match="restitution"):
        contact_chain([M, M], [0.0, 1.0], [0.0, 0.0], [R, R], stiffness=K, restitution=1.5, t_end=0.01)


# --------------------------------------------------------------------------- audio


def test_place_clips_puts_one_clip_per_event_with_gain_from_energy():
    sr = 1000
    click = np.array([1.0, 0.5])
    bed = place_clips([(0.1, 4.0), (0.3, 1.0)], click, length_s=0.5, sr=sr)
    assert bed.shape == (500,)
    assert bed[100] == pytest.approx(1.0) and bed[101] == pytest.approx(0.5)
    assert bed[300] == pytest.approx(0.5)  # a quarter of the energy: half the amplitude (gain ~ sqrt(energy))
    assert np.count_nonzero(bed) == 4


def test_place_clips_merges_close_events_drops_quiet_ones_and_rotates_variants():
    sr = 1000
    a, b = np.array([1.0]), np.array([-1.0])
    events = [{"t": 0.100, "energy": 1.0}, {"t": 0.105, "energy": 1.0}, {"t": 0.2, "energy": 0.0001},
              {"t": 0.3, "energy": 2.0}]  # fmt: skip
    bed = place_clips(events, [a, b], length_s=0.5, sr=sr, merge_s=0.015, floor=0.05)
    assert bed[100] == pytest.approx(1.0)  # two contacts 5 ms apart are one click, their energy added (= the max)
    assert bed[105] == 0.0 and bed[200] == 0.0  # merged; and too quiet to place
    assert bed[300] == pytest.approx(-1.0)  # the next variant
    fixed = place_clips(events, a, length_s=0.5, sr=sr, ref_energy=8.0, merge_s=0.015)
    assert fixed[300] == pytest.approx(0.5)  # gain against a fixed reference energy, so shots match


def test_place_clips_takes_the_contact_chain_impacts_and_a_time_map():
    out = contact_chain([M, M], [0.0, 2 * R + 0.001], [0.5, 0.0], [R, R], stiffness=K, t_end=0.02)
    bed = place_clips(out["impacts"], np.array([1.0]), length_s=1.0, sr=1000, offset_s=0.5)
    (hit,) = np.flatnonzero(bed)
    assert hit == round((out["impacts"][0]["t"] + 0.5) * 1000)
