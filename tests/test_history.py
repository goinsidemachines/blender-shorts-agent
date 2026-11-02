"""Version history: only versions the page showed are kept (the last three per stage), with what each one showed."""

import json
import os
import shutil
import threading

import pagefix

from bsa.review import history, snapshot, versions


def stage(snap: dict, sid: str) -> dict:
    return next(s for s in snap["stages"] if s["id"] == sid)


def set_line(sdir, line_id: str, text: str) -> None:
    p = sdir / "source" / "short.json"
    data = json.loads(p.read_text("utf-8"))
    for ln in data["lines"]:
        if ln["id"] == line_id:
            ln["text"] = text
    p.write_text(json.dumps(data, indent=2), "utf-8")


def show(sdir) -> dict:
    return history.shown(sdir, snapshot.build(sdir, settings={}), now=1000.0)


def index(sdir, sid: str) -> list[dict]:
    return json.loads((history.root(sdir, sid) / "index.json").read_text("utf-8"))


def stand_in_mp4(sdir, data: bytes = b"video-1" * 300) -> None:
    mp4 = sdir / "out" / "pendulum.mp4"
    mp4.parent.mkdir(parents=True, exist_ok=True)
    mp4.write_bytes(data)


def test_constants():
    assert history.STAGES == ("story", "storyboard", "final") and history.KEEP == 3 and history.MP4_KEEP == 3


def test_only_shown_versions_are_offered(tmp_path):
    sdir = pagefix.make_short(tmp_path, "story")
    snap = show(sdir)  # v1 shown
    assert stage(snap, "story")["version"] == 1 and stage(snap, "story")["history"] == []
    set_line(sdir, "l02", "Length sets the swing.")
    assert stage(snapshot.build(sdir, settings={}), "story")["version"] == 2  # built, never shown
    set_line(sdir, "l02", "Length sets the rhythm.")
    snap = show(sdir)  # v3 shown
    assert stage(snap, "story")["version"] == 3
    assert [h["version"] for h in stage(snap, "story")["history"]] == [1]
    assert [e["version"] for e in index(sdir, "story")] == [1, 3]


def test_history_prunes_to_three(tmp_path):
    sdir = pagefix.make_short(tmp_path, "story")
    for word in ("one", "two", "three", "four", "five"):
        set_line(sdir, "l02", f"Length sets the {word}.")
        show(sdir)
    assert [e["version"] for e in index(sdir, "story")] == [3, 4, 5]
    root = history.root(sdir, "story")
    assert not (root / "v1").exists() and not (root / "v2").exists()
    assert all((root / f"v{n}").is_dir() for n in (3, 4, 5))
    assert [h["version"] for h in stage(show(sdir), "story")["history"]] == [4, 3]  # newest first, never current


def test_a_returning_fingerprint_reuses_its_entry(tmp_path):
    sdir = pagefix.make_short(tmp_path, "story")
    show(sdir)
    set_line(sdir, "l02", "Length sets the swing.")
    show(sdir)
    set_line(sdir, "l02", "Length sets the beat.")
    snap = show(sdir)
    assert stage(snap, "story")["version"] == 1
    assert [e["version"] for e in index(sdir, "story")] == [1, 2]
    assert [h["version"] for h in stage(snap, "story")["history"]] == [2]


def test_a_version_number_reused_after_versions_json_moved_aside_is_replaced(tmp_path):
    sdir = pagefix.make_short(tmp_path, "story")
    show(sdir)
    versions.versions_path(sdir).rename(sdir / "review" / "versions.old.json")
    set_line(sdir, "l02", "Length sets the swing.")
    show(sdir)  # story is v1 again, with another fingerprint
    entries = index(sdir, "story")
    assert [e["version"] for e in entries] == [1]
    lines = json.loads((history.root(sdir, "story") / "v1" / "lines.json").read_text("utf-8"))
    assert lines[1] == {"id": "l02", "text": "Length sets the swing."}


def test_story_compare_is_a_word_diff(tmp_path):
    sdir = pagefix.make_short(tmp_path, "story")
    show(sdir)
    set_line(sdir, "l02", "Length sets the swing!")
    entry = stage(show(sdir), "story")["history"][0]
    assert entry["version"] == 1 and entry["at"] == 1000.0
    assert entry["files"] == ["review/history/story/v1/lines.json"]
    lines = {ln["id"]: ln["diff"] for ln in entry["compare"]["lines"]}
    assert lines["l02"] == [["same", "length"], ["same", "sets"], ["same", "the"], ["changed", "swing"]]
    assert all(op == "same" for op, _ in lines["l01"])


