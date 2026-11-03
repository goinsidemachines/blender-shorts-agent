"""out/post-pack.md: every post field and file in one plain file, written only when it changes."""

import json
import os

import pagefix
import pytest

from bsa import engine_runs, postpack
from bsa.review import checklist, snapshot
from bsa.short import load_short


def _short(sdir):
    return load_short(sdir / "source" / "short.json")


def _set_post(sdir, **kw):
    p = sdir / "source" / "short.json"
    d = json.loads(p.read_text("utf-8"))
    d["post"].update(kw)
    p.write_text(json.dumps(d), "utf-8")


def _video(sdir):
    mp4 = sdir / "out" / "pendulum.mp4"
    if not mp4.is_file():  # no ffmpeg here: a stand-in file
        mp4.write_bytes(bytes(2000))


def test_clock_and_megabytes():
    assert postpack.clock(46.7) == "0:47" and postpack.clock(65) == "1:05" and postpack.clock(59.5) == "1:00"
    assert postpack.megabytes(38_000_000) == "38 MB" and postpack.megabytes(4_250_000) == "4.3 MB"
    assert postpack.megabytes(9_960_000) == "10 MB" and postpack.megabytes(9_940_000) == "9.9 MB"


def test_pack_has_every_field_in_order_and_the_files(tmp_path):
    sdir = pagefix.make_short(tmp_path, "done")
    _video(sdir)
    pagefix.write_png(sdir / "out" / "thumbnail-1080x1920.png", __import__("numpy").zeros((4, 4, 3), "uint8"))
    (sdir / "out" / "pendulum.srt").write_text("1\n", "utf-8")
    md = postpack.markdown(sdir, _short(sdir))
    assert [x for x in md.splitlines() if x.startswith("## ")] == [
        "## Title", "## Description", "## Hashtags", "## Tags", "## Pinned comment", "## Link", "## Related video",
        "## Files"]  # fmt: skip
    assert md.startswith("# Post pack: Why a pendulum keeps perfect time")
    assert "- Thumbnail, Shorts and Reels: out/thumbnail-1080x1920.png" in md and "## Tags\n(not written yet)" in md
    assert "## Hashtags\n#physics #pendulum #blender #science" in md
    assert "- Video: out/pendulum.mp4" in md and "Post pack" not in md.split("## Files")[1]


def test_pack_is_written_only_when_it_changes(tmp_path):
    sdir = pagefix.make_short(tmp_path, "done")
    short = _short(sdir)
    path, changed = postpack.write(sdir, short)
    assert changed and path == sdir / "out" / "post-pack.md" and path.is_file()
    os.utime(path, (1_000_000, 1_000_000))
    assert postpack.write(sdir, short) == (path, False)
    assert path.stat().st_mtime == 1_000_000
    assert not list((sdir / "out").glob("*.tmp"))


def test_pack_never_names_the_plugin_author(tmp_path):
    sdir = pagefix.make_short(tmp_path, "done")
    assert "goinsidemachines" not in postpack.markdown(sdir, _short(sdir)).lower()


def test_stale_captions_are_marked_in_the_pack(tmp_path):
    sdir = pagefix.make_short(tmp_path, "done")
    (sdir / "out" / "pendulum.srt").write_text("1\n", "utf-8")
    assert "older timing" not in postpack.markdown(sdir, _short(sdir))
    pagefix.record_edit(sdir, "final", stale=True)
    md = postpack.markdown(sdir, _short(sdir))
    assert "- Captions (SRT): out/pendulum.srt (made for an older timing: run the final edit again)" in md
    assert [r["stale"] for r in postpack.files(sdir, _short(sdir)) if r["key"] == "srt"] == [True]


def test_video_info_reads_length_from_the_final_edit(tmp_path):
    sdir = pagefix.make_short(tmp_path, "done")
    _video(sdir)
    info = postpack.video_info(sdir, _short(sdir))
    assert info["rel"] == "out/pendulum.mp4" and info["exists"] and info["seconds"] == 1.0 and info["length"] == "0:01"


