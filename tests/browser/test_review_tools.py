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
    expect(row.get_by_text("Not heard yet")).to_be_visible()
    expect(row.locator("mark")).to_have_count(0)
    expect(row.get_by_text("length set the beat")).to_have_count(0)
    assert_healthy(p)


def test_fact_panel_flags_and_needs_a_source_note(view, live_server):
    srv = live_server("story")
    pagefix.with_numbers(srv.sdir)
    p = open_short(view, srv, stage="story")
    card = p.locator("details.facts-panel")
    expect(card).to_have_attribute("open", "")  # something is flagged, so it opens by itself
    expect(card.get_by_text("Has a number but cites no fact")).to_be_visible()
    expect(card.locator(".fact-line[data-line='l04']").get_by_text("Clocks kept time this way for 270 years.")).to_be_visible()  # the line's own text
    expect(card.get_by_text("not on its mass").first).to_be_visible()  # the fact's claim, in view
    expect(card.get_by_text("No fact has this number").first).to_be_visible()
    expect(card.locator("a.chip[rel='noopener noreferrer']").first).to_be_visible()
    version = snapshot.stage_version(srv.sdir, "story", snapshot.load_ctx(srv.sdir, {}))
    p.get_by_role("button", name="Needs a source: l04").click()
    expect(p.get_by_role("textbox")).to_have_value("Needs a source")
    p.get_by_role("button", name="Save note").click()
    assert _until(lambda: bool(notes.load(srv.sdir)))
    assert notes.load(srv.sdir)[-1]["target"] == {"kind": "line", "id": "l04", "version": version}
    assert_healthy(p)


def test_fact_panel_shows_on_final(view, live_server):
    srv = live_server("done")
    _stand_in_video(srv)
    p = open_short(view, srv, stage="final")
    card = p.locator("details.facts-panel")
    expect(card).to_be_visible()
    expect(card.locator("summary")).to_have_text("Facts behind every line")
    assert not card.evaluate("c => c.open")  # nothing flagged on the plain pendulum
    card.locator("summary").click()
    expect(card.get_by_text("f-period").first).to_be_visible()
    expect(card.locator(".fact-line[data-line='l02']").get_by_text("Length sets the beat.")).to_be_visible()
    expect(card.get_by_text("not on its mass").first).to_be_visible()  # the claim, readable without a hover
    card.locator("summary").click()  # the user closed it: a live update keeps it closed
    p.wait_for_timeout(1500)
    assert not card.evaluate("c => c.open")
    assert_healthy(p)


def _set_line(sdir, line_id: str, text: str) -> None:
    path = sdir / "source" / "short.json"
    data = json.loads(path.read_text("utf-8"))
    for ln in data["lines"]:
        if ln["id"] == line_id:
            ln["text"] = text
    path.write_text(json.dumps(data, indent=2), "utf-8")


def test_story_compare_shows_a_word_diff(view, live_server):
    srv = live_server("story")
    p = open_short(view, srv, stage="story")  # version 1 is shown, so it is kept
    _set_line(srv.sdir, "l01", "Now a pendulum swings like.")  # "now" is new, "clockwork" is gone
    _set_line(srv.sdir, "l02", "Length sets the swing.")  # a plain one-word swap: "beat" became "swing"
    _set_line(srv.sdir, "l03", "Four times longer, half as fast.")  # "twice" became "half"
    button = p.get_by_role("button", name="Compare with version 1")
    expect(button).to_be_visible(timeout=4000)
    expect(button).to_have_attribute("aria-expanded", "false")
    button.click()
    expect(button).to_have_attribute("aria-expanded", "true")
    l01 = p.locator(".cmp-line[data-line='l01']")
    expect(l01.locator("del", has_text="clockwork")).to_be_visible()
    expect(l01.locator("ins", has_text="now")).to_be_visible()
    swap = p.locator(".cmp-line[data-line='l02'] mark")
    expect(swap.locator("del", has_text="beat")).to_be_visible()  # the old word and the new one, both shown
    expect(swap.locator("ins", has_text="swing")).to_be_visible()
    expect(swap).to_contain_text("changed from: beat to: swing")
    l03 = p.locator(".cmp-line[data-line='l03'] mark")
    expect(l03.first.locator("del", has_text="twice")).to_be_visible()
    expect(l03.first.locator("ins", has_text="half")).to_be_visible()
    for changed in ("l01", "l02", "l03"):
        expect(p.locator(f".cmp-line[data-line='{changed}']").get_by_text("Changed", exact=True)).to_be_visible()
    unchanged = p.locator(".cmp-line[data-line='l04']")
    expect(unchanged).to_be_hidden()  # unchanged lines start collapsed
    show = p.get_by_role("button", name="Show unchanged lines")
    expect(show).to_have_attribute("aria-expanded", "false")
    show.click()
    expect(show).to_have_attribute("aria-expanded", "true")
    expect(unchanged).to_be_visible()
    notes.add(srv.sdir, "story", {"kind": "overall", "id": None, "version": 2}, "keep it", "chat")  # a live update
    expect(p.get_by_text("keep it", exact=True)).to_be_visible(timeout=4000)
    expect(button).to_have_attribute("aria-expanded", "true")  # still open
    expect(swap.locator("del", has_text="beat")).to_be_visible()
    expect(unchanged).to_be_visible()  # and the unchanged lines still shown
    button.click()
    expect(button).to_have_attribute("aria-expanded", "false")
    expect(p.locator(".cmp-line")).to_have_count(0)
    assert_healthy(p)


