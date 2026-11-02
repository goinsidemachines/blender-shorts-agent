"""Narrate a short. Never blocks: any failure means captions-only, said once, and the short finishes.

Providers are injectable for tests: pass `elevenlabs=`, `local=` and/or `custom=` to narrate(). `local` is any object with
`generate(model, lines, voice, speed, out_dir, seed=7)` and `hear(lines, out_dir)` or `verify(lines, out_dir)` (the
bsa.narrate.local module is the default; it also checks ElevenLabs and custom takes). `elevenlabs` is any object
with `generate(lines, voice_id, speed, out_dir)`, or such a callable itself
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
from bsa.narrate.contract import NarrationError, check_take, read_durations, voice_onset
from bsa.narrate.local import RETRIES, WER_LIMIT
from bsa.short import Short, spoken_text

LOCAL_ENGINES = ("kokoro", "chatterbox", "chatterbox-turbo")
HEARD_FILE = "heard.json"
RECORD_FILE = "narrator.json"
FIRST_SEED = 7
WHISPER_MISSING = (
    "Whisper isn't installed, so the takes weren't checked. CLI voice plan whisper-small.en shows what it downloads."
)
CHECK_OFF = "The voice check is turned off (narrator.verify false)."
CAPTIONS_ONLY = "This short uses captions only, so there is no voice to check."


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


def _reason(exc: Exception) -> str:
    return _scrub(str(exc) if isinstance(exc, NarrationError) else f"{type(exc).__name__}: {exc}").rstrip(".")


def _hear(mod, lines, out_dir) -> tuple[dict | None, str | None]:
    """(heard, why_not): what Whisper heard per line, {id: {"said", "wer"}}, from the checker's `hear`, else its
    `verify` (word error rates only, so "said" is None). Best-effort: a failure is a reason, never an error."""
    try:
        if getattr(mod, "hear", None) is not None:
            heard = mod.hear(lines, out_dir)
        else:
            wer = mod.verify(lines, out_dir)
            heard = None if wer is None else {i: {"said": None, "wer": float(w)} for i, w in wer.items()}
    except Exception as exc:  # noqa: BLE001 - the check is best-effort and must never block
        return None, f"The check couldn't run: {_reason(exc)}."
    return (heard, None) if heard is not None else (None, WHISPER_MISSING)


def _read_record(out_dir: Path) -> dict:
    try:
        data = json.loads((Path(out_dir) / RECORD_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    jobstate.write_atomic(path, json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8"))


def take_seeds(out_dir: Path) -> dict[str, int]:
    """The seed each line's take was (or is next to be) spoken with, from narrator.json; {} when there is none."""
    seeds = _read_record(out_dir).get("seeds")
    if not isinstance(seeds, dict):
        return {}
    return {str(k): v for k, v in seeds.items() if isinstance(v, int) and not isinstance(v, bool)}


def _record_seeds(out_dir: Path, seeds: dict[str, int]) -> None:
    record = _read_record(out_dir)
    record["seeds"] = {**take_seeds(out_dir), **seeds}
    _write_json(Path(out_dir) / RECORD_FILE, record)


