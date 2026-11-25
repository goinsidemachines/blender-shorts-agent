"""Timing and caption-card values of a short: one place that turns the settings (and the short's own `timing` and
`captions` objects) into the numbers the timeline, the takes and the checks use. Stdlib only.

Order of precedence, last wins: the built-in defaults, the `timing` / `captions` settings, the short's own
`timing` / `captions` in short.json. Worker Blender processes call `resolve` with the settings file and short.json they
already load, so a value set in either reaches them without patching any module."""

from __future__ import annotations

from dataclasses import dataclass, fields

# group -> key -> (default, low, high, whole number, what it is)
SPEC: dict[str, dict[str, tuple]] = {
    "timing": {
        "lead_in_s": (0.2, 0.0, 2.0, False, "silence before a shot's first line"),
        "gap_s": (0.35, 0.0, 2.0, False, "silence between two lines of a shot"),
        "tail_s": (0.6, 0.0, 2.0, False, "hold after a shot's last line"),
        "take_tail_s": (0.35, 0.0, 2.0, False, "pause kept after a line's last word when a paragraph take is cut"),
        "end_card_s": (1.5, 0.0, 5.0, False, "hold added to the last shot for the end card"),
    },
    "captions": {
        "max_words": (6, 1, 12, True, "most words on one caption card"),
        "max_chars": (32, 6, 60, True, "most characters on one caption card"),
        "orphan_max_chars": (8, 0, 20, True, "a one-word card this short joins a neighbour"),
    },
}
GROUPS = tuple(SPEC)


def defaults(group: str) -> dict:
    """The built-in values of `timing` or `captions` (what settings.DEFAULTS holds)."""
    return {k: v[0] for k, v in SPEC[group].items()}


@dataclass(frozen=True)
class Timing:
    lead_in_s: float = SPEC["timing"]["lead_in_s"][0]
    gap_s: float = SPEC["timing"]["gap_s"][0]
    tail_s: float = SPEC["timing"]["tail_s"][0]
    take_tail_s: float = SPEC["timing"]["take_tail_s"][0]
    end_card_s: float = SPEC["timing"]["end_card_s"][0]
    max_words: int = SPEC["captions"]["max_words"][0]
    max_chars: int = SPEC["captions"]["max_chars"][0]
    orphan_max_chars: int = SPEC["captions"]["orphan_max_chars"][0]

    def as_dict(self) -> dict:
        return {f.name: getattr(self, f.name) for f in fields(self)}


def check(group: str, key: str, value):
    """`value` as stored (a float, or an int for the whole-number keys); ValueError saying what is allowed."""
    if group not in SPEC or key not in SPEC[group]:
        allowed = ", ".join(f"{g}.{k}" for g in SPEC for k in SPEC[g])
        raise ValueError(f"unknown setting {group}.{key}: use one of {allowed}")
    _, lo, hi, whole, what = SPEC[group][key]
    ok = isinstance(value, (int, float)) and not isinstance(value, bool)
    if ok and whole:
        ok = float(value) == int(value)
    if not ok or not lo <= value <= hi:
        kind = "a whole number" if whole else "seconds" if group == "timing" else "a number"
        raise ValueError(f"{group}.{key} ({what}) must be {kind} from {lo:g} to {hi:g}, got {value!r}")
    return int(value) if whole else float(value)


def problems_in(group: str, value) -> list[str]:
    """What is wrong with a `timing` or `captions` object (settings or short.json): not an object, unknown keys, or a
    value out of range. [] when it is sound (None counts as absent)."""
    if value is None:
        return []
    if not isinstance(value, dict):
        first = next(iter(SPEC[group]))
        return [f"{group} must be an object such as {{\"{first}\": ...}}, got {value!r}"]
    out = []
    extra = sorted(set(value) - set(SPEC[group]))
    if extra:
        out.append(f"{group} has unknown keys {extra}: use {', '.join(SPEC[group])}")
    for k, v in value.items():
        if k in SPEC[group]:
            try:
                check(group, k, v)
            except ValueError as exc:
                out.append(str(exc))
    return out


def problems(settings: dict | None = None, short=None) -> list[str]:
    """Every problem of the timing and captions values in the settings and in the short."""
    out = []
    for group in GROUPS:
        out += problems_in(group, (settings or {}).get(group))
        out += [f"short.json {p}" for p in problems_in(group, getattr(short, group, None))]
    return out


def resolve(settings: dict | None = None, short=None) -> Timing:
    """The Timing for a short: defaults, then the settings' `timing` / `captions`, then the short's own. Values that
    do not validate are ignored here (validate and settings set report them), so a bad file never stops a build."""
    values = Timing().as_dict()
    for source in (settings or {}, short):
        for group in GROUPS:
            own = source.get(group) if isinstance(source, dict) else getattr(source, group, None)
            if not isinstance(own, dict):
                continue
            for k, v in own.items():
                if k in SPEC[group]:
                    try:
                        values[k] = check(group, k, v)
                    except ValueError:
                        pass
    return Timing(**values)


def for_short(short=None) -> Timing:
    """resolve() with the settings file as it is now (for code that has no settings dict at hand)."""
    from bsa import settings

    return resolve(settings.load_settings(), short)
