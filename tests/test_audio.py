import wave
from pathlib import Path

import numpy as np
import pytest

from bsa.audio import dsp, recorded
from bsa.audio.mix import BED_DBFS, DUCK_DB, PEAK_DBFS, VOICE_DBFS, _bed, mix, render_mix, write_wav
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


def test_mix_refuses_a_span_id_that_is_not_a_plain_file_name(tmp_path):
    outside = tmp_path / "x.wav"
    _write_take(outside, 0.5 * np.ones(SR))
    for bad in ("C:x", "../x", "a/b", "nul"):
        tl = Timeline(30, 300, [Span("s01", 1, 300)], [Span(bad, 31, 60)], [])
        with pytest.raises(ValueError, match="rename it"):
            mix(tl, _short([]), tmp_path / "vo")


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
    nz = np.flatnonzero(np.abs(out[:, 0]) > 1e-4)  # the effects bus is FFT convolution: float dust, not exact zeros
    assert np.abs(out[:expected]).max() < 1e-9  # causal: nothing sounds before the event
    assert expected <= nz[0] <= expected + 480  # a 3 ms fade-in, then the hit


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


def _speech_head_take(seconds: float = 0.5) -> tuple[np.ndarray, np.ndarray]:
    """(take, burst): 50 ms of a 2 kHz burst from sample 0, then a 300 Hz tone loud up to the last sample."""
    t = np.arange(round(seconds * SR)) / SR
    head = round(0.05 * SR)
    x = 0.3 * np.sin(2 * np.pi * 300 * t)
    x[:head] = 0.3 * np.sin(2 * np.pi * 2000 * t[:head])
    return x, x[:head].copy()


def _head_ratio_db(x: np.ndarray, at: int) -> float:
    """Energy of [at, at + 50 ms] over [at + 50 ms, at + 100 ms], in dB."""
    w = round(0.05 * SR)
    return float(10 * np.log10(np.sum(x[at : at + w] ** 2) / np.sum(x[at + w : at + 2 * w] ** 2)))


@pytest.mark.parametrize(("lead_s", "gap_s", "n_lines"), [(0.2, 0.35, 1), (0.2, 0.0, 2), (0.0, 0.35, 1)],
                         ids=["one-line", "back-to-back", "on-frame-1"])  # fmt: skip
def test_first_50_ms_of_speech_survive_the_mix(tmp_path, monkeypatch, lead_s, gap_s, n_lines):
    from bsa import timeline
    from bsa.audio.mix import _idx
    from bsa.short import Line

    monkeypatch.setattr(timeline, "LEAD_IN_S", lead_s)
    monkeypatch.setattr(timeline, "GAP_S", gap_s)
    take, burst = _speech_head_take()
    ids = [f"l{i}" for i in range(1, n_lines + 1)]
    for i in ids:
        _write_take(tmp_path / f"{i}.wav", take)
    short = Short(slug="t", title="T", length_s=3, shots=[Shot("s01", "Hook", "pic", "Scene")],
                  lines=[Line(i, "Words here", "s01") for i in ids])  # fmt: skip
    tl = build_timeline(short, {i: len(take) / SR for i in ids})
    out, _ = mix(tl, short, tmp_path)
    mono = out.mean(axis=1)
    if lead_s == 0:
        assert tl.lines[0].f0 == 1
    if gap_s == 0:
        assert tl.lines[1].f0 == tl.lines[0].f1 + 1  # back to back: the previous take is loud up to this line
    want = _head_ratio_db(take, 0)
    for span in tl.lines:
        at = _idx(span.f0, tl.fps)
        assert abs(_head_ratio_db(mono, at) - want) <= 1.0, (span.id, _head_ratio_db(mono, at), want)
        lo = max(at - round(0.1 * SR), 0)
        seg = mono[lo : at + round(0.15 * SR)]
        lag = lo + int(np.argmax(np.correlate(seg, burst, mode="valid")))
        assert abs(lag - at) <= 1, (span.id, lag, at)


# --------------------------------------------------------------------------- loudness, envelopes, bus, dynamics



