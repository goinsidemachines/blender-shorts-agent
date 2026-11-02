"""The voice check on the review page's Narration stage: per line, what Whisper heard against what the voice was
asked to say. Reads heard.json (written by bsa.narrate.run); writes nothing."""

from __future__ import annotations

import json
from pathlib import Path

from bsa import closing as closing_mod
from bsa.narrate.local import WER_LIMIT
from bsa.narrate.run import HEARD_FILE
from bsa.review import worddiff
from bsa.short import Short, spoken_text

NOT_NARRATED = "Not narrated yet."


def _read(vo: Path) -> dict | None:
    try:
        data = json.loads((vo / HEARD_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _row(line_id: str, text: str, entry: dict | None, vo: Path, now_text: str | None) -> dict:
    """One line. Nothing heard counts for a line whose take is gone (a re-take is waiting) or whose words changed
    since: those show as unchecked, never with the old heard text."""
    row = {"id": line_id, "text": text, "said": None, "wer": None, "ok": None, "diff": None, "stale": False,
           "waiting": False}  # fmt: skip
    if entry is None:
        return row
    if not (vo / f"{line_id}.wav").is_file():
        row["waiting"] = True
        return row
    if now_text is not None and entry.get("text") != now_text:
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


def block(sdir: Path, short: Short, narration: dict) -> dict:
    """{"lines": [{"id", "text", "said", "wer", "ok", "diff", "stale", "waiting"}], "checked", "why_not"}: the short's
    lines in order, then the closing line when it was narrated."""
    vo = Path(sdir) / "audio" / "vo"
    data = _read(vo)
    if data is None:
        return {"lines": [], "checked": False, "why_not": NOT_NARRATED}
    heard = data.get("lines") if isinstance(data.get("lines"), dict) else {}
    heard = {k: v for k, v in heard.items() if isinstance(v, dict)}
    rows = [_row(ln.id, spoken_text(ln), heard.get(ln.id), vo, spoken_text(ln)) for ln in short.lines]
    extra = heard.get(closing_mod.LINE_ID)
    if extra is not None and all(r["id"] != closing_mod.LINE_ID for r in rows):
        rows.append(_row(closing_mod.LINE_ID, str(extra.get("text") or ""), extra, vo, None))
    why_not = data.get("why_not")
    return {
        "lines": rows,
        "checked": bool(data.get("checked")),
        "why_not": why_not if isinstance(why_not, str) else None,
    }
