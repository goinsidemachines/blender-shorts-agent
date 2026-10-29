from pathlib import Path

from bsa.short import Fact, Line, Short, Shot, load_short, save_short, validate

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
