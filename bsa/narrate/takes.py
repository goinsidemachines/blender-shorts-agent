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


def pauses(x: np.ndarray, sr: int = SR) -> list[tuple[int, int]]:
    """Sample ranges [start, end) of the pauses inside a take: runs of quiet 10 ms windows at least MIN_PAUSE_S
    long, in order. Leading and trailing silence are not pauses."""
    rms, win = _window_rms(x, sr)
    quiet = _quiet(rms)
    if quiet.all():
        return []
    min_windows = MIN_PAUSE_S * sr / win
    return [(a * win, b * win) for a, b in _runs(quiet) if a > 0 and b < len(quiet) and b - a >= min_windows - 1e-9]


def _speech_span(x: np.ndarray, sr: int) -> tuple[int, int]:
    rms, win = _window_rms(x, sr)
    loud = np.flatnonzero(~_quiet(rms))
    if len(loud) == 0:
        return 0, len(x)
    return int(loud[0]) * win, (int(loud[-1]) + 1) * win


def expected(span: tuple[int, int], texts: list[str]) -> list[int]:
    """Where each line break should fall, by each line's share of the characters across the speech span."""
    start, end = span
    total = sum(len(t) for t in texts) or 1
    out, acc = [], 0
    for t in texts[:-1]:
        acc += len(t)
        out.append(round(start + (end - start) * acc / total))
    return out


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


def _match(x, texts, ids, words, sr) -> list[tuple[int, int] | str]:
    """Per break, the matched pause or the reason none fits."""
    found = pauses(x, sr)
    gaps = word_gaps(words, texts, sr) if words else None
    out: list[tuple[int, int] | str] = []
    after = 0
    if gaps is None:
        span = _speech_span(x, sr)
        exp = expected(span, texts)
        bounds = [span[0], *((a + b) / 2 for a, b in itertools.pairwise(exp)), span[1]]
    for k in range(len(texts) - 1):
        free = [p for p in found if p[0] >= after]
        if gaps is None:
            pick = [p for p in free if bounds[k] <= (p[0] + p[1]) / 2 <= bounds[k + 1]]
            target = exp[k]
        else:
            g0, g1 = gaps[k]
            pick = [p for p in free if p[0] < max(g1, g0 + 1) and p[1] > g0]
            target = (g0 + g1) / 2
        if pick:
            best = min(pick, key=lambda p: abs((p[0] + p[1]) / 2 - target))
            out.append(best)
            after = best[1]
            continue
        a, b = ids[k], ids[k + 1]
        if gaps is not None:
            n = sum(len(t.split()) for t in texts[: k + 1])
            lo, hi = round(words[n - 1][1] * sr), round(words[n][2] * sr)
            spans = [(round(w[1] * sr), round(w[2] * sr)) for w in words]
            near = [p for p in free if p[0] < hi and p[1] > lo]
            if any(p[0] < we and p[1] > ws for p in near for ws, we in spans):
                out.append(f"the only pause between {a} and {b} is inside a word")
                continue
        out.append(f"no clear pause between {a} and {b}")
    return out


def _bounds(x: np.ndarray, pause: tuple[int, int], sr: int) -> tuple[int, int]:
    """(where the line before ends, where the next starts) in a matched pause.

    The pause is quiet by QUIET_DB, so a soft first consonant (or a soft last one) can sit inside it. The cut points
    stay in the pause's silent core (quieter than the onset detector's threshold, with one window of margin), so
    neither is ever cut into. A long pause keeps TAIL_S after the last word and LEAD_S before the next first word;
    a shorter one gives the next line its lead first (up to LEAD_S) and both meet there."""
    ps, pe = pause
    lead, tail = round(LEAD_S * sr), round(TAIL_S * sr)
    rms, win = _window_rms(x, sr)
    peak = float(rms.max())
    silent_level = max(peak * 10 ** (-LEAD_BELOW_PEAK_DB / 20), 10 ** (LEAD_FLOOR_DBFS / 20))
    inside = rms[ps // win : pe // win]
    silent = np.flatnonzero(inside < silent_level)
    if len(silent) >= 3:
        cs, ce = ps + (int(silent[0]) + 1) * win, ps + int(silent[-1]) * win
    elif len(silent):
        cs = ce = ps + int((silent[0] + silent[-1] + 1) * win // 2)
    else:
        cs = ce = (ps + pe) // 2
    start = min(max(pe - lead, cs), ce)
    end = min(max(ps + tail, cs), start)
    return end, start


def cut(x: np.ndarray, texts: list[str], ids: list[str], words=None, sr: int = SR) -> list[Piece]:
    """One Piece per line: the samples it takes from the take, or start=end=None with the reason it can't be cut."""
    n = len(texts)
    if n == 1:
        return [Piece(0, 0, len(x), None)]
    matched = _match(x, texts, ids, words, sr)
    cuts = [_bounds(x, m, sr) if isinstance(m, tuple) else None for m in matched]
    pieces = []
    for i in range(n):
        before = matched[i - 1] if i else None
        after = matched[i] if i < n - 1 else None
        if isinstance(before, str) or isinstance(after, str):
            reason = after if isinstance(after, str) else before
            pieces.append(Piece(i, None, None, reason))
            continue
        start = cuts[i - 1][1] if i else 0
        end = cuts[i][0] if i < n - 1 else len(x)
        pieces.append(Piece(i, start, end, None))
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
