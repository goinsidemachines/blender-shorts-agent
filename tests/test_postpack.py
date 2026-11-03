"""out/post-pack.md: every post field and file in one plain file, written only when it changes."""

import json
import os

import pagefix
import pytest

from bsa import engine_runs, postpack
from bsa.review import checklist, snapshot
from bsa.short import load_short


@pytest.fixture(autouse=True)
def _no_machine_settings(monkeypatch):
    """The pack reads the user's settings when none are given: here it never does, whatever machine runs this."""
    monkeypatch.setattr(postpack, "_settings", lambda settings: {} if settings is None else settings)


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
    out = sdir / postpack.OUT_FILES["1080x1920"]
    kept = out.read_bytes()
    out.unlink()  # a choice whose file is gone from out/: nothing to point at
    assert "thumbnail" not in [i["id"] for i in postpack.checklist_items(sdir, _short(sdir))]
    out.write_bytes(kept)
    (sdir / "out" / "pendulum.srt").write_text("1\n", "utf-8")
    _set_post(sdir, pinned_comment="Watch more: https://example.com")
    items = postpack.checklist_items(sdir, _short(sdir))
    assert [i["id"] for i in items] == [c for c in postpack.CHECK_IDS if c != "languages"]
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


def test_checklist_says_when_the_thumbnail_is_from_an_earlier_make(tmp_path):
    from bsa import thumbs

    sdir = pagefix.make_short(tmp_path, "done")
    pagefix.write_thumbs(sdir, 2)
    thumbs.choose(sdir, 1, "page")
    text = {i["id"]: i["text"] for i in postpack.checklist_items(sdir, _short(sdir))}["thumbnail"]
    assert text.endswith("(YouTube custom thumbnail).")
    row = thumbs.load(sdir)["candidates"][0]
    thumbs.put_candidates(sdir, [row], only=1)  # candidate 1 made again: out/ still holds the earlier one
    text = {i["id"]: i["text"] for i in postpack.checklist_items(sdir, _short(sdir))}["thumbnail"]
    assert text.endswith("(YouTube custom thumbnail) (from an earlier make).")
    (sdir / thumbs.THUMBS_JSON).write_text("{", encoding="utf-8")  # damaged: not known, so not said
    text = {i["id"]: i["text"] for i in postpack.checklist_items(sdir, _short(sdir))}["thumbnail"]
    assert text.endswith("(YouTube custom thumbnail).")


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


def _translated(sdir, **langs):
    p = sdir / "source" / "short.json"
    d = json.loads(p.read_text("utf-8"))
    en = {ln["id"]: ln["text"] for ln in d["lines"]}
    out = {}
    for lang, fresh in langs.items():
        out[lang] = {"lines": {k: f"{lang} {k}" for k in en}, "from": dict(en) if fresh else {"l01": "older"}}
    d["captions_translations"] = out
    p.write_text(json.dumps(d), "utf-8")


def test_language_rows_and_checklist(tmp_path):
    sdir = pagefix.make_short(tmp_path, "done")
    _translated(sdir, es=True, de=False)
    out = sdir / "out"
    for name in ("es.srt", "es.vtt", "de.srt"):
        (out / f"pendulum.{name}").write_text("1\n", "utf-8")
    rows = {r["key"]: r for r in postpack.files(sdir, _short(sdir))}
    assert rows["srt-es"]["label"] == "Captions, es (SRT)" and rows["srt-es"]["rel"] == "out/pendulum.es.srt"
    assert rows["vtt-es"]["exists"] and not rows["srt-es"]["stale"] and "why" not in rows["srt-es"]
    assert rows["srt-de"]["stale"] and rows["srt-de"]["why"].startswith("Made for the old English")
    assert "CLI captions SHORT" in rows["srt-de"]["why"] and not rows["vtt-de"]["exists"]
    assert "why" not in rows["srt"]
    keys = [r["key"] for r in postpack.files(sdir, _short(sdir))]
    assert keys.index("srt-es") > keys.index("transcript") and keys[-1] == "pack"
    md = postpack.markdown(sdir, _short(sdir))
    assert "- Captions, es (SRT): out/pendulum.es.srt" in md and "- Captions, es (VTT): out/pendulum.es.vtt" in md
    assert "Captions, de (SRT): out/pendulum.de.srt (" in md and "old English" in md
    assert postpack.CHECK_IDS.index("languages") == postpack.CHECK_IDS.index("captions") + 1
    (out / "pendulum.srt").write_text("1\n", "utf-8")
    items = postpack.checklist_items(sdir, _short(sdir))
    ids = [i["id"] for i in items]
    assert ids[ids.index("captions") + 1] == "languages"
    text = {i["id"]: i["text"] for i in items}
    assert text["languages"] == "Upload each language in YouTube Studio → Subtitles: out/pendulum.es.srt"


