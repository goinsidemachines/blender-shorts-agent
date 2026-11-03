import json

import kitfix
import numpy as np
import pagefix

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
