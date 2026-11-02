"""The page shell in a real browser: the stage bar, live updates, notes, decisions, the offline view and the theme."""

import json
import re

import pagefix
import pytest
from playwright.sync_api import expect

from browser.conftest import assert_healthy, open_short
from bsa.review import decisions, launch, notes

pytestmark = pytest.mark.browser


def _stage_button(p, name):
    return p.get_by_role("navigation", name="Stages").get_by_role("button", name=name, exact=True)


def _rewrite_title(sdir, title):
    path = sdir / "source" / "short.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["title"] = title
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _clipboard_is(p, want):
    """The clipboard holds `want` (the copy finishes just after the click)."""
    got = None
    for _ in range(40):
        got = p.evaluate("navigator.clipboard.readText()").replace("\r\n", "\n")  # Windows clipboards use CRLF
        if got == want:
            return True
        p.wait_for_timeout(50)
    raise AssertionError(f"clipboard holds {got!r}, not {want!r}")


def _damage_decisions(sdir):
    (sdir / "review" / "decisions.json").write_text("{not json", encoding="utf-8")


def test_stage_bar_states(view, live_server):
    srv = live_server("storyboard")
    p = open_short(view, srv)
    expect(p.get_by_role("navigation", name="Stages").locator("ol > li > button")).to_have_count(8)
    expect(_stage_button(p, "Storyboard")).to_have_attribute("aria-current", "step")
    expect(_stage_button(p, "Render")).to_be_disabled()
    expect(_stage_button(p, "Interview")).to_be_enabled()
    expect(p.get_by_role("heading", level=2, name="Storyboard")).to_be_visible()  # the current stage by default
    _stage_button(p, "Interview").click()
    expect(p.get_by_role("heading", name="Interview")).to_be_visible()
    expect(p.get_by_text("simple pendulum", exact=True)).to_be_visible()  # the interview answers
    assert_healthy(p)


def test_hash_opens_a_stage_and_locked_hash_falls_back(view, live_server):
    srv = live_server("story")
    p = open_short(view, srv, stage="interview")
    expect(p.get_by_role("heading", level=2, name="Interview")).to_be_visible()
    open_short(view, srv, stage="final")  # locked: the current stage shows instead
    p.reload()
    expect(p.get_by_role("heading", level=2, name="Story")).to_be_visible()


def test_live_update_within_3s(view, live_server):
    srv = live_server("story")
    p = open_short(view, srv)
    expect(p).to_have_title(re.compile(r"^\(1\) Needs your yes · "))
    _rewrite_title(srv.sdir, "Why a lever lifts more")
    expect(p.get_by_role("heading", level=1)).to_have_text("Why a lever lifts more", timeout=3000)
    expect(p).to_have_title(re.compile(r"^\(1\) Needs your yes · Why a lever lifts more$"), timeout=3000)
    expect(p.locator("[aria-live=polite]")).to_have_text("Updated: Interview version 2", timeout=3000)


def test_title_names_the_stage_when_nothing_needs_a_yes(view, live_server):
    srv = live_server("narration")
    p = open_short(view, srv)
    expect(p).to_have_title(re.compile(r"^Narration · "))


def test_show_hint_switches_stage(view, live_server):
    srv = live_server("storyboard")
    p = open_short(view, srv)
    with srv.srv.lock:
        srv.srv.hints[srv.key] = "interview"
    expect(p.get_by_role("heading", level=2, name="Interview")).to_be_visible(timeout=3000)


def test_hello_is_posted_on_load(view, live_server):
    srv = live_server("story")
    open_short(view, srv)
    assert srv.key in srv.srv.hello_at


