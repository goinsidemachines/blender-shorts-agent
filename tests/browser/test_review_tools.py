"""The review tools in a real browser: the clickable timeline under the final video and each render run."""

import json
import re
import time

import pagefix
import pytest
from playwright.sync_api import expect

from browser.conftest import assert_healthy, open_short
from bsa.narrate.run import WHISPER_MISSING
from bsa.review import notes, snapshot

pytestmark = pytest.mark.browser

STALE = "This video was cut from an older timing; run the edit again to line up the markers."


def _until(fn, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if fn():
            return True
        time.sleep(0.05)
    return fn()


def _real_video(srv) -> None:
    """Skip unless the final is make_short's counter video (made with ffmpeg)."""
    if not (srv.sdir / "out" / "pendulum.mp4").is_file():
        pytest.skip("ffmpeg is not installed: no counter video to play")


def _stand_in_video(srv) -> None:
    """The final's MP4 for a test that needs the timeline but never plays it: the counter video when ffmpeg made one,
    else a stand-in file (enough for the snapshot to draw the track)."""
    mp4 = srv.sdir / "out" / "pendulum.mp4"
    if not mp4.is_file():
        mp4.write_bytes(bytes(2000))


def _final_version(sdir) -> int:
    return snapshot.stage_version(sdir, "final", snapshot.load_ctx(sdir, {}))


def _watch_seeks(p) -> None:
    p.evaluate("""() => { window.__seeks = 0;
        document.querySelector('#bsa-stage video').addEventListener('seeked', () => { window.__seeks += 1; }); }""")


def _wait_seek(p, n: int) -> None:
    js = "n => window.__seeks >= n && document.querySelector('#bsa-stage video').readyState >= 2"
    assert _until(lambda: p.evaluate(js, n))


def _frame_on_screen(p) -> int:
    """The frame of the counter video on screen: its centre pixel's grey, drawn on a canvas."""
    grey = p.evaluate("""() => {
        const v = document.querySelector('#bsa-stage video');
        const c = document.createElement('canvas');
        c.width = v.videoWidth; c.height = v.videoHeight;
        const g = c.getContext('2d');
        g.drawImage(v, 0, 0);
        return g.getImageData(c.width >> 1, c.height >> 1, 1, 1).data[0];
    }""")
    return pagefix.counter_frame(grey)


def test_marker_click_seeks_to_its_frame(view, live_server):
    srv = live_server("final")
    _real_video(srv)
    p = open_short(view, srv, stage="final")
    video = p.locator("#bsa-stage video")
    assert _until(lambda: video.evaluate("v => v.readyState") >= 1)
    _watch_seeks(p)
    p.get_by_role("button", name="Finding: frame 12 flickers, frame 12").click()
    _wait_seek(p, 1)
    assert abs(_frame_on_screen(p) - 12) <= 1
    slider = p.get_by_role("slider", name="Frame")
    expect(slider).to_have_attribute("aria-valuenow", "12")
    slider.press("ArrowRight")
    _wait_seek(p, 2)
    assert abs(_frame_on_screen(p) - 13) <= 1
    expect(slider).to_have_attribute("aria-valuenow", "13")
    assert_healthy(p)


def test_marker_names_and_seek_target(view, live_server):
    srv = live_server("final")
    _stand_in_video(srv)
    p = open_short(view, srv, stage="final")
    stage = p.locator("#bsa-stage")
    names = ["Shot s02: Length not weight, frames 11 to 20", "Finding: frame 12 flickers, frame 12"]
    if view.viewport_size["width"] >= 768:  # the phone starts with only the shots and findings rows
        names += ["Sound: whoosh, frame 17", "Caption: caption 11, frame 11"]
    for name in names:
        expect(stage.get_by_role("button", name=name, exact=True)).to_have_count(1)
    slider = stage.get_by_role("slider", name="Frame")
    expect(slider).to_have_attribute("aria-valuemin", "1")
    expect(slider).to_have_attribute("aria-valuemax", "30")
    stage.get_by_role("button", name="Finding: frame 12 flickers, frame 12").click()
    expect(slider).to_have_attribute("aria-valuenow", "12")
    assert abs(stage.locator("video").evaluate("v => v.currentTime") - 11.5 / 30) < 1e-3
    assert_healthy(p)


def test_note_at_this_moment_writes_a_frame_note(view, live_server):
    srv = live_server("final")
    _stand_in_video(srv)
    v = _final_version(srv.sdir)
    p = open_short(view, srv, stage="final")
    p.get_by_role("button", name="Finding: frame 12 flickers, frame 12").click()
    p.get_by_role("button", name="Note at this moment").click()
    box = p.get_by_role("textbox", name="Note at frame 12 (shot s02)")
    expect(box).to_be_focused()
    box.fill("Stray dot")
    p.get_by_role("button", name="Save note").click()
    assert _until(lambda: bool(notes.load(srv.sdir)))
    n = notes.load(srv.sdir)[-1]
    assert n["stage"] == "final" and n["text"] == "Stray dot"
    assert n["target"] == {"kind": "frame", "id": "12", "shot": "s02", "version": v}
    expect(p.get_by_text("Not sent", exact=True)).to_be_visible()
    assert_healthy(p)


def test_render_run_frame_note_names_the_run(view, live_server):
    srv = live_server("render")
    p = open_short(view, srv, stage="render")
    panel = p.get_by_role("tabpanel")
    expect(panel.locator("video")).to_have_count(1)
    panel.get_by_role("button", name=re.compile("^Shot s02: ")).click()
    panel.get_by_role("button", name="Note at this moment").click()
    p.get_by_role("textbox").fill("Too dark here")
    p.get_by_role("button", name="Save note").click()
    assert _until(lambda: bool(notes.load(srv.sdir)))
    n = notes.load(srv.sdir)[-1]
    assert n["stage"] == "render" and n["target"]["kind"] == "frame"
    assert n["target"]["id"] == "preview:11" and n["target"]["shot"] == "s02"
    assert_healthy(p)


def test_stale_timeline_hides_markers(view, live_server):
    srv = live_server("final")
    _stand_in_video(srv)
    pagefix.record_edit(srv.sdir, "final", stale=True)
    p = open_short(view, srv, stage="final")
    stage = p.locator("#bsa-stage")
    expect(stage.get_by_text(STALE)).to_be_visible()
    expect(stage.get_by_role("button", name=re.compile("^Finding: "))).to_have_count(0)
    expect(stage.get_by_role("button", name=re.compile("^Shot "))).to_have_count(0)
    pagefix.record_edit(srv.sdir, "final")  # the edit again: the markers come back on the next update
    expect(stage.get_by_role("button", name="Finding: frame 12 flickers, frame 12")).to_be_visible(timeout=4000)
    expect(stage.get_by_text(STALE)).to_have_count(0)
    assert_healthy(p)


def test_unplaced_findings_are_listed(view, live_server):
    srv = live_server("final")
    _stand_in_video(srv)
    p = open_short(view, srv, stage="final")
    box = p.get_by_role("region", name="Findings without a place on the timeline")
    expect(box.get_by_text("Bob is dark")).to_be_visible()
    expect(box.get_by_text("no frame")).to_be_visible()
    box.get_by_role("button", name=re.compile("^\\+ Note on finding")).click()
    p.get_by_role("textbox").fill("Light it")
    p.get_by_role("button", name="Save note").click()
    assert _until(lambda: bool(notes.load(srv.sdir)))
    assert notes.load(srv.sdir)[-1]["target"]["kind"] == "finding"
    assert_healthy(p)


def test_phone_shows_shots_and_findings_rows_first(view, live_server):
    srv = live_server("final")
    _stand_in_video(srv)
    p = open_short(view, srv, stage="final")
    stage = p.locator("#bsa-stage")
    phone = view.viewport_size["width"] < 768
    for row in ("Shots", "Findings", "Captions", "Sounds", "Holds", "Closing card"):
        on = "true" if row in ("Shots", "Findings") or not phone else "false"
        expect(stage.get_by_role("button", name=row, exact=True)).to_have_attribute("aria-pressed", on)
    captions = stage.get_by_role("button", name="Captions", exact=True)
    caption = stage.get_by_role("button", name=re.compile("^Caption: "))
    if phone:
        expect(caption).to_have_count(0)
        captions.click()
        expect(captions).to_have_attribute("aria-pressed", "true")
        expect(caption.first).to_be_visible()
        notes.add(srv.sdir, "final", {"kind": "overall", "id": None, "version": 1}, "x", "chat")  # a live update
        expect(p.get_by_text("Not sent", exact=True)).to_be_visible(timeout=4000)
        expect(captions).to_have_attribute("aria-pressed", "true")  # the choice is kept across updates
    else:
        expect(caption.first).to_be_visible()
    assert_healthy(p)


def test_marker_row_is_one_tab_stop_with_arrow_keys(view, live_server):
    srv = live_server("final")
    _stand_in_video(srv)
    p = open_short(view, srv, stage="final")
    shots = p.locator("#bsa-stage").get_by_role("button", name=re.compile("^Shot s0"))
    expect(shots).to_have_count(3)
    assert [shots.nth(i).get_attribute("tabindex") for i in range(3)] == ["0", "-1", "-1"]
    shots.first.focus()
    p.keyboard.press("ArrowRight")
    expect(shots.nth(1)).to_be_focused()
    p.keyboard.press("Enter")
    expect(p.get_by_role("slider", name="Frame")).to_have_attribute("aria-valuenow", "11")
    assert shots.nth(1).get_attribute("tabindex") == "0"
    assert_healthy(p)


def test_long_timeline_renders_under_50ms_per_poll(view, live_server):
    srv = live_server("final")
    _stand_in_video(srv)
    pagefix.write_timeline(srv.sdir, frames=2000)
    rows = [{"check": "flicker", "severity": "warning", "message": f"spot {i}", "frame": 1 + i * 10, "shots": ["s01"]}
            for i in range(200)]  # fmt: skip
    (srv.sdir / "review" / "frame-checks-final.json").write_text(json.dumps({"findings": rows}), encoding="utf-8")
    pagefix.record_edit(srv.sdir, "final")
    p = open_short(view, srv, stage="final")
    expect(p.get_by_role("button", name="Finding: spot 199, frame 1991")).to_have_count(1)
    p.evaluate("document.querySelector('.tl-track').dataset.mark = 'kept'")
    for i in range(3):  # three live updates that leave the timeline as it was
        notes.add(srv.sdir, "final", {"kind": "overall", "id": None, "version": 1}, f"poll {i}", "chat")
        expect(p.get_by_text(f"poll {i}", exact=True)).to_be_visible(timeout=4000)
    assert p.evaluate("document.querySelector('.tl-track').dataset.mark") == "kept"  # the track was reused, not rebuilt
    assert p.evaluate("BSA.renderMs") < 50
    assert_healthy(p)


def test_voice_table_shows_the_diff_and_retake_note(view, live_server):
    srv = live_server("render")
    pagefix.write_heard(srv.sdir, {"l02": "length set the beat"})
    p = open_short(view, srv, stage="narration")
    row = p.locator("tr[data-line='l02']")
    expect(row.locator("mark", has_text="set")).to_be_visible()
    expect(row.get_by_text("Didn't pass the check")).to_be_visible()
    p.get_by_role("button", name="Re-take: l02").click()
    box = p.get_by_role("textbox")
    expect(box).to_have_value("Re-take this line")
    p.get_by_role("button", name="Save note").click()
    assert _until(lambda: bool(notes.load(srv.sdir)))
    n = notes.load(srv.sdir)[-1]
    assert n["target"]["id"] == "l02" and n["text"] == "Re-take this line"
    assert_healthy(p)


def test_voice_table_says_why_it_was_not_checked(view, live_server):
    srv = live_server("render")
    pagefix.write_heard(srv.sdir, {}, checked=False, why_not=WHISPER_MISSING)
    p = open_short(view, srv, stage="narration")
    expect(p.get_by_text(WHISPER_MISSING)).to_be_visible()
    expect(p.locator("tr[data-line='l01']").get_by_text("Not checked")).to_be_visible()
    assert_healthy(p)


def test_voice_table_shows_a_deleted_take_as_waiting(view, live_server):
    srv = live_server("render")
    pagefix.write_heard(srv.sdir, {"l02": "length set the beat"})
    (srv.sdir / "audio" / "vo" / "l02.wav").unlink()  # what a re-take does
    p = open_short(view, srv, stage="narration")
    row = p.locator("tr[data-line='l02']")
    expect(row.get_by_text("Waiting for a new take")).to_be_visible()
    expect(row.locator("mark")).to_have_count(0)
    expect(row.get_by_text("length set the beat")).to_have_count(0)
    assert_healthy(p)
