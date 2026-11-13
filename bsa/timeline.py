"""Frame timeline for a short: shots, lines and caption cards. Frames are 1-based and inclusive."""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import asdict, dataclass

from bsa.short import Short, camera_problems, one_take

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


def build_timeline(short: Short, durations: dict[str, float] | None, end_card: bool = False,
                   onsets: dict[str, float] | None = None) -> Timeline:  # fmt: skip
    """Lay shots out back to back; durations maps line id to its audio length in seconds. With `end_card` (the user
    has a handle) the last shot holds END_CARD_S longer, so the end card never covers a line or its caption.
    `onsets` maps line id to the seconds of silence its take opens with (bsa.narrate.run.takes_onsets): the line and
    its audio keep their frames, its caption cards start when the voice does."""
    if one_take(short):
        return _one_take_timeline(short, durations, end_card, onsets)
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
            wait = min(round(float((onsets or {}).get(ln.id) or 0.0) * fps), max(n - 1, 0))
            cards += _split_cards(ln.text, ln.id, pos + max(wait, 0), pos + n - 1)
            pos += n + gap
        cursor += length
    return Timeline(fps, cursor - 1, shots, lines, cards)


class WindowOverrun(ValueError):
    """A one-take short's lines need more time than their window on the camera path. `timeline` is the timeline it
    would have been (the windows, with the overlong lines running past their window's end): a page can still show
    the shots while it says what to fix."""

    def __init__(self, message: str, timeline: Timeline | None = None):
        super().__init__(message)
        self.timeline = timeline


def _up(seconds: float) -> float:
    """Seconds rounded up to a tenth, so a one-frame shortfall never reads as 0.0 s."""
    return math.ceil(seconds * 10 - 1e-6) / 10


def _down(seconds: float) -> float:
    """Seconds rounded down to a tenth: a window's length beside a need rounded up never reads as the same."""
    return math.floor(seconds * 10 + 1e-6) / 10


def window_frames(short: Short) -> dict[str, tuple[int, int]]:
    """A one-take short's windows: shot id -> (f0, f1), 1-based and inclusive. A window starts on the frame of its
    shot's `at` and runs to the frame before the next one; the last ends at camera.seconds. ValueError when the
    windows do not validate, or two shots share an id (the timeline is never built from them)."""
    if not one_take(short):
        raise ValueError("not a one-take short: its shots have no windows (set camera.style to one-take)")
    seen: set[str] = set()
    for s in short.shots:
        if s.id in seen:
            raise ValueError(f"shot id {s.id!r} is used twice: give every shot its own id, each window is one shot's")
        seen.add(s.id)
    problems = camera_problems(short)
    if problems:
        raise ValueError("; ".join(problems))
    fps = short.fps
    starts = [round(float(s.at) * fps) + 1 for s in short.shots]
    ends = [f - 1 for f in starts[1:]] + [round(float(short.camera["seconds"]) * fps)]
    return {s.id: (f0, f1) for s, f0, f1 in zip(short.shots, starts, ends, strict=True)}


