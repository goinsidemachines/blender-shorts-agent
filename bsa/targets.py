"""Optional script and edit targets, reported as warnings. Stdlib and numpy only.

One group, `targets`, resolved like the timing values (built-in defaults, the settings' own `targets`, the chosen style
preset, the short's own `targets`; see `bsa.presets.targets_resolved`). Every key defaults to null = off: with nothing
set, nothing is measured and nothing is printed anywhere. A set target never blocks anything: a miss is a warning that
shows the measured value beside the target ("pace 158 wpm, target 170-180"), on the first stage where it can be
measured.

- `wpm` [min, max]: words per spoken second, per take (a line of 3 words or more) and for the whole short. The spoken
  seconds of a take run from its first to its last sound (an energy gate on the take's wav). After narration.
- `max_pause_s`: the longest silence inside the voice, taken on the voice track (the takes placed on the timeline,
  lines of different shots included; the silence before the first and after the last word is not a pause). After
  narration, and again after the mix (the mix places the same takes).
- `max_numbers`: the most numbers spoken in the script: digits (1,000 and 3.5 and 50% each one), number words (one to
  twenty, thirty to ninety, hundred, thousand, million, billion; "twenty five" is one number) and half, double, twice,
  triple. Whenever the short has lines.
- `numbers_on_screen` true: every number spoken in a shot must also be written in that shot's on-screen text other than
  its caption: the shot's labels, title card, badge, step, category and value bars. (The caption is the line itself, so
  it would always match.) Storyboard.
- `max_cuts`: the most cuts. A cuts short has one cut between each two shots; a one-take short (camera.style) is one
  continuous move and has none. Storyboard.
- `length_s` [min, max]: the timeline's length in seconds. After narration (the timeline is from the takes).
- `hook_callback` true: the last line shares at least one content word with the first line or with the title. A simple
  word-overlap heuristic (lower case, no punctuation, a small stopword list, a trailing "s" dropped): it cannot tell
  whether the callback is meant, only whether any word comes back. Script.
- `payoff_by_s`: the shot marked `"payoff": true` in short.json starts by this second. No marked shot is a warning
  "mark the payoff shot". After narration.
- `motion_at_frame_1` true: after the build, the camera or an animated object already moves between frame 1 and
  frame 2 (Blender; `bsa.blender.targetcheck`, run with the scene checks).
"""

from __future__ import annotations

import itertools
import json
import math
import re
import wave
from pathlib import Path

import numpy as np

GROUP = "targets"
FILE = "targets.json"  # review/targets.json: the last rows
STAGES = ("story", "storyboard", "narration", "final")
# key -> (default, low, high, kind, what it is, the stage it is first measured on)
# kind: "range" [min, max]; "int?" / "num?" a whole number / a number or null = off; "flag" true, or null/false = off
SPEC: dict[str, tuple] = {
    "wpm": (None, 60, 400, "range", "spoken pace, words per second of speech x 60: [min, max]", "narration"),
    "max_pause_s": (None, 0, 5, "num?", "the longest silence inside the voice, seconds", "narration"),
    "max_numbers": (None, 0, 50, "int?", "the most numbers spoken in the script", "story"),
    "numbers_on_screen": (
        None,
        None,
        None,
        "flag",
        "each spoken number also shows in the shot's on-screen text",
        "storyboard",
    ),
    "max_cuts": (None, 0, 100, "int?", "the most cuts (a one-take short has none)", "storyboard"),
    "length_s": (None, 1, 600, "range", "the timeline's length in seconds: [min, max]", "narration"),
    "hook_callback": (None, None, None, "flag", "the last line echoes the first line or the title", "story"),
    "payoff_by_s": (None, 0, 600, "num?", "the payoff shot starts by this second", "narration"),
    "motion_at_frame_1": (None, None, None, "flag", "something already moves between frames 1 and 2", "final"),
}


def defaults() -> dict:
    return {k: v[0] for k, v in SPEC.items()}


def _num(v) -> bool:
    return isinstance(v, int | float) and not isinstance(v, bool) and math.isfinite(v)


