"""Named style presets: a reusable block of the values that give a short its feel (the `timing` and `captions` groups
and the `loop` flag). Stdlib only.

A preset is an object with any of the groups in `GROUPS` and optionally `"extend": "<other preset name>"` (that preset
is applied first, then this one on top). Shipped presets are neutral; a user adds their own under the settings key
`presets` (same name as a shipped one = theirs replaces it). The render engine is never part of a preset: it is the
user's choice for each short.

Choosing: the settings key `style_preset` (null = "explainer", the built-in defaults), and a short's own `preset` in
short.json (the short wins).

Order of precedence, last wins (`layers`, used by `bsa.timing.resolve`):
  1. the built-in defaults (bsa.timing.SPEC)
  2. the settings' own top-level `timing` / `captions` (the user's global tweaks)
  3. the chosen preset, its `extend` chain applied base first
  4. the short's own `timing` / `captions` in short.json
The `loop` flag (true: the short ends seamlessly back into frame 1) resolves the same way: settings top-level `loop`
(default false), the preset chain, the short's own `loop` (`loop_on`).
"""

from __future__ import annotations

import re

from bsa import master, targets, timing

# The groups a preset may hold. One place to extend: a new group is added here (and given its check in
# `_group_problems` and its reader in `_valid_flat`) when its feature lands.
GROUPS = ("timing", "captions", "loop", "master", "targets")
# `loop` is one yes/no value (a short that ends seamlessly back into frame 1), not an object like the other groups.
FLAGS = ("loop",)
EXTEND = "extend"
DEFAULT_NAME = "explainer"
# Keys that choose the render engine are refused anywhere in a preset.
ENGINE_KEYS = ("render", "engine")
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,39}$")

SHIPPED: dict[str, dict] = {
    # Today's defaults: nothing overridden.
    "explainer": {},
    # Tight pacing, short caption cards and a seamless loop (no end card, a looped sound bed) for a short that
    # restarts straight away.
    "fast-loop": {
        "timing": {"lead_in_s": 0.05, "gap_s": 0.2, "tail_s": 0.1, "take_tail_s": 0.12, "end_card_s": 0.0},
        "captions": {"max_words": 3, "max_chars": 18, "orphan_max_chars": 6},
        "loop": True,
    },
}


class PresetError(ValueError):
    """A preset name that does not exist, or an extend chain that loops."""


def user_presets(settings: dict | None) -> dict:
    own = (settings or {}).get("presets")
    return own if isinstance(own, dict) else {}


def all_presets(settings: dict | None = None) -> dict[str, dict]:
    """Shipped presets with the user's over them (a user preset replaces a shipped one of the same name)."""
    out = dict(SHIPPED)
    out.update({k: v for k, v in user_presets(settings).items() if isinstance(k, str)})
    return out


def source_of(name: str, settings: dict | None = None) -> str:
    """"user" when the settings define `name` (replacing a shipped one of that name too), else "shipped"."""
    return "user" if name in user_presets(settings) else "shipped"


def chain(name: str, presets: dict[str, dict]) -> list[str]:
    """`name` and what it extends, base first. PresetError for an unknown name or a loop."""
    out: list[str] = []
    current: str | None = name
    while current is not None:
        if current not in presets:
            raise PresetError(f"unknown preset {current!r}: use one of {', '.join(sorted(presets))}")
        if current in out:
            raise PresetError(f"presets extend each other in a loop: {' -> '.join([*out, current])}")
        out.append(current)
        base = presets[current].get(EXTEND) if isinstance(presets[current], dict) else None
        current = base if isinstance(base, str) else None
    return out[::-1]


def _short_preset(short):
    return short.get("preset") if isinstance(short, dict) else getattr(short, "preset", None)