def _one_take_timeline(short: Short, durations: dict[str, float] | None, end_card: bool,
                       onsets: dict[str, float] | None) -> Timeline:  # fmt: skip
    """The picture sets the clock: each shot is its window; its lines start at the window's start plus the lead and
    follow with the usual gaps; a window with time left over holds. min_s is not used. WindowOverrun (every window
    too short, joined by "; ") when a window's lines need more than it has."""
    fps = short.fps
    lead = round(LEAD_IN_S * fps)
    gap = _frames(GAP_S, fps)
    tail = _frames(TAIL_S, fps)
    windows = window_frames(short)
    ids = list(windows)
    shots: list[Span] = []
    lines: list[Span] = []
    cards: list[Card] = []
    overruns: list[str] = []
    for i, shot in enumerate(short.shots):
        f0, f1 = windows[shot.id]
        mine = [ln for ln in short.lines if ln.shot == shot.id]
        lens = []
        for ln in mine:
            secs = durations.get(ln.id) if durations is not None else None
            lens.append(_frames(secs if secs is not None else reading_time(ln.text), fps))
        need = lead + sum(lens) + gap * (len(mine) - 1) + tail if mine else 0
        last = i == len(ids) - 1
        if end_card and last:
            need += _frames(END_CARD_S, fps)
        have = f1 - f0 + 1
        if need > have:
            fix = "make camera.seconds {d:.1f} s longer" if last else f"move {ids[i + 1]}'s start {{d:.1f}} s later"
            overruns.append(f"{shot.id}'s lines need {_up(need / fps):.1f} s but its window is {_down(have / fps):.1f} s: "
                            "shorten them or " + fix.format(d=_up((need - have) / fps)))  # fmt: skip
        shots.append(Span(shot.id, f0, f1))
        pos = f0 + lead
        for ln, n in zip(mine, lens, strict=True):
            lines.append(Span(ln.id, pos, pos + n - 1))
            wait = min(round(float((onsets or {}).get(ln.id) or 0.0) * fps), max(n - 1, 0))
            cards += _split_cards(ln.text, ln.id, pos + max(wait, 0), pos + n - 1)
            pos += n + gap
    tl = Timeline(fps, windows[ids[-1]][1] if ids else 0, shots, lines, cards)
    if overruns:
        raise WindowOverrun("; ".join(overruns), tl)
    return tl


def length_drift(short: Short, tl: Timeline) -> float:
    """Fraction by which the timeline's length is off the short's target length_s."""
    return abs(tl.n_frames / tl.fps - short.length_s) / short.length_s


LENGTH_TOLERANCE = 0.15  # validate warns when the timeline is off the target length by more than this


def length_warning(short: Short, tl: Timeline, tolerance: float = LENGTH_TOLERANCE,
                   extra_s: float = 0.0) -> str | None:  # fmt: skip
    """The validate warning when the timeline plus `extra_s` (the closing card after the story) is more than
    `tolerance` off the short's target length_s, with the estimated seconds and the direction ("about 65 s, 44% over
    its 45 s target"); None when it is close enough or the short has no target."""
    if short.length_s <= 0:
        return None
    seconds = tl.n_frames / tl.fps + extra_s
    drift = abs(seconds - short.length_s) / short.length_s
    if drift <= tolerance:
        return None
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
    return _ranged_frames(getattr(shot, "hold", False), span, fps)


def wide_frames(shot, span: Span, fps: int) -> set[int]:
    """The frames of `span` that the shot's `wide` declares deliberately wide (true or a reason: all of them; ranges
    in seconds from the shot's start, inclusive): the framing checks skip them."""
    wide = getattr(shot, "wide", False)
    return _ranged_frames(True if isinstance(wide, str) and wide.strip() else wide, span, fps)


def _ranged_frames(value, span: Span, fps: int) -> set[int]:
    if value is True:
        return set(range(span.f0, span.f1 + 1))
    out: set[int] = set()
    for a, b in value if isinstance(value, list) else []:
        lo, hi = span.f0 + round(a * fps), min(span.f0 + round(b * fps), span.f1)
        out.update(range(max(lo, span.f0), hi + 1))
    return out


def to_json(tl: Timeline) -> dict:
    return asdict(tl)


def timeline_from_json(data: dict) -> Timeline:
    """The inverse of to_json. ValueError when `data` is not a timeline's JSON."""
    try:
        def spans(rows):
            return [Span(str(r["id"]), int(r["f0"]), int(r["f1"])) for r in rows]

        return Timeline(
            fps=int(data["fps"]),
            n_frames=int(data["n_frames"]),
            shots=spans(data["shots"]),
            lines=spans(data["lines"]),
            cards=[Card(str(c["text"]), str(c["line_id"]), int(c["f0"]), int(c["f1"])) for c in data["cards"]],
        )
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise ValueError(f"not a timeline: {exc!r}") from exc