def check(key: str, value):
    """`value` as stored; ValueError saying what is allowed."""
    if key not in SPEC:
        raise ValueError(f"unknown setting {GROUP}.{key}: use one of {', '.join(f'{GROUP}.{k}' for k in SPEC)}")
    _, lo, hi, kind, what, _ = SPEC[key]
    if value is None:
        return None
    if kind == "flag":
        if not isinstance(value, bool):
            raise ValueError(f"{GROUP}.{key} ({what}) must be true, false or null, got {value!r}")
        return value
    if kind == "range":
        if not (isinstance(value, list | tuple) and len(value) == 2 and all(_num(v) for v in value)):
            raise ValueError(f"{GROUP}.{key} ({what}) must be [min, max] or null, got {value!r}")
        if not all(lo <= v <= hi for v in value):
            raise ValueError(f"{GROUP}.{key} ({what}) must have both numbers from {lo:g} to {hi:g}, got {value!r}")
        if value[0] > value[1]:
            raise ValueError(f"{GROUP}.{key} ({what}) must have min at most max, got {value!r}")
        return [float(value[0]), float(value[1])]
    whole = kind == "int?"
    if not _num(value) or not lo <= value <= hi or (whole and value != int(value)):
        noun = "a whole number" if whole else "a number"
        raise ValueError(f"{GROUP}.{key} ({what}) must be {noun} from {lo:g} to {hi:g} or null, got {value!r}")
    return int(value) if whole else float(value)


def problems_in(value) -> list[str]:
    """What is wrong with a `targets` object (settings, short.json or a preset): [] when sound (None = absent)."""
    if value is None:
        return []
    if not isinstance(value, dict):
        return [f'{GROUP} must be an object such as {{"max_numbers": 3}}, got {value!r}']
    out = []
    extra = sorted(set(value) - set(SPEC))
    if extra:
        out.append(f"{GROUP} has unknown keys {extra}: use {', '.join(SPEC)}")
    for k, v in value.items():
        if k in SPEC:
            try:
                check(k, v)
            except ValueError as exc:
                out.append(str(exc))
    return out


def active(values: dict) -> dict:
    """The targets that are set (not null, not false)."""
    return {k: v for k, v in values.items() if k in SPEC and v is not None and v is not False}


def resolved(settings: dict | None, short=None) -> dict:
    """The active targets of a short (see `bsa.presets.targets_resolved`)."""
    from bsa import presets

    return active(presets.targets_resolved(settings, short)[0])


# ----------------------------------------------------------------------------------------------------- text helpers

_STOP = frozenset(
    [
        "a",
        "an",
        "the",
        "and",
        "or",
        "but",
        "so",
        "if",
        "then",
        "than",
        "that",
        "this",
        "these",
        "those",
        "it",
        "its",
        "it's",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "am",
        "do",
        "does",
        "did",
        "have",
        "has",
        "had",
        "having",
        "to",
        "of",
        "in",
        "on",
        "at",
        "by",
        "for",
        "with",
        "from",
        "as",
        "into",
        "over",
        "under",
        "about",
        "up",
        "down",
        "out",
        "off",
        "not",
        "no",
        "yes",
        "you",
        "your",
        "we",
        "our",
        "i",
        "me",
        "my",
        "he",
        "she",
        "they",
        "them",
        "their",
        "his",
        "her",
        "him",
        "what",
        "which",
        "who",
        "whom",
        "when",
        "where",
        "why",
        "how",
        "all",
        "any",
        "each",
        "both",
        "more",
        "most",
        "some",
        "such",
        "only",
        "own",
        "same",
        "too",
        "very",
        "can",
        "will",
        "just",
        "would",
        "could",
        "should",
        "may",
        "might",
        "must",
        "shall",
        "there",
        "here",
        "also",
        "again",
        "once",
    ]
)
_WORD = re.compile(r"[a-z0-9]+(?:'[a-z]+)?")


def plain(text) -> str:
    """Caption text as spoken: a *highlight*'s asterisks are not part of the words."""
    return str(text or "").replace("*", "")


