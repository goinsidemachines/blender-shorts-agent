"""Stale outputs are refused with a plain message: an old timeline, old frames, old or failed scene checks."""

import json
from pathlib import Path

import pytest

from bsa import freshness
from bsa.short import load_short
from bsa.timeline import Span, build_timeline, timeline_hash, to_json

ROOT = Path(__file__).resolve().parent.parent
SAMPLE = ROOT / "tests" / "e2e" / "pendulum" / "source" / "short.json"


@pytest.fixture
def sdir(tmp_path):
    d = tmp_path / "01-pendulum"
    for sub in ("source", "review", "frames"):
        (d / sub).mkdir(parents=True)
    (d / "source" / "short.json").write_text(SAMPLE.read_text(encoding="utf-8"), encoding="utf-8")
    return d


def _save_timeline(sdir, tl):
    (sdir / "source" / "timeline.json").write_text(json.dumps(to_json(tl), indent=2), encoding="utf-8")


def test_check_timeline_returns_the_saved_hash(sdir):
    tl = build_timeline(load_short(sdir / "source" / "short.json"), None)
    _save_timeline(sdir, tl)
    assert freshness.check_timeline(sdir, tl) == timeline_hash(to_json(tl))


def test_check_timeline_missing_file(sdir):
    tl = build_timeline(load_short(sdir / "source" / "short.json"), None)
    with pytest.raises(freshness.StaleError, match="timeline.json is missing"):
        freshness.check_timeline(sdir, tl)


def test_check_timeline_detects_changed_timing(sdir):
    short = load_short(sdir / "source" / "short.json")
    _save_timeline(sdir, build_timeline(short, None))
    short.lines[0].text += " and a few more words so the line runs longer"
    with pytest.raises(freshness.StaleError, match="timing changed: run CLI timeline"):
        freshness.check_timeline(sdir, build_timeline(short, None))


def test_timeline_hash_ignores_key_order():
    a = {"fps": 30, "n_frames": 10, "shots": []}
    assert timeline_hash(a) == timeline_hash(dict(reversed(a.items())))
    assert timeline_hash(a) != timeline_hash({**a, "n_frames": 11})


def test_scene_stamp():
    freshness.check_scene_stamp("Workshop", "abc", "abc")
    with pytest.raises(freshness.StaleError, match="no timeline stamp"):
        freshness.check_scene_stamp("Workshop", None, "abc")
    with pytest.raises(freshness.StaleError, match="built for another timing"):
        freshness.check_scene_stamp("Workshop", "old", "abc")


def _frames(folder: Path, f0: int, f1: int, stamp: dict) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    for f in range(f0, f1 + 1):
        (folder / f"{f:04d}.png").write_bytes(b"png")
    (folder / ".render.json").write_text(json.dumps(stamp), encoding="utf-8")


def test_rendered_frames_ok(tmp_path):
    span = Span("s01", 5, 9)
    _frames(tmp_path / "s01", 5, 9, freshness.frames_stamp(span, "eevee", "fp1", "preview"))
    got = freshness.rendered_frames(tmp_path / "s01", span, "eevee", "fp1", "preview")
    assert [p.name for p in got] == ["0005.png", "0006.png", "0007.png", "0008.png", "0009.png"]
    assert freshness.rendered_frames(tmp_path / "s01", span, "eevee", None, "preview") == got  # no .blend: no check


@pytest.mark.parametrize(
    ("stamp", "match"),
    [
        (None, "no preview frames"),
        ({"f0": 1, "f1": 5, "engine": "eevee", "preset": "preview", "fingerprint": "fp1"}, "older timing"),
        ({"f0": 5, "f1": 9, "engine": "cycles", "preset": "preview", "fingerprint": "fp1"}, "another engine"),
        ({"f0": 5, "f1": 9, "engine": "eevee", "preset": "preview", "fingerprint": "fp0"}, "fingerprint changed"),
        ({"f0": 5, "f1": 9, "engine": "eevee", "blend": 123}, "older plugin version"),  # stamped by an older version
    ],
)
def test_rendered_frames_refuses_a_stale_stamp(tmp_path, stamp, match):
    folder = tmp_path / "s01"
    _frames(folder, 5, 9, stamp or {})
    if stamp is None:
        (folder / ".render.json").unlink()
    with pytest.raises(freshness.StaleError, match=match):
        freshness.rendered_frames(folder, Span("s01", 5, 9), "eevee", "fp1", "preview")


