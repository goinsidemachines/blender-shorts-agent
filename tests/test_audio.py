import wave
from pathlib import Path

import numpy as np
import pytest

from bsa.audio.mix import BED_DBFS, DUCK_DB, PEAK_DBFS, VOICE_DBFS, mix, render_mix, write_wav
from bsa.audio.sfx import KINDS, SR, shaped_noise
from bsa.short import Sfx, Short, Shot, load_short
from bsa.timeline import Span, Timeline, build_timeline

SAMPLE = Path(__file__).parent / "e2e" / "pendulum" / "source" / "short.json"


def _timeline(n_frames=300):
    return Timeline(30, n_frames, [Span("s01", 1, n_frames)], [Span("l1", 31, 60)], [])


def _short(sfx):
    return Short(slug="t", title="T", length_s=10, shots=[Shot("s01", "Hook", "pic", "Scene")], sfx=sfx)


def _write_take(path: Path, x: np.ndarray) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((x * 32767).astype(np.int16).tobytes())


def test_shaped_noise_unit_rms_and_band():
    x = shaped_noise(SR * 2, 1000, 4000, tilt_db_oct=-3, seed=3)
    assert np.sqrt(np.mean(x**2)) == pytest.approx(1.0, abs=1e-6)
    p = np.abs(np.fft.rfft(x)) ** 2
    f = np.fft.rfftfreq(len(x), 1 / SR)
    assert p[f < 500].sum() / p.sum() < 0.01


def test_shaped_noise_is_deterministic():
    assert np.array_equal(shaped_noise(1000, 100, 2000, seed=5), shaped_noise(1000, 100, 2000, seed=5))


def test_mix_length_exact():
    out = render_mix(_timeline(300), _short([Sfx("whoosh", "s01", 0.2, 0.8, -18)]), None)
    assert out.shape == (480000, 2)
    assert np.abs(out).max() <= 1.0


def test_levels(tmp_path):
    t = np.arange(SR) / SR
    _write_take(tmp_path / "l1.wav", 0.5 * np.sin(2 * np.pi * 440 * t))
    tl = _timeline(300)  # line l1 starts at frame 31 = 1.0 s
    _, lv = mix(tl, _short([Sfx("whoosh", "s01", 0.2, 0.8, -18)]), tmp_path)
    assert lv["voice_dbfs"] == pytest.approx(VOICE_DBFS, abs=1.0)
    assert lv["peak_dbfs"] <= PEAK_DBFS


def test_captions_only_mix_has_no_voice():
    sfx = [Sfx("whoosh", "s01", 0.2, 0.8, -18), Sfx("impact", "s01", 4.0, 0.6, -12)]
    _, lv = mix(_timeline(300), _short(sfx), None)
    assert lv["voice_dbfs"] is None
    assert lv["bed_dbfs"] == pytest.approx(BED_DBFS, abs=1.0)
    assert lv["peak_dbfs"] <= PEAK_DBFS


def test_missing_take_is_skipped(tmp_path):
    out = render_mix(_timeline(300), _short([Sfx("blip", "s01", 1, 0.12, -10)]), tmp_path)
    assert out.shape == (480000, 2)


def test_unknown_sfx_kind_rejected():
    with pytest.raises(ValueError) as e:
        render_mix(_timeline(), _short([Sfx("kazoo", "s01", 0, 1, -10)]), None)
    for kind in KINDS:
        assert kind in str(e.value)


def test_all_kinds_render_and_are_deterministic():
    for kind, fn in KINDS.items():
        a, b = fn(0.5, seed=1), fn(0.5, seed=1)
        assert len(a) == round(0.5 * SR) and np.isfinite(a).all(), kind
        assert np.array_equal(a, b), kind


def test_extra_bed_is_mixed_in():
    extra = np.zeros((480000, 2))
    extra[:, 0] = np.sin(np.linspace(0, 2000, 480000))
    _, lv = mix(_timeline(300), _short([]), None, extra_bed=extra)
    assert lv["bed_dbfs"] == pytest.approx(BED_DBFS, abs=1.0)


def test_sample_short_mixes_and_writes(tmp_path):
    tl = build_timeline(load_short(SAMPLE), None)
    out = render_mix(tl, load_short(SAMPLE), None)
    assert out.shape == (round(tl.n_frames / tl.fps * SR), 2)
    write_wav(tmp_path / "mix.wav", out)
    with wave.open(str(tmp_path / "mix.wav"), "rb") as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()) == (2, 2, 48000, len(out))