def test_overall_note_writes_notes_json(view, live_server):
    srv = live_server("story")
    p = open_short(view, srv, stage="interview")
    p.get_by_role("button", name="Add a note on Interview").click()
    p.get_by_role("textbox").fill("Audience is teens")
    p.get_by_role("button", name="Save note").click()
    expect(p.get_by_text("Not sent", exact=True)).to_be_visible()
    p.get_by_role("button", name="Send").click()
    expect(p.get_by_text("Sent", exact=True)).to_be_visible()
    n = notes.load(srv.sdir)[-1]
    assert n["target"] == {"kind": "overall", "id": None, "version": 1} and n["status"] == "sent" and n["by"] == "page"
    notes.answer(srv.sdir, n["id"], "done", "Made it for teens.")
    expect(p.get_by_text("Made it for teens.")).to_be_visible(timeout=3000)
    assert_healthy(p)


def test_hostile_note_text_is_shown_as_text(view, live_server):  # Review Focus 5
    srv = live_server("story")
    text = "<img src=x onerror=window.pwned=1></script> 🙂 שלום " + "y" * 3900  # the longest a note can be is 4000
    notes.add(srv.sdir, "interview", {"kind": "overall", "id": None, "version": 1}, text, "chat")
    p = open_short(view, srv, stage="interview")
    expect(p.get_by_text("🙂 שלום", exact=False)).to_be_visible()
    assert p.evaluate("window.pwned === undefined") and p.locator("img[src='x']").count() == 0
    p.get_by_role("button", name="Add a note on Interview").click()
    p.get_by_role("textbox").fill("z" * 4001)
    p.get_by_role("button", name="Save note").click()
    expect(p.get_by_role("alert")).to_contain_text("4000")
    assert_healthy(p)


def test_editor_text_survives_a_live_update(view, live_server):
    srv = live_server("story")
    p = open_short(view, srv, stage="interview")
    p.get_by_role("button", name="Add a note on Interview").click()
    p.get_by_role("textbox").fill("half a thought")
    _rewrite_title(srv.sdir, "A new title")
    expect(p.get_by_role("heading", level=1)).to_have_text("A new title", timeout=3000)
    expect(p.get_by_role("textbox")).to_have_value("half a thought")


def test_approve_sends_and_waits_for_claude(view, live_server):
    srv = live_server("story")
    p = open_short(view, srv)
    expect(p.get_by_role("button", name="Send changes")).to_be_visible()
    p.get_by_role("button", name="Approve", exact=True).click()
    expect(p.get_by_text("Sent · waiting for Claude")).to_be_visible()
    expect(p.get_by_role("button", name="Approve", exact=True)).to_have_count(0)
    d = decisions.load(srv.sdir)[-1]
    assert (d["stage"], d["version"], d["decision"], d["by"], d["applied"]) == ("story", 1, "approve", "page", False)
    expect(p.get_by_text("Approved", exact=True)).to_have_count(0)
    decisions.mark_applied(srv.sdir, d["id"])
    expect(p.get_by_text("Approved", exact=True)).to_be_visible(timeout=3000)
    assert_healthy(p)


def test_send_changes_posts_the_decision_with_open_notes(view, live_server):
    srv = live_server("story")
    p = open_short(view, srv)
    p.get_by_role("button", name="Send changes").click()  # no note yet: the server's 400 is shown
    expect(p.get_by_role("alert")).to_contain_text("Add a note")
    p.get_by_role("button", name="Add a note on Story").click()
    p.get_by_role("textbox").fill("Shorter hook")
    p.get_by_role("button", name="Save note").click()
    expect(p.get_by_text("Not sent", exact=True)).to_be_visible()
    expect(p.get_by_role("button", name="Send", exact=True)).to_have_count(0)  # approval stage: no per-note send
    p.get_by_role("button", name="Send changes").click()
    expect(p.get_by_text("Sent", exact=True)).to_be_visible()
    assert decisions.load(srv.sdir)[-1]["decision"] == "changes" and notes.load(srv.sdir)[-1]["status"] == "sent"


def test_stale_decision_shows_the_409(view, live_server):
    srv = live_server("story")
    p = open_short(view, srv)
    decisions.record(srv.sdir, "story", 1, "approve", "chat", current=1)
    p.get_by_role("button", name="Approve", exact=True).click()
    expect(p.get_by_role("alert")).to_be_visible()


