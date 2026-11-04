from dataclasses import replace
from pathlib import Path

from bsa.short import (
    Fact,
    Line,
    Short,
    Shot,
    bar_text,
    category_text,
    label_kind,
    load_short,
    save_short,
    step_text,
    title_lines,
    validate,
)

SAMPLE = Path(__file__).parent / "e2e" / "pendulum" / "source" / "short.json"


def _short(**kw) -> Short:
    base = {
        "slug": "t",
        "title": "T",
        "length_s": 10,
        "facts": [Fact("f1", "claim", "https://example.com")],
        "lines": [Line("l01", "Plain words.", "s01", [])],
        "shots": [Shot("s01", "Hook", "pic", "Scene")],
    }
    base.update(kw)
    return Short(**base)


def test_validate_catches_unsourced_number():
    s = _short(lines=[Line("l01", "It weighs 660 tonnes", "s01", [])])
    errors = validate(s)
    assert len(errors) == 1
    assert "l01" in errors[0]


def test_validate_ok_on_sample():
    assert validate(load_short(SAMPLE)) == []


def test_number_with_fact_is_ok():
    assert validate(_short(lines=[Line("l01", "It weighs 660 tonnes", "s01", ["f1"])])) == []


def test_validate_other_errors():
    s = _short(
        fps=24,
        width=720,
        facts=[Fact("f1", "c", ""), Fact("f1", "c", "x")],
        lines=[Line("l01", "a", "nope", ["zzz"]), Line("l01", "b", "s01", [])],
    )
    text = "\n".join(validate(s))
    for needle in ("fps", "1080", "empty source", "duplicate fact", "duplicate line", "unknown shot", "unknown fact"):
        assert needle in text
    assert any("no lines" in e for e in validate(_short(lines=[])))


def test_label_number_needs_fact():
    s = _short(shots=[Shot("s01", "Hook", "pic", "Scene", labels=["660 t"])])
    assert any("s01" in e for e in validate(s))


def test_roundtrip(tmp_path):
    s = load_short(SAMPLE)
    save_short(s, tmp_path / "out" / "short.json")
    assert load_short(tmp_path / "out" / "short.json") == s


def test_labels_may_be_near_an_object():
    from bsa.short import label_near, label_text

    s = _short(shots=[Shot("s01", "Hook", "pic", "Scene", labels=["Plain", {"text": "Light", "near": "Light bob"}])])
    assert validate(s) == []
    assert [label_text(x) for x in s.shots[0].labels] == ["Plain", "Light"]
    assert [label_near(x) for x in s.shots[0].labels] == [None, "Light bob"]


def test_a_near_label_may_point_at_a_local_point():
    from bsa.short import label_anchor, label_at

    at = {"text": "Stop", "near": "Tape", "at": [0, 0.01, 0.002]}
    plain = {"text": "Stop", "near": "Tape"}
    assert validate(_short(shots=[Shot("s01", "Hook", "pic", "Scene", labels=[at])])) == []
    assert label_at(at) == (0.0, 0.01, 0.002) and label_at(plain) is None and label_at("Stop") is None
    assert label_anchor(at) == "Tape@0,0.01,0.002"
    assert label_anchor(plain) == "Tape" and label_anchor("Stop") is None
    for bad in ({**plain, "at": [0, 1]}, {**plain, "at": "0,0,1"}, {**plain, "at": [0, 1, float("nan")]},
                {**plain, "at": [0, True, 1]}, {"text": "Stop", "at": [0, 0, 1]}):  # fmt: skip
        errs = validate(_short(shots=[Shot("s01", "Hook", "pic", "Scene", labels=[bad])]))
        assert any("s01" in e and "at" in e for e in errs), (bad, errs)


def test_labels_with_different_at_points_get_their_own_anchors():
    from bsa.short import near_anchors

    labels = [{"text": "Stop", "near": "Tape", "at": [0, 0.01, 0]}, {"text": "Pull", "near": "Tape", "at": [0, 0.2, 0]},
              {"text": "Tape", "near": "Tape"}, "Plain"]  # fmt: skip
    s = _short(shots=[Shot("s01", "Hook", "pic", "Scene", labels=labels)])
    got = near_anchors(s)
    assert got == [("s01", "Stop", "Tape", "Tape@0,0.01,0", (0.0, 0.01, 0.0)),
                   ("s01", "Pull", "Tape", "Tape@0,0.2,0", (0.0, 0.2, 0.0)),
                   ("s01", "Tape", "Tape", "Tape", None)]  # fmt: skip


