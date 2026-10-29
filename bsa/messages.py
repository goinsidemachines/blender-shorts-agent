"""Plain one-line messages: whitespace squashed and long text trimmed at a sentence or clause boundary."""

from __future__ import annotations

LIMIT = 200
_BOUNDARIES = (". ", "; ", ": ")


def trim(text: str, limit: int = LIMIT) -> str:
    """`text` cut to at most `limit` characters plus "…": at the last ". ", "; " or ": " within the limit (when that
    keeps at least half of it), else at the last space, never mid-word."""
    text = str(text).strip()
    if len(text) <= limit:
        return text
    head = text[: limit + 1]  # one more: a boundary right at the limit counts
    cut = max(head.rfind(b) for b in _BOUNDARIES)
    if cut < limit // 2:
        cut = head.rfind(" ")
    if cut < limit // 2:
        cut = limit
    return text[:cut].rstrip(" .;:,") + "…"


def one_line(text, limit: int | None = None) -> str:
    """`text` on one line ("failed" when empty), trimmed to `limit` when given."""
    line = " ".join(str(text).split()) or "failed"
    return trim(line, limit) if limit else line
