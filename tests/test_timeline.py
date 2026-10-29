import itertools
from pathlib import Path

import pytest

from bsa.short import Line, Short, Shot, load_short
from bsa.timeline import (
    CARD_MAX_CHARS,
    CARD_MAX_WORDS,
    build_timeline,
    chunk_caption,
    length_drift,
    reading_time,
    to_json,
)

SAMPLE = Path(__file__).parent / "e2e" / "pendulum" / "source" / "short.json"


def _short(lines, min_s=1.0, length_s=10):
    shots = [Shot("s01", "Hook", "pic", "Scene", min_s=min_s)]
    return Short(slug="t", title="T", length_s=length_s, lines=lines, shots=shots)


def test_captions_only_timing():
    text = "One two three four five six seven eight."
    assert reading_time(text) == pytest.approx(3.38, abs=0.005)
    tl = build_timeline(_short([Line("l01", text, "s01")]), None)
    line = tl.lines[0]
    assert line.f0 == 1 + round(0.2 * 30)
    assert line.f1 - line.f0 + 1 == 102


def test_long_take_stretches_shot():
    s = _short([Line("l01", "a", "s01"), Line("l02", "b", "s01")], min_s=3)
    tl = build_timeline(s, {"l01": 2.5, "l02": 2.0})
    shot = tl.shots[0]
    assert (shot.f0, shot.f1) == (1, 170)  # 0.2+2.5+0.35+2.0+0.6 = 5.65 s
    assert tl.n_frames == 170
    for ln in tl.lines:
        assert shot.f0 <= ln.f0 <= ln.f1 <= shot.f1


def test_min_s_wins_and_shots_are_contiguous():
    shots = [Shot("s01", "a", "p", "s", min_s=3), Shot("s02", "b", "p", "s", min_s=2), Shot("s03", "c", "p", "s")]
    s = Short(slug="t", title="T", length_s=5, lines=[Line("l01", "hi", "s02")], shots=shots)
    tl = build_timeline(s, {"l01": 0.1})
    assert tl.shots[0].f1 - tl.shots[0].f0 + 1 == 90
    assert tl.shots[1].f0 == tl.shots[0].f1 + 1
    assert tl.shots[2].f0 == tl.shots[1].f1 + 1
    assert tl.n_frames == tl.shots[2].f1


def test_missing_take_falls_back_to_reading_time():
    s = _short([Line("l01", "x", "s01"), Line("l02", "One two three four five six seven eight.", "s01")])
    tl = build_timeline(s, {"l01": 1.0})
    assert tl.lines[0].f1 - tl.lines[0].f0 + 1 == 30
    assert tl.lines[1].f1 - tl.lines[1].f0 + 1 == 102


def test_long_and_odd_captions():
    text = " ".join(f"word{i}" for i in range(40))
    cards = chunk_caption(text)
    assert " ".join(cards) == text
    for c in cards:
        assert len(c.split()) <= CARD_MAX_WORDS
        assert len(c) <= CARD_MAX_CHARS
    giant = "x" * 50
    assert chunk_caption(f"a {giant} b") == ["a", giant, "b"]
    odd = "“Hello there,” she said \U0001f600"
    assert " ".join(chunk_caption(odd)) == odd
    assert chunk_caption("Short one. Then, another bit") == ["Short one.", "Then, another bit"]


def test_cards_fill_line_exactly():
    text = "Inside this skyscraper hangs a huge steel ball, and it never stops swinging at all."
    tl = build_timeline(_short([Line("l01", text, "s01")]), {"l01": 4.0})
    line = tl.lines[0]
    cards = tl.cards
    assert len(cards) > 1
    assert cards[0].f0 == line.f0 and cards[-1].f1 == line.f1
    for a, b in itertools.pairwise(cards):
        assert b.f0 == a.f1 + 1
    assert all(c.f1 >= c.f0 for c in cards)


def test_drift_and_json():
    tl = build_timeline(_short([Line("l01", "hello", "s01")], min_s=5), {"l01": 1.0})
    s = _short([], length_s=10)
    assert length_drift(s, tl) == pytest.approx(abs(tl.n_frames / 30 - 10) / 10)
    data = to_json(tl)
    assert data["fps"] == 30 and data["n_frames"] == tl.n_frames
    assert data["shots"][0] == {"id": "s01", "f0": 1, "f1": tl.shots[0].f1}


def test_more_cards_than_frames_merges_without_overlap():
    text = "One two three four five six seven eight nine ten eleven twelve."
    assert chunk_caption(text) == ["One two three four five six", "seven eight nine ten eleven twelve."]
    tl = build_timeline(_short([Line("l01", text, "s01")]), {"l01": 0.05})  # 2 frames
    line = tl.lines[0]
    assert line.f1 - line.f0 + 1 == 2
    cards = tl.cards
    assert len(cards) == 2
    assert cards[0].f0 == line.f0 and cards[-1].f1 == line.f1
    for a, b in itertools.pairwise(cards):
        assert b.f0 == a.f1 + 1
    assert all(c.f0 <= c.f1 for c in cards)
    assert " ".join(c.text for c in cards) == text


def test_sample_captions_only_drift_is_small():
    sample = load_short(SAMPLE)
    assert length_drift(sample, build_timeline(sample, None)) < 0.15


def test_zero_length_take_gets_no_cards_and_does_not_crash():
    from bsa.timeline import _split_cards

    assert _split_cards("Some words here.", "l01", 10, 9) == []  # a 0-frame line
    tl = build_timeline(_short([Line("l01", "Some words here.", "s01")]), {"l01": 0.0})
    assert tl.n_frames > 0 and tl.cards == []