def test_bad_labels_are_reported():
    for bad in ({"near": "Ball"}, {"text": "", "near": "Ball"}, {"text": "Ball", "near": ""}, 7, ""):
        s = _short(shots=[Shot("s01", "Hook", "pic", "Scene", labels=[bad])])
        assert any("s01" in e and "label" in e for e in validate(s)), bad
    s = _short(shots=[Shot("s01", "Hook", "pic", "Scene", labels=[{"text": "660 t", "near": "Ball"}])])
    assert any("contains a number" in e for e in validate(s))


def test_say_is_spoken_and_validated(tmp_path):
    from bsa.short import spoken_text

    ln = Line("l01", "Balanced: 30 × 2.7 = 80 × 1", "s01", ["f1"], say="Thirty times two point seven, eighty times one.")
    s = _short(lines=[ln, Line("l02", "Plain.", "s01")])
    assert validate(s) == []
    assert spoken_text(ln).startswith("Thirty") and spoken_text(s.lines[1]) == "Plain."
    save_short(s, tmp_path / "short.json")
    assert load_short(tmp_path / "short.json") == s
    for bad in ("", "   ", 7):
        s = _short(lines=[Line("l01", "Plain.", "s01", say=bad)])
        assert any("l01" in e and "say" in e for e in validate(s)), bad
    s = _short(lines=[Line("l01", "Plain words.", "s01", say="It weighs 660 tonnes")])  # a number spoken
    assert any("l01" in e and "number" in e for e in validate(s))


def test_say_warning_for_symbols_a_voice_reads_badly():
    from bsa.short import spoken_warnings

    s = _short(facts=[Fact("f1", "c", "https://x")], lines=[Line("l01", "30 × 2.7 ≈ 80", "s01", ["f1"])])
    warns = spoken_warnings(s)
    assert len(warns) == 1 and "l01" in warns[0] and "say" in warns[0]
    s.lines[0].say = "Thirty times two point seven is about eighty."
    assert spoken_warnings(s) == []


def test_end_card_is_validated(tmp_path):
    for good in (None, "Play with it", {"text": "Play with it"}, {"text": "Play", "second": "example.com"}, {"text": ""}):
        assert validate(_short(end_card=good)) == [], good
    for bad in (7, {"second": 3}, {"text": "a", "url": "x"}, ["a"]):
        assert any("end_card" in e for e in validate(_short(end_card=bad))), bad
    s = _short(end_card={"text": "Play with it", "second": "example.com"})
    save_short(s, tmp_path / "short.json")
    assert load_short(tmp_path / "short.json") == s


def test_shot_hold_is_validated():
    for good in (False, True, [], [[0.5, 1.5]], [[0, 1], [2.5, 3]]):
        assert validate(_short(shots=[Shot("s01", "Hook", "pic", "Scene", hold=good)])) == [], good
    for bad in ("yes", [[1]], [[2, 1]], [[-1, 1]], [["a", 1]], 3):
        assert any("s01" in e and "hold" in e for e in validate(_short(shots=[Shot("s01", "H", "p", "S", hold=bad)]))), bad


def test_near_labels_round_trip(tmp_path):
    s = _short(shots=[Shot("s01", "Hook", "pic", "Scene", labels=[{"text": "Light", "near": "Light bob"}])])
    save_short(s, tmp_path / "short.json")
    assert load_short(tmp_path / "short.json") == s


def test_validate_messages_are_unchanged_by_the_helper():
    s = _short(lines=[Line("l01", "It weighs 660 tonnes", "s01", ["nope"])])
    assert validate(s) == ["line l01 cites unknown fact 'nope'", "line l01 contains a number but cites no fact"]
    s = _short(lines=[Line("l01", "Plain.", "s01", [], say="seven")], facts=[])
    assert validate(s) == []
    s = _short(lines=[Line("l01", "Plain.", "s01", [], say="in 1999")])
    assert validate(s) == ["line l01 contains a number but cites no fact"]


def test_line_fact_flags_codes():
    from bsa.short import line_fact_flags

    assert line_fact_flags(Line("l01", "No digits", "s01", ["f1"]), {"f1"}) == []
    assert line_fact_flags(Line("l01", "In 1999", "s01", ["x", "f1"]), {"f1"}) == [("unknown_fact", "x")]
    assert line_fact_flags(Line("l01", "In 1999", "s01", ["x"]), {"f1"}) == [("unknown_fact", "x"), ("number_without_fact", None)]


