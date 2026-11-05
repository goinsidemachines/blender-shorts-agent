import json
import os
from pathlib import Path

import kitfix
import numpy as np
import pagefix
import pytest

from bsa import jobstate
from bsa import kit as kit_mod
from bsa.review import decisions, snapshot
from bsa.short import load_short


def _stage(snap, sid):
    return next(s for s in snap["stages"] if s["id"] == sid)


def test_story_content(tmp_path):
    snap = snapshot.build(pagefix.make_short(tmp_path, "story"), settings={})
    c = _stage(snap, "story")["content"]
    assert c["hook"] == "A pendulum swings like clockwork." and c["closing"] == "Clocks kept time this way."
    assert [b["shot"] for b in c["beats"]] == ["s01", "s02", "s03"]
    assert [ln["id"] for ln in c["beats"][1]["lines"]] == ["l02", "l03"]
    assert c["facts"][0]["is_link"] is True
    assert _stage(snap, "story")["needs_yes"] is True and _stage(snap, "story")["version"] == 1
    assert set(c["beats"][0]["pace"]) == {"seconds", "spoken_s", "silent_s", "too_silent", "suggest_s"}
    assert c["look"] == {"note": None, "options": []} and c["assets"] == {"pending": [], "downloaded": []}
    json.dumps(snap)


def test_story_look_options_and_pending_assets(tmp_path):
    sdir = pagefix.make_short(tmp_path, "story")
    (sdir / "source" / "styles.json").write_text(json.dumps([{"name": "Dusk", "description": "warm"}]), "utf-8")
    pagefix.write_png(sdir / "stills" / "style-1.png", np.zeros((4, 4, 3), np.uint8))
    plan = [
        {
            "id": "a",
            "name": "Brass",
            "kind": "texture",
            "source": "https://polyhaven.com/a",
            "license": "CC0",
            "files": [],
            "bytes": 2_000_000,
        }
    ]
    (sdir / "source" / "assets-plan.json").write_text(json.dumps(plan), "utf-8")
    c = _stage(snapshot.build(sdir, settings={"brand": {"mode": "fresh"}}), "story")["content"]
    assert c["look"]["options"] == [{"name": "Dusk", "description": "warm", "file": {"file": "stills/style-1.png"}}]
    assert c["look"]["note"] and [a["name"] for a in c["assets"]["pending"]] == ["Brass"]
    c = _stage(snapshot.build(sdir, settings={"brand": {"mode": "brand"}}), "story")["content"]
    assert c["look"]["options"] == []  # the brand has no look options


def test_story_version_moves_on_a_typo_fix(tmp_path):
    sdir = pagefix.make_short(tmp_path, "story")
    snapshot.build(sdir, settings={})
    p = sdir / "source" / "short.json"
    p.write_text(p.read_text("utf-8").replace("beat.", "beat!"), "utf-8")
    assert _stage(snapshot.build(sdir, settings={}), "story")["version"] == 2


def test_storyboard_content_and_still_bytes_version(tmp_path):
    sdir = pagefix.make_short(tmp_path, "storyboard")
    c = _stage(snapshot.build(sdir, settings={}), "storyboard")["content"]
    assert [s["still"] for s in c["shots"]] == [{"file": f"stills/s0{i}.png"} for i in (1, 2, 3)]
    assert c["frame"]["cap_px"] == 64 and 0 < c["frame"]["safe"]["w"] <= 100
    assert c["total_s"] > 0 and c["closing"] is None
    assert c["shots"][1]["lines"][0]["id"] == "l02" and c["shots"][1]["labels"] == ["same length, same beat", "Longer"]
    pagefix.write_png(sdir / "stills" / "s02.png", np.zeros((8, 8, 3), np.uint8))
    assert _stage(snapshot.build(sdir, settings={}), "storyboard")["version"] == 2


def test_engine_choices_and_settings_source(tmp_path):
    sdir = pagefix.make_short(tmp_path, "engine")
    c = _stage(snapshot.build(sdir, settings={}), "engine")["content"]
    assert {ch["engine"] for ch in c["choices"]} == {"cycles", "eevee"} and c["from"] == "ask"
    assert all(isinstance(ch["estimate_s"], float) for ch in c["choices"]) and c["machine_note"] is None
    c = _stage(snapshot.build(sdir, settings={"render": {"engine": "eevee"}}), "engine")["content"]
    assert c["from"] == "settings" and c["engine"] == "eevee"


