"""The look preview on the Storyboard stage in a real browser: its frames, kit sheet and colours, the speed as
information only, the notes asking for another style or set, and the look in the before-and-after view."""

import re

import kitfix
import pagefix
import pytest
from playwright.sync_api import expect

from browser.conftest import assert_healthy, open_short
from bsa import settings as settings_mod
from bsa.review import launch, notes, snapshot

pytestmark = pytest.mark.browser


@pytest.fixture
def board(live_server, bsa_home):
    """A live server on a short at Storyboard whose user has the made-up kit, with a look preview (toy, built toon)."""
    settings_mod.save_settings({"brand": kitfix.KIT})
    srv = live_server("storyboard")
    pagefix.write_look(srv.sdir)
    return srv


def test_look_section_renders_at_375_and_desktop_with_no_axe_violations(view, board):
    p = open_short(view, board, stage="storyboard")
    stage = p.locator("#bsa-stage")
    look = stage.locator("section.st-sec").filter(has=p.get_by_role("heading", name="Look", exact=True))
    expect(look).to_have_count(1)
    heads = stage.locator("section.st-sec > h3").all_inner_texts()
    assert heads.index("Timing") + 1 == heads.index("Look") == heads.index("Shots") - 1  # between Timing and Shots
    expect(look.get_by_role("button", name=re.compile("^Enlarge the look in shot"))).to_have_count(3)
    expect(look.get_by_role("img", name="Your kit: colours, font and card shapes")).to_be_visible()
    expect(look.locator(".swatch")).to_have_count(9)
    expect(look.locator(".swatch").filter(has_text="spark")).to_contain_text("#D9483B")
    expect(look.get_by_text("Style: toy, set: workbench")).to_be_visible()
    expect(look.get_by_text("Built as toon, set: workbench.")).to_be_visible()
    expect(look.get_by_text("About 2.5 s a frame in EEVEE at preview quality.")).to_be_visible()
    expect(look.get_by_text("The final render: about 5 min in EEVEE, about 20 min in Cycles.")).to_be_visible()
    expect(look.get_by_text("These are estimates only; the engine stays your choice.")).to_be_visible()
    expect(look.get_by_text("These choices were saved for every short.")).to_be_visible()
    expect(look.get_by_role("button", name=re.compile("^Try "))).to_have_count(6 + 4)  # other styles, other sets
    expect(look.get_by_role("button", name="Try no set")).to_be_visible()
    expect(look.get_by_role("button", name=re.compile("cycles|eevee|engine", re.IGNORECASE))).to_have_count(0)
    look.get_by_role("button", name="Enlarge the look in shot s02").click()
    dialog = p.locator("dialog.zoom")
    expect(dialog).to_be_visible()
    expect(dialog.locator("img")).to_have_attribute("alt", re.compile("The look in shot s02"))
    p.keyboard.press("Escape")
    expect(dialog).to_be_hidden()
    expect(look.get_by_role("button", name="Enlarge the look in shot s02")).to_be_focused()
    assert_healthy(p)
    p.emulate_media(color_scheme="dark")
    expect(look.get_by_text("These choices were saved for every short.")).to_be_visible()
    assert_healthy(p)


def test_try_another_style_leaves_a_look_note(view, board):
    p = open_short(view, board, stage="storyboard")
    p.get_by_role("button", name="Try pencil").click()
    box = p.get_by_role("textbox")
    expect(box).to_have_value("Use the pencil style")
    p.get_by_role("button", name="Save note").click()
    expect(p.get_by_text("Not sent", exact=True)).to_be_visible()
    n = notes.load(board.sdir)[-1]
    assert n["stage"] == "storyboard" and n["target"] == {"kind": "look", "id": "style", "version": 1}
    assert n["text"] == "Use the pencil style" and n["status"] == "open"
    expect(p.get_by_role("button", name="Try pencil")).to_be_focused()  # the button that opened it, not the first
    p.get_by_role("button", name="Try graph").click()
    expect(p.get_by_role("textbox")).to_have_value("Use the graph set")
    p.get_by_role("button", name="Save note").click()
    expect(p.get_by_text("Not sent", exact=True)).to_have_count(2)
    assert notes.load(board.sdir)[-1]["target"] == {"kind": "look", "id": "set", "version": 1}
    assert_healthy(p)


