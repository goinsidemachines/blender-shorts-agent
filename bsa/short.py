"""The short's spec: the dataclasses behind <short>/source/short.json, plus loading, saving and validation."""

from __future__ import annotations

import json
import math
import re
from dataclasses import asdict, dataclass, fields
from dataclasses import field as dc_field
from pathlib import Path

FPS = 30
WIDTH = 1080
HEIGHT = 1920
SOUND_POLICIES = ("", "any", "natural-only")


@dataclass
class Fact:
    id: str
    claim: str
    source: str


@dataclass
class Line:
    id: str
    text: str
    shot: str
    facts: list[str] = dc_field(default_factory=list)
    say: str | None = None  # what the narrator speaks (and Whisper checks) when it differs from the caption `text`


@dataclass
class Shot:
    id: str
    title: str
    picture: str
    scene: str
    min_s: float = 3.0
    still_at: float = 0.5  # fraction (0-1) of the shot's length
    labels: list = dc_field(default_factory=list)  # each "text", or {"text": ..., "near": "<object name>"}
    badge: str | None = None
    # A deliberate hold: true (the whole shot) or [[from_s, to_s], ...] seconds from the shot's start. frame-check
    # does not report stuck frames there (a scripted freeze, a part at rest).
    hold: bool | list = False
    # A deliberately wide or off-centre framing: true, a reason ("establishing shot of the whole bridge") or
    # [[from_s, to_s], ...] as for hold. The framing checks (subject too small, an empty half) skip those frames.
    wide: bool | str | list = False
    # Brand-kit cards (bsa.cards; only drawn with a kit): a title card (one line, 1-2 lines, or {"lines": [...],
    # "highlight": index of the line in the accent colour}), a category tag, a step tag [n, of] and value bars
    # {"items": [{"label", "value", "text"?, "category"?}], "verdict"?, "max"?}.
    title_card: str | list | dict | None = None
    category: str | None = None
    step: list | None = None
    bars: dict | None = None


@dataclass
class Sfx:
    kind: str
    shot: str
    at_s: float
    dur_s: float
    level_db: float


@dataclass
class Asset:
    id: str
    name: str
    kind: str
    source: str
    license: str
    files: list[dict] = dc_field(default_factory=list)  # each {"url", "bytes", "role"}
    bytes: int = 0  # total of the files


@dataclass
class Short:
    slug: str
    title: str
    length_s: float
    version: int = 1
    topic: str = ""
    audience: str = ""
    field: str = ""
    fps: int = FPS
    width: int = WIDTH
    height: int = HEIGHT
    style: dict = dc_field(default_factory=dict)
    facts: list[Fact] = dc_field(default_factory=list)
    lines: list[Line] = dc_field(default_factory=list)
    shots: list[Shot] = dc_field(default_factory=list)
    sfx: list[Sfx] = dc_field(default_factory=list)
    assets: list[Asset] = dc_field(default_factory=list)
    sound_policy: str = ""  # "" = the setting; "any" or "natural-only" (no synthetic sfx kinds, recordings only)
    post: dict = dc_field(default_factory=lambda: {"title": "", "description": "", "hashtags": []})
    # This short's end card, over the `end_card` setting: "line", or {"text": line, "second": a line under the handle
    # such as the user's site}. None = the setting's line. Shown with the user's @handle, or on its own without one.
    end_card: str | dict | None = None
    # This short's closing card (bsa.closing), over the `website` and `closing` settings: false = none, or
    # {"image", "seconds", "voice_line", "website"} to change those for this short. None = the settings.
    closing: bool | dict | None = None
    # This short's look, over the brand.mode setting: "fresh" = its own style only (the settings' brand is off for
    # this short: background check, captions, view), "brand" = the brand, None = the setting.
    look: str | None = None
    # Hard rules (bsa.rules): this short's own rules on top of the user's (never loosening them), and how the short
    # keeps each rule: {rule id: "how", or {"kept": "how", "cleared": {word: why it keeps the rule}}}.
    rules: list = dc_field(default_factory=list)
    rules_check: dict = dc_field(default_factory=dict)
    # This short's render choices: {"engine": "cycles" | "eevee"} is the engine the engine stage proposes (the
    # approved engine stays the engine stage's own). Empty = the setting.
    render: dict = dc_field(default_factory=dict)


