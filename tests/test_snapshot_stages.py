import json

import numpy as np
import pagefix

from bsa import jobstate
from bsa.review import decisions, snapshot


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
