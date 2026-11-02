"""The Render, Final and Post stages of the snapshot, and the plugin feedback panel's rules."""

import json

import pagefix

from bsa import feedback, jobstate
from bsa.review import decisions, snapshot


def _stage(snap, sid):
    return next(s for s in snap["stages"] if s["id"] == sid)


# --------------------------------------------------------------------------- render


def test_render_runs_include_side_runs_as_tabs(tmp_path):
    sdir = pagefix.make_short(tmp_path, "render", side_run="eevee")
    c = _stage(snapshot.build(sdir, settings={}), "render")["content"]
    assert [r["key"] for r in c["runs"]] == ["preview", "preview-eevee"] and c["runs"][1]["side"] is True
    assert _stage(snapshot.build(sdir, settings={}), "render")["version"] is None


def test_render_run_shows_progress_steps_shots_and_latest_frame(tmp_path):
    sdir = pagefix.make_short(tmp_path, "render")
    run = _stage(snapshot.build(sdir, settings={}), "render")["content"]["runs"][0]
    assert run["label"] == "Preview" and run["side"] is False and run["state"] == "running"
    assert run["frames"]["done"] == 10 and run["frames"]["total"] == 90
    assert "10 of 90 frames" in run["summary"] and run["eta_s"] == 120.0
    assert {"name": "render", "state": "running"} in run["steps"]
    s01, s02 = run["shots"][0], run["shots"][1]
    assert (s01["id"], s01["state"], s01["done"], s01["total"]) == ("s01", "rendering", 10, 30)
    assert s01["thumb"]["file"] == "review/render-status/preview-s01.jpg" and s02["thumb"] is None
    assert run["latest"]["file"] == "review/render-status/preview-s01.jpg"
    pagefix.write_render_status(sdir, "preview", state="running", done=40)
    run = _stage(snapshot.build(sdir, settings={}), "render")["content"]["runs"][0]
    assert run["frames"]["done"] == 40 and run["latest"]["file"] == "review/render-status/preview-s02.jpg"


def test_render_side_run_label_and_dead_run_is_stopped(tmp_path):
    sdir = pagefix.make_short(tmp_path, "render", side_run="eevee")
    path = sdir / "review" / "render-status-preview.json"
    status = json.loads(path.read_text(encoding="utf-8"))
    status["pid_token"] = status["render"]["pid_token"] = "not-this-process"
    path.write_text(json.dumps(status), encoding="utf-8")
    runs = _stage(snapshot.build(sdir, settings={}), "render")["content"]["runs"]
    assert runs[0]["state"] == "stopped" and "stopped" in runs[0]["summary"]
    assert runs[1]["label"] == "Preview · eevee" and runs[1]["state"] == "done"


def test_render_without_any_run_and_with_a_bad_file(tmp_path):
    sdir = pagefix.make_short(tmp_path, "story")
    assert _stage(snapshot.build(sdir, settings={}), "render")["content"] == {"runs": []}
    (sdir / "review" / "render-status-final.json").write_text("{not json", encoding="utf-8")
    pagefix.write_render_status(sdir, "preview", state="done", done=90)
    (sdir / "review" / "render-status-preview.json").rename(sdir / "review" / "render-status-Not A Key.json")
    assert _stage(snapshot.build(sdir, settings={}), "render")["content"] == {"runs": []}


def test_an_odd_status_file_does_not_hide_the_other_runs(tmp_path):
    sdir = pagefix.make_short(tmp_path, "render", side_run="eevee")
    path = sdir / "review" / "render-status-preview-eevee.json"
    status = json.loads(path.read_text(encoding="utf-8"))
    status["run"]["steps"] = [{"state": "done"}]  # a step without a name
    path.write_text(json.dumps(status), encoding="utf-8")
    runs = _stage(snapshot.build(sdir, settings={}), "render")["content"]["runs"]
    assert runs[0]["state"] == "running" and runs[1]["state"] == "none" and "can't be read" in runs[1]["summary"]


# --------------------------------------------------------------------------- final


def test_final_findings_have_stable_ids_and_ack_is_not_an_approval(tmp_path):
    sdir = pagefix.make_short(tmp_path, "post")
    a = _stage(snapshot.build(sdir, settings={}), "final")["content"]["findings"]
    assert a and [f["id"] for f in a] == [f["id"] for f in _stage(snapshot.build(sdir, settings={}), "final")
                                          ["content"]["findings"]]  # fmt: skip
    decisions.record(sdir, "final", 1, "approve", "page", current=1)
    assert "final" not in jobstate.APPROVALS


