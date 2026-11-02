"""The page's extras in a real browser: the inline QR encoder against segno, the footer, the health banner, the
notifications and the phone link with its QR code (only with phone access on)."""

import json
import time

import pagefix
import pytest
import segno
from playwright.sync_api import expect

from browser.conftest import assert_healthy, open_short
from bsa import feedback, jobstate
from bsa.review import launch

pytestmark = pytest.mark.browser

LAN = "192.168.1.20"

# A stand-in for the browser's Notification: it records what was shown and the permission requests.
NOTIFY_STUB = """
window.__shown = [];
window.__asked = 0;
window.Notification = class {
  constructor(title, opts) { window.__shown.push({ title, body: (opts || {}).body || "" }); }
  static get permission() { return "granted"; }
  static requestPermission() { window.__asked += 1; return Promise.resolve("granted"); }
};
"""


@pytest.mark.parametrize("text", ["http://192.168.1.20:51234/s/03-watch?t=abc", "HELLO", "é" * 40,
                                  "€ and a longer UTF-8 link " * 4])  # fmt: skip
@pytest.mark.parametrize("mask", [0, 3, 7])
def test_qr_matches_reference(view, live_server, text, mask):
    p = open_short(view, live_server("story"))
    got = p.evaluate("([t, m]) => BSA.qr(t, {mask: m}).modules", [text, mask])
    ref = segno.make(text, error="m", mask=mask, boost_error=False, mode="byte", micro=False)
    assert got == [[bool(v) for v in row] for row in ref.matrix]


def test_qr_picks_a_mask_and_refuses_too_long(view, live_server):
    p = open_short(view, live_server("story"))
    got = p.evaluate("(t) => BSA.qr(t)", "http://192.168.1.20:51234/s/03-watch?t=abc")
    assert got["size"] == len(got["modules"]) and got["size"] in range(21, 58, 4)
    err = p.evaluate("() => { try { BSA.qr('x'.repeat(300)); return null; } catch (e) { return e.message; } }")
    assert err and "too long" in err


def test_footer_text_and_links(view, live_server):
    p = open_short(view, live_server("story"))
    foot = p.locator("footer")
    assert " ".join(foot.inner_text().split()) == (
        "Made with blender-shorts-agent by Inside Machines · See what others made · Report a problem"
    )
    links = foot.get_by_role("link")
    assert [links.nth(i).get_attribute("href") for i in range(links.count())] == [
        feedback.REPO,
        "https://goinsidemachines.com",
        "https://goinsidemachines.com/made-with/",
        feedback.REPO + "/issues/new?template=bug_report.yml",
    ]
    for i in range(links.count()):
        assert links.nth(i).get_attribute("target") == "_blank"
        assert links.nth(i).get_attribute("rel") == "noopener noreferrer"
    assert_healthy(p)


def _shown_soon(p, timeout=4.0):
    """Wait for a notification (the page's CSP forbids wait_for_function's eval)."""
    end = time.monotonic() + timeout
    while time.monotonic() < end and not p.evaluate("() => window.__shown.length"):
        p.wait_for_timeout(100)


def _doctor(sdir, checks):
    (sdir / "review" / "doctor.json").write_text(json.dumps({"at": 1, "checks": checks}), "utf-8")


def test_health_banner(view, live_server):
    srv = live_server("story")
    _doctor(srv.sdir, [
        {"id": "blender", "level": "ok", "text": "Blender 5.2", "fix": None},
        {"id": "disk", "level": "warn", "text": "Only 3 GB free.", "fix": "Free some space."},
    ])  # fmt: skip
    p = open_short(view, srv)
    banner = p.locator("#bsa-banners").get_by_role("status")
    expect(banner).to_contain_text("Only 3 GB free.")
    expect(banner).to_contain_text("Free some space.")
    expect(p.locator("#bsa-banners")).not_to_contain_text("Blender 5.2")
    _doctor(srv.sdir, [{"id": "blender", "level": "error", "text": "Blender was not found.", "fix": "Install it."}])
    expect(p.locator("#bsa-banners").get_by_role("alert")).to_contain_text("Blender was not found.", timeout=3000)
    expect(p.locator("#bsa-banners")).not_to_contain_text("Only 3 GB free.")
    assert_healthy(p)


def test_notification_when_a_yes_is_needed(view, live_server):
    view.add_init_script(NOTIFY_STUB)
    srv = live_server("interview")
    p = open_short(view, srv)
    p.locator("#bsa-stage").click()
    assert p.evaluate("window.__asked") == 1
    p.locator("#bsa-stage").click()
    assert p.evaluate("window.__asked") == 1  # asked once
    jobstate.mark_done(srv.sdir, "interview")
    _shown_soon(p)
    shown = p.evaluate("window.__shown")
    assert len(shown) == 1 and "Story" in shown[0]["title"] + shown[0]["body"]


def test_notification_when_a_render_ends(view, live_server):
    view.add_init_script(NOTIFY_STUB)
    srv = live_server("render")
    p = open_short(view, srv, stage="render")
    p.wait_for_timeout(300)
    pagefix.write_render_status(srv.sdir, "preview", state="failed", done=40)
    _shown_soon(p)
    shown = p.evaluate("window.__shown")
    assert len(shown) == 1 and "failed" in (shown[0]["title"] + shown[0]["body"]).lower()


def test_no_notification_on_first_load(view, live_server):
    view.add_init_script(NOTIFY_STUB)
    srv = live_server("story")  # the story needs a yes from the start: nothing changed, nothing to say
    p = open_short(view, srv)
    p.wait_for_timeout(2500)  # a poll or two later
    assert p.evaluate("window.__shown") == []


def test_no_phone_link_without_lan(view, live_server):
    p = open_short(view, live_server("story"))
    p.wait_for_timeout(300)
    expect(p.get_by_role("img", name="QR code for the phone link")).to_have_count(0)
    expect(p.get_by_text("Open on your phone")).to_have_count(0)


def test_phone_link_and_qr_only_with_lan(view, live_server, monkeypatch):
    monkeypatch.setattr(launch, "lan_ip", lambda: LAN)
    srv = live_server("story", lan=True)
    p = open_short(view, srv)
    p.get_by_text("Open on your phone").click()
    qr = p.get_by_role("img", name="QR code for the phone link")
    expect(qr).to_be_visible()
    url = f"http://{LAN}:{srv.srv.port}/s/{srv.key}?t={srv.srv.token}"
    expect(p.locator("#bsa-phone a")).to_have_attribute("href", url)
    assert_healthy(p)
