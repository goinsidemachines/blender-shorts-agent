"""The Render, Final and Post stages of the snapshot, and the plugin feedback panel's rules."""

import json

import pagefix

from bsa import feedback, jobstate, post
from bsa.review import checklist, decisions, snapshot


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
    assert [f["key"] for f in c["fields"]] == list(post.FIELDS)
    assert [f["label"] for f in c["fields"]] == ["Title", "Description", "Hashtags", "Tags", "Pinned comment", "Link",
                                                 "Related video"]  # fmt: skip
    assert c["fields"][0]["text"] == "Why a pendulum keeps perfect time"
    assert c["fields"][2]["text"] == "#physics #pendulum #blender #science"
    assert [f["text"] for f in c["fields"][3:]] == ["", "", "", ""]
    assert c["closing"] is None and c["credits"] == []


def test_post_candidates_and_no_thumbnail_yet(tmp_path):
    from bsa import thumbs

    sdir = pagefix.make_short(tmp_path, "post")
    c = _stage(snapshot.build(sdir, settings={}), "post")["content"]
    assert c["thumbnail"] is None and c["candidates"] == []
    pagefix.write_thumbs(sdir, 2)
    c = _stage(snapshot.build(sdir, settings={}), "post")["content"]
    assert [x["n"] for x in c["candidates"]] == [1, 2] and c["thumbnail"] is None
    two = c["candidates"][1]
    assert two["hero"]["shot"] == "s02" and two["source"] == "final" and two["chosen"] is False
    assert two["vertical"] == {"file": "review/thumbs/2-1080x1920.png"}
    assert two["landscape"] == {"file": "review/thumbs/2-1280x720.png"}
    assert two["warnings"] == [] and two["findings"] == []
    thumbs.choose(sdir, 2, "page")
    c = _stage(snapshot.build(sdir, settings={}), "post")["content"]
    assert c["thumbnail"] == {"chosen": 2, "file": {"file": "out/thumbnail-1080x1920.png"}}
    assert [x["chosen"] for x in c["candidates"]] == [False, True]


def test_post_version_moves_with_the_chosen_thumbnail_not_with_candidates(tmp_path):
    from bsa import thumbs

    sdir = pagefix.make_short(tmp_path, "post")
    v = _stage(snapshot.build(sdir, settings={}), "post")["version"]
    pagefix.write_thumbs(sdir, 2)
    assert _stage(snapshot.build(sdir, settings={}), "post")["version"] == v
    thumbs.choose(sdir, 1, "page")
    assert _stage(snapshot.build(sdir, settings={}), "post")["version"] == v + 1
    thumbs.choose(sdir, 2, "page")
    assert _stage(snapshot.build(sdir, settings={}), "post")["version"] == v + 2


def test_a_damaged_thumbs_store_is_a_problem_line_not_a_crash(tmp_path):
    sdir = pagefix.make_short(tmp_path, "post")
    (sdir / "review" / "thumbs.json").write_text("{", encoding="utf-8")
    row = _stage(snapshot.build(sdir, settings={}), "post")
    assert row["content"]["fields"][0]["text"] == "Why a pendulum keeps perfect time"
    assert row["content"]["candidates"] == []
    assert "thumbs.json is damaged" in row["content"]["thumbs_problem"]
    assert str(sdir) not in row["content"]["thumbs_problem"]


def _set_post(sdir, **kw):
    p = sdir / "source" / "short.json"
    d = json.loads(p.read_text("utf-8"))
    d["post"].update(kw)
    p.write_text(json.dumps(d), "utf-8")


def _field(c, key):
    return next(f for f in c["fields"] if f["key"] == key)


