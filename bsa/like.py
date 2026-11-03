"""A list of the workspace's shorts, each with its look, length and voice (for "make it like that one")."""

from __future__ import annotations

import copy
import statistics
import time
from datetime import UTC, datetime
from pathlib import Path

from bsa import engine_runs, jobstate, kit, settings, workspace
from bsa.narrate import run as narrate_run
from bsa.short import load_short


def _label(record: dict) -> str:
    """The voice a short was narrated with, from narrator.json: "Kokoro (af_heart, 1.0×)"."""
    provider = record.get("provider")
    if not provider:
        return "not narrated"
    if provider == "none":
        return "captions only"
    if provider == "custom":
        return "your voice tool"
    name = settings._PROVIDER_NAMES.get(provider, provider)
    parts = [str(record["voice"])] if record.get("voice") else []
    try:
        parts.append(f"{float(record.get('speed') or 1.0):.1f}×")
    except (TypeError, ValueError):
        pass
    return f"{name} ({', '.join(parts)})"


def _edit_seconds(sdir: Path) -> float | None:
    rec = engine_runs.edit_info(sdir, "final")
    try:
        seconds = round(float(rec["frames"]) / float(rec["fps"]), 1)
    except (TypeError, ValueError, KeyError, ZeroDivisionError):
        return None
    return seconds if seconds > 0 else None


def _finished(sdir: Path) -> bool:
    try:
        return "final" in jobstate.load_state(sdir)["stages"]
    except Exception:  # noqa: BLE001 - a damaged state never breaks the list
        return False


def summary(sdir: Path, settings_: dict) -> dict:
    """One short's summary. A short.json that can't be read gives `problem` and None values; never raises."""
    sdir = Path(sdir)
    m = workspace._NUM.match(sdir.name)
    row = {"short": sdir.name, "number": int(m.group(1)) if m else None, "title": None, "style": None, "set": None,
           "length_s": None, "narrator": "not narrated", "finished": _finished(sdir), "problem": None}  # fmt: skip
    try:
        short = load_short(sdir / "source" / "short.json")
    except Exception as exc:  # noqa: BLE001
        row["problem"] = f"short.json can't be read: {exc}"
        return row
    row["title"] = short.title
    k = kit.kit_for(settings_, short)
    if k is not None:
        row["style"], row["set"] = k.style, k.set
    row["length_s"] = _edit_seconds(sdir) or short.length_s
    row["narrator"] = _label(narrate_run._read_record(sdir / "audio" / "vo"))
    return row


def list_shorts(root: Path, settings_: dict) -> list[dict]:
    """Every numbered short in the workspace, highest number first."""
    return [summary(p, settings_) for _, p in sorted(workspace._numbered(root), key=lambda t: t[0], reverse=True)]


# "Make it like that one": what `new-short --like` copies from an earlier short. Never its story, facts, sources,
# shots, assets, post or thumbnail, and never the engine it was made with: the engine is always the user's choice.
COPY_KEYS = ("style", "look", "length_s", "pace", "end_card", "closing", "narrator")
LABELS = {"style": "the look", "look": "the brand or fresh look", "length_s": "the length", "pace": "the pace",
          "end_card": "the end card", "closing": "the closing", "narrator": "the voice"}  # fmt: skip
# The interview question each copied key answers (the interview skips those).
SKIP = {"length_s": "length", "pace": "pace", "style": "look", "look": "look", "end_card": "closing",
        "closing": "closing", "narrator": "voice"}  # fmt: skip


def pace_of(short) -> dict:
    """The short's pace as guidance for a new storyboard: shot count, median shot min_s, shots with a hold.
    {} for a short without shots."""
    shots = list(short.shots)
    if not shots:
        return {}
    lengths = []
    for s in shots:
        try:
            lengths.append(float(s.min_s))
        except (TypeError, ValueError):
            pass
    return {"shots": len(shots), "min_s": round(statistics.median(lengths), 1) if lengths else 3.0,
            "holds": sum(1 for s in shots if s.hold)}  # fmt: skip


def _provider_name(provider) -> str:
    return settings._PROVIDER_NAMES.get(provider, provider) if provider else "no narrator"


def _narrator_from(other_dir: Path, other, settings_: dict) -> tuple[dict, list[str]]:
    """take_mode and lead_in the other short was (or would be) narrated with, plus its voice and speed when it was
    narrated by the provider the user has now."""
    record = narrate_run._read_record(other_dir / "audio" / "vo")
    used = record.get("provider")
    if used and used != "none":
        out = {"take_mode": record.get("take_mode") or "line", "lead_in": float(record.get("lead_in") or 0.0)}
    else:
        eff = narrate_run.narrator_for(settings_, other)
        out = {"take_mode": eff["take_mode"], "lead_in": eff["lead_in"]}
    now = (settings_.get("narrator") or {}).get("provider")
    if not used or used == "none":
        return out, [f"the voice wasn't copied: {other.title} hasn't been narrated yet"]
    if used != now:
        note = f"the voice wasn't copied: {other.title} used {_provider_name(used)}, you now use {_provider_name(now)}"
        return out, [note]
    if record.get("voice"):
        out["voice"] = record["voice"]
    if record.get("speed") is not None:
        out["speed"] = float(record["speed"])
    return out, []


def copy_from(other: Path, settings_: dict) -> tuple[dict, list[str], list[str]]:
    """(values for the new short.json, the keys copied in COPY_KEYS order, notes) from the earlier short `other`.
    ValueError when its short.json can't be read."""
    other = Path(other)
    try:
        short = load_short(other / "source" / "short.json")
    except Exception as exc:
        raise ValueError(f"{other.name}'s short.json can't be read: {exc}") from exc
    values: dict = {}
    if isinstance(short.style, dict) and short.style:
        values["style"] = copy.deepcopy(short.style)
    if short.look is not None:
        values["look"] = short.look
    values["length_s"] = short.length_s
    pace = pace_of(short)
    if pace:
        values["pace"] = pace
    for key in ("end_card", "closing"):
        if getattr(short, key) is not None:
            values[key] = copy.deepcopy(getattr(short, key))
    values["narrator"], notes = _narrator_from(other, short, settings_)
    return values, [k for k in COPY_KEYS if k in values], notes


def provenance(other: Path, copied: list[str], now: float | None = None) -> dict:
    """short.json's `like`: where the short started from, what was copied and when (UTC)."""
    at = datetime.fromtimestamp(time.time() if now is None else now, tz=UTC)
    return {"short": Path(other).name, "copied": list(copied), "at": at.strftime("%Y-%m-%dT%H:%M:%SZ")}


def skipped_questions(copied: list[str]) -> list[str]:
    """The interview questions the copied keys answer, each once, in order."""
    out: list[str] = []
    for key in copied:
        q = SKIP.get(key)
        if q and q not in out:
            out.append(q)
    return out


def said(title: str, skipped: list[str]) -> str:
    """The one line telling the user what was copied: "Started from <title>: I copied the look, pace, closing and
    voice, so I won't ask about those." """
    if not skipped:
        return f"Started from {title}: nothing could be copied, so I'll ask about everything."
    words = skipped[0] if len(skipped) == 1 else f"{', '.join(skipped[:-1])} and {skipped[-1]}"
    return f"Started from {title}: I copied the {words}, so I won't ask about {'it' if len(skipped) == 1 else 'those'}."
