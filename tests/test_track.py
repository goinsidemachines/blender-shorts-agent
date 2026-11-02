"""track.build: the timeline rows of one run, from the timeline, the events, the checks and the edit record."""

import json

import pagefix
import pytest

from bsa.review import track
from bsa.short import load_short


@pytest.fixture
def sdir(tmp_path):
    sdir = pagefix.make_short(tmp_path, "final")
    pagefix.write_timeline(sdir, frames=90)
    (sdir / "out").mkdir(exist_ok=True)
    if not (sdir / "out" / "pendulum.mp4").is_file():  # no ffmpeg: a stand-in, never played
        (sdir / "out" / "pendulum.mp4").write_bytes(bytes(2000))
    pagefix.record_edit(sdir, "final")
    return sdir


def _build(sdir, key="final"):
    return track.build(sdir, load_short(sdir / "source" / "short.json"), key)


def _frame_checks(sdir, key, rows):
    (sdir / "review" / f"frame-checks-{key}.json").write_text(json.dumps({"findings": rows}), encoding="utf-8")


def _short_json(sdir, edit):
    path = sdir / "source" / "short.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    edit(data)
    path.write_text(json.dumps(data), encoding="utf-8")


def test_track_rows_come_from_the_timeline_events_and_checks(sdir):
    tl = _build(sdir)
    assert [s["id"] for s in tl["tracks"]["shots"]] == ["s01", "s02", "s03"] and tl["fps"] == 30
    assert tl["tracks"]["findings"][0] == {**tl["tracks"]["findings"][0], "f": 12, "shot": "s02", "severity": "error"}
    assert tl["video"]["file"] == "out/pendulum.mp4" and tl["stale"] is False and tl["run"] == "final"
    assert tl["frames"] == 90 and tl["duration_s"] == 3.0 and tl["problem"] is None 
    assert [u["why"] for u in tl["unplaced"]] == ["no frame"]  # the fixture's lit warning names no frame
    assert tl["tracks"]["captions"][0] == {"f0": 1, "f1": 10, "text": "caption 1", "line": "l01"}
    assert tl["tracks"]["closing"] is None and set(tl["tracks"]) == set(track.ROWS)


def test_sound_frames_round_down_to_the_frame_on_screen(sdir):
    s2 = _build(sdir)["tracks"]["shots"][1]["f0"]
    assert _build(sdir)["tracks"]["sounds"] == [{"f": s2 + 6, "kind": "whoosh", "label": "whoosh"}]
    _short_json(sdir, lambda d: d["sfx"][0].update(at_s=0.199))
    assert _build(sdir)["tracks"]["sounds"][0]["f"] == s2 + 5


def test_event_sounds_join_and_a_bad_events_file_is_a_problem(sdir):
    events = sdir / "source" / "events.json"
    events.write_text(json.dumps([{"kind": "click", "frame": 5}]), encoding="utf-8")
    kinds = [s["kind"] for s in _build(sdir)["tracks"]["sounds"]]
    assert kinds == ["click", "whoosh"]
    events.write_text("not json", encoding="utf-8")
    tl = _build(sdir)
    assert [s["kind"] for s in tl["tracks"]["sounds"]] == ["whoosh"] and tl["problem"]


def test_closing_hold_extends_the_frames(sdir):
    pagefix.record_edit(sdir, "final", closing=45)
    tl = _build(sdir)
    assert tl["tracks"]["closing"] == {"f0": 91, "f1": 135} and tl["frames"] == 135 and tl["duration_s"] == 4.5


def test_holds_become_ranges(sdir):
    _short_json(sdir, lambda d: d["shots"][1].update(hold=[[0, 0.5]]))
    assert _build(sdir)["tracks"]["holds"] == [{"f0": 31, "f1": 46, "shot": "s02"}]


def test_each_run_reads_only_its_own_frame_checks(sdir):
    pagefix.record_edit(sdir, "preview-eevee")
    _frame_checks(sdir, "preview-eevee", [{"check": "blur", "severity": "warning", "message": "soft", "frame": 40,
                                          "f1": 44, "shots": ["s02"]}])  # fmt: skip
    eevee, final = _build(sdir, "preview-eevee"), _build(sdir)
    assert [f["check"] for f in eevee["tracks"]["findings"]] == ["blur"]
    assert eevee["tracks"]["findings"][0]["f1"] == 44
    assert [f["check"] for f in final["tracks"]["findings"]] == ["flicker"]


def test_no_mp4_no_track(sdir):
    (sdir / "out" / "pendulum.mp4").unlink()
    assert _build(sdir) is None
    assert _build(sdir, "nonexistent") is None


