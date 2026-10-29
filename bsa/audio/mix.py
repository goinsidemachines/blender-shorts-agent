"""Voice + effects mix.

Voice is normalised to VOICE_DBFS RMS while speaking (pauses do not count), the effects bed to BED_DBFS RMS where it
sounds (silence between sparse effects does not count, so a few blips are not boosted over the voice), the bed ducks
by DUCK_DB under speech with a 0.25 s envelope, then a tanh soft limit and a PEAK_DBFS peak ceiling. `mix` reports
the levels as heard in the result, measured the same way: the voice while speaking, the bed where it sounds clear of
speech.

Convention for sfx `level_db`: before the bed is normalised, each event is scaled so its RMS over the whole event
(its dur_s) is 10**(level_db/20). So level_db sets the balance between events (and against `extra_bed`, which is added as
given); the summed bed is then normalised as a whole to BED_DBFS.
"""

from __future__ import annotations

import wave
from pathlib import Path

import numpy as np

from bsa.audio import recorded
from bsa.audio.sfx import KINDS, SR
from bsa.short import Short
from bsa.timeline import Timeline

VOICE_DBFS = -15
BED_DBFS = -23
DUCK_DB = -8
PEAK_DBFS = -1
DUCK_S = 0.25
_SPEECH_THRESHOLD = 0.01
_BED_ACTIVE_DB = -40  # the bed "sounds" where its 50 ms envelope is within this of its loudest
_FLOOR_DB = -120.0


def _db(x: float) -> float:
    return 10 ** (x / 20)


def _to_db(rms: float) -> float:
    return float(20 * np.log10(rms)) if rms > 0 else _FLOOR_DB


def _moving_average(x: np.ndarray, width: int) -> np.ndarray:
    """Centred box average over `width` samples, zero-padded at the ends (cumulative-sum form, O(n))."""
    c = np.concatenate([[0.0], np.cumsum(np.concatenate([x, np.zeros(width)]))])
    lo = width // 2
    i = np.arange(len(x))
    start = np.maximum(i - lo, 0)
    end = np.minimum(i - lo + width, len(x))
    return (c[end] - c[start]) / width


def _soft_limit(x: np.ndarray) -> np.ndarray:
    return np.tanh(1.6 * x) / 1.6


def _envelope(x: np.ndarray) -> np.ndarray:
    return _moving_average(np.abs(x), int(0.05 * SR))


def _active(bed_mono: np.ndarray) -> np.ndarray:
    """Where the bed sounds: its 50 ms envelope within _BED_ACTIVE_DB of its loudest."""
    env = _envelope(bed_mono)
    top = float(env.max()) if len(env) else 0.0
    return env > top * _db(_BED_ACTIVE_DB) if top > 0 else np.zeros(len(bed_mono), dtype=bool)


def _bed_gain(bed: np.ndarray, active: np.ndarray) -> float:
    """Gain putting the bed at BED_DBFS RMS where it sounds, as heard, i.e. after the soft limiter (a few
    fixed-point steps)."""
    mono = bed.mean(axis=1)[active]
    if not mono.any():
        return 1.0
    g = _db(BED_DBFS) / np.sqrt(np.mean(mono**2))
    for _ in range(12):
        heard = np.sqrt(np.mean(_soft_limit(mono * g) ** 2))
        g *= _db(BED_DBFS) / heard
    return float(g)


def _idx(frame: int, fps: int) -> int:
    """Sample index where 1-based frame `frame` starts."""
    return round((frame - 1) / fps * SR)


def _read_take(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as w:
        if (w.getframerate(), w.getnchannels(), w.getsampwidth()) != (SR, 1, 2):
            raise ValueError(
                f"{path.name}: take must be {SR} Hz mono 16-bit "
                f"(got {w.getframerate()} Hz, {w.getnchannels()} channel(s), {8 * w.getsampwidth()}-bit)"
            )
        return np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float64) / 32768


def _voice(tl: Timeline, takes_dir: Path | None, n: int) -> np.ndarray:
    vo = np.zeros(n)
    if takes_dir is None:
        return vo
    for span in tl.lines:
        path = Path(takes_dir) / f"{span.id}.wav"
        if not path.exists():
            continue
        x = _read_take(path)
        st = _idx(span.f0, tl.fps)
        x = x[: max(0, n - st)]
        vo[st : st + len(x)] += x
    return vo


