"""The loudness master: measurement against references (BS.1770-4 / R128, Tech 3342), the settings group and its
resolution, and the mastering step."""

import numpy as np
import pytest

from bsa import master, presets, settings
from bsa.audio import dsp
from bsa.audio import master as amaster
from bsa.audio.sfx import SR
from bsa.short import Short, validate


def _sine(freq, seconds, amp, phase=0.0):
    t = np.arange(round(seconds * SR)) / SR
    return amp * np.sin(2 * np.pi * freq * t + phase)


def _stereo(x):
    return np.stack([x, x], 1)


def _noise(seconds, rms, seed=0):
    x = np.random.default_rng(seed).standard_normal(round(seconds * SR))
    return x / np.sqrt(np.mean(x**2)) * rms


# ------------------------------------------------------------------ measurement references


def test_stereo_1khz_sine_reads_its_level():
    # BS.1770: K-weighting is +0.69 dB at 1 kHz, which the -0.691 offset cancels, so a 1 kHz sine of peak P dBFS in both
    # channels reads P LUFS (EBU Tech 3341 test: -23 dBFS peak per channel = -23.0 LUFS), and in one channel P - 3.01.
    x = _stereo(_sine(1000, 10, 10 ** (-23 / 20)))
    assert dsp.gated_lufs(x) == pytest.approx(-23.0, abs=0.1)
    assert dsp.gated_lufs(_stereo(_sine(1000, 10, 1.0))) == pytest.approx(0.0, abs=0.1)
    assert dsp.gated_lufs(_sine(1000, 10, 1.0)) == pytest.approx(-3.01, abs=0.1)


def test_mono_counts_as_one_channel_and_stereo_adds_both():
    mono = _sine(1000, 10, 0.1)
    assert dsp.gated_lufs(_stereo(mono)) - dsp.gated_lufs(mono) == pytest.approx(3.01, abs=0.02)


def test_gating_ignores_silence_and_quiet_parts():
    loud = _sine(1000, 10, 10 ** (-23 / 20))
    plain = dsp.gated_lufs(_stereo(loud))
    padded = np.concatenate([loud, np.zeros(10 * SR), _sine(1000, 10, 10 ** (-80 / 20))])  # silence + a -80 dB part
    assert dsp.gated_lufs(_stereo(padded)) == pytest.approx(plain, abs=0.1)  # under the -70 abs and -10 LU gates
    assert dsp.gated_lufs(np.zeros((SR, 2))) == dsp.FLOOR_DB


def test_true_peak_sees_the_intersample_peak():
    # fs/4 at 45 degrees: every sample is +-0.7071 but the waveform reaches 1.0: +3 dB over the sample peak
    x = _sine(SR / 4, 1.0, 1.0, phase=np.pi / 4)
    sample_peak = dsp.to_db(float(np.abs(x).max()))
    assert sample_peak == pytest.approx(-3.01, abs=0.01)
    assert dsp.true_peak(x) == pytest.approx(0.0, abs=0.05)
    assert dsp.true_peak(_stereo(x * 0.5)) == pytest.approx(-6.02, abs=0.05)
    assert dsp.true_peak(np.zeros(100)) == dsp.FLOOR_DB


def test_loudness_range_of_alternating_levels():
    # 20 s at -20 LUFS then 20 s at -30 LUFS, twice: the 3 s windows are almost all pure, so about 10 LU
    seg = lambda lufs: _stereo(_sine(1000, 20, 10 ** (lufs / 20)))
    x = np.concatenate([seg(-20), seg(-30), seg(-20), seg(-30)])
    assert dsp.loudness_range(x) == pytest.approx(10.0, abs=1.0)
    assert dsp.loudness_range(seg(-20)) < 0.5  # steady: no range
    assert dsp.loudness_range(seg(-20)[: 2 * SR]) == 0.0  # shorter than one window


def test_measure_reports_all_three():
    m = dsp.measure(_stereo(_sine(1000, 5, 0.1)))
    assert set(m) == {"integrated_lufs", "true_peak_dbtp", "lra_lu"}
    assert m["true_peak_dbtp"] == pytest.approx(-20.0, abs=0.1)