def test_engine_version_ignores_the_approved_engine_in_state(tmp_path):
    sdir = pagefix.make_short(tmp_path, "engine")
    before = _stage(snapshot.build(sdir, settings={}), "engine")["version"]
    jobstate.mark_done(sdir, "engine", engine="eevee")
    assert _stage(snapshot.build(sdir, settings={}), "engine")["version"] == before


def test_narration_content(tmp_path):
    sdir = pagefix.make_short(tmp_path, "narration")
    assert _stage(snapshot.build(sdir, settings={}), "narration")["content"] == {
        "status": None,
        "provider": None,
        "message": None,
        "takes": 0,
    }
    (sdir / "audio" / "vo").mkdir(parents=True, exist_ok=True)
    (sdir / "audio" / "vo" / "l01.wav").write_bytes(b"x")
    jobstate.mark_done(sdir, "narration", status="captions-only", provider="local", message="no voice")
    assert _stage(snapshot.build(sdir, settings={}), "narration")["content"] == {
        "status": "captions-only",
        "provider": "local",
        "message": "no voice",
        "takes": 1,
    }


def test_still_ref_prefers_jpg_then_png(tmp_path):
    (tmp_path / "stills").mkdir()
    assert snapshot.still_ref(tmp_path, "s01") is None
    pagefix.write_png(tmp_path / "stills" / "s01.png", np.zeros((2, 2, 3), np.uint8))
    assert snapshot.still_ref(tmp_path, "s01") == {"file": "stills/s01.png"}
    (tmp_path / "stills" / "s01.jpg").write_bytes(b"j")
    assert snapshot.still_ref(tmp_path, "s01") == {"file": "stills/s01.jpg"}


def test_needs_yes_clears_once_approved_and_applied(tmp_path):
    sdir = pagefix.make_short(tmp_path, "story")
    d = decisions.record(sdir, "story", 1, "approve", "page", current=1)
    st = _stage(snapshot.build(sdir, settings={}), "story")
    assert st["needs_yes"] is False and st["waiting_for_claude"] is True
    decisions.mark_applied(sdir, d["id"])
    assert _stage(snapshot.build(sdir, settings={}), "story")["waiting_for_claude"] is False


def test_damaged_versions_json_is_one_problem_line_and_content_still_shows(tmp_path):
    sdir = pagefix.make_short(tmp_path, "story")
    (sdir / "review" / "versions.json").write_text("[]", encoding="utf-8")
    snap = snapshot.build(sdir, settings={})
    assert len([p for p in snap["problems"] if "versions.json" in p]) == 1
    assert all(s["problem"] is None for s in snap["stages"])
    assert _stage(snap, "story")["content"] is not None and _stage(snap, "story")["version"] is None


KIT_SETTINGS = {"brand": kitfix.KIT}  # the made-up kit (style toon)


def test_storyboard_has_no_look_without_a_kit(tmp_path):
    sdir = pagefix.make_short(tmp_path, "storyboard")
    pagefix.write_look(sdir)
    st = _stage(snapshot.build(sdir, settings={}), "storyboard")
    c = st["content"]
    assert c["look"] is None and st["version"] == 1
    before = {k: v for k, v in c.items() if k != "look"}  # the content as it was before the look preview existed
    assert snapshot.stage_fingerprint(sdir, "storyboard", c) == snapshot.stage_fingerprint(sdir, "storyboard", before)
    (sdir / "stills" / "look.json").unlink()  # a kit but no look preview yet: no look either, same version
    st = _stage(snapshot.build(sdir, settings=KIT_SETTINGS), "storyboard")
    assert st["content"]["look"] is None and st["version"] == 1