def chosen(settings: dict | None, short=None) -> str | None:
    """The preset name that applies: the short's own `preset`, else the settings' `style_preset`, else None (= the
    built-in explainer)."""
    own = _short_preset(short)
    if isinstance(own, str) and own.strip():
        return own.strip()
    named = (settings or {}).get("style_preset")
    return named.strip() if isinstance(named, str) and named.strip() else None


def active_name(settings: dict | None, short=None) -> str:
    return chosen(settings, short) or DEFAULT_NAME


def _group_problems(group: str, value) -> list[str]:
    if group in FLAGS:
        return [] if value is None or isinstance(value, bool) else [f"{group} must be true or false, got {value!r}"]
    if group == master.GROUP:
        return master.problems_in(value)
    if group == targets.GROUP:
        return targets.problems_in(value)
    if group in timing.GROUPS:
        return timing.problems_in(group, value)
    return [f"unsupported group {group!r}"]  # a group in GROUPS without a check here would be a bug


def preset_problems(name: str, preset, presets: dict[str, dict] | None = None) -> list[str]:
    """What is wrong with one preset: not an object, a render/engine key, an unknown group, a value out of range, an
    unknown or looping `extend`. [] when it is sound. `presets` is the full set `extend` is looked up in."""
    label = f"preset {name!r}"
    if not isinstance(preset, dict):
        return [f"{label} must be an object such as {{\"timing\": {{...}}}}, got {preset!r}"]
    out = []
    engine = [k for k in preset if k in ENGINE_KEYS]
    if engine:
        out.append(f"{label} has {', '.join(repr(k) for k in engine)}: the render engine is always the user's choice "
                   "per short; presets can't set it")  # fmt: skip
    extra = sorted(str(k) for k in preset if k not in GROUPS and k != EXTEND and k not in ENGINE_KEYS)
    if extra:
        out.append(f"{label} has unknown groups {extra}: use {', '.join(GROUPS)} (and extend)")
    for group in GROUPS:
        out += [f"{label}: {p}" for p in _group_problems(group, preset.get(group))]
    if EXTEND in preset:
        base = preset[EXTEND]
        if not isinstance(base, str):
            out.append(f"{label}: extend must be another preset's name, got {base!r}")
        elif presets is not None:
            try:
                chain(name, presets)
            except PresetError as exc:
                out.append(f"{label}: {exc}")
    return out


def name_problem(name) -> str | None:
    if not isinstance(name, str) or not NAME_RE.match(name):
        return (f"preset name {name!r} must be letters, digits, '-' or '_' (start with a letter or digit, up to 40), "
                "for example fast-loop")  # fmt: skip
    return None


def _name_known(key: str, value, merged: dict) -> list[str]:
    if not isinstance(value, str) or not value.strip():
        return [f"{key} must be a preset name or null, got {value!r}"]
    try:
        chain(value.strip(), merged)
    except PresetError as exc:
        return [f"{key}: {exc}"]
    return []


def problems(settings: dict | None = None, short=None) -> list[str]:
    """Every problem of the presets in the settings, of the chosen name and of the short's `preset`."""
    raw = (settings or {}).get("presets")
    if raw is not None and not isinstance(raw, dict):
        return [f"presets must be an object of {{name: preset}}, got {raw!r}"]
    out: list[str] = []
    merged = all_presets(settings)
    for name, preset in user_presets(settings).items():
        bad = name_problem(name)
        out += [bad] if bad else preset_problems(name, preset, merged)
    named = (settings or {}).get("style_preset")
    if named is not None:
        out += _name_known("style_preset", named, merged)
    own = _short_preset(short)
    if own is not None:
        out += [f"short.json {p}" for p in _name_known("preset", own, merged)]
    out += _group_problems(master.GROUP, (settings or {}).get(master.GROUP))
    out += [f"short.json {p}" for p in _group_problems(master.GROUP, _short_value(short, master.GROUP))]
    out += _group_problems(targets.GROUP, (settings or {}).get(targets.GROUP))
    out += [f"short.json {p}" for p in _group_problems(targets.GROUP, _short_value(short, targets.GROUP))]
    for flag in FLAGS:
        out += _group_problems(flag, (settings or {}).get(flag))
        out += [f"short.json {p}" for p in _group_problems(flag, _short_value(short, flag))]
    return out