def test_storyboard_compare_side_by_side_and_toggle_on_phone(view, live_server):
    srv = live_server("storyboard")
    p = open_short(view, srv, stage="storyboard")
    pagefix.write_png(srv.sdir / "stills" / "s02.png", pagefix.np.full((8, 8, 3), 200, pagefix.np.uint8))
    button = p.get_by_role("button", name="Compare with version 1")
    expect(button).to_be_visible(timeout=4000)
    button.click()
    shot = p.locator(".cmp-shot[data-shot='s02']")
    before = shot.locator("img[alt='Shot s02 before (version 1)']")  # by alt: the hidden one too
    now = shot.locator("img[alt='Shot s02 now']")
    expect(before).to_have_attribute("src", re.compile("review/history/storyboard/v1/stills/s02.png"))
    phone = view.viewport_size["width"] < 768
    if not phone:
        expect(before).to_be_visible()
        expect(now).to_be_visible()
        assert before.bounding_box()["y"] == now.bounding_box()["y"]  # side by side
        expect(p.get_by_role("button", name="Show before")).to_be_hidden()
    else:
        show_before = p.get_by_role("button", name="Show before")
        show_now = p.get_by_role("button", name="Show now")
        expect(show_now).to_have_attribute("aria-pressed", "true")
        expect(now).to_be_visible()
        expect(before).to_be_hidden()
        show_before.click()
        expect(show_before).to_have_attribute("aria-pressed", "true")
        expect(show_now).to_have_attribute("aria-pressed", "false")
        expect(before).to_be_visible()
        expect(now).to_be_hidden()
        notes.add(srv.sdir, "storyboard", {"kind": "overall", "id": None, "version": 2}, "a board note", "chat")
        expect(p.get_by_text("a board note", exact=True)).to_be_visible(timeout=4000)
        expect(before).to_be_visible()  # the choice survives a live update
    assert_healthy(p)


def test_final_compare_plays_both_videos(view, live_server):
    from bsa.review import history

    srv = live_server("final")
    _real_video(srv)
    p = open_short(view, srv, stage="final")  # version 1 is shown, with its MP4's digest
    first = p.locator(".st-final > .video-wrap video")
    assert _until(lambda: first.evaluate("v => v.readyState >= 1"))  # not moved while the page is still reading it
    mp4 = srv.sdir / "out" / "pendulum.mp4"
    assert history.keep_outgoing_final(srv.sdir, mp4) is not None
    assert pagefix.make_mp4(mp4, seconds=1.0)  # the new final: another 30-frame video
    button = p.get_by_role("button", name="Compare with version 1")
    expect(button).to_be_visible(timeout=6000)
    button.click()
    videos = p.locator(".cmp-videos video")
    expect(videos).to_have_count(2)
    now, before = videos.nth(0), videos.nth(1)
    assert before.evaluate("v => v.muted") and not now.evaluate("v => v.muted")
    both = "[...document.querySelectorAll('.cmp-videos video')]"
    # Chromium may hold off preloading the compare's "now" video while the main player has the same file; a play
    # starts it, so only the before video's metadata is waited for
    assert _until(lambda: before.evaluate("v => v.readyState >= 1"))
    main = p.locator(".st-final > .video-wrap video")
    main.evaluate("v => { v.muted = true; v.loop = true; v.play().catch(() => {}); }")  # not awaited: may never settle
    assert _until(lambda: not main.evaluate("v => v.paused"))
    before.evaluate("v => { v.play().catch(() => {}); }")  # play on the muted one: the other plays too
    assert _until(lambda: p.evaluate(both + ".every(v => !v.paused)"))
    assert _until(lambda: main.evaluate("v => v.paused"))  # and the main player stops: one sound at a time
    now.evaluate("v => v.pause()")
    assert _until(lambda: p.evaluate(both + ".every(v => v.paused)"))
    now.evaluate("v => { v.currentTime = 15.5 / 30; }")  # a seek on one moves the other
    times = both + ".map(v => v.currentTime)"
    ok = _until(lambda: abs(p.evaluate(times)[1] - 15.5 / 30) <= 1 / 30)
    assert ok, p.evaluate(both + '.map(v => [v.readyState, v.networkState, v.currentTime, v.paused, v.seeking])')
    a, b = p.evaluate(times)
    assert abs(a - b) <= 1 / 30
    assert_healthy(p)