def test_changing_the_post_never_moves_the_final_version(tmp_path):
    sdir = pagefix.make_short(tmp_path, "done")
    before = snapshot.stage_version(sdir, "final")
    _set_post(sdir, title="Another title")
    postpack.write(sdir, _short(sdir))
    assert snapshot.stage_version(sdir, "final") == before


def test_shown_never_raises(tmp_path):
    sdir = pagefix.make_short(tmp_path, "done")
    (sdir / "source" / "short.json").write_text("{not json", "utf-8")
    postpack.shown(sdir)
    assert not (sdir / "out" / "post-pack.md").exists()


def _sized_video(sdir, n_bytes):
    mp4 = sdir / "out" / "pendulum.mp4"
    mp4.parent.mkdir(parents=True, exist_ok=True)
    with open(mp4, "r+b" if mp4.is_file() else "wb") as f:
        f.truncate(n_bytes)


def test_checklist_copy_is_built_from_what_the_plugin_knows(tmp_path):
    from bsa import thumbs

    sdir = pagefix.make_short(tmp_path, "done")
    pagefix.record_edit(sdir, "final", closing=1371)  # 1401 frames at 30 fps
    _sized_video(sdir, 38_000_000)
    items = postpack.checklist_items(sdir, _short(sdir))
    assert [i["id"] for i in items] == ["upload", "length", "link"]
    assert items[0]["text"] == ("Upload out/pendulum.mp4 (0:47, 38 MB) from this computer. A copy sent through a "
                                "phone app can be cut short.")
    assert items[1]["text"] == "After uploading, check the platform shows the full 0:47."
    assert items[2]["text"] == "After posting, tell Claude the link"
    pagefix.write_thumbs(sdir, 2)
    thumbs.choose(sdir, 1, "page")
    (sdir / "out" / "pendulum.srt").write_text("1\n", "utf-8")
    _set_post(sdir, pinned_comment="Watch more: https://example.com")
    items = postpack.checklist_items(sdir, _short(sdir))
    assert [i["id"] for i in items] == list(postpack.CHECK_IDS)
    text = {i["id"]: i["text"] for i in items}
    assert text["thumbnail"] == ("Thumbnail: out/thumbnail-1080x1920.png (Shorts and Reels: pick it as the cover frame "
                                 "where the app allows), out/thumbnail-1280x720.png (YouTube custom thumbnail).")
    assert text["captions"] == ("Captions: out/pendulum.srt. Upload it in YouTube Studio → Subtitles, so the "
                                "numbers come out right.")
    assert text["pin"] == "Pin the first comment"


def test_checklist_without_a_length_names_the_size_and_the_full_video(tmp_path):
    sdir = pagefix.make_short(tmp_path, "done")
    engine_runs.runs_path(sdir).unlink()  # no final edit on record: no length
    _sized_video(sdir, 4_250_000)
    items = postpack.checklist_items(sdir, _short(sdir))
    assert items[0]["text"] == ("Upload out/pendulum.mp4 (4.3 MB) from this computer. A copy sent through a phone app "
                                "can be cut short.")
    assert items[1]["text"] == "After uploading, check the platform shows the full video."


def test_tick_refuses_an_unknown_item(tmp_path):
    sdir = pagefix.make_short(tmp_path, "post")
    with pytest.raises(ValueError, match="nope"):
        checklist.tick(sdir, "nope", True)
    with pytest.raises(ValueError):
        checklist.tick(sdir, "upload", "yes")
    assert not checklist.path(sdir).exists() and checklist.load(sdir) == {}
    assert checklist.path(sdir) == sdir / "review" / "checklist.json"
    assert checklist.tick(sdir, "upload", True) == {"upload": True}
    assert checklist.tick(sdir, "link", True) == {"upload": True, "link": True}
    assert checklist.tick(sdir, "upload", False) == {"upload": False, "link": True}
    assert checklist.load(sdir) == {"upload": False, "link": True}
