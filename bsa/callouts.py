"""Callouts: a part is named in the narration, a label pops in near it, an arrow points and the part glows. This module
is the pure timing: which word triggers a callout, the frames it shows between, and source/callouts.json (stamped
with the timeline's hash, so a re-timed short never keeps old frames). No bpy: the build's glow
(bsa.blender.callouts) and the edit read the file.

A callout's keys (a shot's `callouts` in short.json): text, near, on (required), at, hold_s, glow, culprit."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from bsa.freshness import StaleError
from bsa.short import spoken_text
from bsa.timeline import Timeline, timeline_hash, to_json

FILE = "callouts.json"
KEYS = ("text", "near", "on", "at", "hold_s", "glow", "culprit")

_OCCURRENCE = re.compile(r"^(.*)#(\d+)\s*$", re.DOTALL)
_NON_WORD = re.compile(r"[^0-9a-z]+")


@dataclass
class Callout:
    text: str
    near: str
    on: str
    at: tuple | None = None
    hold_s: float | None = None
    glow: bool = True
    culprit: bool = False


def callouts_of(shot) -> list[Callout]:
    """The shot's callouts as Callout objects (an entry the validation would reject is left out)."""
    out = []
    for raw in getattr(shot, "callouts", None) or []:
        if not isinstance(raw, dict) or not all(isinstance(raw.get(k), str) and raw[k].strip()
                                                for k in ("text", "near", "on")):  # fmt: skip
            continue
        at = raw.get("at")
        hold = raw.get("hold_s")
        out.append(Callout(
            text=raw["text"], near=raw["near"], on=raw["on"],
            at=tuple(float(v) for v in at) if isinstance(at, (list, tuple)) and len(at) == 3 else None,
            hold_s=float(hold) if isinstance(hold, (int, float)) and not isinstance(hold, bool) else None,
            glow=raw.get("glow", True) is not False,
            culprit=raw.get("culprit") is True,
        ))  # fmt: skip
    return out


def warnings(short) -> list[str]:
    """Findings that do not block: more than one culprit in a short (rule N: one culprit part)."""
    culprits = [(s.id, c.text) for s in short.shots for c in callouts_of(s) if c.culprit]
    if len(culprits) > 1:
        names = ", ".join(f"{t!r} (shot {sid})" for sid, t in culprits)
        return [f"{len(culprits)} callouts are marked culprit ({names}): a short names one culprit part"]
    return []


# --------------------------------------------------------------------------- matching the `on` word


def _tokens(text: str) -> list[str]:
    """Lowercase words of `text`: punctuation, asterisks and hyphens split and vanish ("*slip-blind,*" is slip, blind;
    an apostrophe just drops out)."""
    return [t for t in _NON_WORD.split(text.lower().replace("'", "").replace("’", "")) if t]


def _parse_on(on: str) -> tuple[list[str], int]:
    m = _OCCURRENCE.match(on)
    nth = int(m.group(2)) if m else 1
    return _tokens(m.group(1) if m else on), max(nth, 1)


def _matches(want: list[str], words: list[str]) -> list[int]:
    """The index in `words` of the first word of every match of the token run `want`, in order."""
    if not want:
        return []
    flat: list[tuple[str, int]] = [(t, i) for i, w in enumerate(words) for t in _tokens(w)]
    found: list[int] = []
    n = len(want)
    for k in range(len(flat) - n + 1):
        hit = all(flat[k + j][0] == want[j] for j in range(n))
        if hit and (not found or found[-1] != flat[k][1]):  # two starts inside one hyphenated word count once
            found.append(flat[k][1])
    return found


def match_on(on: str, words: list[str]) -> int | None:
    """The index in `words` of the first word of the match of `on` (case, punctuation, hyphens and `*highlight*`
    asterisks ignored; "valve#2" is the second occurrence), or None."""
    want, nth = _parse_on(on)
    hits = _matches(want, words)
    return hits[nth - 1] if len(hits) >= nth else None


def found_in(on: str, texts: list[list[str]]) -> bool:
    """Whether `on` (with its #N) is in the shot's lines' words."""
    return _locate(on, texts) is not None


# --------------------------------------------------------------------------- cue frames


def _locate(on: str, texts: list[list[str]]) -> tuple[int, int, int] | None:
    """(line index, word index in that line, which occurrence within that line) of the `on` match across the shot's
    lines' words, in order."""
    want, nth = _parse_on(on)
    seen = 0
    for li, words in enumerate(texts):
        hits = _matches(want, words)
        if seen + len(hits) >= nth:
            k = nth - seen - 1
            return li, hits[k], k
        seen += len(hits)
    return None


