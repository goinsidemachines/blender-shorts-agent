"""Word differences between what a line should say and what Whisper heard, for the voice check on the review page."""

from __future__ import annotations

import difflib
import re

_WORD = re.compile(r"[a-z0-9]+(?:'[a-z0-9]+)*")


def words(text: str) -> list[str]:
    """Lower case words: punctuation dropped (an apostrophe inside a word kept), hyphens and dashes split words,
    digits as they are (no number-to-word conversion)."""
    return _WORD.findall(str(text).lower().replace("\u2019", "'").replace("\u2018", "'"))


def diff(want: str, got: str) -> list[list[str]]:
    """`[op, word]` over words(): `same`; `missing` (the word is in `want` only); `extra` (in `got` only); `changed`
    (a word of `want` heard as another: the word heard). An extra or missing word is shown after an equal word it
    could just as well come before, as diff tools do."""
    a, b = words(want), words(got)
    ops: list[list[str]] = []
    for tag, i0, i1, j0, j1 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag == "equal":
            ops += [["same", w] for w in a[i0:i1]]
        elif tag == "delete":
            ops += [["missing", w] for w in a[i0:i1]]
        elif tag == "insert":
            ops += [["extra", w] for w in b[j0:j1]]
        else:
            paired = min(i1 - i0, j1 - j0)
            ops += [["changed", w] for w in b[j0 : j0 + paired]]
            ops += [["missing", w] for w in a[i0 + paired : i1]]
            ops += [["extra", w] for w in b[j0 + paired : j1]]
    for i in range(len(ops) - 1, -1, -1):  # slide an extra/missing word right over equal words
        k = i
        while k + 1 < len(ops) and ops[k][0] in ("extra", "missing") and ops[k + 1] == ["same", ops[k][1]]:
            ops[k], ops[k + 1] = ops[k + 1], ops[k]
            k += 1
    return ops