def _build(cls, data: dict):
    names = {f.name for f in fields(cls)}
    return cls(**{k: v for k, v in data.items() if k in names})


def load_short(path: Path) -> Short:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    kids = {"facts": Fact, "lines": Line, "shots": Shot, "sfx": Sfx, "assets": Asset}
    for key, cls in kids.items():
        data[key] = [_build(cls, item) for item in data.get(key, [])]
    return _build(Short, data)


def save_short(short: Short, path: Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(short), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


_LINE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")
_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}


def line_id_problem(line_id) -> str | None:
    """Why a line id can't be used (None when it can). A take is audio/vo/<id>.wav, so an id must be a plain file
    name on every system: letters, digits, "_", "-" and "." (not first), no trailing dot, no Windows device name."""
    if not isinstance(line_id, str) or not _LINE_ID.fullmatch(line_id):
        return (f"line id {line_id!r} must be letters, digits, \"_\", \"-\" or \".\" and start with a letter or "
                "digit: rename it")
    if line_id.endswith("."):
        return f"line id {line_id!r} must not end with a dot: rename it"
    if line_id.split(".")[0].lower() in _RESERVED:
        return f"line id {line_id!r} is a reserved file name on Windows: rename it"
    return None


def label_text(label) -> str:
    """A shot label's words: the label itself, or its "text" when it is an object."""
    return str(label.get("text") or "") if isinstance(label, dict) else str(label)


def label_near(label) -> str | None:
    """The object a label sits next to (its "near"), or None for a plain label (stacked top-left)."""
    if not isinstance(label, dict):
        return None
    return str(label.get("near") or "").strip() or None


def label_kind(label) -> str:
    """"measure" for a label object with "kind": "measure" (a measure label), else "label"."""
    return "measure" if isinstance(label, dict) and label.get("kind") == "measure" else "label"


def _label_problem(label) -> str | None:
    if isinstance(label, str):
        return None if label.strip() else "is empty"
    if not isinstance(label, dict):
        return f"must be text or an object with text and near, got {label!r}"
    if not isinstance(label.get("text"), str) or not label["text"].strip():
        return f"{label!r} needs a non-empty text"
    if label.get("kind") not in (None, "measure"):
        return f"{label!r} has an unknown kind: the only kind is \"measure\""
    near = label.get("near")
    if near is None and label.get("kind") == "measure":
        return None  # a measure label without near is stacked
    if not isinstance(near, str) or not near.strip():
        return f"{label!r} needs near: the name of the object it sits next to"
    return None


def _clean_lines(value) -> tuple[str, ...]:
    return tuple(v.strip() for v in value if isinstance(v, str) and v.strip()) if isinstance(value, list) else ()


def title_lines(shot) -> tuple[tuple[str, ...], int | None]:
    """(the title card's lines, the index of the highlighted one or None). ((), None) without a title card."""
    card = getattr(shot, "title_card", None)
    if isinstance(card, str):
        return ((card.strip(),) if card.strip() else ()), None
    if isinstance(card, list):
        return _clean_lines(card), None
    if isinstance(card, dict):
        lines = _clean_lines(card.get("lines"))
        hl = card.get("highlight")
        return lines, (hl if isinstance(hl, int) and not isinstance(hl, bool) and 0 <= hl < len(lines) else None)
    return (), None


def _step_ok(step) -> bool:
    return (isinstance(step, list) and len(step) == 2
            and all(isinstance(v, int) and not isinstance(v, bool) for v in step)
            and 1 <= step[0] <= step[1] <= 20)  # fmt: skip


def step_text(shot) -> str | None:
    """"Step 2 of 4" for a shot with step [2, 4], else None."""
    step = getattr(shot, "step", None)
    return f"Step {step[0]} of {step[1]}" if _step_ok(step) else None


def category_text(name) -> str:
    """The category as shown on its tag: first letter upper case."""
    text = str(name or "").strip()
    return text[:1].upper() + text[1:]


