"""Recorded sound effects: decoding, the sfx kind -> clip library, synthesis fallback, the natural-only policy and
bsa.audio.clip for source/sound.py."""

import json
import struct
import wave
from pathlib import Path

import numpy as np
import pytest

from bsa import audio
from bsa.audio import decode, recorded
from bsa.audio.mix import mix
from bsa.audio.sfx import KINDS, SR
from bsa.short import Sfx, Short, Shot, validate
from bsa.timeline import Span, Timeline


def _wav(path: Path, x: np.ndarray, sr: int = SR, width: int = 2, channels: int = 1) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = np.repeat(x[:, None], channels, 1) if channels > 1 else x[:, None]
    scale = {1: 127, 2: 32767, 3: 8388607, 4: 2147483647}[width]
    ints = np.round(np.clip(data, -1, 1) * scale).astype(np.int64)
    if width == 1:
        raw = (ints + 128).astype(np.uint8).tobytes()
    elif width == 3:
        raw = b"".join(int(v).to_bytes(3, "little", signed=True) for v in ints.ravel())
    else:
        raw = ints.astype({2: "<i2", 4: "<i4"}[width]).tobytes()
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(width)
        w.setframerate(sr)
        w.writeframes(raw)
    return path


def _float_wav(path: Path, x: np.ndarray, sr: int = SR) -> Path:
    data = x.astype("<f4").tobytes()
    fmt = struct.pack("<HHIIHH", 3, 1, sr, sr * 4, 4, 32)
    body = b"WAVE" + b"fmt " + struct.pack("<I", len(fmt)) + fmt + b"data" + struct.pack("<I", len(data)) + data
    path.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)
    return path


def _tone(sec=0.3, f=440.0, sr=SR, lead_silence=0.0):
    t = np.arange(int(sec * sr)) / sr
    x = 0.5 * np.sin(2 * np.pi * f * t) * np.exp(-t / 0.1)
    return np.concatenate([np.zeros(int(lead_silence * sr)), x])


@pytest.mark.parametrize("width", [1, 2, 3, 4])
def test_read_wav_pcm_widths(tmp_path, width):
    x = _tone()
    data, sr = decode.read_wav(_wav(tmp_path / f"w{width}.wav", x, width=width, channels=2))
    assert sr == SR and data.shape == (len(x), 2)
    assert np.max(np.abs(data[:, 0] - x)) < (0.02 if width == 1 else 1e-3)


def test_read_float_wav(tmp_path):
    x = _tone()
    data, sr = decode.read_wav(_float_wav(tmp_path / "f.wav", x))
    assert sr == SR and np.allclose(data[:, 0], x, atol=1e-6)


def test_load_resamples_to_48k(tmp_path):
    x = _tone(sec=0.5, f=1000, sr=44100)
    y = decode.load(_wav(tmp_path / "a.wav", x, sr=44100))
    assert y.shape[0] == round(len(x) * SR / 44100)
    spec = np.abs(np.fft.rfft(y[:, 0]))
    assert np.fft.rfftfreq(len(y), 1 / SR)[spec.argmax()] == pytest.approx(1000, abs=5)


def test_ogg_without_blender_says_so(tmp_path, monkeypatch):
    ogg = tmp_path / "a.ogg"
    ogg.write_bytes(b"OggS fake")
    monkeypatch.setattr(decode, "_aud", lambda: None)
    monkeypatch.setattr(decode, "_find_blender", lambda: None)
    with pytest.raises(RuntimeError, match="Blender"):
        decode.load(ogg)


def test_ogg_uses_a_decoded_sidecar(tmp_path, monkeypatch):
    ogg = tmp_path / "a.ogg"
    ogg.write_bytes(b"OggS fake")
    monkeypatch.setattr(decode, "_aud", lambda: None)
    calls = []

    def fake_blender(src, dst):
        calls.append(src)
        _wav(dst, _tone())

    monkeypatch.setattr(decode, "_decode_with_blender", fake_blender)
    assert decode.load(ogg).shape[0] == len(_tone())
    assert decode.load(ogg).shape[0] == len(_tone())
    assert len(calls) == 1  # the sidecar is reused


# --------------------------------------------------------------------------- library


def _library(tmp_path, clips: dict[str, list[str]]) -> recorded.Library:
    """A cache with one WAV per clip path, tagged with kinds."""
    cache = tmp_path / ".assets-cache"
    lib = recorded.Library(cache)
    entries = []
    for i, (rel, kinds) in enumerate(clips.items()):
        _wav(cache / rel, _tone(f=200 + 100 * i, lead_silence=0.05))
        entries.append({"path": rel, "kinds": kinds, "asset": "sfx-test", "source": "Test pack", "license": "CC0",
                        "name": Path(rel).stem})  # fmt: skip
    lib.add(entries)
    lib.save()
    return recorded.Library(cache)