def _valid_flat(source) -> dict:
    """{key: checked value} of the `timing` / `captions` objects in `source` (a dict); a bad value is left out. (The
    `loop` flag is not here: see `loop_resolved`.)"""
    out: dict = {}
    if not isinstance(source, dict):
        return out
    for group in GROUPS:
        own = source.get(group)
        if group not in timing.SPEC or not isinstance(own, dict):
            continue
        for k, v in own.items():
            if k in timing.SPEC[group]:
                try:
                    out[k] = timing.check(group, k, v)
                except ValueError:
                    pass
    return out


def layers(settings: dict | None, short=None) -> list[tuple[str, dict]]:
    """The value layers on top of the built-in defaults, lowest first: [(label, {key: value})]. Labels: "settings",
    "preset:<name>" (one per preset in the extend chain, base first) and "short". Tolerant: a bad value, an unknown
    preset or a loop is left out here (validation reports it), so a bad file never stops a build."""
    base = timing.Timing().as_dict()  # the settings file holds every default too: only a changed value is a layer
    own_settings = {k: v for k, v in _valid_flat(settings or {}).items() if v != base[k]}
    out: list[tuple[str, dict]] = [("settings", own_settings)]
    name = chosen(settings, short)
    if name:
        merged = all_presets(settings)
        try:
            names = chain(name, merged)
        except PresetError:
            names = []
        out += [(f"preset:{n}", _valid_flat(merged[n])) for n in names]
    own = short if isinstance(short, dict) else {g: getattr(short, g, None) for g in GROUPS}
    out.append(("short", _valid_flat(own)))
    return out


def _short_value(short, key: str):
    return short.get(key) if isinstance(short, dict) else getattr(short, key, None)


def loop_resolved(settings: dict | None, short=None) -> tuple[bool, str]:
    """(loop on?, where it came from): same order as the timing values, last wins: the settings' own top-level `loop`
    (default off), the chosen preset (its `extend` chain base first), then the short's own `loop`. A value that is not
    true or false is ignored here (validation reports it). Where: "default", "settings", "preset:<name>", "short"."""
    value, where = False, "default"
    own = (settings or {}).get("loop")
    if isinstance(own, bool):
        value, where = own, "settings"
    name = chosen(settings, short)
    if name:
        merged = all_presets(settings)
        try:
            names = chain(name, merged)
        except PresetError:
            names = []
        for n in names:
            flag = merged[n].get("loop") if isinstance(merged[n], dict) else None
            if isinstance(flag, bool):
                value, where = flag, f"preset:{n}"
    mine = _short_value(short, "loop")
    if isinstance(mine, bool):
        value, where = mine, "short"
    return value, where


def loop_on(settings: dict | None, short=None) -> bool:
    """True when the short ends seamlessly back into frame 1 (see `loop_resolved`): it has no end card and no
    closing card, and its sound bed wraps. Off unless something turns it on."""
    return loop_resolved(settings, short)[0]


def _valid_group(mod, source) -> dict:
    """{key: checked value} of the `mod.GROUP` object in `source` (a dict); a bad value is left out."""
    own = source.get(mod.GROUP) if isinstance(source, dict) else None
    out: dict = {}
    for k, v in own.items() if isinstance(own, dict) else ():
        if k in mod.SPEC:
            try:
                out[k] = mod.check(k, v)
            except ValueError:
                pass
    return out