def bar_text(item) -> str:
    """The words on a value bar: its `text`, else its value (3.0 -> "3")."""
    if not isinstance(item, dict):
        return ""
    text = item.get("text")
    if isinstance(text, str) and text.strip():
        return text.strip()
    value = item.get("value")
    return f"{value:g}" if isinstance(value, int | float) and not isinstance(value, bool) else ""


def bar_items(shot) -> list[dict]:
    """The shot's value bars, [] without any (or when they are not a list of objects)."""
    bars = getattr(shot, "bars", None)
    items = bars.get("items") if isinstance(bars, dict) else None
    return [i for i in items if isinstance(i, dict)] if isinstance(items, list) else []


def bar_verdict(shot) -> str:
    bars = getattr(shot, "bars", None)
    v = bars.get("verdict") if isinstance(bars, dict) else None
    return v.strip() if isinstance(v, str) else ""


def _number(v) -> bool:
    return isinstance(v, int | float) and not isinstance(v, bool) and math.isfinite(v)


def _title_card_problem(card) -> str | None:
    bad = "title_card must be text, 1-2 lines of text, or {\"lines\": [1-2 lines], \"highlight\": 0 or 1}"
    if isinstance(card, str):
        return None if card.strip() else f"{bad}; it is empty"
    lines = card
    if isinstance(card, dict):
        extra = sorted(set(card) - {"lines", "highlight"})
        if extra:
            return f"title_card has unknown keys {extra}: use lines and highlight"
        lines = card.get("lines")
    if (not isinstance(lines, list) or not 1 <= len(lines) <= 2
            or not all(isinstance(x, str) and x.strip() for x in lines)):  # fmt: skip
        return f"{bad}, got {card!r}"
    hl = card.get("highlight") if isinstance(card, dict) else None
    if hl is not None and (not isinstance(hl, int) or isinstance(hl, bool) or not 0 <= hl < len(lines)):
        return f"title_card highlight must be the index of a line (0 to {len(lines) - 1}), got {hl!r}"
    return None


def _bars_problem(bars) -> str | None:
    if not isinstance(bars, dict):
        return f"bars must be {{\"items\": [2-4 bars], \"verdict\": text, \"max\": number}}, got {bars!r}"
    extra = sorted(set(bars) - {"items", "verdict", "max"})
    if extra:
        return f"bars has unknown keys {extra}: use items, verdict and max"
    items = bars.get("items")
    if not isinstance(items, list) or not 2 <= len(items) <= 4:
        return "bars needs items: a list of 2 to 4 bars"
    for it in items:
        if not (isinstance(it, dict) and isinstance(it.get("label"), str) and it["label"].strip()
                and _number(it.get("value")) and it["value"] >= 0):  # fmt: skip
            return f"bars item {it!r} needs a label (text) and a value (a number, 0 or more)"
        extra = sorted(set(it) - {"label", "value", "text", "category"})
        if extra:
            return f"bars item {it['label']!r} has unknown keys {extra}: use label, value, text and category"
        for key in ("text", "category"):
            if key in it and (not isinstance(it[key], str) or not it[key].strip()):
                return f"bars item {it['label']!r} {key} must be text, got {it[key]!r}"
    if "verdict" in bars and (not isinstance(bars["verdict"], str) or not bars["verdict"].strip()):
        return f"bars verdict must be text, got {bars['verdict']!r}"
    if "max" in bars and not (_number(bars["max"]) and bars["max"] > 0):
        return f"bars max must be a number above 0, got {bars['max']!r}"
    return None


def near_labels(short) -> list[tuple[str, str, str]]:
    """(shot id, label text, object name) for every label placed next to an object."""
    return [(s.id, label_text(lb), label_near(lb)) for s in short.shots for lb in s.labels if label_near(lb)]


def spoken_text(line) -> str:
    """The words the narrator speaks for a line: its `say` when set, else its caption `text`."""
    say = line.say if isinstance(line.say, str) else None
    return say.strip() if say and say.strip() else line.text