def _room_library(tmp_path, seconds=3.0, kind="room", level=0.05, seed=0):
    """A cache with one steady noise clip tagged `kind`."""
    cache = tmp_path / ".assets-cache"
    rel = "sounds/p/roomtone.wav"
    (cache / rel).parent.mkdir(parents=True, exist_ok=True)
    _write_take(cache / rel, level * np.random.default_rng(seed).standard_normal(round(seconds * SR)) / 3)
    lib = recorded.Library(cache)
    lib.add([{"path": rel, "kinds": [kind], "asset": "sfx-test", "source": "Test pack", "license": "CC0",
              "name": "roomtone"}])  # fmt: skip
    lib.save()
    return recorded.Library(cache)


def test_k_weighting_meter_reads_a_full_scale_1khz_sine_as_minus_3_lufs():
    t = np.arange(SR) / SR
    assert dsp.momentary_lufs(np.sin(2 * np.pi * 997 * t)) == pytest.approx(-3.01, abs=0.1)
    assert dsp.integrated_lufs(np.sin(2 * np.pi * 997 * t)) == pytest.approx(-3.01, abs=0.1)
    assert dsp.momentary_lufs(np.zeros(100)) == dsp.FLOOR_DB
    quiet = 0.1 * np.sin(2 * np.pi * 997 * t)
    assert dsp.momentary_lufs(quiet) == pytest.approx(-23.01, abs=0.1)
    low = np.sin(2 * np.pi * 30 * t)  # K-weighting cuts the lows: same RMS, much quieter reading
    assert dsp.momentary_lufs(low) < -8


class _FakeLib:
    """Library stand-in: every clip is the same given samples."""

    def __init__(self, x):
        self.x = x

    def clips_for(self, kind):
        return [{"path": "p/x.wav", "kinds": [kind]}]

    def find(self, name):
        return {"path": name}

    def samples(self, clip):
        return self.x


def test_events_with_different_crest_factor_get_the_same_perceived_loudness():
    """A sharp knock (low RMS over its window, high peak) and steady noise at the same level_db: each event's
    momentary loudness in the bed is within 1 dB of the other (the old RMS-over-window scaling boosted the knock)."""
    t = np.arange(round(0.05 * SR)) / SR
    knock = np.concatenate([np.sin(2 * np.pi * 180 * t) * np.exp(-t / 0.008), np.zeros(round(0.5 * SR))])
    noise = shaped_noise(round(1.5 * SR), 100, 4000, seed=1)
    tl = _timeline(300)
    heard = {}
    for kind, src in (("thud", knock), ("rumble", noise)):
        sfx = [Sfx(kind, "s01", 1.0, 1.0, -20)]
        bed = _bed(tl, _short(sfx), 480000, None, _FakeLib(src), "any")
        heard[kind] = dsp.momentary_lufs(bed[:, 0])
    assert heard["thud"] == pytest.approx(heard["rumble"], abs=1.0)
    assert heard["rumble"] == pytest.approx(-20, abs=1.6)  # level_db is LUFS-ish, jitter is up to 1.5 dB


def test_a_lone_click_is_not_boosted_into_a_spike():
    click = np.zeros(SR)
    click[100] = 1.0
    out = dsp.level_to_lufs(click, -20.0, max_crest_db=26.0)
    assert np.abs(out).max() <= 10 ** (6 / 20) + 1e-9  # peak at most level_db + 26 dB = +6 dBFS


def test_transient_fades_in_and_rings_past_dur_s():
    x = np.exp(-np.arange(2 * SR) / (0.3 * SR)) * np.sin(2 * np.pi * 200 * np.arange(2 * SR) / SR)
    y = recorded.shape(x, 0.3, "impact")
    n = round(0.3 * SR)
    assert y[0] == 0 and abs(y[round(0.0015 * SR)]) < abs(x[round(0.0015 * SR)])  # fade-in over the first 3 ms
    assert round(0.3 * SR) < len(y) <= n + round(recorded.TAIL_S * SR)  # the tail is kept, up to 0.6 s
    assert np.abs(y[n : n + 480]).max() > 0.1 * np.abs(x[:n]).max()  # not chopped at dur_s
    assert abs(y[-1]) < 1e-6 and np.abs(np.diff(y)).max() < 0.05  # smooth end, no steps
    short = recorded.shape(x[:2000], 0.3, "click")  # a clip shorter than dur_s keeps its length, ends on a fade
    assert len(short) == 2000 and abs(short[-1]) < 1e-3


