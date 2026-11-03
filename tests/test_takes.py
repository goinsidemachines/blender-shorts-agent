"""Take mode's pure helpers: grouping lines into takes, finding pauses, matching cuts to line breaks, slicing."""

from __future__ import annotations

import itertools
import wave

import numpy as np

from bsa import closing, timeline
from bsa.narrate import contract, takes
from bsa.short import Line, Shot

SR = contract.SR
PEAK = 0.5


def tone(seconds: float, freq: float = 220.0, amp: float = PEAK) -> np.ndarray:
    t = np.arange(round(seconds * SR)) / SR
    return amp * np.sin(2 * np.pi * freq * t)


def silence(seconds: float, noise_dbfs: float | None = None, rng=None) -> np.ndarray:
    n = round(seconds * SR)
    if noise_dbfs is None:
        return np.zeros(n)
    return (rng or np.random.default_rng(0)).normal(0.0, 10 ** (noise_dbfs / 20), n)


def take(*parts) -> np.ndarray:
    return np.concatenate(parts)


def line(i: int, chars: int, shot: str = "s1") -> Line:
    return Line(id=f"l{i:02d}", text=("x" * (chars - 1) + ".")[:chars], shot=shot)


def shot(sid: str, **kw) -> Shot:
    return Shot(id=sid, title=sid, picture="p", scene="sc", **kw)


def covers(pieces, n: int) -> None:
    """The pieces tile [0, n) with no gap or overlap, in order."""
    assert pieces[0].start == 0
    assert pieces[-1].end == n
    for a, b in itertools.pairwise(pieces):
        assert a.end == b.start


# --- grouping -------------------------------------------------------------------------------------------------------


def test_take_id_counts_from_one():
    assert [takes.take_id(i) for i in (1, 2, 12)] == ["t01", "t02", "t12"]


def test_group_respects_take_chars_and_shot_holds():
    five = [line(i, 100) for i in range(1, 6)]
    assert [[ln.id for ln in g] for g in takes.group(five, [shot("s1", min_s=1.0)])] == [
        ["l01", "l02", "l03"],
        ["l04", "l05"],
    ]

    # a line longer than TAKE_CHARS is a take of its own
    long = [line(1, 50), line(2, 400), line(3, 50)]
    assert [[ln.id for ln in g] for g in takes.group(long, [shot("s1", min_s=1.0)])] == [["l01"], ["l02"], ["l03"]]

    # a shot with a hold ends the take at its boundary; one without does not
    held = [line(1, 40, "a"), line(2, 40, "b"), line(3, 40, "c")]
    shots = [shot("a", min_s=1.0, hold=True), shot("b", min_s=1.0), shot("c", min_s=1.0)]
    assert [[ln.id for ln in g] for g in takes.group(held, shots)] == [["l01"], ["l02", "l03"]]
    shots = [shot("a", min_s=1.0, hold=[[0.5, 1.0]]), shot("b", min_s=1.0), shot("c", min_s=1.0)]
    assert [[ln.id for ln in g] for g in takes.group(held, shots)] == [["l01"], ["l02", "l03"]]

    # a shot whose min_s is 2 s over its reading time (a long hold) ends the take; 0.5 s over does not
    text = "one two three four five"
    a = Line(id="l01", text=text, shot="a")
    b = Line(id="l02", text=text, shot="b")
    needed = timeline.reading_time(text) + timeline.LEAD_IN_S + timeline.TAIL_S
    long_hold = [shot("a", min_s=needed + 2.0), shot("b")]
    assert [[ln.id for ln in g] for g in takes.group([a, b], long_hold)] == [["l01"], ["l02"]]
    short_hold = [shot("a", min_s=needed + 0.5), shot("b")]
    assert [[ln.id for ln in g] for g in takes.group([a, b], short_hold)] == [["l01", "l02"]]

    # the closing line is never grouped
    with_closing = [line(1, 40), line(2, 40), Line(id=closing.LINE_ID, text="See you.", shot="s1")]
    assert [[ln.id for ln in g] for g in takes.group(with_closing, [shot("s1", min_s=1.0)])] == [
        ["l01", "l02"],
        [closing.LINE_ID],
    ]


def test_joined_uses_the_spoken_text():
    lines = [Line(id="l01", text="2 × 3", shot="s", say="two times three"), Line(id="l02", text="Six.", shot="s")]
    assert takes.joined(lines) == "two times three Six."


# --- pauses ---------------------------------------------------------------------------------------------------------


