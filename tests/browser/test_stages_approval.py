"""The Story, Storyboard, Engine and Narration stages in a real browser: what they show, the notes on lines, shots and
stills, the enlarge dialog, and the answers they send."""

import json
import re

import pytest
from playwright.sync_api import expect

from browser.conftest import assert_healthy, open_short
from bsa import settings as settings_mod
from bsa.review import decisions, notes

pytestmark = pytest.mark.browser


@pytest.fixture
def live_server_with_settings(live_server, bsa_home):
    """A live server whose user settings choose EEVEE (`render.engine`)."""
    settings_mod.save_settings({"render": {"engine": "eevee"}})
    return live_server


def test_story_renders_and_line_note(view, live_server):
    srv = live_server("story")
    p = open_short(view, srv)
    expect(p.locator(".st-story .beats").get_by_text("A pendulum swings like clockwork.")).to_be_visible()
    p.get_by_role("button", name="Note on line l02").click()
    p.get_by_role("textbox").fill("Say 'beat'")
    p.get_by_role("button", name="Save note").click()
    expect(p.get_by_text("Not sent", exact=True)).to_be_visible()
    n = notes.load(srv.sdir)[-1]
    assert n["stage"] == "story" and n["target"] == {"kind": "line", "id": "l02", "version": 1} and n["status"] == "open"
    expect(p.get_by_role("button", name="Note on line l02")).to_be_focused()  # focus goes back to the line's button
    assert_healthy(p)


def test_story_shows_beats_facts_and_rules(view, live_server):
    srv = live_server("story")
    p = open_short(view, srv)
    stage = p.locator("#bsa-stage")
    for title in ("Hook", "Length not weight", "Clockwork"):
        expect(stage.get_by_role("heading", level=4, name=title, exact=True)).to_be_visible()
    expect(stage.get_by_text("Motion simplified")).to_be_visible()  # a beat's badge
    expect(stage.locator(".pace")).to_have_count(3)
    link = stage.get_by_role("link", name=re.compile("wikipedia"))
    expect(link).to_have_attribute("href", "https://en.wikipedia.org/wiki/Pendulum_(mechanics)")
    expect(link).to_have_attribute("target", "_blank")
    expect(link).to_have_attribute("rel", "noopener noreferrer")
    expect(stage.get_by_role("heading", name="Downloads")).to_be_visible()
    assert_healthy(p)


def test_story_and_storyboard_show_the_hard_rules(view, live_server, bsa_home):
    settings_mod.save_settings({"rules": [{"id": "r1", "text": "No music, ever"}]})
    srv = live_server("storyboard")
    p = open_short(view, srv)
    stage = p.locator("#bsa-stage")
    expect(stage.get_by_role("heading", name="Your hard rules")).to_be_visible()
    expect(stage.get_by_text("No music, ever")).to_be_visible()
    expect(stage.get_by_text("Not answered yet", exact=False)).to_be_visible()
    open_short(view, srv, stage="story")
    expect(p.locator("#bsa-stage").get_by_text("No music, ever")).to_be_visible()
    assert_healthy(p)


def test_story_fact_that_is_not_a_link_stays_text(view, live_server):
    srv = live_server("story")
    path = srv.sdir / "source" / "short.json"
    path.write_text(path.read_text("utf-8").replace("https://en.wikipedia.org/wiki/Pendulum_(mechanics)",
                                                    "javascript:alert(1)"), "utf-8")  # fmt: skip
    p = open_short(view, srv)
    expect(p.locator("#bsa-stage ul.facts").get_by_text("javascript:alert(1)")).to_be_visible()
    expect(p.locator("#bsa-stage a")).to_have_count(0)
    assert_healthy(p)


def test_story_approve_writes_decision_then_shows_applied(view, live_server):
    srv = live_server("story")
    p = open_short(view, srv)
    p.get_by_role("button", name="Approve").click()
    expect(p.get_by_text("Sent · waiting for Claude")).to_be_visible()
    d = decisions.load(srv.sdir)[-1]
    assert (d["stage"], d["version"], d["decision"], d["by"], d["applied"]) == ("story", 1, "approve", "page", False)
    decisions.mark_applied(srv.sdir, d["id"])
    expect(p.get_by_text("Approved", exact=True)).to_be_visible(timeout=3000)
    assert_healthy(p)


