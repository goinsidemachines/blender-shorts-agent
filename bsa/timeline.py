"""Frame timeline for a short: shots, lines and caption cards. Frames are 1-based and inclusive."""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import asdict, dataclass

from bsa.short import Short

LEAD_IN_S = 0.2
GAP_S = 0.35
TAIL_S = 0.6
END_CARD_S = 1.5  # hold added to the last shot for the @handle end card, after the last line
READ_WPS = 2.6
READ_MIN_S = 1.2
CARD_MAX_WORDS = 6
CARD_MAX_CHARS = 32
ORPHAN_MAX_CHARS = 8  # a one-word card this short never stands alone when it can join a neighbour

_SENTENCE = re.compile(r"(?<=[.!?…])\s+")
_CLAUSE = re.compile(r"(?<=[,;:—–])\s+")


def reading_time(text: str) -> float:
    return max(READ_MIN_S, len(text.split()) / READ_WPS + 0.3)


def _frames(seconds: float, fps: int) -> int:
    # round() first so float noise cannot add a frame
    return math.ceil(round(seconds * fps, 6))


def _pack(words: list[str]) -> list[str]:
    cards: list[str] = []
    cur: list[str] = []
    for w in words:
        trial = " ".join([*cur, w])
        if cur and (len(cur) >= CARD_MAX_WORDS or len(trial) > CARD_MAX_CHARS):
            cards.append(" ".join(cur))
            cur = []
        cur.append(w)
    if cur:
        cards.append(" ".join(cur))
    return cards


def _orphan(card: str) -> bool:
    """A card of a single short word ("one.")."""
    return len(card.split()) == 1 and len(card) <= ORPHAN_MAX_CHARS


def _fits(a: str, b: str) -> bool:
    """Whether two cards may be joined: a little over the usual limits, to avoid an orphan."""
    joined = f"{a} {b}"
    return len(joined) <= CARD_MAX_CHARS + 6 and len(joined.split()) <= CARD_MAX_WORDS + 1


def _join_orphans(cards: list[str]) -> list[str]:
    """Join each one-word card to its neighbour: to the next card when it leads into it ("Then,"), else to the
    previous one; when neither fits, move the split point so the previous card's last word comes down with it
    (never across the end of a sentence)."""
    out = list(cards)
    i = 0
    while i < len(out):
        if _orphan(out[i]):
            if out[i][-1] in ",;:—–" and i + 1 < len(out) and _fits(out[i], out[i + 1]):
                out[i : i + 2] = [f"{out[i]} {out[i + 1]}"]
                continue
            if i > 0 and _fits(out[i - 1], out[i]):
                out[i - 1 : i + 1] = [f"{out[i - 1]} {out[i]}"]
                continue
            head = out[i - 1].split() if i > 0 else []
            if len(head) > 1 and head[-1][-1] not in ".!?…":
                out[i - 1 : i + 1] = [" ".join(head[:-1]), f"{head[-1]} {out[i]}"]
        i += 1
    return out


def chunk_caption(text: str) -> list[str]:
    """Split on sentence then clause boundaries, then by the word and character limits; never leave a one-word card
    that can join a neighbour."""
    cards: list[str] = []
    for sentence in _SENTENCE.split(text.strip()):
        for clause in _CLAUSE.split(sentence):
            words = clause.split()
            if words:
                cards += _pack(words)
    return _join_orphans(cards)


@dataclass
class Span:
    id: str
    f0: int
    f1: int


@dataclass
class Card:
    text: str
    line_id: str
    f0: int
    f1: int


@dataclass
class Timeline:
    fps: int
    n_frames: int
    shots: list[Span]
    lines: list[Span]
    cards: list[Card]


def end_card_start(tl: Timeline) -> int:
    """The first frame of the end card: the frame after the last caption or line (past the end when there is none
    left: then no end card shows)."""
    return max([c.f1 for c in tl.cards] + [ln.f1 for ln in tl.lines] + [0]) + 1