def test_pauses_finds_gaps_and_ignores_noise_and_edges():
    x = take(silence(0.4), tone(0.5), silence(0.3), tone(0.5), silence(0.2), tone(0.5), silence(0.05), tone(0.5),
             silence(0.5))  # fmt: skip
    found = takes.pauses(x)
    assert len(found) == 2
    (a0, a1), (b0, b1) = found
    assert round(0.9 * SR) <= a0 < a1 <= round(1.2 * SR)  # inside the 0.3 s gap
    assert (a1 - a0) >= 0.25 * SR
    assert round(1.7 * SR) <= b0 < b1 <= round(1.9 * SR)  # inside the 0.2 s gap
    assert all(b > a for a, b in found)

    # −60 dBFS noise in the gaps (and at the edges) is still pause; the edges are still not pauses
    rng = np.random.default_rng(0)
    noisy = take(silence(0.4, -60, rng), tone(0.5), silence(0.3, -60, rng), tone(0.5), silence(0.2, -60, rng),
                 tone(0.5), silence(0.05, -60, rng), tone(0.5), silence(0.5, -60, rng))  # fmt: skip
    assert len(takes.pauses(noisy)) == 2

    assert takes.pauses(np.zeros(SR)) == []
    assert takes.pauses(tone(1.0)) == []


def test_expected_shares_the_span_by_characters():
    assert takes.expected((1000, 5000), ["aaa", "a", "aaaa"]) == [2500, 3000]


# --- cutting --------------------------------------------------------------------------------------------------------


def test_cut_matches_pauses_to_breaks_in_order():
    # three equal lines, two clear 0.3 s gaps, and a 0.15 s dip in the middle of line 2
    x = take(silence(0.2), tone(1.0, 200), silence(0.3), tone(0.45, 300), silence(0.15), tone(0.4, 300),
             silence(0.3), tone(1.0, 400), silence(0.3))  # fmt: skip
    texts = ["a" * 40, "b" * 40, "c" * 40]
    ids = ["l01", "l02", "l03"]
    pieces = takes.cut(x, texts, ids)
    assert [p.line for p in pieces] == [0, 1, 2]
    assert all(p.reason is None for p in pieces)
    covers(pieces, len(x))
    gap1, gap2 = (round(1.2 * SR), round(1.5 * SR)), (round(2.5 * SR), round(2.8 * SR))
    assert gap1[0] <= pieces[0].end <= gap1[1]
    assert gap2[0] <= pieces[1].end <= gap2[1]


def test_cut_of_one_line_is_the_whole_take():
    x = take(silence(0.2), tone(1.0), silence(0.3))
    assert takes.cut(x, ["hello"], ["l01"]) == [takes.Piece(line=0, start=0, end=len(x), reason=None)]


def test_cut_uses_word_timings_when_given():
    # line 1: 4 words, 0.2-0.8 s; a real 0.3 s gap; line 2: "five" 1.1-1.9 s with a 0.15 s dip inside it, "six" after
    x = take(silence(0.2), tone(0.6, 200), silence(0.3), tone(0.6, 300), silence(0.15), tone(0.6, 300),
             silence(0.3))  # fmt: skip
    texts = ["one two three four", "five six"]
    ids = ["l01", "l02"]
    words = [("one", 0.2, 0.35), ("two", 0.35, 0.5), ("three", 0.5, 0.65), ("four", 0.65, 0.8),
             ("five", 1.1, 1.9), ("six", 1.9, 2.3)]  # fmt: skip
    real = (round(0.8 * SR), round(1.1 * SR))
    dip = (round(1.7 * SR), round(1.85 * SR))

    blind = takes.cut(x, texts, ids)  # the character estimate (~1.65 s) is nearer the dip
    assert dip[0] <= blind[0].end <= dip[1]

    heard = takes.cut(x, texts, ids, words=words)
    assert real[0] <= heard[0].end <= real[1]
    covers(heard, len(x))


def test_one_missing_pause_falls_back_only_the_lines_it_joins():
    # four equal lines; lines 2 and 3 run together with no pause
    x = take(silence(0.2), tone(0.8, 200), silence(0.3), tone(0.8, 300), tone(0.8, 350), silence(0.3),
             tone(0.8, 400), silence(0.3))  # fmt: skip
    texts = ["a" * 30, "b" * 30, "c" * 30, "d" * 30]
    ids = ["l01", "l02", "l03", "l04"]
    pieces = takes.cut(x, texts, ids)
    assert [p.line for p in pieces] == [0, 1, 2, 3]
    assert pieces[0].start == 0 and pieces[0].end is not None and pieces[0].reason is None
    assert pieces[3].start is not None and pieces[3].end == len(x) and pieces[3].reason is None
    for p in pieces[1:3]:
        assert p.start is None and p.end is None
        assert p.reason == "no clear pause between l02 and l03"