def test_library_round_trip_and_variants(tmp_path):
    lib = _library(tmp_path, {"sounds/p/thud_1.wav": ["thud"], "sounds/p/thud_2.wav": ["thud"],
                              "sounds/p/click.wav": ["click"]})  # fmt: skip
    assert [c["path"] for c in lib.clips_for("thud")] == ["sounds/p/thud_1.wav", "sounds/p/thud_2.wav"]
    assert lib.clips_for("scrape") == []
    assert lib.find("click")["path"] == "sounds/p/click.wav"  # by stem
    assert lib.find("sounds/p/thud_2.wav")["path"] == "sounds/p/thud_2.wav"
    assert recorded.Library(tmp_path / "nothing").clips_for("thud") == []


def test_new_kinds_exist_and_synthesise():
    for kind in ("scrape", "slide", "click", "thud"):
        x = KINDS[kind](0.4, seed=1)
        assert len(x) == round(0.4 * SR) and np.isfinite(x).all() and np.abs(x).max() > 0


def _short(sfx, policy=""):
    return Short(slug="t", title="T", length_s=10, shots=[Shot("s01", "Hook", "pic", "Scene")], sfx=sfx,
                 sound_policy=policy)  # fmt: skip


def _tl(n=300):
    return Timeline(30, n, [Span("s01", 1, n)], [], [])


def test_resolve_prefers_recordings_and_rotates_variants(tmp_path):
    lib = _library(tmp_path, {"sounds/p/thud_1.wav": ["thud"], "sounds/p/thud_2.wav": ["thud"]})
    sfx = [Sfx("thud", "s01", t, 0.3, -18) for t in (0.5, 1.5, 2.5)] + [Sfx("creak", "s01", 4, 0.5, -20)]
    res = recorded.resolve(_short(sfx), lib, "any")
    assert [r["use"] for r in res] == ["recording", "recording", "recording", "synth"]
    assert res[0]["clip"] != res[1]["clip"] and res[0]["clip"] == res[2]["clip"]  # consecutive events differ
    assert res[0]["license"] == "CC0" and res[0]["source"] == "Test pack"
    assert "no recording" in res[3]["reason"]
    assert recorded.resolve(_short(sfx), lib, "any") == res  # deterministic


def test_natural_only_policy(tmp_path):
    lib = _library(tmp_path, {"sounds/p/thud_1.wav": ["thud"]})
    sfx = [Sfx("blip", "s01", 0.5, 0.1, -18), Sfx("thud", "s01", 1, 0.3, -18), Sfx("creak", "s01", 2, 0.4, -18)]
    res = recorded.resolve(_short(sfx, "natural-only"), lib, "natural-only")
    assert [r["use"] for r in res] == ["skipped", "recording", "skipped"]
    assert "synthetic" in res[0]["reason"] and "no recording" in res[2]["reason"]
    warnings = recorded.policy_warnings(_short(sfx), lib, "natural-only")
    assert any("blip" in w and "natural-only" in w for w in warnings)
    assert any("creak" in w and "assets search sounds" in w for w in warnings)
    assert recorded.policy_warnings(_short([Sfx("thud", "s01", 1, 0.3, -18)]), lib, "natural-only") == []


def test_any_policy_warns_only_about_synthesis(tmp_path):
    lib = recorded.Library(tmp_path / "c")
    w = recorded.policy_warnings(_short([Sfx("impact", "s01", 1, 0.3, -18)]), lib, "any")
    assert len(w) == 1 and "synthesis" in w[0]


def test_sound_policy_from_short_then_settings():
    assert recorded.sound_policy(_short([], "natural-only"), {}) == "natural-only"
    assert recorded.sound_policy(_short([]), {"sound_policy": "natural-only"}) == "natural-only"
    assert recorded.sound_policy(_short([]), {}) == "any"


def test_validate_rejects_an_unknown_policy():
    assert any("sound_policy" in e for e in validate(_short([], "loud")))
    assert not any("sound_policy" in e for e in validate(_short([], "natural-only")))


def test_prepare_trims_jitters_and_fades():
    x = _tone(sec=1.0, lead_silence=0.2)
    y, gain = recorded.prepare(x, dur_s=0.5, seed=3)
    assert len(y) <= round(0.5 * SR) + 1
    assert np.abs(y[: round(0.008 * SR)]).max() > 0.1  # 0.2 s of leading silence cut to a few ms of pre-roll
    assert abs(y[-1]) < 1e-3  # faded out
    assert abs(gain) <= recorded.GAIN_JITTER_DB
    y2, gain2 = recorded.prepare(x, dur_s=0.5, seed=4)
    assert gain2 != gain or len(y2) != len(y)  # a different take