def test_rendered_frames_refuses_missing_empty_or_extra_frames(tmp_path):
    span = Span("s01", 5, 9)
    folder = tmp_path / "s01"
    _frames(folder, 5, 8, freshness.frames_stamp(span, "eevee", "a", "final"))  # 0009 missing: never padded
    with pytest.raises(freshness.StaleError, match="0009.png"):
        freshness.rendered_frames(folder, span, "eevee", "a", "final")
    (folder / "0009.png").write_bytes(b"")
    with pytest.raises(freshness.StaleError, match="0009.png"):
        freshness.rendered_frames(folder, span, "eevee", "a", "final")
    (folder / "0009.png").write_bytes(b"png")
    (folder / "0010.png").write_bytes(b"png")
    with pytest.raises(freshness.StaleError, match="0010.png"):
        freshness.rendered_frames(folder, span, "eevee", "a", "final")


def test_prepare_frames_keeps_same_stamp_and_clears_others(tmp_path):
    folder = tmp_path / "s01"
    stamp = freshness.frames_stamp(Span("s01", 1, 3), "eevee", "fp", "preview")
    _frames(folder, 1, 3, stamp)
    assert freshness.prepare_frames(folder, stamp) is None
    assert len(list(folder.glob("*.png"))) == 3
    assert freshness.prepare_frames(folder, {**stamp, "fingerprint": "other"}) == "fingerprint changed"
    assert not list(folder.glob("*.png"))
    _frames(folder, 1, 3, stamp)
    assert freshness.prepare_frames(folder, {**stamp, "f1": 4}) == "new range"
    assert not list(folder.glob("*.png"))
    assert freshness.prepare_frames(tmp_path / "new", stamp) == "not rendered yet"


def test_frame_specs_round_trip():
    assert freshness.parse_frames("120-123,200, 7") == [7, 120, 121, 122, 123, 200]
    assert freshness.frames_spec([7, 120, 121, 122, 123, 200]) == "7,120-123,200"
    for bad in ("", "a-b", "9-3"):
        with pytest.raises(ValueError, match="--frames"):
            freshness.parse_frames(bad)


def test_redo_frames_deletes_only_those_frames(tmp_path):
    span = Span("s01", 1, 6)
    folder = tmp_path / "s01"
    stamp = freshness.frames_stamp(span, "eevee", "fp1", "preview")
    _frames(folder, 1, 6, stamp)
    assert freshness.redo_frames(folder, span, stamp, [2, 3]) == {"frames": [2, 3], "mixed": False}
    assert sorted(p.name for p in folder.glob("*.png")) == ["0001.png", "0004.png", "0005.png", "0006.png"]
    assert json.loads((folder / ".render.json").read_text(encoding="utf-8")) == stamp
    with pytest.raises(freshness.StaleError, match="outside shot s01"):
        freshness.redo_frames(folder, span, stamp, [9])
    with pytest.raises(freshness.StaleError, match="new range"):
        freshness.redo_frames(folder, span, {**stamp, "f1": 7}, [2])


