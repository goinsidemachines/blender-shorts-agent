"""Translated subtitles: a short's `captions_translations`, checked line by line.

    {lang: {"lines": {line id: text}, "closing": text (when the short has a closing voice line),
            "from": {line id: the English that text was translated from, "closing": likewise}}}

None = nobody asked yet; {} = asked, none wanted. Claude writes the translations (no download, no key) and records the
English each one was made from in "from". A line whose current English differs from its "from" (or has none) is stale:
validate warns. When the English changes but the meaning doesn't, Claude updates "from" to the new English instead of
retranslating. Validation only reads the short; it never writes anything."""

from __future__ import annotations

import re
import unicodedata
from fractions import Fraction

LANG = re.compile(r"^[a-z]{2,3}(-[A-Za-z0-9]{2,8})*$")  # BCP-47 shape: es, pt-BR, zh-Hans
RTL = ("ar", "fa", "he", "ur", "ps", "yi", "dv", "ckb", "sd", "ug")  # right-to-left: accepted, noted on the page
BIDI_OVERRIDES = "\u202a\u202b\u202c\u202d\u202e"  # reorder text invisibly; the isolates (2066-2069) are fine
CLOSING = "closing"  # the key of the closing line in "from"

_THOUSANDS = "   "  # no-break, narrow no-break and thin space (a plain space is not one)
_SEPS = ".,٫٬" + _THOUSANDS
_SOURCE = re.compile(r"\d+(?:,\d+)*(?:\.\d+)?")  # English: "," thousands, "." decimal
_TRANSLATED = re.compile(r"\d+(?:[" + _SEPS + r"]\d+)*")


def _digits(s: str) -> str:
    return "".join(str(unicodedata.decimal(ch)) for ch in s if ch.isdigit())


def _value(whole: str, frac: str = "") -> Fraction:
    return Fraction(int(whole)) + (Fraction(int(frac), 10 ** len(frac)) if frac else 0)


def source_numbers(text: str) -> list[tuple[str, Fraction]]:
    """The numbers of English text as (as written, value): "1,000" is 1000, "3.5" is 3.5."""
    out = []
    for m in _SOURCE.findall(str(text)):
        whole, _, frac = _digits_keep(m).partition(".")
        out.append((m, _value(whole, frac)))
    return out


def _digits_keep(m: str) -> str:
    """ASCII digits of a number, with "." kept and "," dropped."""
    return "".join("." if ch == "." else str(unicodedata.decimal(ch)) for ch in m if ch != ",")


def _grouped(groups: list[str]) -> bool:
    """Digit groups of a number whose separators group thousands: 1-3 digits then 3s (1,234,567), or Indian 2s then 3."""
    if len(groups) < 2 or not 1 <= len(groups[0]) <= 3 or len(groups[-1]) != 3:
        return False
    rest = groups[1:-1]
    return all(len(g) == 3 for g in rest) or (len(groups[0]) <= 2 and all(len(g) == 2 for g in rest))


def possible_values(token: str) -> set[Fraction]:
    """Every value a number written in a translation can mean. The last "." "," or U+066B may be the decimal point
    (any other separators then group thousands); or every separator groups thousands, which needs groups of exactly
    3 digits (or Indian 2-2-3). U+066B is only ever a decimal point; U+066C, no-break and thin spaces only group."""
    parts = re.split("([" + _SEPS + "])", token)
    groups, seps = [_digits(g) for g in parts[::2]], parts[1::2]
    if not seps:
        return {_value(groups[0])}
    out: set[Fraction] = set()
    if "٫" not in seps and _grouped(groups):
        out.add(_value("".join(groups)))
    if seps[-1] in ".,٫" and "٫" not in seps[:-1]:
        whole = groups[:-1]
        if len(whole) == 1 or _grouped(whole):
            out.add(_value("".join(whole), groups[-1]))
    return out


def translated_numbers(text: str) -> list[tuple[str, set[Fraction]]]:
    return [(m, possible_values(m)) for m in _TRANSLATED.findall(str(text))]


def match_numbers(source: str, translated: str) -> tuple[list[str], list[str]]:
    """(English numbers the translation leaves out, numbers in the translation the English hasn't), as written. Each
    English number must match a distinct number of the translation, by value."""
    src = source_numbers(source)
    tr = translated_numbers(translated)
    owner: dict[int, int] = {}  # translated index -> source index

    def place(i: int, seen: set[int]) -> bool:
        for j, (_, vals) in enumerate(tr):
            if src[i][1] in vals and j not in seen:
                seen.add(j)
                if j not in owner or place(owner[j], seen):
                    owner[j] = i
                    return True
        return False

    for i in range(len(src)):
        place(i, set())
    placed = set(owner.values())
    return [src[i][0] for i in range(len(src)) if i not in placed], [tr[j][0] for j in range(len(tr)) if j not in owner]


def is_rtl(lang: str) -> bool:
    return str(lang).split("-")[0].lower() in RTL


def _text_ok(v) -> bool:
    return isinstance(v, str) and bool(v.strip())


def _closing_voice(short, settings: dict | None) -> str | None:
    from bsa import closing

    card = closing.resolve(settings or {}, short)
    return card.get("voice_line") if card else None


