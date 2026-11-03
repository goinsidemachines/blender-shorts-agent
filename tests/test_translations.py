"""Translated subtitles: the data in short.json, its checks, and stale stamps."""

import copy
import dataclasses

import pytest

from bsa import rules, settings, translations
from bsa.short import Line, Short, Shot, validate

NO_HUMANS = {"id": "r1", "text": "No people, no body parts", "kind": "no_humans"}


def _short(**kw):
    lines = [
        Line("l01", "A pendulum swings like clockwork.", "s1"),
        Line("l02", "Length sets the beat.", "s1"),
        Line("l03", "The arm swings 2 metres", "s1"),
    ]
    return Short(slug="t", title="T", length_s=10, lines=lines, shots=[Shot(id="s1", title="x", picture="y", scene="z")], **kw)


ES = {"lines": {"l01": "Un pendulo oscila como un reloj.", "l02": "El largo marca el ritmo.",
                "l03": "El brazo oscila 2 metros"}}  # fmt: skip


def test_numbers_ignore_separators():
    assert translations.numbers("1,000 and 1.000") == ["1000", "1000"]
    assert translations.numbers("3.5 or 3,5") == ["35", "35"]
    assert translations.numbers("no digits") == []
    assert translations.numbers("٣٫٥ १२") == ["35", "12"]  # Arabic-Indic, Devanagari


def test_every_line_once_and_no_extras():
    short = _short(captions_translations={"es": {"lines": {"l01": "a", "l02": "b", "l09": "c"}}})
    errors, _ = translations.problems(short, settings.DEFAULTS)
    assert "line l03 has no es translation" in errors
    assert "es translates line l09, which the short doesn't have" in errors
    assert not any("l01" in e or "l02" in e for e in errors)


def test_a_dropped_number_is_an_error():
    bad = copy.deepcopy(ES)
    bad["lines"]["l03"] = "El brazo oscila dos metros"
    errors, _ = translations.problems(_short(captions_translations={"es": bad}), settings.DEFAULTS)
    assert errors == ["line l03's es translation leaves out the number 2"]
    ok = copy.deepcopy(ES)
    ok["lines"]["l03"] = "الذراع ٢"  # Arabic-Indic 2 counts as 2
    assert translations.problems(_short(captions_translations={"es": ok}), settings.DEFAULTS)[0] == []
    s = _short(captions_translations={"es": copy.deepcopy(ES)})
    s.lines[2].text = "It is 3.5 metres"
    s.captions_translations["es"]["lines"]["l03"] = "Son 3,5 metros"
    assert translations.problems(s, settings.DEFAULTS)[0] == []


def test_bad_language_codes_are_refused():
    for code in ("Spanish", "es_ES"):
        errors, _ = translations.problems(_short(captions_translations={code: ES}), settings.DEFAULTS)
        assert len(errors) == 1 and repr(code) in errors[0]
    for code in ("pt-BR", "ar"):
        assert translations.problems(_short(captions_translations={code: ES}), settings.DEFAULTS)[0] == []
    errors, _ = translations.problems(_short(captions_translations={"es": ["x"]}), settings.DEFAULTS)
    assert errors and "lines" in errors[0]


def test_rtl_text_is_accepted():
    ar = {"lines": {"l01": "النواس يتأرجح",
                    "l02": "الطول يحدد الإيقاع",
                    "l03": "الذراع تتأرجح 2 متر"}}  # fmt: skip
    errors, warnings = translations.problems(_short(captions_translations={"ar": ar}), settings.DEFAULTS)
    assert errors == [] and warnings == []
    assert translations.is_rtl("ar") and translations.is_rtl("he-IL") and not translations.is_rtl("es")


def test_closing_needs_a_voice_line_and_its_numbers():
    s = copy.deepcopy(settings.DEFAULTS)
    s["website"] = "example.org"
    s["closing"] = {"voice_line": "Find 3 more at example dot org."}
    tr = {"es": {**ES, "closing": "Encuentra mas"}}
    errors, _ = translations.problems(_short(captions_translations=tr), s)
    assert errors == ["the es closing translation leaves out the number 3"]
    _, warnings = translations.problems(_short(captions_translations={"es": ES}), s)
    assert warnings == ["the closing line has no es translation"]
    errors, _ = translations.problems(_short(captions_translations=tr), settings.DEFAULTS)  # no closing voice line
    assert errors == ["es has a closing translation but the short has no closing voice line"]


def test_stale_and_incomplete_translations_are_reported(tmp_path):
    short = _short(captions_translations={"es": copy.deepcopy(ES)})
    assert translations.stamp(tmp_path, short) == {}
    assert (tmp_path / "review" / "translations.json").exists()
    short.lines[1].text = "Length sets the pace."
    assert translations.stale(tmp_path, short) == {"es": ["l02"]}
    assert translations.stamp(tmp_path, short) == {"es": ["l02"]}
    short.captions_translations["es"]["lines"]["l02"] = "El largo marca el compas."
    assert translations.stale(tmp_path, short) == {}
    assert translations.stamp(tmp_path, short) == {}
    assert translations.stale(tmp_path, short) == {}
    short.lines.append(Line("l07", "One more.", "s1"))
    errors, _ = translations.problems(short, settings.DEFAULTS)
    assert errors == ["line l07 has no es translation"]


def test_hard_rules_see_translated_text():
    s = copy.deepcopy(settings.DEFAULTS)
    s["rules"] = [dict(NO_HUMANS)]
    s["rules_asked"] = True
    short = _short(rules_check={"r1": "no people shown"})
    short.lines[2].text = "It swings 2 metres"
    short.captions_translations = {"es": {**ES, "closing": "El actor"}}
    short.captions_translations["es"]["lines"]["l01"] = "Un actor oscila"
    _, warnings = rules.problems(short, s)
    assert any("line l01 (es)" in w and "actor" in w for w in warnings)
    short2 = dataclasses.replace(short, captions_translations=None)
    assert not any("(es)" in w for w in rules.problems(short2, s)[1])


def test_none_and_empty_mean_not_asked_and_none_wanted():
    assert Short(slug="t", title="T", length_s=1).captions_translations is None
    short = _short()
    assert translations.problems(short, settings.DEFAULTS) == ([], [])
    short.captions_translations = {}
    assert translations.problems(short, settings.DEFAULTS) == ([], [])
    assert validate(short) == validate(_short())
    assert translations.stamp(__import__("pathlib").Path("."), short) == {}  # nothing written for {}


@pytest.mark.parametrize("bad", ["es", ["es"], 3])
def test_a_non_object_is_an_error(bad):
    errors, _ = translations.problems(_short(captions_translations=bad), settings.DEFAULTS)
    assert errors and "captions_translations" in errors[0]