def test_no_language_files_no_languages_item(tmp_path):
    sdir = pagefix.make_short(tmp_path, "done")
    _translated(sdir, es=True)
    assert "languages" not in [i["id"] for i in postpack.checklist_items(sdir, _short(sdir))]


def _closing_translated(sdir, closing_from):
    p = sdir / "source" / "short.json"
    d = json.loads(p.read_text("utf-8"))
    en = {ln["id"]: ln["text"] for ln in d["lines"]}
    d["captions_translations"] = {"es": {"lines": {k: f"es {k}" for k in en}, "closing": "Descubre mas.",
                                         "from": {**en, "closing": closing_from}}}  # fmt: skip
    p.write_text(json.dumps(d), "utf-8")


def test_a_stale_closing_line_is_flagged_like_captions_skips_it(tmp_path):
    from bsa import captions

    sdir = pagefix.make_short(tmp_path, "done")
    s = {"website": "example.com", "closing": {"voice_line": "Find out more."}}
    _closing_translated(sdir, "Visit us.")
    (sdir / "out" / "pendulum.es.srt").write_text("1\n", "utf-8")
    row = {r["key"]: r for r in postpack.files(sdir, _short(sdir), settings=s)}["srt-es"]
    assert row["stale"] and row["why"] == postpack.OLD_ENGLISH
    assert "old English" in postpack.markdown(sdir, _short(sdir), s)
    assert "languages" not in [i["id"] for i in postpack.checklist_items(sdir, _short(sdir), settings=s)]
    assert "es" in captions.write_files(sdir, _short(sdir), s)["skipped"]
    _closing_translated(sdir, "Find out more.")
    row = {r["key"]: r for r in postpack.files(sdir, _short(sdir), settings=s)}["srt-es"]
    assert not row["stale"] and "es" in captions.write_files(sdir, _short(sdir), s)["languages"]


def test_a_language_file_says_which_reason_it_is_stale(tmp_path):
    sdir = pagefix.make_short(tmp_path, "done")

    def row(fresh):
        _translated(sdir, es=fresh)
        (sdir / "out" / "pendulum.es.srt").write_text("1\n", "utf-8")
        return {r["key"]: r for r in postpack.files(sdir, _short(sdir))}["srt-es"]

    assert not row(True)["stale"]
    only_english = row(False)
    assert only_english["stale"] and only_english["why"] == postpack.OLD_ENGLISH
    pagefix.record_edit(sdir, "final", stale=True)
    both = row(False)
    assert postpack.OLD_ENGLISH in both["why"] and "older timing" in both["why"]
    only_timing = row(True)
    assert only_timing["stale"] and "why" not in only_timing
    md = postpack.markdown(sdir, _short(sdir))
    assert "run the final edit again" in md


