"""Caption files for the final video: SRT, VTT and a plain transcript, cue for cue with the captions the video shows.

One cue per caption card (the card's `text`, never the spoken `say`), at most MAX_LINES lines of MAX_CHARS characters;
a card that wraps longer is split into consecutive cues. The closing line, when narrated over the closing card, is the
last cue. The files are only written for a final video cut from the current timing (`state`): else they would be out
of step with it. Frames are 1-based and inclusive; frame `f` is on screen for [(f-1)/fps, f/fps).
"""

from __future__ import annotations

import json
import math
import os
import threading
from dataclasses import dataclass
from pathlib import Path

from bsa import closing as closing_mod
from bsa import engine_runs, timeline
from bsa.narrate.contract import voice_onset
from bsa.narrate.run import _read_record

MAX_CHARS = 42
MAX_LINES = 2
MIN_CUE_S = 0.5
STALE = ("The final video was cut from an older timing, so its caption files would be out of step: run the final "
         "edit again (BLENDER edit SHORT --preset final).")  # fmt: skip
NO_FINAL = "There is no final video yet: run the final edit first (BLENDER edit SHORT --preset final)."


class Stale(Exception):
    """The caption files cannot be made: there is no final edit, or it is from an older timing."""


@dataclass
class Cue:
    f0: int
    f1: int
    lines: list[str]


def wrap(text: str) -> list[str]:
    """Greedy by words, each line at most MAX_CHARS characters; a longer word is a line of its own, never cut."""
    lines: list[str] = []
    cur = ""
    for word in text.split():
        if cur and len(cur) + 1 + len(word) <= MAX_CHARS:
            cur = f"{cur} {word}"
            continue
        if cur:
            lines.append(cur)
        cur = word
    if cur:
        lines.append(cur)
    return lines


def _text(cue: Cue) -> str:
    return " ".join(cue.lines)


def _share(lines: list[str], f0: int, f1: int) -> list[Cue]:
    """A card's lines in groups of MAX_LINES, its frames shared in proportion to characters (each group at least one
    frame, contiguous). The one exception to MAX_LINES: a card with fewer frames than groups (a 1-frame card that wraps
    to 3 lines) has its groups merged until each has a frame, so its cue may hold more than MAX_LINES lines; no text
    is dropped and the cues stay contiguous without overlap."""
    groups = [lines[i : i + MAX_LINES] for i in range(0, len(lines), MAX_LINES)]
    n = f1 - f0 + 1
    if n < len(groups):
        per = math.ceil(len(lines) / max(n, 1))
        groups = [lines[i : i + per] for i in range(0, len(lines), per)]
    k = len(groups)
    total = sum(len(" ".join(g)) for g in groups)
    cues_, start, cum = [], f0, 0
    for i, g in enumerate(groups):
        cum += len(" ".join(g))
        end = f1 if i == k - 1 else f0 + round(n * cum / total) - 1
        end = min(max(end, start), f1 - (k - 1 - i))
        cues_.append(Cue(start, end, list(g)))
        start = end + 1
    return cues_


def _merge(cues_: list[Cue], n: int) -> list[Cue]:
    out = list(cues_)
    i = 0
    while i < len(out):
        c = out[i]
        have = c.f1 - c.f0 + 1
        if have >= n or len(out) < 2:
            i += 1
            continue
        need = n - have
        if i > 0:
            prev = out[i - 1]
            joined = wrap(f"{_text(prev)} {_text(c)}")
            if len(joined) <= MAX_LINES:
                out[i - 1 : i + 1] = [Cue(prev.f0, c.f1, joined)]
                continue
            if prev.f1 - prev.f0 + 1 - need >= n:
                prev.f1 -= need
                c.f0 -= need
        else:
            nxt = out[1]
            joined = wrap(f"{_text(c)} {_text(nxt)}")
            if len(joined) <= MAX_LINES:
                out[0:2] = [Cue(c.f0, nxt.f1, joined)]
                continue
            if nxt.f1 - nxt.f0 + 1 - need >= n:
                nxt.f0 += need
                c.f1 += need
        i += 1
    return out


def cues(cards: list[timeline.Card], fps: int, closing: Cue | None = None) -> list[Cue]:
    """One cue per card from its `text`; a card wrapping past MAX_LINES lines splits into consecutive cues. Cues
    under MIN_CUE_S are merged into or given frames by a neighbour. `closing` is appended last."""
    out: list[Cue] = []
    for card in cards:
        lines = wrap(card.text)
        if lines and card.f1 >= card.f0:
            out += [Cue(card.f0, card.f1, lines)] if len(lines) <= MAX_LINES else _share(lines, card.f0, card.f1)
    out = _merge(out, math.ceil(round(MIN_CUE_S * fps, 6)))
    return [*out, closing] if closing else out