def test_storyboard_look_lists_frames_swatches_and_other_choices(tmp_path):
    sdir = pagefix.make_short(tmp_path, "storyboard")
    pagefix.write_look(sdir, warnings=["shot s02: the label near 'Bob' is left out of the look (no room)"])
    lk = _stage(snapshot.build(sdir, settings=KIT_SETTINGS), "storyboard")["content"]["look"]
    assert lk["style"] == "toy" and lk["set"] == "workbench" and lk["built"] == {"style": "toon", "set": "workbench"}
    assert lk["styles"] == ["realistic", "toon", "clay", "blueprint", "paper", "pencil"]
    assert lk["sets"] == ["graph", "outdoor", "studio", "none"]
    assert [f["shot"] for f in lk["frames"]] == ["s01", "s02", "s03"]
    assert lk["frames"][0]["file"] == {"file": "stills/look/s01.jpg"} and lk["frames"][1]["title"]
    assert lk["sheet"] == {"file": "stills/look/kit.png"}
    names = [s["name"] for s in lk["swatches"]]
    assert names == ["background", "ink", "card", "text", "accent", "highlight", "spark", "flow", "grip"]
    kit = kit_mod.kit_for(KIT_SETTINGS, load_short(sdir / "source" / "short.json"))  # the short's own style counts
    assert lk["swatches"][4] == {"name": "accent", "hex": kit.palette["accent"]}
    assert lk["swatches"][6] == {"name": "spark", "hex": "#D9483B"}
    assert lk["engine"] == "eevee" and lk["seconds_per_frame"] == 2.5
    assert lk["estimates"] == {"eevee_s": 300.0, "cycles_s": 1200.0} and lk["first"] is True
    assert lk["warnings"] == ["shot s02: the label near 'Bob' is left out of the look (no room)"]
    assert lk["kit_warning"] is None
    pagefix.write_look(sdir, set_="none", no_estimates=True)
    lk = _stage(snapshot.build(sdir, settings=KIT_SETTINGS), "storyboard")["content"]["look"]
    assert lk["sets"] == ["workbench", "graph", "outdoor", "studio"] and lk["estimates"] is None
    json.dumps(lk)


def test_storyboard_look_skips_missing_frames_and_a_damaged_file_is_a_warning(tmp_path):
    sdir = pagefix.make_short(tmp_path, "storyboard")
    pagefix.write_look(sdir)
    (sdir / "stills" / "look" / "s02.jpg").unlink()
    lk = _stage(snapshot.build(sdir, settings=KIT_SETTINGS), "storyboard")["content"]["look"]
    assert lk["frames"][1]["file"] is None
    (sdir / "stills" / "look.json").write_text("{not json", encoding="utf-8")
    st = _stage(snapshot.build(sdir, settings=KIT_SETTINGS), "storyboard")
    assert st["problem"] is None and st["content"]["look"]["frames"] == []
    assert "stills/look.json" in st["content"]["look"]["warnings"][0]


def test_a_look_json_with_wrongly_typed_fields_is_warnings_not_a_stage_problem(tmp_path):
    sdir = pagefix.make_short(tmp_path, "storyboard")
    data = pagefix.write_look(sdir)
    path = sdir / "stills" / "look.json"
    bad = {**data, "frames": 5, "warnings": "oops", "style": 7, "set": ["x"], "built": "toon", "estimates": [1],
           "seconds_per_frame": "fast", "swatches": [{"name": "accent", "hex": "red"}], "sheet": 3}  # fmt: skip
    path.write_text(json.dumps(bad), encoding="utf-8")
    st = _stage(snapshot.build(sdir, settings=KIT_SETTINGS), "storyboard")
    lk = st["content"]["look"]
    assert st["problem"] is None and st["version"] is not None
    assert lk["frames"] == [] and lk["style"] is None and lk["set"] is None and lk["sheet"] is None
    assert lk["seconds_per_frame"] is None and lk["estimates"] is None and lk["swatches"] == []
    wrong = " ".join(lk["warnings"])
    for key in ("frames", "warnings", "style", "set", "built", "estimates", "seconds_per_frame", "swatches", "sheet"):
        assert repr(key) in wrong
    path.write_text(json.dumps({**data, "frames": [5, {"shot": 3}, data["frames"][0]]}), encoding="utf-8")
    lk = _stage(snapshot.build(sdir, settings=KIT_SETTINGS), "storyboard")["content"]["look"]
    assert [f["shot"] for f in lk["frames"]] == ["s01"] and "'frames'" in " ".join(lk["warnings"])
    path.write_text("[1, 2]", encoding="utf-8")
    st = _stage(snapshot.build(sdir, settings=KIT_SETTINGS), "storyboard")
    assert st["problem"] is None and "not a JSON object" in st["content"]["look"]["warnings"][0]


