"""Stale outputs are refused with a plain message: an old timeline, old frames, old or failed scene checks."""

import json
import os
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
    with pytest.raises(freshness.StaleError, match="timing changed: run CLI timeline 12, then rebuild"):
        freshness.check_timeline(sdir, build_timeline(short, None), "12")


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
    _checks(sdir, {**freshness.checks_stamp("h1", keys, 5), "code": "0:old"})  # the checks' code changed
    with pytest.raises(freshness.StaleError, match="the checks code changed"):
        freshness.require_checks(sdir, "h1", keys)
    _checks(sdir, freshness.checks_stamp("h1", keys, 5), errors=2)
    with pytest.raises(freshness.StaleError, match="2 error"):
        freshness.require_checks(sdir, "h1", keys)


def test_the_final_needs_checks_every_5_frames_or_finer(sdir):
    """A check run with a coarse --every samples 1 frame in N: fine for a look at the preview, not for the final."""
    keys = {"s01": "k1", "s02": "k2"}
    _checks(sdir, freshness.checks_stamp("h1", keys, 50))
    with pytest.raises(freshness.StaleError, match="--every 5 or finer") as err:
        freshness.require_checks(sdir, "h1", keys, "the final edit")
    assert "every 50th frame (--every 50)" in str(err.value) and "the final edit needs" in str(err.value)
    assert "run BLENDER check SHORT --every 5 again" in str(err.value)
    for every in (5, 1):  # the default and finer pass, nothing else changed
        _checks(sdir, freshness.checks_stamp("h1", keys, every))
        freshness.require_checks(sdir, "h1", keys)
    _checks(sdir, freshness.checks_stamp("h1", keys))  # a stamp that does not say how finely it checked
    with pytest.raises(freshness.StaleError, match="can't say how finely") as err:
        freshness.require_checks(sdir, "h1", keys)
    assert "--every 5 or finer" in str(err.value)
    assert freshness.FINAL_MAX_EVERY == 5


def test_failed_checks_count_only_when_they_ran_on_this_build(sdir):
    """The errors review/checks.json found on the build and timing as they are now (what the preview render
    refuses on); none when there are no checks, or they ran on another build, timing or checks code."""
    keys = {"s01": "k1", "s02": "k2"}
    assert freshness.failed_checks(sdir, "h1", keys) == 0
    _checks(sdir, freshness.checks_stamp("h1", keys, 50), errors=3)  # any --every: the preview may check coarsely
    assert freshness.failed_checks(sdir, "h1", keys) == 3
    assert freshness.failed_checks(sdir, "h2", keys) == 0  # another timing
    assert freshness.failed_checks(sdir, "h1", {**keys, "s02": "k2b"}) == 0  # a shot rebuilt since
    _checks(sdir, {**freshness.checks_stamp("h1", keys, 5), "code": "0:old"}, errors=3)
    assert freshness.failed_checks(sdir, "h1", keys) == 0  # older checks code
    _checks(sdir, freshness.checks_stamp("h1", keys, 5))
    assert freshness.failed_checks(sdir, "h1", keys) == 0


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
    """The title, picture and still_at are not checked: rewording them keeps the checks current."""
    _checks(sdir, freshness.checks_stamp("h", {"s01": _inputs()}, 5))
    reworded = _inputs(shot=_shot(title="New", picture="reworded", still_at=0.2))
    freshness.require_checks(sdir, "h", {"s01": reworded})
    assert set(freshness.reusable_checks(sdir, freshness.checks_stamp("h", {"s01": reworded}, 5))) == {"s01"}


