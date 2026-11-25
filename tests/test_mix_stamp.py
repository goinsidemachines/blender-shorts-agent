"""audio/mix.json: the stamp CLI mix leaves next to the mix, and the final edit re-making a mix whose master moved on."""

from __future__ import annotations

import json

import pagefix
import pytest
from test_captions import _stubbed_edit
from test_cli import home, ok, pendulum, run, workspace  # noqa: F401 - fixtures

import bsa_blender
import bsa_cli
from bsa import captions, presets, settings
from bsa.audio import master as audio_master
from bsa.audio.master import MASTER_VERSION, stale_reason, stamp_path, write_stamp

ON = {"enabled": True, "integrated_lufs": -14.0, "true_peak_dbtp": -1.0, "max_lra_lu": None}
OFF = {**ON, "enabled": False}


def _sdir_with_mix(tmp_path):
    sdir = tmp_path / "s"
    (sdir / "audio").mkdir(parents=True)
    (sdir / "audio" / "mix.wav").write_bytes(b"RIFF")
    return sdir


def test_no_mix_is_never_stale(tmp_path):
    assert stale_reason(tmp_path, ON) is None


def test_a_matching_stamp_is_current(tmp_path):
    sdir = _sdir_with_mix(tmp_path)
    write_stamp(sdir, ON)
    assert stale_reason(sdir, ON) is None


def test_a_missing_stamp_is_stale_when_the_master_is_on(tmp_path):
    sdir = _sdir_with_mix(tmp_path)
    assert "no readable audio/mix.json" in stale_reason(sdir, ON)


def test_an_unreadable_stamp_is_a_missing_one(tmp_path):
    sdir = _sdir_with_mix(tmp_path)
    stamp_path(sdir).write_text("{not json", encoding="utf-8")
    assert stale_reason(sdir, ON) is not None


def test_an_older_master_version_is_stale(tmp_path):
    sdir = _sdir_with_mix(tmp_path)
    stamp_path(sdir).write_text(json.dumps({"master_version": MASTER_VERSION - 1, "master": ON}), encoding="utf-8")
    assert "master changed" in stale_reason(sdir, ON)


def test_changed_master_values_are_stale(tmp_path):
    sdir = _sdir_with_mix(tmp_path)
    write_stamp(sdir, ON)
    assert "settings changed" in stale_reason(sdir, {**ON, "integrated_lufs": -16.0})


def test_with_the_master_off_a_missing_stamp_is_fine(tmp_path):
    sdir = _sdir_with_mix(tmp_path)
    assert stale_reason(sdir, OFF) is None
    write_stamp(sdir, OFF)
    assert stale_reason(sdir, OFF) is None


def test_a_mix_that_was_mastered_is_stale_once_the_master_is_off(tmp_path):
    sdir = _sdir_with_mix(tmp_path)
    write_stamp(sdir, ON)
    assert "is off now" in stale_reason(sdir, OFF)


def test_cmd_mix_writes_the_stamp(home, pendulum):  # noqa: F811
    ok(run(home, "timeline", "pendulum"))
    ok(run(home, "mix", "pendulum"))
    stamp = json.loads((pendulum / "audio" / "mix.json").read_text(encoding="utf-8"))
    values, _ = presets.master_resolved(settings.load_settings(), None)
    assert stamp["master_version"] == MASTER_VERSION and stamp["master"]["enabled"] == values["enabled"]
    assert audio_master.stale_reason(pendulum, stamp["master"]) is None


def _fake_mix(monkeypatch, sdir, values):
    """CLI mix stubbed: writes a mix and its stamp for `values`, and counts its calls."""
    calls = []

    def cmd_mix(args):
        calls.append(args.short)
        (sdir / "audio").mkdir(exist_ok=True)
        (sdir / "audio" / "mix.wav").write_bytes(b"remade")
        write_stamp(sdir, values)
        return {}

    monkeypatch.setattr(bsa_cli, "cmd_mix", cmd_mix)
    return calls


def _edit(tmp_path, monkeypatch, preset="final"):
    sdir = pagefix.make_short(tmp_path, "story")
    data = pagefix.write_timeline(sdir)
    go = _stubbed_edit(monkeypatch, sdir, captions.timeline.timeline_hash(data), has_audio=True, preset=preset)
    monkeypatch.setattr(bsa_blender, "_loudness_check", lambda *a, **k: ({}, []))
    monkeypatch.setattr(bsa_blender, "_after_final", lambda *a, **k: [])
    (sdir / "audio").mkdir(exist_ok=True)
    (sdir / "audio" / "mix.wav").write_bytes(b"old")
    values, _ = presets.master_resolved(settings.load_settings(), bsa_blender._load(sdir))
    return sdir, go, values


def test_the_final_edit_remakes_a_mix_with_no_stamp_and_says_why(tmp_path, monkeypatch):
    sdir, go, values = _edit(tmp_path, monkeypatch)
    calls = _fake_mix(monkeypatch, sdir, values)
    out = go()
    assert len(calls) == 1 and (sdir / "audio" / "mix.wav").read_bytes() == b"remade"
    assert any(w.startswith("remixed: ") and "mix.json" in w for w in out["warnings"])


def test_the_final_edit_remakes_a_mix_whose_master_values_changed(tmp_path, monkeypatch):
    sdir, go, values = _edit(tmp_path, monkeypatch)
    write_stamp(sdir, {**values, "integrated_lufs": values["integrated_lufs"] - 3})
    calls = _fake_mix(monkeypatch, sdir, values)
    out = go()
    assert len(calls) == 1 and "settings changed" in out["warnings"][0]


def test_the_final_edit_keeps_a_current_mix(tmp_path, monkeypatch):
    sdir, go, values = _edit(tmp_path, monkeypatch)
    write_stamp(sdir, values)
    calls = _fake_mix(monkeypatch, sdir, values)
    out = go()
    assert calls == [] and (sdir / "audio" / "mix.wav").read_bytes() == b"old" and "warnings" not in out


def test_a_preview_edit_never_remakes_the_mix(tmp_path, monkeypatch):
    sdir, go, values = _edit(tmp_path, monkeypatch, preset="preview")
    calls = _fake_mix(monkeypatch, sdir, values)  # no stamp: a final edit would re-make it
    go()
    assert calls == [] and (sdir / "audio" / "mix.wav").read_bytes() == b"old"


def test_a_failed_remix_is_a_refusal_naming_why(tmp_path, monkeypatch):
    _sdir, go, _values = _edit(tmp_path, monkeypatch)

    def boom(args):
        raise bsa_cli.CliError("no timeline")

    monkeypatch.setattr(bsa_cli, "cmd_mix", boom)
    with pytest.raises(bsa_blender.CliError, match="needs re-making.*no timeline"):
        go()
