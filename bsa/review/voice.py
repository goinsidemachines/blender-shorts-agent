"""The voice check on the review page's Narration stage: per line, what Whisper heard against what the voice was
asked to say. Reads heard.json (written by bsa.narrate.run); writes nothing."""

from __future__ import annotations

import json
from pathlib import Path

from bsa.narrate.local import WER_LIMIT
from bsa.narrate.run import HEARD_FILE
from bsa.review import store, worddiff
from bsa.short import Line, Short, spoken_text

NOT_NARRATED = "Not narrated yet."
UNREADABLE = "The voice check file can't be read."


def _read(vo: Path) -> dict | None:
    """heard.json as a dict; None when it is not there; ValueError when it can't be read."""
    path = vo / HEARD_FILE
    if not path.is_file():
        return None
    try:
        data = json.loads(store.read_bytes(path).decode("utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(UNREADABLE) from exc
    if not isinstance(data, dict):
        raise ValueError(UNREADABLE)  # noqa: TRY004 - one reason for every unreadable shape
    return data


def _mtime(path: Path) -> float | None:
    try:
        return path.stat().st_mtime
    except OSError:
        return None


def _row(line: Line, entry: dict | None, vo: Path, heard_at: float | None) -> dict:
    """One line. Nothing heard counts for a line whose take is gone or newer than heard.json (a re-take is under
    way) or whose words changed since: those show as unchecked, never with the old heard text."""
    text = spoken_text(line)
    row = {"id": line.id, "text": text, "said": None, "wer": None, "ok": None, "diff": None, "stale": False,
           "waiting": False}  # fmt: skip
    if entry is None:
        return row
    wav = _mtime(vo / f"{line.id}.wav")
    if wav is None or (heard_at is not None and wav > heard_at):
        row["waiting"] = True
        return row
    if entry.get("text") != text:
        row["stale"] = True
        return row
    said, wer = entry.get("said"), entry.get("wer")
    wer = float(wer) if isinstance(wer, (int, float)) and not isinstance(wer, bool) else None
    row["wer"] = wer
    if isinstance(said, str):
        row["said"] = said
        row["diff"] = worddiff.diff(text, said)
    if wer is not None:
        row["ok"] = wer <= WER_LIMIT
    return row


def block(sdir: Path, short: Short, narration: dict, closing: Line | None = None) -> dict:
    """{"lines": [{"id", "text", "said", "wer", "ok", "diff", "stale", "waiting"}], "checked", "why_not"}: the short's
    lines in order, then the closing line (`closing`, the closing card's voice line) once heard.json has its entry."""
    vo = Path(sdir) / "audio" / "vo"
    try:
        data = _read(vo)
    except ValueError as exc:
        return {"lines": [], "checked": False, "why_not": str(exc)}
    if data is None:
        return {"lines": [], "checked": False, "why_not": NOT_NARRATED}
    heard = data.get("lines") if isinstance(data.get("lines"), dict) else {}
    heard = {k: v for k, v in heard.items() if isinstance(v, dict)}
    heard_at = _mtime(vo / HEARD_FILE)
    lines = list(short.lines)
    if closing is not None and closing.id in heard and all(ln.id != closing.id for ln in lines):
        lines.append(closing)
    rows = [_row(ln, heard.get(ln.id), vo, heard_at) for ln in lines]
    why_not = data.get("why_not")
    return {
        "lines": rows,
        "checked": bool(data.get("checked")),
        "why_not": why_not if isinstance(why_not, str) else None,
    }