def test_swatches_are_the_rendered_ones_and_a_changed_kit_is_a_warning_not_a_version(tmp_path):
    sdir = pagefix.make_short(tmp_path, "storyboard")
    old = [{"name": "background", "hex": "#101010"}, {"name": "accent", "hex": "#ABCDEF"}]
    pagefix.write_look(sdir, swatches=old)
    st = _stage(snapshot.build(sdir, settings=KIT_SETTINGS), "storyboard")
    assert st["content"]["look"]["swatches"] == old and st["version"] == 1
    assert st["content"]["look"]["kit_warning"] == snapshot.OLD_KIT
    assert "run BLENDER look again" in snapshot.OLD_KIT
    edited = {"brand": {**kitfix.KIT, "categories": {"spark": "#000000"}}}  # the kit changes again: same version
    assert _stage(snapshot.build(sdir, settings=edited), "storyboard")["version"] == 1
    pagefix.write_look(sdir)  # rendered again with the kit as it is now: the warning goes, a new version
    st = _stage(snapshot.build(sdir, settings=KIT_SETTINGS), "storyboard")
    assert st["content"]["look"]["kit_warning"] is None and st["version"] == 2


def test_a_new_look_changes_the_storyboard_version(tmp_path):
    sdir = pagefix.make_short(tmp_path, "storyboard")
    pagefix.write_look(sdir)
    assert _stage(snapshot.build(sdir, settings=KIT_SETTINGS), "storyboard")["version"] == 1
    pagefix.write_look(sdir, seconds=9.75, first=False)  # the same frames measured again: still the same look
    assert _stage(snapshot.build(sdir, settings=KIT_SETTINGS), "storyboard")["version"] == 1
    pagefix.write_look(sdir, style="pencil")
    assert _stage(snapshot.build(sdir, settings=KIT_SETTINGS), "storyboard")["version"] == 2


def test_new_short_fields_move_no_stage_version(tmp_path):
    from bsa.review import stages
    from bsa.short import save_short

    sdir = pagefix.make_short(tmp_path, "done")
    before = {s: snapshot.stage_version(sdir, s, snapshot.load_ctx(sdir, {})) for s in stages.PAGE_STAGES}
    path = sdir / "source" / "short.json"
    short = load_short(path)
    assert short.pace == {} and short.like is None
    save_short(short, path)
    data = json.loads(path.read_text("utf-8"))
    assert data["pace"] == {} and data["like"] is None
    assert {s: snapshot.stage_version(sdir, s, snapshot.load_ctx(sdir, {})) for s in stages.PAGE_STAGES} == before


def test_set_narrator_pace_and_like_move_no_stage_version(tmp_path):
    from bsa.review import stages
    from bsa.short import save_short

    sdir = pagefix.make_short(tmp_path, "done")
    before = {s: snapshot.stage_version(sdir, s, snapshot.load_ctx(sdir, {})) for s in stages.PAGE_STAGES}
    path = sdir / "source" / "short.json"
    short = load_short(path)
    short.narrator = {"voice": "v1", "take_mode": "paragraph", "lead_in": 0.3}
    short.pace = {"shots": 3, "min_s": 4.0, "holds": 1}
    short.like = {"short": "01-other", "copied": ["pace", "narrator"], "at": "2026-11-04T10:00:00Z"}
    save_short(short, path)
    assert {s: snapshot.stage_version(sdir, s, snapshot.load_ctx(sdir, {})) for s in stages.PAGE_STAGES} == before


def _interview(sdir):
    return _stage(snapshot.build(sdir, settings={}), "interview")