def test_post_fields_show_counts_notes_and_platform_badges(tmp_path):
    sdir = pagefix.make_short(tmp_path, "post")
    _set_post(sdir, title="T" * 70, description="d" * 2300, tags=["pendulum clock", "physics"],
              related={"title": "How a lever works", "url": "https://example.com/lever"})  # fmt: skip
    c = _stage(snapshot.build(sdir, settings={}), "post")["content"]
    title = _field(c, "title")
    assert (title["count"], title["max"], title["warn_over"], title["state"]) == (70, 100, 60, "warn")
    notes = c["field_notes"]
    assert "may be cut off in the feed" in notes["title"] and title["badges"] == []
    desc = _field(c, "description")
    assert desc["state"] == "warn" and desc["badges"] == ["YouTube"] and "too long for Instagram" in notes["description"]
    hashtags = _field(c, "hashtags")
    assert (hashtags["count"], hashtags["max"], hashtags["state"]) == (4, 6, "ok") and "hashtags" not in notes
    tags = _field(c, "tags")
    assert tags["text"] == "pendulum clock, physics" and tags["count"] == post.tags_length(["pendulum clock", "physics"])
    for key in ("link", "related"):
        row = _field(c, key)
        assert [row[k] for k in ("count", "max", "warn_over", "state")] == [None] * 4 and row["badges"] == []
    assert _field(c, "related")["text"] == "How a lever works https://example.com/lever"
    _set_post(sdir, title="T" * 101, link="http://example.com")
    c = _stage(snapshot.build(sdir, settings={}), "post")["content"]
    assert _field(c, "title")["state"] == "over" and "YouTube allows 100" in c["field_notes"]["title"]
    assert "must start with https://" in c["field_notes"]["link"]
    assert set(_field(c, "description")) == {"key", "label", "text", "count", "max", "warn_over", "state", "badges"}


def test_settings_change_field_notes_but_never_the_post_version(tmp_path):
    sdir = pagefix.make_short(tmp_path, "post")
    _set_post(sdir, link="https://example.com/pendulum")
    row = _stage(snapshot.build(sdir, settings={"website": "example.com"}), "post")
    assert "link" not in row["content"]["field_notes"]
    again = _stage(snapshot.build(sdir, settings={}), "post")
    assert "is not on your website (none is set)" in again["content"]["field_notes"]["link"]
    assert again["version"] == row["version"]
    fmt = {"post_format": {"title_style": "question"}}
    assert _stage(snapshot.build(sdir, settings=fmt), "post")["version"] == row["version"]


def test_ticks_and_files_never_move_the_post_version(tmp_path):
    sdir = pagefix.make_short(tmp_path, "post")
    row = _stage(snapshot.build(sdir, settings={}), "post")
    v = row["version"]
    assert [i["id"] for i in row["content"]["checklist"]] == ["upload", "length", "link"]
    assert not any(i["done"] for i in row["content"]["checklist"])
    checklist.tick(sdir, "link", True)
    (sdir / "out" / "pendulum.srt").write_text("1\n", "utf-8")
    row = _stage(snapshot.build(sdir, settings={}), "post")
    assert row["version"] == v
    assert {i["id"]: i["done"] for i in row["content"]["checklist"]}["link"] is True
    assert next(f for f in row["content"]["files"] if f["key"] == "srt")["exists"]


def test_a_damaged_checklist_is_a_problem_line_not_a_crash(tmp_path):
    sdir = pagefix.make_short(tmp_path, "post")
    checklist.path(sdir).write_text("{", encoding="utf-8")
    row = _stage(snapshot.build(sdir, settings={}), "post")
    assert row["problem"] is None and not any(i["done"] for i in row["content"]["checklist"])
    assert "checklist.json" in row["content"]["checklist_problem"]


def test_stale_caption_files_are_flagged_on_the_page(tmp_path):
    from bsa import captions
    from bsa.short import load_short

    sdir = pagefix.make_short(tmp_path, "done")
    captions.write_files(sdir, load_short(sdir / "source" / "short.json"), {})
    content = _stage(snapshot.build(sdir, settings={}), "post")["content"]
    row = next(f for f in content["files"] if f["key"] == "srt")
    assert row["exists"] and not row["stale"]
    pagefix.record_edit(sdir, "final", stale=True)
    content = _stage(snapshot.build(sdir, settings={}), "post")["content"]
    row = next(f for f in content["files"] if f["key"] == "srt")
    assert row["exists"] and row["stale"]


