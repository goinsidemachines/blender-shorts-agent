"""ElevenLabs text-to-speech over urllib. The API key is read from the argument or ELEVENLABS_API_KEY and is
never written anywhere."""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
import wave
from pathlib import Path

from bsa.narrate import contract
from bsa.narrate.contract import SR, NarrationError
from bsa.short import spoken_text

API = "https://api.elevenlabs.io/v1"
_sleep = time.sleep
_TIMEOUT_S = 120


def _key(api_key: str | None) -> str:
    key = api_key or os.environ.get("ELEVENLABS_API_KEY")
    if not key:
        raise NarrationError("no ElevenLabs API key (set ELEVENLABS_API_KEY)")
    return key


def _open(http, req: urllib.request.Request) -> bytes:
    resp = http(req, timeout=_TIMEOUT_S)
    try:
        return resp.read()
    finally:
        close = getattr(resp, "close", None)
        if close:
            close()


def _request(http, req: urllib.request.Request, what: str) -> bytes:
    """One retry on 429/5xx after a 2 s backoff. Error text never includes headers (the key lives there)."""
    for attempt in (1, 2):
        try:
            return _open(http, req)
        except urllib.error.HTTPError as e:
            if attempt == 1 and (e.code == 429 or e.code >= 500):
                _sleep(2)
                continue
            raise NarrationError(f"ElevenLabs refused {what} (HTTP {e.code})") from None
        except (urllib.error.URLError, OSError) as e:
            raise NarrationError(f"could not reach ElevenLabs for {what} ({type(e).__name__})") from None
    raise AssertionError("unreachable")


def _write_wav(path: Path, pcm: bytes) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm)


def generate(
    lines,
    voice_id: str,
    speed: float,
    out_dir: Path,
    model: str = "eleven_v3",
    api_key=None,
    http=urllib.request.urlopen,
) -> dict[str, float]:
    """Write <id>.wav for every line and durations.json; return seconds per line. A take already on disk that meets
    the contract is kept (an interrupted run resumes); a bad or empty one is made again. Each take is written to
    <id>.wav.part and renamed when complete, so an interrupted write never passes for a take."""
    key = _key(api_key)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    url = f"{API}/text-to-speech/{voice_id}?output_format=pcm_{SR}"
    for ln in lines:
        wav = out_dir / f"{ln.id}.wav"
        if wav.exists():
            if not contract.check_take(wav):
                continue
            wav.unlink()
        body = json.dumps({"text": spoken_text(ln), "model_id": model, "voice_settings": {"speed": speed}}).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=body,
            method="POST",
            headers={"xi-api-key": key, "Content-Type": "application/json", "Accept": "audio/pcm"},
        )
        pcm = _request(http, req, f"line {ln.id}")
        part = wav.with_name(wav.name + ".part")
        try:
            _write_wav(part, pcm[: len(pcm) - len(pcm) % 2])
            problems = contract.check_take(part)
            if problems:
                raise NarrationError("; ".join(p.replace(part.name, wav.name) for p in problems))
            part.replace(wav)
        finally:
            part.unlink(missing_ok=True)
    durations = {ln.id: round(contract.wav_seconds(out_dir / f"{ln.id}.wav"), 3) for ln in lines}
    (out_dir / "durations.json").write_text(json.dumps(durations, indent=2), encoding="utf-8")
    return durations


def list_voices(api_key=None, http=urllib.request.urlopen) -> list[dict]:
    req = urllib.request.Request(f"{API}/voices", headers={"xi-api-key": _key(api_key)})
    data = json.loads(_request(http, req, "the voice list"))
    return list(data.get("voices", []))
