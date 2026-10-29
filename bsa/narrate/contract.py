"""The narrator contract: takes are 48 kHz mono 16-bit WAVs, one per line, plus durations.json."""

from __future__ import annotations

import json
import math
import wave
from pathlib import Path

import numpy as np

from bsa.short import Line, spoken_text

SR = 48000
SILENCE_DBFS = -50.0
DURATION_TOLERANCE_S = 0.05


class NarrationError(Exception):
    """A narrator failed; the short continues with captions only."""


def write_lines(path: Path, lines: list[Line]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    """The lines JSON a voice engine (and the Whisper check) reads: each line's spoken words (`say`, else `text`)."""
    path.write_text(json.dumps([{"id": ln.id, "text": spoken_text(ln)} for ln in lines], ensure_ascii=False),
                    encoding="utf-8")  # fmt: skip


def check_take(path: Path) -> list[str]:
    """Problems with a take; empty means it meets the contract."""
    path = Path(path)
    try:
        with wave.open(str(path), "rb") as w:
            rate, channels, width, frames = w.getframerate(), w.getnchannels(), w.getsampwidth(), w.getnframes()
            data = w.readframes(frames)
    except (wave.Error, EOFError, OSError) as e:
        return [f"{path.name}: not a readable WAV ({e})"]
    problems = []
    if rate != SR:
        problems.append(f"{path.name}: sample rate is {rate}, must be {SR}")
    if channels != 1:
        problems.append(f"{path.name}: has {channels} channels, must be mono")
    if width != 2:
        problems.append(f"{path.name}: is {width * 8}-bit, must be 16-bit")
    if frames == 0:
        problems.append(f"{path.name}: is empty")
    elif width == 2:
        samples = np.frombuffer(data, dtype="<i2").astype(np.float64) / 32768.0
        rms = float(np.sqrt(np.mean(samples**2)))
        db = 20 * math.log10(rms) if rms > 0 else -math.inf
        if db <= SILENCE_DBFS:
            problems.append(f"{path.name}: is silent ({db:.0f} dBFS)")
    return problems


def wav_seconds(path: Path) -> float:
    with wave.open(str(path), "rb") as w:
        return w.getnframes() / w.getframerate()


def read_durations(out_dir: Path, ids: list[str]) -> dict[str, float]:
    """Seconds per line from durations.json, cross-checked against the WAV lengths (±0.05 s).

    A line missing from durations.json (e.g. the runner rewrote it for one regenerated line) is measured from its WAV.
    """
    out_dir = Path(out_dir)
    listed: dict = {}
    jpath = out_dir / "durations.json"
    if jpath.exists():
        try:
            listed = json.loads(jpath.read_text(encoding="utf-8"))
        except ValueError as e:
            raise NarrationError(f"durations.json is not valid JSON ({e})") from e
    result: dict[str, float] = {}
    for i in ids:
        wav = out_dir / f"{i}.wav"
        if not wav.exists():
            raise NarrationError(f"take {i}.wav is missing")
        actual = wav_seconds(wav)
        if i in listed:
            claimed = float(listed[i])
            if abs(claimed - actual) > DURATION_TOLERANCE_S:
                raise NarrationError(f"durations.json says {i} is {claimed:.2f}s but {i}.wav is {actual:.2f}s")
            result[i] = claimed
        else:
            result[i] = round(actual, 3)
    return result
