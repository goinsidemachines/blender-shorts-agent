import math

import numpy as np

from bsa.fields.physics import integrate, systems, verify

G = 9.81


def test_small_angle_period():
    out = systems.pendulum(1.0, 0.05, dt=1 / 300, n=3000)
    expected = 2 * math.pi * math.sqrt(1.0 / G)
    assert abs(verify.period_from_series(out["t"], out["theta"]) - expected) / expected < 0.002


def test_rk4_energy_drift_small():
    length = 1.0
    out = systems.pendulum(length, 0.5, dt=1 / 300, n=6000)  # 20 s
    th, om = out["theta"], out["omega"]
    energy = 0.5 * om**2 + (G / length) * (1 - np.cos(th))
    assert verify.energy_drift(energy) < 1e-4


def test_rk4_shape_and_exact_exponential():
    y = integrate.rk4(lambda t, s: -s, [1.0], 0.1, 10)
    assert y.shape == (11, 1)
    assert abs(y[-1, 0] - math.exp(-1.0)) < 1e-6


def test_msd_critical_no_overshoot():
    m, k = 2.0, 50.0
    c = 2 * math.sqrt(m * k)
    out = systems.mass_spring_damper(m, k, c, 1.0, dt=0.01, n=1000)
    assert out["x"].min() > -1e-6
    assert abs(out["x"][-1]) < 1e-3


def test_tmd_reduces_peak():
    mu = 0.02
    M, K = 1.0, (2 * math.pi) ** 2  # 1 s period
    w = math.sqrt(K / M)
    force = lambda t: math.sin(w * t)
    kw = {"M": M, "K": K, "zeta_s": 0.01, "mass_ratio": mu, "force": force, "dt": 0.005, "n": 40000}
    tuned = systems.tuned_mass_damper(tuned=True, **kw)
    bare = systems.tuned_mass_damper(tuned=False, **kw)  # no damper at all, never "locked"
    assert verify.peak_reduction(bare["xs"], tuned["xs"]) >= 0.4
    assert np.max(np.abs(tuned["vr"])) > 0
    assert np.all(bare["vr"] == 0) and np.all(bare["pull"] == 0)
    assert tuned["xs"].shape == (40001,)


def test_tmd_array_force_matches_callable():
    M, K = 1.0, 40.0
    dt, n = 0.01, 500
    f = lambda t: math.sin(3 * t)
    arr = np.array([f(i * dt) for i in range(n + 1)])
    a = systems.tuned_mass_damper(M, K, 0.01, 0.02, arr, dt=dt, n=n)
    b = systems.tuned_mass_damper(M, K, 0.01, 0.02, f, dt=dt, n=n)
    assert np.max(np.abs(a["xs"] - b["xs"])) < 1e-4


def test_period_and_peak_helpers():
    t = np.linspace(0, 10, 2001)
    assert abs(verify.period_from_series(t, np.sin(2 * math.pi * t / 2.0)) - 2.0) < 1e-3
    assert verify.peak_reduction([1, -2], [0.5, 1]) == 0.5
