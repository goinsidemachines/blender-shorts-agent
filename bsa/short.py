"""The short's spec: the dataclasses behind <short>/source/short.json, plus loading, saving and validation."""

from __future__ import annotations

import json
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


def label_text(label) -> str:
    """A shot label's words: the label itself, or its "text" when it is an object."""
    return str(label.get("text") or "") if isinstance(label, dict) else str(label)


def label_near(label) -> str | None:
    """The object a label sits next to (its "near"), or None for a plain label (stacked top-left)."""
    if not isinstance(label, dict):
        return None
    return str(label.get("near") or "").strip() or None


def _label_problem(label) -> str | None:
    if isinstance(label, str):
        return None if label.strip() else "is empty"
    if not isinstance(label, dict):
        return f"must be text or an object with text and near, got {label!r}"
    if not isinstance(label.get("text"), str) or not label["text"].strip():
        return f"{label!r} needs a non-empty text"
    if not isinstance(label.get("near"), str) or not label["near"].strip():
        return f"{label!r} needs near: the name of the object it sits next to"
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


def _dupes(kind: str, ids: list[str]) -> list[str]:
    seen: set[str] = set()
    out = []
    for i in ids:
        if i in seen:
            out.append(f"duplicate {kind} id {i!r}")
        seen.add(i)
    return out


def validate(short: Short) -> list[str]:
    """Return a list of human-readable problems; empty means the spec is sound."""
    errors: list[str] = []
    fact_ids = {f.id for f in short.facts}
    shot_ids = {s.id for s in short.shots}

    errors += _dupes("fact", [f.id for f in short.facts])
    errors += _dupes("shot", [s.id for s in short.shots])
    errors += _dupes("line", [ln.id for ln in short.lines])
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
        for fid in ln.facts:
            if fid not in fact_ids:
                errors.append(f"line {ln.id} cites unknown fact {fid!r}")
        cites = any(fid in fact_ids for fid in ln.facts)
        if cites:
            cited_by_shot[ln.shot] = True
        if ln.say is not None and (not isinstance(ln.say, str) or not ln.say.strip()):
            errors.append(f"line {ln.id} say must be the spoken words (non-empty text), got {ln.say!r}")
        spoken = ln.say if isinstance(ln.say, str) else ""
        if (_has_digit(ln.text) or _has_digit(spoken)) and not cites:
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
        if not 0 <= s.still_at <= 1:
            errors.append(f"shot {s.id} still_at must be between 0 and 1")
        if _hold_problem(s.hold):
            errors.append(f"shot {s.id} hold must be true, false or a list of [from_s, to_s] ranges (seconds from "
                          f"the shot's start, from_s <= to_s), got {s.hold!r}")  # fmt: skip

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
    if short.sound_policy not in SOUND_POLICIES:
        errors.append(f"sound_policy must be one of {', '.join(p for p in SOUND_POLICIES if p)} (or empty), got "
                      f"{short.sound_policy!r}")  # fmt: skip
    for fx in short.sfx:
        if fx.shot not in shot_ids:
            errors.append(f"sfx {fx.kind!r} refers to unknown shot {fx.shot!r}")
    return errors