def test_damaged_decisions_offer_no_approve(view, live_server):
    srv = live_server("story")
    _damage_decisions(srv.sdir)
    p = open_short(view, srv)
    expect(p.get_by_text("decisions.json is damaged").first).to_be_visible()
    expect(p.get_by_role("button", name="Approve", exact=True)).to_have_count(0)


def test_unreachable_server_says_so_and_pauses_buttons(view, live_server):
    srv = live_server("story")
    p = open_short(view, srv, stage="interview")
    srv.shutdown()
    expect(p.get_by_role("alert")).to_contain_text("Can't reach the page server", timeout=8000)
    expect(p.get_by_role("button", name="Add a note on Interview")).to_be_disabled()


def test_offline_static_copy_buttons(view, tmp_path, bsa_home):
    sdir = pagefix.make_short(tmp_path / "ws", "story")
    f = launch.static_page(sdir)
    view.context.grant_permissions(["clipboard-read", "clipboard-write"])
    view.goto(f.as_uri())
    expect(view.get_by_text("Offline view: buttons copy your answer for the chat")).to_be_visible()
    view.get_by_role("button", name="Copy: Approve").click()
    assert _clipboard_is(view, "Approve Story (version 1)")
    view.get_by_role("button", name="Add a note on Story").click()
    view.get_by_role("textbox").fill("Shorter hook")
    view.get_by_role("button", name="Copy: Note").click()
    assert _clipboard_is(view, "Note on Story overall (version 1): Shorter hook")
    view.get_by_role("button", name="Copy: Send changes").click()
    assert _clipboard_is(view, "Changes to Story (version 1):\n- overall: Shorter hook")
    assert_healthy(view)


def test_answer_text_rules(view, live_server):
    srv = live_server("story")
    p = open_short(view, srv)
    got = p.evaluate("""() => [
        BSA.answerText("approve", {label: "Storyboard", version: 2}),
        BSA.answerText("changes", {label: "Story", version: 1, notes: [
            {target: {kind: "line", id: "l02"}, text: "Say beat"}, {target: {kind: "overall", id: null}, text: "Faster"}]}),
        BSA.answerText("note", {label: "Story", version: 3, note: {target: {kind: "shot", id: "s01"}, text: "Closer"}}),
    ]""")
    assert got == [
        "Approve Storyboard (version 2)",
        "Changes to Story (version 1):\n- line l02: Say beat\n- overall: Faster",
        "Note on Story shot s01 (version 3): Closer",
    ]


def test_dark_mode_and_toggle_persists(view, live_server):
    srv = live_server("story")
    view.emulate_media(color_scheme="dark")
    p = open_short(view, srv)
    bg = "getComputedStyle(document.body).backgroundColor"
    assert p.evaluate(bg) == "rgb(30, 26, 21)"
    assert_healthy(p)
    p.get_by_role("button", name="Dark theme").click()
    assert p.evaluate(bg) == "rgb(246, 241, 231)"
    p.reload()
    p.locator("html[data-ready='1']").wait_for(state="attached")
    assert p.evaluate(bg) == "rgb(246, 241, 231)"
    assert p.evaluate("localStorage.getItem('bsa-theme')") == "light"
    assert_healthy(p)


def test_poll_delay_hidden_tab(view, live_server):
    srv = live_server("story")
    p = open_short(view, srv)
    assert p.evaluate("BSA.pollDelay()") == 2000
    p.evaluate("Object.defineProperty(document, 'visibilityState', {configurable: true, get: () => 'hidden'})")
    assert p.evaluate("BSA.pollDelay()") == 15000


def test_keyboard_reaches_stage_bar_with_visible_focus(view, live_server):
    srv = live_server("story")
    p = open_short(view, srv)
    for _ in range(10):
        p.keyboard.press("Tab")
        if p.evaluate("!!document.activeElement.closest('nav')"):
            break
    else:
        raise AssertionError("Tab never reached the stage bar")
    style = p.evaluate("(() => { const s = getComputedStyle(document.activeElement);"
                       " return [s.outlineStyle, s.outlineWidth]; })()")  # fmt: skip
    assert style == ["solid", "3px"]
