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

from bsa.image_checks import EDGE_LEVEL, edge_share  # noqa: F401 - EDGE_LEVEL moved there; kept importable here
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
TEXT_EDGE_SHARE = 0.004  # a covered zone with more of its pixels on edges than this probably has text in it
OWN_KEYS = ("image", "seconds", "voice_line", "website")
BACKGROUND = "#14161A"  # the made card's background without a brand background
ACCENT = "#FFC800"  # without a brand accent
GAP_PX = 36  # between the made card's rows
TOP_PX = 640  # the rows' top: in the upper half, far from the covered bottom
KIT_TEXT_PAD = 8  # the kit's plain-text card's padding round its text
DARK, LIGHT = (0.08, 0.086, 0.1, 1.0), (1.0, 1.0, 1.0, 1.0)


def on_colour(rgba) -> tuple:
    """Dark or white text, whichever reads better on `rgba` (0..1 floats)."""
    return DARK if 0.2126 * rgba[0] + 0.7152 * rgba[1] + 0.0722 * rgba[2] > 0.5 else LIGHT


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


def card_seconds(cfg: dict | None, take_s: float | None, fps: int) -> float | None:
    """How long the card is really held (closing.seconds, or longer when the closing line needs it), in seconds;
    None without a card."""
    end = plan(cfg, 1, fps, take_s)
    return round(end["n_frames"] / fps, 2) if end else None


def stretch_warning(cfg: dict | None, take_s: float | None, fps: int) -> str | None:
    """The warning when the closing line holds the card longer than closing.seconds; None otherwise."""
    end = plan(cfg, 1, fps, take_s)
    if not end or end["n_frames"] <= math.ceil(round(cfg["seconds"] * fps, 6)):
        return None
    held = end["n_frames"] / fps
    return (f"the closing card will hold {held:.1f} s, not {cfg['seconds']:g} s: shorten the line or raise "
            "closing.seconds")  # fmt: skip


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


def kit_layout(cfg: dict, kit, font) -> list[tuple]:
    """The made card's rows as the kit's cards, top to bottom from TOP_PX, GAP_PX apart, each centred on the frame
    and no wider than the clear part of the frame: [(CardSpec, Rect)]. The logo badge first (when the kit has a
    readable logo), then as card_rows says: "More at" as plain text (the text colour, no box), the website as a pill
    on the accent (text that reads on it, shrunk to fit one line) and the @handle as a label pill."""
    from bsa import cards
    from bsa.layout import Rect
    from bsa.overlays import hex_colour

    maxw = 2 * (W * (1 - COVERED_RIGHT) - W / 2) - 40
    room = maxw - 2 * cards.PILL_PAD[0]
    specs = []
    if kit.logo:
        badge = cards.logo_badge(kit, font, kit.logo.get("name"))
        if badge is not None and badge.w > maxw:  # too wide with its name: the logo alone
            badge = cards.logo_badge(kit, font, None)
        if badge is not None and badge.w <= maxw:
            specs.append(badge)
    for text, kind in card_rows(cfg):
        if kind == "lead":
            specs.append(_plain_text(kit, text, font, cards.SIZES["lead"], maxw))
            continue
        pill_kind, fill, colour = (("category", kit.palette["accent"],
                                    cards.hex_of(on_colour(hex_colour(kit.palette["accent"]))))
                                   if kind == "pill" else ("label", None, None))  # fmt: skip
        used, _ = cards.font_for(text, font)
        size = cards.fit(text, cards.SIZES["website" if kind == "pill" else "handle"], used, room)
        while True:
            spec = cards.pill(kit, pill_kind, text, font, fill=fill, colour=colour, wrap=room, size=size)
            if spec.w <= maxw or size <= 1:
                break
            size -= 1
        specs.append(spec)
    rows, y = [], float(TOP_PX)
    for spec in specs:
        rows.append((spec, Rect(W / 2 - spec.w / 2, y, spec.w, spec.h)))
        y += spec.h + GAP_PX
    return rows


def _plain_text(kit, text: str, font, size: int, maxw: float):
    """A card of plain text in the kit's text colour: no fill, no outline, no shadow; `size` shrunk to fit `maxw`."""
    from bsa import cards

    used, _ = cards.font_for(text, font)
    size = cards.fit(text, size, used, maxw - 2 * KIT_TEXT_PAD)
    w = math.ceil(cards.measure(text, size, used)) + 2 * KIT_TEXT_PAD
    h = math.ceil(cards.text_height(1, size, used))
    run = cards.Run(text, size, kit.palette["text"], w / 2, h / 2, "CENTER")
    return cards.CardSpec("text", w, h, h, None, None, 0, 0, 0.0, (run,), str(used) if used else None)


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
    return edge_share(lum)


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