def test_a_language_with_errors_is_flagged_everywhere_until_fixed(tmp_path):
    from bsa import captions

    sdir = pagefix.make_short(tmp_path, "done")
    _translated(sdir, es=True)
    captions.write_files(sdir, _short(sdir), {})
    items = postpack.checklist_items(sdir, _short(sdir))
    assert "out/pendulum.es.srt" in {i["id"]: i["text"] for i in items}["languages"]
    p = sdir / "source" / "short.json"
    good = p.read_text("utf-8")
    d = json.loads(good)
    d["lines"].append({**d["lines"][-1], "id": "l05", "text": "A new line."})
    p.write_text(json.dumps(d), "utf-8")
    rows = {r["key"]: r for r in postpack.files(sdir, _short(sdir))}
    assert rows["srt-es"]["stale"] and rows["srt-es"]["why"] == "Not updated: line l05 has no es translation"
    assert "languages" not in [i["id"] for i in postpack.checklist_items(sdir, _short(sdir))]
    md = postpack.markdown(sdir, _short(sdir))
    assert "(Not updated: line l05 has no es translation)" in md
    assert captions.write_files(sdir, _short(sdir), {})["skipped"]["es"].startswith("Not updated: line l05")
    p.write_text(good, "utf-8")
    assert not {r["key"]: r for r in postpack.files(sdir, _short(sdir))}["srt-es"]["stale"]
    assert "languages" in [i["id"] for i in postpack.checklist_items(sdir, _short(sdir))]


def test_skipped_without_settings_reads_the_users_settings(tmp_path, monkeypatch):
    from bsa import translations

    s = {"website": "example.com", "closing": {"voice_line": "Find out more."}}
    home = tmp_path / "home"
    home.mkdir()
    (home / "settings.json").write_text(json.dumps(s), "utf-8")
    monkeypatch.setenv("BSA_HOME", str(home))
    sdir = pagefix.make_short(tmp_path, "done")
    _closing_translated(sdir, "Find out more.")
    assert translations.skipped(_short(sdir), s) == {}
    assert translations.skipped(_short(sdir)) == {}  # a valid closing is not made to look wrong
    _closing_translated(sdir, "Visit us.")
    assert translations.skipped(_short(sdir)) == {"es": translations.OLD_ENGLISH}


def test_a_removed_languages_files_are_left_and_said_not_current(tmp_path):
    from bsa import captions

    sdir = pagefix.make_short(tmp_path, "done")
    _translated(sdir, es=True, de=True)
    captions.write_files(sdir, _short(sdir), {})
    old = [sdir / "out" / "pendulum.de.srt", sdir / "out" / "pendulum.de.vtt"]
    assert all(p.is_file() for p in old)
    _translated(sdir, es=True)  # German removed
    got = captions.write_files(sdir, _short(sdir), {})
    assert got["left_over"] == {"de": [str(p) for p in old]} and set(got["languages"]) == {"es"}
    assert all(p.is_file() for p in old)  # the user's files are never deleted
    rows = {r["key"]: r for r in postpack.files(sdir, _short(sdir), settings={})}
    for key in ("srt-de", "vtt-de"):
        assert rows[key]["exists"] and rows[key]["stale"] and rows[key]["why"] == postpack.LANGUAGE_REMOVED
    assert not rows["srt-es"]["stale"]
    assert "Captions, de (SRT): out/pendulum.de.srt (Not current (language removed))" in postpack.markdown(
        sdir, _short(sdir), {})
    assert "pendulum.de.srt" not in {i["id"]: i["text"] for i in postpack.checklist_items(
        sdir, _short(sdir), settings={})}.get("languages", "")
    _translated(sdir, es=True, de=True)  # added back: current again, nothing left over
    assert "left_over" not in captions.write_files(sdir, _short(sdir), {})


def test_cli_captions_names_the_stale_lines_of_a_skipped_language(tmp_path):
    from bsa import captions

    sdir = pagefix.make_short(tmp_path, "done")
    _translated(sdir, de=False)
    why = captions.write_files(sdir, _short(sdir), {})["skipped"]["de"]
    assert why.startswith("Made for the old English (l01") and why.endswith("then run CLI captions SHORT")
