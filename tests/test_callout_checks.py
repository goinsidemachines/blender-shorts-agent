"""The callouts' checks that need no Blender (bsa.callout_checks), the check keys they add (bsa.freshness) and the
review page's callout lists (storyboard and final)."""

import json

import pytest

from bsa import callout_checks as cc
from bsa import callout_layout as cl
from bsa import callouts, freshness
from bsa.layout import H, Rect, W
from bsa.overlays import captions
from bsa.short import Line, Short, Shot
from bsa.timeline import Span, build_timeline

TEXT = "The valve opens and the pump runs."


def _short(calls=(), labels=(), more=None):
    shots = [Shot("s01", "Hook", "pic", "Scene", min_s=3.0, callouts=list(calls), labels=list(labels)),
             Shot("s02", "Next", "pic", "Scene", min_s=2.0, callouts=list(more or []))]  # fmt: skip
    lines = [Line("l01", TEXT, "s01"), Line("l02", "The pump runs and the valve shuts.", "s02")]
    return Short(slug="t", title="T", length_s=10, lines=lines, shots=shots)


def _call(**over):
    return {"text": "Valve", "near": "Valve", "on": "valve", **over}


def _tl(short):
    return build_timeline(short, {"l01": 3.0, "l02": 2.0})


def _cue(i=0, near="Valve", f_on=20, f_off=60, **over):
    return {"i": i, "text": "Valve", "near": near, "at": None, "f_on": f_on, "f_off": f_off, "glow": True,
            "culprit": False, "estimated": False, **over}  # fmt: skip


def _anchors(point, n=90, shot="s01", i=0):
    return {"callouts": {shot: {str(i): {"f0": 1, "points": [point] * n}}}}


def test_a_near_label_and_a_callout_on_one_object_warn_once():
    short = _short([_call()], labels=[{"text": "Valve", "near": "Valve"}])
    rows = cc.duplicates(short)
    assert [(r[0], r[1], r[2]) for r in rows] == [("s01", "warning", "Valve")]
    assert "Keep the callout only" in rows[0][4]
    assert not cc.duplicates(_short([_call()], labels=[{"text": "Pump", "near": "Pump"}, "plain"]))
    assert not cc.duplicates(_short())


def test_more_than_one_culprit_is_the_validate_warning_at_the_first_culprits_shot():
    short = _short([_call(culprit=True)], more=[_call(text="Pump", near="Pump", on="pump", culprit=True)])
    rows = cc.culprits(short)
    assert len(rows) == 1 and rows[0][0] == "s01" and rows[0][1] == "warning"
    assert rows[0][4] == callouts.warnings(short)[0]  # reused, not another wording
    assert not cc.culprits(_short([_call(culprit=True)]))


def test_an_estimated_cue_is_a_note_and_a_timed_one_is_not():
    notes = cc.estimated({"s01": [_cue(estimated=True), _cue(i=1, estimated=False)]})
    assert len(notes) == 1 and "'Valve'" in notes[0] and "frame (20)" in notes[0]
    assert not cc.estimated({})


def test_a_part_inside_the_safe_area_with_room_for_its_label_is_clean():
    short = _short([_call()])
    assert cc.layout(short, _tl(short), {"s01": [_cue()]}, _anchors([W / 2, H / 2])) == []


def test_a_part_outside_the_safe_area_at_its_cue_warns_and_names_the_frame():
    short = _short([_call()])
    rows = cc.layout(short, _tl(short), {"s01": [_cue()]}, _anchors([5, 5]))
    assert [(r[0], r[1], r[2], r[3]) for r in rows if "safe area" in r[4]] == [("s01", "warning", "Valve", 20)]


def test_a_part_off_screen_at_its_cue_warns_that_the_arrow_is_dropped():
    short = _short([_call()])
    anchors = {"callouts": {"s01": {"0": {"f0": 1, "points": [None] * 20 + [[W / 2, H / 2]] * 70}}}}
    rows = cc.layout(short, _tl(short), {"s01": [_cue()]}, anchors)
    assert len(rows) == 1 and "not on screen at frame 20" in rows[0][4] and "arrow is dropped" in rows[0][4]
    ok = {"callouts": {"s01": {"0": {"f0": 1, "points": [None] * 5 + [[W / 2, H / 2]] * 85}}}}  # on screen by then
    assert not [r for r in cc.layout(short, _tl(short), {"s01": [_cue()]}, ok) if "not on screen" in r[4]]


def test_a_label_that_still_overlaps_a_caption_is_one_warning_with_its_frames(monkeypatch):
    short = _short([_call()])
    tl = _tl(short)
    f0, f1, _, box, _ = captions(short, tl)[0]
    last = min(f1, f0 + 9)

    def onto_the_caption(cue, span, points, bands, fixed, placed, heroes, near):
        return {f: cl.Frame(0, 0, box, 1.0, 1.0, None, None) for f in range(f0, last + 1)}

    monkeypatch.setattr(cc, "plan", onto_the_caption)
    rows = cc.layout(short, tl, {"s01": [_cue(f_on=f0, f_off=last)]}, _anchors([W / 2, H / 2]))
    assert len(rows) == 1 and rows[0][1] == "warning" and rows[0][3] == f0, rows
    assert "overlaps a caption" in rows[0][4] and f"frames {f0} to {last}" in rows[0][4]


