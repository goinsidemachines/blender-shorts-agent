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
import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from bsa import jobstate
from bsa.narrate import custom as _custom
from bsa.narrate import elevenlabs as _elevenlabs
from bsa.narrate import local as _local
from bsa.narrate import params as _params
from bsa.narrate import takes as _takes
from bsa.narrate.contract import NarrationError, check_take, forget_durations, pad_lead, read_durations, voice_onset
from bsa.narrate.local import RETRIES, WER_LIMIT
from bsa.short import Line, Short, line_id_problem, spoken_text

LOCAL_ENGINES = ("kokoro", "chatterbox", "chatterbox-turbo")
HEARD_FILE = "heard.json"
RECORD_FILE = "narrator.json"
FIRST_SEED = 7
SOURCES = ("take", "line", "fallback")  # where a line's audio came from: a paragraph take, its own, its own after a take failed
WHISPER_MISSING = (
    "Whisper isn't installed, so the takes weren't checked. CLI voice plan whisper-small.en shows what it downloads."
)
CHECK_OFF = "The voice check is turned off (narrator.verify false)."
CAPTIONS_ONLY = "This short uses captions only, so there is no voice to check."
TAKES_DIR = "takes"  # audio/vo/takes/<take id>.wav: paragraph takes, kept for re-cuts
NO_TAKE_PLACEHOLDER = (
    "take mode needs {take} in your voice command, so the lines were spoken one by one; see playbook "
    "05-narration (custom voice tools)."
)


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


def line_sources(out_dir: Path) -> dict[str, dict]:
    """{line id: {"source": "take" | "line" | "fallback", "reason": str | None}} from narrator.json, for the valid
    entries only; {} when there is none."""
    record = _read_record(out_dir)
    sources, reasons = record.get("sources"), record.get("reasons")
    if not isinstance(sources, dict):
        return {}
    reasons = reasons if isinstance(reasons, dict) else {}
    found = {}
    for lid, source in sources.items():
        if source not in SOURCES:
            continue
        reason = reasons.get(lid)
        found[str(lid)] = {"source": source, "reason": reason if isinstance(reason, str) and reason else None}
    return found


def record_sources(out_dir: Path, sources: dict[str, str], reasons: dict[str, str] | None = None) -> str | None:
    """Record where each line's audio came from ({id: source}) and, for a fallback, why ({id: reason}); an id whose
    new source is not "fallback" loses its old reason. Best-effort like _record_seeds: returns why it couldn't."""
    record = _read_record(out_dir)
    old = record.get("sources")
    merged = {**(old if isinstance(old, dict) else {}), **sources}
    old_reasons = record.get("reasons")
    kept = {k: v for k, v in (old_reasons if isinstance(old_reasons, dict) else {}).items()
            if k not in sources}  # fmt: skip
    kept.update({k: v for k, v in (reasons or {}).items() if sources.get(k) == "fallback"})
    record["sources"], record["reasons"] = merged, kept
    try:
        _write_json(Path(out_dir) / RECORD_FILE, record)
    except OSError as exc:
        return f"Where the lines' audio came from couldn't be saved ({exc})."
    return None


def retake_problem(out_dir: Path, line_id: str) -> str | None:
    """Why `line_id` can't be re-taken (None when it can); touches nothing."""
    out_dir = Path(out_dir)
    problem = line_id_problem(line_id)
    if problem:
        return problem
    if (out_dir / f"{line_id}.wav").resolve().parent != out_dir.resolve():
        return f"{line_id!r} is not a line id"
    known = _read_record(out_dir).get("lines")
    if isinstance(known, dict) and line_id not in known:
        return f"{line_id!r} is not a line of this short (its lines: {', '.join(known) or 'none'})"
    return None


