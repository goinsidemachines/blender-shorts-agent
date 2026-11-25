"""Callouts in the edit, the parts that need no Blender: the label and arrow placement frame by frame
(bsa.callout_layout), the arrow pixels, and the callout anchors in anchors.json (bsa_blender, bsa.freshness)."""

import json
import math
import sys
import types

import pytest

import bsa_blender
from bsa import callout_layout as cl
from bsa import freshness
from bsa.layout import H, Rect, W, safe_rect
from bsa.timeline import Span

SPAN = Span("s01", 1, 90)
CUE = {"i": 0, "text": "Slip blind", "near": "Slip blind", "f_on": 20, "f_off": 60, "glow": True, "culprit": False,
       "at": None, "estimated": False}  # fmt: skip
POINT = [W / 2, H / 2]


def _points(p=POINT, n=90):
    return {"f0": 1, "points": [p] * n}


def test_label_hidden_before_f_on_and_shown_after():
    frames = cl.plan(CUE, SPAN, _points())
    assert min(frames) == 20 and 19 not in frames and 21 in frames
    assert frames[20].alpha < frames[24].alpha <= 1.0 and frames[27].alpha == 1.0


def test_pop_in_scale_overshoots_then_settles():
    scales = [cl.pop(f, CUE)[1] for f in range(20, 30)]
    assert max(scales) == pytest.approx(1.05, abs=0.02) and scales[-1] == 1.0 and scales[0] < 0.8


def test_fades_out_at_f_off_and_ends_there():
    frames = cl.plan(CUE, SPAN, _points())
    assert frames[59].alpha == 1.0 and frames[60].alpha < 1.0 and max(frames) == 60 + cl.FADE_FRAMES - 1
    assert frames[max(frames)].alpha == pytest.approx(1 / (cl.FADE_FRAMES + 1))  # the last drawn frame is nearly gone


def test_hold_past_the_shot_end_leaks_nothing_into_the_next_shot():
    late = {**CUE, "f_on": 85, "f_off": 90}  # f_off is the shot's last frame
    frames = cl.plan(late, SPAN, _points())
    assert max(frames) == SPAN.f1 and min(frames) == 85


def test_arrow_points_at_anchor():
    frames = cl.plan(CUE, SPAN, _points())
    for f in (30, 50):
        fr = frames[f]
        assert fr.start is not None
        tx, ty = fr.tip
        assert math.hypot(tx - POINT[0], ty - POINT[1]) == pytest.approx(cl.TIP_GAP_PX, abs=2)  # tip 6 px short
        sx, sy = fr.start  # and on the line from the label's edge to the part
        cross = (tx - sx) * (POINT[1] - sy) - (ty - sy) * (POINT[0] - sx)
        assert abs(cross) < 1.0


def test_arrow_tail_is_on_the_labels_box_edge():
    fr = cl.plan(CUE, SPAN, _points())[30]
    b = fr.box
    sx, sy = fr.start
    assert b.x - 0.01 <= sx <= b.right + 0.01 and b.y - 0.01 <= sy <= b.bottom + 0.01
    assert sx in (b.x, b.right) or sy in (b.y, b.bottom)


def test_label_keeps_off_captions():
    band = Rect(safe_rect().x, POINT[1] - 200, safe_rect().w, 400)  # a caption band right where the label wants to go
    frames = cl.plan(CUE, SPAN, _points(), [(1, 90, band)])
    for fr in frames.values():
        assert fr.box.bottom <= band.y + 0.01 or fr.box.y >= band.bottom - 0.01


def test_label_keeps_off_other_labels_in_the_frame():
    placed = {}
    a = cl.plan(CUE, SPAN, _points(), placed=placed)
    b = cl.plan({**CUE, "i": 1, "text": "Other"}, SPAN, _points(), placed=placed)
    for f in a:
        x, y = a[f].box, b[f].box
        assert x.right <= y.x or y.right <= x.x or x.bottom <= y.y or y.bottom <= x.y


def test_arrow_dropped_when_anchor_offscreen_but_label_stays():
    pts = {"f0": 1, "points": [POINT] * 30 + [None] * 10 + [[W + 400, H / 2]] * 10 + [POINT] * 40}
    frames = cl.plan(CUE, SPAN, pts)
    assert frames[25].start is not None
    assert frames[35].start is None and frames[35].tip is None  # behind the camera
    assert frames[45].start is None  # outside the safe area
    assert frames[55].start is not None and all(f.box is not None for f in frames.values())  # label drawn throughout


def test_arrow_dropped_when_the_part_is_under_the_label():
    box = Rect(100, 100, 300, 80)
    assert cl.arrow_ends(box, [200, 140]) is None
    assert cl.arrow_ends(box, [200, 1150]) is not None