def test_sfx_lands_at_shot_start_plus_at_s():
    tl = Timeline(30, 300, [Span("s01", 1, 100), Span("s02", 101, 300)], [], [])
    short = Short(
        slug="t", title="T", length_s=10,
        shots=[Shot("s01", "A", "pic", "x"), Shot("s02", "B", "pic", "x")],
        sfx=[Sfx("impact", "s02", 0.5, 0.6, -10)],
    )
    out = render_mix(tl, short, None)
    expected = round(100 / 30 * SR) + round(0.5 * SR)
    nz = np.flatnonzero(np.abs(out[:, 0]) > 0)
    assert np.all(out[:expected] == 0)
    assert expected <= nz[0] <= expected + 200


def test_negative_start_rejected():
    with pytest.raises(ValueError, match="before the video"):
        render_mix(_timeline(), _short([Sfx("blip", "s01", -1.0, 0.12, -10)]), None)


def test_bed_ducks_8_db_under_speech(tmp_path):
    t = np.arange(3 * SR) / SR
    _write_take(tmp_path / "l1.wav", 0.5 * np.sin(2 * np.pi * 440 * t))  # speech 1.0 s to 4.0 s
    steady = shaped_noise(480000, 200, 4000, seed=9)
    tl = _timeline(300)
    short = _short([])
    bed_only = render_mix(tl, short, None, extra_bed=steady)
    mixed = render_mix(tl, short, tmp_path, extra_bed=steady)
    voice_only = render_mix(tl, short, tmp_path)
    residual = (mixed - voice_only)[:, 0]  # the ducked bed, up to small limiter effects

    def rms(x, a, b):
        return np.sqrt(np.mean(x[int(a * SR) : int(b * SR)] ** 2))

    def rel_db(a, b):
        return 20 * np.log10(rms(residual, a, b) / rms(bed_only[:, 0], a, b))

    assert rel_db(6.0, 9.0) == pytest.approx(0.0, abs=0.7)  # clear of speech: untouched
    assert rel_db(2.0, 3.0) == pytest.approx(DUCK_DB, abs=1.5)  # deep in speech: ducked 8 dB
    assert rel_db(0.5, 0.7) == pytest.approx(0.0, abs=0.7)  # before the 0.25 s ramp starts
    assert rel_db(1.2, 1.4) == pytest.approx(DUCK_DB, abs=1.5)  # ramp finished


def test_bad_take_format_rejected(tmp_path):
    for rate, ch in ((44100, 1), (SR, 2)):
        with wave.open(str(tmp_path / "l1.wav"), "wb") as w:
            w.setnchannels(ch)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes(np.zeros(1000 * ch, dtype="<i2").tobytes())
        with pytest.raises(ValueError, match="l1.wav"):
            render_mix(_timeline(), _short([]), tmp_path)


def test_levels_measure_what_the_targets_mean(tmp_path):
    """A real short: speech with pauses inside each line, and a few short effects in a long, mostly quiet bed. The
    voice is measured while speaking (not over the pauses), the bed where it sounds (not over the silence)."""
    t = np.arange(SR) / SR
    word = 0.5 * np.sin(2 * np.pi * 220 * t[: SR // 2])
    gap = np.zeros(SR // 3)
    _write_take(tmp_path / "l1.wav", np.concatenate([word, gap, word, gap, word]))
    tl = Timeline(30, 480, [Span("s01", 1, 480)], [Span("l1", 31, 90)], [])
    sfx = [Sfx("blip", "s01", 0.3, 0.12, -20), Sfx("whoosh", "s01", 9.0, 0.6, -20), Sfx("creak", "s01", 12.0, 0.6, -24)]
    out, lv = mix(tl, _short(sfx), tmp_path)
    assert lv["voice_dbfs"] == pytest.approx(VOICE_DBFS, abs=1.0)
    assert lv["bed_dbfs"] == pytest.approx(BED_DBFS, abs=1.5)
    assert lv["peak_dbfs"] <= PEAK_DBFS + 1e-6
    mono = out.mean(axis=1)
    effect = mono[9 * SR : int(9.6 * SR)]  # the whoosh, in the clear: sparse effects are not boosted over the voice
    assert 20 * np.log10(np.sqrt(np.mean(effect**2))) < VOICE_DBFS - 4
