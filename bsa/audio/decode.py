"""Reading recorded sound files as float samples at 48 kHz (numpy + stdlib only).

WAV (8/16/24/32-bit PCM and 32/64-bit float) is read with the stdlib. Everything else (OGG Vorbis, FLAC, MP3) is
decoded by Blender's own audio library `aud`: directly when this runs inside Blender (the preview chain runs `mix` there),
otherwise by a short background Blender run that writes a decoded 48 kHz WAV next to the file (`<name>.48k.wav`),
reused from then on.
"""

from __future__ import annotations

import struct
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np

SR = 48000
WAV_EXT = (".wav", ".wave")
DECODE_TIMEOUT_S = 120
_ROOT = Path(__file__).resolve().parent.parent.parent


def _pcm(raw: bytes, width: int, channels: int) -> np.ndarray:
    if width == 1:
        x = (np.frombuffer(raw, np.uint8).astype(np.float64) - 128) / 128
    elif width == 2:
        x = np.frombuffer(raw, "<i2").astype(np.float64) / 32768
    elif width == 3:
        b = np.frombuffer(raw, np.uint8).reshape(-1, 3).astype(np.int32)
        v = b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16)
        x = np.where(v >= 1 << 23, v - (1 << 24), v).astype(np.float64) / (1 << 23)
    elif width == 4:
        x = np.frombuffer(raw, "<i4").astype(np.float64) / 2147483648
    else:
        raise ValueError(f"unsupported sample width {width}")
    return x.reshape(-1, channels)


def _riff_chunks(data: bytes):
    if data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise ValueError("not a RIFF/WAVE file")
    pos = 12
    while pos + 8 <= len(data):
        cid, size = data[pos : pos + 4], struct.unpack("<I", data[pos + 4 : pos + 8])[0]
        yield cid, data[pos + 8 : pos + 8 + size]
        pos += 8 + size + (size & 1)


def read_wav(path) -> tuple[np.ndarray, int]:
    """(samples of shape (n, channels) in [-1, 1], sample rate) of a PCM or IEEE-float WAV file."""
    path = Path(path)
    try:
        with wave.open(str(path), "rb") as w:
            ch, width, sr = w.getnchannels(), w.getsampwidth(), w.getframerate()
            return _pcm(w.readframes(w.getnframes()), width, ch), sr
    except wave.Error:
        pass  # float or extensible WAV: read the chunks ourselves
    fmt = raw = None
    for cid, body in _riff_chunks(path.read_bytes()):
        if cid == b"fmt ":
            fmt = body
        elif cid == b"data":
            raw = body
    if fmt is None or raw is None:
        raise ValueError(f"{path.name}: WAV without fmt or data chunk")
    tag, ch, sr, _, _, bits = struct.unpack("<HHIIHH", fmt[:16])
    if tag == 0xFFFE and len(fmt) >= 26:  # WAVE_FORMAT_EXTENSIBLE: the real format is in the sub-format GUID
        tag = struct.unpack("<H", fmt[24:26])[0]
    if tag == 3 and bits in (32, 64):
        x = np.frombuffer(raw[: len(raw) - len(raw) % (bits // 8)], "<f4" if bits == 32 else "<f8").astype(np.float64)
        return x.reshape(-1, ch), sr
    if tag == 1:
        return _pcm(raw[: len(raw) - len(raw) % (ch * bits // 8)], bits // 8, ch), sr
    raise ValueError(f"{path.name}: unsupported WAV format {tag}")


def resample(x: np.ndarray, sr_from: float, sr_to: float) -> np.ndarray:
    """Band-limited (FFT) resampling along axis 0; n_out = round(n * sr_to / sr_from)."""
    n = x.shape[0]
    m = round(n * sr_to / sr_from)
    if m == n or n == 0:
        return x.copy()
    spec = np.fft.rfft(x, axis=0)
    keep = min(spec.shape[0], m // 2 + 1)
    out = np.zeros((m // 2 + 1, *x.shape[1:]), dtype=complex)
    out[:keep] = spec[:keep]
    return np.fft.irfft(out, m, axis=0) * (m / n)


def _aud():
    try:
        import aud  # Blender's audio library: only inside Blender

        return aud
    except ImportError:
        return None


def _find_blender():
    try:
        from bsa import locate, settings

        return locate.find_blender(settings.load_settings())
    except Exception:  # noqa: BLE001 - no Blender: the caller says so plainly
        return None


def _decode_aud(aud, path: Path) -> tuple[np.ndarray, int]:
    snd = aud.Sound(str(path))
    rate = int(snd.specs[0])
    data = np.asarray(snd.data(), dtype=np.float64)
    return (data if data.ndim == 2 else data[:, None]), rate


def write_wav(path, x: np.ndarray, sr: int = SR) -> None:
    """16-bit PCM WAV of (n,) or (n, channels) samples in [-1, 1]."""
    x = x if x.ndim == 2 else x[:, None]
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with wave.open(str(tmp), "wb") as w:
        w.setnchannels(x.shape[1])
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((np.clip(x, -1, 1) * 32767).astype("<i2").tobytes())
    tmp.replace(path)


def _decode_with_blender(src: Path, dst: Path) -> None:
    blender = _find_blender()
    if blender is None:
        raise RuntimeError(f"{src.name} is not a WAV file, and decoding it needs Blender, which was not found: run "
                           "CLI where, or convert the file to WAV")  # fmt: skip
    expr = (f"import sys; sys.path.insert(0, {str(_ROOT)!r}); from bsa.audio import decode; "
            f"decode.blender_decode({str(src)!r}, {str(dst)!r})")  # fmt: skip
    cmd = [str(blender), "-b", "--factory-startup", "--python-exit-code", "1", "--python-expr", expr]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=DECODE_TIMEOUT_S, check=False)
    if result.returncode != 0 or not dst.is_file():
        tail = (result.stderr or result.stdout).strip().splitlines()[-1:] or ["no output"]
        raise RuntimeError(f"Blender could not decode {src.name}: {tail[0]}")


def blender_decode(src: str, dst: str) -> None:
    """Inside Blender: decode `src` with aud and write it as a 48 kHz 16-bit WAV at `dst`."""
    data, rate = _decode_aud(_aud(), Path(src))
    write_wav(dst, resample(data, rate, SR) if rate != SR else data)


def sidecar(path: Path) -> Path:
    return path.with_name(path.name + ".48k.wav")


def load(path, sr: int = SR) -> np.ndarray:
    """Samples of a sound file as (n, channels) float64 at `sr`."""
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"no sound file at {path}")
    if path.suffix.lower() in WAV_EXT:
        data, rate = read_wav(path)
    else:
        aud = _aud()
        side = sidecar(path)
        if aud is not None:
            data, rate = _decode_aud(aud, path)
        else:
            if not (side.is_file() and side.stat().st_mtime >= path.stat().st_mtime):
                _decode_with_blender(path, side)
            data, rate = read_wav(side)
    return resample(data, rate, sr) if rate != sr else data


if __name__ == "__main__" and "--" in sys.argv:  # blender -b --python decode.py -- SRC DST
    blender_decode(*sys.argv[sys.argv.index("--") + 1 :][:2])
