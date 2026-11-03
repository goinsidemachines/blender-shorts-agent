"""Take mode (pure): group a short's lines into paragraph takes, find the pauses in a spoken take, match one pause
to each line break and cut the take back into one WAV per line.

A cut never trims speech: the take's head and tail stay on its first and last line, and a line keeps up to LEAD_S of
the pause before its first word (a soft first consonant sits inside that pause, below the pause threshold, and is
never cut into). A break with no clear pause, or whose only pause is inside a word, leaves the lines it joins uncut:
they are spoken on their own instead.
"""

from __future__ import annotations

import itertools
import os
import re
import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from bsa import closing, timeline
from bsa.narrate.contract import LEAD_BELOW_PEAK_DB, LEAD_FLOOR_DBFS, SR
from bsa.short import Line, Shot, spoken_text

TAKE_CHARS = 320  # longer takes can drift, so a take is a paragraph, not the whole script
QUIET_DB = -40.0  # a 10 ms window this far below the take's loudest one is quiet
MIN_PAUSE_S = 0.12
WINDOW_S = 0.01
LEAD_S = 0.25  # pause kept before a line's first word
TAIL_S = 0.35  # pause kept after a line's last word
HOLD_BREAK_S = 1.0  # a shot held this much longer than its lines need ends the take


def take_id(i: int) -> str:
    return f"t{i:02d}"


def joined(lines: list[Line]) -> str:
    return " ".join(spoken_text(ln) for ln in lines)


def _breaks_after(shot: Shot | None, lines: list[Line]) -> bool:
    """A take ends at the end of this shot: it has a hold, or is held much longer than its lines need."""
    if shot is None:
        return False
    if shot.hold is True or (isinstance(shot.hold, list) and shot.hold):
        return True
    own = [ln for ln in lines if ln.shot == shot.id]
    need = sum(timeline.reading_time(ln.text) for ln in own) + timeline.LEAD_IN_S + timeline.TAIL_S
    return shot.min_s > need + HOLD_BREAK_S


def group(lines: list[Line], shots: list[Shot]) -> list[list[Line]]:
    """Consecutive lines in paragraph-sized takes, in order (see the module doc)."""
    by_id = {s.id: s for s in shots}
    groups: list[list[Line]] = []
    cur: list[Line] = []
    for ln in lines:
        if ln.id == closing.LINE_ID:
            if cur:
                groups.append(cur)
                cur = []
            groups.append([ln])
            continue
        if cur and (
            len(joined([*cur, ln])) > TAKE_CHARS
            or (ln.shot != cur[-1].shot and _breaks_after(by_id.get(cur[-1].shot), lines))
        ):
            groups.append(cur)
            cur = []
        cur.append(ln)
    if cur:
        groups.append(cur)
    return groups


def _window_rms(x: np.ndarray, sr: int) -> tuple[np.ndarray, int]:
    """RMS of each whole 10 ms window (a trailing part window is left out), and the window length in samples."""
    win = max(1, round(WINDOW_S * sr))
    n = len(x) // win
    x = np.asarray(x, dtype=np.float64)
    return np.sqrt(np.mean(x[: n * win].reshape(n, win) ** 2, axis=1)), win


def _quiet(rms: np.ndarray) -> np.ndarray:
    peak = float(rms.max()) if len(rms) else 0.0
    if peak <= 0:
        return np.ones(len(rms), dtype=bool)
    return rms < peak * 10 ** (QUIET_DB / 20)


def _runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """[i, j) window ranges where mask is true."""
    padded = np.concatenate([[False], mask, [False]]).astype(np.int8)
    edges = np.flatnonzero(np.diff(padded))
    return [(int(a), int(b)) for a, b in zip(edges[::2], edges[1::2])]


def _pauses(rms: np.ndarray, win: int, sr: int) -> list[tuple[int, int]]:
    quiet = _quiet(rms)
    if quiet.all():
        return []
    min_windows = MIN_PAUSE_S * sr / win
    return [(a * win, b * win) for a, b in _runs(quiet) if a > 0 and b < len(quiet) and b - a >= min_windows - 1e-9]


def pauses(x: np.ndarray, sr: int = SR) -> list[tuple[int, int]]:
    """Sample ranges [start, end) of the pauses inside a take: runs of quiet 10 ms windows at least MIN_PAUSE_S
    long, in order. Leading and trailing silence are not pauses.

    Pauses are measured in whole windows: a window that is only partly quiet is not, so a pause can be up to one
    window shorter at each end than the silence it lies in (and never covers sound above the quiet level)."""
    rms, win = _window_rms(x, sr)
    return _pauses(rms, win, sr)


def _span(rms: np.ndarray, win: int, n: int) -> tuple[int, int]:
    loud = np.flatnonzero(~_quiet(rms))
    if len(loud) == 0:
        return 0, n
    return int(loud[0]) * win, (int(loud[-1]) + 1) * win