def test_story_compare_marks_lines_only_on_one_side(tmp_path):
    sdir = pagefix.make_short(tmp_path, "story")
    show(sdir)
    p = sdir / "source" / "short.json"
    data = json.loads(p.read_text("utf-8"))
    data["lines"][2]["id"] = "l03b"  # l03 is gone, l03b is new
    p.write_text(json.dumps(data, indent=2), "utf-8")
    lines = {ln["id"]: ln["diff"] for ln in stage(show(sdir), "story")["history"][0]["compare"]["lines"]}
    assert lines["l03"] and all(op == "missing" for op, _ in lines["l03"])
    assert lines["l03b"] and all(op == "extra" for op, _ in lines["l03b"])


def test_storyboard_compare_pairs_stills(tmp_path):
    sdir = pagefix.make_short(tmp_path, "storyboard")
    show(sdir)
    pagefix.write_png(sdir / "stills" / "s02.png", pagefix.np.full((8, 8, 3), 200, pagefix.np.uint8))
    snap = show(sdir)
    entry = stage(snap, "storyboard")["history"][0]
    assert entry["version"] == 1
    shots = {s["id"]: s for s in entry["compare"]["shots"]}
    assert shots["s02"]["before"] == {"file": "review/history/storyboard/v1/stills/s02.png"}
    assert shots["s02"]["after"] == {"file": "stills/s02.png"}
    assert shots["s02"]["title"]
    assert (sdir / shots["s02"]["before"]["file"]).read_bytes() != (sdir / "stills" / "s02.png").read_bytes()
    assert "review/history/storyboard/v1/shots.json" in entry["files"]


def test_a_still_rewritten_in_place_keeps_its_before(tmp_path):  # Review Focus 5
    sdir = pagefix.make_short(tmp_path, "storyboard")
    still = sdir / "stills" / "s01.png"
    original = still.read_bytes()
    show(sdir)
    with open(still, "r+b") as f:  # Blender saves over the same file
        f.seek(len(original) - 8)
        f.write(b"\x01" * 8)
    snap = show(sdir)
    assert stage(snap, "storyboard")["version"] == 2
    before = stage(snap, "storyboard")["history"][0]["compare"]["shots"][0]["before"]["file"]
    assert (sdir / before).read_bytes() == original


def test_two_polls_record_one_history_entry(tmp_path):  # Review Focus 3
    sdir = pagefix.make_short(tmp_path, "story")
    set_line(sdir, "l02", "Length sets the swing.")
    snaps = [snapshot.build(sdir, settings={}) for _ in range(2)]
    errors = []
    gate = threading.Barrier(2)

    def poll(snap):
        try:
            gate.wait()
            history.shown(sdir, snap)
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=poll, args=(s,)) for s in snaps]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors and not any(s["problems"] for s in snaps)
    root = history.root(sdir, "story")
    assert len(index(sdir, "story")) == 1 and not list(root.glob("*.tmp"))
    assert sorted(p.name for p in root.iterdir() if p.is_dir()) == ["v1"]


def test_a_half_copied_folder_is_never_listed(tmp_path):
    sdir = pagefix.make_short(tmp_path, "story")
    show(sdir)
    set_line(sdir, "l02", "Length sets the swing.")
    root = history.root(sdir, "story")
    (root / "v2.999.tmp").mkdir()  # a copy a crash left behind
    snap = show(sdir)
    assert [h["version"] for h in stage(snap, "story")["history"]] == [1]
    assert not list(root.glob("*.tmp"))


def test_a_missing_version_folder_is_not_offered(tmp_path):
    sdir = pagefix.make_short(tmp_path, "story")
    show(sdir)
    set_line(sdir, "l02", "Length sets the swing.")
    show(sdir)
    shutil.rmtree(history.root(sdir, "story") / "v1")
    snap = history.shown(sdir, snapshot.build(sdir, settings={}))
    assert stage(snap, "story")["history"] == [] and not snap["problems"]


def test_a_damaged_index_is_one_problem_and_no_history(tmp_path):
    sdir = pagefix.make_short(tmp_path, "story")
    show(sdir)
    (history.root(sdir, "story") / "index.json").write_text("{nope", "utf-8")
    snap = show(sdir)
    assert stage(snap, "story")["history"] == []
    assert len([p for p in snap["problems"] if "index.json" in p]) == 1
    assert stage(snap, "storyboard")["history"] == []


def test_final_records_findings_and_the_video_digest(tmp_path):
    sdir = pagefix.make_short(tmp_path, "final")
    if not (sdir / "out" / "pendulum.mp4").is_file():
        stand_in_mp4(sdir)
    snap = show(sdir)
    assert stage(snap, "final")["version"] == 1
    entry = index(sdir, "final")[0]
    assert entry["mp4_digest"] == versions.file_digest(sdir, "out/pendulum.mp4")
    final = json.loads((history.root(sdir, "final") / "v1" / "final.json").read_text("utf-8"))
    assert final["findings"] == stage(snap, "final")["content"]["findings"] and "size" in final


