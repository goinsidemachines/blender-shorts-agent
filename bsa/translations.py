"""Translated subtitles: a short's `captions_translations` ({lang: {"lines": {line id: text}, "closing": text}}),
checked line by line, and stamped so a line whose English changed shows its translation is out of date.

None = nobody asked yet; {} = asked, none wanted. Claude writes the translations (no download, no key)."""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path

LANG = re.compile(r"^[a-z]{2,3}(-[A-Za-z0-9]{2,8})*$")  # BCP-47 shape: es, pt-BR, zh-Hans
RTL = ("ar", "fa", "he", "ur", "ps", "yi")  # right-to-left languages: accepted, noted on the page
STAMPS = "review/translations.json"  # {lang: {line id: {"en": English text, "text": translated text}}}

# A digit run (any script's digits) with `.` `,` and the Arabic decimal/thousands marks between its digits.
_NUMBER = re.compile(r"\d+(?:[.,٫٬]\d+)*")


def numbers(text: str) -> list[str]:
    """Every number in `text` as plain ASCII digits with its separators removed: "1,000" and "1.000" -> "1000",
    "3.5" and "3,5" -> "35", Arabic-Indic or Devanagari digits -> ASCII."""
    out = []
    for m in _NUMBER.findall(str(text)):
        out.append("".join(str(unicodedata.decimal(ch)) for ch in m if ch.isdigit()))
    return out


def is_rtl(lang: str) -> bool:
    return str(lang).split("-")[0].lower() in RTL


def _text_ok(v) -> bool:
    return isinstance(v, str) and bool(v.strip())


def _missing(source: str, translated: str) -> list[str]:
    have = set(numbers(translated))
    seen: list[str] = []
    for n in numbers(source):
        if n not in have and n not in seen:
            seen.append(n)
    return seen


def problems(short, settings: dict) -> tuple[list[str], list[str]]:
    """(errors, warnings) for the short's translations."""
    data = getattr(short, "captions_translations", None)
    if data is None:
        return [], []
    if not isinstance(data, dict):
        return [f"captions_translations must map language codes to their subtitles, got {data!r}"], []
    from bsa import closing

    errors: list[str] = []
    warnings: list[str] = []
    ids = [ln.id for ln in short.lines]
    card = closing.resolve(settings or {}, short)
    voice = card.get("voice_line") if card else None
    for lang, entry in data.items():
        if not isinstance(lang, str) or not LANG.match(lang):
            errors.append(f"captions_translations language {lang!r} isn't a language code (es, pt-BR, ar...)")
            continue
        if not isinstance(entry, dict) or not isinstance(entry.get("lines"), dict):
            errors.append(f'captions_translations {lang} must be {{"lines": {{line id: text}}, "closing": text}}')
            continue
        extra = sorted(set(entry) - {"lines", "closing"})
        if extra:
            errors.append(f"captions_translations {lang} has unknown keys {extra}: use lines, closing")
        got = entry["lines"]
        for line in short.lines:
            t = got.get(line.id)
            if not _text_ok(t):
                errors.append(f"line {line.id} has no {lang} translation")
                continue
            for n in _missing(line.text, t):
                errors.append(f"line {line.id}'s {lang} translation leaves out the number {n}")
        for lid in got:
            if lid not in ids:
                errors.append(f"{lang} translates line {lid}, which the short doesn't have")
        c = entry.get("closing")
        if c is not None and not _text_ok(c):
            errors.append(f"{lang} closing translation must be text, got {c!r}")
        elif c is not None and not voice:
            errors.append(f"{lang} has a closing translation but the short has no closing voice line")
        elif c is not None:
            for n in _missing(voice, c):
                errors.append(f"the {lang} closing translation leaves out the number {n}")
        elif voice:
            warnings.append(f"the closing line has no {lang} translation")
    return errors, warnings


def _path(sdir: Path) -> Path:
    return Path(sdir) / STAMPS


def _stale(stamps: dict, short) -> dict[str, list[str]]:
    data = getattr(short, "captions_translations", None)
    if not isinstance(data, dict):
        return {}
    out: dict[str, list[str]] = {}
    for lang, entry in data.items():
        got = entry.get("lines") if isinstance(entry, dict) else None
        old = stamps.get(lang) if isinstance(stamps.get(lang), dict) else {}
        if not isinstance(got, dict):
            continue
        bad = []
        for line in short.lines:
            st = old.get(line.id)
            if isinstance(st, dict) and st.get("en") != line.text and st.get("text") == got.get(line.id):
                bad.append(line.id)
        if bad:
            out[lang] = bad
    return out


def _read(sdir: Path) -> dict:
    from bsa.review import store

    d = store.read_json(_path(sdir), {}, dict)
    return d if isinstance(d, dict) else {}


def stale(sdir: Path, short) -> dict[str, list[str]]:
    """Per language the line ids whose English changed since their translation was stamped (and the translation
    didn't)."""
    return _stale(_read(sdir), short)


def stamp(sdir: Path, short) -> dict[str, list[str]]:
    """Record the current (English, translation) pair of every line that isn't stale, and return `stale`."""
    from bsa.review import store

    data = getattr(short, "captions_translations", None)
    if not isinstance(data, dict) or not data:
        return {}
    result: dict[str, list[str]] = {}

    def apply(stamps):
        stamps = stamps if isinstance(stamps, dict) else {}
        result.update(_stale(stamps, short))
        for lang, entry in data.items():
            got = entry.get("lines") if isinstance(entry, dict) else None
            if not isinstance(got, dict):
                continue
            mine = dict(stamps.get(lang) or {})
            for line in short.lines:
                t = got.get(line.id)
                if _text_ok(t) and line.id not in result.get(lang, []):
                    mine[line.id] = {"en": line.text, "text": t}
            stamps[lang] = mine
        return stamps

    _path(sdir).parent.mkdir(parents=True, exist_ok=True)
    store.update_json(_path(sdir), {}, apply, dict)
    return result
