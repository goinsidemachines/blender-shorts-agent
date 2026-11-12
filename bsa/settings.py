"""User settings: ~/.blender-shorts-agent/settings.json (or $BSA_HOME/settings.json)."""

from __future__ import annotations

import copy
import json
import os
import re
import shlex
import time
from pathlib import Path

DEFAULTS = {
    "workspace": None,
    "blender_exe": None,
    "narrator": {"provider": None, "voice": None, "speed": 1.0, "command": None, "label": None,
                 "take_mode": "line", "lead_in": 0.0, "params": {}},
    "render": {"engine": "ask"},
    "brand": {"mode": None},
    "handle": None,
    "end_card": "Follow for more",  # the line above the @handle on the end card; "" = no line
    "website": None,  # the user's site, said on the closing card (bsa.closing)
    # The still every short ends on (bsa.closing): the user's own 1080x1920 image, or none for one made from the brand
    # with the website and @handle; held `seconds`, with `voice_line` narrated over it.
    "closing": {"image": None, "seconds": 2.5, "voice_line": None},
    "closing_asked": False,  # the closing-card question was answered (asked once, existing users too)
    "sound_policy": "any",  # "natural-only": no synthetic sfx kinds and no synthesis, recordings only
    # The mix (bsa.audio.mix): `room_db` is the room tone floor in LUFS-ish (null = none; it plays only when a `room`
    # recording is cached), `reverb` puts the effects through one shared room.
    "sound": {"room_db": -50.0, "reverb": True},
    "freesound": {"api_key": None},  # optional: lets `assets search sounds` include Freesound's CC0 sounds
    "rules": [],  # the user's hard rules for every video, in their own words (bsa.rules; CLI settings rule add)
    "rules_asked": False,  # the hard-rules question was answered (asked once, existing users too)
    "feedback": "ask",  # problems in the plugin itself (bsa.feedback): "ask" at the end, "never" ask, or "off"
    "post_format": {},  # the user's own post format (bsa.post.FORMAT_KEYS)
    "post_format_asked": False,  # the post-format question was answered
    "page": {"lan": False},  # phone access: the review page server also answers on the local network (token-protected)
}
SECRET_KEYS = ("freesound.api_key",)

# Settings that take one of a few words (stored lowercase). Narrators: none (captions only), ElevenLabs, or a local
# voice engine (bsa.narrate.run.LOCAL_ENGINES), or custom (the user's own voice tool, narrator.command).
# "ask-always": ask for the engine for every short.
ENUMS = {
    "narrator.provider": ("none", "elevenlabs", "kokoro", "chatterbox", "chatterbox-turbo", "custom"),
    "narrator.take_mode": ("line", "paragraph"),  # one take per line, or one take for the whole story
    "render.engine": ("cycles", "eevee", "ask-always"),
    "brand.mode": ("brand", "fresh"),
    "sound_policy": ("any", "natural-only"),
    "feedback": ("ask", "never", "off"),
}

# narrator.lead_in: seconds of quiet before the voice starts in each take.
LEAD_IN_RANGE_S = (0.0, 1.0)


def lead_in_ok(seconds: float) -> bool:
    """A lead-in in range (settings and a short's own narrator share this check)."""
    return LEAD_IN_RANGE_S[0] <= seconds <= LEAD_IN_RANGE_S[1]


def lead_in_error(value) -> str:
    return f"narrator.lead_in must be seconds from {LEAD_IN_RANGE_S[0]:g} to {LEAD_IN_RANGE_S[1]:g}, got {value!r}"


# narrator.params: the voice engine's own parameters (bsa.narrate.params), given to it last; {} = its defaults.


def narrator_problems(s: dict) -> list[str]:
    """Why the narrator settings can't be used as they are: engine params the provider doesn't take."""
    from bsa.narrate import params

    narrator = s.get("narrator") if isinstance(s.get("narrator"), dict) else {}
    return params.problems(narrator.get("provider"), narrator.get("params"))


# Settings that are true or false (JSON booleans on the command line).
BOOLEANS = ("page.lan", "post_format_asked", "sound.reverb")

# sound.room_db: the room tone floor under the story, in LUFS-ish; null turns it off.
ROOM_DB_RANGE = (-80.0, -20.0)


def room_db_error(value) -> str:
    return (f"sound.room_db must be a level from {ROOM_DB_RANGE[0]:g} to {ROOM_DB_RANGE[1]:g} (LUFS-ish, about -50 is "
            f"a quiet room) or null for no room tone, got {value!r}")


