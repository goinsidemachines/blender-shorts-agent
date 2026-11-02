"""The page's extras in a real browser: the inline QR encoder against segno, the footer, the health banner, the
notifications and the phone link with its QR code (only with phone access on)."""

import json
import time

import pagefix
import pytest
import segno
from playwright.sync_api import expect

from browser.conftest import assert_healthy, open_short
from bsa import feedback, jobstate, render_status
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


V9 = "http://192.168.1.20:51234/s/09-long?t=" + "a1B2c3D4e5" * 13  # 169 bytes: version 9, two block groups
V10 = "http://192.168.1.20:51234/s/10-long?t=" + "Z9y8X7w6v5" * 16  # 199 bytes: version 10, 16-bit count
QR_TEXTS = {
    "url": ("http://192.168.1.20:51234/s/03-watch?t=abc", None),
    "hello": ("HELLO", None),  # ends on a codeword boundary: no padding bits
    "latin1": ("é" * 40, None),  # ISO-8859-1, also on a boundary
    "utf8": ("€ and a longer UTF-8 link " * 4, None),
    "v9": (V9, 9),
    "v10": (V10, 10),
}


@pytest.fixture
def iso_segno(monkeypatch):
    """segno, with its padding bits as ISO/IEC 18004 7.4.10 says: only to reach a codeword boundary. segno pads
    `8 - len % 8`, a whole 0x00 codeword when the stream is already on one, where the standard puts the 0xEC pad."""
    from segno import consts, encoder

    def iso_padding_bits(buff, version, length):
        if version not in (consts.VERSION_M1, consts.VERSION_M3) and length % 8:
            buff.extend([0] * (8 - length % 8))

    monkeypatch.setattr(encoder, "write_padding_bits", iso_padding_bits)
    return segno


@pytest.mark.parametrize("name", list(QR_TEXTS))
def test_qr_matches_reference_for_every_mask(view, live_server, iso_segno, name):
    text, version = QR_TEXTS[name]
    p = open_short(view, live_server("story"))
    got = p.evaluate("(t) => [0, 1, 2, 3, 4, 5, 6, 7].map((m) => BSA.qr(t, {mask: m}).modules)", text)
    for mask in range(8):
        ref = iso_segno.make(text, error="m", mask=mask, boost_error=False, mode="byte", micro=False)
        if version is not None:
            assert ref.version == version
        assert got[mask] == [[bool(v) for v in row] for row in ref.matrix], (name, mask)


def test_qr_padding_is_iso_not_segnos_extra_codeword(view, live_server):
    p = open_short(view, live_server("story"))
    got = p.evaluate("() => BSA.qr('HELLO', {mask: 0}).modules")
    plain = segno.make("HELLO", error="m", mask=0, boost_error=False, mode="byte", micro=False)
    assert got != [[bool(v) for v in row] for row in plain.matrix]  # unpatched segno adds a 0x00 codeword


def test_qr_picks_one_of_its_masks_and_refuses_too_long(view, live_server):
    p = open_short(view, live_server("story"))
    for text, _v in QR_TEXTS.values():
        got = p.evaluate("""(t) => {
          const auto = BSA.qr(t);
          const same = [0, 1, 2, 3, 4, 5, 6, 7].filter((m) =>
            JSON.stringify(BSA.qr(t, {mask: m}).modules) === JSON.stringify(auto.modules));
          return {size: auto.size, rows: auto.modules.length, same};
        }""", text)  # fmt: skip
        assert got["size"] == got["rows"] and got["size"] in range(21, 58, 4) and len(got["same"]) == 1
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


def test_notification_when_a_render_goes_stale(view, live_server):
    view.add_init_script(NOTIFY_STUB)
    srv = live_server("render")  # the preview is running, written by this (living) process
    p = open_short(view, srv, stage="render")
    p.wait_for_timeout(300)
    path = render_status.status_path(srv.sdir, "preview")
    status = json.loads(path.read_text("utf-8"))
    status["pid_token"] = status["render"]["pid_token"] = "a-process-that-is-gone"
    path.write_text(json.dumps(status), "utf-8")
    _shown_soon(p)
    shown = p.evaluate("window.__shown")
    assert len(shown) == 1 and "stopped answering" in shown[0]["title"]


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
