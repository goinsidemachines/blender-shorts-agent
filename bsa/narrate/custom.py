"""Bring your own narrator: the user's own voice tool, run as one saved command line.

The command has placeholders {lines} (a lines JSON file), {out} (the folder for the takes), {seed}, and optionally
{voice} and {speed}. It must write <out>/<id>.wav for every line. Whatever WAV it writes is normalised to the
contract (48 kHz mono 16-bit). The command is run without a shell, so a path with spaces stays one argument.
"""

from __future__ import annotations

import struct
import subprocess
import wave
from pathlib import Path

import numpy as np

from bsa.messages import trim
from bsa.narrate import contract
from bsa.narrate.contract import SR, NarrationError
from bsa.settings import split_command

_START_HINT = "put the program that runs it first, e.g. python script.py"
_ADAPTER_HINT = "ask Claude to write a small adapter that turns your tool's output into WAV files"


def _tail(proc) -> str:
    text = (proc.stderr or "").strip() or (proc.stdout or "").strip()
    return trim(text.splitlines()[-1]) if text else "no output"


def _argv(command: str, values: dict[str, str | None]) -> list[str]:
    try:
        parts = split_command(command)
    except ValueError as e:
        raise NarrationError(f"the voice command can't be read ({e})") from e
    if not parts:
        raise NarrationError("no voice command has been saved")
    out = []
    for part in parts:
        for key, val in values.items():
            if f"{{{key}}}" in part:
                if val is None:
                    raise NarrationError(f"the voice command uses {{{key}}} but no narrator.{key} is set")
                part = part.replace(f"{{{key}}}", val)
        out.append(part)
    return out


def generate(
    command: str, lines, out_dir: Path, seed: int = 7, voice=None, speed: float = 1.0, run=subprocess.run
) -> dict[str, float]:
    """Run the command for `lines` and return seconds per line, measured from the (normalised) WAVs."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    lines_file = out_dir / "lines.json"
    contract.write_lines(lines_file, lines)
    argv = _argv(
        command,
        {
            "lines": str(lines_file),
            "out": str(out_dir),
            "seed": str(seed),
            "voice": str(voice) if voice else None,
            "speed": str(speed),
        },
    )
    for ln in lines:  # an old take must not pass for the tool's new one
        (out_dir / f"{ln.id}.wav").unlink(missing_ok=True)
    try:
        proc = run(
            argv, check=False, capture_output=True, text=True, errors="replace", stdin=subprocess.DEVNULL
        )  # stdin closed: a tool that prompts must fail, not hang
    except FileNotFoundError as e:
        raise NarrationError(
            f"the voice command {Path(argv[0]).name!r} was not found ({e}) - {_START_HINT}"
        ) from e
    except OSError as e:
        raise NarrationError(
            f"couldn't start {Path(argv[0]).name}: {e} - {_START_HINT}"
        ) from e
    if proc.returncode != 0:
        raise NarrationError(f"your voice tool failed (exit {proc.returncode}): {_tail(proc)}")
    (out_dir / "durations.json").unlink(missing_ok=True)  # durations come from the WAVs, never from the tool
    for ln in lines:
        take = out_dir / f"{ln.id}.wav"
        if not take.is_file():
            raise NarrationError(f"your voice tool did not write {ln.id}.wav")
        normalise_take(take)
        problems = contract.check_take(take)
        if problems:
            raise NarrationError("; ".join(problems))
    return {ln.id: round(contract.wav_seconds(out_dir / f"{ln.id}.wav"), 3) for ln in lines}


def _read_wav(path: Path) -> tuple[np.ndarray, int]:
    """(samples as floats in -1..1, shape (frames, channels); sample rate). PCM 8/16/24/32-bit or float32/64. The
    stdlib wave module can't read float or extensible WAVs, so the chunks are read here."""
    raw = path.read_bytes()
    if raw[:4] not in (b"RIFF", b"RF64") or raw[8:12] != b"WAVE":
        raise NarrationError(f"{path.name} is not a WAV file; for a tool that writes MP3 or similar, {_ADAPTER_HINT}")
    fmt, data, pos = None, None, 12
    while pos + 8 <= len(raw):
        cid, size = raw[pos : pos + 4], struct.unpack("<I", raw[pos + 4 : pos + 8])[0]
        if cid == b"fmt ":
            fmt = raw[pos + 8 : pos + 8 + size]
        elif cid == b"data":
            end = len(raw) if size == 0xFFFFFFFF or pos + 8 + size > len(raw) else pos + 8 + size
            data = raw[pos + 8 : end]
            break
        pos += 8 + size + (size & 1)
    if fmt is None or data is None or len(fmt) < 16:
        raise NarrationError(f"{path.name} is not a complete WAV file; {_ADAPTER_HINT}")
    tag, channels, rate, _, _, bits = struct.unpack("<HHIIHH", fmt[:16])
    if tag == 0xFFFE and len(fmt) >= 26:  # WAVE_FORMAT_EXTENSIBLE: the real format is the sub-format's first word
        tag = struct.unpack("<H", fmt[24:26])[0]
    if channels < 1 or rate < 1 or bits < 8 or bits % 8:
        raise NarrationError(f"{path.name} is not a WAV I can read (broken header); {_ADAPTER_HINT}")
    data = data[: len(data) - len(data) % (bits // 8 * channels)]
    if tag == 1 and bits == 8:
        x = (np.frombuffer(data, dtype=np.uint8).astype(np.float64) - 128.0) / 128.0
    elif tag == 1 and bits == 16:
        x = np.frombuffer(data, dtype="<i2").astype(np.float64) / 32768.0
    elif tag == 1 and bits == 24:
        b = np.frombuffer(data, dtype=np.uint8).reshape(-1, 3).astype(np.int32)
        v = b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16)
        x = np.where(v >= 1 << 23, v - (1 << 24), v).astype(np.float64) / 8388608.0
    elif tag == 1 and bits == 32:
        x = np.frombuffer(data, dtype="<i4").astype(np.float64) / 2147483648.0
    elif tag == 3 and bits in (32, 64):
        x = np.frombuffer(data, dtype="<f4" if bits == 32 else "<f8").astype(np.float64)
    else:
        raise NarrationError(f"{path.name} is a WAV format this plugin can't read (tag {tag}, {bits}-bit); {_ADAPTER_HINT}")
    return x.reshape(-1, channels), rate


def _resample(x: np.ndarray, rate: int) -> np.ndarray:
    """FFT resampling (numpy only): keeps the spectrum below the lower Nyquist, so downsampling is band-limited."""
    n = len(x)
    m = round(n * SR / rate)
    spec = np.fft.rfft(x)
    out = np.zeros(m // 2 + 1, dtype=complex)
    k = min(len(spec), len(out))
    out[:k] = spec[:k]
    return np.fft.irfft(out, m) * (m / n)


def normalise_take(path: Path) -> None:
    """Rewrite a WAV take in place as 48 kHz mono 16-bit."""
    path = Path(path)
    try:
        x, rate = _read_wav(path)
    except OSError as e:
        raise NarrationError(f"{path.name} can't be read ({e})") from e
    mono = np.nan_to_num(x, nan=0.0, posinf=1.0, neginf=-1.0).mean(axis=1)
    if len(mono) == 0:
        raise NarrationError(f"{path.name} is empty")
    if rate != SR:
        mono = _resample(mono, rate)
    pcm = np.clip(np.round(mono * 32767.0), -32768, 32767).astype("<i2")
    tmp = path.with_name(path.name + ".part")
    with wave.open(str(tmp), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())
    tmp.replace(path)
