"""`narrator.params`: engine parameters for a local voice engine (Chatterbox's own, say), set in the settings and
over them a short's own. The runner gives them last: the model's params, then the voice's, then these.

Which keys an engine takes, and the range of each, come from the runner's registry (a model's `params` and
`param_ranges`); an engine without `params` takes none, and nor do ElevenLabs or a custom voice tool.
"""

from __future__ import annotations

import math

KEY = "narrator.params"


def _registry_models() -> dict:
    from bsa.narrate import install

    return {k: m for k, m in install._registry()["models"].items() if m.get("kind") == "tts"}


def _number(value) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value)


def shape_problems(params) -> list[str]:
    """Problems of the value itself: an object of parameter names to finite numbers."""
    if not isinstance(params, dict):
        example = '{"temperature": 0.7}'
        return [f"{KEY} must be an object of engine parameters to numbers, such as {example}, got {params!r}"]
    return [f"{KEY}.{k} must be a number, got {v!r}" for k, v in params.items() if not _number(v)]


def engines_with_params() -> dict[str, dict]:
    """{provider: {param: [low, high] or None}} for the voice engines that take parameters."""
    out = {}
    for name, m in _registry_models().items():
        if m.get("params"):
            ranges = m.get("param_ranges") or {}
            out[name] = {k: ranges.get(k) for k in m["params"]}
    return out


def problems(provider, params) -> list[str]:
    """Why `params` can't go to `provider`'s engine: unknown keys or a provider that takes none (each naming the
    keys allowed), and numbers out of range. [] for empty params, whatever the provider."""
    if params in (None, {}):
        return []
    out = shape_problems(params)
    if out:
        return out
    engines = engines_with_params()
    allowed = engines.get(provider)
    if allowed is None:
        which = "; ".join(f"{name}: {', '.join(keys)}" for name, keys in engines.items())
        name = provider or "no narrator"
        return [f"{name} takes no engine parameters, so {KEY} must be empty (engines that take them: {which})"]
    for k, v in params.items():
        if k not in allowed:
            out.append(f"{KEY} has unknown parameter {k!r} for {provider}: use {', '.join(allowed)}")
            continue
        span = allowed[k]
        if span and not span[0] <= v <= span[1]:
            out.append(f"{KEY}.{k} must be from {span[0]:g} to {span[1]:g} for {provider}, got {v!r}")
    return out


def as_args(params: dict | None) -> list[str]:
    """The runner's `--param KEY=VALUE` arguments ([] for none)."""
    out: list[str] = []
    for k, v in (params or {}).items():
        out += ["--param", f"{k}={float(v)!r}"]
    return out
