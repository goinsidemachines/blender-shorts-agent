"""A list of the workspace's shorts, each with its look, length and voice (for "make it like that one")."""

from __future__ import annotations

import copy
import statistics
import time
from datetime import UTC, datetime
from pathlib import Path

from bsa import engine_runs, jobstate, kit, settings, workspace
from bsa.narrate import run as narrate_run
from bsa.short import Short, load_short


def _label(record: dict) -> str:
    """The voice a short was narrated with, from narrator.json: "Kokoro (af_heart, 1.0×)"."""
    provider = record.get("provider")
    if not provider:
        return "not narrated"
    if provider == "none":
        return "captions only"
    if provider == "custom":
        return "your voice tool"
    name = settings._PROVIDER_NAMES.get(provider, provider)
    if not record.get("voice"):  # no voice on record: just the provider, never "Kokoro ()"
        return str(name)
    parts = [str(record["voice"])]
    try:
        parts.append(f"{float(record.get('speed') or 1.0):.1f}×")
    except (TypeError, ValueError):
        pass
    return f"{name} ({', '.join(parts)})"


def _edit_seconds(sdir: Path) -> float | None:
    rec = engine_runs.edit_info(sdir, "final")
    try:
        seconds = round(float(rec["frames"]) / float(rec["fps"]), 1)
    except (TypeError, ValueError, KeyError, ZeroDivisionError):
        return None
    return seconds if seconds > 0 else None


def _finished(sdir: Path) -> bool | None:
    """True when the final stage is done; None when state.json is damaged (the list says so, never breaks)."""
    try:
        return "final" in jobstate.load_state(sdir)["stages"]
    except Exception:  # noqa: BLE001 - a damaged state never breaks the list
        return None


def summary(sdir: Path, settings_: dict) -> dict:
    """One short's summary. A short.json that can't be read gives `problem` and None values; never raises."""
    sdir = Path(sdir)
    m = workspace._NUM.match(sdir.name)
    finished = _finished(sdir)
    row = {"short": sdir.name, "number": int(m.group(1)) if m else None, "title": None, "style": None, "set": None,
           "length_s": None, "narrator": "not narrated", "finished": bool(finished),
           "problem": None if finished is not None else "state.json damaged"}  # fmt: skip
    try:
        short = load_short(sdir / "source" / "short.json")
    except Exception as exc:  # noqa: BLE001
        row["problem"] = f"short.json can't be read: {exc}" + ("; state.json damaged" if finished is None else "")
        return row
    row["title"] = short.title
    k = kit.kit_for(settings_, short)
    if k is not None:
        row["style"], row["set"] = k.style, k.set
    row["length_s"] = _edit_seconds(sdir) or short.length_s
    row["narrator"] = _label(narrate_run._read_record(sdir / "audio" / "vo"))
    return row


def list_shorts(root: Path, settings_: dict) -> list[dict]:
    """Every numbered short in the workspace, highest number first."""
    return [summary(p, settings_) for _, p in sorted(workspace._numbered(root), key=lambda t: t[0], reverse=True)]


# "Make it like that one": what `new-short --like` copies from an earlier short. Never its story, facts, sources,
# shots, assets, post or thumbnail, and never the engine it was made with: the engine is always the user's choice.
COPY_KEYS = ("style", "look", "length_s", "pace", "end_card", "closing", "narrator")
LABELS = {"style": "the look", "look": "the brand or fresh look", "length_s": "the length", "pace": "the pace",
          "end_card": "the end card", "closing": "the closing", "narrator": "the voice"}  # fmt: skip
# The interview question each copied key answers (the interview skips those).
SKIP = {"length_s": "length", "pace": "pace", "style": "look", "look": "look", "end_card": "closing",
        "closing": "closing", "narrator": "voice"}  # fmt: skip


def pace_of(short) -> dict:
    """The short's pace as guidance for a new storyboard: shot count, median shot min_s, shots with a hold.
    {} for a short without shots."""
    shots = list(short.shots)
    if not shots:
        return {}
    lengths = []
    for s in shots:
        try:
            lengths.append(float(s.min_s))
        except (TypeError, ValueError):
            pass
    return {"shots": len(shots), "min_s": round(statistics.median(lengths), 1) if lengths else 3.0,
            "holds": sum(1 for s in shots if s.hold)}  # fmt: skip


def _provider_name(provider) -> str:
    return settings._PROVIDER_NAMES.get(provider, provider) if provider else "no narrator"


def _blank(look=None) -> Short:
    """A short with no choices of its own: what a new short gets from the user's settings alone."""
    return Short(slug="new", title="New", length_s=45, look=look)