def test_compare_view_shows_look_before_and_after(view, board):
    p = open_short(view, board, stage="storyboard")  # version 1 is shown: toy on the workbench
    expect(p.get_by_text("Style: toy, set: workbench")).to_be_visible()
    pagefix.write_look(board.sdir, style="pencil", set_="graph", first=False)
    button = p.get_by_role("button", name="Compare with version 1")
    expect(button).to_be_visible(timeout=6000)
    expect(p.get_by_text("Style: pencil, set: graph")).to_be_visible()
    expect(p.get_by_text("These choices were saved for every short.")).to_have_count(0)
    button.click()
    view_ = p.locator(".cmp-view")
    expect(view_.get_by_text("Style/set before → now: toy, set: workbench → pencil, set: graph")).to_be_visible()
    pair = view_.locator(".cmp-look[data-look='s01']")
    before = pair.locator("img[alt='The look in shot s01 before (version 1)']")
    now = pair.locator("img[alt='The look in shot s01 now']")
    expect(before).to_have_attribute("src", re.compile("review/history/storyboard/v1/look/s01.jpg"))
    expect(now).to_have_attribute("src", re.compile("stills/look/s01.jpg"))
    if view.viewport_size["width"] >= 768:
        expect(before).to_be_visible()
        expect(now).to_be_visible()
    else:  # one side at a time, the same toggle as the shots
        expect(now).to_be_visible()
        expect(before).to_be_hidden()
        p.get_by_role("button", name="Show before").click()
        expect(before).to_be_visible()
        expect(now).to_be_hidden()
    assert_healthy(p)


def test_a_kit_changed_since_the_look_says_so(view, board):
    pagefix.write_look(board.sdir, swatches=[{"name": "accent", "hex": "#ABCDEF"}])
    p = open_short(view, board, stage="storyboard")
    look = p.locator("section.st-sec").filter(has=p.get_by_role("heading", name="Look", exact=True))
    expect(look.locator(".warns")).to_contain_text(snapshot.OLD_KIT)
    expect(look.locator(".swatch")).to_have_count(1)  # the colours the look was rendered with
    expect(look.locator(".swatch")).to_contain_text("#ABCDEF")
    assert_healthy(p)


def _loaded(img) -> bool:
    return img.evaluate("i => i.complete && i.naturalWidth > 0")


def test_the_offline_page_shows_the_look_and_its_before(view, tmp_path, bsa_home):
    settings_mod.save_settings({"brand": kitfix.KIT})
    sdir = pagefix.make_short(tmp_path / "ws", "storyboard")
    pagefix.write_look(sdir)
    launch.static_page(sdir)  # version 1 shown: its look frames are kept
    pagefix.write_look(sdir, style="pencil", set_="graph")
    p = view
    p.goto(launch.static_page(sdir).as_uri() + "#storyboard")
    p.locator("html[data-ready='1']").wait_for(state="attached")
    look = p.locator("section.st-sec").filter(has=p.get_by_role("heading", name="Look", exact=True))
    frame = look.locator("img.still").first
    expect(frame).to_have_attribute("src", re.compile(r"^\.\./stills/look/s01\.jpg$"))
    frame.scroll_into_view_if_needed()
    p.wait_for_function("[...document.querySelectorAll('.look-frame img')].every(i => i.complete)")
    assert _loaded(frame) and _loaded(look.get_by_role("img", name="Your kit: colours, font and card shapes"))
    p.get_by_role("button", name="Compare with version 1").click()
    before = p.locator(".cmp-look[data-look='s01'] img[alt='The look in shot s01 before (version 1)']")
    expect(before).to_have_attribute("src", re.compile(r"^\.\./review/history/storyboard/v1/look/s01\.jpg$"))
    if view.viewport_size["width"] < 768:
        p.get_by_role("button", name="Show before").click()  # a hidden lazy picture is never loaded
    before.scroll_into_view_if_needed()
    p.wait_for_function("[...document.querySelectorAll('.cmp-look .cmp-before img')].every(i => i.complete)")
    assert _loaded(before)
    assert_healthy(p)
