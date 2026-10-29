"""Narrate a short. Never blocks: any failure means captions-only, said once, and the short finishes.

Providers are injectable for tests: pass `elevenlabs=`, `local=` and/or `custom=` to narrate(). `local` is any object with
`generate(model, lines, voice, speed, out_dir, seed=7)` and `verify(lines, out_dir)` (the bsa.narrate.local module is
the default). `elevenlabs` is any object with `generate(lines, voice_id, speed, out_dir)`, or such a callable itself
(the bsa.narrate.elevenlabs module is the default; its api_key comes from ELEVENLABS_API_KEY). `custom` is any
object with `generate(command, lines, out_dir, seed=7, voice=None, speed=1.0)` (bsa.narrate.custom is the default).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from bsa import jobstate
from bsa.narrate import custom as _custom
from bsa.narrate import elevenlabs as _elevenlabs
from bsa.narrate import local as _local
from bsa.narrate.contract import NarrationError, check_take, read_durations
from bsa.short import Short, spoken_text

LOCAL_ENGINES = ("kokoro", "chatterbox", "chatterbox-turbo")


@dataclass
class NarrationResult:
    status: str  # "ok" | "captions-only"
    durations: dict | None = None
    message: str | None = None


def _fn(provider, name: str):
    return getattr(provider, name, None) or (provider if callable(provider) else None)


def _scrub(text: str) -> str:
    key = os.environ.get("ELEVENLABS_API_KEY")
    return text.replace(key, "***") if key else text


def _fallback(provider, exc: Exception) -> NarrationResult:
    reason = str(exc) if isinstance(exc, NarrationError) else f"{type(exc).__name__}: {exc}"
    name = {"custom": "voice tool", "elevenlabs": "ElevenLabs"}.get(provider, provider)
    msg = (
        f"The {name} narration didn't work ({_scrub(reason).rstrip('.')}), "
        "so this short uses captions only; you can add a voice later."
    )
    return NarrationResult("captions-only", None, msg)


def _verify(mod, lines, out_dir):
    try:
        return mod.verify(lines, out_dir)
    except Exception:  # noqa: BLE001 - verification is best-effort and must never block
        return None


def _spoken(short: Short, out_dir: Path, speak, verify, check) -> NarrationResult:
    """Only lines without a good take on disk are spoken (clear_stale_takes has already removed takes of another
    voice, speed or text), so an interrupted or reworded run remakes only what it must. `speak(lines, seed)` makes
    the takes and returns seconds per line; `verify(lines)` returns Whisper word error rates or None."""
    kept = [ln for ln in short.lines if (out_dir / f"{ln.id}.wav").is_file() and not check_take(out_dir / f"{ln.id}.wav")]
    todo = [ln for ln in short.lines if ln not in kept]
    durations = read_durations(out_dir, [ln.id for ln in kept]) if kept else {}
    if todo:
        durations.update(speak(todo, 7))
    wer = verify(short.lines) if check else None
    still_bad: list[str] = []
    if wer:
        by_id = {ln.id: ln for ln in short.lines}
        for lid in [i for i in by_id if wer.get(i, 0.0) > _local.WER_LIMIT]:
            for seed in _local.RETRY_SEEDS:
                durations.update(speak([by_id[lid]], seed))
                score = (verify([by_id[lid]]) or {}).get(lid, 0.0)
                if score <= _local.WER_LIMIT:
                    break
            else:
                still_bad.append(lid)
    message = None
    if still_bad:
        message = f"Lines {', '.join(still_bad)} may not be spoken clearly; listen to them or reword before publishing."
    return NarrationResult("ok", {ln.id: durations[ln.id] for ln in short.lines}, message)


def _local_narrate(provider: str, short: Short, narrator: dict, out_dir: Path, mod) -> NarrationResult:
    voice, speed = narrator.get("voice"), float(narrator.get("speed") or 1.0)
    gen = _fn(mod, "generate")
    return _spoken(
        short, out_dir,
        lambda lns, seed: gen(provider, lns, voice, speed, out_dir, seed=seed),
        lambda lns: _verify(mod, lns, out_dir),
        narrator.get("verify", True),
    )  # fmt: skip


def _custom_narrate(short: Short, narrator: dict, out_dir: Path, mod, local) -> NarrationResult:
    """The user's own voice tool; checked with Whisper only when the plugin's own runner has it installed."""
    command = str(narrator.get("command") or "").strip()
    if not command:
        raise NarrationError("no voice command has been saved")
    voice, speed = narrator.get("voice"), float(narrator.get("speed") or 1.0)
    gen = _fn(mod, "generate")
    return _spoken(
        short, out_dir,
        lambda lns, seed: gen(command, lns, out_dir, seed=seed, voice=voice, speed=speed),
        lambda lns: _verify(local, lns, out_dir),
        narrator.get("verify", True),
    )  # fmt: skip


def narrate(short_dir: Path, short: Short, settings: dict, **providers) -> NarrationResult:
    short_dir = Path(short_dir)
    narrator = settings.get("narrator") or {}
    provider = narrator.get("provider")
    out_dir = short_dir / "audio" / "vo"
    if provider in (None, "none"):
        result = NarrationResult("captions-only")
    else:
        try:
            if provider == "elevenlabs":
                if not narrator.get("voice"):
                    raise NarrationError("no ElevenLabs voice has been chosen")
                gen = _fn(providers.get("elevenlabs", _elevenlabs), "generate")
                durations = gen(short.lines, narrator["voice"], float(narrator.get("speed") or 1.0), out_dir)
                result = NarrationResult("ok", dict(durations))
            elif provider in LOCAL_ENGINES:
                result = _local_narrate(provider, short, narrator, out_dir, providers.get("local", _local))
            elif provider == "custom":
                result = _custom_narrate(
                    short, narrator, out_dir, providers.get("custom", _custom), providers.get("local", _local)
                )
            else:
                raise NarrationError(f"unknown narrator {provider!r}")
        except Exception as exc:  # noqa: BLE001 - narration never blocks the short
            result = _fallback(provider, exc)
    info = {"status": result.status, "provider": provider}
    if result.message:
        info["message"] = result.message
    jobstate.mark_done(short_dir, "narration", **info)
    return result


def _takes_record(short: Short, narrator: dict) -> dict:
    return {
        "command": (narrator.get("command") or None) if narrator.get("provider") == "custom" else None,
        "provider": narrator.get("provider"),
        "voice": narrator.get("voice"),
        "speed": float(narrator.get("speed") or 1.0),
        "lines": {ln.id: spoken_text(ln) for ln in short.lines},  # a take is stale when its spoken words change
    }


def clear_stale_takes(out_dir: Path, short: Short, narrator: dict) -> list[str]:
    """Delete takes made by another narrator, voice or speed, or for a line whose text has changed since, and
    record what the takes left on disk are for. Takes still current are kept, so an interrupted run resumes.
    Returns the ids of the takes deleted."""
    out_dir = Path(out_dir)
    record_path = out_dir / "narrator.json"
    want = _takes_record(short, narrator)
    try:
        had = json.loads(record_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        had = None
    same_voice = isinstance(had, dict) and all(had.get(k) == want[k] for k in ("provider", "voice", "speed", "command"))
    old_lines = had.get("lines", {}) if same_voice else {}
    removed = []
    for wav in sorted(out_dir.glob("*.wav")):
        lid = wav.stem
        if not same_voice or old_lines.get(lid) != want["lines"].get(lid):
            wav.unlink()
            removed.append(lid)
    if removed:
        (out_dir / "durations.json").unlink(missing_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    record_path.write_text(json.dumps(want, indent=2, ensure_ascii=False), encoding="utf-8")
    return removed


def takes_durations(short_dir: Path, short: Short) -> dict[str, float] | None:
    """Seconds per line from the takes when narration finished with a voice; None for a captions-only short."""
    info = jobstate.load_state(short_dir)["stages"].get("narration") or {}
    if info.get("status") != "ok":
        return None
    try:
        return read_durations(Path(short_dir) / "audio" / "vo", [ln.id for ln in short.lines])
    except NarrationError as exc:
        raise NarrationError(f"{exc}; run narration again (state SHORT reset narration, then narrate SHORT)") from exc