def test_storyboard_send_changes_sends_notes(view, live_server):
    srv = live_server("storyboard")
    p = open_short(view, srv)
    expect(p.locator("img.still")).to_have_count(3)
    for _ in range(40):  # the stills load just after the first render
        if p.locator("img.still").evaluate_all("els => els.every(e => e.complete)"):
            break
        p.wait_for_timeout(50)
    assert p.locator("img.still").evaluate_all("els => els.every(e => e.naturalWidth > 0)")
    p.get_by_role("button", name="Note on shot s02").click()
    p.get_by_role("textbox").fill("closer")
    p.get_by_role("button", name="Save note").click()
    expect(p.get_by_text("Not sent", exact=True)).to_be_visible()
    p.get_by_role("button", name="Send changes").click()
    expect(p.get_by_text("Sent", exact=True)).to_be_visible()
    assert notes.load(srv.sdir)[-1]["status"] == "sent" and decisions.load(srv.sdir)[-1]["decision"] == "changes"
    assert notes.load(srv.sdir)[-1]["target"] == {"kind": "shot", "id": "s02", "version": 1}
    assert_healthy(p)


def test_storyboard_still_note_and_timing(view, live_server):
    srv = live_server("storyboard")
    p = open_short(view, srv)
    stage = p.locator("#bsa-stage")
    expect(stage.get_by_role("button", name=re.compile(r"^Shot \d: "))).to_have_count(3)  # the timing strip
    expect(stage.get_by_text("same length, same beat")).to_be_visible()  # a shot's on-screen label
    expect(stage.get_by_text("Four times longer, twice slower.")).to_be_visible()
    p.get_by_role("button", name="Note on still s03").click()
    p.get_by_role("textbox").fill("too dark")
    p.get_by_role("button", name="Save note").click()
    expect(p.get_by_text("Not sent", exact=True)).to_be_visible()
    assert notes.load(srv.sdir)[-1]["target"] == {"kind": "still", "id": "s03", "version": 1}
    stage.get_by_role("button", name=re.compile(r"^Shot 3: ")).click()  # a strip segment goes to its shot
    expect(p.get_by_role("button", name="Enlarge shot 3")).to_be_focused()
    assert_healthy(p)


def test_storyboard_enlarge_dialog_is_keyboard_closable(view, live_server):
    srv = live_server("storyboard")
    p = open_short(view, srv)
    thumb = p.get_by_role("button", name="Enlarge shot 2")
    thumb.focus()
    p.keyboard.press("Enter")
    dialog = p.get_by_role("dialog")
    expect(dialog).to_be_visible()
    expect(dialog).to_contain_text("Length not weight")
    expect(dialog.locator(".cap")).to_have_attribute("style", re.compile(r"left: ?[\d.]+%"))
    expect(dialog.locator(".frame")).to_have_attribute("style", re.compile("--cap-px: ?64"))
    dialog.get_by_role("button", name="Show safe area").click()
    expect(dialog.locator(".frame")).to_have_class(re.compile("showsafe"))
    p.keyboard.press("Escape")
    expect(dialog).to_be_hidden()
    expect(thumb).to_be_focused()
    assert_healthy(p)


def test_engine_approve_needs_a_choice(view, live_server):
    srv = live_server("engine")
    p = open_short(view, srv)
    expect(p.get_by_role("radio")).to_have_count(2)
    expect(p.get_by_text("about 5 min")).to_be_visible()  # eevee_s 300
    expect(p.get_by_text("about 20 min")).to_be_visible()  # cycles_s 1200
    p.get_by_role("button", name="Approve").click()  # nothing picked yet
    expect(p.get_by_role("alert")).to_contain_text("Pick an engine")
    assert not [d for d in decisions.load(srv.sdir) if d["stage"] == "engine"]
    p.get_by_role("radio", name=re.compile("EEVEE")).check()
    p.get_by_role("button", name="Approve").click()
    expect(p.get_by_text("Sent · waiting for Claude")).to_be_visible()
    assert decisions.load(srv.sdir)[-1]["choice"] == {"engine": "eevee"}
    assert_healthy(p)


