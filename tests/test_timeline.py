import itertools
import json
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
    timeline_from_json,
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


def test_captions_start_at_the_voice_onset_not_the_take_start():
    """A take that opens with silence: the line (and its audio) stays where it was, its captions wait for the voice."""
    lines = [Line("l01", "The pendulum swings. Then it stops.", "s01"), Line("l02", "Every time.", "s01")]
    plain = build_timeline(_short(lines), {"l01": 3.0, "l02": 1.5})
    tl = build_timeline(_short(lines), {"l01": 3.0, "l02": 1.5}, onsets={"l01": 0.5, "l02": 0.0})
    assert [(s.f0, s.f1) for s in tl.lines] == [(s.f0, s.f1) for s in plain.lines]
    assert tl.n_frames == plain.n_frames
    l01 = [c for c in tl.cards if c.line_id == "l01"]
    assert l01[0].f0 == tl.lines[0].f0 + 15 and l01[-1].f1 == tl.lines[0].f1  # 0.5 s at 30 fps
    assert [(c.f0, c.f1) for c in tl.cards if c.line_id == "l02"] == [
        (c.f0, c.f1) for c in plain.cards if c.line_id == "l02"]  # fmt: skip
    # an onset past the take's end never leaves a line without a caption
    late = build_timeline(_short(lines), {"l01": 3.0, "l02": 1.5}, onsets={"l01": 9.0})
    assert [c for c in late.cards if c.line_id == "l01"][-1].f0 == late.lines[0].f1


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


def test_timeline_round_trips_through_json():
    short = _short([Line("l01", "One two three four five six seven eight.", "s01")])
    tl = build_timeline(short, None, end_card=True)
    assert timeline_from_json(json.loads(json.dumps(to_json(tl)))) == tl
    with pytest.raises(ValueError):
        timeline_from_json({"fps": 30})


