"""The Final stage's video carries the caption track, so the captions can be switched on while watching; the Post
stage shows the thumbnail candidates to choose from, the post text with its limits, the files to upload and the
before-you-post checklist."""

import json

import pagefix
import pytest
from playwright.sync_api import expect

from browser.conftest import assert_healthy, open_short
from bsa import captions
from bsa.short import load_short

pytestmark = pytest.mark.browser


def test_final_video_has_the_caption_track(view, live_server):
    srv = live_server("done")
    mp4 = srv.sdir / "out" / "pendulum.mp4"
    if not mp4.is_file():  # no ffmpeg: a stand-in file still makes the page build the video element and its track
        mp4.write_bytes(bytes(2000))
    captions.write_files(srv.sdir, load_short(srv.sdir / "source" / "short.json"), {})
    p = open_short(view, srv, stage="final")
    track = p.locator("video.player track[kind=captions]")
    expect(track).to_have_count(1)
    assert ".vtt" in track.get_attribute("src")
    assert_healthy(p)


def test_the_caption_file_is_served_as_text_vtt(view, live_server):
    srv = live_server("done")
    mp4 = srv.sdir / "out" / "pendulum.mp4"
    if not mp4.is_file():
        mp4.write_bytes(bytes(2000))
    captions.write_files(srv.sdir, load_short(srv.sdir / "source" / "short.json"), {})
    p = open_short(view, srv, stage="final")
    expect(p.locator("video.player track[kind=captions]")).to_have_count(1)
    url = p.evaluate("document.querySelector('video.player track').src")
    reply = p.request.get(url)
    assert reply.ok and reply.headers["content-type"].startswith("text/vtt")
    assert reply.text().startswith("WEBVTT")


# --------------------------------------------------------------------------- thumbnails


def _until(fn, timeout=5.0):
    import time

    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if fn():
            return True
        time.sleep(0.05)
    return fn()


def test_no_candidates_says_how_to_make_them(view, live_server):
    srv = live_server("post")
    p = open_short(view, srv, stage="post")
    expect(p.get_by_text("No thumbnails yet. Claude makes them with BLENDER thumbs.")).to_be_visible()
    assert_healthy(p)


def test_candidates_and_feed_previews_render(view, live_server):
    srv = live_server("post")
    pagefix.write_thumbs(srv.sdir, 2)
    p = open_short(view, srv, stage="post")
    expect(p.get_by_role("heading", name="Candidate 2")).to_be_visible()
    expect(p.get_by_role("img", name="Candidate 1, vertical")).to_be_visible()
    expect(p.get_by_role("img", name="Candidate 2, landscape")).to_be_visible()
    expect(p.get_by_role("heading", name="In the feed").first).to_be_visible()
    feed = p.locator(".feed img").first
    expect(feed).to_be_visible()
    assert feed.evaluate("e => e.getBoundingClientRect().width") == pytest.approx(180, abs=1)
    assert p.locator(".feed .feed-ph").first.get_attribute("aria-hidden") == "true"
    v = p.get_by_role("img", name="Candidate 1, vertical").bounding_box()
    land = p.get_by_role("img", name="Candidate 1, landscape").bounding_box()
    if view.viewport_size["width"] < 768:  # stacked
        assert land["y"] >= v["y"] + v["height"] - 1
    else:  # side by side
        assert abs(land["y"] - v["y"]) < 2 and land["x"] > v["x"]
    for btn in p.get_by_role("button", name="Use this one").all():
        expect(btn).to_have_attribute("aria-pressed", "false")
    two = p.locator("section.thumb-cand", has=p.get_by_role("heading", name="Candidate 2"))
    heading_id = two.get_by_role("heading", name="Candidate 2").get_attribute("id")
    expect(two.get_by_role("button", name="Use this one")).to_have_attribute("aria-describedby", heading_id)
    expect(two.get_by_role("button", name="Use this one")).to_have_accessible_description("Candidate 2")
    assert_healthy(p)


def test_use_this_one_copies_the_files_and_records_chosen(view, live_server):
    from bsa import postpack, thumbs

    srv = live_server("post")
    pagefix.write_thumbs(srv.sdir, 2)
    p = open_short(view, srv, stage="post")
    two = p.locator("section.thumb-cand", has=p.get_by_role("heading", name="Candidate 2"))
    two.get_by_role("button", name="Use this one").click()
    expect(two.get_by_role("button", name="Use this one")).to_have_attribute("aria-pressed", "true")
    one = p.locator("section.thumb-cand", has=p.get_by_role("heading", name="Candidate 1"))
    expect(one.get_by_role("button", name="Use this one")).to_have_attribute("aria-pressed", "false")
    expect(p.get_by_text("Thumbnail 2 chosen.").first).to_be_attached()
    assert thumbs.load(srv.sdir)["chosen"]["n"] == 2 and thumbs.load(srv.sdir)["chosen"]["by"] == "page"
    for size, rel in postpack.OUT_FILES.items():
        assert (srv.sdir / rel).read_bytes() == (srv.sdir / f"review/thumbs/2-{size}.png").read_bytes()
    assert_healthy(p)