def test_interview_started_from_lists_what_was_copied(tmp_path):
    sdir = pagefix.make_short(tmp_path, "story")
    before = _interview(sdir)["version"]
    other = pagefix.write_like(sdir, "Eiffel Tower")
    st = _interview(sdir)
    assert st["version"] == before  # review information: it never moves the stage's version
    sf = st["content"]["started_from"]
    assert sf["title"] == "Eiffel Tower" and sf["short"] == other.name
    assert [c["key"] for c in sf["copied"]] == ["style", "look", "length_s", "pace", "closing", "narrator"]
    got = {c["key"]: c for c in sf["copied"]}
    assert got["style"]["label"] == "the look" and got["style"]["value"] == "toy style, workbench set, own palette (accent), font Segoe UI"
    assert got["pace"]["value"] == "8 shots, about 4 s each, 2 holds"
    assert got["closing"]["value"] == "none" and got["length_s"]["value"] == "15 s"
    assert got["narrator"]["value"] == "voice af_heart at 1.0×, paragraph takes, 0.3 s lead-in"
    json.dumps(st)


def test_interview_started_from_names_a_short_whose_folder_is_gone(tmp_path):
    import shutil

    sdir = pagefix.make_short(tmp_path, "story")
    other = pagefix.write_like(sdir, "Eiffel Tower")
    shutil.rmtree(other)
    sf = _interview(sdir)["content"]["started_from"]
    assert sf["title"] == "Eiffel Tower" and sf["short"] == other.name and sf["copied"]  # the recorded title
    import json as _json

    path = sdir / "source" / "short.json"  # an older short.json (no recorded title): the folder name
    data = _json.loads(path.read_text("utf-8"))
    del data["like"]["title"]
    path.write_text(_json.dumps(data), "utf-8")
    assert _interview(sdir)["content"]["started_from"]["title"] == other.name


def test_interview_without_like_has_no_started_from(tmp_path):
    c = _interview(pagefix.make_short(tmp_path, "story"))["content"]
    assert "started_from" not in c


def test_describe_one_plain_line_per_copied_value():
    from bsa import like

    assert like.describe("style", {"style": "toy", "set": "workbench", "palette": {"a": "#fff"}, "font": "Lato"}) == (
        "toy style, workbench set, own palette, font Lato")
    assert like.describe("look", "brand") == "brand" and like.describe("look", "fresh") == "its own look"
    assert like.describe("length_s", 45) == "45 s"
    assert like.describe("pace", {"shots": 1, "min_s": 3.5, "holds": 0}) == "1 shot, about 3.5 s each"
    assert like.describe("end_card", "Follow @me") == "Follow @me"
    assert like.describe("end_card", {"text": "Hi", "second": "site"}) == "Hi"
    assert like.describe("closing", False) == "none"
    assert like.describe("closing", {"voice_line": "Bye"}) == "Bye"
    assert like.describe("narrator", {"take_mode": "line"}) == "line takes"
    # a style is described from the keys recorded as copied, never from what the style holds now
    rec = {"style_keys": ["palette.accent", "palette.ink", "caption.box", "font"]}
    now = {"style": "toy", "set": "workbench", "palette": {"accent": "#fff", "ink": "#000", "x": "#111"}, "font": "Lato"}
    assert like.describe("style", now, rec) == "own palette (accent, ink), caption box, font Lato"
    assert like.describe("style", now, {}) == "toy style, workbench set, own palette, font Lato"  # old record
    assert like.style_keys({"palette": {"a": 1}, "caption": {"box": 1}, "font": "x"}) == ["palette.a", "caption.box",
                                                                                           "font"]  # fmt: skip
    p = like.provenance(Path("x/03-how-a-clock-ticks"), ["style"], now=0, title="How a clock ticks", style={"font": "x"})
    assert p["title"] == "How a clock ticks" and p["style_keys"] == ["font"]


# --------------------------------------------------------------------------- the optional camera stage


def _one_take(sdir, approve=None):
    """Make the fixture short a one-take short (or, with `approve`, a cuts short with camera.approve)."""
    path = sdir / "source" / "short.json"
    data = json.loads(path.read_text("utf-8"))
    if approve is None:
        data["camera"] = {"style": "one-take", "seconds": 20}
        for shot, at in zip(data["shots"], (0, 6, 13), strict=True):
            shot["at"] = at
    else:
        data["camera"] = {"approve": approve}
    path.write_text(json.dumps(data), "utf-8")