def test_card_fields_validate():
    ok = Shot("s1", "t", "p", "sc", title_card={"lines": ["How a lever", "lifts more"], "highlight": 1},
              category="spark", step=[2, 4], labels=[{"text": "Pivot", "kind": "measure"}])
    assert validate(_short(shots=[ok], lines=[Line("l01", "Plain words.", "s1", [])])) == []
    bars = {"items": [{"label": "a", "value": 1}]}
    for bad, msg in (({"title_card": ["a", "b", "c"]}, "title_card"), ({"step": [5, 4]}, "step"),
                     ({"bars": bars}, "bars"), ({"labels": [{"text": "x", "kind": "arrow"}]}, "kind"),
                     ({"title_card": {"lines": ["a"], "highlight": 1}}, "title_card"),
                     ({"category": " "}, "category"),
                     ({"bars": {"items": [*bars["items"], {"label": "b", "value": -1}]}}, "bars")):
        shots = [replace(ok, **bad)]
        errors = validate(_short(shots=shots, lines=[Line("l01", "Plain words.", "s1", [])]))
        assert any(msg in e and "s1" in e for e in errors), (bad, errors)


def test_numbers_on_cards_need_a_fact():
    def errors(cited, **kw):
        ln = Line("l01", "Plain words.", "s1", ["f1"] if cited else [])
        return validate(_short(shots=[Shot("s1", "t", "p", "sc", **kw)], lines=[ln]))

    assert any("title card" in e and "no line in the shot cites a fact" in e for e in errors(False, title_card="Lifts 3x"))
    assert errors(True, title_card="Lifts 3x") == []
    bars = {"items": [{"label": "a", "value": 1}, {"label": "b", "value": 3}]}
    assert any("value bars" in e and "contains a number but no line in the shot cites a fact" in e
               for e in errors(False, bars=bars))
    assert errors(True, bars=bars) == []
    assert errors(False, step=[2, 4], title_card="Lifts a lot") == []  # the step tag's digits are exempt


def test_title_lines_and_step_text():
    def sh(**kw):
        return Shot("s1", "t", "p", "sc", **kw)

    assert title_lines(sh(title_card="One")) == (("One",), None)
    assert title_lines(sh(title_card=["a", "b"])) == (("a", "b"), None)
    assert title_lines(sh(title_card={"lines": ["a", "b"], "highlight": 1})) == (("a", "b"), 1)
    assert title_lines(sh()) == ((), None)
    assert step_text(sh(step=[2, 4])) == "Step 2 of 4" and step_text(sh()) is None
    assert category_text("spark") == "Spark" and category_text("fluid flow") == "Fluid flow"
    assert label_kind({"text": "x", "kind": "measure"}) == "measure" and label_kind("x") == "label"
    assert bar_text({"label": "a", "value": 2.5}) == "2.5" and bar_text({"label": "a", "value": 3, "text": "3 kg"}) == "3 kg"


def test_short_narrator_override_is_validated():
    assert validate(_short(narrator={"take_mode": "paragraph", "lead_in": 0.3, "voice": "v", "speed": 1.2})) == []
    assert any("provider" in e for e in validate(_short(narrator={"provider": "kokoro"})))
    assert any("lead_in" in e for e in validate(_short(narrator={"lead_in": 2})))
    assert any("take_mode" in e for e in validate(_short(narrator={"take_mode": "fast"})))
    assert any("speed" in e for e in validate(_short(narrator={"speed": 3})))
    assert any("narrator" in e for e in validate(_short(narrator="paragraph")))
    assert _short().narrator == {}


def test_pace_and_like_are_validated():
    ok = {"short": "03-how-a-clock-ticks", "copied": ["style", "pace"], "at": "2026-11-04T10:00:00Z"}
    assert validate(_short(pace={"shots": 5, "min_s": 3.5, "holds": 1}, like=ok)) == []
    assert _short().pace == {} and _short().like is None
    assert any("pace" in e for e in validate(_short(pace={"shots": 5, "speed": 1})))
    assert any("pace" in e for e in validate(_short(pace={"shots": "5"})))
    assert any("pace" in e for e in validate(_short(pace={"min_s": True})))
    assert any("pace" in e for e in validate(_short(pace={"holds": -1})))
    assert any("pace" in e for e in validate(_short(pace=[5])))
    assert any("like" in e for e in validate(_short(like={**ok, "extra": 1})))
    assert any("like" in e for e in validate(_short(like={**ok, "copied": ["render"]})))
    assert any("like" in e for e in validate(_short(like={**ok, "short": ""})))
    assert any("like" in e for e in validate(_short(like={**ok, "at": 5})))
    assert any("like" in e for e in validate(_short(like="03-how-a-clock-ticks")))