def stale(short, settings: dict | None = None) -> dict[str, list[str]]:
    """Per language the translated lines (and "closing", when `settings` give the closing voice line) whose English
    isn't the one recorded in the language's "from". Pure: reads the short only."""
    data = getattr(short, "captions_translations", None)
    out: dict[str, list[str]] = {}
    for lang, entry in data.items() if isinstance(data, dict) else ():
        if not isinstance(entry, dict) or not isinstance(entry.get("lines"), dict):
            continue
        frm = entry.get("from") if isinstance(entry.get("from"), dict) else {}
        bad = [ln.id for ln in short.lines if _text_ok(entry["lines"].get(ln.id)) and frm.get(ln.id) != ln.text]
        voice = _closing_voice(short, settings) if settings is not None else None
        if voice and _text_ok(entry.get("closing")) and frm.get(CLOSING) != voice:
            bad.append(CLOSING)
        if bad:
            out[lang] = bad
    return out


def problems(short, settings: dict) -> tuple[list[str], list[str]]:
    """(errors, warnings) for the short's translations."""
    data = getattr(short, "captions_translations", None)
    if data is None:
        return [], []
    if not isinstance(data, dict):
        return [f"captions_translations must map language codes to their subtitles, got {data!r}"], []
    errors: list[str] = []
    warnings: list[str] = []
    ids = [ln.id for ln in short.lines]
    voice = _closing_voice(short, settings)

    def check(where: str, lang: str, source: str, text: str) -> None:
        if any(ch in text for ch in BIDI_OVERRIDES):
            errors.append(f"{where}'s {lang} translation has a text-direction override character (U+202A-U+202E): remove it")
        left, extra = match_numbers(source, text)
        errors.extend(f"{where}'s {lang} translation leaves out the number {n}" for n in left)
        warnings.extend(f"{where}'s {lang} translation has the number {n}, which the English doesn't" for n in extra)

    for lang, entry in data.items():
        if not isinstance(lang, str) or not LANG.match(lang):
            errors.append(f"captions_translations language {lang!r} isn't a language code (es, pt-BR, ar...)")
            continue
        if not isinstance(entry, dict) or not isinstance(entry.get("lines"), dict):
            errors.append(
                f'captions_translations {lang} must be {{"lines": {{line id: text}}, "closing": text, '
                f'"from": {{line id: English}}}}'
            )
            continue
        extra_keys = sorted(set(entry) - {"lines", "closing", "from"})
        if extra_keys:
            errors.append(f"captions_translations {lang} has unknown keys {extra_keys}: use lines, closing, from")
        frm = entry.get("from")
        if frm is not None and not (isinstance(frm, dict) and all(isinstance(v, str) for v in frm.values())):
            errors.append(f"captions_translations {lang} from must map line ids to the English they were made from")
        got = entry["lines"]
        for line in short.lines:
            t = got.get(line.id)
            if not _text_ok(t):
                errors.append(f"line {line.id} has no {lang} translation")
                continue
            check(f"line {line.id}", lang, line.text, t)
        for lid in got:
            if lid not in ids:
                errors.append(f"{lang} translates line {lid}, which the short doesn't have")
        c = entry.get("closing")
        if c is not None and not _text_ok(c):
            errors.append(f"{lang} closing translation must be text, got {c!r}")
        elif c is not None and not voice:
            errors.append(f"{lang} has a closing translation but the short has no closing voice line")
        elif c is not None:
            check("the closing", lang, voice, c)
        elif voice:
            warnings.append(f"the closing line has no {lang} translation")
        warnings.append(f"hard-rule word checks are English-only; check the {lang} text")
    for lang, bad in stale(short, settings).items():
        names = ", ".join("the closing line" if b == CLOSING else b for b in bad)
        warnings.append(f"{lang} subtitles for {names} were made for the old English: update them")
    return errors, warnings


def cues(tl, short, lang: str, closing=None) -> list:
    """The language's subtitle cues, with the English timing: per line one span from its first card's start to its
    last card's end, the translated text wrapped like the English and shared out when it needs more than MAX_LINES
    lines. The closing cue keeps the English cue's frames with the translated closing text. A line (or the closing
    line) with no translation gets no cue."""
    from bsa import captions

    data = getattr(short, "captions_translations", None)
    entry = data.get(lang) if isinstance(data, dict) else None
    if not isinstance(entry, dict) or not isinstance(entry.get("lines"), dict):
        return []
    out = []
    for line in short.lines:
        cards = [c for c in tl.cards if c.line_id == line.id and c.f1 >= c.f0]
        text = entry["lines"].get(line.id)
        if not cards or not _text_ok(text):
            continue
        f0, f1 = min(c.f0 for c in cards), max(c.f1 for c in cards)
        wrapped = captions.wrap(text)
        if not wrapped:
            continue
        out += [captions.Cue(f0, f1, wrapped)] if len(wrapped) <= captions.MAX_LINES else captions._share(wrapped, f0, f1)
    text = entry.get(CLOSING)
    if closing is not None and _text_ok(text) and captions.wrap(text):
        out.append(captions.Cue(closing.f0, closing.f1, captions.wrap(text)))
    return out
