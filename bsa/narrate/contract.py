"""The narrator contract: takes are 48 kHz mono 16-bit WAVs, one per line, plus durations.json."""

from __future__ import annotations

import json
import math
import os
import wave
from pathlib import Path

import numpy as np

from bsa import jobstate
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


ONSET_WINDOW_S = 0.01
ONSET_BELOW_PEAK_DB = 35.0  # the voice starts at the first window within this many dB of the loudest one
ONSET_FLOOR_DBFS = -55.0  # never count anything quieter than this as voice
ONSET_PREROLL_S = 0.05  # captions show this much before the first sound, so they never trail the word


def voice_onset(path: Path) -> float:
    """Seconds of leading silence in a take (where the voice starts, less a small pre-roll): captions wait for it,
    the mix still plays the take from its start. 0.0 for a take that starts at once or can't be read."""
    try:
        with wave.open(str(path), "rb") as w:
            rate, width, channels = w.getframerate(), w.getsampwidth(), w.getnchannels()
            data = w.readframes(w.getnframes())
    except (wave.Error, EOFError, OSError):
        return 0.0
    if width != 2 or rate <= 0 or channels < 1:
        return 0.0
    x = np.frombuffer(data[: len(data) - len(data) % (2 * channels)], dtype="<i2").astype(np.float64) / 32768.0
    x = x.reshape(-1, channels).mean(axis=1)
    win = max(1, round(ONSET_WINDOW_S * rate))
    n = len(x) // win
    if n == 0:
        return 0.0
    rms = np.sqrt(np.mean(x[: n * win].reshape(n, win) ** 2, axis=1))
    peak = float(rms.max())
    threshold = max(peak * 10 ** (-ONSET_BELOW_PEAK_DB / 20), 10 ** (ONSET_FLOOR_DBFS / 20))
    loud = np.nonzero(rms >= threshold)[0]
    if len(loud) == 0:
        return 0.0
    return round(max(0.0, loud[0] * win / rate - ONSET_PREROLL_S), 3)


LEAD_BELOW_PEAK_DB = 50.0  # more sensitive than the caption onset, so a soft first consonant is never silence
LEAD_FLOOR_DBFS = -60.0


def leading_silence(path: Path) -> float:
    """Seconds before the first 10 ms window within LEAD_BELOW_PEAK_DB of the loudest one (and above
    LEAD_FLOOR_DBFS); no pre-roll. 0.0 for a take that can't be read or is silent."""
    try:
        with wave.open(str(path), "rb") as w:
            rate, width, channels = w.getframerate(), w.getsampwidth(), w.getnchannels()
            data = w.readframes(w.getnframes())
    except (wave.Error, EOFError, OSError):
        return 0.0
    if width != 2 or rate <= 0 or channels < 1:
        return 0.0
    x = np.frombuffer(data[: len(data) - len(data) % (2 * channels)], dtype="<i2").astype(np.float64) / 32768.0
    x = x.reshape(-1, channels).mean(axis=1)
    win = max(1, round(ONSET_WINDOW_S * rate))
    n = len(x) // win
    if n == 0:
        return 0.0
    rms = np.sqrt(np.mean(x[: n * win].reshape(n, win) ** 2, axis=1))
    threshold = max(float(rms.max()) * 10 ** (-LEAD_BELOW_PEAK_DB / 20), 10 ** (LEAD_FLOOR_DBFS / 20))
    loud = np.nonzero(rms >= threshold)[0]
    if len(loud) == 0:
        return 0.0
    return round(loud[0] * win / rate, 3)


def pad_lead(path: Path, lead_in: float) -> float:
    """Prepend the silence a take is missing so it has `lead_in` seconds before its first word. Returns the seconds
    added; 0.0 when it already has that much (a take is never trimmed) or can't be read."""
    path = Path(path)
    missing = round((lead_in - leading_silence(path)) * SR)
    if missing <= 0:
        return 0.0
    try:
        with wave.open(str(path), "rb") as w:
            params, data = w.getparams(), w.readframes(w.getnframes())
    except (wave.Error, EOFError, OSError):
        return 0.0
    part = path.with_name(path.name + ".part")
    try:
        with wave.open(str(part), "wb") as w:
            w.setparams(params)
            w.writeframes(bytes(1) * (2 * params.nchannels * missing) + data)
        os.replace(part, path)
    finally:
        part.unlink(missing_ok=True)
    return missing / SR


def forget_durations(out_dir: Path, ids) -> None:
    """Drop these ids from durations.json (atomically), so read_durations measures them from their WAVs."""
    jpath = Path(out_dir) / "durations.json"
    try:
        listed = json.loads(jpath.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    if not isinstance(listed, dict) or not any(i in listed for i in ids):
        return
    for i in ids:
        listed.pop(i, None)
    jobstate.write_atomic(jpath, json.dumps(listed, indent=2).encode("utf-8"))


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
