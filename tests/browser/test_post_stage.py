"""The Final stage's video carries the caption track, so the captions can be switched on while watching."""

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