def test_engine_from_settings_asks_nothing(view, live_server_with_settings):
    srv = live_server_with_settings("engine")
    p = open_short(view, srv)
    expect(p.get_by_text("EEVEE, from your settings")).to_be_visible()
    expect(p.get_by_role("radio")).to_have_count(0)
    expect(p.get_by_role("button", name="Approve")).to_have_count(0)
    expect(p.get_by_role("button", name="Send changes")).to_have_count(0)
    assert not p.title().startswith("(1)")
    assert_healthy(p)


def test_engine_from_short_json_asks_nothing(view, live_server):
    srv = live_server("engine")
    path = srv.sdir / "source" / "short.json"
    data = json.loads(path.read_text("utf-8"))
    data["render"] = {"engine": "cycles"}
    path.write_text(json.dumps(data, indent=2), "utf-8")
    p = open_short(view, srv)
    expect(p.get_by_text("CYCLES, from this short")).to_be_visible()
    expect(p.get_by_role("radio")).to_have_count(0)
    expect(p.get_by_role("button", name="Approve")).to_have_count(0)
    expect(p.get_by_role("button", name="Send changes")).to_have_count(0)
    assert not p.title().startswith("(1)")
    assert_healthy(p)


def test_engine_radios_lock_when_decisions_cannot_be_read(view, live_server):
    srv = live_server("engine")
    (srv.sdir / "review" / "decisions.json").write_text("{not json", encoding="utf-8")
    p = open_short(view, srv)
    expect(p.get_by_role("radio")).to_have_count(2)
    for radio in p.get_by_role("radio").all():
        expect(radio).to_be_disabled()
    assert_healthy(p)


def test_engine_radios_lock_once_approved_here(view, live_server):
    srv = live_server("engine")
    p = open_short(view, srv)
    p.get_by_role("radio", name=re.compile("EEVEE")).check()
    p.get_by_role("button", name="Approve").click()
    expect(p.get_by_text("Sent · waiting for Claude")).to_be_visible()
    expect(p.get_by_role("radio", name=re.compile("EEVEE"))).to_be_checked()
    for radio in p.get_by_role("radio").all():
        expect(radio).to_be_disabled()


def test_storyboard_missing_still_shows_a_placeholder(view, live_server):
    srv = live_server("storyboard")
    p = open_short(view, srv)
    expect(p.locator("img.still")).to_have_count(3)
    (srv.sdir / "stills" / "s02.png").unlink()  # the snapshot drops it: no broken img
    expect(p.locator("img.still")).to_have_count(2, timeout=3000)
    expect(p.locator("#shot-s02").get_by_text("Picture not made yet")).to_be_visible()
    assert_healthy(p)


def test_storyboard_still_that_fails_to_load_shows_a_placeholder(view, live_server):
    srv = live_server("storyboard")
    (srv.sdir / "stills" / "s02.jpg").write_bytes(b"not a picture")  # the .jpg wins over the .png, and won't load
    p = open_short(view, srv)
    expect(p.locator("#shot-s02").get_by_text("Picture not made yet")).to_be_visible()
    expect(p.locator("#shot-s02 img")).to_have_count(0)
    assert_healthy(p)


def test_narration_renders(view, live_server):
    srv = live_server("narration")
    p = open_short(view, srv)
    stage = p.locator("#bsa-stage")
    expect(stage.get_by_text("Not started yet")).to_be_visible()
    expect(stage.get_by_role("button")).to_have_count(0)  # an overall note only, beside the stage
    expect(p.get_by_role("button", name="Add a note on Narration")).to_be_visible()
    assert_healthy(p)


# --------------------------------------------------------------------------- the camera stage


def test_camera_stage_shows_the_clip_and_the_sheet(view, live_server):
    srv = live_server("camera", one_take=True)
    p = open_short(view, srv)
    stage = p.locator("#bsa-stage")
    expect(p.locator("#bsa-stage-h")).to_have_text("Camera")
    video = stage.locator("video.player")
    expect(video).to_have_attribute("src", re.compile(r"/review/camtest/camtest\.mp4\?"))
    expect(video).to_have_attribute("aria-label", "The camera test")
    sheet = stage.get_by_role("img", name="Contact sheet of the camera test: 12 frames across the short")
    expect(sheet).to_have_attribute("src", re.compile(r"/review/camtest/camtest-sheet\.png\?"))
    expect(stage.locator(".problem")).to_have_count(0)  # a current clip: nothing out of date
    expect(p.get_by_role("button", name="Approve")).to_be_visible()
    assert_healthy(p)
    p.emulate_media(color_scheme="dark")
    expect(video).to_be_visible()
    assert_healthy(p)