def spoken_text(line) -> str:
    return plain(getattr(line, "say", None) or getattr(line, "text", ""))


def count_words(text) -> int:
    return len([w for w in plain(text).split() if re.search(r"\w", w)])


_UNITS = {w: i for i, w in enumerate(["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"])}
_TEENS = {w: 10 + i for i, w in enumerate(["ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen"])}  # fmt: skip
_TENS = {
    w: 10 * (i + 2)
    for i, w in enumerate(["twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"])
}
_SCALES = {"hundred": 100, "thousand": 1000, "million": 10**6, "billion": 10**9}
_SINGLE = {"half": 0.5, "double": 2.0, "twice": 2.0, "triple": 3.0}
_TOKEN = re.compile(r"\d[\d,]*(?:\.\d+)?|[a-z]+", re.IGNORECASE)


def numbers_in(text) -> list[float]:
    """The numbers in `text`, in order, as values: digits (1,000 and 3.5 and 50% count once each), number words (a run
    like "twenty five" or "two hundred and five" is one number) and half, double, twice, triple."""
    out: list[float] = []
    total = cur = 0  # a number being read from words: total (thousands and up) + cur (below a thousand)
    open_ = False
    last = None  # the kind of the last number word: "unit", "teen", "tens", "scale"

    def close():
        nonlocal total, cur, open_, last
        if open_:
            out.append(float(total + cur))
        total = cur = 0
        open_, last = False, None

    for m in _TOKEN.finditer(plain(text)):
        tok = m.group(0).lower()
        if tok[0].isdigit():
            close()
            out.append(float(tok.replace(",", "")))
            continue
        if tok == "and" and open_:  # "two hundred and five"
            continue
        if tok in _SINGLE:
            close()
            out.append(_SINGLE[tok])
        elif tok in _SCALES:
            if not open_:
                cur, open_ = 1, True
            if tok == "hundred":
                cur = (cur or 1) * 100
            else:
                total += (cur or 1) * _SCALES[tok]
                cur = 0
            last = "scale"
        elif tok in _UNITS or tok in _TEENS or tok in _TENS:
            v = _UNITS.get(tok, _TEENS.get(tok, _TENS.get(tok)))
            kind = "unit" if tok in _UNITS else "teen" if tok in _TEENS else "tens"
            joins = (
                open_
                and tok != "zero"
                and ((last == "tens" and kind == "unit") or (last == "scale" and (cur == 0 or cur % 100 == 0)))
            )
            if open_ and not joins:
                close()
            cur += v
            open_, last = True, kind
        else:
            close()
    close()
    return out


def _stem(word: str) -> str:
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def key_words(text) -> set[str]:
    """The content words of `text`: lower case, punctuation and *asterisks* dropped, stopwords dropped, a trailing "s"
    dropped (gear/gears)."""
    words = _WORD.findall(plain(text).lower().replace("’", "'"))
    return {_stem(w.split("'")[0]) for w in words if w not in _STOP and len(w) > 1} - {""}


# --------------------------------------------------------------------------------------------------------- the rows


def _fmt(v) -> str:
    return f"{v:g}"


def _range_text(r) -> str:
    return f"{_fmt(r[0])}–{_fmt(r[1])}"


def row(target: str, stage: str, measured, wanted, ok: bool | None, text: str = "", **extra) -> dict:
    """One result: `ok` None = could not be measured (`text` says why, it is information, not a warning)."""
    return {"target": target, "stage": stage, "measured": measured, "wanted": wanted, "ok": ok, "text": text, **extra}


def warnings_of(rows: list[dict]) -> list[str]:
    """The warning line of every row that missed its target."""
    return [r["text"] for r in rows if r.get("ok") is False]


def notes_of(rows: list[dict]) -> list[str]:
    """The information line of every row that could not be measured yet."""
    return [r["text"] for r in rows if r.get("ok") is None and r.get("text")]