def retake(out_dir: Path, line_id: str) -> int:
    """Save the line's next seed in narrator.json, then throw its take (and its duration) away, so the next
    narration speaks it again with that seed (a different reading). Returns the seed. Refuses (ValueError, touching
    nothing) an id that isn't a plain line id, or isn't one of the lines narrator.json records the takes are for.
    Raises OSError, keeping the take, when the seed can't be saved: a re-take is only worth its new seed."""
    out_dir = Path(out_dir)
    problem = retake_problem(out_dir, line_id)
    if problem:
        raise ValueError(problem)
    seed = take_seeds(out_dir).get(line_id, FIRST_SEED) + 1
    problem = _record_seeds(out_dir, {line_id: seed})
    if problem:
        raise OSError(problem)
    (out_dir / f"{line_id}.wav").unlink(missing_ok=True)
    dpath = out_dir / "durations.json"
    try:
        durations = json.loads(dpath.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        durations = None
    if isinstance(durations, dict) and line_id in durations:
        del durations[line_id]
        _write_json(dpath, durations)
    return seed


def take_of(out_dir: Path, line_id: str) -> dict | None:
    """The paragraph take (narrator.json's `takes` entry) that holds the line; None when it was spoken on its own."""
    return next((t for t in take_records(out_dir) if line_id in t["lines"]), None)


def retake_take(out_dir: Path, line_id: str) -> int:
    """Re-take the whole paragraph holding the line: save the take's next seed (and its lines' too, so no line is
    ever spoken with an older one), then throw away every line of the take, the take itself and their durations, so
    the next narration speaks the paragraph again with that seed. Returns the seed. Refuses (ValueError, touching
    nothing) a line that isn't re-takeable or wasn't spoken in a paragraph take. Raises OSError, keeping the audio,
    when the seed can't be saved."""
    out_dir = Path(out_dir)
    problem = retake_problem(out_dir, line_id)
    if problem:
        raise ValueError(problem)
    take = take_of(out_dir, line_id)
    if take is None:
        raise ValueError(f"line {line_id} was not spoken in a paragraph take: re-take it with --line alone")
    seeds = take_seeds(out_dir)
    seed = max(take["seed"], *(seeds.get(i, FIRST_SEED) for i in take["lines"])) + 1
    record = _read_record(out_dir)
    record["seeds"] = {**seeds, **{i: seed for i in take["lines"]}}
    record["takes"] = [{**t, "seed": seed} if t["id"] == take["id"] else t for t in take_records(out_dir)]
    try:
        _write_json(out_dir / RECORD_FILE, record)
    except OSError as exc:
        raise OSError(f"The take's next seed couldn't be saved ({exc}), so it was kept.") from exc
    for i in take["lines"]:
        if (out_dir / f"{i}.wav").resolve().parent == out_dir.resolve():
            (out_dir / f"{i}.wav").unlink(missing_ok=True)
    take_file = (out_dir / take["file"]).resolve()
    if take_file.is_relative_to((out_dir / TAKES_DIR).resolve()):  # never a file outside the takes folder
        take_file.unlink(missing_ok=True)
    forget_durations(out_dir, take["lines"])
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


def apply_lead_in(out_dir: Path, ids: list[str], lead_in: float) -> dict[str, float]:
    """Pad each id's take to `lead_in` seconds before its first word (only what is missing), forget their durations
    (so they are measured from the padded WAVs) and return the new seconds per id."""
    out_dir = Path(out_dir)
    for i in ids:
        pad_lead(out_dir / f"{i}.wav", lead_in)
    forget_durations(out_dir, ids)
    return read_durations(out_dir, list(ids))


def take_records(out_dir: Path) -> list[dict]:
    """The paragraph takes narrator.json records, [{"id", "lines", "seed", "file"}], valid entries only."""
    found = _read_record(out_dir).get("takes")
    out = []
    for t in found if isinstance(found, list) else []:
        if (isinstance(t, dict) and isinstance(t.get("id"), str) and isinstance(t.get("lines"), list)
                and all(isinstance(i, str) for i in t["lines"]) and isinstance(t.get("seed"), int)
                and not isinstance(t.get("seed"), bool) and isinstance(t.get("file"), str)):  # fmt: skip
            out.append({"id": t["id"], "lines": list(t["lines"]), "seed": t["seed"], "file": t["file"]})
    return out


def _record_take(out_dir: Path, tid: str, entry: dict | None) -> str | None:
    """Record (or, with None, forget) one paragraph take. Best-effort like _record_seeds."""
    record = _read_record(out_dir)
    takes = [t for t in take_records(out_dir) if t["id"] != tid] + ([entry] if entry else [])
    record["takes"] = sorted(takes, key=lambda t: t["id"])
    try:
        _write_json(Path(out_dir) / RECORD_FILE, record)
    except OSError as exc:
        return f"The paragraph takes couldn't be recorded ({exc})."
    return None


def current_takes(short: Short) -> dict[str, list[str]]:
    """{take id: its line ids} for the paragraph takes the short's lines group into now (groups of 2+ lines)."""
    groups = _takes.group(short.lines, short.shots)
    return {_takes.take_id(i): [ln.id for ln in g] for i, g in enumerate(groups, start=1) if len(g) >= 2}


def _take_samples(path: Path) -> np.ndarray:
    """A take's samples as floats in -1..1 (the contract: 48 kHz mono 16-bit, already checked)."""
    with wave.open(str(path), "rb") as w:
        data = w.readframes(w.getnframes())
    return np.frombuffer(data, dtype="<i2").astype(np.float64) / 32768.0


def _plan(short: Short, todo: list) -> tuple[list[tuple[str, list]], list]:
    """([(take id, its lines)] to speak as paragraph takes, lines to speak on their own): a group of 2+ lines that all
    need speaking is a take; the lines of any other group that need speaking are spoken on their own."""
    need = {ln.id for ln in todo}
    paragraphs, alone = [], []
    for i, g in enumerate(_takes.group(short.lines, short.shots), start=1):
        if len(g) >= 2 and all(ln.id in need for ln in g):
            paragraphs.append((_takes.take_id(i), g))
        else:
            alone += [ln for ln in g if ln.id in need]
    return paragraphs, alone


def _speak_takes(short: Short, out_dir: Path, todo: list, speak, take, words, lead_in: float):
    """Paragraph mode: speak each planned take, cut it into line takes and speak the lines it couldn't cut on their
    own; then the lines that are no take's. Each take, its lines' seeds and sources are recorded as soon as it is
    done, so a run that stops half way keeps the takes it finished. A take the engine fails on is spoken line by
    line instead (source "fallback"). Returns (durations, seed used per line, notes)."""
    paragraphs, alone = _plan(short, todo)
    old = {t["id"]: t for t in take_records(out_dir)}
    takes_dir = out_dir / TAKES_DIR
    durations: dict[str, float] = {}
    used: dict[str, int] = {}
    notes: list[str | None] = []
    for tid, g in paragraphs:
        ids = [ln.id for ln in g]
        seeds = take_seeds(out_dir)
        had = old.get(tid)
        # a re-take waiting on one of its lines has moved that line on: never speak it with an older seed
        seed = max([had["seed"] if had and had["lines"] == ids else FIRST_SEED, *(seeds[i] for i in ids if i in seeds)])
        (takes_dir / f"{tid}.wav").unlink(missing_ok=True)  # an old take must not pass for this one
        take_line = Line(tid, _takes.joined(g), g[0].shot)
        try:
            x = _take_samples(Path(take(take_line, seed, g)))
        except Exception as exc:  # noqa: BLE001 - a failed take costs only the take: its lines are spoken alone
            failed = f"the take failed: {_reason(exc)}"
            pieces = [_takes.Piece(k, None, None, failed) for k in range(len(g))]
            notes.append(_record_take(out_dir, tid, None))
        else:
            timings = (words([take_line], takes_dir) if words else None) or {}
            pieces = _takes.cut(x, [spoken_text(ln) for ln in g], ids, words=timings.get(tid))
            cut_ids = [ids[p.line] for p in pieces if p.start is not None]
            secs = _takes.write_pieces(x, pieces, ids, out_dir)
            forget_durations(out_dir, cut_ids)  # an engine's old durations.json entry must not outlive its take
            durations.update(apply_lead_in(out_dir, cut_ids, lead_in) if lead_in > 0 else secs)
            note = next((p.note for p in pieces if p.note), None)
            if note:
                notes.append(f"Take {tid}: {note}.")
            notes.append(_record_take(out_dir, tid, {"id": tid, "lines": ids, "seed": seed,
                                                     "file": f"{TAKES_DIR}/{tid}.wav"}))  # fmt: skip
            notes.append(record_sources(out_dir, {i: "take" for i in cut_ids}))
        used.update({i: seed for i in ids})
        notes.append(_record_seeds(out_dir, {i: seed for i in ids}))
        alone_now = [g[p.line] for p in pieces if p.start is None]
        if alone_now:
            durations.update(speak(alone_now, seed))
            reasons = {ids[p.line]: p.reason or "the take couldn't be cut there" for p in pieces if p.start is None}
            notes.append(record_sources(out_dir, {ln.id: "fallback" for ln in alone_now}, reasons))
    seeds = take_seeds(out_dir)
    by_seed: dict[int, list] = {}
    for ln in alone:
        by_seed.setdefault(seeds.get(ln.id, FIRST_SEED), []).append(ln)
    for seed, lns in by_seed.items():
        durations.update(speak(lns, seed))
        used.update({ln.id: seed for ln in lns})
        notes.append(_record_seeds(out_dir, {ln.id: seed for ln in lns}))
        notes.append(record_sources(out_dir, {ln.id: "line" for ln in lns}))
    return durations, used, notes


def _spoken(short: Short, out_dir: Path, speak, hear, check, *, take=None, words=None, lead_in: float = 0.0,
            retry: bool = True, note: str | None = None) -> NarrationResult:  # fmt: skip
    """Only lines without a good take on disk are spoken (clear_stale_takes has already removed takes of another
    voice, speed or text), so an interrupted, re-taken or reworded run remakes only what it must. Each take is
    spoken with its line's seed (take_seeds: 7 for a first take, the next one after a retake) and every seed used
    is recorded; an unclear take spoken in this call is spoken again with its seed + 1, then + 2 (unless `retry` is
    off: then it is only reported). An unclear take kept from an earlier run already had its retries: it is
    reported, not spoken again. `speak(lines, seed)` makes the takes and returns seconds per line; `hear(lines)`
    returns (heard, why_not) as _hear does. Writes heard.json.

    With `take(take_line, seed, lines) -> Path` (paragraph mode) the lines to speak are planned with takes.group: a group
    whose lines all need speaking is spoken as one take into out_dir/TAKES_DIR and cut back into line takes (source
    "take"); a line the cut can't reach is spoken on its own (source "fallback", with the cut's reason). `words` is
    local.word_times (or None): Whisper's word timings, which settle where each line ends. Lead-in pads every line
    take written, cut or spoken."""
    if lead_in > 0:
        spoke = speak

        def speak(lns, seed):  # every call (first takes and retries) is padded to the lead-in
            got = spoke(lns, seed)
            return {**got, **apply_lead_in(out_dir, [ln.id for ln in lns], lead_in)}

    seeds = take_seeds(out_dir)
    kept = [ln for ln in short.lines if (out_dir / f"{ln.id}.wav").is_file() and not check_take(out_dir / f"{ln.id}.wav")]
    todo = [ln for ln in short.lines if ln not in kept]
    kept_ids = [ln.id for ln in kept]
    if kept and lead_in > 0:  # a crash between a take's write and its padding must not leave it unpadded
        durations = apply_lead_in(out_dir, kept_ids, lead_in)
    else:
        durations = read_durations(out_dir, kept_ids) if kept else {}
    notes: list[str | None] = [note]
    used: dict[str, int] = {}
    if take is not None:
        got, used, more = _speak_takes(short, out_dir, todo, speak, take, words, lead_in)
        durations.update(got)
        notes += more
    else:
        by_seed: dict[int, list] = {}
        for ln in todo:
            by_seed.setdefault(seeds.get(ln.id, FIRST_SEED), []).append(ln)
        for seed, lns in by_seed.items():
            durations.update(speak(lns, seed))
            used.update({ln.id: seed for ln in lns})
            notes.append(_record_seeds(out_dir, {ln.id: seed for ln in lns}))
            notes.append(record_sources(out_dir, {ln.id: "line" for ln in lns}))
    heard, why_not = hear(short.lines) if check else (None, CHECK_OFF)
    still_bad: list[str] = []
    by_id = {ln.id: ln for ln in short.lines}
    for lid in _unclear(heard, by_id):
        if lid not in used or not retry:  # kept from an earlier run, which already retried it (or no retries)
            still_bad.append(lid)
            continue
        base = used[lid]
        for step in range(1, RETRIES + 1):
            durations.update(speak([by_id[lid]], base + step))
            notes.append(_record_seeds(out_dir, {lid: base + step}))
            notes.append(record_sources(out_dir, {lid: "line"}))
            again = (hear([by_id[lid]])[0] or {}).get(lid)
            if again is not None:
                heard[lid] = again
            if (again or {}).get("wer", 0.0) <= WER_LIMIT:
                break
        else:
            still_bad.append(lid)
    write_heard(out_dir, short.lines, heard, why_not)
    said = list(dict.fromkeys(n for n in notes if n))  # each note once: a save that failed fails for every line
    message = " ".join(m for m in (_unclear_message(still_bad), *said) if m) or None
    return NarrationResult("ok", {ln.id: durations[ln.id] for ln in short.lines}, message)


def _paragraph(narrator: dict) -> bool:
    return narrator.get("take_mode") == "paragraph"


def _words(mod):
    """Whisper's word timings for the takes (the checker's word_times), or None when it has none."""
    fn = getattr(mod, "word_times", None)
    return (lambda take_lines, takes_dir: fn(take_lines, takes_dir)) if fn else None


def _local_narrate(provider: str, short: Short, narrator: dict, out_dir: Path, mod) -> NarrationResult:
    voice, speed = narrator.get("voice"), float(narrator.get("speed") or 1.0)
    params = narrator.get("params")
    extra = {"params": params} if params else {}  # none: the engine is called exactly as before narrator.params
    raw = _fn(mod, "generate")

    def gen(*args, **kw):
        return raw(*args, **kw, **extra)

    def take(take_line, seed, lines):
        gen(provider, [take_line], voice, speed, out_dir / TAKES_DIR, seed=seed)
        return out_dir / TAKES_DIR / f"{take_line.id}.wav"

    para = _paragraph(narrator)
    return _spoken(
        short, out_dir,
        lambda lns, seed: gen(provider, lns, voice, speed, out_dir, seed=seed),
        lambda lns: _hear(mod, lns, out_dir),
        narrator.get("verify", True),
        take=take if para else None,
        words=_words(mod) if para else None,
        lead_in=float(narrator.get("lead_in") or 0.0),
    )  # fmt: skip


def _move_log(takes_dir: Path, out_dir: Path) -> None:
    """Append what the voice tool printed while speaking a take to the narration's own narrator.log."""
    src = Path(takes_dir) / _custom.LOG_FILE
    try:
        text = src.read_text(encoding="utf-8", errors="replace")
        with open(Path(out_dir) / _custom.LOG_FILE, "a", encoding="utf-8", errors="replace") as f:
            f.write(text)
        src.unlink()
    except OSError:  # the log is a convenience: it never stops the narration
        pass


def _custom_narrate(short: Short, narrator: dict, out_dir: Path, mod, local) -> NarrationResult:
    """The user's own voice tool; checked with Whisper only when the plugin's own runner has it installed. In
    paragraph mode it speaks takes only when its command has {take}; otherwise line by line, and says so."""
    command = str(narrator.get("command") or "").strip()
    if not command:
        raise NarrationError("no voice command has been saved")
    voice, speed = narrator.get("voice"), float(narrator.get("speed") or 1.0)
    gen = _fn(mod, "generate")

    def take(take_line, seed, lines):
        try:
            gen(command, [take_line], out_dir / TAKES_DIR, seed=seed, voice=voice, speed=speed, take=take_line.id,
                parts=lines)
        finally:
            _move_log(out_dir / TAKES_DIR, out_dir)
        return out_dir / TAKES_DIR / f"{take_line.id}.wav"

    para = _paragraph(narrator) and _custom.takes_supported(command)
    return _spoken(
        short, out_dir,
        lambda lns, seed: gen(command, lns, out_dir, seed=seed, voice=voice, speed=speed),
        lambda lns: _hear(local, lns, out_dir),
        narrator.get("verify", True),
        take=take if para else None,
        words=_words(local) if para else None,
        lead_in=float(narrator.get("lead_in") or 0.0),
        note=NO_TAKE_PLACEHOLDER if _paragraph(narrator) and not para else None,
    )  # fmt: skip


def _elevenlabs_paragraph(short: Short, narrator: dict, out_dir: Path, gen, local) -> NarrationResult:
    """ElevenLabs in paragraph mode: the same planner, with one voice check after and no retries (each take costs
    credit)."""
    voice, speed = narrator["voice"], float(narrator.get("speed") or 1.0)

    def take(take_line, seed, lines):
        gen([take_line], voice, speed, out_dir / TAKES_DIR)
        return out_dir / TAKES_DIR / f"{take_line.id}.wav"

    return _spoken(
        short, out_dir,
        lambda lns, seed: dict(gen(lns, voice, speed, out_dir)),
        lambda lns: _hear(local, lns, out_dir),
        narrator.get("verify", True),
        take=take, words=_words(local), retry=False,
        lead_in=float(narrator.get("lead_in") or 0.0),
    )  # fmt: skip


def _elevenlabs_checked(short: Short, narrator: dict, out_dir: Path, durations: dict, local) -> NarrationResult:
    """ElevenLabs takes get the voice check too, once over every line: no retries, since each take costs credit."""
    heard, why_not = _hear(local, short.lines, out_dir) if narrator.get("verify", True) else (None, CHECK_OFF)
    write_heard(out_dir, short.lines, heard, why_not)
    note = record_sources(out_dir, {ln.id: "line" for ln in short.lines})
    message = " ".join(m for m in (_unclear_message(_unclear(heard, [ln.id for ln in short.lines])), note) if m)
    return NarrationResult("ok", durations, message or None)


def check_line_ids(short: Short) -> None:
    """Refuse (ValueError, with the rule's "rename it" message) a short with a line id that isn't a plain file name:
    takes are written, rewritten and deleted as <id>.wav, so it is checked before anything is touched."""
    for ln in short.lines:
        problem = line_id_problem(ln.id)
        if problem:
            raise ValueError(problem)


def own_voice_dropped(settings: dict, short) -> str | None:
    """The provider this short's own voice and speed were chosen for (its narrator's `voice_for`) when the settings'
    narrator is another one now, so they are not used (nor its engine params); None otherwise. A short with no
    `voice_for` (made before it was recorded) keeps its voice and speed as before."""
    own = getattr(short, "narrator", None)
    if not isinstance(own, dict) or not own.get("voice_for") or not ({"voice", "speed", "params"} & set(own)):
        return None
    meant = own["voice_for"]
    return meant if meant != (settings.get("narrator") or {}).get("provider") else None


def _as_dict(value) -> dict:
    return dict(value) if isinstance(value, dict) else {}


def narrator_for(settings: dict, short) -> dict:
    """The narrator settings with this short's own choices (voice, speed, take_mode, lead_in, params) over them; the
    short's params merge key by key over the settings' (`params` is left out when there are none). The provider,
    command and label are never per short."""
    from bsa.short import NARRATOR_KEYS

    own = dict(getattr(short, "narrator", None) or {})
    if own_voice_dropped(settings, short):  # its voice and speed were for another narrator: the settings' apply
        own.pop("voice", None)
        own.pop("speed", None)
        own.pop("params", None)
    own.pop("voice_for", None)
    out = dict(settings.get("narrator") or {})
    params = {**_as_dict(out.get("params")), **_as_dict(own.pop("params", None))}
    out.update({k: v for k, v in own.items() if k in NARRATOR_KEYS})
    out.pop("params", None)
    if params:
        out["params"] = params
    out["take_mode"] = out.get("take_mode") or "line"
    out["lead_in"] = float(out.get("lead_in") or 0.0)
    return out


def narrate(short_dir: Path, short: Short, settings: dict, **providers) -> NarrationResult:
    check_line_ids(short)
    short_dir = Path(short_dir)
    narrator = narrator_for(settings, short)
    provider = narrator.get("provider")
    out_dir = short_dir / "audio" / "vo"
    if provider in (None, "none"):
        result = NarrationResult("captions-only")
    else:
        try:
            wrong = _params.problems(provider, narrator.get("params"))
            if wrong:
                raise NarrationError("; ".join(wrong))
            if provider == "elevenlabs":
                if not narrator.get("voice"):
                    raise NarrationError("no ElevenLabs voice has been chosen")
                gen = _fn(providers.get("elevenlabs", _elevenlabs), "generate")
                if _paragraph(narrator):
                    result = _elevenlabs_paragraph(short, narrator, out_dir, gen, providers.get("local", _local))
                else:
                    durations = dict(gen(short.lines, narrator["voice"], float(narrator.get("speed") or 1.0), out_dir))
                    if float(narrator.get("lead_in") or 0.0) > 0:
                        durations.update(apply_lead_in(out_dir, [ln.id for ln in short.lines], narrator["lead_in"]))
                    result = _elevenlabs_checked(
                        short, narrator, out_dir, dict(durations), providers.get("local", _local)
                    )
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
    extra = {}
    if narrator.get("take_mode") == "paragraph":
        extra["take_mode"] = "paragraph"
    if float(narrator.get("lead_in") or 0.0) > 0:
        extra["lead_in"] = float(narrator["lead_in"])
    if narrator.get("params"):  # the engine's params: a change re-speaks the takes (none = no key, as before)
        extra["params"] = dict(narrator["params"])
    return {
        **extra,
        "command": (narrator.get("command") or None) if narrator.get("provider") == "custom" else None,
        "provider": narrator.get("provider"),
        "voice": narrator.get("voice"),
        "speed": float(narrator.get("speed") or 1.0),
        "lines": {ln.id: spoken_text(ln) for ln in short.lines},  # a take is stale when its spoken words change
    }


def clear_stale_takes(out_dir: Path, short: Short, narrator: dict) -> list[str]:
    """Delete takes made by another narrator, voice, speed or engine params, or for a line whose text has changed
    since, and record what the takes left on disk are for. Takes still current are kept, so an interrupted run
    resumes. Returns the ids of the takes deleted."""
    check_line_ids(short)
    out_dir = Path(out_dir)
    record_path = out_dir / RECORD_FILE
    want = _takes_record(short, narrator)
    try:
        had = json.loads(record_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        had = None
    same_voice = isinstance(had, dict) and all(had.get(k) == want[k] for k in ("provider", "voice", "speed", "command"))
    same_voice = same_voice and all(had.get(k) == want.get(k) for k in ("take_mode", "lead_in", "params"))
    old_lines = had.get("lines", {}) if same_voice else {}
    removed = []
    waiting = {lid for lid in take_seeds(out_dir) if not (out_dir / f"{lid}.wav").is_file()}  # a re-take's line
    for wav in sorted(out_dir.glob("*.wav")):
        lid = wav.stem
        if not same_voice or old_lines.get(lid) != want["lines"].get(lid):
            wav.unlink()
            removed.append(lid)
    if removed:
        (out_dir / "durations.json").unlink(missing_ok=True)
    # a seed belongs to a take of this voice and these words, even while a retake waits for its take
    had_seeds = take_seeds(out_dir) if same_voice else {}
    want["seeds"] = {lid: n for lid, n in had_seeds.items()
                     if old_lines.get(lid) == want["lines"].get(lid) or lid in waiting}  # fmt: skip
    # a source belongs to a take that is still there (a re-take's line has none until it is spoken again)
    had_sources = line_sources(out_dir) if same_voice else {}
    want["sources"] = {lid: v["source"] for lid, v in had_sources.items()
                       if old_lines.get(lid) == want["lines"].get(lid) and lid not in waiting}  # fmt: skip
    want["reasons"] = {lid: v["reason"] for lid, v in had_sources.items() if lid in want["sources"] and v["reason"]}
    # a paragraph take is kept (for re-cuts) while it is of this voice and its lines' words are unchanged
    # and while the lines still group into it (a hold or a reworded line can regroup the paragraphs)
    now = current_takes(short)
    kept_takes = [t for t in (take_records(out_dir) if same_voice else [])
                  if now.get(t["id"]) == t["lines"]
                  and all(old_lines.get(lid) == want["lines"][lid] for lid in t["lines"])]  # fmt: skip
    # a take whose lines are all gone (each re-taken on its own, or the take re-taken) has nothing left to cut
    kept_takes = [t for t in kept_takes if any((out_dir / f"{lid}.wav").is_file() for lid in t["lines"])]
    if want.get("take_mode") == "paragraph" and kept_takes:
        want["takes"] = kept_takes
    keep = {f"{t['id']}.wav" for t in kept_takes} if want.get("take_mode") == "paragraph" else set()
    for wav in sorted((out_dir / TAKES_DIR).glob("*.wav")):
        if wav.name not in keep:
            wav.unlink()
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