def room_db(s: dict) -> float | None:
    """The room tone level to mix at: the `sound.room_db` setting (None = off); the default for a missing or
    unusable value."""
    sound = s.get("sound") if isinstance(s.get("sound"), dict) else {}
    if "room_db" not in sound:
        return DEFAULTS["sound"]["room_db"]
    v = sound["room_db"]
    if v is None:
        return None
    ok = isinstance(v, (int, float)) and not isinstance(v, bool) and ROOM_DB_RANGE[0] <= v <= ROOM_DB_RANGE[1]
    return float(v) if ok else DEFAULTS["sound"]["room_db"]


def reverb_on(s: dict) -> bool:
    """The `sound.reverb` setting (default on)."""
    sound = s.get("sound") if isinstance(s.get("sound"), dict) else {}
    v = sound.get("reverb", DEFAULTS["sound"]["reverb"])
    return v if isinstance(v, bool) else DEFAULTS["sound"]["reverb"]


_PROVIDER_NAMES = {
    "elevenlabs": "ElevenLabs",
    "kokoro": "Kokoro",
    "chatterbox": "Chatterbox",
    "chatterbox-turbo": "Chatterbox Turbo",
}
_BASENAME = re.compile(r"[/\\]")
_INTERPRETERS = {"python", "python3", "pythonw", "py", "uv", "uvx", "node", "bun", "deno", "pwsh", "powershell", "bash",
                 "sh", "cmd", "ruby", "perl"}  # fmt: skip
_RUNNER_WORDS = {"run", "exec"}  # "uv run tool.py"
_SCRIPT_EXT = re.compile(r"\.(py|pyw|js|mjs|ts|ps1|sh|bat|cmd|rb|pl)$", re.IGNORECASE)
_ENGINE_NAMES = {"cycles": "Cycles", "eevee": "EEVEE"}


def split_command(command: str) -> list[str]:
    """A command line as a list of arguments, without a shell. Quoted paths with spaces stay one argument; on
    Windows backslashes are left alone (shlex posix=False keeps the quotes, which are stripped here)."""
    parts = shlex.split(command, posix=os.name != "nt")
    if os.name == "nt":
        parts = [p[1:-1] if len(p) >= 2 and p[0] == p[-1] and p[0] in "\"'" else p for p in parts]
    return parts


def bsa_home() -> Path:
    env = os.environ.get("BSA_HOME")
    return Path(env) if env else Path.home() / ".blender-shorts-agent"


def settings_path() -> Path:
    return bsa_home() / "settings.json"


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load_settings() -> dict:
    path = settings_path()
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return copy.deepcopy(DEFAULTS)
    try:
        data = json.loads(text)
        if not isinstance(data, dict):
            raise TypeError("settings must be a JSON object")
    except (ValueError, TypeError):
        stamp = time.strftime("%Y%m%d-%H%M%S")
        os.replace(path, path.with_name(f"settings.corrupt-{stamp}.json"))
        return copy.deepcopy(DEFAULTS)
    return _merge(DEFAULTS, data)


def redacted(s: dict) -> dict:
    """The settings with secrets (API keys) shown as "set" instead of their value, for printing."""
    out = copy.deepcopy(s)
    for path in SECRET_KEYS:
        *parents, last = path.split(".")
        node = out
        for k in parents:
            node = node.get(k) if isinstance(node, dict) else None
        if isinstance(node, dict) and node.get(last):
            node[last] = "set"
    return out


def freesound_key(s: dict | None = None) -> str | None:
    """The Freesound API key: $FREESOUND_API_KEY, else the freesound.api_key setting, else None."""
    env = os.environ.get("FREESOUND_API_KEY", "").strip()
    if env:
        return env
    s = load_settings() if s is None else s
    return str((s.get("freesound") or {}).get("api_key") or "").strip() or None