def test_lead_in_takes_put_captions_at_the_voice(tmp_path):
    import wave

    import numpy as np

    from bsa import jobstate
    from bsa.narrate import contract
    from bsa.narrate import run as nrun

    lines = [Line("l01", "The pendulum swings. Then it stops.", "s01"), Line("l02", "Every time.", "s01")]
    short = _short(lines)
    vo = tmp_path / "audio" / "vo"
    vo.mkdir(parents=True)
    for lid, sil in (("l01", 0.1), ("l02", 0.25)):
        t = np.arange(48000) / 48000
        x = np.concatenate([np.zeros(round(sil * 48000)), 9000 * np.sin(2 * np.pi * 220 * t)])
        with wave.open(str(vo / f"{lid}.wav"), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(48000)
            w.writeframes(x.astype("<i2").tobytes())
        contract.pad_lead(vo / f"{lid}.wav", 0.4)
    jobstate.mark_done(tmp_path, "narration", status="ok")
    onsets = nrun.takes_onsets(tmp_path, short)
    want = 0.4 - contract.ONSET_PREROLL_S
    assert onsets["l01"] == pytest.approx(want, abs=0.015) and onsets["l02"] == pytest.approx(want, abs=0.015)
    durs = nrun.takes_durations(tmp_path, short)
    plain = build_timeline(short, durs)
    tl = build_timeline(short, durs, onsets=onsets)
    assert [(s.f0, s.f1) for s in tl.lines] == [(s.f0, s.f1) for s in plain.lines]
    first = next(c for c in tl.cards if c.line_id == "l01")
    assert first.f0 == tl.lines[0].f0 + round(onsets["l01"] * tl.fps)


def test_length_warning_counts_extra_seconds():
    from bsa.timeline import Timeline, length_warning

    s, tl = _short([], length_s=45), Timeline(30, 40 * 30, [], [], [])
    assert length_warning(s, tl) is None
    assert length_warning(s, tl, extra_s=10) is None  # 50 s: still within 15% of 45 s
    msg = length_warning(s, tl, extra_s=15)  # the closing card's 15 s on top
    assert "about 55 s" in msg and "over" in msg


def _one_take(ats, seconds, lines_per_shot=1, text="Plain words here."):
    shots = [Shot(f"s{i + 1:02d}", "T", "pic", "Scene", at=a) for i, a in enumerate(ats)]
    lines = [Line(f"l{i + 1:02d}{chr(97 + k)}", text, s.id) for i, s in enumerate(shots) for k in range(lines_per_shot)]
    return Short(slug="t", title="T", length_s=seconds, lines=lines, shots=shots,
                 camera={"style": "one-take", "seconds": seconds})  # fmt: skip


def test_one_take_shots_are_the_windows_and_lines_start_in_them():
    from bsa.timeline import LEAD_IN_S, window_frames

    short = _one_take([0, 4, 9], 12, lines_per_shot=2)
    spans = {"s01": (1, 120), "s02": (121, 270), "s03": (271, 360)}
    assert window_frames(short) == spans
    lead = round(LEAD_IN_S * 30)
    long = {ln.id: 0.9 for ln in short.lines}  # s03's 3.0 s window needs 2.95 s
    tl = build_timeline(short, long)
    assert [(s.id, s.f0, s.f1) for s in tl.shots] == [(k, *v) for k, v in spans.items()]
    assert tl.n_frames == 360
    first_s02 = next(ln for ln in tl.lines if ln.id == "l02a")
    assert first_s02.f0 == 121 + lead and first_s02.f1 - first_s02.f0 + 1 == 27
    second_s02 = next(ln for ln in tl.lines if ln.id == "l02b")
    assert second_s02.f0 == first_s02.f1 + 1 + 11  # the usual gap (0.35 s)
    # every line sits in its window, with its cards
    for ln in tl.lines:
        f0, f1 = spans[ln.id[:3].replace("l", "s")]
        assert f0 <= ln.f0 <= ln.f1 <= f1
    assert {c.line_id for c in tl.cards} == {ln.id for ln in short.lines}
    # a window with time left holds: shorter lines never change a span
    short_takes = build_timeline(short, {ln.id: 0.5 for ln in short.lines})
    assert [(s.id, s.f0, s.f1) for s in short_takes.shots] == [(s.id, s.f0, s.f1) for s in tl.shots]
    # min_s is not used: a long min_s does not stretch a window
    for s in short.shots:
        s.min_s = 30.0
    assert [(s.f0, s.f1) for s in build_timeline(short, long).shots] == list(spans.values())


def test_redoing_the_narration_never_moves_a_window():
    short = _one_take([0, 4, 9], 12, lines_per_shot=2)
    a = build_timeline(short, {ln.id: 0.8 for ln in short.lines})
    b = build_timeline(short, {ln.id: 0.5 + 0.05 * i for i, ln in enumerate(short.lines)},
                       onsets={short.lines[0].id: 0.2})  # fmt: skip
    assert [(s.id, s.f0, s.f1) for s in a.shots] == [(s.id, s.f0, s.f1) for s in b.shots]
    assert a.n_frames == b.n_frames == 360
    assert [ln.f1 for ln in a.lines] != [ln.f1 for ln in b.lines]


def test_a_window_too_short_says_by_how_much_and_both_fixes():
    from bsa.timeline import WindowOverrun

    # s04 is 6.0 s; its one line is 6.4 s, so it needs 0.2 lead + 6.4 + 0.6 tail = 7.2 s
    short = _one_take([0, 2, 4, 6, 12], 16)
    durs = {ln.id: 1.0 for ln in short.lines}
    durs["l04a"] = 6.4
    with pytest.raises(WindowOverrun) as err:
        build_timeline(short, durs)
    assert str(err.value) == ("s04's lines need 7.2 s but its window is 6.0 s: shorten them or move s05's start "
                              "1.2 s later")  # fmt: skip
    assert isinstance(err.value, ValueError)
    # the last window: make the camera path longer
    short = _one_take([0, 2, 4, 6], 12)
    durs = {ln.id: 1.0 for ln in short.lines}
    durs["l04a"] = 6.4
    with pytest.raises(WindowOverrun) as err:
        build_timeline(short, durs)
    assert str(err.value).endswith("make camera.seconds 1.2 s longer")
    assert str(err.value) == ("s04's lines need 7.2 s but its window is 6.0 s: shorten them or make camera.seconds "
                              "1.2 s longer")  # fmt: skip
    # every overrun is reported, joined by "; "
    durs["l01a"] = 3.0
    with pytest.raises(WindowOverrun) as err:
        build_timeline(short, durs)
    parts = str(err.value).split("; ")
    assert len(parts) == 2 and parts[0].startswith("s01's lines need 3.8 s but its window is 2.0 s")
    # the end card's hold counts in the last window
    durs = {ln.id: 1.0 for ln in short.lines}
    durs["l04a"] = 4.8  # 0.2 + 4.8 + 0.6 = 5.6 s fits 6.0 s; with the 1.5 s end card it needs 7.1 s
    build_timeline(short, durs)
    with pytest.raises(WindowOverrun, match=r"s04's lines need 7\.1 s but its window is 6\.0 s"):
        build_timeline(short, durs, end_card=True)
    durs["l04a"] = 3.5  # 0.2 + 3.5 + 0.6 + 1.5 = 5.8 s: fits, and the end card never lengthens the short
    assert build_timeline(short, durs, end_card=True).n_frames == 360


def test_a_one_frame_overrun_never_says_zero_seconds():
    from bsa.timeline import WindowOverrun

    short = _one_take([0, 2], 4)  # s01's window is 60 frames; 6 lead + 37 + 18 tail = 61
    with pytest.raises(WindowOverrun) as err:
        build_timeline(short, {"l01a": 37 / 30, "l02a": 1.0})
    assert str(err.value) == ("s01's lines need 2.1 s but its window is 2.0 s: shorten them or move s02's start "
                              "0.1 s later")  # fmt: skip
    # the timeline it would have been rides on the error (the page still shows the storyboard)
    assert [(s.id, s.f0, s.f1) for s in err.value.timeline.shots] == [("s01", 1, 60), ("s02", 61, 120)]


def test_duplicate_shot_ids_never_collapse_windows():
    from bsa.timeline import window_frames

    short = _one_take([0, 2, 4], 6)
    short.shots[2].id = "s02"
    with pytest.raises(ValueError, match=r"shot id 's02' is used twice"):
        window_frames(short)
    with pytest.raises(ValueError, match=r"shot id 's02' is used twice"):
        build_timeline(short, None)


# to_json(build_timeline(...)) for cuts shorts as the base commit 0fdc655 built them, before one-take shorts.
PENDULUM_TIMELINE = {"fps": 30, "n_frames": 420, "shots": [{"id": "s01", "f0": 1, "f1": 120}, {"id": "s02", "f0": 121, "f1": 300}, {"id": "s03", "f0": 301, "f1": 420}], "lines": [{"id": "l01", "f0": 7, "f1": 73}, {"id": "l02", "f0": 127, "f1": 182}, {"id": "l03", "f0": 194, "f1": 260}, {"id": "l04", "f0": 307, "f1": 373}], "cards": [{"text": "A pendulum swings like", "line_id": "l01", "f0": 7, "f1": 52}, {"text": "clockwork.", "line_id": "l01", "f0": 53, "f1": 73}, {"text": "Length sets the beat.", "line_id": "l02", "f0": 127, "f1": 182}, {"text": "Four times longer,", "line_id": "l03", "f0": 194, "f1": 232}, {"text": "twice slower.", "line_id": "l03", "f0": 233, "f1": 260}, {"text": "Clocks kept time this way.", "line_id": "l04", "f0": 307, "f1": 373}]}
TWO_LINE_TIMELINE = {"fps": 30, "n_frames": 312, "shots": [{"id": "s01", "f0": 1, "f1": 117}, {"id": "s02", "f0": 118, "f1": 312}], "lines": [{"id": "l01", "f0": 7, "f1": 99}, {"id": "l02", "f0": 124, "f1": 165}], "cards": [{"text": "The weight swings down,", "line_id": "l01", "f0": 7, "f1": 64}, {"text": "then up again.", "line_id": "l01", "f0": 65, "f1": 99}, {"text": "Gravity pulls it back.", "line_id": "l02", "f0": 132, "f1": 165}]}


def test_cuts_timelines_are_unchanged():
    assert to_json(build_timeline(load_short(SAMPLE), None)) == PENDULUM_TIMELINE
    syn = Short(slug="t", title="T", length_s=10,
                shots=[Shot("s01", "A", "p", "S", min_s=2.0), Shot("s02", "B", "p", "S", min_s=5.0)],
                lines=[Line("l01", "The weight swings down, then up again.", "s01"),
                       Line("l02", "Gravity pulls it back.", "s02")])  # fmt: skip
    got = build_timeline(syn, {"l01": 3.1, "l02": 1.4}, end_card=True, onsets={"l02": 0.25})
    assert to_json(got) == TWO_LINE_TIMELINE
    syn.camera = {"style": "cuts", "approve": True}  # a cuts short asking for the camera stage: same timeline
    assert to_json(build_timeline(syn, {"l01": 3.1, "l02": 1.4}, end_card=True, onsets={"l02": 0.25})) == TWO_LINE_TIMELINE