def _narrator_from(other_dir: Path, other, settings_: dict) -> tuple[dict, list[str]]:
    """The other short's take_mode, lead_in and (narrated by the provider the user has now) voice, speed and engine
    params, each only where it differs from what the user's settings give a new short now: a value equal to the
    settings is left out, so later settings changes keep reaching the new short."""
    record = narrate_run._read_record(other_dir / "audio" / "vo")
    used = record.get("provider")
    narrated = bool(used) and used != "none"
    if narrated:  # narrator.json leaves out take_mode and lead_in at their defaults
        theirs = {"take_mode": record.get("take_mode") or "line", "lead_in": float(record.get("lead_in") or 0.0)}
    else:
        eff = narrate_run.narrator_for(settings_, other)
        theirs = {"take_mode": eff["take_mode"], "lead_in": eff["lead_in"]}
    notes: list[str] = []
    now = (settings_.get("narrator") or {}).get("provider")
    if used == "none":
        notes.append(f"the voice wasn't copied: {other.title} was captions only")
    elif not used:
        notes.append(f"the voice wasn't copied: {other.title} hasn't been narrated yet")
    elif used != now:
        notes.append(f"the voice wasn't copied: {other.title} used {_provider_name(used)}, you now use "
                     + _provider_name(now))  # fmt: skip
    else:
        if record.get("voice"):
            theirs["voice"] = record["voice"]
        if record.get("speed") is not None:
            theirs["speed"] = float(record["speed"])
        if isinstance(record.get("params"), dict) and record["params"]:  # the engine's delivery, with its voice
            theirs["params"] = dict(record["params"])
    mine = narrate_run.narrator_for(settings_, _blank())
    mine["speed"] = float(mine.get("speed") or 1.0)
    mine["lead_in"] = float(mine["lead_in"])
    out = {k: v for k, v in theirs.items() if v != mine.get(k)}
    if {"voice", "speed", "params"} & set(out):  # the narrator they belong to: under another one they are not used
        out["voice_for"] = used
    return out, notes


class _Refused(Exception):
    """A style value the current kit refuses (it is not copied)."""


def _norm(path: str, value):
    """A style value as the kit stores it (so "#e8b33a" equals "#E8B33A"). _Refused when the kit refuses it now."""
    try:
        return kit.check(path, value, _must_exist=False)
    except (ValueError, TypeError) as exc:
        raise _Refused(path) from exc


_SUB_KEYS = ("palette", "categories", "caption", "logo")


def _style_from(style: dict, settings_: dict, look) -> tuple[dict, list[str]]:
    """(the other short's style keys (per sub-key for palette, categories, caption and logo) whose values differ from
    what the user's brand gives a new short now, the keys left out because the kit refuses their value now). Equal
    ones are left out so the new short keeps following the brand."""
    base, _ = kit._merged(settings_, _blank(look))
    brand = settings.brand_for(settings_, _blank(look))
    out: dict = {}
    refused: list[str] = []
    for key, value in style.items():
        if key not in kit._KIT_KEYS:  # prose and other keys: compared with the brand as written
            if brand.get(key) != value:
                out[key] = copy.deepcopy(value)
        elif key in _SUB_KEYS and isinstance(value, dict):
            cur = base.get(key) or {}
            if key == "logo" and isinstance(brand.get("logo"), str):
                cur = {"file": brand["logo"]}
            sub = {}
            for k, v in value.items():
                name = str(k).strip().lower() if key in ("palette", "categories") else k
                try:
                    if _norm(f"{key}.{k}", v) != cur.get(name):
                        sub[k] = copy.deepcopy(v)
                except _Refused:
                    refused.append(f"{key}.{k}")
            if sub:
                out[key] = sub
        else:
            try:
                if _norm(key, value) != base.get(key):
                    out[key] = copy.deepcopy(value)
            except _Refused:
                refused.append(key)
    return out, refused


def copy_from(other: Path, settings_: dict) -> tuple[dict, list[str], list[str]]:
    """(values for the new short.json, the keys copied in COPY_KEYS order, notes) from the earlier short `other`.
    Only what made it different is copied: a value equal to what the user's settings give a new short now is left
    out. ValueError when its short.json can't be read."""
    other = Path(other)
    try:
        short = load_short(other / "source" / "short.json")
    except Exception as exc:
        raise ValueError(f"{other.name}'s short.json can't be read: {exc}") from exc
    values: dict = {}
    notes: list[str] = []
    if short.look is not None and short.look != settings.look_mode(settings_, None):
        values["look"] = short.look
    if isinstance(short.style, dict) and short.style:
        style, refused = _style_from(short.style, settings_, values.get("look"))
        notes += [f"{short.title}'s {key} isn't valid in your kit now, so it wasn't copied" for key in refused]
        if style:
            values["style"] = style
            if kit.kit_for(settings_, _blank(values.get("look"))) is None and kit.kit_for(settings_, short) is not None:
                notes.append(f"this short keeps {short.title}'s look as its own")
    values["length_s"] = short.length_s
    pace = pace_of(short)
    if pace:
        values["pace"] = pace
    for key in ("end_card", "closing"):
        if getattr(short, key) is not None:
            values[key] = copy.deepcopy(getattr(short, key))
    narrator, voice_notes = _narrator_from(other, short, settings_)
    if narrator:
        values["narrator"] = narrator
    notes += voice_notes
    return values, [k for k in COPY_KEYS if k in values], notes


def style_keys(style: dict) -> list[str]:
    """The style keys a copy took, one per value: "palette.accent", "caption.box", "font"."""
    out: list[str] = []
    for key, value in style.items():
        if key in _SUB_KEYS and isinstance(value, dict):
            out += [f"{key}.{sub}" for sub in value]
        else:
            out.append(key)
    return out