def save_settings(s: dict) -> None:
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(s, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def check_value(path: str, value):
    """`value` as stored: a choice setting is matched case-insensitively and stored lowercase; ValueError for a
    value that is not one of its choices."""
    if path == "brand.exposure":  # stops, as Blender's view exposure (bsa.blender.look.brand_view)
        try:
            return float(value)
        except (TypeError, ValueError):
            raise ValueError(f"{value!r} is not a valid brand.exposure: use a number of stops, e.g. 0.3") from None
    if path == "brand":  # the whole object: every key checked as its own brand.KEY (set_value merges it in)
        if not isinstance(value, dict):
            raise ValueError(f"brand must be an object of brand settings, got {value!r}")
        return {k: check_value(f"brand.{k}", v) for k, v in value.items()}
    if path.startswith("brand.") and path not in ("brand.mode", "brand.exposure"):
        from bsa import kit

        return kit.check(path[6:], value)
    if path == "post_format" or path.startswith("post_format."):
        from bsa import post

        if path == "post_format":
            return post.format_value(value)
        key = path.split(".", 1)[1]
        return post.format_value({key: value})[key]
    if path == "narrator.params" or path.startswith("narrator.params."):
        from bsa.narrate import params

        if path == "narrator.params":
            wrong = params.shape_problems(value)
        elif value is None:  # null removes the key
            return None
        else:
            wrong = params.shape_problems({path.split(".", 2)[2]: value})
        if wrong:
            raise ValueError("; ".join(wrong))
        return {k: float(v) for k, v in value.items()} if path == "narrator.params" else float(value)
    if path == "narrator.lead_in":
        seconds = None
        if not isinstance(value, bool):
            try:
                seconds = float(value)
            except (TypeError, ValueError):
                pass
        if seconds is None or not lead_in_ok(seconds):
            raise ValueError(lead_in_error(value))
        return seconds
    if path == "sound":
        if not isinstance(value, dict) or set(value) - set(DEFAULTS["sound"]):
            raise ValueError(f"sound must be an object with {' and '.join(DEFAULTS['sound'])}, got {value!r}")
        return {k: check_value(f"sound.{k}", v) for k, v in value.items()}
    if path == "sound.room_db":
        if value is None:
            return None
        level = None
        if not isinstance(value, bool):
            try:
                level = float(value)
            except (TypeError, ValueError):
                pass
        if level is None or not ROOM_DB_RANGE[0] <= level <= ROOM_DB_RANGE[1]:
            raise ValueError(room_db_error(value))
        return level
    if path in BOOLEANS:
        if not isinstance(value, bool):
            raise ValueError(f"{value!r} is not a valid {path}: use true or false")
        return value
    choices = ENUMS.get(path)
    if choices is None:
        return value
    word = str(value).strip().lower()
    if word not in choices:
        raise ValueError(f"{value!r} is not a valid {path}: use one of {', '.join(choices)}")
    return word


def set_value(path: str, value) -> dict:
    """Store `value` at `path`. A whole `brand` object merges into the stored brand key by key (the setup flow sets
    brand.mode first, then the sampled kit: the kit never drops mode, exposure or a note it does not name)."""
    s = load_settings()
    if path == "brand" and isinstance(value, dict):  # a null value deletes that key (brand.style null: no kit)
        old = s.get("brand") if isinstance(s.get("brand"), dict) else {}
        value = {k: v for k, v in {**old, **value}.items() if v is not None}
    node = s
    keys = path.split(".")
    for k in keys[:-1]:
        if not isinstance(node.get(k), dict):
            node[k] = {}
        node = node[k]
    if value is None and path.startswith(("brand.", "narrator.params.")):  # brand.style none, brand.logo null,
        # narrator.params.temperature null: the key is removed
        node.pop(keys[-1], None)
    else:
        node[keys[-1]] = value
    save_settings(s)
    return s


def _text(value) -> str | None:
    text = str(value).strip() if isinstance(value, str) else ""
    return text or None


def end_card(s: dict, short=None) -> dict:
    """What the end card shows: {"handle", "text", "second"} (each None when absent). The short's own `end_card`
    ("line", or {"text", "second"}) wins over the `end_card` setting. With no handle and no card of the short's own
    there is no end card at all (every value None): the setting's line never shows on its own. A short that ends on a
    closing card (bsa.closing) has no end card: the card says it."""
    from bsa import closing

    if closing.active(s, short):
        return {"handle": None, "text": None, "second": None}
    handle = str(s.get("handle") or "").strip().lstrip("@") or None
    own = getattr(short, "end_card", None)
    if own is not None:
        card = own if isinstance(own, dict) else {"text": own}
        return {"handle": handle, "text": _text(card.get("text")), "second": _text(card.get("second"))}
    if handle is None:
        return {"handle": None, "text": None, "second": None}
    return {"handle": handle, "text": _text(s.get("end_card", DEFAULTS["end_card"])), "second": None}


def look_mode(s: dict, short=None) -> str | None:
    """The look a short follows: its own `look` ("fresh": its own `style`, the brand off for this short; "brand":
    the brand), else the `brand.mode` setting ("brand", "fresh", or None when not chosen yet)."""
    own = getattr(short, "look", None)
    if own in ("fresh", "brand"):
        return own
    return ((s or {}).get("brand") or {}).get("mode")


def brand_for(s: dict, short=None) -> dict:
    """The `brand` settings that apply to a short: none ({}) when its look is fresh (its own `"look": "fresh"`, or
    `brand.mode` fresh and the short not `"look": "brand"`), so its background check, captions and view follow only
    its own `style`. Without a short: the brand as set, unless `brand.mode` is fresh."""
    if look_mode(s, short) == "fresh":
        return {}
    brand = (s or {}).get("brand")
    return brand if isinstance(brand, dict) else {}


def has_end_card(s: dict, short=None) -> bool:
    """An end card is shown (the user's @handle, or the short's own card): then the timeline holds the last shot
    END_CARD_S longer."""
    return any(end_card(s, short).values())


def missing(s: dict) -> list[str]:
    out = []
    if not s.get("workspace"):
        out.append("workspace")
    narrator = s.get("narrator") or {}
    provider = narrator.get("provider")
    if provider is None or (provider == "custom" and not str(narrator.get("command") or "").strip()):
        out.append("narrator.provider")
    if (s.get("render") or {}).get("engine", "ask") == "ask":
        out.append("render.engine")
    if (s.get("brand") or {}).get("mode") is None:
        out.append("brand.mode")
    if not s.get("rules_asked") and not s.get("rules"):
        out.append("rules")
    if s.get("handle") is None:  # never asked; "" means asked and the user wants no handle
        out.append("handle")
    closing = s.get("closing") if isinstance(s.get("closing"), dict) else {}
    if not s.get("closing_asked") and not s.get("website") and not closing.get("image"):
        out.append("closing")
    return out


def _name(path: str) -> str:
    """A program or script path as a short name: the base name without .exe or a script extension."""
    base = _BASENAME.split(path)[-1]
    return _SCRIPT_EXT.sub("", re.sub(r"\.exe$", "", base, flags=re.IGNORECASE))


def _option(parts: list[str], flag: str) -> str | None:
    for i, part in enumerate(parts):
        if part == flag and i + 1 < len(parts):
            return parts[i + 1]
        if part.startswith(flag + "="):
            return part.split("=", 1)[1]
    return None


def custom_label(narrator: dict) -> str:
    """What the user's own voice tool is called in the summary: narrator.label when set; else "model · voice" from
    the command's --model and voice ({voice} or --voice); else the script or module it runs, never the interpreter."""
    label = str(narrator.get("label") or "").strip()
    if label:
        return label
    try:
        parts = split_command(str(narrator.get("command") or ""))
    except ValueError:
        parts = []
    if not parts:
        return "no command yet"
    voice = _option(parts, "--voice")
    if voice is None or "{voice}" in voice:
        voice = narrator.get("voice") if any("{voice}" in p for p in parts) else None
    shown = [str(x) for x in (_option(parts, "--model"), voice) if x]
    if shown:
        return " · ".join(shown)
    module = _option(parts, "-m")
    if module:
        return module
    script = next((p for p in parts[1:] if _SCRIPT_EXT.search(p)), None)
    if script:
        return _name(script)
    if _name(parts[0]).lower() in _INTERPRETERS:  # "uv run tool": the first plain word after the interpreter
        rest = [p for p in parts[1:] if not p.startswith("-") and "{" not in p and p not in _RUNNER_WORDS]
        if rest:
            return _name(rest[0])
    return _name(parts[0])


def summary_line(s: dict) -> str:
    n = s.get("narrator") or {}
    provider = n.get("provider")
    if provider is None:
        narrator = "narrator not set"
    elif provider == "none":
        narrator = "captions only"
    elif provider == "custom":
        narrator = f"your voice tool ({custom_label(n)})"
    else:
        name = _PROVIDER_NAMES.get(provider, provider)
        parts = [str(n["voice"])] if n.get("voice") else []
        parts.append(f"{float(n.get('speed', 1.0)):.1f}×")
        narrator = f"{name} ({', '.join(parts)})"
    engine_word = (s.get("render") or {}).get("engine")
    engine = _ENGINE_NAMES.get(engine_word) or ("engine: ask each time" if engine_word == "ask-always" else "engine not set")
    handle = s.get("handle")
    handle = "handle not set" if handle is None else f"@{handle}" if handle else "no handle"
    workspace = s.get("workspace") or "workspace not set"
    from bsa import closing

    card = closing.resolve(s)
    ends = "" if card is None else " · ends on your card" if card["image"] else f" · ends on {card['website']}"
    line = f"{narrator} · {engine} · {handle}{ends} · saving to {workspace}"
    n = len([r for r in (s.get("rules") or []) if isinstance(r, dict)])
    return line + (f" · {n} hard rule{'s' if n != 1 else ''}" if n else "")