def cue_frames(short, tl: Timeline, word_times: dict | None) -> dict[str, list[dict]]:
    """{shot id: [{"i", "text", "near", "at", "f_on", "f_off", "glow", "culprit", "estimated"}]} for the shots that
    have callouts. f_on is the frame the `on` word starts: Whisper's word start inside the line when `word_times`
    ({line id: [(word, start_s, end_s)]}) has the line and the word, else an estimate: the share of the line's
    characters before the word, of the line's frames (a line is as long as its take, or as the reading model
    bsa.timeline.reading_time says without one). f_off is f_on + hold_s, else the shot's end, never past the shot's
    last frame. ValueError when an `on` word is not in the shot's narration."""
    spans = {s.id: s for s in tl.lines}
    shots = {s.id: s for s in tl.shots}
    out: dict[str, list[dict]] = {}
    for shot in short.shots:
        calls = callouts_of(shot)
        if not calls:
            continue
        span = shots.get(shot.id)
        if span is None:
            raise ValueError(f"shot {shot.id} is not in the timeline")
        mine = [ln for ln in short.lines if ln.shot == shot.id and ln.id in spans]
        texts = [spoken_text(ln) for ln in mine]
        words = [t.split() for t in texts]
        rows = []
        for i, c in enumerate(calls):
            hit = _locate(c.on, words)
            if hit is None:
                raise ValueError(f"shot {shot.id} callout {c.text!r}: the word {c.on!r} is not in the shot's narration")
            li, wi, k = hit
            line, line_span = mine[li], spans[mine[li].id]
            f_on, estimated = _frame_of(c.on, line, line_span, texts[li], words[li], wi, k, word_times, tl.fps)
            f_on = min(max(f_on, span.f0), span.f1)
            f_off = span.f1 if c.hold_s is None else f_on + round(c.hold_s * tl.fps)
            rows.append({"i": i, "text": c.text, "near": c.near, "at": list(c.at) if c.at else None,
                         "f_on": f_on, "f_off": min(f_off, span.f1), "glow": c.glow, "culprit": c.culprit,
                         "estimated": estimated})  # fmt: skip
        out[shot.id] = rows
    return out


def _frame_of(on, line, span, text, words, wi, k, word_times, fps) -> tuple[int, bool]:
    heard = (word_times or {}).get(line.id)
    if heard:
        want, _ = _parse_on(on)
        hits = _matches(want, [w[0] for w in heard])
        if len(hits) > k:
            return span.f0 + round(float(heard[hits[k]][1]) * fps), False
    before = len(" ".join(words[:wi])) + (1 if wi else 0)
    share = before / max(len(text), 1)
    return span.f0 + round(share * (span.f1 - span.f0 + 1)), True


# --------------------------------------------------------------------------- source/callouts.json


def path(sdir) -> Path:
    return Path(sdir) / "source" / FILE


def write(sdir, short, tl: Timeline, word_times: dict | None) -> Path | None:
    """Write source/callouts.json for this timeline; None (and a file left from before removed) when no shot has
    callouts, so a short without them has no such file."""
    target = path(sdir)
    shots = cue_frames(short, tl, word_times)
    if not shots:
        target.unlink(missing_ok=True)
        return None
    data = {"timeline_hash": timeline_hash(json.loads(json.dumps(to_json(tl)))), "shots": shots}
    text = json.dumps(data, indent=2) + "\n"
    if not target.is_file() or target.read_text(encoding="utf-8") != text:  # same frames: keep the file and its time
        target.write_text(text, encoding="utf-8")
    return target


def load(sdir, tl_hash: str) -> dict:
    """{shot id: [cue, ...]} of source/callouts.json. StaleError when it is missing, unreadable, or was made for
    another timeline than `tl_hash` (re-narrate or run CLI timeline SHORT, then rebuild)."""
    target = path(sdir)
    run = "run CLI timeline SHORT, then rebuild (source/build.py)"
    if not target.is_file():
        raise StaleError(f"source/{FILE} is missing: {run}")
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
        shots = data["shots"]
        stamp = data["timeline_hash"]
    except (ValueError, KeyError, TypeError) as exc:
        raise StaleError(f"source/{FILE} is not valid ({exc!r}): {run}") from exc
    if stamp != tl_hash:
        raise StaleError(f"source/{FILE} was made for another timing: {run}")
    return shots