def test_redo_frames_after_an_edit_patches_and_says_so(tmp_path):
    """The scene changed (new fingerprint) but only flagged frames are redone: the rest stay, the stamp says so."""
    span = Span("s01", 1, 6)
    folder = tmp_path / "s01"
    old = freshness.frames_stamp(span, "eevee", "fp1", "preview")
    _frames(folder, 1, 6, old)
    new = {**old, "fingerprint": "fp2"}
    assert freshness.redo_frames(folder, span, new, [4]) == {"frames": [4], "mixed": True}
    had = json.loads((folder / ".render.json").read_text(encoding="utf-8"))
    assert had["fingerprint"] == "fp2" and had["patched"] == {"from": "fp1", "frames": [4]}
    (folder / "0004.png").write_bytes(b"png")
    assert freshness.prepare_frames(folder, new) is None  # a normal render keeps the patched shot
    assert len(freshness.rendered_frames(folder, span, "eevee", "fp2", "preview")) == 6
    assert freshness.redo_frames(folder, span, new, [5])["mixed"] is True
    had = json.loads((folder / ".render.json").read_text(encoding="utf-8"))
    assert had["patched"] == {"from": "fp1", "frames": [4, 5]}


def test_fingerprint_cache_is_of_one_saved_blend(sdir, tmp_path):
    blend = sdir / "blend" / "short.blend"
    blend.parent.mkdir()
    blend.write_bytes(b"one")
    tex = tmp_path / "tex.png"
    tex.write_bytes(b"x")
    freshness.save_fingerprints(sdir, "h", {"s01": "a"}, {str(tex): [1, tex.stat().st_mtime_ns]})
    assert freshness.load_fingerprints(sdir, "h") == {"s01": "a"}
    assert freshness.load_fingerprints(sdir, "other timing") is None
    tex.write_bytes(b"xy")  # a file the scene reads changed
    assert freshness.load_fingerprints(sdir, "h") is None
    freshness.save_fingerprints(sdir, "h", {"s01": "a"})
    blend.write_bytes(b"saved again")
    assert freshness.load_fingerprints(sdir, "h") is None


def _checks(sdir, stamp, errors=0, findings=()):
    data = {"stamp": stamp, "errors": errors, "warnings": 0, "findings": list(findings)}
    (sdir / "review" / "checks.json").write_text(json.dumps(data), encoding="utf-8")


def test_require_checks(sdir):
    keys = {"s01": "k1", "s02": "k2"}
    with pytest.raises(freshness.StaleError, match="checks have not run"):
        freshness.require_checks(sdir, "h1", keys)
    _checks(sdir, freshness.checks_stamp("h1", keys, 5))
    freshness.require_checks(sdir, "h1", keys)
    with pytest.raises(freshness.StaleError, match="shot s02 changed since the checks ran"):
        freshness.require_checks(sdir, "h1", {**keys, "s02": "k2b"})
    with pytest.raises(freshness.StaleError, match="changed since the checks ran"):
        freshness.require_checks(sdir, "h2", keys)
    _checks(sdir, None)  # written before checks were stamped
    with pytest.raises(freshness.StaleError, match="changed since the checks ran"):
        freshness.require_checks(sdir, "h1", keys)
    _checks(sdir, {**freshness.checks_stamp("h1", keys), "code": "0:old"})  # the checks' code changed
    with pytest.raises(freshness.StaleError, match="the checks code changed"):
        freshness.require_checks(sdir, "h1", keys)
    _checks(sdir, freshness.checks_stamp("h1", keys), errors=2)
    with pytest.raises(freshness.StaleError, match="2 error"):
        freshness.require_checks(sdir, "h1", keys)


def test_reusable_checks_keep_unchanged_shots_findings(sdir):
    keys = {"s01": "k1", "s02": "k2"}
    one = {"check": "pop", "severity": "warning", "frame": 3, "shot": "s01", "shots": ["s01"]}
    both = {"check": "names", "severity": "warning", "frame": None, "shot": "s01", "shots": ["s01", "s02"]}
    two = {"check": "pop", "severity": "error", "frame": 40, "shot": "s02", "shots": ["s02"]}
    _checks(sdir, freshness.checks_stamp("h", keys, 5), findings=[one, both, two])
    now = freshness.checks_stamp("h", {**keys, "s02": "changed"}, 5)
    assert freshness.reusable_checks(sdir, now) == {"s01": [one, both]}
    assert freshness.reusable_checks(sdir, freshness.checks_stamp("h", keys, 1)) == {}  # --every changed
    assert freshness.reusable_checks(sdir, {**now, "code": "0:old"}) == {}  # the checks' code changed
    assert set(freshness.reusable_checks(sdir, freshness.checks_stamp("h", keys, 5))) == {"s01", "s02"}