def test_note_on_a_candidate(view, live_server):
    from bsa.review import notes, snapshot

    srv = live_server("post")
    pagefix.write_thumbs(srv.sdir, 2)
    v = snapshot.stage_version(srv.sdir, "post", snapshot.load_ctx(srv.sdir, {}))
    p = open_short(view, srv, stage="post")
    p.get_by_role("button", name="+ Note on candidate 1").click()
    p.get_by_role("textbox").fill("Brighter, please")
    p.get_by_role("button", name="Save note").click()
    assert _until(lambda: bool(notes.load(srv.sdir)))
    n = notes.load(srv.sdir)[-1]
    assert n["stage"] == "post" and n["text"] == "Brighter, please"
    assert n["target"] == {"kind": "thumbnail", "id": "1", "version": v}
    assert_healthy(p)


def test_use_this_frame_for_the_thumbnail_is_a_frame_note(view, live_server):
    from bsa.review import notes

    srv = live_server("done")
    mp4 = srv.sdir / "out" / "pendulum.mp4"
    if not mp4.is_file():  # no ffmpeg: a stand-in file still makes the page draw the timeline
        mp4.write_bytes(bytes(2000))
    p = open_short(view, srv, stage="final")
    p.get_by_role("button", name="Finding: frame 12 flickers, frame 12").click()
    p.get_by_role("button", name="Use this frame for the thumbnail").click()
    box = p.get_by_role("textbox", name="Thumbnail from frame 12 (shot s02)")
    expect(box).to_be_focused()
    expect(box).to_have_value("Use this frame for the thumbnail")
    p.get_by_role("button", name="Save note").click()
    assert _until(lambda: bool(notes.load(srv.sdir)))
    n = notes.load(srv.sdir)[-1]
    assert n["target"]["kind"] == "frame" and n["target"]["id"] == "12" and n["target"]["shot"] == "s02"
    assert n["text"] == "Use this frame for the thumbnail"
    assert_healthy(p)


def test_render_runs_have_no_thumbnail_button(view, live_server):
    srv = live_server("render")
    p = open_short(view, srv, stage="render")
    expect(p.get_by_role("tabpanel").get_by_role("button", name="Note at this moment")).to_be_visible()
    expect(p.get_by_role("button", name="Use this frame for the thumbnail")).to_have_count(0)
    assert_healthy(p)


def test_a_remade_chosen_candidate_asks_to_pick_again(view, live_server):
    from bsa import thumbs

    srv = live_server("post")
    pagefix.write_thumbs(srv.sdir, 2)
    thumbs.choose(srv.sdir, 1, "chat")
    row = thumbs.load(srv.sdir)["candidates"][0]
    thumbs.put_candidates(srv.sdir, [row], only=1)  # candidate 1 made again: out/ still holds the earlier one
    p = open_short(view, srv, stage="post")
    expect(p.get_by_text("The thumbnail files in out/ are from an earlier make of candidate 1, which has been made "
                         "again since. Pick again to post one of these.")).to_be_visible()  # fmt: skip
    for btn in p.get_by_role("button", name="Use this one").all():
        expect(btn).to_have_attribute("aria-pressed", "false")
    assert_healthy(p)


# --------------------------------------------------------------------------- fields, files, checklist


def _set_post(sdir, **kw):
    path = sdir / "source" / "short.json"
    d = json.loads(path.read_text("utf-8"))
    d["post"].update(kw)
    path.write_text(json.dumps(d), "utf-8")


def test_copy_buttons_and_limit_badges(view, live_server):
    srv = live_server("post")
    title = "Why does a pendulum keep such perfect time, even when it slows down?"
    title = title + "!" * (70 - len(title))
    assert len(title) == 70
    _set_post(srv.sdir, title=title)
    view.context.grant_permissions(["clipboard-read", "clipboard-write"])
    p = open_short(view, srv, stage="post")
    stage = p.locator("#bsa-stage")
    expect(stage.get_by_text("70 / 100", exact=True)).to_be_visible()
    expect(stage.get_by_text("may be cut off in the feed")).to_be_visible()
    expect(stage.locator(".count", has_text="70 / 100").get_by_text("Near the limit", exact=True)).to_be_visible()
    expect(stage.get_by_text("Fits YouTube", exact=True)).to_be_visible()
    expect(stage.get_by_text("Fits TikTok", exact=True)).to_be_visible()
    assert stage.locator(".count").first.get_attribute("aria-live") == "off"
    stage.get_by_role("button", name="Copy the title").click()
    expect(p.get_by_text("Copied the title.").first).to_be_attached()
    assert _until(lambda: p.evaluate("navigator.clipboard.readText()") == title)
    headings = [h.strip() for h in stage.locator(".st-post h3").all_inner_texts()]
    assert headings[:4] == ["Thumbnail", "Post text", "Files", "Before you post"]
    assert_healthy(p)