def _split_cards(text: str, line_id: str, f0: int, f1: int) -> list[Card]:
    """Share the line's frames between its cards in proportion to their characters."""
    n = f1 - f0 + 1
    if n <= 0:  # a zero-length take: no frames, so no cards
        return []
    pieces = chunk_caption(text)
    while len(pieces) > n:  # fewer frames than cards: merge the shortest adjacent pair
        i = min(range(len(pieces) - 1), key=lambda j: len(pieces[j]) + len(pieces[j + 1]))
        pieces[i : i + 2] = [f"{pieces[i]} {pieces[i + 1]}"]
    k = len(pieces)
    if k == 0:
        return []
    total = sum(len(p) for p in pieces)
    ends = []  # exclusive end offset of each card within the line
    cum = 0
    for p in pieces:
        cum += len(p)
        ends.append(round(n * cum / total))
    ends[-1] = n
    for i in range(k):  # every card gets at least one frame
        ends[i] = max(ends[i], (ends[i - 1] if i else 0) + 1)
    for i in range(k - 1, -1, -1):
        ends[i] = min(ends[i], (ends[i + 1] - 1) if i < k - 1 else n)
    cards = []
    start = 0
    for piece, end in zip(pieces, ends, strict=True):
        cards.append(Card(piece, line_id, f0 + start, f0 + end - 1))
        start = end
    return cards


def build_timeline(short: Short, durations: dict[str, float] | None, end_card: bool = False) -> Timeline:
    """Lay shots out back to back; durations maps line id to its audio length in seconds. With `end_card` (the user
    has a handle) the last shot holds END_CARD_S longer, so the end card never covers a line or its caption."""
    fps = short.fps
    lead = round(LEAD_IN_S * fps)
    gap = _frames(GAP_S, fps)
    tail = _frames(TAIL_S, fps)
    shots: list[Span] = []
    lines: list[Span] = []
    cards: list[Card] = []
    cursor = 1
    for shot in short.shots:
        mine = [ln for ln in short.lines if ln.shot == shot.id]
        lens = []
        for ln in mine:
            secs = durations.get(ln.id) if durations is not None else None
            lens.append(_frames(secs if secs is not None else reading_time(ln.text), fps))
        content = lead + sum(lens) + gap * max(len(mine) - 1, 0) + tail
        length = max(_frames(shot.min_s, fps), content)
        if end_card and shot is short.shots[-1]:
            length += _frames(END_CARD_S, fps)
        shots.append(Span(shot.id, cursor, cursor + length - 1))
        pos = cursor + lead
        for ln, n in zip(mine, lens, strict=True):
            lines.append(Span(ln.id, pos, pos + n - 1))
            cards += _split_cards(ln.text, ln.id, pos, pos + n - 1)
            pos += n + gap
        cursor += length
    return Timeline(fps, cursor - 1, shots, lines, cards)


def length_drift(short: Short, tl: Timeline) -> float:
    """Fraction by which the timeline's length is off the short's target length_s."""
    return abs(tl.n_frames / tl.fps - short.length_s) / short.length_s


LENGTH_TOLERANCE = 0.15  # validate warns when the timeline is off the target length by more than this


def length_warning(short: Short, tl: Timeline, tolerance: float = LENGTH_TOLERANCE) -> str | None:
    """The validate warning when the timeline is more than `tolerance` off the short's target length_s, with the
    estimated seconds and the direction ("about 65 s, 44% over its 45 s target"); None when it is close enough or
    the short has no target."""
    if short.length_s <= 0:
        return None
    drift = length_drift(short, tl)
    if drift <= tolerance:
        return None
    seconds = tl.n_frames / tl.fps
    way = "over" if seconds > short.length_s else "under"
    return (f"at reading pace the short runs about {seconds:.0f} s, {drift:.0%} {way} its {short.length_s:g} s "
            "target")  # fmt: skip


