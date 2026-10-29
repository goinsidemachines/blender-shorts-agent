"""Synthesised sound effects (numpy only). Every effect is deterministic for a given seed.

Each function returns a mono float64 array at SR. KINDS maps the `kind` names allowed in short.json to
callables with the uniform signature (dur_s, seed=0).
"""

from __future__ import annotations

from collections.abc import Callable

import numpy as np

SR = 48000


def _n(dur_s: float) -> int:
    return max(0, round(dur_s * SR))


def _t(dur_s: float) -> np.ndarray:
    return np.arange(_n(dur_s)) / SR


def shaped_noise(n: int, f_lo: float, f_hi: float, tilt_db_oct: float = 0.0, seed: int = 0) -> np.ndarray:
    """Noise band-limited to [f_lo, f_hi] Hz with a spectral tilt (dB/octave, FFT-shaped). Unit RMS."""
    if n <= 0:
        return np.zeros(0)
    x = np.fft.rfft(np.random.default_rng(seed).standard_normal(n))
    f = np.fft.rfftfreq(n, 1 / SR)
    g = np.zeros_like(f)
    band = (f >= f_lo) & (f <= f_hi)
    g[band] = (np.maximum(f[band], 1) / 1000.0) ** (tilt_db_oct / 6.02)
    g *= 1 / (1 + (f_lo / np.maximum(f, 1)) ** 4) * 1 / (1 + (f / f_hi) ** 4)  # soft band edges
    y = np.fft.irfft(x * g, n)
    return y / (y.std() + 1e-12)


def whoosh(dur_s: float, seed: int = 0) -> np.ndarray:
    n = _n(dur_s)
    tt = np.linspace(0, 1, n)
    return shaped_noise(n, 200, 6000, -3, seed) * np.sin(np.pi * tt) ** 2


def blip(freq: float = 1320, dur_s: float = 0.12) -> np.ndarray:
    tt = _t(dur_s)
    return np.sin(2 * np.pi * freq * tt) * np.exp(-tt / 0.025)


def impact(dur_s: float = 0.6, seed: int = 0) -> np.ndarray:
    tt = _t(dur_s)
    phase = 2 * np.pi * (45 * tt + 75 * 0.06 * (1 - np.exp(-tt / 0.06)))  # pitch falls from 120 to 45 Hz
    thump = np.sin(phase) * np.exp(-tt / 0.18)
    click = shaped_noise(len(tt), 80, 3000, -4, seed) * np.exp(-tt / 0.02) * 0.5
    return (thump + click) * np.minimum(tt / 0.002, 1.0)


def rumble(dur_s: float, seed: int = 0) -> np.ndarray:
    tt = _t(dur_s)
    d = max(dur_s, 1e-9)
    env = (1 - np.exp(-tt / 0.08)) * np.exp(-tt / (0.3 * d)) * (1 + 0.5 * np.sin(2 * np.pi * 1.7 * tt) ** 2)
    return shaped_noise(len(tt), 25, 400, -6, seed) * env


def hiss(dur_s: float, seed: int = 0) -> np.ndarray:
    tt = _t(dur_s)
    fade = np.clip(np.minimum(tt, dur_s - tt) / 0.05, 0, 1)
    return shaped_noise(len(tt), 1500, 9000, -2, seed) * fade


def creak(dur_s: float = 0.9, seed: int = 0) -> np.ndarray:
    tt = _t(dur_s)
    f_c = np.random.default_rng(seed).uniform(70, 140)
    tone = np.sin(2 * np.pi * f_c * tt + 3 * np.sin(2 * np.pi * 11 * tt))
    env = np.sin(np.pi * np.clip(tt / max(dur_s, 1e-9), 0, 1)) ** 2
    return tone * shaped_noise(len(tt), 50, 900, -3, seed) * env


def hum(freq: float, dur_s: float) -> np.ndarray:
    tt = _t(dur_s)
    y = np.sin(2 * np.pi * freq * tt) + 0.3 * np.sin(2 * np.pi * 2 * freq * tt) + 0.1 * np.sin(2 * np.pi * 3 * freq * tt)
    return y * np.clip(np.minimum(tt, dur_s - tt) / 0.05, 0, 1)


def wind(dur_s: float, seed: int = 0) -> np.ndarray:
    tt = _t(dur_s)
    gust = 0.7 + 0.3 * np.sin(2 * np.pi * 0.35 * tt + seed)
    return shaped_noise(len(tt), 60, 1800, -5, seed) * gust


def thud(dur_s: float = 0.5, seed: int = 0) -> np.ndarray:
    """A dull, heavy landing: low thump, a soft body of low noise, no bright click."""
    tt = _t(dur_s)
    phase = 2 * np.pi * (55 * tt + 40 * 0.04 * (1 - np.exp(-tt / 0.04)))
    body = shaped_noise(len(tt), 40, 700, -6, seed) * np.exp(-tt / 0.05) * 0.4
    return (np.sin(phase) * np.exp(-tt / 0.12) + body) * np.minimum(tt / 0.004, 1.0)


def click(dur_s: float = 0.08, seed: int = 0) -> np.ndarray:
    """A small mechanical click: a very short broadband tick and a brief metallic ring."""
    tt = _t(dur_s)
    f = np.random.default_rng(seed).uniform(2200, 3400)
    tick = shaped_noise(len(tt), 800, 9000, -1, seed) * np.exp(-tt / 0.003)
    return tick + 0.4 * np.sin(2 * np.pi * f * tt) * np.exp(-tt / 0.012)


def scrape(dur_s: float = 0.8, seed: int = 0) -> np.ndarray:
    """Something dragged with friction: stick-slip bursts of gritty mid-band noise."""
    tt = _t(dur_s)
    rng = np.random.default_rng(seed)
    rate = rng.uniform(18, 30)
    grit = np.abs(np.sin(np.pi * rate * tt + rng.uniform(0, np.pi))) ** 3
    env = np.sin(np.pi * np.clip(tt / max(dur_s, 1e-9), 0, 1)) ** 0.5
    return shaped_noise(len(tt), 300, 6000, -2, seed) * (0.35 + grit) * env


def slide(dur_s: float = 0.8, seed: int = 0) -> np.ndarray:
    """A smooth slide: steady low-mid friction noise that swells and fades."""
    tt = _t(dur_s)
    env = np.sin(np.pi * np.clip(tt / max(dur_s, 1e-9), 0, 1)) ** 1.5
    return shaped_noise(len(tt), 120, 2500, -4, seed) * env


KINDS: dict[str, Callable[..., np.ndarray]] = {
    "thud": thud,
    "click": click,
    "scrape": scrape,
    "slide": slide,
    "whoosh": whoosh,
    "blip": lambda dur_s, seed=0: blip(dur_s=dur_s),
    "impact": impact,
    "rumble": rumble,
    "hiss": hiss,
    "creak": creak,
    "hum": lambda dur_s, seed=0: hum(110, dur_s),
    "wind": wind,
}