def test_sustained_fades_in_and_out_inside_dur_s():
    x = np.ones(2 * SR)
    y = recorded.shape(x, 2.0, "rumble")
    assert len(y) == 2 * SR and y[0] == 0 and y[-1] == pytest.approx(0, abs=1e-3)
    assert y[round(0.15 * SR)] == pytest.approx(np.sin(np.pi / 4), abs=0.01)  # equal power: half way in = -3 dB
    assert y[SR] == 1.0
    short = recorded.shape(np.ones(SR), 0.3, "wind")  # fades capped at a third of dur_s
    n = round(0.3 * SR)
    assert len(short) == n and short[n // 3 : n - n // 3].min() == pytest.approx(1.0)


def test_loop_is_seamless_seeded_and_exact_length():
    src = shaped_noise(SR * 3, 100, 3000, seed=4) * 0.1
    a = dsp.loop_to_length(src, SR * 20, seed=1)
    assert len(a) == SR * 20 and np.array_equal(a, dsp.loop_to_length(src, SR * 20, seed=1))
    assert not np.array_equal(a, dsp.loop_to_length(src, SR * 20, seed=2))  # another start
    assert np.abs(np.diff(a)).max() < 4 * np.abs(np.diff(src)).max()  # no step at any join
    blocks = a[: 19 * SR].reshape(19, SR)
    rms = np.sqrt((blocks**2).mean(axis=1))
    assert rms.max() / rms.min() < 1.25  # equal-power joins: no dip or bump
    assert len(dsp.loop_to_length(src, SR)) == SR  # a clip longer than needed is just cut


def test_room_tone_floor_means_no_digital_silence_in_the_story(tmp_path):
    """Sparse effects and a pause between lines: with a cached room clip the 50 ms RMS never drops below -70 dBFS
    inside the story (past the 0.5 s fades at each end)."""
    t = np.arange(SR) / SR
    _write_take(tmp_path / "l1.wav", 0.5 * np.sin(2 * np.pi * 220 * t))
    lib = _room_library(tmp_path)
    tl = Timeline(30, 600, [Span("s01", 1, 600)], [Span("l1", 31, 90)], [])
    sfx = [Sfx("impact", "s01", 6.0, 0.4, -18), Sfx("rumble", "s01", 12.0, 1.0, -24)]
    out, _ = mix(tl, _short(sfx), tmp_path, library=lib)
    mono = out.mean(axis=1)
    w = round(0.05 * SR)
    frames = mono[: len(mono) // w * w].reshape(-1, w)
    rms_db = 20 * np.log10(np.sqrt((frames**2).mean(axis=1)) + 1e-12)
    inner = rms_db[round(0.5 / 0.05) : -round(0.5 / 0.05)]
    assert inner.min() > -70, inner.min()
    nofloor, _ = mix(tl, _short(sfx), tmp_path)  # without the clip the gaps are silent
    gaps = nofloor[16 * SR : 18 * SR]
    assert np.abs(gaps).max() < 1e-9


def test_room_tone_level_fades_not_ducked_and_off_switch(tmp_path):
    t = np.arange(3 * SR) / SR
    _write_take(tmp_path / "l1.wav", 0.5 * np.sin(2 * np.pi * 440 * t))  # speech from 1.0 s to 4.0 s
    lib = _room_library(tmp_path)
    tl = _timeline(300)
    room_only, _ = mix(tl, _short([]), None, library=lib, room_db=-50)
    mono = room_only.mean(axis=1)
    mid = mono[3 * SR : 8 * SR]
    assert dsp.integrated_lufs(room_only[3 * SR : 8 * SR, 0]) == pytest.approx(-50, abs=1.5)
    assert np.abs(mono[:200]).max() < 0.2 * np.abs(mid).max()  # faded in over 0.5 s ...
    assert np.abs(mono[-200:]).max() < 0.2 * np.abs(mid).max()  # ... and out
    with_voice, _ = mix(tl, _short([]), tmp_path, library=lib, room_db=-50)
    voice_only, _ = mix(tl, _short([]), tmp_path)
    diff = (with_voice - voice_only)[:, 0]
    during = np.sqrt(np.mean(diff[2 * SR : 3 * SR] ** 2))
    after = np.sqrt(np.mean(diff[6 * SR : 7 * SR] ** 2))
    assert during == pytest.approx(after, rel=0.15)  # the floor does not duck under speech
    off, _ = mix(tl, _short([]), None, library=lib, room_db=None)
    assert np.abs(off).max() == 0
    quieter, _ = mix(tl, _short([]), None, library=lib, room_db=-60)
    assert np.abs(quieter).max() < np.abs(room_only).max() / 2


def test_room_tone_stops_with_the_story_and_stays_off_the_closing_card(tmp_path):
    lib = _room_library(tmp_path)
    out, _ = mix(_timeline(300), _short([]), None, library=lib, closing=(60, None, None))
    n_story = 300 / 30 * SR
    assert out.shape == (round(360 / 30 * SR), 2)
    assert np.abs(out[round(n_story) :]).max() == 0
    assert np.abs(out[round(n_story) - 4800 : round(n_story) - 2400]).max() > 0


def test_a_designed_room_clip_is_not_used_under_natural_only(tmp_path):
    lib = _room_library(tmp_path)
    lib.clips[0]["designed"] = True
    assert recorded.room_clip(lib, "natural-only") is None and recorded.room_clip(lib, "any") is not None
    out, _ = mix(_timeline(300), _short([]), None, library=lib, policy="natural-only")
    assert np.abs(out).max() == 0


def test_room_is_not_an_sfx_event():
    with pytest.raises(ValueError, match="sound.room_db"):
        render_mix(_timeline(), _short([Sfx("room", "s01", 0, 1, -30)]), None)


def test_reverb_adds_a_short_tail_and_can_be_turned_off():
    tl = _timeline(300)
    short = _short([Sfx("click", "s01", 1.0, 0.08, -18)])
    wet, _ = mix(tl, short, None, reverb=True)
    dry, _ = mix(tl, short, None, reverb=False)
    st = SR  # the click
    tail = slice(st + round(0.25 * SR), st + round(0.45 * SR))
    assert np.abs(dry[tail]).max() < 1e-4  # a click has no tail of its own
    assert np.abs(wet[tail]).max() > 1e-3  # the shared room rings
    assert np.abs(wet[tail, 0] - wet[tail, 1]).max() > 0  # decorrelated channels: some width
    assert np.abs(wet[round(st + 1.2 * SR) :]).max() < 1e-3 * np.abs(wet).max()  # and it dies away (RT60 ~0.35 s)


def test_reverb_wet_level_and_highpass():
    ir = dsp.room_ir(0)
    assert 10 * np.log10(np.sum(ir[0] ** 2)) == pytest.approx(-16, abs=0.1)
    assert not np.array_equal(ir[0], ir[1])
    assert np.abs(ir[0][: round(0.007 * SR)]).max() == 0  # pre-delay
    n = 4 * SR
    t = np.arange(n) / SR
    bed = np.stack([np.sin(2 * np.pi * f * t) for f in (30, 1000)], 1)  # channel 0 low, channel 1 mid
    out = dsp.effects_bus(bed, reverb=False)
    rms = np.sqrt((out[SR:] ** 2).mean(axis=0)) / np.sqrt((bed[SR:] ** 2).mean(axis=0))
    assert rms[0] < 0.25 and rms[1] == pytest.approx(1.0, abs=0.03)  # 30 Hz is cut (>12 dB), 1 kHz is not
    assert dsp.effects_bus(np.zeros((100, 2))).shape == (100, 2)


def test_extra_bed_goes_through_the_bus_too():
    extra = np.zeros((480000, 2))
    extra[SR : SR + 4800, 0] = 0.5  # one 0.1 s step
    wet, _ = mix(_timeline(300), _short([]), None, extra_bed=extra, reverb=True)
    dry, _ = mix(_timeline(300), _short([]), None, extra_bed=extra, reverb=False)
    tail = slice(SR + round(0.2 * SR), SR + round(0.4 * SR))
    assert np.abs(wet[tail]).max() > 5 * np.abs(dry[tail]).max()


def test_duck_gain_dips_before_speech_and_recovers_slowly():
    speech = np.zeros(6 * SR, dtype=bool)
    speech[2 * SR : 4 * SR] = True
    g = dsp.duck_gain(speech, DUCK_DB)
    floor = 10 ** (DUCK_DB / 20)
    assert g[round(1.9 * SR)] == 1.0  # nothing before the lookahead
    assert g[2 * SR] < 10 ** (-3 / 20)  # already dipping (>3 dB) when the line starts
    assert g[round(2.0 * SR) - 1] > g[2 * SR + 2400] > g[3 * SR]  # monotone down
    assert g[3 * SR] == pytest.approx(floor, abs=0.01)
    assert g[4 * SR] < 0.4  # release is slow: 0 ms after speech ends it is still down
    assert 0.5 < g[round(4.4 * SR)] < 0.8  # ~0.4 s time constant
    assert g[5 * SR + SR // 2] > 0.9
    assert np.all(np.diff(g[: 2 * SR]) <= 1e-12)
    assert DUCK_DB == -10
    assert np.array_equal(dsp.duck_gain(np.zeros(100, dtype=bool), -10), np.ones(100))


def test_limiter_holds_the_ceiling_without_touching_quiet_material():
    quiet = 0.3 * shaped_noise(SR, 100, 4000, seed=2)
    quiet *= 0.5 / np.abs(quiet).max()
    assert dsp.limit(quiet, -1.0) is quiet or np.array_equal(dsp.limit(quiet, -1.0), quiet)
    rng = np.random.default_rng(0)
    x = np.stack([0.1 * rng.standard_normal(2 * SR)] * 2, 1)
    x[SR : SR + 20] *= 12  # a loud burst well over the ceiling
    y = dsp.limit(x, -1.0)
    assert np.abs(y).max() <= 10 ** (-1 / 20) + 1e-9
    assert np.array_equal(y[: SR - 2400], x[: SR - 2400])  # untouched well before the burst
    gain = np.abs(y[:, 0]) / np.maximum(np.abs(x[:, 0]), 1e-12)
    start = np.flatnonzero(gain[: SR + 20] < 0.97)[0]
    assert SR - 480 < start < SR - 100  # gain starts to fall about 5 ms ahead
    smooth = np.convolve(gain, np.ones(5) / 5, "same")  # a gain envelope, no hard steps in it
    assert np.abs(np.diff(smooth[SR - 3000 : SR + 9000])).max() < 0.01
    assert gain[SR + 20 + 24000] > 0.97  # released within ~0.1 s time constant, 0.5 s later
    # transparency: a limited sample is a scaled copy of the original (no harmonic distortion from clipping)
    assert np.allclose(y[SR : SR + 20, 0] / x[SR : SR + 20, 0], y[SR, 0] / x[SR, 0], rtol=0.5)


def test_limiter_is_fast_enough():
    import time

    x = np.random.default_rng(1).standard_normal((60 * SR, 2)) * 0.4
    t0 = time.time()
    y = dsp.limit(x, -1.0)
    assert time.time() - t0 < 3.0 and np.abs(y).max() <= 10 ** (-1 / 20) + 1e-9


def test_sliding_min_matches_brute_force():
    r = np.random.default_rng(5).random(1000)
    for w in (1, 2, 11, 50):
        assert np.allclose(dsp._sliding_min(r, w), [r[i : i + w].min() for i in range(len(r) - w + 1)])


def test_sixty_second_mix_is_fast(tmp_path):
    import time

    t = np.arange(40 * SR) / SR
    _write_take(tmp_path / "l1.wav", 0.4 * np.sin(2 * np.pi * 180 * t) * (np.sin(2 * np.pi * 3 * t) > 0))
    tl = Timeline(30, 1800, [Span("s01", 1, 1800)], [Span("l1", 31, 1230)], [])
    sfx = [Sfx("impact", "s01", 2.0 * i, 0.4, -18) for i in range(25)] + [Sfx("rumble", "s01", 10, 8, -26)]
    lib = _room_library(tmp_path, seconds=5)
    t0 = time.time()
    out, lv = mix(tl, _short(sfx), tmp_path, library=lib)
    assert time.time() - t0 < 8.0
    assert out.shape == (60 * SR, 2) and lv["peak_dbfs"] <= PEAK_DBFS + 1e-6
