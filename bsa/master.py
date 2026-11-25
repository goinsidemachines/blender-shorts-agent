"""The loudness master of the final mix: its settings and their checks. Stdlib only (the processing is in
bsa.audio.master).

One group, `master`, resolved like the timing values (built-in defaults, the settings' own `master`, the chosen style
preset, the short's own `master`; see `bsa.presets.master_resolved`):

- `enabled`: master the final mix to the level below (false: the mix is written exactly as it is mixed).
- `integrated_lufs`: the target integrated loudness (ITU-R BS.1770-4 / EBU R128, gated), default -14 (the public
  streaming norm). Set your own.
- `true_peak_dbtp`: the true-peak ceiling in dBTP, default -1.
- `max_lra_lu`: the most loudness range (EBU Tech 3342) allowed in LU, default null = no check.
"""

from __future__ import annotations

GROUP = "master"
# key -> (default, low, high, kind, what it is); kind "bool", "num" or "num?" (a number, or null = off)
SPEC: dict[str, tuple] = {
    "enabled": (True, None, None, "bool", "master the final mix to the target loudness"),
    "integrated_lufs": (-14.0, -30.0, -6.0, "num", "target integrated loudness in LUFS"),
    "true_peak_dbtp": (-1.0, -9.0, 0.0, "num", "true-peak ceiling in dBTP"),
    "max_lra_lu": (None, 1.0, 20.0, "num?", "most loudness range in LU, null = no check"),
}


def defaults() -> dict:
    return {k: v[0] for k, v in SPEC.items()}


def check(key: str, value):
    """`value` as stored; ValueError saying what is allowed."""
    if key not in SPEC:
        raise ValueError(f"unknown setting {GROUP}.{key}: use one of {', '.join(f'{GROUP}.{k}' for k in SPEC)}")
    _, lo, hi, kind, what = SPEC[key]
    if kind == "bool":
        if not isinstance(value, bool):
            raise ValueError(f"{GROUP}.{key} ({what}) must be true or false, got {value!r}")
        return value
    if value is None and kind == "num?":
        return None
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not lo <= value <= hi:
        null = " or null" if kind == "num?" else ""
        raise ValueError(f"{GROUP}.{key} ({what}) must be a number from {lo:g} to {hi:g}{null}, got {value!r}")
    return float(value)


def problems_in(value) -> list[str]:
    """What is wrong with a `master` object (settings, short.json or a preset): [] when sound (None = absent)."""
    if value is None:
        return []
    if not isinstance(value, dict):
        return [f'{GROUP} must be an object such as {{"integrated_lufs": -16}}, got {value!r}']
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