@pytest.mark.parametrize("over", [{"badge": "Simplified"}, {"wide": True}])
def test_check_inputs_include_the_badge_and_wide(sdir, over):
    """The badge may cover the subject and `wide` turns the framing checks off: changing either checks again."""
    _checks(sdir, freshness.checks_stamp("h", {"s01": _inputs()}, 5))
    changed = _inputs(shot=_shot(**over))
    assert not freshness.reusable_checks(sdir, freshness.checks_stamp("h", {"s01": changed}, 5))


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
    shots = {"s01": _inputs(), "s02": _inputs(span=Span("s02", 91, 180))}
    msgs = []
    _checks(sdir, freshness.checks_stamp("h", shots, 5))
    with pytest.raises(freshness.StaleError, match="the timing changed since the checks ran") as err:
        freshness.require_checks(sdir, "h2", shots, key="12")
    msgs.append(str(err.value))
    assert "run BLENDER check 12 again" in msgs[-1] and "it re-checks all 2 shots" in msgs[-1]
    _checks(sdir, {**freshness.checks_stamp("h", shots, 5), "code": "1:0000000000000000"})
    with pytest.raises(freshness.StaleError, match="checks code changed since the checks ran") as err:
        freshness.require_checks(sdir, "h", shots, key="12")
    msgs.append(str(err.value))
    # every other refusal: too coarse, no --every, a shot changed, errors, an old stamp-less file, no checks
    for stamp, errors in ((freshness.checks_stamp("h", shots, 50), 0), (freshness.checks_stamp("h", shots), 0),
                          (freshness.checks_stamp("h", {**shots, "s02": _inputs(fp="x")}, 5), 0),
                          (freshness.checks_stamp("h", shots, 5), 2), (None, 0)):  # fmt: skip
        _checks(sdir, stamp, errors)
        with pytest.raises(freshness.StaleError) as err:
            freshness.require_checks(sdir, "h", shots, key="12")
        msgs.append(str(err.value))
    (sdir / "review" / "checks.json").unlink()
    with pytest.raises(freshness.StaleError) as err:
        freshness.require_checks(sdir, "h", shots, key="12")
    msgs.append(str(err.value))
    assert all("BLENDER check 12" in m and "SHORT" not in m for m in msgs), msgs


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _touch_newer(path: Path, than: Path) -> None:
    t = than.stat().st_mtime_ns + 10**9
    os.utime(path, ns=(t, t))


def test_a_newer_imported_script_means_rebuild(sdir):
    src = sdir / "source"
    _write(src / "build.py", "import parts\nfrom gears import teeth\n")
    _write(src / "parts.py", "import helpers\n")
    helpers = _write(src / "helpers.py", "X = 1\n")
    _write(src / "gears" / "__init__.py", "")
    _write(src / "gears" / "teeth.py", "")
    notes = _write(src / "notes.py", "")
    blend = _write(sdir / "blend" / "short.blend", "blend")
    for p in src.rglob("*.py"):  # every script older than the saved .blend
        t = blend.stat().st_mtime_ns - 10**9
        os.utime(p, ns=(t, t))
    assert freshness.stale_build_file(sdir) is None and not freshness.build_changed(sdir)
    assert freshness.build_inputs(sdir) == [src / "build.py", src / "gears" / "__init__.py", src / "gears" / "teeth.py",
                                            src / "helpers.py", src / "parts.py"]  # fmt: skip
    _touch_newer(notes, blend)  # imported by nothing
    assert not freshness.build_changed(sdir)
    _touch_newer(helpers, blend)
    assert freshness.stale_build_file(sdir) == helpers and freshness.build_changed(sdir)
    msg = "source/helpers.py (imported by build.py) changed since the scene was built: rebuild"
    assert freshness.build_changed_message(sdir) == msg
    with pytest.raises(freshness.StaleError, match=r"source/helpers\.py \(imported by build\.py\) changed"):
        freshness.require_current_build(sdir, "the final edit")
    _touch_newer(src / "build.py", blend)  # build.py itself comes first and keeps its own message
    assert freshness.build_changed_message(sdir) == freshness.BUILD_CHANGED


def test_build_inputs_skip_non_local_and_survive_cycles_and_syntax_errors(sdir):
    src = sdir / "source"
    _write(src / "build.py", "import math\nimport numpy as np\nimport bpy\nfrom bsa.blender import bake\nimport a\n")
    _write(src / "a.py", "import b\n")
    _write(src / "b.py", "from . import c\nimport a\n")
    _write(src / "c.py", "def (:\n")
    assert freshness.build_inputs(sdir) == [src / "build.py", src / "a.py", src / "b.py", src / "c.py"]
    assert freshness.build_inputs(sdir.parent / "nowhere") == []


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
    monkeypatch.setattr(freshness, "_CHECKS_CODE", (lf,))
    code = freshness.checks_code()
    monkeypatch.setattr(freshness, "_CHECKS_CODE", (crlf,))
    assert freshness.checks_code() == code