def test_reusable_checks_keep_findings_listed_once_and_pairs_held_back(sdir):
    """A finding listed once for two shots is kept for whichever of them did not change; findings the overlap cap
    held back are kept too, and the "not listed" finding is not (the cap runs again over every shot)."""
    keys = {"s01": "k1", "s02": "k2"}
    both = {"check": "overlap", "severity": "error", "frame": None, "shot": "s01", "shots": ["s01", "s02"],
            "pair": ["A", "B"]}
    held = {"check": "overlap", "severity": "error", "frame": 50, "shot": "s02", "shots": ["s02"], "pair": ["C", "D"]}
    summary = {"check": "overlap", "severity": "error", "frame": None, "shot": "s02", "shots": ["s02"], "capped": 1}
    data = {"stamp": freshness.checks_stamp("h", keys, 5), "errors": 2, "warnings": 0, "findings": [both, summary],
            "held_back": [held]}
    (sdir / "review" / "checks.json").write_text(json.dumps(data), encoding="utf-8")
    now = freshness.checks_stamp("h", {**keys, "s01": "changed"}, 5)
    assert freshness.reusable_checks(sdir, now) == {"s02": [{**both, "shot": "s02"}, held]}


def test_anchors_must_exist_and_match_the_shot_and_timing(sdir):
    fps = {"s01": "a", "s02": "b"}
    with pytest.raises(freshness.StaleError, match="run BLENDER anchors SHORT"):
        freshness.load_anchors(sdir, fps, "abc")
    data = {"stamp": freshness.anchors_stamp(fps, "abc"), "shots": {"s01": {"Ball": {"f0": 1, "points": [[1, 2]]}}}}
    (sdir / "source" / "anchors.json").write_text(json.dumps(data), encoding="utf-8")
    need = [("s01", "Ball", "Ball")]
    assert freshness.load_anchors(sdir, fps, "abc", need) == data["shots"]
    assert freshness.load_anchors(sdir, {**fps, "s02": "changed"}, "abc", need) == data["shots"]  # s02 not needed
    for now, tl_hash in (({**fps, "s01": "changed"}, "abc"), (fps, "abd")):
        with pytest.raises(freshness.StaleError, match="run BLENDER anchors SHORT"):
            freshness.load_anchors(sdir, now, tl_hash, need)
    assert freshness.reusable_anchors(sdir, {**fps, "s01": "changed"}, "abc") == {"shots": {}, "heroes": {}}
    assert freshness.reusable_anchors(sdir, fps, "abc")["shots"] == data["shots"]


def test_anchors_must_hold_every_near_label(sdir):
    fps = {"s01": "a", "s02": "b"}
    data = {"stamp": freshness.anchors_stamp(fps, "h"), "shots": {"s01": {"Ball": {"f0": 1, "points": [[1, 2]]}}}}
    (sdir / "source" / "anchors.json").write_text(json.dumps(data), encoding="utf-8")
    assert freshness.load_anchors(sdir, fps, "h", [("s01", "Ball", "Ball")]) == data["shots"]
    with pytest.raises(freshness.StaleError, match=r"no anchors for label 'Cube' near 'Cube' in shot s02: run BLENDER"):
        freshness.load_anchors(sdir, fps, "h", [("s01", "Ball", "Ball"), ("s02", "Cube", "Cube")])


def test_hero_boxes_come_from_current_anchors(sdir):
    fps = {"s01": "a", "s03": "c"}
    with pytest.raises(freshness.StaleError, match="run BLENDER anchors SHORT"):
        freshness.load_hero_boxes(sdir, fps, "h", "s03")
    heroes = {"s03": {"f0": 5, "boxes": {"Wheel": [[1, 2, 3, 4], None]}}}
    data = {"stamp": freshness.anchors_stamp(fps, "h"), "shots": {}, "heroes": heroes}
    (sdir / "source" / "anchors.json").write_text(json.dumps(data), encoding="utf-8")
    assert freshness.load_hero_boxes(sdir, fps, "h", "s03") == heroes
    assert freshness.load_hero_boxes(sdir, {**fps, "s01": "changed"}, "h", "s03") == heroes  # another shot changed
    with pytest.raises(freshness.StaleError, match="older build or timing"):
        freshness.load_hero_boxes(sdir, {**fps, "s03": "changed"}, "h", "s03")