def test_final_content(tmp_path):
    sdir = pagefix.make_short(tmp_path, "final")
    c = _stage(snapshot.build(sdir, settings={}), "final")["content"]
    assert c["checks_run"] is True and c["redo_all"] == ["BLENDER render SHORT --preset preview --redo-flagged"]
    first, second = c["findings"]
    assert first["severity"] == "error" and first["check"] == "flicker" and first["where"] == "shot s02 · frame 12"
    assert first["redo"] == "BLENDER render SHORT --preset preview --frames 12"
    assert second["severity"] == "warning" and second["where"] == "shot s01 · Bob"
    assert len(first["id"]) == 10 and all(ch in "0123456789abcdef" for ch in first["id"])
    assert c["acked"] is False and c["rules"] == [] and c["closing"] is None
    if (sdir / "out" / "pendulum.mp4").is_file():
        assert c["video"] == {"file": "out/pendulum.mp4"}
    (sdir / "out").mkdir(exist_ok=True)
    (sdir / "out" / "pendulum.mp4").write_bytes(b"\0" * 2_500_000)
    c = _stage(snapshot.build(sdir, settings={}), "final")["content"]
    assert c["video"] == {"file": "out/pendulum.mp4"} and c["size_mb"] == 2.5


def test_final_without_checks_says_so(tmp_path):
    sdir = pagefix.make_short(tmp_path, "story")
    c = _stage(snapshot.build(sdir, settings={}), "final")["content"]
    assert c["findings"] == [] and c["checks_run"] is False and c["video"] is None and c["size_mb"] is None


def test_final_ack_shows_without_moving_the_version(tmp_path):
    sdir = pagefix.make_short(tmp_path, "final")
    before = _stage(snapshot.build(sdir, settings={}), "final")
    assert before["needs_yes"] is False and before["content"]["acked"] is False
    decisions.record(sdir, "final", before["version"], "approve", "page", current=before["version"])
    after = _stage(snapshot.build(sdir, settings={}), "final")
    assert after["version"] == before["version"] and after["content"]["acked"] is True
    assert after["waiting_for_claude"] is True and after["needs_yes"] is False


# --------------------------------------------------------------------------- post


def test_post_fields_closing_and_credits(tmp_path):
    sdir = pagefix.make_short(tmp_path, "post")
    c = _stage(snapshot.build(sdir, settings={}), "post")["content"]
    assert [f["key"] for f in c["fields"]] == ["title", "description", "hashtags"]
    assert [f["label"] for f in c["fields"]] == ["Title", "Description", "Hashtags"]
    assert c["fields"][0]["text"] == "Why a pendulum keeps perfect time"
    assert c["fields"][2]["text"] == "#physics #pendulum #blender #science"
    assert c["closing"] is None and c["credits"] == []


# --------------------------------------------------------------------------- feedback


def test_feedback_panel_rules(tmp_path):
    sdir = pagefix.make_short(tmp_path, "post")
    feedback.add(sdir, "rough-edge", "a thing")
    assert snapshot.build(sdir, settings={})["feedback"] is None  # not asked yet
    feedback.allow_panel(sdir)
    assert snapshot.build(sdir, settings={"feedback": "off"})["feedback"] is None
    snap = snapshot.build(sdir, settings={})
    assert snap["feedback"]["entries"] == 1 and feedback.pending(sdir)  # building never marks reported


def test_feedback_panel_content_is_redacted(tmp_path):
    sdir = pagefix.make_short(tmp_path, "post")
    feedback.add(sdir, "blocking", "card said @secrethandle", happened="Why a pendulum keeps perfect time broke")
    feedback.allow_panel(sdir)
    fb = snapshot.build(sdir, settings={"handle": "secrethandle"})["feedback"]
    assert set(fb) >= {"entries", "title", "body", "url", "shortened", "ids"}
    assert fb["ids"] == ["f1"] and fb["url"].startswith(feedback.REPO)
    for secret in ("secrethandle", "Why a pendulum keeps perfect time"):
        assert secret not in fb["body"] and secret not in fb["url"]


def test_allow_panel_mark_reported_and_panel_allowed(tmp_path):
    sdir = pagefix.make_short(tmp_path, "post")
    assert not feedback.panel_allowed(sdir, "ask")
    feedback.allow_panel(sdir)
    assert not feedback.panel_allowed(sdir, "ask")  # nothing noted: nothing to show
    feedback.add(sdir, "blocking", "one")
    feedback.add(sdir, "rough-edge", "two")
    data = json.loads(feedback.feedback_path(sdir).read_text(encoding="utf-8"))
    assert data["panel"] and data["asked"] and not feedback.should_ask(sdir, "ask")
    assert feedback.panel_allowed(sdir, "ask") and feedback.panel_allowed(sdir, None)
    assert feedback.panel_allowed(sdir, "never") and not feedback.panel_allowed(sdir, "off")
    assert feedback.mark_reported(sdir, ["f1", "f9"]) == 1
    assert [e["id"] for e in feedback.pending(sdir)] == ["f2"]
    assert feedback.mark_reported(sdir, ["f1"]) == 0
    assert {e["id"]: e["status"] for e in feedback.entries(sdir)} == {"f1": "reported", "f2": "new"}