def test_final_compare_without_a_kept_video_lists_its_findings(view, live_server):
    srv = live_server("final")
    _stand_in_video(srv)
    p = open_short(view, srv, stage="final")
    (srv.sdir / "out" / "pendulum.mp4").write_bytes(bytes(3000))  # a new final; the old MP4 was not kept
    button = p.get_by_role("button", name="Compare with version 1")
    expect(button).to_be_visible(timeout=4000)
    button.click()
    expect(p.get_by_text("Version 1 had no video kept; its findings were:")).to_be_visible()
    expect(p.locator(".cmp-findings").get_by_text("frame 12 flickers")).to_be_visible()
    assert_healthy(p)


def test_no_compare_without_history(view, live_server):
    srv = live_server("story")
    p = open_short(view, srv, stage="story")
    notes.add(srv.sdir, "story", {"kind": "overall", "id": None, "version": 1}, "a poll", "chat")
    expect(p.get_by_text("a poll", exact=True)).to_be_visible(timeout=4000)
    expect(p.get_by_role("button", name=re.compile("^Compare with"))).to_have_count(0)
    assert_healthy(p)


def test_final_compare_video_and_focus_survive_a_live_update(view, live_server):
    from bsa.review import history

    srv = live_server("final")
    _stand_in_video(srv)
    p = open_short(view, srv, stage="final")
    mp4 = srv.sdir / "out" / "pendulum.mp4"
    assert history.keep_outgoing_final(srv.sdir, mp4) is not None
    mp4.write_bytes(bytes(3000))  # a new final
    button = p.get_by_role("button", name="Compare with version 1")
    expect(button).to_be_visible(timeout=4000)
    button.click()
    expect(button).to_be_focused()
    expect(p.locator(".cmp-videos video")).to_have_count(2)
    p.evaluate("document.querySelectorAll('.cmp-videos video').forEach(v => { v.dataset.mark = 'kept'; })")
    notes.add(srv.sdir, "final", {"kind": "overall", "id": None, "version": 2}, "an update", "chat")
    expect(p.get_by_text("an update", exact=True)).to_be_visible(timeout=4000)
    assert p.evaluate("[...document.querySelectorAll('.cmp-videos video')].map(v => v.dataset.mark)") == ["kept"] * 2
    expect(button).to_have_attribute("aria-expanded", "true")
    expect(button).to_be_focused()
    assert_healthy(p)


def test_every_review_tool_on_one_short(view, live_server):
    """One finished short, each stage in turn: Story's fact panel, Narration's voice table, the Render run's
    timeline and the Final's timeline with its fact panel."""
    srv = live_server("done")
    _stand_in_video(srv)
    pagefix.with_numbers(srv.sdir)
    pagefix.write_heard(srv.sdir, {"l02": "length set the beat"})
    p = open_short(view, srv, stage="final")
    nav = p.get_by_role("navigation", name="Stages")

    nav.get_by_role("button", name="Story", exact=True).click()
    expect(p.get_by_role("heading", level=2, name="Story")).to_be_visible()
    card = p.locator("details.facts-panel")
    expect(card).to_have_attribute("open", "")
    expect(card.get_by_text("Has a number but cites no fact")).to_be_visible()
    expect(p.get_by_role("button", name="Needs a source: l04")).to_be_visible()
    assert_healthy(p)

    nav.get_by_role("button", name="Narration", exact=True).click()
    row = p.locator("tr[data-line='l02']")
    expect(row.locator("mark", has_text="set")).to_be_visible()
    expect(p.get_by_role("button", name="Re-take: l02")).to_be_visible()
    assert_healthy(p)

    nav.get_by_role("button", name="Render", exact=True).click()
    panel = p.get_by_role("tabpanel")
    expect(panel.locator("video")).to_have_count(1)
    expect(panel.get_by_role("slider", name="Frame")).to_be_visible()
    expect(panel.get_by_role("button", name="Note at this moment")).to_be_visible()
    assert_healthy(p)

    nav.get_by_role("button", name="Final", exact=True).click()
    stage = p.locator("#bsa-stage")
    expect(stage.get_by_role("slider", name="Frame")).to_be_visible()
    expect(stage.get_by_role("button", name="Note at this moment")).to_be_visible()
    expect(stage.locator("details.facts-panel")).to_have_attribute("open", "")
    assert_healthy(p)