def test_frames_from_before_fingerprints_say_so_not_changed():
    from bsa.freshness import OLD_FRAMES, frames_reason

    old = {"f0": 1, "f1": 10, "engine": "eevee", "preset": "preview"}
    new = {**old, "fingerprint": "abc"}
    assert frames_reason(old, new) == OLD_FRAMES
    assert frames_reason({**old, "fingerprint": "xyz"}, new) == "fingerprint changed"


def test_stamps_from_before_presets_and_fingerprints_say_older_version():
    from bsa.freshness import OLD_FRAMES, frames_reason

    old = {"f0": 1, "f1": 10, "engine": "eevee", "blend": 123}  # no preset, no fingerprint
    assert frames_reason(old, {"f0": 1, "f1": 10, "engine": "eevee", "preset": "final", "fingerprint": "a"}) == OLD_FRAMES


# ---- issue #12: say which input changed; post-only fields and unchanged re-runs never refuse


def _shot(**over):
    return {"id": "s01", "title": "Hook", "picture": "a lever", "scene": "Lever", "min_s": 3.0, "still_at": 0.5,
            "labels": [], "badge": None, **over}  # fmt: skip


def _inputs(fp="fp1", span=None, shot=None, ids=("s01", "s02"), parts=None):
    return freshness.shot_check_inputs(fp, span or Span("s01", 1, 90), shot or _shot(), ids, parts)


_PARTS = {"data:Camera": "c0", "data:Object": "o1", "animation": "a1", "code": "x"}


def test_a_tag_change_alone_makes_the_checks_run_again(sdir):
    """Tags (a contact:<other> tag added in build.py) change what the checks find but not a pixel, so they are not
    in the fingerprint: the checks key on the fingerprint's "tags" part instead, and say it changed."""
    _checks(sdir, freshness.checks_stamp("h", {"s01": _inputs(parts={**_PARTS, "tags": "t1"})}, 5))
    retagged = _inputs(parts={**_PARTS, "tags": "t2"})
    assert freshness.reusable_checks(sdir, freshness.checks_stamp("h", {"s01": retagged}, 5)) == {}
    with pytest.raises(freshness.StaleError, match=r"shot s01's tags changed"):
        freshness.require_checks(sdir, "h", {"s01": retagged})
    same = _inputs(parts={**_PARTS, "tags": "t1", "data:Camera": "c9"})  # other parts are information only
    freshness.require_checks(sdir, "h", {"s01": same})


def test_check_inputs_ignore_fields_the_checks_never_read(sdir):
    """The title, picture, still_at and badge are not checked: rewording them keeps the checks current."""
    _checks(sdir, freshness.checks_stamp("h", {"s01": _inputs()}, 5))
    reworded = _inputs(shot=_shot(title="New", picture="reworded", still_at=0.2, badge="Simplified"))
    freshness.require_checks(sdir, "h", {"s01": reworded})
    assert set(freshness.reusable_checks(sdir, freshness.checks_stamp("h", {"s01": reworded}, 5))) == {"s01"}


