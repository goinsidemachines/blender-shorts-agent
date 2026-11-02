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


def test_damaged_decisions_fail_with_one_plain_line(home, pendulum):
    _to(home, pendulum, "story")
    (pendulum / "review" / "decisions.json").write_text("", encoding="utf-8")
    for args in (("decide", "pendulum", "story", "approve", "--by", "chat"), ("wait", "pendulum", "--stage", "story")):
        proc = run(home, "page", *args)
        assert proc.returncode == 1 and "decisions.json" in proc.stderr and "damaged" in proc.stderr
        assert "Traceback" not in proc.stderr and len(proc.stderr.strip().splitlines()) == 1
    out = ok(run(home, "state", "pendulum"))
    assert "problem" in out["page_notes"]


def test_unwritten_page_commands_and_bad_stage_are_plain_errors(home, pendulum):
    proc = run(home, "page", "pendulum")
    assert proc.returncode == 1 and "not available yet" in proc.stderr
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