def _speech_span(x: np.ndarray, sr: int) -> tuple[int, int]:
    rms, win = _window_rms(x, sr)
    return _span(rms, win, len(x))


def _shares(span: tuple[int, int], lengths: list[int]) -> list[int]:
    start, end = span
    total = sum(lengths) or 1
    out, acc = [], 0
    for n in lengths[:-1]:
        acc += n
        out.append(round(start + (end - start) * acc / total))
    return out


def expected(span: tuple[int, int], texts: list[str]) -> list[int]:
    """Where each line break should fall, by each line's share of the characters across the speech span."""
    return _shares(span, [len(t) for t in texts])


_SENTENCE_END = re.compile(r"[.!?;:]\s+")


def _segments(texts: list[str]) -> tuple[list[int], list[tuple[int, bool]]]:
    """The take's text cut at line ends and at sentence ends inside a line ([.!?;:] then a space): each segment's
    length in characters, and per boundary between segments (k, True) for the break after line k or (k, False) for
    a sentence end inside line k."""
    lengths: list[int] = []
    kinds: list[tuple[int, bool]] = []
    for k, t in enumerate(texts):
        cuts = [m.end() for m in _SENTENCE_END.finditer(t) if t[m.end() :].strip()]
        edges = [0, *cuts, len(t)]
        for j, (a, b) in enumerate(itertools.pairwise(edges)):
            if j:
                kinds.append((k, False))
            lengths.append(b - a)
        if k < len(texts) - 1:
            kinds.append((k, True))
    return lengths, kinds


def word_gaps(words: list[tuple[str, float, float]], texts: list[str], sr: int = SR) -> list[tuple[int, int]] | None:
    """Per line break, the samples from line k's last word end to line k+1's first word start; None when the words
    heard don't number the words written (then the character estimate is used)."""
    counts = [len(t.split()) for t in texts]
    if not words or sum(counts) != len(words) or any(c == 0 for c in counts):
        return None
    out, i = [], 0
    for c in counts[:-1]:
        i += c
        out.append((round(words[i - 1][2] * sr), round(words[i][1] * sr)))
    return out


@dataclass(frozen=True)
class Piece:
    line: int
    start: int | None
    end: int | None
    reason: str | None
    # Set on every piece when word timings were given but couldn't be used (the take was cut by character timing).
    note: str | None = None


def _assign(found: list[tuple[int, int]], targets: list[int], lo: list[float], hi: list[float]) -> list[int | None]:
    """In-order assignment of pauses to boundaries: boundary i may take a pause whose middle lies strictly between
    lo[i] and hi[i]; as many boundaries as possible get one, then the least total distance from the targets.
    Returns the pause index per boundary, or None."""
    m, q = len(targets), len(found)
    mids = [(a + b) / 2 for a, b in found]
    # best[i][j] = (matches, -distance) using the first i boundaries and the first j pauses
    best = [[(0, 0.0)] * (q + 1) for _ in range(m + 1)]
    move = [[0] * (q + 1) for _ in range(m + 1)]  # 1 skip boundary, 2 skip pause, 3 match
    for i in range(1, m + 1):
        best[i][0], move[i][0] = best[i - 1][0], 1
        for j in range(1, q + 1):
            options = [(best[i - 1][j], 1), (best[i][j - 1], 2)]
            if lo[i - 1] < mids[j - 1] < hi[i - 1]:
                n, d = best[i - 1][j - 1]
                options.append(((n + 1, d - abs(mids[j - 1] - targets[i - 1])), 3))
            best[i][j], move[i][j] = max(options, key=lambda o: o[0])
    out: list[int | None] = [None] * m
    i, j = m, q
    while i > 0 and j > 0:
        mv = move[i][j]
        if mv == 3:
            out[i - 1] = j - 1
            i, j = i - 1, j - 1
        elif mv == 1:
            i -= 1
        else:
            j -= 1
    return out


