"""CLI page decide / note / wait: approvals only through the page's decision log, notes in every result."""

import json
import os
import subprocess
import sys

from bsa import jobstate
from bsa.review import decisions, notes
from tests import test_cli
from tests.test_cli import CLI, ok, run

home, workspace, pendulum = test_cli.home, test_cli.workspace, test_cli.pendulum  # the CLI tests' fixtures


def _to(home, short, stage):
    """Decide (by chat) every approval stage before `stage`, after the interview."""
    ok(run(home, "state", short.name, "done", "interview"))
    for earlier in ("story", "storyboard", "engine"):
        if earlier == stage:
            return
        extra = ["--choice", "eevee"] if earlier == "engine" else []
        ok(run(home, "page", "decide", short.name, earlier, "approve", "--by", "chat", *extra))


def test_decide_by_chat_marks_state_in_one_step(home, pendulum):
    ok(run(home, "state", "pendulum", "done", "interview"))
    out = ok(run(home, "page", "decide", "pendulum", "story", "approve", "--by", "chat"))
    assert out["decision"]["by"] == "chat" and out["decision"]["applied"] is True
    assert out["state"]["stages"]["story"]["version"] == 1 and out["state"]["next"] == "storyboard"


def test_state_done_story_without_a_decision_is_refused(home, pendulum):
    ok(run(home, "state", "pendulum", "done", "interview"))
    proc = run(home, "state", "pendulum", "done", "story")
    assert proc.returncode == 1 and "page decide" in proc.stderr
    assert "Traceback" not in proc.stderr


def test_engine_choice_recorded_like_info(home, pendulum):
    _to(home, pendulum, "engine")
    out = ok(run(home, "page", "decide", "pendulum", "engine", "approve", "--by", "chat", "--choice", "eevee"))
    assert out["state"]["stages"]["engine"]["engine"] == "eevee"
    proc = run(home, "page", "decide", "pendulum", "engine", "approve", "--by", "settings", "--choice", "cycles")
    assert proc.returncode == 1  # setting is "ask", and already approved


def test_wait_returns_page_approval_and_applies_it(home, pendulum):
    _to(home, pendulum, "story")
    decisions.record(pendulum, "story", 1, "approve", "page", current=1)
    out = ok(run(home, "page", "wait", "pendulum", "--stage", "story", "--timeout", "5"))
    assert out["decision"]["by"] == "page" and out["applied"] is True and not out["timed_out"]
    assert "story" in jobstate.load_state(pendulum)["stages"]


def test_wait_ends_on_sent_notes_and_on_timeout(home, pendulum):
    _to(home, pendulum, "story")
    assert ok(run(home, "page", "wait", "pendulum", "--stage", "story", "--timeout", "1"))["timed_out"] is True
    notes.add(pendulum, "story", {"kind": "line", "id": "l02", "version": 1}, "shorter", "page")
    notes.send_stage(pendulum, "story")
    out = ok(run(home, "page", "wait", "pendulum", "--stage", "story", "--timeout", "5"))
    assert [n["target"]["id"] for n in out["notes"]] == ["l02"] and out["decision"] is None


def test_stale_version_is_refused_with_a_clear_message(home, pendulum):
    _to(home, pendulum, "story")
    proc = run(home, "page", "decide", "pendulum", "story", "approve", "--by", "page", "--version", "7")
    assert proc.returncode == 1 and "changed since" in proc.stderr


def test_note_reply_and_page_notes_in_every_result(home, pendulum):
    _to(home, pendulum, "story")
    n = notes.add(pendulum, "story", {"kind": "overall", "id": None, "version": 1}, "warmer hook", "page")
    assert ok(run(home, "state", "pendulum"))["page_notes"] == {"count": 1, "ids": [n["id"]], "decisions": []}
    out = ok(run(home, "page", "note", "pendulum", n["id"], "done", "--reply", "Warmer now."))
    assert out["note"]["reply"] == "Warmer now." and "page_notes" not in ok(run(home, "state", "pendulum"))


def test_reset_supersedes_approvals(home, pendulum):
    _to(home, pendulum, "storyboard")
    ok(run(home, "state", "pendulum", "reset", "story"))
    assert ok(run(home, "page", "decide", "pendulum", "story", "approve", "--by", "chat"))["decision"]["version"] == 1