def test_checks_stamped_with_only_the_checks_module_hash_are_stale(sdir):
    """Stamps whose code hash covered bsa/blender/checks.py alone (raw or line endings ignored) can't vouch for the
    viewer checks, the edit's text boxes or the framing notes: the checks run once more."""
    import hashlib

    raw = freshness._CHECKS_CODE[0].read_bytes()
    for data in (raw, raw.replace(b"\r\n", b"\n")):
        code = f"{freshness.CHECKS_VERSION}:{hashlib.sha256(data).hexdigest()[:16]}"
        _checks(sdir, {**freshness.checks_stamp("h", {"s01": _inputs()}, 5), "code": code})
        with pytest.raises(freshness.StaleError, match="checks code changed"):
            freshness.require_checks(sdir, "h", {"s01": _inputs()})


def test_checks_code_covers_every_file_the_findings_come_from():
    """The viewer checks (framing, covered), the edit's text boxes they measure, the framing_call notes and the
    framing band all change what the checks find: a change to any of them means a re-check."""
    root = freshness._CHECKS_CODE[0].parent.parent
    names = {p.relative_to(root).as_posix() for p in freshness._CHECKS_CODE}
    assert {"blender/checks.py", "viewer.py", "overlays.py", "findings.py", "blender/camera.py",
            "layout.py", "rules.py", "blender/bake.py", "pops.py", "blender/anchors.py", "short.py",
            "kitlayout.py"} <= names, names  # fmt: skip
    assert all(p.is_file() for p in freshness._CHECKS_CODE)


def test_a_change_to_the_viewer_code_makes_the_checks_stale(sdir, tmp_path, monkeypatch):
    files = [tmp_path / "checks.py", tmp_path / "viewer.py"]
    for f in files:
        f.write_bytes(b"x = 1\n")
    monkeypatch.setattr(freshness, "_CHECKS_CODE", tuple(files))
    _checks(sdir, freshness.checks_stamp("h", {"s01": _inputs()}, 5))
    freshness.require_checks(sdir, "h", {"s01": _inputs()})
    files[1].write_bytes(b"x = 2\n")  # only viewer.py changed
    with pytest.raises(freshness.StaleError, match="checks code changed"):
        freshness.require_checks(sdir, "h", {"s01": _inputs()})


def test_a_new_caption_size_makes_the_checks_stale(sdir):
    """The caption size moves the caption box the covered check measures: changing it means a re-check."""

    def inputs(size):
        return freshness.shot_check_inputs("fp1", Span("s01", 1, 90), _shot(), ("s01", "s02"), caption_size=size)

    _checks(sdir, freshness.checks_stamp("h", {"s01": inputs(64)}, 5))
    freshness.require_checks(sdir, "h", {"s01": inputs(64)})
    with pytest.raises(freshness.StaleError, match="caption size changed"):
        freshness.require_checks(sdir, "h", {"s01": inputs(96)})


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


def test_object_keys_are_cached_and_never_part_of_the_check_key(sdir):
    """The per-object keys and the rest's key are cached beside the fingerprints and recorded in a shot's check
    inputs as information: the check key stays what it was without them."""
    blend = sdir / "blend" / "short.blend"
    blend.parent.mkdir()
    blend.write_bytes(b"one")
    objects = {"s01": {"objects": {"Cube": "k1", "Sphere": "k2"}, "rest": "r1"}}
    freshness.save_fingerprints(sdir, "h", {"s01": "a"}, objects=objects)
    assert freshness.load_fingerprints(sdir, "h") == {"s01": "a"}
    assert freshness.has_object_keys(sdir)
    assert freshness.load_fingerprint_objects(sdir, {"s01": "a"}) == objects
    assert freshness.load_fingerprint_objects(sdir, {"s01": "b"}) == {}
    assert freshness.load_fingerprint_objects(sdir, None) == {}
    freshness.save_fingerprints(sdir, "h", {"s01": "a"})
    assert freshness.load_fingerprint_objects(sdir, {"s01": "a"}) == {}  # a cache from before the keys
    assert not freshness.has_object_keys(sdir)  # so the next Blender run computes the cache again, keys and all
    freshness.save_fingerprints(sdir, "h", {"s01": "a"}, objects={})
    assert freshness.has_object_keys(sdir)  # computed with keys, even none: never computed again for that
    span = Span("s01", 1, 90)
    plain = freshness.shot_check_inputs("a", span, _shot(), ["s01"])
    keyed = freshness.shot_check_inputs("a", span, _shot(), ["s01"], objects=objects["s01"])
    assert keyed["objects"] == {"Cube": "k1", "Sphere": "k2"} and keyed["rest"] == "r1"
    assert "objects" not in plain and "rest" not in plain
    assert freshness.checks_stamp("h", {"s01": plain})["shots"] == freshness.checks_stamp("h", {"s01": keyed})["shots"]
    assert freshness.blend_stat(sdir) == freshness._blend_stat(sdir) == [3, blend.stat().st_mtime_ns]