def test_a_title_over_the_limit_says_so_in_words(view, live_server):
    srv = live_server("post")
    _set_post(srv.sdir, title="T" * 101)
    p = open_short(view, srv, stage="post")
    stage = p.locator("#bsa-stage")
    expect(stage.get_by_text("101 / 100", exact=True)).to_be_visible()
    expect(stage.get_by_text("Over the limit", exact=True)).to_be_visible()
    expect(stage.get_by_text("YouTube allows 100")).to_be_visible()
    assert_healthy(p)
    p.emulate_media(color_scheme="dark")  # the counts, notes and badges keep their contrast in the dark theme too
    assert_healthy(p)


def test_checklist_ticks_persist(view, live_server):
    from bsa.review import checklist

    srv = live_server("post")
    p = open_short(view, srv, stage="post")
    expect(p.get_by_role("heading", name="Before you post")).to_be_visible()
    expect(p.get_by_text("from this computer. A copy sent through a phone app can be cut short.")).to_be_visible()
    box = p.get_by_role("checkbox", name="After posting, tell Claude the link")
    expect(box).not_to_be_checked()
    box.check()
    assert _until(lambda: checklist.load(srv.sdir) == {"link": True})
    expect(box).to_be_checked()
    p.reload()
    p.locator("html[data-ready='1']").wait_for(state="attached")
    expect(p.get_by_role("checkbox", name="After posting, tell Claude the link")).to_be_checked()
    expect(p.get_by_text("Ticks aren't saved in the offline view.")).to_have_count(0)
    assert checklist.load(srv.sdir) == {"link": True}
    p.get_by_role("checkbox", name="After posting, tell Claude the link").uncheck()
    assert _until(lambda: checklist.load(srv.sdir) == {"link": False})
    assert_healthy(p)


def test_files_have_copy_path_buttons(view, live_server):
    srv = live_server("done")
    mp4 = srv.sdir / "out" / "pendulum.mp4"
    if not mp4.is_file():  # no ffmpeg: a stand-in file
        mp4.write_bytes(bytes(2000))
    captions.write_files(srv.sdir, load_short(srv.sdir / "source" / "short.json"), {})
    view.context.grant_permissions(["clipboard-read", "clipboard-write"])
    p = open_short(view, srv, stage="post")
    files = p.locator("section.post-files")
    expect(files.get_by_text("out/pendulum.mp4", exact=True)).to_be_visible()
    expect(files.get_by_text("out/pendulum.srt", exact=True)).to_be_visible()
    expect(files.get_by_text("Made for an older timing: run the final edit again.")).to_have_count(0)
    btn = files.get_by_role("button", name="Copy path").first
    expect(btn).to_have_accessible_description("Video")
    btn.click()
    want = str(mp4)
    assert _until(lambda: p.evaluate("navigator.clipboard.readText()") == want)
    assert_healthy(p)


def test_stale_caption_files_are_flagged(view, live_server):
    srv = live_server("done")
    mp4 = srv.sdir / "out" / "pendulum.mp4"
    if not mp4.is_file():
        mp4.write_bytes(bytes(2000))
    captions.write_files(srv.sdir, load_short(srv.sdir / "source" / "short.json"), {})
    pagefix.record_edit(srv.sdir, "final", stale=True)
    p = open_short(view, srv, stage="post")
    row = p.locator("li.file-row", has=p.get_by_text("Captions (SRT)", exact=True))
    expect(row.get_by_text("Made for an older timing: run the final edit again.")).to_be_visible()
    assert_healthy(p)


def test_offline_checklist_boxes_work_but_are_not_saved(view, tmp_path, bsa_home):
    from bsa.review import checklist, launch

    sdir = pagefix.make_short(tmp_path / "ws", "post")
    view.goto(launch.static_page(sdir).as_uri() + "#post")
    view.locator("html[data-ready='1']").wait_for(state="attached")
    expect(view.get_by_text("Ticks aren't saved in the offline view.")).to_be_visible()
    box = view.get_by_role("checkbox", name="After posting, tell Claude the link")
    box.check()
    expect(box).to_be_checked()
    view.get_by_role("button", name="Copy the title").click()  # a re-render keeps the tick in this tab
    expect(box).to_be_checked()
    assert checklist.load(sdir) == {}
    assert_healthy(view)


def test_the_whole_post_stage_on_one_short(view, live_server):
    from bsa import captions, settings, thumbs

    srv = live_server("done")
    _set_post(srv.sdir, pinned_comment="More like this: https://example.com/x", link="https://example.com/x")
    pagefix.write_thumbs(srv.sdir, 2)
    captions.write_files(srv.sdir, load_short(srv.sdir / "source" / "short.json"), settings.load_settings())
    thumbs.choose(srv.sdir, 1, "chat")
    p = open_short(view, srv, stage="post")
    stage = p.locator("#bsa-stage")
    expect(stage.get_by_role("heading", name="Candidate 1")).to_be_visible()
    expect(stage.get_by_role("heading", name="Post text")).to_be_visible()
    expect(stage.get_by_role("button", name="Copy the title")).to_be_visible()
    expect(stage.get_by_role("heading", name="Files")).to_be_visible()
    expect(stage.get_by_role("heading", name="Before you post")).to_be_visible()
    assert_healthy(p)
