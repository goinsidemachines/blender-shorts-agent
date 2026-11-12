"""Signal processing for the mix (numpy only: this runs inside Blender's Python).

- K-weighted loudness (ITU-R BS.1770): `momentary_lufs` (loudest 400 ms) and `integrated_lufs` (the whole signal).
  The K-weighting filter is applied as its magnitude response in the frequency domain (zero phase), which is exact
  for a loudness reading and needs no per-sample loop.
- Fades and seamless loops (equal-power).
- The effects bus: a 75 Hz high-pass and one short room reverb, both as FFT convolution.
- A ducking gain with lookahead, and a lookahead peak limiter. Both are vectorised: no per-sample Python loops.
"""

from __future__ import annotations

from functools import lru_cache

import numpy as np

from bsa.audio.sfx import SR, shaped_noise

FLOOR_DB = -120.0
K_WINDOW_S = 0.4  # momentary loudness: 400 ms

# BS.1770 K-weighting at 48 kHz: stage 1 a high shelf (+4 dB above ~1.7 kHz), stage 2 a high-pass (~38 Hz).
_K_SHELF = ((1.53512485958697, -2.69169618940638, 1.19839281085285), (1.0, -1.69065929318241, 0.73248077421585))
_K_RLB = ((1.0, -2.0, 1.0), (1.0, -1.99004745483398, 0.99007225036621))


def db(x: float) -> float:
    return 10 ** (x / 20)


def to_db(x: float) -> float:
    return float(20 * np.log10(x)) if x > 0 else FLOOR_DB


def _biquad_mag(b, a, w: np.ndarray) -> np.ndarray:
    z1 = np.exp(-1j * w)
    z2 = z1 * z1
    return np.abs((b[0] + b[1] * z1 + b[2] * z2) / (a[0] + a[1] * z1 + a[2] * z2))


@lru_cache(maxsize=8)
def _k_response(n: int) -> np.ndarray:
    w = 2 * np.pi * np.fft.rfftfreq(n, 1 / SR) / SR
    return _biquad_mag(*_K_SHELF, w) * _biquad_mag(*_K_RLB, w)


def k_weight(x: np.ndarray) -> np.ndarray:
    """`x` (mono) K-weighted (BS.1770 magnitude response, zero phase)."""
    x = np.asarray(x, dtype=np.float64)
    if len(x) == 0:
        return x
    return np.fft.irfft(np.fft.rfft(x) * _k_response(len(x)), len(x))


def _lufs(mean_square: float) -> float:
    return float(-0.691 + 10 * np.log10(mean_square)) if mean_square > 0 else FLOOR_DB


def momentary_lufs(x: np.ndarray) -> float:
    """Loudest momentary loudness (LUFS, 400 ms window, mono). A clip shorter than the window counts as if padded with
    silence, so a 50 ms knock is read as the 400 ms it would occupy: that is how loud a short sound seems."""
    x = np.asarray(x, dtype=np.float64)
    w = round(K_WINDOW_S * SR)
    if len(x) == 0 or not x.any():
        return FLOOR_DB
    x = np.concatenate([x, np.zeros(max(w - len(x), 0) + 2048)])
    p = k_weight(x) ** 2
    c = np.concatenate([[0.0], np.cumsum(p)])
    return _lufs(float(np.max(c[w:] - c[:-w])) / w)


def integrated_lufs(x: np.ndarray) -> float:
    """Loudness (LUFS) of the whole signal, ungated: for steady material such as room tone."""
    x = np.asarray(x, dtype=np.float64)
    if len(x) == 0 or not x.any():
        return FLOOR_DB
    return _lufs(float(np.mean(k_weight(x) ** 2)))


def level_gain(x: np.ndarray, target_lufs: float, max_crest_db: float | None = None) -> float:
    """The scale that puts `x`'s loudest momentary loudness at `target_lufs` (1.0 for silence). `max_crest_db` is the
    safety step: the scale is lowered if the peak would end more than that many dB above the target (a lone click can
    read as quiet over 400 ms but must not be boosted into a huge spike)."""
    m = momentary_lufs(x)
    if m <= FLOOR_DB:
        return 1.0
    g = db(target_lufs - m)
    if max_crest_db is not None:
        peak = float(np.abs(x).max())
        g = min(g, db(target_lufs + max_crest_db) / peak)
    return g


def level_to_lufs(x: np.ndarray, target_lufs: float, max_crest_db: float | None = None) -> np.ndarray:
    """`x` scaled so its loudest momentary loudness is `target_lufs` (see `level_gain`)."""
    g = level_gain(x, target_lufs, max_crest_db)
    return x if g == 1.0 and momentary_lufs(x) <= FLOOR_DB else x * g


