"""The Interview stage's "Started from" block in a real browser: what was copied from the other short, a note
button for each item."""

import pagefix
import pytest
from playwright.sync_api import expect

from browser.conftest import assert_healthy, open_short

pytestmark = pytest.mark.browser

KEYS = ["style", "look", "length_s", "pace", "closing", "narrator"]


def test_interview_shows_started_from_with_a_note_button_per_item(view, live_server):
    srv = live_server("interview")
    pagefix.write_like(srv.sdir, "Eiffel Tower")
    p = open_short(view, srv, stage="interview")
    stage = p.locator("#bsa-stage")
    expect(stage.get_by_role("heading", name="Started from: Eiffel Tower")).to_be_visible()
    expect(stage.get_by_text("These came from that short. Change any with a note.")).to_be_visible()
    items = stage.locator("ul.copied > li")
    expect(items).to_have_count(len(KEYS))
    expect(items.filter(has_text="the look")).to_contain_text("toy style, workbench set, own palette (accent)")
    expect(items.filter(has_text="the pace")).to_contain_text("8 shots, about 4 s each, 2 holds")
    expect(stage.get_by_role("button", name="Change the voice")).to_be_visible()
    stage.get_by_role("button", name="Change the look").click()
    expect(p.get_by_role("textbox").first).to_be_visible()
    expect(p.get_by_role("button", name="Save note")).to_be_visible()
    assert_healthy(p)


def test_interview_without_like_has_no_started_from(view, live_server):
    srv = live_server("interview")
    p = open_short(view, srv, stage="interview")
    expect(p.locator("ul.copied")).to_have_count(0)
    assert_healthy(p)
