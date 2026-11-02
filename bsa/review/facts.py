"""The fact panel on the review page's Story and Final stages: per line, the facts it cites and what is wrong with
them, and every number a viewer sees on screen with the fact that backs it. Reads the short only; writes nothing."""

from __future__ import annotations

import re
from decimal import Decimal
from urllib.parse import urlparse

from bsa.short import Short, label_text, line_fact_flags

FLAG_WORDS = {
    "number_without_fact": "Has a number but cites no fact",
    "unknown_fact": "Cites a fact that isn't in the list ({id})",
}

# One number: digit groups of three joined by commas (a thousands separator) or plain digits, then an optional
# decimal part. A comma anywhere else ends the number. Scanning left to right takes the whole run of digits, so a
# number is never read out of the middle of a longer one.
_NUMBER = re.compile(r"(?:\d{1,3}(?:,\d{3})+(?!\d)|\d+)(?:\.\d+)?")


def is_link(url: str) -> bool:
    parts = urlparse(url.strip())
    return parts.scheme in ("http", "https") and bool(parts.netloc)


def _normal(raw: str) -> str:
    return format(Decimal(raw.replace(",", "")).normalize(), "f")  # "f": never exponent form (1E+2 is "100")


def numbers(text: str) -> list[str]:
    """Every number in `text`, written one way: 1,800 is 1800, 3.50 is 3.5, 4.0 is 4."""
    return [_normal(m.group(0)) for m in _NUMBER.finditer(str(text))]


def _flag_word(code: str) -> str:
    kind, _, rest = code.partition(":")
    return FLAG_WORDS[kind].format(id=rest)


def _row(where: str, text: str, number: str, facts: list) -> dict:
    fact = next((f.id for f in facts if number in numbers(f.claim)), None)
    return {"where": where, "text": text, "number": number, "fact": fact, "ok": fact is not None}


def panel(short: Short) -> dict:
    """{"lines": [{"id", "text", "flags": [words], "facts": [{"id", "claim", "source", "is_link"}]}],
    "on_screen": [{"where", "text", "number", "fact", "ok"}], "flagged"}: a line's number is backed by a fact the line
    cites; a label's by a fact cited by any line of the same shot."""
    by_id = {f.id: f for f in short.facts}
    ids = set(by_id)
    lines, on_screen = [], []
    cited_in_shot: dict[str, list] = {}
    flagged = False
    for ln in short.lines:
        cited = [by_id[fid] for fid in dict.fromkeys(ln.facts) if fid in by_id]
        pool = cited_in_shot.setdefault(ln.shot, [])
        pool += [f for f in cited if f not in pool]
        flags = [_flag_word(c) for c in line_fact_flags(ln, ids)]
        flagged = flagged or bool(flags)
        facts = [{"id": f.id, "claim": f.claim, "source": f.source, "is_link": is_link(f.source)} for f in cited]
        lines.append({"id": ln.id, "text": ln.text, "flags": flags, "facts": facts})
        on_screen += [_row(f"line {ln.id}", ln.text, n, cited) for n in numbers(ln.text)]
    for shot in short.shots:
        for label in shot.labels:
            text = label_text(label)
            on_screen += [_row(f"shot {shot.id} label", text, n, cited_in_shot.get(shot.id, [])) for n in numbers(text)]
    flagged = flagged or any(not r["ok"] for r in on_screen)
    return {"lines": lines, "on_screen": on_screen, "flagged": flagged}