# --------------------------------------------------------------------------- feedback


def test_feedback_panel_rules(tmp_path):
    sdir = pagefix.make_short(tmp_path, "post")
    feedback.add(sdir, "rough-edge", "a thing")
    assert snapshot.build(sdir, settings={})["feedback"] is None  # not asked yet
    feedback.allow_panel(sdir)
    assert snapshot.build(sdir, settings={"feedback": "off"})["feedback"] is None
    snap = snapshot.build(sdir, settings={})
    assert snap["feedback"]["entries"] == 1 and feedback.pending(sdir)  # building never marks reported
    assert snap["feedback"]["sent"] is False


def test_feedback_panel_says_sent_once_every_entry_is_reported(tmp_path):
    sdir = pagefix.make_short(tmp_path, "post")
    feedback.add(sdir, "rough-edge", "a thing")
    feedback.allow_panel(sdir)
    feedback.mark_reported(sdir, ["f1"])
    fb = snapshot.build(sdir, settings={})["feedback"]
    assert fb["sent"] is True and fb["entries"] == 1
    feedback.add(sdir, "blocking", "a new thing")  # noted after the report: the panel shows only that one
    fb = snapshot.build(sdir, settings={})["feedback"]
    assert fb["sent"] is False and fb["ids"] == ["f2"]


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


# --------------------------------------------------------------------------- versions that must not move


def test_side_runs_do_not_change_the_engine_version(tmp_path, monkeypatch):
    from bsa import engine_runs

    sdir = pagefix.make_short(tmp_path, "engine")
    before = _stage(snapshot.build(sdir, settings={}), "engine")
    run = {"preset": "preview", "engine": "cycles", "tag": "cycles", "s_per_frame": 4.2, "frames_measured": 30,
           "approved": False, "mp4": None}  # fmt: skip
    monkeypatch.setattr(engine_runs, "compare", lambda _sdir, preset=None: [run])
    after = _stage(snapshot.build(sdir, settings={}), "engine")
    assert after["content"]["runs"] == [run] and after["version"] == before["version"]
    assert snapshot.stage_version(sdir, "engine", snapshot.load_ctx(sdir, {})) == before["version"]


def test_final_is_not_versioned_while_its_mp4_is_being_written(tmp_path):
    sdir = pagefix.make_short(tmp_path, "final")
    pagefix.write_render_status(sdir, "final", state="running")  # the final edit is encoding out/<slug>.mp4
    (sdir / "out").mkdir(exist_ok=True)
    (sdir / "out" / "pendulum.mp4").write_bytes(bytes(1000))  # half written
    row = _stage(snapshot.build(sdir, settings={}), "final")
    assert row["version"] is None and row["content"]["video"] is None and row["content"]["writing"] is True
    assert snapshot.stage_version(sdir, "final") is None
    assert "final" not in json.loads((sdir / "review" / "versions.json").read_text("utf-8"))
    pagefix.write_render_status(sdir, "final", state="done")  # the encode finished
    row = _stage(snapshot.build(sdir, settings={}), "final")
    assert row["version"] == 1 and row["content"]["video"] == {"file": "out/pendulum.mp4"}
    assert row["content"]["writing"] is False


def test_final_is_versioned_once_the_final_step_is_done_in_state(tmp_path):
    sdir = pagefix.make_short(tmp_path, "final")
    pagefix.write_render_status(sdir, "final", state="running")
    jobstate.mark_done(sdir, "final")
    assert _stage(snapshot.build(sdir, settings={}), "final")["version"] == 1


# --------------------------------------------------------------------------- timeline