def _keyed(sid="s01", objects=None, rest="r1", fp="fp1", span=None, tags=None):
    parts = {"tags": tags} if tags is not None else None
    keys = {"objects": dict(objects or {"A": "ka", "B": "kb", "C": "kc"}), "rest": rest}
    return freshness.shot_check_inputs(fp, span or Span(sid, 1, 90), _shot(id=sid), ("s01", "s02"), parts,
                                       objects=keys)  # fmt: skip


def _reuse(old_stamp, sid, now_stamp):
    now = {**now_stamp["inputs"][sid], "_stamp": {"timeline": now_stamp["timeline"], "every": now_stamp["every"]}}
    return freshness.object_reuse(old_stamp, sid, now)


def test_object_reuse_rules():
    """Which objects of a changed shot keep their own checks: only when nothing but object keys changed (same checks
    code, timing, --every, frames, entry, shot list, rules, caption size, tags and rest), and then only the names
    whose key is the same; None (re-check the whole shot) otherwise."""
    base = {"s01": _keyed("s01", tags="t1"), "s02": _keyed("s02", tags="t1")}
    old = freshness.checks_stamp("h", base, 5)

    def now(sid="s01", every=5, tl="h", **over):
        return freshness.checks_stamp(tl, {**base, sid: _keyed(sid, **{"tags": "t1", **over})}, every)

    assert _reuse(old, "s01", now(objects={"A": "ka2", "B": "kb", "C": "kc"}, fp="fp2")) == {"B", "C"}
    assert _reuse(old, "s02", now("s02", objects={"A": "ka", "B": "kb2", "C": "kc"}, fp="fp2")) == {"A", "C"}
    assert _reuse(old, "s01", now(rest="r2", fp="fp2")) is None  # camera, lights, world, collections...
    assert _reuse(old, "s01", now(tags="t2")) is None  # a tag changed anywhere in the shot
    assert _reuse(old, "s01", now(span=Span("s01", 1, 80), fp="fp2")) is None  # its frames changed
    assert _reuse(old, "s01", now(every=1, fp="fp2")) is None  # --every changed
    assert _reuse(old, "s01", now(tl="h2", fp="fp2")) is None  # the timing changed
    assert _reuse(old, "s01", now(objects={"A": "x", "B": "y", "C": "z"}, fp="fp2")) is None  # every key changed
    assert _reuse({**old, "code": "0:old"}, "s01", now(fp="fp2")) is None  # the checks code changed
    plain = freshness.checks_stamp("h", {sid: _inputs(span=Span(sid, 1, 90), shot=_shot(id=sid)) for sid in base}, 5)
    assert _reuse(plain, "s01", now(fp="fp2")) is None  # old inputs without object keys
    without = freshness.checks_stamp("h", {**base, "s01": _inputs(fp="fp2", parts={"tags": "t1"})}, 5)
    assert freshness.object_reuse(old, "s01", {**without["inputs"]["s01"], "_stamp": {"timeline": "h", "every": 5}}) \
        is None  # new inputs without object keys  # fmt: skip
    new = {**now(fp="fp2")["inputs"]["s01"], "_stamp": {"timeline": "h", "every": 5}}
    assert freshness.object_reuse(old, "s03", new) is None  # a shot the old checks never had


def _row(check, obj=None, pair=None, sid="s01", **over):
    return {"check": check, "severity": "error", "obj": obj, "frame": None, "message": f"{check} {obj or pair}",
            "shot": sid, "shots": [sid], **({"pair": list(pair)} if pair else {}), **over}  # fmt: skip


def _old_rows(sdir, rows, held=()):
    base = {"s01": _keyed("s01", tags="t1"), "s02": _keyed("s02", tags="t1")}
    data = {"stamp": freshness.checks_stamp("h", base, 5), "errors": len(rows), "warnings": 0, "findings": rows,
            "held_back": list(held)}  # fmt: skip
    (sdir / "review" / "checks.json").write_text(json.dumps(data), encoding="utf-8")
    return base


