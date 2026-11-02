"""What is waiting for Claude on a short's review page: notes the user left and answers Claude has not acted on."""

from __future__ import annotations

from pathlib import Path

from bsa.review import decisions, notes


def page_notes(sdir: Path) -> dict | None:
    """{"count", "ids", "decisions"}: open or sent notes, and ids of decisions not applied yet. None when there is
    nothing. Never raises: a damaged file gives {"problem": "..."}."""
    try:
        ids = [n["id"] for n in notes.load(sdir) if n["status"] in ("open", "sent")]
        waiting = [d["id"] for d in decisions.unapplied(sdir)]
    except Exception as exc:  # noqa: BLE001 - a bad file must never break a command's result
        return {"problem": " ".join(str(exc).split()) or type(exc).__name__}
    if not ids and not waiting:
        return None
    return {"count": len(ids), "ids": ids, "decisions": waiting}