def provenance(other: Path, copied: list[str], now: float | None = None, title: str | None = None,
               style: dict | None = None) -> dict:
    """short.json's `like`: where the short started from, what was copied and when (UTC), the other short's title
    (kept for when its folder is gone) and exactly which style keys were copied."""
    at = datetime.fromtimestamp(time.time() if now is None else now, tz=UTC)
    out = {"short": Path(other).name, "copied": list(copied), "at": at.strftime("%Y-%m-%dT%H:%M:%SZ")}
    if title:
        out["title"] = title
    if style:
        out["style_keys"] = style_keys(style)
    return out


def skipped_questions(copied: list[str], narrator: dict | None = None) -> list[str]:
    """The interview questions the copied keys answer, each once, in order. The voice question is skipped only when
    the voice itself was copied (`narrator`, the copied narrator values, has a voice); without `narrator` a copied
    narrator counts as the voice."""
    out: list[str] = []
    for key in copied:
        if key == "narrator" and narrator is not None and "voice" not in narrator:
            continue
        q = SKIP.get(key)
        if q and q not in out:
            out.append(q)
    return out


def said(title: str, skipped: list[str]) -> str:
    """The one line telling the user what was copied: "Started from <title>: I copied the look, pace, closing and
    voice, so I won't ask about those." """
    if not skipped:
        return f"Started from {title}: nothing could be copied, so I'll ask about everything."
    words = skipped[0] if len(skipped) == 1 else f"{', '.join(skipped[:-1])} and {skipped[-1]}"
    return f"Started from {title}: I copied the {words}, so I won't ask about {'it' if len(skipped) == 1 else 'those'}."


def _n(value) -> str:
    return f"{float(value):g}"


def _style_words(keys: list[str], value: dict) -> str:
    """The style line from the record of copied keys (names only; a name or font is read from the style)."""
    groups: dict[str, list[str]] = {}
    for k in keys:
        head, _, sub = k.partition(".")
        groups.setdefault(head, [])
        if sub:
            groups[head].append(sub)
    parts = []
    for head, subs in groups.items():
        if head == "style":
            parts.append(f"{value['style']} style" if value.get("style") else "style")
        elif head == "set":
            parts.append(f"{value['set']} set" if value.get("set") else "set")
        elif head == "font":
            parts.append(f"font {value['font']}" if value.get("font") else "font")
        elif head == "palette":
            parts.append(f"own palette ({', '.join(subs)})" if subs else "own palette")
        else:
            name = head.replace("_", " ")
            parts.append(f"{name} {subs[0]}" if len(subs) == 1 else f"{name} ({', '.join(subs)})" if subs else name)
    return ", ".join(parts)


def describe(key: str, value, record: dict | None = None) -> str:
    """One plain line for a copied value, as the page's "Started from" block shows it. `record` is short.json's
    `like`: a style is described from the keys it records as copied (without them, from the style as it is)."""
    try:
        if key == "style" and isinstance(value, dict) and (record or {}).get("style_keys"):
            return _style_words(record["style_keys"], value)
        return _describe(key, value)
    except (TypeError, ValueError, AttributeError):
        return "copied"


def _describe(key: str, value) -> str:
    if key == "style":
        v = value if isinstance(value, dict) else {}
        parts = []
        if v.get("style"):
            parts.append(f"{v['style']} style")
        if v.get("set"):
            parts.append(f"{v['set']} set")
        if v.get("palette"):
            parts.append("own palette")
        if v.get("font"):
            parts.append(f"font {v['font']}")
        return ", ".join(parts) or "its own style choices"
    if key == "look":
        return "brand" if value == "brand" else "its own look"
    if key == "length_s":
        return f"{_n(value)} s"
    if key == "pace":
        shots = int(value.get("shots") or 0)
        parts = [f"{shots} shot{'s' if shots != 1 else ''}"]
        if value.get("min_s") is not None:
            parts.append(f"about {_n(value['min_s'])} s each")
        holds = int(value.get("holds") or 0)
        if holds:
            parts.append(f"{holds} hold{'s' if holds != 1 else ''}")
        return ", ".join(parts)
    if key in ("end_card", "closing"):
        if isinstance(value, dict):
            text = value.get("text") or value.get("voice_line") or value.get("website")
            return str(text) if text else "its own settings"
        if value is True:
            return "on"
        return str(value) if value else "none"
    if key == "narrator":
        parts = []
        if value.get("voice"):
            said_ = f"voice {value['voice']}"
            if value.get("speed") is not None:
                said_ += f" at {float(value['speed']):.1f}×"
            parts.append(said_)
        elif value.get("speed") is not None:
            parts.append(f"speed {float(value['speed']):.1f}×")
        if value.get("take_mode"):
            parts.append(f"{value['take_mode']} takes")
        if value.get("lead_in") is not None:
            parts.append(f"{_n(value['lead_in'])} s lead-in")
        return ", ".join(parts) or "its own voice settings"
    return str(value)