def script_rows(short, values: dict) -> list[dict]:
    """max_numbers and hook_callback: from the lines alone."""
    rows = []
    if "max_numbers" in values and short.lines:
        n = sum(len(numbers_in(spoken_text(ln))) for ln in short.lines)
        limit = values["max_numbers"]
        rows.append(row("max_numbers", "story", n, f"at most {limit}", n <= limit,
                        f"{n} numbers spoken, target at most {limit}"))  # fmt: skip
    if "hook_callback" in values and short.lines:
        first, last = short.lines[0], short.lines[-1]
        if first is last:
            rows.append(row("hook_callback", "story", None, "last line echoes the first", None,
                            "hook_callback: the short has one line, nothing to compare"))  # fmt: skip
        else:
            shared = key_words(last.text) & (key_words(first.text) | key_words(short.title))
            text = (
                f"the last line echoes the hook: it shares {', '.join(sorted(shared))} with the first line or the title"
                if shared
                else "the last line shares no word with the first line or the title (a simple word-overlap "
                "check: reword the ending to come back to the hook, or leave it if the echo is meant "
                "in other words)"
            )
            rows.append(row("hook_callback", "story", sorted(shared) or "no shared word", "a shared word", bool(shared),
                            text))  # fmt: skip
    return rows


def screen_text(shot) -> str:
    """The shot's on-screen words other than its caption: labels, title card, badge, step, category, value bars."""
    from bsa import short as short_mod

    parts = [short_mod.label_text(label) for label in shot.labels]
    parts += list(short_mod.title_lines(shot)[0])
    parts += [shot.badge or "", short_mod.step_text(shot) or "", short_mod.category_text(shot.category)]
    for item in short_mod.bar_items(shot):
        parts += [str(item.get("label") or ""), short_mod.bar_text(item)]
    parts.append(short_mod.bar_verdict(shot))
    return " ".join(p for p in parts if p)


def cut_count(short) -> int:
    """Cuts: one between each two shots of a cuts short; none in a one-take short (the camera never cuts)."""
    from bsa.short import one_take

    return 0 if one_take(short) else max(len(short.shots) - 1, 0)


def storyboard_rows(short, values: dict) -> list[dict]:
    """numbers_on_screen and max_cuts: from the shots."""
    rows = []
    if "numbers_on_screen" in values:
        missing = []
        for shot in short.shots:
            shown = set(numbers_in(screen_text(shot)))
            said = [n for ln in short.lines if ln.shot == shot.id for n in numbers_in(spoken_text(ln))]
            missing += [(shot.id, n) for n in said if n not in shown]
        text = ", ".join(f"{_fmt(n)} (shot {sid})" for sid, n in missing)
        rows.append(row("numbers_on_screen", "storyboard", [f"{sid}: {_fmt(n)}" for sid, n in missing] or "all shown",
                        "every spoken number on screen", not missing,
                        f"spoken but not written in the shot's labels or cards: {text}"))  # fmt: skip
    if "max_cuts" in values:
        n, limit = cut_count(short), values["max_cuts"]
        rows.append(row("max_cuts", "storyboard", n, f"at most {limit}", n <= limit,
                        f"{n} cuts, target at most {limit}"))  # fmt: skip
    return rows


def timeline_rows(short, tl, values: dict) -> list[dict]:
    """length_s and payoff_by_s: from the timeline made from the takes."""
    rows = []
    if "length_s" in values:
        seconds = round(tl.n_frames / tl.fps, 2)
        lo, hi = values["length_s"]
        rows.append(row("length_s", "narration", seconds, _range_text(values["length_s"]), lo <= seconds <= hi,
                        f"length {seconds:g} s, target {_range_text(values['length_s'])}"))  # fmt: skip
    if "payoff_by_s" in values:
        limit = values["payoff_by_s"]
        marked = [s for s in short.shots if getattr(s, "payoff", False) is True]
        spans = {s.id: s for s in tl.shots}
        if not marked or marked[0].id not in spans:
            rows.append(row("payoff_by_s", "narration", "no payoff shot", f"by {_fmt(limit)} s", False,
                            'payoff_by_s is set but no shot is marked: mark the payoff shot ("payoff": true on it '
                            "in short.json)"))  # fmt: skip
        else:
            start = round((spans[marked[0].id].f0 - 1) / tl.fps, 2)
            rows.append(
                row(
                    "payoff_by_s",
                    "narration",
                    start,
                    f"by {_fmt(limit)} s",
                    start <= limit,
                    f"the payoff shot ({marked[0].id}) starts at {start:g} s, target by {_fmt(limit)} s",
                )
            )
    return rows