def test_arrow_pixels_head_and_shaft():
    head = cl.head_rgba((1, 0.5, 0, 1))
    assert head.shape == (cl.HEAD_W, cl.HEAD_LEN, 4) and head.dtype.name == "uint8"
    assert head[cl.HEAD_W // 2, cl.HEAD_LEN - 2, 3] > 0 and head[0, cl.HEAD_LEN - 2, 3] == 0  # tapers to the tip
    assert tuple(head[cl.HEAD_W // 2, 2, :3]) == (255, 128, 0)
    thick = cl.shaft_rgba((1, 0, 0, 1), None, cl.CULPRIT_SHAFT_PX)
    thin = cl.shaft_rgba((1, 0, 0, 1), None, cl.SHAFT_PX)
    assert thick.shape[0] > thin.shape[0] == cl.SHAFT_PX
    edged = cl.shaft_rgba((1, 0, 0, 1), (0, 0, 0, 1), cl.SHAFT_PX)
    assert edged.shape[0] == cl.SHAFT_PX + 2 * cl.OUTLINE_PX and tuple(edged[0, 0, :3]) == (0, 0, 0)
    assert tuple(cl.head_rgba((1, 0, 0, 1), (0, 0, 0, 1))[cl.HEAD_W // 2, 2, :3]) == (255, 0, 0)


# --------------------------------------------------------------------------- anchors.json


def _anchors_file(tmp_path, callouts):
    (tmp_path / "source").mkdir()
    out = {"stamp": freshness.anchors_stamp({"s01": "fp"}, "h"), "shots": {}, "heroes": {}}
    if callouts is not None:
        out["callouts"] = callouts
    (tmp_path / "source" / freshness.ANCHORS).write_text(json.dumps(out), encoding="utf-8")
    return tmp_path


def test_load_callout_anchors_needs_each_callout(tmp_path):
    sdir = _anchors_file(tmp_path, {"s01": {"0": {"f0": 1, "points": [[1, 2]]}}})
    got = freshness.load_callout_anchors(sdir, {"s01": "fp"}, "h", [("s01", 0, "Valve")])
    assert got["s01"]["0"]["points"] == [[1, 2]]
    with pytest.raises(freshness.StaleError, match="Gasket"):
        freshness.load_callout_anchors(sdir, {"s01": "fp"}, "h", [("s01", 1, "Gasket")])
    with pytest.raises(freshness.StaleError):  # the shot content changed since
        freshness.load_callout_anchors(sdir, {"s01": "other"}, "h", [("s01", 0, "Valve")])


def test_reusable_anchors_has_no_callouts_key_without_any(tmp_path):
    sdir = _anchors_file(tmp_path, None)
    assert freshness.reusable_anchors(sdir, {"s01": "fp"}, "h") == {"shots": {}, "heroes": {}}


class _Scene:
    name = "Scene"

    def __init__(self, names):
        self.objects = {n: object() for n in names}


def _callout_anchors(monkeypatch, tmp_path, calls, objects=("Valve",), reuse=None, projected=None):
    from bsa import callouts as callouts_mod
    from bsa.short import Line, Short, Shot
    from bsa.timeline import build_timeline

    shots = [Shot("s01", "Hook", "pic", "Scene", min_s=2.0, callouts=calls)]
    short = Short(slug="t", title="T", length_s=5, lines=[Line("l01", "The valve opens.", "s01")], shots=shots)
    tl = build_timeline(short, {"l01": 2.0})
    scene = _Scene(objects)
    monkeypatch.setattr(bsa_blender, "_scene", lambda data, short, shot_id, h: (None, scene))
    monkeypatch.setattr(callouts_mod, "load", lambda sdir, h: {})
    seen = []
    fake = types.ModuleType("bsa.blender.anchors")
    fake.project = lambda sc, names, f0, f1, at=None: seen.append((names, at)) or {
        names[0]: {"f0": f0, "points": [[1.0, 2.0]] * (f1 - f0 + 1)}}  # fmt: skip
    monkeypatch.setitem(sys.modules, "bsa.blender.anchors", fake)
    projected = set() if projected is None else projected
    got = bsa_blender._callout_anchors(tmp_path, short, tl, "h", None, reuse or {}, projected)
    return got, seen, tl


def test_callout_anchors_are_projected_per_frame_of_the_shot(monkeypatch, tmp_path):
    projected = set()
    call = {"text": "V", "near": "Valve", "on": "valve", "at": [0, 0, 1]}
    got, seen, tl = _callout_anchors(monkeypatch, tmp_path, [call], projected=projected)
    span = tl.shots[0]
    assert len(got["s01"]["0"]["points"]) == span.f1 - span.f0 + 1 and got["s01"]["0"]["f0"] == span.f0
    assert seen == [(["Valve"], (0.0, 0.0, 1.0))] and projected == {"s01"}


def test_no_callouts_projects_nothing_and_adds_no_key(monkeypatch, tmp_path):
    got, seen, _ = _callout_anchors(monkeypatch, tmp_path, [])
    assert got == {} and seen == []


def test_unchanged_callout_anchors_are_reused(monkeypatch, tmp_path):
    prior = {"s01": {"0": {"f0": 1, "points": [[9, 9]]}}}
    projected = set()
    call = {"text": "V", "near": "Valve", "on": "valve"}
    got, seen, _ = _callout_anchors(monkeypatch, tmp_path, [call], reuse=prior, projected=projected)
    assert got == prior and seen == [] and projected == set()


def test_a_callout_object_the_scene_lacks_is_named(monkeypatch, tmp_path):
    with pytest.raises(bsa_blender.CliError, match="Gasket"):
        _callout_anchors(monkeypatch, tmp_path, [{"text": "G", "near": "Gasket", "on": "valve"}])
