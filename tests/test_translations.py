"""Translated subtitles: the data in short.json, its checks, and stale lines."""

import copy
import json

import pytest
from test_cli import home, ok, pendulum, run, workspace  # noqa: F401 - fixtures

from bsa import rules, settings, translations
from bsa.short import Line, Short, Shot, validate

NO_HUMANS = {"id": "r1", "text": "No people, no body parts", "kind": "no_humans"}
EN = ["A pendulum swings like clockwork.", "Length sets the beat.", "The arm swings 2 metres"]
ES = {
    "lines": {"l01": "Un pendulo oscila como un reloj.", "l02": "El largo marca el ritmo.",
              "l03": "El brazo oscila 2 metros"},
    "from": {"l01": EN[0], "l02": EN[1], "l03": EN[2]},
}  # fmt: skip


def _short(**kw):
    lines = [Line(f"l0{i + 1}", t, "s1") for i, t in enumerate(EN)]
    shots = [Shot(id="s1", title="x", picture="y", scene="z")]
    return Short(slug="t", title="T", length_s=10, lines=lines, shots=shots, **kw)


def _es(**over):
    e = copy.deepcopy(ES)
    e.update(over)
    return e


def _errors(short, s=None):
    return translations.problems(short, s or settings.DEFAULTS)[0]


def _warnings(short, s=None):
    return translations.problems(short, s or settings.DEFAULTS)[1]


def _one(source, translated):
    return translations.match_numbers(source, translated)


# --------------------------------------------------------------------------- numbers by value


def test_numbers_are_compared_by_value():
    assert _one("It is 1.5 long", "Mide 15")[0] == ["1.5"]  # 1.5 is not 15
    assert _one("1,500 units", "1.500 unidades") == ([], [])  # German thousands
    assert _one("1,500 units", "1,5 Einheiten")[0] == ["1,500"]  # 1,5 is a decimal there
    assert _one("1.5 long", "1,5 lang") == ([], [])  # decimal comma
    assert _one("3.5 metres", "٣٫٥ متر") == ([], [])  # Arabic-Indic with U+066B
    for t in ("50%", "%50", "50 %", "٥٠٪", "٥٠٪‏"):
        assert _one("50% more", t) == ([], []), t
    assert _one("50% more", "٥٠٪")[1] == []
    assert _one("2 and 2", "2")[0] == ["2"]  # each English number needs its own
    assert _one("2 and 2", "2 y 2") == ([], [])
    assert _one("It swings 2 metres", "oscila dos metros")[0] == ["2"]  # words stay an error
    assert _one("12,500", "12 500") == ([], []) and _one("12,500", "12 500")[0] == ["12,500"]
    assert _one("100000", "1,00,000") == ([], [])  # Indian grouping
    assert _one("1,234,567", "1.234.567") == ([], [])


def test_a_number_the_english_doesnt_have_is_a_warning():
    left, extra = _one("It swings", "Oscila 3 veces")
    assert left == [] and extra == ["3"]
    short = _short(captions_translations={"es": _es()})
    short.captions_translations["es"]["lines"]["l01"] += " 7"
    assert _errors(short) == []
    assert any("line l01's es translation has the number 7" in w for w in _warnings(short))


# --------------------------------------------------------------------------- shape


def test_every_line_once_and_no_extras():
    short = _short(captions_translations={"es": {"lines": {"l01": "a", "l02": "b", "l09": "c"}}})
    errors = _errors(short)
    assert "line l03 has no es translation" in errors
    assert "es translates line l09, which the short doesn't have" in errors
    assert not any("l01" in e or "l02" in e for e in errors)


def test_a_dropped_number_is_an_error():
    bad = _es()
    bad["lines"]["l03"] = "El brazo oscila dos metros"
    assert _errors(_short(captions_translations={"es": bad})) == ["line l03's es translation leaves out the number 2"]


def test_bad_language_codes_are_refused():
    for code in ("Spanish", "es_ES"):
        errors = _errors(_short(captions_translations={code: _es()}))
        assert len(errors) == 1 and repr(code) in errors[0]
    for code in ("pt-BR", "ar"):
        assert _errors(_short(captions_translations={code: _es()})) == []
    assert "lines" in _errors(_short(captions_translations={"es": ["x"]}))[0]


def test_rtl_text_is_accepted():
    ar = {"lines": {"l01": "النواس يتأرجح",
                    "l02": "الطول يحدد الإيقاع",
                    "l03": "الذراع تتأرجح ٢ متر"},
          "from": {"l01": EN[0], "l02": EN[1], "l03": EN[2]}}  # fmt: skip
    short = _short(captions_translations={"ar": ar})
    assert _errors(short) == [] and not any("old English" in w for w in _warnings(short))
    for code in ("ar", "he-IL", "ckb", "dv", "sd", "ug"):
        assert translations.is_rtl(code)
    assert not translations.is_rtl("es")


