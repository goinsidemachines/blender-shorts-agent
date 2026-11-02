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
import math
import os
import re
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
    name = {"custom": "voice tool", "elevenlabs": "ElevenLabs"}.get(provider, provider)
    msg = (
        f"The {name} narration didn't work ({_reason(exc)}), "
        "so this short uses captions only; you can add a voice later."
    )
    return NarrationResult("captions-only", None, msg)


def _reason(exc: Exception) -> str:
    return _scrub(str(exc) if isinstance(exc, NarrationError) else f"{type(exc).__name__}: {exc}").rstrip(".")


def _wer(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return float(value)


def _heard_entries(raw: dict, from_hear: bool) -> dict[str, dict]:
    """The checker's output, coerced: an entry that isn't a dict (or a number, from verify) or whose word error rate
    isn't a number is left out, so that line counts as unchecked and never costs the voice."""
    out = {}
    for i, v in raw.items():
        entry = v if from_hear else {"said": None, "wer": v}
        if not isinstance(entry, dict) or _wer(entry.get("wer")) is None:
            continue
        said = entry.get("said")
        out[str(i)] = {"said": said if isinstance(said, str) else None, "wer": _wer(entry.get("wer"))}
    return out


def _hear(mod, lines, out_dir) -> tuple[dict | None, str | None]:
    """(heard, why_not): what Whisper heard per line, {id: {"said", "wer"}}, from the checker's `hear`, else its
    `verify` (word error rates only, so "said" is None). Best-effort: a failure is a reason, never an error."""
    from_hear = getattr(mod, "hear", None) is not None
    try:
        raw = mod.hear(lines, out_dir) if from_hear else mod.verify(lines, out_dir)
    except Exception as exc:  # noqa: BLE001 - the check is best-effort and must never block
        return None, f"The check couldn't run: {_reason(exc)}."
    if raw is None:
        return None, WHISPER_MISSING
    if not isinstance(raw, dict):
        return None, f"The check couldn't run: it returned {type(raw).__name__}, not a result per line."
    return _heard_entries(raw, from_hear), None


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


def _record_seeds(out_dir: Path, seeds: dict[str, int]) -> str | None:
    """Record the seeds the takes were spoken with. Best-effort: returns why it couldn't, never raises OSError."""
    record = _read_record(out_dir)
    record["seeds"] = {**take_seeds(out_dir), **seeds}
    try:
        _write_json(Path(out_dir) / RECORD_FILE, record)
    except OSError as exc:
        return f"The takes' seeds couldn't be saved ({exc}), so a re-take may repeat a reading."
    return None


# short.validate puts no limit on a line id's characters, so any id it allows is a take's file name here, except one
# that could leave the folder: no path separator, no control character, no "..".
_SAFE_ID = re.compile(r"[^/\\\x00-\x1f]+")


def retake(out_dir: Path, line_id: str) -> int:
    """Throw one line's take away so the next narration speaks it again with the next seed (a different reading).
    Returns that seed. Refuses (ValueError, touching nothing) an id that isn't a plain line id, or isn't one of the
    lines narrator.json records the takes are for."""
    out_dir = Path(out_dir)
    known = _read_record(out_dir).get("lines")
    if not isinstance(line_id, str) or not _SAFE_ID.fullmatch(line_id) or ".." in line_id:
        raise ValueError(f"{line_id!r} is not a line id")
    if isinstance(known, dict) and line_id not in known:
        raise ValueError(f"{line_id!r} is not a line of this short (its lines: {', '.join(known) or 'none'})")
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
    problem = _record_seeds(out_dir, {line_id: seed})
    if problem:
        raise OSError(problem)  # a re-take is only worth its new seed
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
    is recorded; an unclear take spoken in this call is spoken again with its seed + 1, then + 2. An unclear take
    kept from an earlier run already had its retries: it is reported, not spoken again. `speak(lines, seed)` makes
    the takes and returns seconds per line; `hear(lines)` returns (heard, why_not) as _hear does. Writes
    heard.json."""
    seeds = take_seeds(out_dir)
    kept = [ln for ln in short.lines if (out_dir / f"{ln.id}.wav").is_file() and not check_take(out_dir / f"{ln.id}.wav")]
    todo = [ln for ln in short.lines if ln not in kept]
    durations = read_durations(out_dir, [ln.id for ln in kept]) if kept else {}
    by_seed: dict[int, list] = {}
    for ln in todo:
        by_seed.setdefault(seeds.get(ln.id, FIRST_SEED), []).append(ln)
    notes: list[str] = []
    used: dict[str, int] = {}
    for seed, lns in by_seed.items():
        durations.update(speak(lns, seed))
        used.update({ln.id: seed for ln in lns})
        notes.append(_record_seeds(out_dir, {ln.id: seed for ln in lns}))
    heard, why_not = hear(short.lines) if check else (None, CHECK_OFF)
    still_bad: list[str] = []
    by_id = {ln.id: ln for ln in short.lines}
    for lid in _unclear(heard, by_id):
        if lid not in used:  # kept from an earlier run, which already retried it
            still_bad.append(lid)
            continue
        base = used[lid]
        for step in range(1, RETRIES + 1):
            durations.update(speak([by_id[lid]], base + step))
            notes.append(_record_seeds(out_dir, {lid: base + step}))
            again = (hear([by_id[lid]])[0] or {}).get(lid)
            if again is not None:
                heard[lid] = again
            if (again or {}).get("wer", 0.0) <= WER_LIMIT:
                break
        else:
            still_bad.append(lid)
    write_heard(out_dir, short.lines, heard, why_not)
    note = next((n for n in notes if n), None)
    message = " ".join(m for m in (_unclear_message(still_bad), note) if m) or None
    return NarrationResult("ok", {ln.id: durations[ln.id] for ln in short.lines}, message)


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