def test_two_callouts_that_land_on_each_other_overlap(monkeypatch):
    short = _short([_call(), _call(text="Pump", near="Pump", on="pump")])
    box = Rect(100, 300, 300, 100)
    monkeypatch.setattr(cc, "plan", lambda *a, **k: {f: cl.Frame(0, 0, box, 1.0, 1.0, None, None) for f in (20, 21)})
    anchors = {"callouts": {"s01": {"0": {"f0": 1, "points": [[W / 2, H / 2]] * 90},
                                     "1": {"f0": 1, "points": [[W / 2, H / 2]] * 90}}}}
    cues = {"s01": [_cue(), _cue(i=1, near="Pump", f_off=30)]}
    rows = cc.layout(short, _tl(short), cues, anchors)
    assert [r[4] for r in rows if "another callout" in r[4]] and rows[0][3] == 20, rows


def test_a_shot_without_callouts_has_no_layout_rows():
    short = _short()
    assert cc.layout(short, _tl(short), {}, {}) == []


# --------------------------------------------------------------------------- check keys


def _inputs(shot, cues=None):
    return freshness.shot_check_inputs("fp", Span("s01", 1, 90), shot, ["s01"], cues=cues)


def test_check_keys_of_a_shot_without_callouts_are_what_they_were():
    plain = {"id": "s01", "scene": "Scene", "labels": [], "badge": None, "wide": None, "callouts": []}
    inputs = _inputs(plain)
    assert "cues" not in inputs and "callouts" not in inputs["entry"]
    assert set(inputs["entry"]) == {"scene", "labels", "badge", "wide"}


def test_callouts_and_their_cue_frames_move_the_check_key():
    plain = {"id": "s01", "scene": "Scene", "labels": [], "badge": None, "wide": None}
    with_call = {**plain, "callouts": [_call()]}
    base = freshness._input_key(_inputs(plain))
    keyed = freshness._input_key(_inputs(with_call, [_cue()]))
    assert keyed != base
    assert freshness._input_key(_inputs(with_call, [_cue(f_on=21)])) != keyed  # re-timed: checked again
    assert freshness._input_key(_inputs(with_call, [_cue()])) == keyed


# --------------------------------------------------------------------------- the review page


def _stage(page, name):
    return next(s for s in page["stages"] if s["id"] == name)["content"]


def test_storyboard_lists_callouts_only_for_a_shot_that_has_them(tmp_path):
    import pagefix

    from bsa.review import snapshot

    sdir = pagefix.make_short(tmp_path, "storyboard")
    path = sdir / "source" / "short.json"
    assert all("callouts" not in s for s in _stage(snapshot.build(sdir, settings={}), "storyboard")["shots"])
    data = json.loads(path.read_text("utf-8"))
    word = data["lines"][1]["text"].split()[0].strip(".,").lower()
    data["shots"][1]["callouts"] = [{"text": "Bob", "near": "Bob", "on": word, "culprit": True}]
    path.write_text(json.dumps(data), "utf-8")
    shots = _stage(snapshot.build(sdir, settings={}), "storyboard")["shots"]
    assert shots[1]["callouts"] == [{"text": "Bob", "on": word, "near": "Bob", "culprit": True}]
    assert "callouts" not in shots[0] and "callouts" not in shots[2]


def test_final_lists_callouts_with_word_and_second(tmp_path):
    import pagefix

    from bsa.review import snapshot

    sdir = pagefix.make_short(tmp_path, "final")
    path = sdir / "source" / "short.json"
    assert "callouts" not in _stage(snapshot.build(sdir, settings={}), "final")
    data = json.loads(path.read_text("utf-8"))
    word = data["lines"][1]["text"].split()[0].strip(".,").lower()
    data["shots"][1]["callouts"] = [{"text": "Bob", "near": "Bob", "on": word}]
    path.write_text(json.dumps(data), "utf-8")
    rows = _stage(snapshot.build(sdir, settings={}), "final")["callouts"]
    assert rows == [{"shot": "s02", "text": "Bob", "on": word, "at_s": None, "estimated": False}]  # no cue file yet
    cue = {"i": 0, "text": "Bob", "near": "Bob", "f_on": 31, "f_off": 60, "estimated": True}
    (sdir / "source" / "callouts.json").write_text(json.dumps({"timeline_hash": "x", "shots": {"s02": [cue]}}), "utf-8")
    rows = _stage(snapshot.build(sdir, settings={}), "final")["callouts"]
    assert rows[0]["at_s"] == pytest.approx(1.0, abs=0.01) and rows[0]["estimated"] is True