def test_findings_outside_the_video_are_unplaced(sdir):
    (sdir / "review" / "checks.json").write_text('{"findings": []}', encoding="utf-8")
    rows = [{"check": "c", "severity": "error", "message": f"m{i}", "frame": f} for i, f in enumerate((0, 400, None))]
    _frame_checks(sdir, "final", rows)
    tl = _build(sdir)
    assert tl["tracks"]["findings"] == []
    assert [u["why"] for u in tl["unplaced"]] == [
        "frame 0 is not in this video (frames 1 to 90)", "frame 400 is not in this video (frames 1 to 90)", "no frame"]


def test_stale_when_the_timeline_changed_since_the_edit(sdir):
    pagefix.record_edit(sdir, "final", stale=True)
    assert _build(sdir)["stale"] is True
    runs = json.loads((sdir / "review" / "engine-runs.json").read_text(encoding="utf-8"))
    del runs["final"]["edit"]
    (sdir / "review" / "engine-runs.json").write_text(json.dumps(runs), encoding="utf-8")
    assert _build(sdir)["stale"] is True


def test_a_different_fps_makes_the_edit_stale(sdir):
    runs = json.loads((sdir / "review" / "engine-runs.json").read_text(encoding="utf-8"))
    runs["final"]["edit"]["fps"] = 24.0
    (sdir / "review" / "engine-runs.json").write_text(json.dumps(runs), encoding="utf-8")
    tl = _build(sdir)
    assert tl["stale"] is True and tl["fps"] == 24 and "24 fps" in tl["problem"]


def test_a_finding_range_is_checked_and_clamped(sdir):
    base = {"check": "c", "severity": "warning", "frame": 40}
    (sdir / "review" / "checks.json").write_text('{"findings": []}', encoding="utf-8")
    _frame_checks(sdir, "final", [{**base, "message": "a", "f1": 30}, {**base, "message": "b", "f1": 500},
                                  {**base, "message": "c", "f1": 45}])  # fmt: skip
    got = {f["text"]: f.get("f1") for f in _build(sdir)["tracks"]["findings"]}
    assert got == {"a": None, "b": 90, "c": 45}


def test_a_sound_of_a_missing_shot_is_a_problem_line(sdir):
    _short_json(sdir, lambda d: d["sfx"][0].update(shot="s09"))
    tl = _build(sdir)
    assert tl["tracks"]["sounds"] == [] and "whoosh (shot s09)" in tl["problem"]


def test_any_events_failure_keeps_the_rest_of_the_track(sdir):
    (sdir / "source" / "events.json").write_text(json.dumps([{"kind": "nope", "frame": 5}]), encoding="utf-8")
    tl = _build(sdir)
    assert [s["kind"] for s in tl["tracks"]["sounds"]] == ["whoosh"] and "events.json" in tl["problem"]


def test_the_video_tag_changes_when_the_mp4_is_rewritten(sdir):
    import os

    mp4 = sdir / "out" / "pendulum.mp4"
    first = _build(sdir)["video"]
    st = mp4.stat()
    assert first["tag"] == f"{st.st_mtime_ns}-{st.st_size}"
    mp4.write_bytes(mp4.read_bytes())  # a re-edit with the same frames: same size, a new file
    os.utime(mp4, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))
    again = _build(sdir)["video"]
    assert again["file"] == first["file"] and again["tag"] != first["tag"]


def test_an_edit_without_its_record_says_so(sdir):
    import json as _json

    from bsa import engine_runs

    assert _build(sdir)["no_edit"] is False
    path = engine_runs.runs_path(sdir)
    runs = _json.loads(path.read_text(encoding="utf-8"))
    del runs["final"]["edit"]
    path.write_text(_json.dumps(runs), encoding="utf-8")
    tl = _build(sdir)
    assert tl["no_edit"] is True and tl["stale"] is True


def test_the_track_reads_retry_a_file_being_replaced(sdir, monkeypatch):  # Windows: a reader racing os.replace
    from pathlib import Path

    from bsa.review import store

    real, denied = Path.read_bytes, []

    def flaky(self):
        if self.name in ("timeline.json", "checks.json") and denied.count(self.name) < 2:
            denied.append(self.name)
            raise PermissionError(13, "Access is denied", str(self))
        return real(self)

    monkeypatch.setattr(store, "_RETRY_DENIED", True)
    monkeypatch.setattr(store.time, "sleep", lambda s: None)
    monkeypatch.setattr(Path, "read_bytes", flaky)
    monkeypatch.setattr(Path, "read_text", lambda self, *a, **k: flaky(self).decode("utf-8"))
    tl = _build(sdir)
    assert denied.count("timeline.json") == 2 and denied.count("checks.json") == 2
    assert tl["tracks"]["findings"] and tl["tracks"]["shots"]