# Characters a voice reads unpredictably ("×" as "x" or nothing): a line showing them on screen wants a `say`.
UNSPOKEN = "×÷≈≠≤≥±→←↔=^√∞·"


def spoken_warnings(short) -> list[str]:
    """Lines whose caption has symbols a voice reads badly and no `say` spelling them out."""
    out = []
    for ln in short.lines:
        if ln.say is None:
            bad = sorted({ch for ch in ln.text if ch in UNSPOKEN})
            if bad:
                out.append(f"line {ln.id} shows {' '.join(bad)}, which a voice reads unpredictably: add a \"say\" "
                           "with it spelled out in words (the caption keeps the symbols)")  # fmt: skip
    return out


def _hold_problem(hold) -> bool:
    if isinstance(hold, bool):
        return False
    if not isinstance(hold, list):
        return True
    for r in hold:
        if not (isinstance(r, list | tuple) and len(r) == 2 and all(isinstance(v, int | float) and not isinstance(v, bool)
                                                                   for v in r)):  # fmt: skip
            return True
        if r[0] < 0 or r[0] > r[1]:
            return True
    return False


def _has_digit(text: str) -> bool:
    return any(ch.isdigit() for ch in text)


def line_fact_flags(line: Line, fact_ids: set[str]) -> list[tuple[str, str | None]]:
    """What is wrong with a line's facts, as (code, fact id): ("unknown_fact", id) for each cited id not in
    `fact_ids`, then ("number_without_fact", None) when its caption or its spoken words have a digit and it cites no known fact."""
    flags: list[tuple[str, str | None]] = [("unknown_fact", fid) for fid in line.facts if fid not in fact_ids]
    spoken = line.say if isinstance(line.say, str) else ""
    if (_has_digit(line.text) or _has_digit(spoken)) and not any(fid in fact_ids for fid in line.facts):
        flags.append(("number_without_fact", None))
    return flags


def _dupes(kind: str, ids: list[str]) -> list[str]:
    seen: set[str] = set()
    out = []
    for i in ids:
        if i in seen:
            out.append(f"duplicate {kind} id {i!r}")
        seen.add(i)
    return out


def _render_problems(render) -> list[str]:
    from bsa.engine_runs import ENGINES

    if not isinstance(render, dict):
        return [f"render must be an object such as {{\"engine\": \"eevee\"}}, got {render!r}"]
    out = []
    extra = sorted(set(render) - {"engine"})
    if extra:
        out.append(f"render has unknown keys {extra}: use engine")
    if "engine" in render and render["engine"] not in ENGINES:
        out.append(f"render.engine must be {' or '.join(ENGINES)}, got {render['engine']!r}")
    return out


def _card_problems(s: Shot, cited: bool) -> list[str]:
    """Problems of a shot's title card, category, step and value bars (all optional)."""
    out = []
    if s.title_card is not None:
        problem = _title_card_problem(s.title_card)
        if problem:
            out.append(f"shot {s.id} {problem}")
        else:
            for text in title_lines(s)[0]:
                if _has_digit(text) and not cited:
                    out.append(f"shot {s.id} title card {text!r} contains a number but no line in the shot cites a fact")
    if s.category is not None and (not isinstance(s.category, str) or not s.category.strip()):
        out.append(f"shot {s.id} category must be a name (non-empty text), got {s.category!r}")
    if s.step is not None and not _step_ok(s.step):
        out.append(f"shot {s.id} step must be [n, of] whole numbers with 1 <= n <= of <= 20, got {s.step!r}")
    if s.bars is not None:
        problem = _bars_problem(s.bars)
        if problem:
            out.append(f"shot {s.id} {problem}")
        elif not cited:
            names = ", ".join(str(i["label"]) for i in s.bars["items"])
            out.append(f"shot {s.id} value bars ({names}) contains a number but no line in the shot cites a fact")
    return out


