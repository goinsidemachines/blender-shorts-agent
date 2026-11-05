"""The fact panel: the facts behind each line and the numbers on screen, each with the fact that backs it."""

import pagefix

from bsa.review import facts, snapshot
from bsa.short import Fact, Line, Short, Shot, validate


def short_with(fact_claim="A claim", line_text="Plain.", cites=("f1",), labels=(), source="https://example.com/x"):
    return Short(
        slug="t", title="T", length_s=10,
        facts=[Fact("f1", fact_claim, source)],
        lines=[Line("l01", line_text, "s01", list(cites))],
        shots=[Shot("s01", "Hook", "pic", "Scene", labels=list(labels))],
    )  # fmt: skip


def test_on_screen_numbers_match_whole_normalised_numbers():  # Review Focus 4
    assert facts.numbers("1,800 swings, 3.50 m, 12 and 4.0") == ["1800", "3.5", "12", "4"]
    s = short_with(fact_claim="It swings 120 times and 1800 times, 3.5 m", line_text="12 or 1,800 at 3.50 m")
    ok = {r["number"]: r["ok"] for r in facts.panel(s)["on_screen"]}
    assert ok == {"12": False, "1800": True, "3.5": True}


def test_numbers_edge_cases():
    assert facts.numbers("1,8000") == ["1", "8000"]  # not a thousands separator
    assert facts.numbers("1,2,3") == ["1", "2", "3"]
    assert facts.numbers("100 and 1,000,000") == ["100", "1000000"]
    assert facts.numbers("10.0 and 0.50") == ["10", "0.5"]
    assert facts.numbers("no digits") == []
    assert facts.numbers(".5 and 0.50 and 007") == ["0.5", "0.5", "7"]
    assert facts.numbers("12345678901234567890123456789.10") == ["12345678901234567890123456789.1"]  # no rounding


def test_a_number_inside_a_longer_number_is_no_match():
    s = short_with(fact_claim="Takes 120 seconds and 1.25 m", line_text="12 and 1 and 25 and 1.2")
    assert not any(r["ok"] for r in facts.panel(s)["on_screen"])


def test_flag_words_match_what_validate_says():
    s = short_with(line_text="It weighs 660 tonnes", cites=())
    p = facts.panel(s)
    assert p["lines"][0]["flags"] == ["Has a number but cites no fact"] and p["flagged"] is True
    assert validate(s) == ["line l01 contains a number but cites no fact"]
    s = short_with(cites=("nope",))
    assert facts.panel(s)["lines"][0]["flags"] == ["Cites a fact that isn't in the list (nope)"]
    assert validate(s) == ["line l01 cites unknown fact 'nope'"]
    assert facts.panel(short_with())["flagged"] is False


def test_label_number_uses_a_fact_cited_in_the_same_shot():
    s = short_with(fact_claim="It is 4 m long", labels=["4 m", {"text": "9 m", "near": "Bob"}])
    s.shots.append(Shot("s02", "Other", "pic", "Scene", labels=["4 m"]))
    rows = [r for r in facts.panel(s)["on_screen"] if r["where"].endswith("label")]
    assert [(r["where"], r["number"], r["fact"]) for r in rows] == [
        ("shot s01 label", "4", "f1"), ("shot s01 label", "9", None), ("shot s02 label", "4", None)]  # fmt: skip


def test_fact_chips_say_whether_the_source_is_a_link():
    p = facts.panel(short_with())
    assert p["lines"][0]["facts"] == [{"id": "f1", "claim": "A claim", "source": "https://example.com/x", "is_link": True}]
    assert facts.panel(short_with(source="Hammad's notes"))["lines"][0]["facts"][0]["is_link"] is False


def test_fact_panel_never_moves_a_version(tmp_path):
    for stage in ("story", "final"):
        sdir = pagefix.make_short(tmp_path / stage, stage)
        content = snapshot.BUILDERS[stage](snapshot.load_ctx(sdir, {}))
        assert content["facts_panel"]["lines"]
        before = snapshot.stage_version(sdir, stage, snapshot.load_ctx(sdir, {}))
        content["facts_panel"] = {"lines": [], "on_screen": [{"number": "changed"}], "flagged": True}
        assert snapshot._version_of(sdir, stage, content) == before


def test_final_version_ignores_the_story_words_the_panel_follows(tmp_path):
    sdir = pagefix.make_short(tmp_path, "final")
    before = snapshot.stage_version(sdir, "final", snapshot.load_ctx(sdir, {}))
    pagefix.with_numbers(sdir)
    ctx = snapshot.load_ctx(sdir, {})
    assert snapshot.BUILDERS["final"](ctx)["facts_panel"]["flagged"] is True
    assert snapshot.stage_version(sdir, "final", ctx) == before


def test_card_numbers_are_checked_against_the_shots_facts():
    s = short_with(fact_claim="It lifts 3 times more", line_text="Plain.")
    s.shots[0].title_card = ["Lifts 3x", "not 7"]
    s.shots[0].bars = {"items": [{"label": "Bar", "value": 3}, {"label": "Cup", "value": 1, "text": "1 kg"}],
                       "verdict": "Bar wins"}
    rows = {(r["where"], r["number"]): r["ok"] for r in facts.panel(s)["on_screen"]}
    assert rows[("shot s01 title card", "3")] is True and rows[("shot s01 title card", "7")] is False
    assert ("shot s01 value bar", "1") in rows