@pytest.mark.parametrize(
    ("now", "match"),
    [
        (_inputs(fp="fp2"), r"shot s01's scene content changed"),
        (_inputs(fp="fp2", parts={**_PARTS, "data:Camera": "c1", "data:Object": "o2"}),
         r"shot s01's camera/objects changed"),
        (_inputs(fp="fp2", parts={**_PARTS, "animation": "a2"}), r"shot s01's animation changed"),
        (_inputs(shot=_shot(labels=[{"text": "Pivot", "near": "Pivot"}])),
         r"short\.json shot s01 entry changed \(fields: labels\)"),
        (_inputs(shot=_shot(scene="Other")), r"short\.json shot s01 entry changed \(fields: scene\)"),
        (_inputs(span=Span("s01", 1, 120)), r"shot s01's frames changed \(1-90, now 1-120\)"),
        (_inputs(ids=("s01", "s02", "s03")), r"the shots in short\.json changed \(added s03\)"),
    ],
)
def test_require_checks_says_which_input_changed(sdir, now, match):
    _checks(sdir, freshness.checks_stamp("h", {"s01": _inputs(parts=_PARTS)}, 5))
    with pytest.raises(freshness.StaleError, match=match) as err:
        freshness.require_checks(sdir, "h", {"s01": now}, "the final edit")
    assert "since the checks ran" in str(err.value) and "before the final edit" in str(err.value)


def test_hard_rules_that_scan_names_are_a_check_input_only_when_set(sdir):
    assert "rules" not in _inputs()  # users without such rules keep their checks across the update
    key = [["r1", "no_humans", []]]
    with_rules = freshness.shot_check_inputs("fp1", Span("s01", 1, 90), _shot(), ("s01", "s02"), None, key)
    _checks(sdir, freshness.checks_stamp("h", {"s01": _inputs()}, 5))
    with pytest.raises(freshness.StaleError, match="hard rules"):
        freshness.require_checks(sdir, "h", {"s01": with_rules})
    _checks(sdir, freshness.checks_stamp("h", {"s01": with_rules}, 5))
    freshness.require_checks(sdir, "h", {"s01": with_rules})


def test_require_checks_names_timing_and_code_separately(sdir):
    _checks(sdir, freshness.checks_stamp("h", {"s01": _inputs()}, 5))
    with pytest.raises(freshness.StaleError, match="the timing changed since the checks ran"):
        freshness.require_checks(sdir, "h2", {"s01": _inputs()})
    _checks(sdir, {**freshness.checks_stamp("h", {"s01": _inputs()}, 5), "code": "1:0000000000000000"})
    with pytest.raises(freshness.StaleError, match="checks code changed since the checks ran"):
        freshness.require_checks(sdir, "h", {"s01": _inputs()})


def test_checks_from_before_check_inputs_stay_current(sdir):
    """checks.json written before the inputs were recorded (its shot keys hash the whole short.json entry) still
    holds when nothing changed: no re-check after the plugin update."""
    shot = _shot()
    old = {"code": freshness.checks_code(), "timeline": "h", "every": 5,
           "shots": {"s01": freshness.shot_check_key("fp1", Span("s01", 1, 90), shot)}}  # fmt: skip
    _checks(sdir, old)
    freshness.require_checks(sdir, "h", {"s01": _inputs(shot=shot)})
    stamp = freshness.checks_stamp("h", {"s01": _inputs(shot=shot)}, 5)
    assert set(freshness.reusable_checks(sdir, stamp)) == {"s01"}
    with pytest.raises(freshness.StaleError, match=r"shot s01 changed \(the checks ran on an older plugin version"):
        freshness.require_checks(sdir, "h", {"s01": _inputs(fp="fp2", shot=shot)})


def test_checks_code_ignores_line_endings(tmp_path, monkeypatch):
    lf, crlf = tmp_path / "lf.py", tmp_path / "crlf.py"
    lf.write_bytes(b"a = 1\nb = 2\n")
    crlf.write_bytes(b"a = 1\r\nb = 2\r\n")
    monkeypatch.setattr(freshness, "_CHECKS_CODE", lf)
    code = freshness.checks_code()
    monkeypatch.setattr(freshness, "_CHECKS_CODE", crlf)
    assert freshness.checks_code() == code


def test_checks_stamped_with_the_raw_code_hash_stay_current(sdir):
    """Stamps written while the code hash covered the file's raw bytes (CRLF on a Windows checkout) still match."""
    import hashlib

    raw = hashlib.sha256(freshness._CHECKS_CODE.read_bytes()).hexdigest()[:16]
    _checks(sdir, {**freshness.checks_stamp("h", {"s01": _inputs()}, 5), "code": f"{freshness.CHECKS_VERSION}:{raw}"})
    freshness.require_checks(sdir, "h", {"s01": _inputs()})