def test_partial_checks_keep_only_rows_of_unchanged_objects_and_pairs(sdir):
    normals_a, materials_b = _row("normals", "A"), _row("materials", "B")
    ab, ac = _row("overlap", "A", ("A", "B")), _row("overlap", "A", ("A", "C"))
    floating, contact = _row("floating", "A"), _row("contact_parent", "A")
    other = _row("normals", "A", sid="s02")
    base = _old_rows(sdir, [normals_a, materials_b, ab, floating, contact, other], held=[ac])
    changed_a = freshness.checks_stamp("h", {**base, "s01": _keyed(objects={"A": "ka2", "B": "kb", "C": "kc"},
                                                                   fp="fp2", tags="t1")}, 5)  # fmt: skip
    assert freshness.partial_checks(sdir, changed_a) == {"s01": ({"B", "C"}, [materials_b])}
    changed_b = freshness.checks_stamp("h", {**base, "s01": _keyed(objects={"A": "ka", "B": "kb2", "C": "kc"},
                                                                   fp="fp2", tags="t1")}, 5)  # fmt: skip
    assert freshness.partial_checks(sdir, changed_b) == {"s01": ({"A", "C"}, [normals_a, ac])}
    assert freshness.partial_checks(sdir, freshness.checks_stamp("h", base, 5)) == {}  # nothing changed: all kept
    retagged = freshness.checks_stamp("h", {**base, "s01": _keyed(fp="fp2", tags="t2")}, 5)
    assert freshness.partial_checks(sdir, retagged) == {}  # re-check the whole shot


def test_an_overlap_row_without_its_pair_checks_the_whole_shot_again(sdir):
    """An overlap row that doesn't name its two objects can't be kept or dropped by object: its shot is checked again
    whole, and the other shot's partial reuse is not affected."""
    no_pair = _row("overlap", "A")  # no "pair"
    other = _row("normals", "A", sid="s02")
    base = _old_rows(sdir, [_row("normals", "B"), no_pair, other])
    changed = freshness.checks_stamp("h", {**base, "s01": _keyed(objects={"A": "ka2", "B": "kb", "C": "kc"},
                                                                 fp="fp2", tags="t1")}, 5)  # fmt: skip
    assert "s01" not in freshness.partial_checks(sdir, changed)
    _old_rows(sdir, [_row("normals", "B"), other])  # the same without that row: s01 is reused by object
    assert freshness.partial_checks(sdir, changed)["s01"][0] == {"B", "C"}


def test_partial_reuse_drops_deleted_and_renamed_objects(sdir):
    """A deleted object's findings are dropped and a pair with it is not kept; a renamed (or new) object has no old
    key, so its checks run and its pairs are tested again."""
    rows = [_row("normals", "C"), _row("overlap", "A", ("A", "C")), _row("materials", "D"), _row("faceted", "A"),
            _row("overlap", "A", ("A", "D")), _row("overlap", "A", ("A", "B"))]  # fmt: skip
    old = {"s01": _keyed(objects={"A": "ka", "B": "kb", "C": "kc", "D": "kd"}, tags="t1"),
           "s02": _keyed("s02", tags="t1")}  # fmt: skip
    data = {"stamp": freshness.checks_stamp("h", old, 5), "errors": 0, "warnings": 0, "findings": rows}
    (sdir / "review" / "checks.json").write_text(json.dumps(data), encoding="utf-8")
    deleted = freshness.checks_stamp("h", {**old, "s01": _keyed(objects={"A": "ka", "B": "kb", "D": "kd"}, fp="fp2",
                                                                tags="t1")}, 5)  # fmt: skip
    kept, kept_rows = freshness.partial_checks(sdir, deleted)["s01"]
    assert kept == {"A", "B", "D"} and not [r for r in kept_rows if "C" in (r["obj"], *r.get("pair", ()))]
    assert kept_rows == [rows[2], rows[3], rows[4], rows[5]]
    renamed = freshness.checks_stamp("h", {**old, "s01": _keyed(objects={"A": "ka", "B": "kb", "C": "kc", "E": "kd"},
                                                                fp="fp2", tags="t1")}, 5)  # fmt: skip
    kept, kept_rows = freshness.partial_checks(sdir, renamed)["s01"]
    assert kept == {"A", "B", "C"} and "E" not in kept
    assert kept_rows == [rows[0], rows[1], rows[3], rows[5]]  # nothing of D, nor of the pair A-D