def test_compressor_is_gentle_and_only_acts_on_loud_parts():
    quiet = _stereo(_sine(1000, 2, 0.05))
    loud = _stereo(_sine(1000, 2, 0.5))
    x = np.concatenate([quiet, loud])
    y = dsp.compress(x, 3.0)
    assert y.shape == x.shape
    reduction = dsp.to_db(float(np.abs(x[-SR:]).max())) - dsp.to_db(float(np.abs(y[-SR:]).max()))
    assert 2.0 < reduction <= 3.2  # the loud part is down by about the 3 dB allowed
    assert np.allclose(y[: SR // 2], x[: SR // 2], atol=1e-3)  # the quiet part is untouched
    assert dsp.compress(np.zeros((1000, 2))).shape == (1000, 2)


# ------------------------------------------------------------------ the master step


def _mix_like(seconds=20, seed=1):
    """Speech-like: loud bursts over a quiet floor, stereo."""
    n = round(seconds * SR)
    x = _noise(seconds, 0.001, seed)
    env = (np.sin(2 * np.pi * np.arange(n) / SR * 0.5) > 0).astype(float)
    x = x + _noise(seconds, 0.03, seed + 1) * env
    return _stereo(np.clip(x, -0.9, 0.9))


def test_master_lands_on_the_target_within_half_an_lu():
    values = master.defaults()
    out, report = amaster.master(_mix_like(), values)
    assert report["applied"] and report["rounds"] >= 1
    assert abs(dsp.gated_lufs(out) - -14.0) <= 0.5
    assert dsp.true_peak(out) <= -1.0 + 0.05
    assert report["after"]["integrated_lufs"] == pytest.approx(dsp.gated_lufs(out), abs=0.01)
    assert report["before"]["integrated_lufs"] < -20


def test_master_obeys_a_custom_target_and_ceiling_and_keeps_balance():
    values = {**master.defaults(), "integrated_lufs": -18.0, "true_peak_dbtp": -2.0}
    x = _mix_like()
    out, report = amaster.master(x, values)
    assert abs(dsp.gated_lufs(out) - -18.0) <= 0.5
    assert dsp.true_peak(out) <= -2.0 + 0.05
    assert not report["compressed"]  # a plain gain and the limiter: nothing else touches the mix


def test_master_squeezes_a_wide_range_when_asked():
    seg = lambda lufs: _stereo(_sine(300, 8, 10 ** (lufs / 20)))
    x = np.concatenate([seg(-16), seg(-30), seg(-16), seg(-30)])
    wide = dsp.loudness_range(x)
    out, report = amaster.master(x, {**master.defaults(), "max_lra_lu": 6.0})
    assert report["compressed"]
    assert dsp.loudness_range(out) < wide
    assert abs(dsp.gated_lufs(out) - -14.0) <= 0.5
    _, plain = amaster.master(x, master.defaults())
    assert not plain["compressed"]  # no LRA limit and the peak fits: no compressor


def test_master_leaves_a_silent_mix_alone():
    x = np.zeros((SR, 2))
    out, report = amaster.master(x, master.defaults())
    assert out is x and not report["applied"] and "silent" in report["note"]


def test_verdict_flags_what_is_off():
    values = master.defaults()
    ok, why = amaster.verdict({"integrated_lufs": -14.2, "true_peak_dbtp": -1.1, "lra_lu": 30.0}, values)
    assert ok and why == []
    ok, why = amaster.verdict({"integrated_lufs": -12.0, "true_peak_dbtp": 0.0, "lra_lu": 9.0},
                              {**values, "max_lra_lu": 6.0})
    assert not ok and len(why) == 3
    assert "over the -14 LUFS target" in why[0] and "ceiling" in why[1] and "6 LU limit" in why[2]
    assert amaster.verdict({"integrated_lufs": -14.0, "true_peak_dbtp": -0.9, "lra_lu": 1.0}, values)[0]  # 0.1 over: ok


# ------------------------------------------------------------------ the settings group and its resolution


def test_defaults_are_the_streaming_norm():
    assert master.defaults() == {"enabled": True, "integrated_lufs": -14.0, "true_peak_dbtp": -1.0, "max_lra_lu": None}
    assert settings.DEFAULTS["master"] == master.defaults()
    assert "master" in presets.GROUPS


@pytest.mark.parametrize("key,value", [("integrated_lufs", -31), ("integrated_lufs", -5), ("true_peak_dbtp", 0.5),
                                       ("true_peak_dbtp", -10), ("max_lra_lu", 0.5), ("max_lra_lu", 21),
                                       ("enabled", 1), ("integrated_lufs", True), ("integrated_lufs", "loud")])
def test_values_out_of_range_are_refused(key, value):
    with pytest.raises(ValueError):
        master.check(key, value)
    with pytest.raises(ValueError):
        settings.check_value(f"master.{key}", value)
    assert master.problems_in({key: value})


def test_settings_set_accepts_good_values_and_null_lra():
    assert settings.check_value("master.integrated_lufs", -16) == -16.0
    assert settings.check_value("master.max_lra_lu", None) is None
    assert settings.check_value("master.enabled", False) is False
    assert settings.check_value("master", {"integrated_lufs": -9, "max_lra_lu": 8}) == {"integrated_lufs": -9.0,
                                                                                         "max_lra_lu": 8.0}
    with pytest.raises(ValueError, match="unknown"):
        settings.check_value("master", {"loudness": -9})
    with pytest.raises(ValueError):
        settings.check_value("master", "loud")


def test_resolution_order_defaults_settings_preset_short():
    s = {**settings.DEFAULTS}
    values, where = presets.master_resolved(s)
    assert values == master.defaults() and set(where.values()) == {"default"}  # the stored defaults are no layer
    s = {"master": {"integrated_lufs": -16.0}, "style_preset": "loud",
         "presets": {"loud": {"master": {"integrated_lufs": -9.0, "true_peak_dbtp": -2.0}}}}
    values, where = presets.master_resolved(s)
    assert values["integrated_lufs"] == -9.0 and where["integrated_lufs"] == "preset:loud"
    assert values["true_peak_dbtp"] == -2.0 and where["enabled"] == "default"
    short = Short(slug="t", title="T", length_s=10, master={"integrated_lufs": -12.0, "enabled": False})
    values, where = presets.master_resolved(s, short)
    assert values["integrated_lufs"] == -12.0 and where["integrated_lufs"] == "short" and values["enabled"] is False
    assert presets.master_resolved(s, {"master": {"max_lra_lu": 7}})[0]["max_lra_lu"] == 7.0
    own_only = presets.master_resolved({"master": {"integrated_lufs": -16.0}})
    assert own_only[0]["integrated_lufs"] == -16.0 and own_only[1]["integrated_lufs"] == "settings"


def test_a_bad_value_is_ignored_in_resolution_and_reported_in_problems():
    s = {"master": {"integrated_lufs": -99}}
    assert presets.master_resolved(s)[0]["integrated_lufs"] == -14.0
    assert any("master.integrated_lufs" in p for p in presets.problems(s))
    short = Short(slug="t", title="T", length_s=10, master={"true_peak_dbtp": 3})
    assert presets.master_resolved({}, short)[0]["true_peak_dbtp"] == -1.0
    assert any("true_peak_dbtp" in p for p in validate(short))
    assert any(p.startswith("short.json") for p in presets.problems({}, short))


def test_presets_accept_a_master_group_and_save_it():
    assert presets.preset_problems("p", {"master": {"integrated_lufs": -16}}) == []
    assert presets.preset_problems("p", {"master": {"integrated_lufs": -50}})
    s = {"master": {"integrated_lufs": -16.0}}
    assert presets.from_short(s, Short(slug="t", title="T", length_s=10)) == {"master": {"integrated_lufs": -16.0}}
    assert presets.from_short({}, Short(slug="t", title="T", length_s=10)) == {}


def test_short_json_roundtrip_keeps_master_only_when_set(tmp_path):
    from bsa.short import load_short, save_short

    p = tmp_path / "short.json"
    save_short(Short(slug="t", title="T", length_s=10), p)
    assert "master" not in p.read_text("utf-8")
    save_short(Short(slug="t", title="T", length_s=10, master={"integrated_lufs": -16.0}), p)
    assert load_short(p).master == {"integrated_lufs": -16.0}


# ------------------------------------------------------------------ the measured check on the final


def _check(tmp_path, monkeypatch, audio, s=None, short=None):
    import json

    import bsa_blender
    from bsa.audio import decode

    monkeypatch.setattr(decode, "load", lambda path: audio)
    record, warnings = bsa_blender._loudness_check(tmp_path, short or Short(slug="t", title="T", length_s=10),
                                                   {**settings.DEFAULTS, **(s or {})}, tmp_path / "x.mp4")
    return record, warnings, json.loads((tmp_path / "review" / "loudness.json").read_text("utf-8"))


def test_final_check_writes_loudness_json_and_passes_on_target(tmp_path, monkeypatch):
    good, _ = amaster.master(_mix_like(), master.defaults())
    record, warnings, saved = _check(tmp_path, monkeypatch, good)
    assert warnings == [] and record == saved and saved["ok"] is True
    assert abs(saved["integrated_lufs"] - -14.0) <= 0.5 and saved["targets"]["true_peak_dbtp"] == -1.0
    assert set(saved) >= {"integrated_lufs", "true_peak_dbtp", "lra_lu", "targets", "ok"}


def test_final_check_warns_but_never_blocks(tmp_path, monkeypatch):
    loud = _stereo(_sine(1000, 10, 0.9))  # about -1 LUFS, a true peak near -0.9 dBTP
    _record, warnings, saved = _check(tmp_path, monkeypatch, loud, {"master": {"max_lra_lu": 3.0}})
    assert saved["ok"] is False and len(warnings) == 1 and "over the -14 LUFS target" in warnings[0]
    seg = lambda lufs: _stereo(_sine(300, 20, 10 ** (lufs / 20)))
    wide = np.concatenate([seg(-14), seg(-24), seg(-14), seg(-24)])
    _, warnings, saved = _check(tmp_path, monkeypatch, wide, {"master": {"max_lra_lu": 3.0}})
    assert any("loudness range" in p for p in saved["problems"])


def test_final_check_with_the_master_off_only_records(tmp_path, monkeypatch):
    _record, warnings, saved = _check(tmp_path, monkeypatch, _stereo(_sine(1000, 5, 0.9)),
                                     {"master": {"enabled": False}})
    assert warnings == [] and saved["ok"] is None and saved["targets"] is None and saved["integrated_lufs"] > -10


def test_final_check_that_cannot_measure_is_a_warning(tmp_path, monkeypatch):
    import bsa_blender
    from bsa.audio import decode

    def boom(path):
        raise RuntimeError("no sound")

    monkeypatch.setattr(decode, "load", boom)
    record, warnings = bsa_blender._loudness_check(tmp_path, Short(slug="t", title="T", length_s=10),
                                                   settings.DEFAULTS, tmp_path / "x.mp4")
    assert record["ok"] is None and "not measured: no sound" in warnings[0]


def _peaky_dense(seconds=20, seed=3):
    """A dense bed with sharp spikes: a single gain to the target leaves the peaks far over the ceiling, so the limiter
    takes a lot of level away and the master has to make it up."""
    n = round(seconds * SR)
    x = _noise(seconds, 0.07, seed)
    rng = np.random.default_rng(seed)
    for i in range(0, n - 200, SR // 8):
        x[i : i + 40] += rng.choice([-1, 1]) * 0.8  # a click every 125 ms
    return _stereo(np.clip(x, -0.99, 0.99))


def test_master_makes_up_what_the_limiter_takes_away_on_a_dense_peaky_mix():
    values = {**master.defaults(), "integrated_lufs": -11.0, "true_peak_dbtp": -1.0, "max_lra_lu": 3.0}
    x = _peaky_dense()
    one_pass = dsp.limit(x * dsp.db(-11.0 - dsp.gated_lufs(x)), -1.3)
    assert dsp.gated_lufs(one_pass) < -11.0 - 0.3  # the premise: one gain pass and the limiter miss the target
    out, report = amaster.master(x, values)
    assert abs(dsp.gated_lufs(out) - -11.0) <= 0.2
    assert dsp.true_peak(out) <= -1.0
    ok, _ = amaster.verdict(dsp.measure(out), values)
    assert ok
    assert report["rounds"] > 1