def _timing(sdir):
    """The short's timeline now (reading pace, no end card) and its hash, as BLENDER camtest sees it."""
    from bsa import settings
    from bsa.timeline import build_timeline, timeline_hash, to_json

    short = load_short(sdir / "source" / "short.json")
    tl = build_timeline(short, None, settings.has_end_card({}, short))
    return tl, timeline_hash(json.loads(json.dumps(to_json(tl))))


def _camtest(sdir, shots=None, legacy=False):
    """review/camtest/ as BLENDER camtest leaves it, made from this .blend, build.py and timing (`legacy`: a record
    from before spans and the build stamp were kept)."""
    from bsa import camtest, freshness

    short = load_short(sdir / "source" / "short.json")
    blend = sdir / "blend" / "short.blend"
    blend.parent.mkdir(parents=True, exist_ok=True)
    if not blend.exists():
        blend.write_bytes(b"BLENDER-v400")
    tl, tl_hash = _timing(sdir)
    folder = sdir / camtest.FOLDER
    folder.mkdir(parents=True, exist_ok=True)
    (folder / camtest.MP4).write_bytes(b"\0\0\0 ftypisom")
    pagefix.write_png(folder / camtest.SHEET, np.full((8, 8, 3), 128, np.uint8))
    path = {} if legacy else {"spans": camtest.shot_spans(tl), "fps": tl.fps, "build": camtest.build_stamp(sdir)}
    rec = camtest.record(freshness.blend_stat(sdir), tl_hash, shots or [s.id for s in short.shots], 0.33,
                         tl.n_frames, **path)  # fmt: skip
    (folder / camtest.RECORD).write_text(json.dumps(rec), "utf-8")


TODAYS_PAGE_STAGES = ["interview", "story", "storyboard", "engine", "narration", "render", "final", "post"]
STORYBOARD_FINGERPRINT = "06da5a6dff4a3a2f8f562a1c52145e1648f2e603940c3e3ee64b1cb77941e108"  # before the camera stage


@pytest.mark.parametrize("stage", ["storyboard", "engine", "done"])
def test_a_short_without_the_camera_stage_has_todays_stage_rows(tmp_path, stage):
    sdir = pagefix.make_short(tmp_path, stage)
    snap = snapshot.build(sdir, settings={})
    assert [s["id"] for s in snap["stages"]] == TODAYS_PAGE_STAGES
    assert [s["version"] for s in snap["stages"]] == [1, 1, 1, 1, 1, None, 1, 1]
    want = {"storyboard": "current", "engine": "current", "done": "done"}[stage]
    assert _stage(snap, "storyboard" if stage != "engine" else "engine")["status"] == want
    sb = _stage(snap, "storyboard")["content"]
    assert snapshot.stage_fingerprint(sdir, "storyboard", sb) == STORYBOARD_FINGERPRINT
    assert "camera" not in jobstate.load_state(sdir)["stages"]
    assert snapshot.stage_version(sdir, "camera") is None  # nothing to approve on a plain short


