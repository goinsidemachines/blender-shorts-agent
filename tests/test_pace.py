"""The speech-rate guard: a line far off the short's own pace was probably cut at the wrong place."""

import json
import wave
from pathlib import Path

import numpy as np
import pytest

from bsa.narrate import pace
from bsa.narrate import run as nrun
from bsa.short import Line, Short

SR = 48000
TEXT = "Some words to say here."  # 18 letters
RNG = np.random.default_rng(3)


def write_speech(path: Path, speech_s: float, quiet_s: float = 0.3) -> None:
    """A take: noise bursts for speech between two silences."""
    gap = np.zeros(int(SR * quiet_s))
    burst = RNG.normal(0, 0.2, int(SR * speech_s))
    x = np.concatenate([gap, burst, gap])
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((np.clip(x, -1, 1) * 32767).astype("<i2").tobytes())


def make_takes(tmp_path: Path, seconds: dict[str, float]) -> dict[str, str]:
    for lid, s in seconds.items():
        write_speech(tmp_path / f"{lid}.wav", s)
    return {lid: TEXT for lid in seconds}


def test_letters_count_letters_and_digits_only():
    assert pace.letters("It lifts 2 tons, fast!") == 16


def test_speech_seconds_trims_the_quiet_at_both_ends(tmp_path):
    write_speech(tmp_path / "a.wav", 1.5, quiet_s=0.7)
    assert pace.speech_seconds(tmp_path / "a.wav") == pytest.approx(1.5, abs=0.05)


def test_speech_seconds_is_none_for_a_missing_take(tmp_path):
    assert pace.speech_seconds(tmp_path / "nope.wav") is None


def test_an_even_short_flags_nothing(tmp_path):
    texts = make_takes(tmp_path, {"l01": 1.4, "l02": 1.5, "l03": 1.6, "l04": 1.5, "l05": 1.45})
    assert pace.outliers(pace.rates(tmp_path, texts)) == {}


def test_a_line_that_lost_its_words_but_kept_the_time_is_slow(tmp_path):
    texts = make_takes(tmp_path, {"l01": 1.5, "l02": 1.5, "l03": 3.5, "l04": 1.5, "l05": 1.5})
    found = pace.outliers(pace.rates(tmp_path, texts))
    assert list(found) == ["l03"] and found["l03"][1] == "slow" and found["l03"][0] < pace.RATE_LOW


def test_a_line_that_gained_words_is_fast(tmp_path):
    texts = make_takes(tmp_path, {"l01": 1.5, "l02": 1.5, "l03": 0.8, "l04": 1.5, "l05": 1.5})
    found = pace.outliers(pace.rates(tmp_path, texts))
    assert list(found) == ["l03"] and found["l03"][1] == "fast" and found["l03"][0] > pace.RATE_HIGH


def test_a_very_short_line_is_never_judged(tmp_path):
    texts = make_takes(tmp_path, {"l01": 1.5, "l02": 1.5, "l03": 1.5, "l04": 1.5, "l05": 4.0})
    texts["l05"] = "Now."
    assert pace.outliers(pace.rates(tmp_path, texts)) == {}


def test_too_few_lines_have_no_median_to_compare_with(tmp_path):
    texts = make_takes(tmp_path, {"l01": 1.5, "l02": 4.0})
    assert pace.outliers(pace.rates(tmp_path, texts)) == {}


# --- in narration -----------------------------------------------------------------------------------------------

IDS = ["l01", "l02", "l03", "l04", "l05"]
KOKORO = {"narrator": {"provider": "kokoro", "voice": "af_heart", "speed": 1.0, "verify": False}}


def short5() -> Short:
    return Short(slug="t", title="T", length_s=10, lines=[Line(id=i, text=TEXT, shot="s1") for i in IDS])


def spoke(tmp_path, scripts: dict[str, list[float]], hear=None):
    """Run _spoken with a fake voice: each call for a line writes its next scripted speech length."""
    vo = tmp_path / "vo"
    calls: list[tuple[list[str], int]] = []

    def speak(lines, seed):
        calls.append(([ln.id for ln in lines], seed))
        out = {}
        for ln in lines:
            s = scripts[ln.id].pop(0) if len(scripts[ln.id]) > 1 else scripts[ln.id][0]
            write_speech(vo / f"{ln.id}.wav", s)
            out[ln.id] = s + 0.6
        return out

    check = hear is not None
    res = nrun._spoken(short5(), vo, speak, hear or (lambda lns: (None, nrun.WHISPER_MISSING)), check)
    return res, calls, vo


def even(**over):
    return {i: [1.5] for i in IDS} | {k: v for k, v in over.items()}


def test_a_rate_outlier_is_spoken_again_alone_once(tmp_path):
    res, calls, vo = spoke(tmp_path, even(l03=[3.5, 1.5]))
    assert calls == [(IDS, 7), (["l03"], 8)]
    assert "l03 sounds too slow" in res.message and "spoken again on its own." in res.message
    heard = json.loads((vo / "heard.json").read_text(encoding="utf-8"))
    assert heard["pace"]["l03"]["respoken"] is True and heard["pace"]["l03"]["still"] is False
    assert nrun.take_seeds(vo)["l03"] == 8


def test_a_rate_outlier_that_stays_bad_is_reported_not_looped(tmp_path):
    res, calls, vo = spoke(tmp_path, even(l03=[3.5, 3.5]))
    assert calls == [(IDS, 7), (["l03"], 8)]
    assert "still sounds too slow" in res.message
    heard = json.loads((vo / "heard.json").read_text(encoding="utf-8"))
    assert heard["pace"]["l03"]["still"] is True


def test_the_guard_works_while_whisper_is_missing_and_says_nothing_when_all_is_even(tmp_path):
    res, calls, vo = spoke(tmp_path, even())
    assert calls == [(IDS, 7)] and res.message is None
    assert "pace" not in json.loads((vo / "heard.json").read_text(encoding="utf-8"))


def test_with_whisper_the_respoken_line_is_heard_again(tmp_path):
    heard_calls = []

    def hear(lines):
        heard_calls.append([ln.id for ln in lines])
        return {ln.id: {"said": TEXT, "wer": 0.0} for ln in lines}, None

    res, calls, _ = spoke(tmp_path, even(l03=[3.5, 1.5]), hear=hear)
    assert heard_calls == [IDS, ["l03"]] and calls[-1] == (["l03"], 8)
    assert "spoken again on its own." in res.message


def test_without_retries_the_outlier_is_only_reported(tmp_path):
    vo = tmp_path / "vo"
    scripts = even(l03=[3.5])
    calls = []

    def speak(lines, seed):
        calls.append(seed)
        for ln in lines:
            write_speech(vo / f"{ln.id}.wav", scripts[ln.id][0])
        return {ln.id: 2.0 for ln in lines}

    res = nrun._spoken(short5(), vo, speak, lambda lns: (None, "x"), False, retry=False)
    assert calls == [7] and "listen to it or retake it" in res.message