# ------------------------------------------------------------------------------------------------------------ audio

GATE_WINDOW_S = 0.02
GATE_BELOW_PEAK_DB = 40.0
GATE_FLOOR_DBFS = -60.0
MIN_PAUSE_S = 0.1  # a silence shorter than this is never reported as a pause (the gap between two sounds of a word)


def voiced_spans(x: np.ndarray, sr: int) -> list[tuple[float, float]]:
    """[(start_s, end_s)] of the stretches of `x` (mono) above an energy gate: 40 dB under its own loudest 20 ms
    window, never lower than -60 dBFS. Neighbouring windows join into one stretch."""
    win = max(1, round(GATE_WINDOW_S * sr))
    n = len(x) // win
    if n == 0:
        return []
    rms = np.sqrt(np.mean(np.asarray(x[: n * win], dtype=np.float64).reshape(n, win) ** 2, axis=1))
    gate = max(float(rms.max()) * 10 ** (-GATE_BELOW_PEAK_DB / 20), 10 ** (GATE_FLOOR_DBFS / 20))
    loud = rms >= gate
    spans, start = [], None
    for i, on in enumerate([*loud, False]):
        if on and start is None:
            start = i
        elif not on and start is not None:
            spans.append((start * win / sr, i * win / sr))
            start = None
    return spans


def longest_pause(spans: list[tuple[float, float]]) -> tuple[float, float] | None:
    """(seconds, where it starts) of the longest silence between two stretches; None with fewer than two."""
    gaps = [(b0 - a1, a1) for (_, a1), (b0, _) in itertools.pairwise(spans) if b0 - a1 >= MIN_PAUSE_S]
    return max(gaps) if gaps else None


def _take(path: Path):
    from bsa.audio.mix import _read_take

    return _read_take(path)


def wpm_rows(short, takes_dir: Path, values: dict) -> list[dict]:
    """wpm per take (3 words or more) and for the whole short, from each take's first to last sound."""
    if "wpm" not in values:
        return []
    lo, hi = values["wpm"]
    want = _range_text(values["wpm"])
    from bsa.audio.mix import SR

    rows, words, secs = [], 0, 0.0
    for ln in short.lines:
        path = Path(takes_dir) / f"{ln.id}.wav"
        n = count_words(spoken_text(ln))
        try:
            spans = voiced_spans(_take(path), SR)
        except (OSError, ValueError, EOFError, wave.Error):
            continue
        if not spans:
            continue
        spoken = spans[-1][1] - spans[0][0]
        words, secs = words + n, secs + spoken
        if n >= 3 and spoken > 0:
            rate = round(n * 60 / spoken)
            if not lo <= rate <= hi:
                rows.append(row("wpm", "narration", rate, want, False,
                                f"line {ln.id}: pace {rate} wpm, target {want}", line=ln.id))  # fmt: skip
    if secs <= 0:
        return [row("wpm", "narration", None, want, None, "wpm: no spoken take could be read, pace not measured")]
    rate = round(words * 60 / secs)
    return [row("wpm", "narration", rate, want, lo <= rate <= hi, f"pace {rate} wpm, target {want}"), *rows]


def voice_track(tl, takes_dir: Path) -> np.ndarray:
    """The voice alone, the takes placed where the mix places them (mono, at the mix's rate)."""
    from bsa.audio.mix import SR, _voice

    n = round(tl.n_frames / tl.fps * SR) + SR
    return _voice(tl, Path(takes_dir), n)


