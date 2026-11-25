import json

import pytest

from bsa import callouts, kit
from bsa.freshness import StaleError
from bsa.short import Line, Short, Shot, load_short, save_short, validate
from bsa.timeline import build_timeline, timeline_hash, to_json

TEXT = "The *slip-blind,* sits in the line. Then the valve shuts, and the valve holds."


def _short(calls, text=TEXT, min_s=2.0):
    shots = [Shot("s01", "Hook", "pic", "Scene", min_s=min_s, callouts=calls),
             Shot("s02", "Next", "pic", "Scene", min_s=min_s)]  # fmt: skip
    lines = [Line("l01", text, "s01"), Line("l02", "Nothing else here.", "s02")]
    return Short(slug="t", title="T", length_s=10, lines=lines, shots=shots)


def _call(**over):
    return {"text": "Slip blind", "near": "Slip blind", "on": "slip blind", **over}


def test_match_on_ignores_case_punctuation_and_asterisks():
    words = ["The", "*slip-blind,*", "sits"]
    assert callouts.match_on("slip blind", words) == 1
    assert callouts.match_on("SLIP-BLIND", words) == 1
    assert callouts.match_on("sits", words) == 2


def test_match_on_second_occurrence():
    words = ["the", "valve", "shuts,", "and", "the", "Valve", "holds"]
    assert callouts.match_on("valve", words) == 1
    assert callouts.match_on("valve#2", words) == 5
    assert callouts.match_on("valve#3", words) is None


def test_match_on_missing_is_none():
    assert callouts.match_on("gasket", ["the", "valve", "shuts"]) is None
    assert callouts.match_on("", ["the", "valve"]) is None


def test_cue_from_word_times():
    short = _short([_call()])
    tl = build_timeline(short, {"l01": 4.0, "l02": 1.0})
    heard = {"l01": [("The", 0.0, 0.2), ("slip", 0.5, 0.8), ("blind", 0.8, 1.2)]}
    cue = callouts.cue_frames(short, tl, heard)["s01"][0]
    assert cue["f_on"] == tl.lines[0].f0 + 15 and cue["estimated"] is False
    assert cue["f_off"] == tl.shots[0].f1  # no hold_s: to the end of the shot
    assert callouts.cue_frames(short, tl, None)["s01"][0]["near"] == "Slip blind"


def test_cue_estimated_when_no_word_times():
    short = _short([_call(on="valve#2")])
    tl = build_timeline(short, {"l01": 4.0, "l02": 1.0})
    cue = callouts.cue_frames(short, tl, None)["s01"][0]
    assert cue["estimated"] is True
    line = tl.lines[0]
    assert line.f0 < cue["f_on"] <= line.f1
    first = callouts.cue_frames(_short([_call(on="valve")]), tl, None)["s01"][0]
    assert first["f_on"] < cue["f_on"]


def test_hold_clipped_to_shot_end():
    short = _short([_call(on="holds", hold_s=30)])
    tl = build_timeline(short, {"l01": 4.0, "l02": 1.0})
    cue = callouts.cue_frames(short, tl, None)["s01"][0]
    assert cue["f_off"] == tl.shots[0].f1 < tl.shots[1].f0 + 5
    short = _short([_call(hold_s=0.5)])
    cue = callouts.cue_frames(short, tl, None)["s01"][0]
    assert cue["f_off"] == cue["f_on"] + 15


def test_validation_rejects_unknown_key_and_missing_on():
    errors = validate(_short([_call(colour="red")]))
    assert any("unknown keys ['colour']" in e for e in errors)
    errors = validate(_short([{"text": "A", "near": "B"}]))
    assert any("needs on" in e for e in errors)
    assert not [e for e in validate(_short([_call()])) if "callout" in e]


def test_validation_error_when_on_not_in_shot_lines():
    errors = validate(_short([_call(on="gasket")]))
    assert any("shot s01" in e and "'gasket'" in e for e in errors)
    errors = validate(_short([_call(on="nothing")]))  # a word of another shot's line
    assert any("'nothing'" in e for e in errors)


def test_no_callouts_writes_no_file(tmp_path):
    (tmp_path / "source").mkdir()
    short = _short([])
    tl = build_timeline(short, None)
    assert callouts.write(tmp_path, short, tl, None) is None
    assert not callouts.path(tmp_path).exists()
    (tmp_path / "source" / "callouts.json").write_text("{}")
    assert callouts.write(tmp_path, short, tl, None) is None
    assert not callouts.path(tmp_path).exists()  # a stale file goes


def test_write_and_load_stamp(tmp_path):
    (tmp_path / "source").mkdir()
    short = _short([_call()])
    tl = build_timeline(short, {"l01": 4.0, "l02": 1.0})
    assert callouts.write(tmp_path, short, tl, None) == callouts.path(tmp_path)
    h = timeline_hash(json.loads(json.dumps(to_json(tl))))
    assert callouts.load(tmp_path, h)["s01"][0]["text"] == "Slip blind"
    with pytest.raises(StaleError):
        callouts.load(tmp_path, "0" * 16)  # re-narrated: never silently the old frames


def test_save_omits_empty_callouts_and_roundtrips(tmp_path):
    p = tmp_path / "short.json"
    save_short(_short([]), p)
    assert "callouts" not in p.read_text()
    save_short(_short([_call()]), p)
    assert load_short(p).shots[0].callouts == [_call()]


def test_two_culprits_warn():
    short = _short([_call(culprit=True), _call(on="valve", culprit=True)])
    assert len(callouts.warnings(short)) == 1
    assert callouts.warnings(_short([_call(culprit=True)])) == []


def test_brand_callout_keys():
    assert kit.check("callout.color", "#ff8800") == "#FF8800"
    assert kit.check("callout.glow", "6") == 6.0
    assert kit.check("callout", {"glow": 10, "color": "#fff000"}) == {"glow": 10.0, "color": "#FFF000"}
    for key, bad in (("callout.glow", 51), ("callout.glow", -1), ("callout.color", "orange")):
        with pytest.raises(ValueError):
            kit.check(key, bad)


def test_write_leaves_an_unchanged_file_alone(tmp_path):
    (tmp_path / "source").mkdir()
    short = _short([_call()])
    tl = build_timeline(short, {"l01": 4.0, "l02": 1.0})
    target = callouts.write(tmp_path, short, tl, None)
    import os

    os.utime(target, ns=(10**9, 10**9))
    callouts.write(tmp_path, short, tl, None)
    assert target.stat().st_mtime_ns == 10**9  # same frames: not rewritten, so the build is not made stale
    callouts.write(tmp_path, short, build_timeline(short, {"l01": 5.0, "l02": 1.0}), None)
    assert target.stat().st_mtime_ns != 10**9