def test_mix_uses_the_recording(tmp_path):
    lib = _library(tmp_path, {"sounds/p/thud_1.wav": ["thud"]})
    sfx = [Sfx("thud", "s01", 1.0, 0.3, -18)]
    rec, _ = mix(_tl(), _short(sfx), None, library=lib)
    syn, _ = mix(_tl(), _short(sfx), None)  # no library: synthesis
    assert rec.shape == syn.shape == (480000, 2)
    seg = slice(SR, SR + int(0.3 * SR))
    spec = np.abs(np.fft.rfft(rec[seg, 0]))
    peak = np.fft.rfftfreq(len(rec[seg, 0]), 1 / SR)[spec.argmax()]
    assert 150 < peak < 260  # the recorded 200 Hz tone (pitch jitter is small), not the synthetic thud
    assert not np.allclose(rec, syn)


def test_mix_skips_what_natural_only_forbids(tmp_path):
    lib = recorded.Library(tmp_path / "c")
    out, lv = mix(_tl(), _short([Sfx("blip", "s01", 1.0, 0.1, -18)]), None, library=lib, policy="natural-only")
    assert np.abs(out).max() == 0 and lv["bed_dbfs"] is None


def test_sound_playbook_documents_every_kind_and_the_policy():
    text = (Path(__file__).resolve().parent.parent / "skills/make-short/playbook/sound.md").read_text(encoding="utf-8")
    for kind in KINDS:
        assert f"| `{kind}` |" in text, kind
    for kind in recorded.SYNTHETIC_KINDS:
        assert f"| `{kind}` |" in text and "synthetic |" in text.split(f"| `{kind}` |")[1].split("\n")[0]
    assert "natural-only" in text and 'clip("' in text and "assets search sounds" in text


# --------------------------------------------------------------------------- bsa.audio.clip


def test_clip_by_path_name_and_kind(tmp_path):
    lib = _library(tmp_path, {"sounds/p/thud_1.wav": ["thud"], "sounds/p/thud_2.wav": ["thud"]})
    cache = lib.cache_dir
    a = audio.clip(str(cache / "sounds/p/thud_1.wav"))
    assert a.ndim == 1 and len(a) == len(_tone(lead_silence=0.05))
    assert np.array_equal(audio.clip("thud_1", cache_dir=cache), a)
    assert np.array_equal(audio.clip("thud", variant=1, cache_dir=cache), audio.clip("thud_2", cache_dir=cache))
    short_dir = cache.parent / "01-x"
    assert len(audio.clip("thud_2", short_dir=short_dir)) == len(a)
    stereo = audio.clip("thud_1", cache_dir=cache, mono=False)
    assert stereo.shape == (len(a), 1)
    with pytest.raises(FileNotFoundError, match="assets search sounds"):
        audio.clip("scrape", cache_dir=cache)


def test_clip_finds_the_workspace_cache_from_settings(tmp_path, bsa_home):
    lib = _library(tmp_path, {"sounds/p/click.wav": ["click"]})
    (bsa_home / "settings.json").write_text(json.dumps({"workspace": str(lib.cache_dir.parent)}), encoding="utf-8")
    assert len(audio.clip("click")) > 0


def test_natural_only_skips_designed_ui_clips(tmp_path):
    """Clips from a designed UI pack (tonal, partly synthesised interface sounds) are not used under natural-only,
    even when tagged with a natural kind; `any` still uses them. Old libraries without the flag are covered too."""
    lib = _library(tmp_path, {"sounds/kenney-ui-sfx/click5.wav": ["click"], "sounds/p/latch.wav": ["click"],
                              "sounds/kenney-ui-sfx/switch1.wav": ["click"]})  # fmt: skip
    for c in lib.clips:
        if "kenney" in c["path"]:
            c["asset"] = "sfx-kenney-ui-sfx"  # as an earlier version indexed it: no `designed` field
    sfx = [Sfx("click", "s01", t, 0.1, -18) for t in (0.5, 1.0, 1.5)]
    res = recorded.resolve(_short(sfx, "natural-only"), lib, "natural-only")
    assert {r["clip"] for r in res} == {"sounds/p/latch.wav"}
    assert {r["clip"] for r in recorded.resolve(_short(sfx), lib, "any")} > {"sounds/p/latch.wav"}
    only_ui = _library(tmp_path / "ui", {"sounds/kenney-ui-sfx/click5.wav": ["click"]})
    only_ui.clips[0]["designed"] = True
    (r,) = recorded.resolve(_short(sfx[:1], "natural-only"), only_ui, "natural-only")
    assert r["use"] == "skipped" and "designed" in r["reason"]
    assert any("designed" in w for w in recorded.policy_warnings(_short(sfx[:1]), only_ui, "natural-only"))


def test_flatness_scores_noise_high_and_a_tone_low():
    rng = np.random.default_rng(0)
    assert recorded.flatness(rng.standard_normal(SR // 4)) > 0.5
    assert recorded.flatness(_tone(0.25)) < 0.05
    assert recorded.flatness(np.zeros(100)) is None
