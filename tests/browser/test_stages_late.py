"""The Render, Final and Post stages in a real browser: live render progress with side runs as tabs, the final video
and its findings, the acknowledgement, the post text's Copy buttons and the plugin feedback panel."""

import json
import time

import pagefix
import pytest
from playwright.sync_api import expect

from browser.conftest import assert_healthy, open_short
from bsa import feedback
from bsa.review import decisions, notes

pytestmark = pytest.mark.browser


def _until(fn, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if fn():
            return True
        time.sleep(0.05)
    return fn()


def test_render_progress_updates_within_3s(view, live_server):
    srv = live_server("render")
    p = open_short(view, srv, stage="render")
    stage = p.locator("#bsa-stage")
    expect(stage.get_by_text("10 of 90 frames", exact=True)).to_be_visible()
    pagefix.write_render_status(srv.sdir, "preview", state="running", done=40)
    expect(stage.get_by_text("40 of 90 frames", exact=True)).to_be_visible(timeout=3000)
    expect(stage.get_by_role("progressbar")).to_have_attribute("aria-valuenow", "44")
    assert_healthy(p)


def test_render_side_runs_are_tabs(view, live_server):
    srv = live_server("render")
    pagefix.write_render_status(srv.sdir, "preview-eevee", state="done", done=90)
    p = open_short(view, srv, stage="render")
    stage = p.locator("#bsa-stage")
    first = stage.get_by_role("tab", name="Preview", exact=True)
    side = stage.get_by_role("tab", name="Preview · eevee")
    expect(side).to_be_visible(timeout=3000)
    expect(first).to_have_attribute("aria-selected", "true")
    first.focus()
    p.keyboard.press("ArrowRight")
    expect(side).to_have_attribute("aria-selected", "true")
    expect(side).to_be_focused()
    expect(stage.get_by_role("tabpanel").get_by_text("90 of 90 frames", exact=True)).to_be_visible()
    assert_healthy(p)


def test_render_stage_shows_the_check_note_beside_the_render(view, live_server):
    srv = live_server("render")
    path = srv.sdir / "review" / "render-status-preview.json"
    status = json.loads(path.read_text(encoding="utf-8"))
    check = next(s for s in status["run"]["steps"] if s["name"] == "check")
    check.update(state="running", finished=None,
                 progress={"done": 1, "total": 4, "unit": "shots", "current": "checking s02 (2 of 4), 0 errors so far"})
    path.write_text(json.dumps(status), encoding="utf-8")
    p = open_short(view, srv, stage="render")
    note = p.locator("#bsa-stage .step-note")
    expect(note).to_have_text("checking s02 (2 of 4), 0 errors so far")
    expect(p.locator("#bsa-stage").get_by_text("checking s02 (2 of 4)")).to_be_visible()
    assert_healthy(p)


def test_final_video_plays_and_finding_note(view, live_server):
    srv = live_server("final")
    has_video = (srv.sdir / "out" / "pendulum.mp4").is_file()
    p = open_short(view, srv, stage="final")
    stage = p.locator("#bsa-stage")
    expect(stage.get_by_text("frame 12 flickers")).to_be_visible()
    stage.get_by_role("button", name="Note on finding: flicker, shot s02 · frame 12").click()
    p.get_by_role("textbox").fill("Re-render it")
    p.get_by_role("button", name="Save note").click()
    expect(p.get_by_text("Not sent", exact=True)).to_be_visible()
    n = notes.load(srv.sdir)[-1]
    assert n["stage"] == "final" and n["target"]["kind"] == "finding" and len(n["target"]["id"]) == 10
    if not has_video:
        expect(stage.get_by_text("The final video is not made yet.")).to_be_visible()
        assert_healthy(p)
        pytest.skip("ffmpeg is not installed: no MP4 to play")
    video = stage.locator("video")
    expect(video).to_have_attribute("playsinline", "")
    assert _until(lambda: video.evaluate("v => v.readyState") >= 1)
    assert_healthy(p)


def test_final_ack_records_decision(view, live_server):
    srv = live_server("post")
    p = open_short(view, srv, stage="final")
    p.get_by_role("button", name="Looks good · go to Post").click()
    assert _until(lambda: bool(decisions.load(srv.sdir)) and decisions.load(srv.sdir)[-1]["stage"] == "final")
    d = decisions.load(srv.sdir)[-1]
    assert (d["stage"], d["decision"]) == ("final", "approve")
    expect(p.locator("#bsa-stage").get_by_text("You said it looks good.")).to_be_visible()
    assert_healthy(p)


def test_post_copy_buttons_and_field_note(view, live_server):
    srv = live_server("post")
    view.context.grant_permissions(["clipboard-read", "clipboard-write"])
    p = open_short(view, srv, stage="post")
    stage = p.locator("#bsa-stage")
    stage.get_by_role("button", name="Copy the title").click()
    assert _until(lambda: p.evaluate("navigator.clipboard.readText()") == "Why a pendulum keeps perfect time")
    stage.get_by_role("button", name="Copy the hashtags").click()
    assert _until(lambda: p.evaluate("navigator.clipboard.readText()") == "#physics #pendulum #blender #science")
    stage.get_by_role("button", name="Note on the description").click()
    p.get_by_role("textbox").fill("Shorter, please")
    p.get_by_role("button", name="Save note").click()
    expect(p.get_by_text("Not sent", exact=True)).to_be_visible()
    n = notes.load(srv.sdir)[-1]
    assert n["stage"] == "post" and n["target"]["kind"] == "field" and n["target"]["id"] == "description"
    expect(stage.get_by_text("Plugin feedback")).to_have_count(0)  # no panel: Claude has not asked
    assert_healthy(p)


def test_feedback_panel_copy_marks_reported(view, live_server):
    srv = live_server("post")
    feedback.add(srv.sdir, "blocking", "x")
    feedback.allow_panel(srv.sdir)
    p = open_short(view, srv, stage="post")
    view.context.grant_permissions(["clipboard-read", "clipboard-write"])
    stage = p.locator("#bsa-stage")
    expect(stage.get_by_role("heading", name="Plugin feedback")).to_be_visible()
    assert feedback.pending(srv.sdir)  # showing the panel marks nothing
    p.get_by_role("button", name="Copy the report").click()
    assert _until(lambda: feedback.pending(srv.sdir) == [])
    assert "### f1. x" in p.evaluate("navigator.clipboard.readText()")
    expect(stage.get_by_text("Sent, thank you.")).to_be_visible(timeout=3000)  # not the same buttons again
    expect(stage.get_by_role("button", name="Copy the report")).to_have_count(0)
    expect(stage.get_by_role("link", name="Open on GitHub")).to_have_count(0)
    assert_healthy(p)


def test_feedback_panel_open_on_github_marks_reported(view, live_server):
    srv = live_server("post")
    feedback.add(srv.sdir, "rough-edge", "y")
    feedback.allow_panel(srv.sdir)
    view.context.route("https://github.com/**", lambda route: route.abort())  # nothing leaves the machine
    p = open_short(view, srv, stage="post")
    link = p.locator("#bsa-stage").get_by_role("link", name="Open on GitHub")
    expect(link).to_have_attribute("href", feedback.report(srv.sdir)["url"])
    expect(link).to_have_attribute("rel", "noopener noreferrer")
    with view.context.expect_page():
        link.click()
    assert _until(lambda: feedback.pending(srv.sdir) == [])
    assert_healthy(p)
