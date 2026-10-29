"""The closing card: the still every final short ends on, held for a few seconds, with an optional closing line
narrated over it (`website` and `closing` settings; a short's own `closing` in short.json).

The card comes after the story's timeline (bsa.timeline is the story; the card is added by the mix and the edit), so
turning it on or off never makes the rendered shots stale. The card is the user's own image (`closing.image`), or one
made from the brand settings with the website and the @handle when only those are set. With a closing card the
overlay end card is not shown: the card says it once.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np

from bsa.layout import H, W

LINE_ID = "closing"  # the closing line's take: audio/vo/closing.wav, made with the story's lines
SECONDS = 2.5  # default hold
MIN_S, MAX_S = 1.5, 5.0
LEAD_S = 0.2  # the closing line starts this long after the card appears
PAUSE_S = 0.5  # the card is held at least this long after the closing line ends
COVERED_BOTTOM = 0.18  # YouTube's title, channel and description cover the bottom ~18% of a Short
COVERED_RIGHT = 0.12  # and its like / comment / share buttons the right ~12%
CARD_FILE = "closing.png"  # the card as the video shows it: <short>/stills/closing.png
INFO_FILE = "closing.json"  # what made it and its warnings: <short>/stills/closing.json
EDGE_LEVEL = 48  # a luminance step (0-255) between neighbouring pixels this big is an edge, as text has
TEXT_EDGE_SHARE = 0.004  # a covered zone with more of its pixels on edges than this probably has text in it
OWN_KEYS = ("image", "seconds", "voice_line", "website")


def _text(value) -> str | None:
    text = str(value).strip() if isinstance(value, str | int | float) and not isinstance(value, bool) else ""
    return text or None


def clamp_seconds(value) -> float:
    try:
        secs = float(value)
    except (TypeError, ValueError):
        return SECONDS
    return SECONDS if math.isnan(secs) else min(max(secs, MIN_S), MAX_S)


def resolve(s: dict, short=None) -> dict | None:
    """The closing card a short ends on, or None. {"image": path or None (None: make one from the brand),
    "website", "handle", "seconds" (clamped to MIN_S..MAX_S), "voice_line"}. The short's own `closing` wins: false
    turns it off, an object overrides image, seconds, voice_line or website (an "image" of null makes the brand card
    instead of the user's image). With no image and no website there is no card: a user with only a handle keeps the
    overlay end card (bsa.settings.end_card), as before."""
    s = s or {}
    own = getattr(short, "closing", None)
    if own is False:
        return None
    base = s.get("closing") if isinstance(s.get("closing"), dict) else {}
    cfg = {"image": base.get("image"), "seconds": base.get("seconds"), "voice_line": base.get("voice_line"),
           "website": s.get("website")}  # fmt: skip
    if isinstance(own, dict):
        cfg.update({k: own[k] for k in OWN_KEYS if k in own})
    handle = str(s.get("handle") or "").strip().lstrip("@") or None
    out = {
        "image": _text(cfg["image"]),
        "website": _text(cfg["website"]),
        "handle": handle,
        "seconds": clamp_seconds(cfg["seconds"] if cfg["seconds"] is not None else SECONDS),
        "voice_line": _text(cfg["voice_line"]),
    }
    if not (out["image"] or out["website"]):
        return None
    return out


def active(s: dict, short=None) -> bool:
    return resolve(s, short) is not None


def problems(short) -> list[str]:
    """What is wrong with a short's own `closing` (validate)."""
    own = getattr(short, "closing", None)
    if own is None or isinstance(own, bool):
        return []
    if not isinstance(own, dict):
        return [(f"closing must be false (no closing card), true or {{\"image\", \"seconds\", \"voice_line\", "
                 f"\"website\"}}, got {own!r}")]  # fmt: skip
    errors = []
    extra = sorted(set(own) - set(OWN_KEYS))
    if extra:
        errors.append(f"closing has unknown keys {extra}: use {', '.join(OWN_KEYS)}")
    for key in ("image", "voice_line", "website"):
        if own.get(key) is not None and not isinstance(own.get(key), str):
            errors.append(f"closing {key} must be text, got {own.get(key)!r}")
    secs = own.get("seconds")
    if secs is not None and (isinstance(secs, bool) or not isinstance(secs, int | float)):
        errors.append(f"closing seconds must be a number, got {secs!r}")
    elif secs is not None and not MIN_S <= secs <= MAX_S:
        errors.append(f"closing seconds must be between {MIN_S:g} and {MAX_S:g}, got {secs!r}")
    return errors


def voice_line(cfg: dict | None, short):
    """The closing line as a Line for the narrator (spoken with the story's lines, same voice), or None."""
    from bsa.short import Line

    if not cfg or not cfg.get("voice_line") or not short.shots:
        return None
    return Line(LINE_ID, cfg["voice_line"], short.shots[-1].id)


def with_voice_line(short, cfg: dict | None):
    """The short with its closing line added to its lines, for narrating; the short itself when there is none."""
    import dataclasses

    line = voice_line(cfg, short)
    return dataclasses.replace(short, lines=[*short.lines, line]) if line else short


def take(takes_dir: Path | None) -> Path | None:
    """The closing line's take when it has been narrated."""
    if takes_dir is None:
        return None
    path = Path(takes_dir) / f"{LINE_ID}.wav"
    return path if path.is_file() and path.stat().st_size > 0 else None


def plan(cfg: dict | None, start: int, fps: int, take_s: float | None) -> dict | None:
    """Where the card sits: {"f0", "f1", "n_frames", "line_f0" (None without a take)} from frame `start` (the frame
    after the story). Held `seconds`, or as long as the closing line needs (LEAD_S + the take + PAUSE_S)."""
    if not cfg:
        return None
    secs = cfg["seconds"]
    if take_s:
        secs = max(secs, LEAD_S + take_s + PAUSE_S)
    n = math.ceil(round(secs * fps, 6))
    return {"f0": start, "f1": start + n - 1, "n_frames": n,
            "line_f0": start + round(LEAD_S * fps) if take_s else None}  # fmt: skip


def hold(cfg: dict, end: dict, fps: int) -> dict:
    """How long the card is held: {"seconds" (what plan gave), "set_seconds" (closing.seconds)}, plus a "note" when
    the closing line made it longer than closing.seconds (closing.seconds is a minimum, never a cut-off). The mix and
    the edit report it, and the final page shows it."""
    held = round(end["n_frames"] / fps, 2)
    out = {"seconds": held, "set_seconds": cfg["seconds"]}
    if end["n_frames"] > math.ceil(round(cfg["seconds"] * fps, 6)):
        take_s = max(0.0, held - LEAD_S - PAUSE_S)
        out["note"] = (f"closing card held {held:.1f} s so the voice line fits (its take is {take_s:.1f} s; "
                       f"closing.seconds is {cfg['seconds']:g})")  # fmt: skip
    return out


def take_seconds(takes_dir: Path | None) -> float | None:
    path = take(takes_dir)
    if path is None:
        return None
    from bsa.narrate.contract import wav_seconds

    return wav_seconds(path)


def card_rows(cfg: dict) -> list[tuple[str, str]]:
    """The made card's text, top to bottom, as (text, kind): kind "lead" (a small line), "pill" (the website, on the
    accent colour) or "handle"."""
    rows = [("More at", "lead"), (cfg["website"], "pill")] if cfg.get("website") else []
    if rows and cfg.get("handle"):
        rows.append(("@" + cfg["handle"], "handle"))
    return rows


def places(cfg: dict | None) -> list[tuple[str, str]]:
    """(where, text) the hard-rule word checks read: what the card says and the line said over it."""
    if not cfg:
        return []
    words = [cfg.get("voice_line") or "", cfg.get("website") or ""]
    if cfg.get("image"):
        words.append(Path(cfg["image"]).stem.replace("-", " ").replace("_", " "))
    return [("closing card", " ".join(w for w in words if w))]


def _luma(px: np.ndarray) -> np.ndarray:
    rgb = px[..., :3].astype(np.float64)
    return rgb @ np.array([0.2126, 0.7152, 0.0722])


def _edge_share(lum: np.ndarray) -> float:
    if lum.shape[0] < 2 or lum.shape[1] < 2:
        return 0.0
    gx = np.abs(np.diff(lum, axis=1))[:-1, :]
    gy = np.abs(np.diff(lum, axis=0))[:, :-1]
    return float(np.mean(np.maximum(gx, gy) > EDGE_LEVEL))


def zone_warnings(px: np.ndarray) -> list[str]:
    """Warnings when the card (a (height, width, 3 or 4) uint8 frame, top row first) has detail like text in the
    zones YouTube covers on a Short: the bottom COVERED_BOTTOM and the right COVERED_RIGHT of the frame."""
    lum = _luma(px)
    h, w = lum.shape
    top_of_bottom, right_edge = round(h * (1 - COVERED_BOTTOM)), round(w * (1 - COVERED_RIGHT))
    bottom = lum[top_of_bottom:, :]
    right = lum[:top_of_bottom, right_edge:]
    out = []
    if _edge_share(bottom) > TEXT_EDGE_SHARE:
        out.append(f"the closing card has text or detail in its bottom {COVERED_BOTTOM:.0%}, where YouTube's title "
                   "and channel name cover it: move it up")  # fmt: skip
    if _edge_share(right) > TEXT_EDGE_SHARE:
        out.append(f"the closing card has text or detail in its right {COVERED_RIGHT:.0%}, where YouTube's like, "
                   "comment and share buttons cover it: move it left")  # fmt: skip
    return out


def aspect_warning(width: int, height: int) -> str | None:
    if not width or not height or abs(width / height - W / H) <= 0.01:
        return None
    return (f"the closing image is {width}x{height}, not 9:16: it is scaled to fill {W}x{H}, so its edges are cut "
            "off; make it 1080x1920")  # fmt: skip