def pause_rows(tl, takes_dir: Path, values: dict, stage: str = "narration") -> list[dict]:
    """max_pause_s: the longest silence on the voice track."""
    if "max_pause_s" not in values:
        return []
    limit = values["max_pause_s"]
    from bsa.audio.mix import SR

    try:
        found = longest_pause(voiced_spans(voice_track(tl, takes_dir), SR))
    except (OSError, ValueError, EOFError, wave.Error) as exc:
        return [row("max_pause_s", stage, None, f"at most {_fmt(limit)} s", None,
                    f"max_pause_s: the voice could not be read ({exc}), pauses not measured")]  # fmt: skip
    if found is None:
        return [row("max_pause_s", stage, 0.0, f"at most {_fmt(limit)} s", True,
                    f"no pause of {MIN_PAUSE_S:g} s or more in the voice")]  # fmt: skip
    seconds, at = found
    return [row("max_pause_s", stage, round(seconds, 2), f"at most {_fmt(limit)} s", seconds <= limit,
                f"longest pause {seconds:.2f} s at {at:.1f} s, target at most {_fmt(limit)} s")]  # fmt: skip


# --------------------------------------------------------------------------------------------------- orchestration


def measure(short, values: dict, tl=None, takes_dir: Path | None = None, narrated: bool = False,
            stage: str = "narration") -> list[dict]:  # fmt: skip
    """Every target in `values` (the active ones) that can be measured now. A target that needs the narration, before
    there is one, is a row with ok None saying so (information, not a warning). Nothing set: []."""
    if not values:
        return []
    rows = script_rows(short, values) + storyboard_rows(short, values)
    needs = [k for k in ("wpm", "max_pause_s", "length_s", "payoff_by_s") if k in values]
    if needs and (not narrated or tl is None):
        rows += [row(k, SPEC[k][5], None, None, None, f"{k}: measured after narration (no voice yet)") for k in needs]
    elif needs:
        rows += timeline_rows(short, tl, values)
        if takes_dir is not None:
            rows += wpm_rows(short, takes_dir, values) + pause_rows(tl, takes_dir, values, stage)
    if "motion_at_frame_1" in values:
        rows.append(row("motion_at_frame_1", "final", None, "moving at frame 1", None,
                        "motion_at_frame_1: measured with the scene checks (CLI check)"))  # fmt: skip
    return rows


def path(sdir: Path) -> Path:
    return Path(sdir) / "review" / FILE


def read_rows(sdir: Path) -> list[dict]:
    try:
        data = json.loads(path(sdir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    rows = data.get("rows") if isinstance(data, dict) else None
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


def write_rows(sdir: Path, rows: list[dict]) -> list[dict]:
    """Merge `rows` into review/targets.json: a row replaces the earlier ones of its target (a not-yet-measured row
    never replaces a real one from the checks). Returns every row now in the file."""
    kept = [r for r in read_rows(sdir)]
    new_targets = {r["target"] for r in rows}
    for r in rows:
        if r.get("ok") is None and r["target"] == "motion_at_frame_1":
            old = [k for k in kept if k["target"] == r["target"] and k.get("ok") is not None]
            if old:
                new_targets.discard(r["target"])
    merged = [r for r in kept if r["target"] not in new_targets] + [r for r in rows if r["target"] in new_targets]
    p = path(sdir)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"rows": merged}, indent=2, default=str) + "\n", encoding="utf-8")
    return merged


def clear(sdir: Path) -> None:
    """Nothing is set any more: the old rows go."""
    try:
        path(sdir).unlink()
    except OSError:
        pass


def report(sdir: Path, short, settings: dict | None, tl=None, takes_dir: Path | None = None,
           narrated: bool = False, stage: str = "narration") -> dict:  # fmt: skip
    """Measure what is set, write review/targets.json, and return {"rows", "warnings", "notes"}. With no target set:
    the empty report and no file."""
    values = resolved(settings, short)
    if not values:
        clear(sdir)
        return {"rows": [], "warnings": [], "notes": []}
    rows = measure(short, values, tl, takes_dir, narrated, stage)
    merged = write_rows(sdir, rows)
    return {"rows": merged, "warnings": warnings_of(rows), "notes": notes_of(rows)}