# --------------------------------------------------------------------------- fades and loops


def fade_in_curve(n: int) -> np.ndarray:
    """Equal-power fade-in over n samples (0 -> 1)."""
    return np.sin(np.linspace(0.0, np.pi / 2, n)) if n > 0 else np.zeros(0)


def fade_out_curve(n: int) -> np.ndarray:
    return fade_in_curve(n)[::-1]


def loop_to_length(x: np.ndarray, n: int, seed: int = 0, xfade_s: float = 1.0) -> np.ndarray:
    """`x` repeated to exactly n samples, seamless: copies are joined with equal-power crossfades of up to `xfade_s`
    (a quarter of the clip at most), and the start is a random (seeded) point inside the clip."""
    x = np.asarray(x, dtype=np.float64)
    if n <= 0 or len(x) == 0:
        return np.zeros(max(n, 0))
    rng = np.random.default_rng(seed)
    if len(x) >= n:
        off = int(rng.integers(0, len(x) - n + 1))
        return x[off : off + n].copy()
    k = min(round(xfade_s * SR), len(x) // 4)
    off = int(rng.integers(0, max(len(x) - 2 * k, 0) + 1))
    first = x[off:]
    out = np.zeros(n)
    m = min(len(first), n)
    out[:m] = first[:m]
    pos = len(first)  # end of what is written
    up, down = fade_in_curve(k), fade_out_curve(k)
    while pos < n:
        start = pos - k  # the next copy overlaps the last k samples
        seg = x[: n - start] if n - start < len(x) else x
        if k:
            out[start:pos] = out[start:pos] * down + seg[:k] * up
        out[pos : start + len(seg)] = seg[k:]
        pos = start + len(seg)
    return out


# --------------------------------------------------------------------------- the effects bus


@lru_cache(maxsize=4)
def highpass_ir(fc: float = 75.0, length: int = 4800) -> np.ndarray:
    """Impulse response of a 2nd-order Butterworth high-pass (RBJ biquad). Causal, so nothing sounds before it
    happens (a zero-phase filter would ring ahead of every hit)."""
    w0 = 2 * np.pi * fc / SR
    alpha = np.sin(w0) / (2 * np.sqrt(0.5))
    cs = np.cos(w0)
    a0 = 1 + alpha
    b0 = b2 = (1 + cs) / 2 / a0
    b1 = -(1 + cs) / a0
    a1, a2 = -2 * cs / a0, (1 - alpha) / a0
    h = np.zeros(length)
    y1 = y2 = 0.0
    for i in range(length):  # a few thousand samples, once
        x0 = 1.0 if i == 0 else 0.0
        x1 = 1.0 if i == 1 else 0.0
        x2 = 1.0 if i == 2 else 0.0
        y = b0 * x0 + b1 * x1 + b2 * x2 - a1 * y1 - a2 * y2
        h[i], y2, y1 = y, y1, y
    return h


@lru_cache(maxsize=4)
def room_ir(seed: int = 0, rt60: float = 0.35, predelay_s: float = 0.008, wet_db: float = -16.0) -> np.ndarray:
    """A synthetic small-room impulse response, shape (2, n): noise that decays 60 dB in `rt60` seconds, darker as it
    goes, after a short pre-delay; the two channels are different noise (a little width). Scaled so the wet signal is
    `wet_db` dB under the dry one for broadband material."""
    n = round(rt60 * SR * 1.1)
    t = np.arange(n) / SR
    decay = np.exp(-t * np.log(1000) / rt60) * np.minimum(t / 0.002, 1.0)
    pre = np.zeros(round(predelay_s * SR))
    chans = []
    for c in range(2):
        h = shaped_noise(n, 120, 7500, -3, seed=1000 * seed + c) * decay
        h *= np.sqrt(db(wet_db) ** 2 / np.sum(h**2))  # energy: wet power = dry power * 10**(wet_db/10)
        chans.append(np.concatenate([pre, h]))
    return np.stack(chans)


def effects_bus(bed: np.ndarray, reverb: bool = True, highpass_hz: float = 75.0, seed: int = 0) -> np.ndarray:
    """The effects (n, 2) through a high-pass and, with `reverb`, one shared room: dry + wet, same length."""
    n = len(bed)
    if n == 0 or not bed.any():
        return bed
    hp = highpass_ir(float(highpass_hz))
    ir = room_ir(seed) if reverb else None
    need = n + len(hp) + (ir.shape[1] if ir is not None else 0)
    nfft = 1 << int(np.ceil(np.log2(need)))
    h_hp = np.fft.rfft(hp, nfft)
    h_ir = np.fft.rfft(ir, nfft, axis=1) if ir is not None else None
    out = np.zeros((n, bed.shape[1]))
    for c in range(bed.shape[1]):
        if not bed[:, c].any():
            continue
        spec = np.fft.rfft(bed[:, c], nfft) * h_hp
        wet = 0.0
        if h_ir is not None:
            wet = np.fft.irfft(spec * h_ir[min(c, 1)], nfft)[:n]
        out[:, c] = np.fft.irfft(spec, nfft)[:n] + wet
    return out


# --------------------------------------------------------------------------- dynamics


def duck_gain(speech: np.ndarray, depth_db: float, lookahead_s: float = 0.06, attack_s: float = 0.07,
              release_s: float = 0.4) -> np.ndarray:  # fmt: skip
    """Gain (1 = untouched, down to 10**(depth_db/20)) for a bed under speech. `speech` is a bool array per sample.
    The gain starts to fall `lookahead_s` before speech starts (so the effects are already dipping when a line starts),
    follows one-pole curves with time constant `attack_s` going down and `release_s` coming back, and is exact:
    each stretch of constant target is one closed-form exponential."""
    n = len(speech)
    g = np.ones(n)
    if n == 0 or not speech.any():
        return g
    floor = db(depth_db)
    la = round(lookahead_s * SR)
    ahead = np.concatenate([speech[la:], np.zeros(min(la, n), dtype=bool)])[:n]
    target = ahead | speech  # starts `la` early, ends with the speech
    edges = np.flatnonzero(np.diff(target.astype(np.int8))) + 1
    starts = np.concatenate([[0], edges])
    ends = np.concatenate([edges, [n]])
    cur = 1.0
    for s, e in zip(starts, ends, strict=True):
        down = bool(target[s])
        tgt, tau = (floor, attack_s * SR) if down else (1.0, release_s * SR)
        seg = tgt + (cur - tgt) * np.exp(-np.arange(1, e - s + 1) / tau)
        g[s:e] = seg
        cur = seg[-1]
    return g


def _sliding_min(x: np.ndarray, w: int) -> np.ndarray:
    """min(x[i : i + w]) for every i in range(len(x) - w + 1), O(n) (van Herk / Gil-Werman, in blocks)."""
    n = len(x)
    pad = (-n) % w
    b = np.concatenate([x, np.full(pad, np.inf)]).reshape(-1, w)
    pre = np.minimum.accumulate(b, axis=1).ravel()
    suf = np.minimum.accumulate(b[:, ::-1], axis=1)[:, ::-1].ravel()
    m = n - w + 1
    return np.minimum(suf[:m], pre[w - 1 : w - 1 + m])


def limit(x: np.ndarray, ceiling_db: float = -1.0, lookahead_s: float = 0.005, release_s: float = 0.1) -> np.ndarray:
    """A transparent lookahead peak limiter: no sample ends above `ceiling_db`. Material under the ceiling is not
    touched at all. Gain starts to fall `lookahead_s` before a peak (a smooth ramp, not a clip), then recovers with
    time constant `release_s`. Stereo is linked (one gain for both channels, the image does not move)."""
    ceiling = db(ceiling_db)
    peak = np.abs(x).max(axis=1) if x.ndim == 2 else np.abs(x)
    if not len(peak) or peak.max() <= ceiling:
        return x
    n = len(peak)
    w = max(round(lookahead_s * SR), 1)
    need = np.minimum(1.0, ceiling / np.maximum(peak, 1e-12))
    padded = np.concatenate([np.ones(w), need, np.ones(w)])
    g = _sliding_min(padded, 2 * w + 1)  # the lowest gain needed within +-lookahead, around every sample
    cs = np.concatenate([[0.0], np.cumsum(np.concatenate([np.ones(w), g, np.ones(w)]))])
    g = (cs[2 * w + 1 :] - cs[: -(2 * w + 1)]) / (2 * w + 1)  # smoothed, still <= need everywhere
    cut = np.where(1.0 - g > 1e-9, 1.0 - g, 0.0)  # float dust is not gain reduction
    tau = release_s * SR
    ramp = np.arange(n) / tau
    with np.errstate(divide="ignore"):
        logs = np.where(cut > 1e-12, np.log(np.maximum(cut, 1e-300)) + ramp, -np.inf)
    released = np.exp(np.maximum.accumulate(logs) - ramp)  # exponential release of every dip, deepest wins
    gain = 1.0 - np.maximum(released, cut)
    out = x * (gain[:, None] if x.ndim == 2 else gain)
    return np.clip(out, -ceiling, ceiling)  # only float dust is left to catch