def retake(out_dir: Path, line_id: str) -> int:
    """Throw one line's take away so the next narration speaks it again with the next seed (a different reading).
    Returns that seed."""
    out_dir = Path(out_dir)
    (out_dir / f"{line_id}.wav").unlink(missing_ok=True)
    dpath = out_dir / "durations.json"
    try:
        durations = json.loads(dpath.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        durations = None
    if isinstance(durations, dict) and line_id in durations:
        del durations[line_id]
        _write_json(dpath, durations)
    seed = take_seeds(out_dir).get(line_id, FIRST_SEED) + 1
    _record_seeds(out_dir, {line_id: seed})
    return seed


def write_heard(out_dir: Path, lines, heard: dict | None, why_not: str | None) -> None:
    """heard.json: per line what the voice was asked to say (`text`), what Whisper heard (`said`) and the word error
    rate; `checked` false, with `why_not`, when the takes weren't checked."""
    got = heard or {}
    data = {
        "checked": heard is not None,
        "why_not": why_not,
        "lines": {
            ln.id: {"text": spoken_text(ln), "said": (got.get(ln.id) or {}).get("said"),
                    "wer": (got.get(ln.id) or {}).get("wer")}
            for ln in lines
        },
    }  # fmt: skip
    try:
        _write_json(Path(out_dir) / HEARD_FILE, data)
    except OSError:
        pass  # the voice check's record is best-effort; it never costs the short its voice


def _unclear(heard: dict | None, ids) -> list[str]:
    return [i for i in ids if ((heard or {}).get(i) or {}).get("wer", 0.0) > WER_LIMIT]


def _unclear_message(ids: list[str]) -> str | None:
    if not ids:
        return None
    return f"Lines {', '.join(ids)} may not be spoken clearly; listen to them or reword before publishing."


def _spoken(short: Short, out_dir: Path, speak, hear, check) -> NarrationResult:
    """Only lines without a good take on disk are spoken (clear_stale_takes has already removed takes of another
    voice, speed or text), so an interrupted, re-taken or reworded run remakes only what it must. Each take is
    spoken with its line's seed (take_seeds: 7 for a first take, the next one after a retake) and every seed used
    is recorded; an unclear take is spoken again with its seed + 1, then + 2. `speak(lines, seed)` makes the takes
    and returns seconds per line; `hear(lines)` returns (heard, why_not) as _hear does. Writes heard.json."""
    seeds = take_seeds(out_dir)
    kept = [ln for ln in short.lines if (out_dir / f"{ln.id}.wav").is_file() and not check_take(out_dir / f"{ln.id}.wav")]
    todo = [ln for ln in short.lines if ln not in kept]
    durations = read_durations(out_dir, [ln.id for ln in kept]) if kept else {}
    by_seed: dict[int, list] = {}
    for ln in todo:
        by_seed.setdefault(seeds.get(ln.id, FIRST_SEED), []).append(ln)
    for seed, lns in by_seed.items():
        durations.update(speak(lns, seed))
        _record_seeds(out_dir, {ln.id: seed for ln in lns})
    seeds = take_seeds(out_dir)
    heard, why_not = hear(short.lines) if check else (None, CHECK_OFF)
    still_bad: list[str] = []
    by_id = {ln.id: ln for ln in short.lines}
    for lid in _unclear(heard, by_id):
        base = seeds.get(lid, FIRST_SEED)
        for step in range(1, RETRIES + 1):
            durations.update(speak([by_id[lid]], base + step))
            _record_seeds(out_dir, {lid: base + step})
            again = (hear([by_id[lid]])[0] or {}).get(lid)
            if again is not None:
                heard[lid] = again
            if (again or {}).get("wer", 0.0) <= WER_LIMIT:
                break
        else:
            still_bad.append(lid)
    write_heard(out_dir, short.lines, heard, why_not)
    return NarrationResult("ok", {ln.id: durations[ln.id] for ln in short.lines}, _unclear_message(still_bad))


def _local_narrate(provider: str, short: Short, narrator: dict, out_dir: Path, mod) -> NarrationResult:
    voice, speed = narrator.get("voice"), float(narrator.get("speed") or 1.0)
    gen = _fn(mod, "generate")
    return _spoken(
        short, out_dir,
        lambda lns, seed: gen(provider, lns, voice, speed, out_dir, seed=seed),
        lambda lns: _hear(mod, lns, out_dir),
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
        lambda lns: _hear(local, lns, out_dir),
        narrator.get("verify", True),
    )  # fmt: skip


def _elevenlabs_checked(short: Short, narrator: dict, out_dir: Path, durations: dict, local) -> NarrationResult:
    """ElevenLabs takes get the voice check too, once over every line: no retries, since each take costs credit."""
    heard, why_not = _hear(local, short.lines, out_dir) if narrator.get("verify", True) else (None, CHECK_OFF)
    write_heard(out_dir, short.lines, heard, why_not)
    return NarrationResult("ok", durations, _unclear_message(_unclear(heard, [ln.id for ln in short.lines])))


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
                result = _elevenlabs_checked(short, narrator, out_dir, dict(durations), providers.get("local", _local))
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
    if result.status != "ok":
        write_heard(out_dir, [], None, CAPTIONS_ONLY)
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
    record_path = out_dir / RECORD_FILE
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
    # a seed belongs to a take of this voice and these words, even while a retake waits for its take
    had_seeds = take_seeds(out_dir) if same_voice else {}
    want["seeds"] = {lid: n for lid, n in had_seeds.items() if old_lines.get(lid) == want["lines"].get(lid)}
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


def takes_onsets(short_dir: Path, short: Short) -> dict[str, float] | None:
    """Seconds of leading silence per take (contract.voice_onset), so each line's captions start with the voice,
    not with the take; None for a captions-only short. A missing or unreadable take counts as 0."""
    info = jobstate.load_state(short_dir)["stages"].get("narration") or {}
    if info.get("status") != "ok":
        return None
    vo = Path(short_dir) / "audio" / "vo"
    return {ln.id: voice_onset(vo / f"{ln.id}.wav") for ln in short.lines}