def test_keep_outgoing_final_names_it_by_the_shown_digest(tmp_path):
    sdir = pagefix.make_short(tmp_path, "final")
    if not (sdir / "out" / "pendulum.mp4").is_file():
        stand_in_mp4(sdir)
    show(sdir)
    entry = index(sdir, "final")[0]
    kept = history.keep_outgoing_final(sdir, sdir / "out" / "pendulum.mp4")
    assert kept.name == entry["mp4_digest"][:16] + ".mp4" and not (sdir / "out" / "pendulum.mp4").exists()
    assert kept.parent == history.root(sdir, "final")
    stand_in_mp4(sdir, b"video-2" * 300)  # the new final
    snap = show(sdir)
    assert stage(snap, "final")["version"] == 2
    old = stage(snap, "final")["history"][0]
    assert old["version"] == 1
    assert old["compare"]["video"] == {"file": f"review/history/final/{entry['mp4_digest'][:16]}.mp4"}
    assert old["compare"]["findings"] == stage(snap, "final")["content"]["findings"]


def test_final_compare_without_the_kept_video_has_only_findings(tmp_path):
    sdir = pagefix.make_short(tmp_path, "final")
    if not (sdir / "out" / "pendulum.mp4").is_file():
        stand_in_mp4(sdir)
    show(sdir)
    stand_in_mp4(sdir, b"video-2" * 300)  # replaced without keeping the old one
    old = stage(show(sdir), "final")["history"][0]
    assert old["compare"]["video"] is None and isinstance(old["compare"]["findings"], list)


def test_keep_outgoing_final_keeps_three(tmp_path):
    sdir = pagefix.make_short(tmp_path, "story")
    mp4 = sdir / "out" / "pendulum.mp4"
    kept = []
    for i in range(5):
        stand_in_mp4(sdir, f"video-{i}".encode() * 100)
        kept.append(history.keep_outgoing_final(sdir, mp4))
        os.utime(kept[-1], ns=(i * 10**9 + 10**18, i * 10**9 + 10**18))  # move order, whatever the clock's grain
    left = sorted(p.name for p in history.root(sdir, "final").glob("*.mp4"))
    assert left == sorted(p.name for p in kept[-3:])
    assert history.keep_outgoing_final(sdir, mp4) is None  # nothing to keep


def test_keep_outgoing_final_never_prunes_the_one_it_just_kept(tmp_path):
    sdir = pagefix.make_short(tmp_path, "story")
    root = history.root(sdir, "final")
    root.mkdir(parents=True)
    for i in range(3):  # three newer-looking files already there (a clock that moved back)
        (root / f"{i:016x}.mp4").write_bytes(b"x")
        os.utime(root / f"{i:016x}.mp4", ns=(4 * 10**18, 4 * 10**18))
    stand_in_mp4(sdir)
    kept = history.keep_outgoing_final(sdir, sdir / "out" / "pendulum.mp4")
    assert kept.is_file() and len(list(root.glob("*.mp4"))) == 3


def test_snapshot_build_writes_no_history(tmp_path):
    sdir = pagefix.make_short(tmp_path, "final")
    snapshot.build(sdir, settings={})
    assert not (sdir / "review" / "history").exists()
    assert all("history" not in (s["content"] or {}) for s in snapshot.build(sdir, settings={})["stages"])


def test_stage_fingerprint_is_what_versions_hashes(tmp_path):
    sdir = pagefix.make_short(tmp_path, "story")
    snap = snapshot.build(sdir, settings={})
    row = stage(snap, "story")
    fp = snapshot.stage_fingerprint(sdir, "story", row["content"])
    seen = json.loads(versions.versions_path(sdir).read_text("utf-8"))["story"]
    assert seen[row["version"] - 1] == fp
    assert versions._file_digest is versions.file_digest


def test_the_edit_keeps_only_the_approved_finals_outgoing_mp4(tmp_path):
    import bsa_blender
    from bsa import engine_runs

    sdir = pagefix.make_short(tmp_path, "story")
    out = sdir / "out"
    out.mkdir(exist_ok=True)
    for name, preset, run in (("pendulum.mp4", "final", engine_runs.EngineRun(None)),
                              ("pendulum-final-eevee.mp4", "final", engine_runs.EngineRun("eevee", "eevee")),
                              ("pendulum-preview.mp4", "preview", engine_runs.EngineRun(None))):  # fmt: skip
        (out / name).write_bytes(name.encode())
        bsa_blender._drop_outgoing(sdir, out / name, preset, run)
        assert not (out / name).exists()
    kept = list(history.root(sdir, "final").glob("*.mp4"))
    assert [p.read_bytes() for p in kept] == [b"pendulum.mp4"]