def _orphans(cards):
    return [c for c in cards[1:] if len(c.split()) == 1 and len(c) <= 8]


def test_no_one_word_orphan_cards():
    assert chunk_caption("A heavy ball and a light one.") == ["A heavy ball and a light one."]
    assert chunk_caption("It swings back. Fast.") == ["It swings back. Fast."]
    for text in (
        "A heavy ball and a light one.",
        "Make the long string much shorter and it swings far faster now.",
        "The bob swings left, then right, then left again, and again.",
        "Neither. They stay in step.",
        "Inside this skyscraper hangs a huge steel ball, and it never stops swinging at all.",
    ):
        cards = chunk_caption(text)
        assert " ".join(cards) == text
        assert _orphans(cards) == [], cards
        for c in cards:
            assert len(c) <= CARD_MAX_CHARS + 6 and len(c.split()) <= CARD_MAX_WORDS + 1, c


def test_orphan_that_cannot_join_is_rebalanced():
    text = "Heavy pendulums measure gravity exactly."  # 31 + 8 chars: too long to join, so the split moves
    assert chunk_caption(text) == ["Heavy pendulums measure", "gravity exactly."]
    text = "Swinging pendulums measure gravity, steadily."  # across a clause too
    assert chunk_caption(text) == ["Swinging pendulums measure", "gravity, steadily."]


def test_end_card_adds_a_hold_to_the_last_shot():
    sample = load_short(SAMPLE)
    plain, held = build_timeline(sample, None), build_timeline(sample, None, end_card=True)
    assert held.n_frames == plain.n_frames + 45
    assert held.shots[:-1] == plain.shots[:-1] and held.shots[-1].f1 == plain.shots[-1].f1 + 45
    assert held.lines == plain.lines and held.cards == plain.cards


def test_length_warning_says_seconds_and_direction():
    from bsa.timeline import Timeline, length_warning

    s = _short([], length_s=45)
    long_tl = Timeline(30, 65 * 30, [], [], [])
    assert length_warning(s, long_tl) == "at reading pace the short runs about 65 s, 44% over its 45 s target"
    short_tl = Timeline(30, 30 * 30, [], [], [])
    assert length_warning(s, short_tl) == "at reading pace the short runs about 30 s, 33% under its 45 s target"
    assert length_warning(s, Timeline(30, 48 * 30, [], [], [])) is None  # within 15%: no warning
    assert length_warning(_short([], length_s=0), long_tl) is None


def _paced():
    shots = [Shot("s01", "Hook", "pic", "Scene", min_s=3), Shot("s02", "Hold", "pic", "Scene", min_s=12),
             Shot("s03", "Quiet", "pic", "Scene", min_s=4), Shot("s04", "Beat", "pic", "Scene", min_s=2)]  # fmt: skip
    lines = [Line("l01", "a", "s01"), Line("l02", "b", "s02")]
    return Short(slug="t", title="T", length_s=20, lines=lines, shots=shots)


def test_pacing_reports_spoken_and_silent_seconds_per_shot():
    from bsa.timeline import pacing

    s = _paced()
    tl = build_timeline(s, {"l01": 2.4, "l02": 3.0})
    rows = {r["shot"]: r for r in pacing(s, tl)}
    assert rows["s01"]["seconds"] == pytest.approx(3.2, abs=0.04)  # 0.2 + 2.4 + 0.6
    assert rows["s01"]["spoken_s"] == pytest.approx(2.4, abs=0.04)
    assert rows["s01"]["silent_s"] == pytest.approx(0.8, abs=0.04)
    assert rows["s02"]["seconds"] == pytest.approx(12.0) and rows["s02"]["spoken_s"] == pytest.approx(3.0)
    assert rows["s02"]["silent_frac"] == pytest.approx(0.75, abs=0.01)
    assert rows["s02"]["too_silent"] and not rows["s01"]["too_silent"]
    assert rows["s02"]["suggest_s"] == pytest.approx(3.8, abs=0.1)  # speech + 25%, at least what the line needs
    assert rows["s03"]["spoken_s"] == 0 and rows["s03"]["too_silent"]
    assert not rows["s04"]["too_silent"]  # a quick silent beat (2.5 s or less) is a cut, not a slow hold


def test_pacing_ignores_the_end_card_hold():
    from bsa.timeline import pacing

    s = _paced()
    s.shots[-1].min_s = 0.5
    s.lines.append(Line("l03", "c", "s04"))
    durations = {"l01": 2.4, "l02": 3.0, "l03": 2.0}
    plain = {r["shot"]: r for r in pacing(s, build_timeline(s, durations))}
    held = {r["shot"]: r for r in pacing(s, build_timeline(s, durations, end_card=True), end_card=True)}
    assert held["s04"]["seconds"] == pytest.approx(plain["s04"]["seconds"])
    assert held["s04"]["silent_s"] == pytest.approx(plain["s04"]["silent_s"])


def test_pacing_warnings_name_the_shot_and_a_length():
    from bsa.timeline import pacing_warnings

    s = _paced()
    warns = pacing_warnings(s, build_timeline(s, {"l01": 2.4, "l02": 3.0}))
    assert len(warns) == 2
    assert "s02" in warns[0] and "75% silent" in warns[0] and "min_s" in warns[0] and "3.8" in warns[0]
    assert "s03" in warns[1] and "no narration" in warns[1]