def validate(short: Short) -> list[str]:
    """Return a list of human-readable problems; empty means the spec is sound."""
    errors: list[str] = []
    fact_ids = {f.id for f in short.facts}
    shot_ids = {s.id for s in short.shots}

    errors += _dupes("fact", [f.id for f in short.facts])
    errors += _dupes("shot", [s.id for s in short.shots])
    errors += _dupes("line", [ln.id for ln in short.lines])
    errors += [p for p in (line_id_problem(ln.id) for ln in short.lines) if p]
    errors += _dupes("asset", [a.id for a in short.assets])

    if not short.lines:
        errors.append("the short has no lines")
    if short.fps != FPS:
        errors.append(f"fps must be {FPS}, got {short.fps}")
    if (short.width, short.height) != (WIDTH, HEIGHT):
        errors.append(f"size must be {WIDTH}x{HEIGHT}, got {short.width}x{short.height}")

    for f in short.facts:
        if not f.source.strip():
            errors.append(f"fact {f.id} has an empty source")

    cited_by_shot: dict[str, bool] = {}
    for ln in short.lines:
        if ln.shot not in shot_ids:
            errors.append(f"line {ln.id} refers to unknown shot {ln.shot!r}")
        flags = line_fact_flags(ln, fact_ids)
        for code, fid in flags:
            if code == "unknown_fact":
                errors.append(f"line {ln.id} cites unknown fact {fid!r}")
        if any(fid in fact_ids for fid in ln.facts):
            cited_by_shot[ln.shot] = True
        if ln.say is not None and (not isinstance(ln.say, str) or not ln.say.strip()):
            errors.append(f"line {ln.id} say must be the spoken words (non-empty text), got {ln.say!r}")
        if any(code == "number_without_fact" for code, _ in flags):
            errors.append(f"line {ln.id} contains a number but cites no fact")

    # A label's number counts as sourced when a line in the same shot cites a fact.
    for s in short.shots:
        for label in s.labels:
            problem = _label_problem(label)
            if problem:
                errors.append(f"shot {s.id} label {problem}")
                continue
            text = label_text(label)
            if _has_digit(text) and not cited_by_shot.get(s.id):
                errors.append(f"shot {s.id} label {text!r} contains a number but no line in the shot cites a fact")
        errors += _card_problems(s, cited_by_shot.get(s.id, False))
        if not 0 <= s.still_at <= 1:
            errors.append(f"shot {s.id} still_at must be between 0 and 1")
        if _hold_problem(s.hold):
            errors.append(f"shot {s.id} hold must be true, false or a list of [from_s, to_s] ranges (seconds from "
                          f"the shot's start, from_s <= to_s), got {s.hold!r}")  # fmt: skip
        if not isinstance(s.wide, str) and _hold_problem(s.wide):
            errors.append(f"shot {s.id} wide must be true, false, a reason or a list of [from_s, to_s] ranges "
                          f"(seconds from the shot's start, from_s <= to_s), got {s.wide!r}")  # fmt: skip

    card = short.end_card
    if isinstance(card, dict):
        extra = sorted(set(card) - {"text", "second"})
        if extra:
            errors.append(f"end_card has unknown keys {extra}: use text and second")
        for key in ("text", "second"):
            if card.get(key) is not None and not isinstance(card.get(key), str):
                errors.append(f"end_card {key} must be text, got {card.get(key)!r}")
    elif card is not None and not isinstance(card, str):
        errors.append(f"end_card must be a line of text or {{\"text\": ..., \"second\": ...}}, got {card!r}")

    from bsa import closing

    errors += closing.problems(short)
    if any(ln.id == closing.LINE_ID for ln in short.lines):
        errors.append(f"line id {closing.LINE_ID!r} is kept for the closing line: give the line another id")
    if short.look not in (None, "fresh", "brand"):
        errors.append(f"look must be \"fresh\" (this short's own style, the brand off) or \"brand\", got {short.look!r}")
    errors += _render_problems(short.render)
    if short.sound_policy not in SOUND_POLICIES:
        errors.append(f"sound_policy must be one of {', '.join(p for p in SOUND_POLICIES if p)} (or empty), got "
                      f"{short.sound_policy!r}")  # fmt: skip
    for fx in short.sfx:
        if fx.shot not in shot_ids:
            errors.append(f"sfx {fx.kind!r} refers to unknown shot {fx.shot!r}")
    return errors