def stamp(seconds: float, sep: str) -> str:
    ms = math.floor(seconds * 1000 + 0.5)
    h, rest = divmod(ms, 3_600_000)
    m, rest = divmod(rest, 60_000)
    s, ms = divmod(rest, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


def _span(cue: Cue, fps: int, sep: str) -> str:
    return f"{stamp((cue.f0 - 1) / fps, sep)} --> {stamp(cue.f1 / fps, sep)}"


def to_srt(cs: list[Cue], fps: int) -> str:
    blocks = [f"{i}\n{_span(c, fps, ',')}\n" + "\n".join(c.lines) for i, c in enumerate(cs, 1)]
    return "\n\n".join(blocks) + "\n"


def to_vtt(cs: list[Cue], fps: int) -> str:
    blocks = [f"{_span(c, fps, '.')}\n" + "\n".join(c.lines) for c in cs]
    return "WEBVTT\n\n" + "\n\n".join(blocks) + "\n"


def transcript(short, closing_line: str | None) -> str:
    """One paragraph per shot (its lines' `text` joined by a space, shots in order, shots without lines left out),
    then the closing line as its own paragraph."""
    paras = []
    for shot in short.shots:
        text = " ".join(ln.text.strip() for ln in short.lines if ln.shot == shot.id and ln.text.strip())
        if text:
            paras.append(text)
    if closing_line:
        paras.append(closing_line)
    return "\n\n".join(paras) + "\n"


def paths(sdir: Path, short, lang: str | None = None) -> dict[str, Path]:
    out = Path(sdir) / "out"
    if lang:
        return {"srt": out / f"{short.slug}.{lang}.srt", "vtt": out / f"{short.slug}.{lang}.vtt"}
    return {"srt": out / f"{short.slug}.srt", "vtt": out / f"{short.slug}.vtt",
            "transcript": out / f"{short.slug}-transcript.txt"}  # fmt: skip


def _timeline_json(sdir: Path) -> dict | None:
    try:
        data = json.loads((Path(sdir) / "source" / "timeline.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def state(sdir: Path, short) -> dict:
    """{"stale": bool, "why": str | None}: stale with no final edit on record, or one cut from another timing."""
    rec = engine_runs.edit_info(Path(sdir), "final")
    if rec is None:
        return {"stale": True, "why": NO_FINAL}
    data = _timeline_json(sdir)
    if data is None or rec.get("timeline_hash") != timeline.timeline_hash(data):
        return {"stale": True, "why": STALE}
    return {"stale": False, "why": None}


def _lead_in(sdir: Path) -> float:
    try:
        value = float(_read_record(Path(sdir) / "audio" / "vo").get("lead_in") or 0.0)
    except (TypeError, ValueError):
        return 0.0
    return value if math.isfinite(value) else 0.0


def _closing_cue(sdir: Path, short, s: dict, rec: dict, fps: int) -> tuple[Cue | None, str | None]:
    cfg = closing_mod.resolve(s, short)
    span = rec.get("closing")
    if not cfg or not cfg.get("voice_line") or not isinstance(span, dict):
        return None, None
    take_s = closing_mod.take_seconds(Path(sdir) / "audio" / "vo")
    if not take_s:
        return None, None
    line_f0 = int(span["f0"]) + round(closing_mod.LEAD_S * fps)
    line_f1 = min(line_f0 + math.ceil(round(take_s * fps, 6)) - 1, int(span["f1"]))
    if _lead_in(sdir) > 0:  # the take opens with padded silence: the words wait for the voice, the cue still ends with the take
        line_f0 += round(voice_onset(closing_mod.take(Path(sdir) / "audio" / "vo")) * fps)
    lines = wrap(cfg["voice_line"])
    if not lines or line_f1 < line_f0:
        return None, None
    return Cue(line_f0, line_f1, lines), cfg["voice_line"]


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")  # one per writer
    tmp.write_bytes(text.encode("utf-8"))
    os.replace(tmp, path)


def write_files(sdir: Path, short, s: dict) -> dict:
    """Write the SRT, VTT and transcript beside the final video. Stale (nothing written) unless the final video was
    cut from the current timing."""
    st = state(sdir, short)
    if st["stale"]:
        raise Stale(st["why"])
    tl = timeline.timeline_from_json(_timeline_json(sdir))
    rec = engine_runs.edit_info(Path(sdir), "final") or {}
    last, line = _closing_cue(sdir, short, s or {}, rec, tl.fps)
    cs = cues(tl.cards, tl.fps, last)
    where = paths(sdir, short)
    _write(where["srt"], to_srt(cs, tl.fps))
    _write(where["vtt"], to_vtt(cs, tl.fps))
    _write(where["transcript"], transcript(short, line))
    languages, skipped = _write_languages(sdir, short, s or {}, tl, last)
    return {"srt": str(where["srt"]), "vtt": str(where["vtt"]), "transcript": str(where["transcript"]),
            "cues": len(cs), "languages": languages, "skipped": skipped}  # fmt: skip


def _write_languages(sdir: Path, short, s: dict, tl, last: Cue | None) -> tuple[dict, dict]:
    """Each translated language's SRT and VTT, with the English timing. A language with problems or lines made for
    older English is skipped (its files are left as they are) and the reason said."""
    import copy

    from bsa import translations

    data = getattr(short, "captions_translations", None)
    if not isinstance(data, dict):
        return {}, {}
    old = translations.stale(short, s)
    written: dict[str, dict] = {}
    skipped: dict[str, str] = {}
    for lang, entry in data.items():
        one = copy.copy(short)
        one.captions_translations = {lang: entry}
        errors, _ = translations.problems(one, s)
        if errors:
            skipped[str(lang)] = f"{lang} subtitles have problems: {errors[0]}"
            continue
        if lang in old:
            names = ", ".join("the closing line" if b == translations.CLOSING else b for b in old[lang])
            skipped[lang] = f"{lang} subtitles for {names} were made for the old English: update them, then run again"
            continue
        cs = translations.cues(tl, short, lang, last)
        where = paths(sdir, short, lang)
        _write(where["srt"], to_srt(cs, tl.fps))
        _write(where["vtt"], to_vtt(cs, tl.fps))
        written[lang] = {"srt": str(where["srt"]), "vtt": str(where["vtt"])}
    return written, skipped