def _match(found, span, texts, ids, words, sr) -> tuple[list[tuple[int, int] | str], str | None]:
    """Per line break, the matched pause or the reason none fits; and a note when word timings were given but
    couldn't be used."""
    gaps = word_gaps(words, texts, sr) if words else None
    note = None
    if words and gaps is None:
        heard, written = len(words), sum(len(t.split()) for t in texts)
        note = (
            f"the words heard ({heard}) don't match the script's ({written}), so the take was cut by character timing"
        )
    out: list[tuple[int, int] | str] = []
    if gaps is None:
        # Sentence ends inside a line are boundaries too, so a pause after "…. Right." is never taken for a line break.
        lengths, kinds = _segments(texts)
        targets = _shares(span, lengths)
        # Each line's expected stretch; a line break may land anywhere in the two lines it joins, a sentence end
        # anywhere in its own line (in order, so neither can take a pause that belongs to the other).
        edges = [span[0], *expected(span, texts), span[1]]
        lo = [edges[k] for k, _ in kinds]
        hi = [edges[k + 2] if is_break else edges[k + 1] for k, is_break in kinds]
        chosen = _assign(found, targets, lo, hi)
        for b, (k, is_break) in enumerate(kinds):
            if not is_break:
                continue
            out.append(
                found[chosen[b]] if chosen[b] is not None else f"no clear pause between {ids[k]} and {ids[k + 1]}"
            )
        return out, note
    after = 0
    for k in range(len(texts) - 1):
        free = [p for p in found if p[0] >= after]
        g0, g1 = sorted(gaps[k])
        pick = [p for p in free if p[0] < g1 + 1 and p[1] > g0]
        if pick:
            best = min(pick, key=lambda p: abs((p[0] + p[1]) / 2 - (g0 + g1) / 2))
            out.append(best)
            after = best[1]
            continue
        a, b = ids[k], ids[k + 1]
        n = sum(len(t.split()) for t in texts[: k + 1])
        lo, hi = round(words[n - 1][1] * sr), round(words[n][2] * sr)
        spans = [(round(w[1] * sr), round(w[2] * sr)) for w in words]
        near = [p for p in free if p[0] < hi and p[1] > lo]
        if any(p[0] < we and p[1] > ws for p in near for ws, we in spans):
            out.append(f"the only pause between {a} and {b} is inside a word")
        else:
            out.append(f"no clear pause between {a} and {b}")
    return out, note


FLOOR_MARGIN_DB = 6.0  # a window within this much of the pause's quietest one is silent too (a noise floor)


def _bounds(rms: np.ndarray, win: int, pause: tuple[int, int], sr: int) -> tuple[int, int]:
    """(where the line before ends, where the next starts) in a matched pause.

    The pause is quiet by QUIET_DB, so a soft first consonant (or a soft last one) can sit inside it. The cut points
    stay in the pause's silent core: windows quieter than the onset detector's threshold, or within FLOOR_MARGIN_DB
    of the pause's own quietest window (a mic's noise floor), with one window of margin at each end. The next line
    starts up to LEAD_S before the core's end (where its first sound begins) and the line before ends up to TAIL_S
    after the core's start; in a short pause the next line's lead comes first and both meet there."""
    ps, pe = pause
    lead, tail = round(LEAD_S * sr), round(TAIL_S * sr)
    inside = rms[ps // win : pe // win]
    if len(inside) == 0:
        mid = (ps + pe) // 2
        return mid, mid
    peak = float(rms.max())
    level = max(peak * 10 ** (-LEAD_BELOW_PEAK_DB / 20), 10 ** (LEAD_FLOOR_DBFS / 20))
    level = max(level, float(inside.min()) * 10 ** (FLOOR_MARGIN_DB / 20))
    silent = np.flatnonzero(inside <= level)
    if len(silent) >= 3:
        cs, ce = ps + (int(silent[0]) + 1) * win, ps + int(silent[-1]) * win
    else:  # a core of one or two windows: cut at the start of the quietest one
        cs = ce = ps + int(np.argmin(inside)) * win
    start = max(ce - lead, cs)
    end = min(cs + tail, start)
    return end, start


def cut(x: np.ndarray, texts: list[str], ids: list[str], words=None, sr: int = SR) -> list[Piece]:
    """One Piece per line: the samples it takes from the take, or start=end=None with the reason it can't be cut."""
    n = len(texts)
    if n == 1:
        return [Piece(0, 0, len(x), None)]
    rms, win = _window_rms(x, sr)
    matched, note = _match(_pauses(rms, win, sr), _span(rms, win, len(x)), texts, ids, words, sr)
    cuts = [_bounds(rms, win, m, sr) if isinstance(m, tuple) else None for m in matched]
    pieces = []
    for i in range(n):
        before = matched[i - 1] if i else None
        after = matched[i] if i < n - 1 else None
        if isinstance(before, str) or isinstance(after, str):
            reason = after if isinstance(after, str) else before
            pieces.append(Piece(i, None, None, reason, note))
            continue
        start = cuts[i - 1][1] if i else 0
        end = cuts[i][0] if i < n - 1 else len(x)
        pieces.append(Piece(i, start, end, None, note))
    return pieces


def write_pieces(x: np.ndarray, pieces: list[Piece], ids: list[str], out_dir: Path) -> dict[str, float]:
    """Write <id>.wav (48 kHz mono 16-bit, via <id>.wav.part + os.replace) for every cut piece; seconds per id."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    secs: dict[str, float] = {}
    for p in pieces:
        if p.start is None or p.end is None:
            continue
        samples = np.asarray(x[p.start : p.end], dtype=np.float64)
        pcm = np.clip(np.round(samples * 32767), -32768, 32767).astype("<i2")
        path = out_dir / f"{ids[p.line]}.wav"
        part = path.with_name(path.name + ".part")
        try:
            with wave.open(str(part), "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(SR)
                w.writeframes(pcm.tobytes())
            os.replace(part, path)
        finally:
            part.unlink(missing_ok=True)
        secs[ids[p.line]] = round(len(pcm) / SR, 3)
    return secs
