import json

import pagefix
import pytest

from bsa.review import notes, snapshot, stages, store


@pytest.mark.parametrize("stage", stages.page_stages(False))
def test_snapshot_at_every_stage(tmp_path, stage):
    sdir = pagefix.make_short(tmp_path / "My Shorts é", stage)
    snap = snapshot.build(sdir, settings={})
    assert snap["schema"] == 1 and snap["current"] == stage and snap["short"]["key"] == sdir.name
    assert [s["id"] for s in snap["stages"]] == list(stages.page_stages(False))
    i = stages.page_stages(False).index(stage)
    assert all(s["status"] == "done" for s in snap["stages"][:i])
    assert snap["stages"][i]["status"] == "current"
    assert all(s["status"] == "locked" for s in snap["stages"][i + 1 :])
    json.dumps(snap)  # serialisable


def test_interview_content_is_read_only_answers(tmp_path):
    sdir = pagefix.make_short(tmp_path, "story")
    c = next(s for s in snapshot.build(sdir, settings={})["stages"] if s["id"] == "interview")["content"]
    assert c == {
        "title": "Why a pendulum keeps perfect time",
        "topic": "simple pendulum",
        "audience": "curious general viewers",
        "length_s": 15,
        "field": "physics",
    }


def test_rebuilding_without_change_keeps_version_and_etag(tmp_path):  # no version churn
    sdir = pagefix.make_short(tmp_path, "story")
    a, b = snapshot.build(sdir, settings={}, now=1), snapshot.build(sdir, settings={}, now=99)
    assert snapshot.etag(a) == snapshot.etag(b)
    assert a["generated"] == 1 and b["generated"] == 99
    assert next(s for s in b["stages"] if s["id"] == "interview")["version"] == 1


def test_all_done_snapshot(tmp_path):
    snap = snapshot.build(pagefix.make_short(tmp_path, "done"), settings={})
    assert all(s["status"] == "done" for s in snap["stages"]) and snap["current"] == "post"


def test_stage_version_none_for_unversioned_or_empty(tmp_path):
    sdir = pagefix.make_short(tmp_path, "story")
    assert snapshot.stage_version(sdir, "render") is None
    assert snapshot.stage_version(sdir, "interview") == 1


def test_damaged_inputs_never_break_or_reset(tmp_path):  # Review Focus 3
    sdir = pagefix.make_short(tmp_path, "story")
    (sdir / "source" / "short.json").write_text('{"title": "half', encoding="utf-8")
    (sdir / "review").mkdir(exist_ok=True)
    (sdir / "review" / "notes.json").write_bytes(b"")
    snap = snapshot.build(sdir, settings={})
    inter = next(s for s in snap["stages"] if s["id"] == "interview")
    assert inter["content"] is None and "short.json" in inter["problem"]
    assert any("notes.json" in p for p in snap["problems"]) and snap["notes"] == []
    assert (sdir / "review" / "notes.json").read_bytes() == b""
    (sdir / "state.json").write_text("{", encoding="utf-8")
    snap = snapshot.build(sdir, settings={})
    assert any("state.json" in p for p in snap["problems"])
    assert (sdir / "state.json").read_text(encoding="utf-8") == "{"


WRONG_TYPE = [("notes.json", "{}"), ("notes.json", '"x"'), ("notes.json", "[1, 2]"), ("decisions.json", "{}"),
              ("decisions.json", "null"), ("versions.json", "[]"), ("versions.json", '"x"')]  # fmt: skip


@pytest.mark.parametrize(("name", "body"), WRONG_TYPE)
def test_wrong_type_store_files_become_problem_lines(tmp_path, name, body):
    sdir = pagefix.make_short(tmp_path, "story")
    (sdir / "review" / name).write_text(body, encoding="utf-8")
    snap = snapshot.build(sdir, settings={})
    lines = snap["problems"] + [s["problem"] or "" for s in snap["stages"]]
    assert any(name in p for p in lines)
    assert (sdir / "review" / name).read_text(encoding="utf-8") == body
    if name == "notes.json":
        assert snap["notes"] == []


def test_store_loaders_raise_damaged_for_wrong_type(tmp_path):
    sdir = tmp_path
    (sdir / "review").mkdir()
    (sdir / "review" / "notes.json").write_text("{}", encoding="utf-8")
    with pytest.raises(store.StoreDamaged, match="notes.json"):
        notes.load(sdir)


def test_health_is_the_doctor_checks_that_are_not_ok(tmp_path):
    sdir = pagefix.make_short(tmp_path, "story")
    assert snapshot.build(sdir)["health"] == []  # no doctor.json yet
    checks = [{"id": "blender", "level": "ok", "text": "Blender 5.2", "fix": None},
              {"id": "disk", "level": "warn", "text": "Only 3 GB free.", "fix": "Free some space."},
              {"id": "voice", "level": "error", "text": "No voice.", "fix": None}, "junk"]  # fmt: skip
    (sdir / "review" / "doctor.json").write_text(json.dumps({"at": 1, "checks": checks}), "utf-8")
    assert snapshot.build(sdir)["health"] == [
        {"id": "disk", "level": "warn", "text": "Only 3 GB free.", "fix": "Free some space."},
        {"id": "voice", "level": "error", "text": "No voice.", "fix": None},
    ]
    (sdir / "review" / "doctor.json").write_text("not json", "utf-8")
    assert snapshot.build(sdir)["health"] == [] and snapshot.build(sdir)["phone"] is None


def test_an_unchanged_snapshot_never_rewrites_versions_json(tmp_path):
    import os

    sdir = pagefix.make_short(tmp_path, "final")
    snapshot.build(sdir, settings={})
    path = sdir / "review" / "versions.json"
    os.utime(path, ns=(1_000_000_000, 1_000_000_000))
    snapshot.build(sdir, settings={})
    assert path.stat().st_mtime_ns == 1_000_000_000


def test_storyboard_shows_a_window_overrun_as_its_problem(tmp_path):
    sdir = pagefix.make_short(tmp_path, "storyboard")
    path = sdir / "source" / "short.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["camera"] = {"style": "one-take", "seconds": 13}
    for shot, at in zip(data["shots"], (0, 3.5, 4), strict=True):  # s02's two lines in half a second
        shot["at"] = at
    path.write_text(json.dumps(data), encoding="utf-8")
    snap = snapshot.build(sdir, settings={})
    by = {s["id"]: s for s in snap["stages"]}
    board = by["storyboard"]
    assert board["problem"].startswith("s02's lines need ") and "move s03's start" in board["problem"]
    assert board["content"]["timing_problem"] == board["problem"]
    assert [s["id"] for s in board["content"]["shots"]] == ["s01", "s02", "s03"]  # the shots still show
    assert board["content"]["shots"][1]["time"] == "3.5 s to 4.0 s"  # its window
    assert by["story"]["problem"] is None and by["story"]["content"] is not None  # other stages unaffected
    assert "Traceback" not in json.dumps(snap)