def _spec_resolved(mod, settings: dict | None, short=None) -> tuple[dict, dict]:
    """(values, where) of a spec group (`bsa.master`, `bsa.targets`): the built-in defaults, the settings' own object (a
    value equal to the default is no layer, as the settings file holds every default), the chosen preset (its `extend`
    chain base first), the short's own. Where: "default", "settings", "preset:<name>", "short"."""
    values = mod.defaults()
    where = {k: "default" for k in values}
    own = {k: v for k, v in _valid_group(mod, settings or {}).items() if v != values[k]}
    stack: list[tuple[str, dict]] = [("settings", own)]
    name = chosen(settings, short)
    if name:
        merged = all_presets(settings)
        try:
            names = chain(name, merged)
        except PresetError:
            names = []
        stack += [(f"preset:{n}", _valid_group(mod, merged[n])) for n in names]
    mine = short if isinstance(short, dict) else {mod.GROUP: getattr(short, mod.GROUP, None)}
    stack.append(("short", _valid_group(mod, mine)))
    for label, flat in stack:
        for k, v in flat.items():
            values[k], where[k] = v, label
    return values, where


def master_resolved(settings: dict | None, short=None) -> tuple[dict, dict]:
    """(the loudness master's values, where each came from): same order as the timing values, last wins (see
    `_spec_resolved`). A bad value is ignored here (validation reports it)."""
    return _spec_resolved(master, settings, short)


def targets_resolved(settings: dict | None, short=None) -> tuple[dict, dict]:
    """(the script and edit targets' values, where each came from), all null (off) unless something sets them: the
    same order as `master_resolved`. A bad value is ignored here (validation reports it)."""
    return _spec_resolved(targets, settings, short)


def resolved(settings: dict | None, short=None) -> tuple[dict, dict]:
    """(every timing/captions value, where each came from): "default", "settings", "preset:<name>" or "short"."""
    values = timing.Timing().as_dict()
    where = {k: "default" for k in values}
    for label, flat in layers(settings, short):
        for k, v in flat.items():
            values[k], where[k] = v, label
    return values, where


def overrides(settings: dict | None, short=None) -> dict:
    """The short's own timing/captions values, {key: value} (what the page shows as "short overrides")."""
    return dict(layers(settings, short)[-1][1])


def line(settings: dict | None, short=None) -> str:
    """"Style preset: fast-loop (short overrides: gap_s 0.25)"."""
    over = overrides(settings, short)
    text = f"Style preset: {active_name(settings, short)}"
    if over:
        text += " (short overrides: " + ", ".join(f"{k} {v:g}" for k, v in over.items()) + ")"
    return text


def listing(settings: dict | None = None) -> list[dict]:
    """Every preset for `preset list`: name, shipped/user, what it extends, the groups it sets, and whether it is the
    one a new short gets."""
    merged = all_presets(settings)
    default = active_name(settings)
    rows = []
    for name in sorted(merged, key=lambda n: (n not in SHIPPED, n)):
        p = merged[name] if isinstance(merged[name], dict) else {}
        rows.append({"name": name, "source": source_of(name, settings), "extends": p.get(EXTEND),
                     "groups": [g for g in GROUPS if p.get(g)], "default": name == default})  # fmt: skip
    return rows


def from_short(settings: dict | None, short) -> dict:
    """The preset `preset save` stores: the short's resolved timing and captions values that differ from the built-in
    defaults (a preset only holds what makes it different)."""
    values, _ = resolved(settings, short)
    base = timing.Timing().as_dict()
    out: dict = {}
    for group in GROUPS:
        if group not in timing.SPEC:
            continue
        diff = {k: values[k] for k in timing.SPEC[group] if values[k] != base[k]}
        if diff:
            out[group] = diff
    if loop_on(settings, short):
        out["loop"] = True
    mvalues, _ = master_resolved(settings, short)
    mdiff = {k: v for k, v in mvalues.items() if v != master.defaults()[k]}
    if mdiff:
        out[master.GROUP] = mdiff
    tvalues = targets.active(targets_resolved(settings, short)[0])
    if tvalues:
        out[targets.GROUP] = tvalues
    return out