def test_chat_and_page_approve_race_marks_state_once(home, pendulum):
    _to(home, pendulum, "story")
    procs = [
        subprocess.Popen(
            [sys.executable, str(CLI), "page", "decide", "pendulum", "story", "approve", "--by", by],
            env={**os.environ, "BSA_HOME": str(home)}, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        for by in ("page", "chat", "page", "chat")
    ]  # fmt: skip
    codes = [p.wait(60) for p in procs]
    assert codes.count(0) == 1 and len([d for d in decisions.load(pendulum) if d["decision"] == "approve"]) == 1


def test_apply_decision_marks_the_stage_done_once(home, pendulum, monkeypatch):
    import bsa_cli

    monkeypatch.setenv("BSA_HOME", str(home))
    _to(home, pendulum, "story")
    entry = decisions.record(pendulum, "story", 1, "approve", "page", current=1)
    calls = []
    real = jobstate.mark_done
    monkeypatch.setattr(jobstate, "mark_done", lambda *a, **k: (calls.append(k), real(*a, **k)))
    first = bsa_cli._apply_decision(pendulum, entry)
    second = bsa_cli._apply_decision(pendulum, entry)  # the loser of a race
    assert len(calls) == 1 and first["applied"] and second["applied"]
    assert jobstate.load_state(pendulum)["stages"]["story"]["decision"] == entry["id"]


def test_a_failed_apply_gives_the_claim_back(home, pendulum, monkeypatch):
    import bsa_cli

    monkeypatch.setenv("BSA_HOME", str(home))
    _to(home, pendulum, "story")
    entry = decisions.record(pendulum, "story", 1, "approve", "page", current=1)

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(jobstate, "mark_done", boom)
    try:
        bsa_cli._apply_decision(pendulum, entry)
    except OSError:
        pass
    assert [d["id"] for d in decisions.unapplied(pendulum)] == [entry["id"]]


def test_wait_racing_decide_marks_state_once(home, pendulum):
    _to(home, pendulum, "story")
    env = {**os.environ, "BSA_HOME": str(home)}
    waiter = subprocess.Popen(
        [sys.executable, str(CLI), "page", "wait", "pendulum", "--stage", "story", "--timeout", "10", "--poll", "0.05"],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )  # fmt: skip
    deciders = [
        subprocess.Popen(
            [sys.executable, str(CLI), "page", "decide", "pendulum", "story", "approve", "--by", by],
            env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        for by in ("page", "chat")
    ]  # fmt: skip
    codes = [p.wait(60) for p in deciders]
    assert codes.count(0) == 1 and waiter.wait(60) == 0
    (only,) = [d for d in decisions.load(pendulum) if d["decision"] == "approve"]
    assert only["applied"] and jobstate.load_state(pendulum)["stages"]["story"]["decision"] == only["id"]


def test_small_refusals(home, pendulum):
    _to(home, pendulum, "story")
    proc = run(home, "state", "pendulum", "reset")
    assert proc.returncode == 1 and "reset STAGE" in proc.stderr and "None" not in proc.stderr
    proc = run(home, "page", "decide", "pendulum", "story", "changes", "--by", "settings", "--note", "x")
    assert proc.returncode == 1 and "from settings" in proc.stderr
    assert notes.load(pendulum) == []


def test_changes_need_a_note_and_send_the_stage_notes(home, pendulum):
    _to(home, pendulum, "story")
    proc = run(home, "page", "decide", "pendulum", "story", "changes", "--by", "chat")
    assert proc.returncode == 1 and "Add a note saying what to change first." in proc.stderr
    out = ok(run(home, "page", "decide", "pendulum", "story", "changes", "--by", "chat", "--note", "shorter hook"))
    assert out["decision"]["decision"] == "changes" and [n["text"] for n in out["notes_sent"]] == ["shorter hook"]
    assert out["notes_sent"][0]["status"] == "sent"
    assert "story" not in out["state"]["stages"]


def test_settings_answer_needs_the_matching_setting(home, pendulum):
    _to(home, pendulum, "engine")
    ok(run(home, "settings", "set", "render.engine", "eevee"))
    proc = run(home, "page", "decide", "pendulum", "engine", "approve", "--by", "settings", "--choice", "cycles")
    assert proc.returncode == 1 and "render.engine" in proc.stderr
    out = ok(run(home, "page", "decide", "pendulum", "engine", "approve", "--by", "settings", "--choice", "eevee"))
    assert out["state"]["stages"]["engine"]["by"] == "settings"


def test_settings_answer_accepts_the_engine_short_json_asks_for(home, pendulum):
    path = pendulum / "source" / "short.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["render"] = {"engine": "cycles"}
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    _to(home, pendulum, "engine")
    proc = run(home, "page", "decide", "pendulum", "engine", "approve", "--by", "settings", "--choice", "eevee")
    assert proc.returncode == 1 and "short.json" in proc.stderr and "Traceback" not in proc.stderr
    out = ok(run(home, "page", "decide", "pendulum", "engine", "approve", "--by", "settings", "--choice", "cycles"))
    assert out["state"]["stages"]["engine"]["by"] == "settings"
    assert out["state"]["stages"]["engine"]["engine"] == "cycles"


def test_damaged_decisions_fail_with_one_plain_line(home, pendulum):
    _to(home, pendulum, "story")
    (pendulum / "review" / "decisions.json").write_text("", encoding="utf-8")
    for args in (("decide", "pendulum", "story", "approve", "--by", "chat"), ("wait", "pendulum", "--stage", "story")):
        proc = run(home, "page", *args)
        assert proc.returncode == 1 and "decisions.json" in proc.stderr and "damaged" in proc.stderr
        assert "Traceback" not in proc.stderr and len(proc.stderr.strip().splitlines()) == 1
    out = ok(run(home, "state", "pendulum"))
    assert "problem" in out["page_notes"]


def test_bad_page_words_and_bad_stage_are_plain_errors(home, pendulum):
    proc = run(home, "page", "pendulum", "extra")
    assert proc.returncode == 1 and "usage: page SHORT" in proc.stderr and "Traceback" not in proc.stderr
    proc = run(home, "page", "decide", "pendulum", "render", "approve", "--by", "chat")
    assert proc.returncode == 1 and "nothing to approve" in proc.stderr


def test_bsa_blender_results_carry_page_notes(home, pendulum, monkeypatch, capsys):
    import bsa_blender

    monkeypatch.setenv("BSA_HOME", str(home))
    monkeypatch.setattr(bsa_blender, "cmd_anchors", lambda args: {"ok": True})
    notes.add(pendulum, "story", {"kind": "overall", "id": None, "version": 1}, "warmer hook", "page")
    assert bsa_blender.main(["anchors", "pendulum"]) == 0
    line = [x for x in capsys.readouterr().out.splitlines() if x.startswith("BSA_RESULT ")][-1]
    assert json.loads(line[len("BSA_RESULT ") :])["page_notes"] == {"count": 1, "ids": ["n1"], "decisions": []}


# ------------------------------------------------------------------- the old commands point at the one page


def _no_browser_run(home, *args):
    from tests import test_page_launch

    return test_page_launch.run(home, *args)  # BROWSER is a no-op: nothing opens on the developer's screen


def test_review_returns_the_one_page_url(home, pendulum):
    out = ok(_no_browser_run(home, "review", "story", "pendulum"))
    assert "/s/" in out["page"] and out["page"].endswith("#story") and out["stage"] == "story"
    assert out["deprecated"] == "use CLI page SHORT" and "opened" not in out
    assert not (pendulum / "review" / "story.html").exists()
    out = ok(_no_browser_run(home, "review", "storyboard", "pendulum", "--open"))
    assert out["page"].endswith("#storyboard") and out["opened"] is False  # the no-op browser never checks in
    assert not list((pendulum / "review").glob("*.html"))


def test_render_status_writes_no_html(home, pendulum):
    out = ok(_no_browser_run(home, "render-status", "pendulum"))
    assert out["page"].endswith("#render") and "/s/" in out["page"] and out["alive"] is False
    assert out["status_file"].endswith("render-status-preview.json")
    assert not list((pendulum / "review").glob("render-*.html"))


def test_feedback_report_allows_the_panel_without_marking(home, pendulum):
    from bsa import feedback

    ok(_no_browser_run(home, "feedback", "add", "pendulum", "--severity", "rough-edge", "--summary", "odd"))
    out = ok(_no_browser_run(home, "feedback", "report", "pendulum"))
    assert out["page"].endswith("#post") and out["url"].startswith(feedback.REPO) and out["entries"] == 1
    assert {"title", "shortened", "by_severity"} <= set(out)
    assert feedback.panel_allowed(pendulum, "ask") and len(feedback.pending(pendulum)) == 1  # nothing marked
    assert not (pendulum / "review" / "plugin-feedback.html").exists()