def test_frame_stamps_with_parts_match_stamps_without(tmp_path):
    """What changed (parts) is information: frames stamped before it was recorded are still current."""
    span = Span("s01", 1, 3)
    folder = tmp_path / "s01"
    plain = freshness.frames_stamp(span, "eevee", "fp", "final")
    _frames(folder, 1, 3, plain)
    with_parts = freshness.frames_stamp(span, "eevee", "fp", "final", {"animation": "a"})
    assert freshness.frames_reason(plain, with_parts) is None
    assert freshness.prepare_frames(folder, with_parts) is None and len(list(folder.glob("*.png"))) == 3
    assert json.loads((folder / ".render.json").read_text(encoding="utf-8"))["parts"] == {"animation": "a"}
    assert len(freshness.rendered_frames(folder, span, "eevee", "fp", "final", {"animation": "b"})) == 3


def test_rendered_frames_say_which_part_of_the_shot_changed(tmp_path):
    span = Span("s01", 1, 3)
    folder = tmp_path / "s01"
    _frames(folder, 1, 3, freshness.frames_stamp(span, "eevee", "fp1", "final", {**_PARTS, "data:Camera": "c1"}))
    with pytest.raises(freshness.StaleError, match="shot s01's camera changed since its final frames were rendered"):
        freshness.rendered_frames(folder, span, "eevee", "fp2", "final", {**_PARTS, "data:Camera": "c2"})
    with pytest.raises(freshness.StaleError, match="render code or Blender version changed"):
        freshness.rendered_frames(folder, span, "eevee", "fp2", "final", {**_PARTS, "data:Camera": "c1", "code": "y"})


def test_fingerprint_cache_is_of_one_render_code(sdir):
    """Cached fingerprints from another Blender or plugin render code are not used (they would differ from a fresh
    fingerprint); a cache from before the code was recorded still is."""
    blend = sdir / "blend" / "short.blend"
    blend.parent.mkdir()
    blend.write_bytes(b"one")
    freshness.save_fingerprints(sdir, "h", {"s01": "a"}, code="5.2:x", parts={"s01": {"animation": "p"}})
    assert freshness.load_fingerprints(sdir, "h", code="5.2:x") == {"s01": "a"}
    assert freshness.load_fingerprint_parts(sdir, {"s01": "a"}) == {"s01": {"animation": "p"}}
    assert freshness.load_fingerprint_parts(sdir, {"s01": "b"}) == {}
    assert freshness.load_fingerprints(sdir, "h", code="5.3:x") is None
    freshness.save_fingerprints(sdir, "h", {"s01": "a"})
    assert freshness.load_fingerprints(sdir, "h", code="5.3:x") == {"s01": "a"}


def test_a_change_shared_by_every_shot_is_said_once(sdir):
    """Clearing a word in rules_check changes every shot's rules input: the refusal says it once, for all shots."""
    ids = [f"s0{i}" for i in range(1, 8)]
    old_key, new_key = [["r1", "no_humans", []]], [["r1", "no_humans", ["body"]]]

    def inputs(key, sid, fp="fp1"):
        return freshness.shot_check_inputs(fp, Span(sid, 1, 90), _shot(id=sid), ids, None, key)

    _checks(sdir, freshness.checks_stamp("h", {sid: inputs(old_key, sid) for sid in ids}, 5))
    now = {sid: inputs(new_key, sid) for sid in ids}
    now["s03"] = inputs(new_key, "s03", fp="fp2")
    with pytest.raises(freshness.StaleError) as err:
        freshness.require_checks(sdir, "h", now)
    msg = str(err.value)
    assert msg.count("hard rules") == 1 and "(all 7 shots)" in msg
    assert "shot s03's scene content changed" in msg
    assert "run BLENDER check SHORT" in msg