def _bed(tl: Timeline, short: Short, n: int, extra_bed: np.ndarray | None, library=None,
         policy: str = "any") -> np.ndarray:  # fmt: skip
    shots = {s.id: s for s in tl.shots}
    bed = np.zeros(n)
    for fx in short.sfx:
        if fx.kind not in KINDS:
            raise ValueError(f"unknown sfx kind {fx.kind!r}; valid kinds: {', '.join(sorted(KINDS))}")
    uses = recorded.resolve(short, library, policy)
    for i, (fx, use) in enumerate(zip(short.sfx, uses, strict=True)):
        if fx.shot not in shots:
            raise ValueError(f"sfx {fx.kind!r} refers to unknown shot {fx.shot!r}")
        gain_db = 0.0
        if use["use"] == "skipped":
            continue
        if use["use"] == "recording":
            x, gain_db = recorded.prepare(library.samples(library.find(use["clip"])), fx.dur_s, seed=i)
        else:
            x = KINDS[fx.kind](fx.dur_s, seed=i)
        rms = np.sqrt(np.mean(x**2)) if len(x) else 0.0
        if rms == 0:
            continue
        st = _idx(shots[fx.shot].f0, tl.fps) + round(fx.at_s * SR)
        if st < 0:
            raise ValueError(f"sfx {fx.kind!r} in shot {fx.shot!r} starts before the video (at_s={fx.at_s})")
        x = (x / rms * _db(fx.level_db + gain_db))[: max(0, n - st)]
        bed[st : st + len(x)] += x
    stereo = np.stack([bed, bed], 1)
    if extra_bed is not None:
        eb = np.asarray(extra_bed, dtype=np.float64)
        if eb.ndim == 1:
            eb = np.stack([eb, eb], 1)
        m = min(n, len(eb))
        stereo[:m] += eb[:m]
    return stereo


def mix(tl: Timeline, short: Short, takes_dir: Path | None, extra_bed: np.ndarray | None = None, library=None,
        policy: str = "any", closing: tuple[int, Path | None, int | None] | None = None):  # fmt: skip
    """(stereo float64 mix in [-1, 1] of shape (n_samples, 2), levels). n_samples = round(n_frames / fps * SR).
    levels: voice_dbfs (the mix while speaking, None without a voice), bed_dbfs (the mix where the bed sounds clear of
    speech, else where it sounds; None without a bed) and peak_dbfs. `library` (bsa.audio.recorded.Library): sfx
    events play cached recordings of their kind, else synthesis; `policy` "natural-only" leaves out synthetic kinds
    and synthesis (recorded.resolve says what each event used). `closing` (frames, take or None, the take's first
    frame) adds the closing card's frames after the story (bsa.closing): the closing line in the voice, normalised
    with the story's lines, and no bed at all there (the card brings no sound of its own)."""
    n_story = round(tl.n_frames / tl.fps * SR)
    n = round((tl.n_frames + (closing[0] if closing else 0)) / tl.fps * SR)
    bed = _bed(tl, short, n, extra_bed, library, policy)
    bed[n_story:] = 0.0
    vo = _voice(tl, takes_dir, n)
    if closing and closing[1] is not None and closing[2] is not None:
        x = _read_take(Path(closing[1]))
        st = _idx(closing[2], tl.fps)
        x = x[: max(0, n - st)]
        vo[st : st + len(x)] += x

    speech = _envelope(vo) > _SPEECH_THRESHOLD
    if speech.any():
        vo *= _db(VOICE_DBFS) / np.sqrt(np.mean(vo[speech] ** 2))
    active = _active(bed.mean(axis=1))
    bed *= _bed_gain(bed, active)
    env = _moving_average(speech.astype(float), int(DUCK_S * SR))
    duck = 1 - (1 - _db(DUCK_DB)) * np.clip(env * 1.5, 0, 1)
    out = bed * duck[:, None] + vo[:, None]

    out = _soft_limit(out)
    peak = float(np.abs(out).max()) if n else 0.0
    if peak > _db(PEAK_DBFS):
        out *= _db(PEAK_DBFS) / peak
    return out, _levels(out, speech, active)


def render_mix(tl: Timeline, short: Short, takes_dir: Path | None, extra_bed: np.ndarray | None = None) -> np.ndarray:
    """The stereo mix alone (see `mix`)."""
    return mix(tl, short, takes_dir, extra_bed)[0]


def _levels(out: np.ndarray, speech: np.ndarray, active: np.ndarray) -> dict:
    mono = out.mean(axis=1)

    def level(where: np.ndarray):
        return _to_db(float(np.sqrt(np.mean(mono[where] ** 2)))) if where.any() else None

    clear = active & ~(_moving_average(speech.astype(float), int(DUCK_S * SR)) > 0)
    return {
        "voice_dbfs": level(speech),
        "bed_dbfs": level(clear) if clear.any() else level(active),
        "peak_dbfs": _to_db(float(np.abs(out).max()) if out.size else 0.0),
    }


def write_wav(path: Path, stereo: np.ndarray) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    pcm = (np.clip(stereo, -1.0, 1.0) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())