def test_final_has_a_timeline_that_never_moves_its_version(tmp_path):
    sdir = pagefix.make_short(tmp_path, "final")
    pagefix.write_timeline(sdir, frames=90)
    (sdir / "out" / "pendulum.mp4").write_bytes(bytes(2000))  # a stand-in when ffmpeg is missing, never played
    pagefix.record_edit(sdir, "final")
    row = _stage(snapshot.build(sdir, settings={}), "final")
    v = row["version"]
    assert row["content"]["timeline"]["video"]["file"] == "out/pendulum.mp4"
    path = sdir / "review" / "frame-checks-final.json"
    path.write_text(json.dumps({"findings": [{"check": "blur", "severity": "warning", "message": "soft",
                                              "frame": 20, "shots": ["s01"]}]}), encoding="utf-8")  # fmt: skip
    s2 = _stage(snapshot.build(sdir, settings={}), "final")
    assert any(f["f"] == 20 for f in s2["content"]["timeline"]["tracks"]["findings"]) and s2["version"] == v + 1
    pagefix.write_timeline(sdir, frames=91)  # only the timeline moved: not a new version
    assert _stage(snapshot.build(sdir, settings={}), "final")["version"] == v + 1


def test_final_has_no_timeline_without_a_video(tmp_path):
    sdir = pagefix.make_short(tmp_path, "story")
    assert _stage(snapshot.build(sdir, settings={}), "final")["content"]["timeline"] is None


def test_render_runs_have_their_own_timelines(tmp_path):
    sdir = pagefix.make_short(tmp_path, "render", side_run="eevee")
    pagefix.record_edit(sdir, "preview-eevee")
    runs = {r["key"]: r for r in _stage(snapshot.build(sdir, settings={}), "render")["content"]["runs"]}
    assert runs["preview-eevee"]["timeline"]["run"] == "preview-eevee"
    assert runs["preview"]["timeline"]["video"]["file"] == "out/pendulum-preview.mp4"
    (sdir / "source" / "timeline.json").write_text("{", encoding="utf-8")
    runs = {r["key"]: r for r in _stage(snapshot.build(sdir, settings={}), "render")["content"]["runs"]}
    assert runs["preview"]["timeline"] is None and runs["preview"]["timeline_problem"]


def test_final_says_when_its_timeline_cannot_be_read_without_moving_the_version(tmp_path):
    sdir = pagefix.make_short(tmp_path, "final")
    pagefix.write_timeline(sdir, frames=90)
    (sdir / "out").mkdir(exist_ok=True)
    (sdir / "out" / "pendulum.mp4").write_bytes(bytes(2000))  # a stand-in when ffmpeg is missing, never played
    pagefix.record_edit(sdir, "final")
    v = _stage(snapshot.build(sdir, settings={}), "final")["version"]
    (sdir / "source" / "timeline.json").write_text("{", encoding="utf-8")
    row = _stage(snapshot.build(sdir, settings={}), "final")
    assert row["content"]["timeline"] is None and row["content"]["timeline_problem"] and row["version"] == v


# --------------------------------------------------------------------------- caption track


def test_final_has_a_caption_track_that_never_moves_its_version(tmp_path):
    from bsa import captions
    from bsa.short import load_short

    sdir = pagefix.make_short(tmp_path, "done")
    mp4 = sdir / "out" / "pendulum.mp4"
    if not mp4.is_file():  # no ffmpeg: a stand-in file is enough for the snapshot
        mp4.write_bytes(bytes(2000))
    short = load_short(sdir / "source" / "short.json")
    before = _stage(snapshot.build(sdir, settings={}), "final")
    assert before["content"]["captions"] is None
    captions.write_files(sdir, short, {})
    after = _stage(snapshot.build(sdir, settings={}), "final")
    assert after["content"]["captions"] == {"file": "out/pendulum.vtt"}
    assert after["version"] == before["version"]
    pagefix.record_edit(sdir, "final", stale=True)  # the files are still there but out of step
    assert (sdir / "out" / "pendulum.vtt").is_file()
    assert _stage(snapshot.build(sdir, settings={}), "final")["content"]["captions"] is None