SILENT_LIMIT = 0.6  # a shot more than this share silent drags (fast-paced shorts hold briefly)
QUICK_BEAT_S = 2.5  # a silent shot this short or shorter is a quick visual beat, not a slow hold
SPEECH_ROOM = 1.25  # a suggested shot length: its speech plus 25%


def pacing(short: Short, tl: Timeline, end_card: bool = False) -> list[dict]:
    """Per shot: its length, the seconds a line is spoken in it and the seconds nothing is said (`seconds`,
    `spoken_s`, `silent_s`, `silent_frac`), `too_silent` (more than SILENT_LIMIT silent and longer than a quick
    beat) and `suggest_s` (speech + 25%, never less than the lines need). With `end_card` the last shot's end-card
    hold is left out: it is there on purpose."""
    fps = tl.fps
    lead, gap, tail = round(LEAD_IN_S * fps), _frames(GAP_S, fps), _frames(TAIL_S, fps)
    out = []
    for i, span in enumerate(tl.shots):
        frames = span.f1 - span.f0 + 1
        if end_card and i == len(tl.shots) - 1:
            frames -= _frames(END_CARD_S, fps)
        mine = [ln for ln in short.lines if ln.shot == span.id]
        spoken = sum(s.f1 - s.f0 + 1 for s in tl.lines if s.id in {ln.id for ln in mine} and span.f0 <= s.f0 <= span.f1)
        need = lead + spoken + gap * max(len(mine) - 1, 0) + tail if mine else 0
        seconds, spoken_s = frames / fps, spoken / fps
        silent_s = max(seconds - spoken_s, 0.0)
        frac = silent_s / seconds if seconds > 0 else 0.0
        suggest = max(math.ceil(spoken_s * SPEECH_ROOM * 10 - 1e-6) / 10, math.ceil(need / fps * 10 - 1e-6) / 10)
        out.append({
            "shot": span.id, "seconds": round(seconds, 2), "spoken_s": round(spoken_s, 2),
            "silent_s": round(silent_s, 2), "silent_frac": round(frac, 3),
            "too_silent": frac > SILENT_LIMIT and seconds > QUICK_BEAT_S, "suggest_s": suggest,
        })  # fmt: skip
    return out


def pacing_warnings(short: Short, tl: Timeline, end_card: bool = False) -> list[str]:
    """One warning per shot that is mostly silent, with a length to try."""
    out = []
    for row in pacing(short, tl, end_card):
        if not row["too_silent"]:
            continue
        if row["spoken_s"] == 0:
            out.append(f"shot {row['shot']} has no narration for {row['seconds']:.1f} s: give it a line, or keep it "
                       f"a quick beat (about {QUICK_BEAT_S:g} s or less, lower its min_s) unless the hold is the point")  # fmt: skip
        else:
            out.append(f"shot {row['shot']} is {row['silent_frac']:.0%} silent ({row['silent_s']:.1f} of "
                       f"{row['seconds']:.1f} s): for a fast-paced short try about {row['suggest_s']:.1f} s (speech "
                       "+ 25%) by lowering its min_s, unless the hold is the point")  # fmt: skip
    return out


def held_frames(shot, span: Span, fps: int) -> set[int]:
    """The frames of `span` that the shot's `hold` declares a deliberate hold (true: all of them; ranges in seconds
    from the shot's start, inclusive)."""
    hold = getattr(shot, "hold", False)
    if hold is True:
        return set(range(span.f0, span.f1 + 1))
    out: set[int] = set()
    for a, b in hold or []:
        lo, hi = span.f0 + round(a * fps), min(span.f0 + round(b * fps), span.f1)
        out.update(range(max(lo, span.f0), hi + 1))
    return out


def to_json(tl: Timeline) -> dict:
    return asdict(tl)


def timeline_hash(data: dict) -> str:
    """A short fingerprint of a timeline's JSON (as in source/timeline.json). build.py stamps it on every scene it
    builds (scene["bsa_timeline"]) so the Blender commands can tell a .blend built for another timing."""
    text = json.dumps(data, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