def test_cut_inside_a_word_is_a_fallback_with_its_reason():
    # two lines spoken straight on; the only quiet stretch is inside the word "seven"
    x = take(silence(0.2), tone(0.6, 200), tone(0.3, 300), silence(0.15), tone(0.3, 300), silence(0.3))
    texts = ["one two", "seven"]
    ids = ["l01", "l02"]
    words = [("one", 0.2, 0.5), ("two", 0.5, 0.8), ("seven", 0.8, 1.55)]
    pieces = takes.cut(x, texts, ids, words=words)
    assert [(p.start, p.end) for p in pieces] == [(None, None), (None, None)]
    assert {p.reason for p in pieces} == {"the only pause between l01 and l02 is inside a word"}

    # without the word timings the same take is cut there (nothing says the pause is inside a word)
    assert all(p.start is not None for p in takes.cut(x, texts, ids))


def test_cut_keeps_a_soft_onset_before_the_first_word():
    # each line opens with 60 ms at −45 dB below the peak (a soft consonant), then the loud vowel. The gaps are long
    # (0.8 s), short (0.3 s) and very short (0.08 s): the soft onset survives whole in every piece, and each piece
    # keeps at least min(LEAD_S, the pause) before its loud part.
    soft_amp = PEAK * 10 ** (-45 / 20)
    gaps = [0.8, 0.3, 0.08]
    parts = [silence(0.2)]
    soft_starts, loud_starts, loud_ends = [], [], []
    pos = round(0.2 * SR)
    for k in range(4):
        soft, loud = tone(0.06, 3000, soft_amp), tone(0.6, 200 + 50 * k)
        soft_starts.append(pos)
        loud_starts.append(pos + len(soft))
        loud_ends.append(pos + len(soft) + len(loud))
        parts += [soft, loud]
        pos += len(soft) + len(loud)
        if k < 3:
            gap = silence(gaps[k])
            parts.append(gap)
            pos += len(gap)
    parts.append(silence(0.3))
    x = take(*parts)
    texts = ["a" * 20] * 4
    ids = ["l01", "l02", "l03", "l04"]
    pieces = takes.cut(x, texts, ids)
    assert all(p.reason is None for p in pieces)
    win = round(takes.WINDOW_S * SR)
    for k, p in enumerate(pieces):
        assert p.start <= soft_starts[k], f"line {k + 1} lost its soft onset"
        assert p.end >= loud_ends[k], f"line {k + 1} lost its last sound"
        if k:
            pause = loud_starts[k] - loud_ends[k - 1]
            kept = loud_starts[k] - p.start
            assert kept >= min(takes.LEAD_S * SR, pause) - win, f"line {k + 1} keeps {kept / SR:.3f} s"
    assert loud_starts[1] - pieces[1].start >= round(takes.LEAD_S * SR) - win  # the long gap keeps the full lead


def test_word_gaps_is_none_when_whisper_heard_another_word_count():
    texts = ["one two", "three"]
    good = [("one", 0.0, 0.2), ("two", 0.25, 0.5), ("three", 0.9, 1.3)]
    assert takes.word_gaps(good, texts) == [(round(0.5 * SR), round(0.9 * SR))]
    assert takes.word_gaps(good[:2], texts) is None
    assert takes.word_gaps([*good, ("four", 1.4, 1.6)], texts) is None


def test_write_pieces_writes_contract_takes(tmp_path):
    x = take(silence(0.2), tone(0.8, 200), silence(0.3), tone(0.8, 300), tone(0.8, 350), silence(0.3),
             tone(0.8, 400), silence(0.3))  # fmt: skip
    ids = ["l01", "l02", "l03", "l04"]
    pieces = takes.cut(x, ["a" * 30] * 4, ids)
    secs = takes.write_pieces(x, pieces, ids, tmp_path)
    assert sorted(secs) == ["l01", "l04"]  # the uncut lines are spoken on their own later
    for i in ("l01", "l04"):
        path = tmp_path / f"{i}.wav"
        assert contract.check_take(path) == []
        with wave.open(str(path), "rb") as w:
            assert (w.getframerate(), w.getnchannels(), w.getsampwidth()) == (SR, 1, 2)
        assert abs(secs[i] - contract.wav_seconds(path)) < 0.001
    assert not (tmp_path / "l02.wav").exists()
    assert not list(tmp_path.glob("*.part"))
    p0 = pieces[0]
    assert secs["l01"] == round((p0.end - p0.start) / SR, 3)