def test_the_camera_stage_shows_the_clip_and_says_when_it_is_stale(tmp_path):
    from bsa import camtest

    sdir = pagefix.make_short(tmp_path, "storyboard")
    ws = {"workspace": str(tmp_path)}
    _one_take(sdir)
    snap = snapshot.build(sdir, settings=ws)
    assert [s["id"] for s in snap["stages"]][2:5] == ["storyboard", "camera", "engine"]
    row = _stage(snap, "camera")
    assert row["label"] == "Camera" and row["status"] == "locked" and row["problem"] is None
    c = row["content"]
    assert c["video"] is None and c["sheet"] is None and c["made"] is None
    assert c["stale"] == "no camera test yet" and c["command"] == "BLENDER camtest 01" and c["lead"]
    _camtest(sdir)
    c = _stage(snapshot.build(sdir, settings=ws), "camera")["content"]
    mtime = (sdir / camtest.FOLDER / camtest.MP4).stat().st_mtime_ns
    assert c["video"] == {"file": "review/camtest/camtest.mp4", "tag": mtime}
    assert c["sheet"]["file"] == "review/camtest/camtest-sheet.png" and isinstance(c["sheet"]["tag"], int)
    assert c["made"]["shots"] == ["s01", "s02", "s03"] and c["made"]["percent"] == 35 and c["made"]["frames"] > 0
    assert c["stale"] is None
    version = snapshot.stage_version(sdir, "camera")
    _later(sdir / "blend" / "short.blend")  # the scene was rebuilt (narration does that): the same camera path
    assert _stage(snapshot.build(sdir, settings=ws), "camera")["content"]["stale"] is None
    build = sdir / "source" / "build.py"
    if not build.exists():
        build.write_text("import bpy\n", encoding="utf-8")
    else:
        _later(build)
    c = _stage(snapshot.build(sdir, settings=ws), "camera")["content"]
    assert c["stale"] == "build.py changed since the camera test"
    assert snapshot.stage_version(sdir, "camera") == version  # stale is said, never a new version
    assert _stage(snapshot.build(sdir, settings={"workspace": str(tmp_path / "elsewhere")}), "camera")["version"] == (
        version)  # the command names the short another way: not a new version


def _later(path):
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))


def test_a_one_take_clip_stays_current_when_only_the_lines_move(tmp_path):
    sdir = pagefix.make_short(tmp_path, "storyboard")
    _one_take(sdir)
    _camtest(sdir)
    _, before = _timing(sdir)
    path = sdir / "source" / "short.json"
    path.write_text(path.read_text("utf-8").replace("Length sets the beat.", "Length sets the beat, not weight."),
                    "utf-8")  # fmt: skip
    assert _timing(sdir)[1] != before  # the whole timeline moved...
    assert _stage(snapshot.build(sdir, settings={}), "camera")["content"]["stale"] is None  # ...the windows did not


def test_an_older_camera_record_falls_back_to_the_whole_timeline_and_the_blend(tmp_path):
    sdir = pagefix.make_short(tmp_path, "storyboard")
    _one_take(sdir)
    _camtest(sdir, legacy=True)
    assert _stage(snapshot.build(sdir, settings={}), "camera")["content"]["stale"] is None
    _later(sdir / "blend" / "short.blend")
    assert "scene changed" in _stage(snapshot.build(sdir, settings={}), "camera")["content"]["stale"]


def test_no_camera_test_yet_even_when_the_timing_cant_be_worked_out(tmp_path):
    sdir = pagefix.make_short(tmp_path, "storyboard")
    _one_take(sdir)
    path = sdir / "source" / "short.json"
    data = json.loads(path.read_text("utf-8"))
    data["shots"][1]["at"] = 0.5  # s01's window too short for its line
    path.write_text(json.dumps(data), "utf-8")
    c = _stage(snapshot.build(sdir, settings={}), "camera")["content"]
    assert c["stale"] == "no camera test yet"


def test_a_camera_test_of_some_shots_does_not_cover_the_short(tmp_path):
    sdir = pagefix.make_short(tmp_path, "storyboard")
    ws = {"workspace": str(tmp_path)}
    _one_take(sdir)
    _camtest(sdir, shots=["s01", "s02"])
    c = _stage(snapshot.build(sdir, settings=ws), "camera")["content"]
    assert c["video"] is not None and "s03" in c["stale"] and "BLENDER camtest 01" in c["stale"]


def test_camera_approve_on_a_cuts_short_adds_the_stage_and_the_timing_moves_it_stale(tmp_path):
    sdir = pagefix.make_short(tmp_path, "storyboard")
    ws = {"workspace": str(tmp_path)}
    _one_take(sdir, approve=True)
    _camtest(sdir)
    assert _stage(snapshot.build(sdir, settings=ws), "camera")["content"]["stale"] is None
    path = sdir / "source" / "short.json"
    path.write_text(path.read_text("utf-8").replace("beat.", "beat, and more words to say here."), "utf-8")
    assert _stage(snapshot.build(sdir, settings=ws), "camera")["content"]["stale"] == (
        "the shot windows changed since the camera test")  # a cuts short's shots follow the lines
