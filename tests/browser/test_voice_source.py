"""The voice check says where each line's audio came from, and shows what the user's own voice tool said."""

import json

import pagefix
import pytest
from playwright.sync_api import expect

from browser.conftest import assert_healthy, open_short

pytestmark = pytest.mark.browser

REASON = "no pause between l02 and l03"
NOTE = "[x] warning: fell back to line-by-line"


def test_voice_check_shows_each_lines_source_and_the_tool_notes(view, live_server):
    srv = live_server("narration")
    pagefix.write_heard(srv.sdir, {"l01": "a pendulum swings like clockwork", "l02": "length sets the beat",
                                   "l03": "a four metre pendulum swings half as fast as a one metre one"})  # fmt: skip
    vo = srv.sdir / "audio" / "vo"
    (vo / "narrator.json").write_text(json.dumps({
        "sources": {"l01": "take", "l02": "line", "l03": "fallback"}, "reasons": {"l03": REASON}}), encoding="utf-8")
    (vo / "narrator.log").write_text(f"=== run ===\nall fine\n{NOTE}\n", encoding="utf-8")
    p = open_short(view, srv, stage="narration")
    expect(p.locator("tr[data-line='l01']").get_by_text("From a paragraph take")).to_be_visible()
    expect(p.locator("tr[data-line='l02']").get_by_text("Spoken on its own", exact=True)).to_be_visible()
    expect(p.locator("tr[data-line='l03']").get_by_text(f"Spoken on its own: {REASON}")).to_be_visible()
    expect(p.locator("tr[data-line='l04'] .src-chip")).to_have_count(0)  # no record: no chip
    box = p.locator("section.voice-notes")
    expect(box.get_by_role("heading", name="Your voice tool said")).to_be_visible()
    expect(box.locator("li")).to_have_text([NOTE])
    expect(box.locator("code")).to_have_text("audio/vo/narrator.log")
    assert_healthy(p)


def test_no_notes_section_without_a_log(view, live_server):
    srv = live_server("narration")
    pagefix.write_heard(srv.sdir, {"l02": "length sets the beat"})
    p = open_short(view, srv, stage="narration")
    expect(p.locator("tr[data-line='l02']")).to_be_visible()
    expect(p.get_by_text("Your voice tool said")).to_have_count(0)
    assert_healthy(p)
