"""A speech-rate guard that needs no Whisper. A narrator keeps one pace through a short, so a line whose letters per
second of speech is far from the short's median was probably cut at the wrong place: too slow means it lost words to
its neighbour but kept their time, too fast means it gained words. Stdlib and numpy only."""

from __future__ import annotations

import wave
from pathlib import Path

import numpy as np

from bsa.narrate import takes as _takes

RATE_LOW = 0.65  # below this share of the short's median rate: too slow for its words
RATE_HIGH = 1.5  # above this share: too fast for its words
MIN_LETTERS = 12  # shorter lines vary too much to judge
MIN_LINES = 4  # a median needs a few lines to mean anything


def letters(text: str) -> int:
    """Letters and digits in the spoken text (spaces and punctuation don't take speaking time)."""
    return sum(1 for c in text if c.isalnum())


def _samples(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as w:
        sr = w.getframerate()
        data = w.readframes(w.getnframes())
    return np.frombuffer(data, dtype="<i2").astype(np.float64) / 32768.0, sr


def speech_seconds(path: Path) -> float | None:
    """Seconds of speech in a take, leading and trailing quiet trimmed; None when it can't be read."""
    try:
        x, sr = _samples(Path(path))
    except (OSError, EOFError, wave.Error, ValueError):
        return None
    if len(x) == 0:
        return None
    a, b = _takes._speech_span(x, sr)
    return (b - a) / sr if b > a else None


def rates(out_dir: Path, texts: dict[str, str]) -> dict[str, tuple[float, int]]:
    """{line id: (letters per speech second, letters)} for each line whose take can be read."""
    found = {}
    for lid, text in texts.items():
        n = letters(text)
        secs = speech_seconds(Path(out_dir) / f"{lid}.wav")
        if n and secs:
            found[lid] = (n / secs, n)
    return found


def outliers(measured: dict[str, tuple[float, int]]) -> dict[str, tuple[float, str]]:
    """{line id: (ratio to the median rate, "slow" | "fast")} for the lines outside the band. The median is of the
    lines long enough to judge; with too few of them nothing is flagged."""
    judged = {i: v for i, v in measured.items() if v[1] >= MIN_LETTERS}
    if len(judged) < MIN_LINES:
        return {}
    median = float(np.median([r for r, _ in judged.values()]))
    out = {}
    for lid, (rate, _) in judged.items():
        ratio = rate / median
        if ratio < RATE_LOW:
            out[lid] = (ratio, "slow")
        elif ratio > RATE_HIGH:
            out[lid] = (ratio, "fast")
    return out