def test_camera_stage_approve_needs_a_current_clip(view, live_server):
    from bsa import camtest

    srv = live_server("camera", one_take=True)
    p = open_short(view, srv)
    stage = p.locator("#bsa-stage")
    expect(stage.locator("video.player")).to_have_count(1)
    mp4 = srv.sdir / camtest.FOLDER / camtest.MP4
    clip = mp4.read_bytes()
    mp4.unlink()  # no clip: the page names the command and Approve sends nothing
    expect(stage.locator("video")).to_have_count(0, timeout=3000)
    expect(stage.locator("code")).to_have_text(re.compile(r"^BLENDER camtest .*01-pendulum"))  # the short's path
    p.get_by_role("button", name="Approve").click()
    expect(p.get_by_role("alert")).to_have_text(re.compile(r"^Make the camera test first: BLENDER camtest .*01-pendulum"))
    assert not [d for d in decisions.load(srv.sdir) if d["stage"] == "camera"]
    assert_healthy(p)
    mp4.write_bytes(clip)
    expect(stage.locator("video.player")).to_have_count(1, timeout=3000)
    p.get_by_role("button", name="Approve").click()
    expect(p.get_by_text("Sent · waiting for Claude")).to_be_visible()
    d = decisions.load(srv.sdir)[-1]
    assert (d["stage"], d["decision"], d["by"]) == ("camera", "approve", "page")


def test_camera_stage_stale_clip_says_why_and_can_still_be_approved(view, live_server):
    from bsa import camtest

    srv = live_server("camera", one_take=True)
    rec_path = srv.sdir / camtest.FOLDER / camtest.RECORD
    rec = json.loads(rec_path.read_text("utf-8"))
    rec["spans"] = {k: [v[0], v[1] + 1] for k, v in rec["spans"].items()}  # made on other shot windows
    rec_path.write_text(json.dumps(rec), "utf-8")
    p = open_short(view, srv)
    notice = p.locator("#bsa-stage .problem")
    expect(notice).to_contain_text("the shot windows changed since the camera test")
    expect(notice.locator("code")).to_have_text(re.compile(r"^BLENDER camtest .*01-pendulum"))
    expect(p.locator("#bsa-stage video.player")).to_have_count(1)
    assert_healthy(p)
    p.emulate_media(color_scheme="dark")
    expect(notice).to_be_visible()
    assert_healthy(p)
    p.get_by_role("button", name="Approve").click()
    expect(p.get_by_text("Sent · waiting for Claude")).to_be_visible()
    assert decisions.load(srv.sdir)[-1]["stage"] == "camera"


def test_camera_stage_shows_the_servers_refusal(view, live_server):
    """The clip went between the page's last look and the click: the server refuses (409) and the page shows its
    line, never "Sent"."""
    srv = live_server("camera", one_take=True)
    p = open_short(view, srv)
    expect(p.locator("#bsa-stage video.player")).to_have_count(1)
    line = "The camera has no test clip yet: run BLENDER camtest 01-pendulum first."
    p.route("**/api/*/decision", lambda route: route.fulfill(status=409, content_type="application/json",
                                                             body=json.dumps({"error": line})))  # fmt: skip
    p.get_by_role("button", name="Approve").click()
    expect(p.get_by_role("alert")).to_contain_text("no test clip yet")
    expect(p.get_by_text("Sent · waiting for Claude")).to_have_count(0)
    assert not [d for d in decisions.load(srv.sdir) if d["stage"] == "camera"]
    # (no assert_healthy: the console's 409 line is the refusal this test sends)


def test_plain_short_has_no_camera_stage(view, live_server):
    srv = live_server("storyboard")
    p = open_short(view, srv)
    nav = p.get_by_role("navigation", name="Stages")
    expect(nav.get_by_role("button", name=re.compile("Storyboard"))).to_be_visible()
    expect(nav.get_by_role("button", name=re.compile("Camera"))).to_have_count(0)