def timeline_hash(data: dict) -> str:
    """A short fingerprint of a timeline's JSON (as in source/timeline.json). build.py stamps it on every scene it
    builds (scene["bsa_timeline"]) so the Blender commands can tell a .blend built for another timing."""
    text = json.dumps(data, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


# --------------------------------------------------------------------------- frames from the timeline

_USED: dict[str, Timeline | None] = {"tl": None, "path": None}


def use_timeline(short_dir) -> None:
    """Make `short_dir`'s source/timeline.json the one `at` reads (the build commands call this; it is read again
    each time `at` runs, so a rebuild never sees an old timing)."""
    from pathlib import Path

    _USED["path"] = str(Path(short_dir) / "source" / "timeline.json")


def _found_timeline() -> Timeline:
    """The timeline `at` reads: the one use_timeline named, else source/timeline.json of the short_dir a script-mode
    build was given (`-- PLUGIN_ROOT SHORT_DIR`), else of the open .blend (short/blend/short.blend)."""
    import sys
    from pathlib import Path

    paths = []
    if _USED["path"]:
        paths.append(Path(_USED["path"]))
    if "--" in sys.argv:
        rest = sys.argv[sys.argv.index("--") + 1 :]
        if len(rest) >= 2:
            paths.append(Path(rest[1]) / "source" / "timeline.json")
    bpy = sys.modules.get("bpy")
    blend = getattr(getattr(bpy, "data", None), "filepath", "") if bpy is not None else ""
    if blend:
        paths.append(Path(blend).resolve().parent.parent / "source" / "timeline.json")
    for path in paths:
        if path.is_file():
            return timeline_from_json(json.loads(path.read_text(encoding="utf-8")))
    raise FileNotFoundError("no source/timeline.json to read frames from: run CLI timeline SHORT first")


def frame_at(tl: Timeline, shot: str, offset: float | str = 0.0, fraction: bool = False) -> int:
    """The frame `offset` seconds into shot `shot` (the shot's first frame at 0). "end" is its last frame;
    with `fraction`, `offset` is 0..1 of the shot's length. ValueError for an unknown shot or an offset past
    the shot's end: a frame in the next shot is never returned."""
    span = next((s for s in tl.shots if s.id == shot), None)
    if span is None:
        raise ValueError(f"no shot {shot!r} in the timeline (shots: {', '.join(s.id for s in tl.shots)})")
    if offset == "end":
        return span.f1
    if isinstance(offset, str):
        raise TypeError(f"offset must be seconds or \"end\", not {offset!r}")
    n = span.f1 - span.f0
    frame = span.f0 + (round(offset * n) if fraction else round(offset * tl.fps))
    if offset < 0 or frame > span.f1:
        raise ValueError(f"{offset}{'' if fraction else ' s'} is outside shot {shot} ({n / tl.fps:.2f} s long)")
    return frame


def at(shot: str, offset: float | str = 0.0, fraction: bool = False, tl: Timeline | None = None) -> int:
    """The frame `offset` seconds into `shot`, read from the short's source/timeline.json (or from `tl`). Use it in
    build and model code instead of a frame number: a re-timed short then rebuilds correctly.
    at("s07", 1.5), at("s07", "end"), at("s07", 0.5, fraction=True)."""
    return frame_at(tl if tl is not None else _found_timeline(), shot, offset, fraction)


def retime_report(old: dict | None, new: Timeline) -> list[str]:
    """One line per shot whose first frame or length moved between the previous timeline JSON and `new`, with old
    and new frames; shots that appeared or went too. [] when nothing moved or there is no previous timeline."""
    if not old:
        return []
    try:
        before = {s.id: s for s in timeline_from_json(old).shots}
    except ValueError:
        return []
    out = []
    for s in new.shots:
        b = before.pop(s.id, None)
        if b is None:
            out.append(f"{s.id} is new: frames {s.f0}-{s.f1}.")
        elif (b.f0, b.f1 - b.f0) != (s.f0, s.f1 - s.f0):
            out.append(f"{s.id} moved: frames {b.f0}-{b.f1} \u2192 {s.f0}-{s.f1}. Re-check anything keyed to fixed "
                       "frames in it.")  # fmt: skip
    out += [f"{sid} is gone (it was frames {b.f0}-{b.f1})." for sid, b in before.items()]
    return out