def test_bidi_overrides_are_refused_but_isolates_are_fine():
    bad = _es()
    bad["lines"]["l01"] = "Un ‮pendulo‬ oscila"
    assert any("l01" in e and "override" in e for e in _errors(_short(captions_translations={"es": bad})))
    ok_ = _es()
    ok_["lines"]["l01"] = "Un ⁧pendulo⁩ oscila"
    assert _errors(_short(captions_translations={"es": ok_})) == []


def test_closing_needs_a_voice_line_and_its_numbers():
    s = copy.deepcopy(settings.DEFAULTS)
    s["website"] = "example.org"
    s["closing"] = {"voice_line": "Find 3 more at example dot org."}
    tr = {"es": _es(closing="Encuentra mas")}
    assert _errors(_short(captions_translations=tr), s) == ["the closing's es translation leaves out the number 3"]
    assert "the closing line has no es translation" in _warnings(_short(captions_translations={"es": _es()}), s)
    errors = _errors(_short(captions_translations=tr))  # no closing voice line
    assert errors == ["es has a closing translation but the short has no closing voice line"]


def test_every_language_warns_that_rule_checks_are_english_only():
    short = _short(captions_translations={"es": _es(), "pt-BR": _es()})
    ws = _warnings(short)
    assert "hard-rule word checks are English-only; check the es text" in ws
    assert "hard-rule word checks are English-only; check the pt-BR text" in ws


# --------------------------------------------------------------------------- stale (pure, from "from")


def test_stale_lines_follow_the_recorded_english():
    short = _short(captions_translations={"es": _es()})
    assert translations.stale(short) == {}
    short.lines[1].text = "Length sets the pace."
    assert translations.stale(short) == {"es": ["l02"]}
    assert any("es subtitles for l02 were made for the old English" in w for w in _warnings(short))
    short.captions_translations["es"]["lines"]["l02"] = "El largo marca el compas."
    assert translations.stale(short) == {"es": ["l02"]}  # still stale until "from" is brought up to date
    short.captions_translations["es"]["from"]["l02"] = "Length sets the pace."
    assert translations.stale(short) == {}
    del short.captions_translations["es"]["from"]["l01"]  # no record = stale
    assert translations.stale(short) == {"es": ["l01"]}
    short.lines.append(Line("l07", "One more.", "s1"))
    assert _errors(short) == ["line l07 has no es translation"]


def test_none_and_empty_mean_not_asked_and_none_wanted():
    assert Short(slug="t", title="T", length_s=1).captions_translations is None
    short = _short()
    assert translations.problems(short, settings.DEFAULTS) == ([], [])
    short.captions_translations = {}
    assert translations.problems(short, settings.DEFAULTS) == ([], [])
    assert validate(short) == validate(_short())
    assert translations.stale(short) == {}


@pytest.mark.parametrize("bad", ["es", ["es"], 3])
def test_a_non_object_is_an_error(bad):
    errors = _errors(_short(captions_translations=bad))
    assert errors and "captions_translations" in errors[0]


def test_hard_rules_see_translated_text():
    s = copy.deepcopy(settings.DEFAULTS)
    s["rules"] = [dict(NO_HUMANS)]
    s["rules_asked"] = True
    short = _short(rules_check={"r1": "no people shown"})
    short.lines[2].text = "It swings 2 metres"
    short.captions_translations = {"es": _es(closing="El actor")}
    short.captions_translations["es"]["lines"]["l01"] = "Un actor oscila"
    _, warnings = rules.problems(short, s)
    assert any("line l01 (es)" in w and "actor" in w for w in warnings)
    assert any("closing (es)" in w and "actor" in w for w in warnings)
    short.captions_translations = None
    assert not any("(es)" in w for w in rules.problems(short, s)[1])


# --------------------------------------------------------------------------- the CLI


def test_validate_reports_translation_errors_and_stale_lines_and_writes_nothing(home, pendulum):  # noqa: F811
    path = pendulum / "source" / "short.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    en = {ln["id"]: ln["text"] for ln in data["lines"]}
    es = {"lines": {i: "texto " + i for i in en}, "from": dict(en)}
    data["captions_translations"] = {"es": es}
    path.write_text(json.dumps(data), encoding="utf-8")
    before = sorted(p.relative_to(pendulum).as_posix() for p in pendulum.rglob("*"))
    out = ok(run(home, "validate", "pendulum"))
    assert not any("old English" in w for w in out["warnings"])
    assert any("hard-rule word checks are English-only" in w for w in out["warnings"])
    assert sorted(p.relative_to(pendulum).as_posix() for p in pendulum.rglob("*")) == before  # read-only
    first = data["lines"][0]
    first["text"] = "It swings back and forth."
    es["lines"]["l02"] = ""
    path.write_text(json.dumps(data), encoding="utf-8")
    proc = run(home, "validate", "pendulum")
    assert proc.returncode == 1 and "line l02 has no es translation" in json.loads(proc.stdout)["errors"]
    es["lines"]["l02"] = "texto l02"
    path.write_text(json.dumps(data), encoding="utf-8")
    out = ok(run(home, "validate", "pendulum"))
    assert any("es subtitles for l01" in w and "old English" in w for w in out["warnings"])
