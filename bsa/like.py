"""A list of the workspace's shorts, each with its look, length and voice (for "make it like that one")."""

from __future__ import annotations

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
