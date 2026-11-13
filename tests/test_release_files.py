"""The release deliverables: the deliver folder, the after-final hook, hand-edit-safe post files, the Instagram file,
the final's thumbnails step, and renaming a short's folder."""

import json
import subprocess
import sys
from pathlib import Path

import pagefix
import pytest
from test_cli import ok, run

from bsa import deliver, hooks, postpack, settings, thumbs, workspace
from bsa.short import load_short


@pytest.fixture(autouse=True)
def _no_machine_settings(monkeypatch):
    monkeypatch.setattr(postpack, "_settings", lambda s: {} if s is None else s)


def _short(sdir):
    return load_short(sdir / "source" / "short.json")


def _final(sdir):
    mp4 = sdir / "out" / "pendulum.mp4"
    mp4.parent.mkdir(exist_ok=True)
    mp4.write_bytes(b"video-bytes")
    return mp4


IG = {"post_format": {"platforms": ["instagram"]}}


# ---------------------------------------------------------------- settings


def test_deliver_dir_is_a_relative_folder_name():
    assert settings.check_value("render.deliver_dir", "final") == "final"
    assert settings.check_value("render.deliver_dir", "") is None
    for bad in ("/tmp/x", "../x", "a/../../x", "source", "C:\\x"):
        with pytest.raises(ValueError, match="deliver_dir"):
            settings.check_value("render.deliver_dir", bad)


def test_after_final_hook_setting_checks_its_placeholders():
    assert settings.check_value("hooks.after_final", "bash b.sh {mp4} {slug}") == "bash b.sh {mp4} {slug}"
    with pytest.raises(ValueError, match="use only"):
        settings.check_value("hooks.after_final", "bash b.sh {nope}")
    with pytest.raises(ValueError, match="can't be read"):
        settings.check_value("hooks.after_final", 'bash "b.sh')


def test_post_format_platforms_are_known_words():
    from bsa import post

    assert post.format_value({"platforms": ["Instagram", "youtube"]}) == {"platforms": ["instagram", "youtube"]}
    with pytest.raises(ValueError, match="platforms"):
        post.format_value({"platforms": ["myspace"]})


# ---------------------------------------------------------------- deliver (D-14)


def test_deliver_copies_the_final_and_thumbnails(tmp_path):
    sdir = pagefix.make_short(tmp_path, "done")
    _final(sdir)
    (sdir / "out" / "thumbnail-1080x1920.png").write_bytes(b"v")
    (sdir / "out" / "thumbnail-1280x720.png").write_bytes(b"h")
    res = deliver.deliver(sdir, _short(sdir), {"render": {"deliver_dir": "final"}})
    assert sorted(Path(p).name for p in res["files"]) == ["pendulum.mp4", "thumbnail-1080x1920.png",
                                                          "thumbnail-1280x720.png"]
    assert (sdir / "final" / "pendulum.mp4").read_bytes() == b"video-bytes"
    assert res["dir"] == str(sdir / "final") and res["warnings"] == []


def test_deliver_is_off_when_unset(tmp_path):
    sdir = pagefix.make_short(tmp_path, "done")
    _final(sdir)
    assert deliver.deliver(sdir, _short(sdir), {}) is None
    assert not (sdir / "final").exists()


def test_deliver_overwrites_its_own_files_but_never_the_users(tmp_path):
    sdir = pagefix.make_short(tmp_path, "done")
    mp4 = _final(sdir)
    s = {"render": {"deliver_dir": "final"}}
    (sdir / "final").mkdir()
    (sdir / "final" / "notes.txt").write_text("mine", "utf-8")
    (sdir / "final" / "thumbnail-1280x720.png").write_bytes(b"user's own")
    (sdir / "out" / "thumbnail-1280x720.png").write_bytes(b"plugin's")
    first = deliver.deliver(sdir, _short(sdir), s)
    assert [x["file"] for x in first["skipped"]] == [str(sdir / "final" / "thumbnail-1280x720.png")]
    assert (sdir / "final" / "thumbnail-1280x720.png").read_bytes() == b"user's own"
    mp4.write_bytes(b"new video")  # the next final replaces what the plugin put there
    deliver.deliver(sdir, _short(sdir), s)
    assert (sdir / "final" / "pendulum.mp4").read_bytes() == b"new video"
    assert (sdir / "final" / "notes.txt").read_text("utf-8") == "mine"


# ---------------------------------------------------------------- hook (D-19)


def test_hook_fills_placeholders_as_single_arguments(tmp_path):
    sdir = tmp_path / "a folder 'q'" / "01-x"
    seen = {}

    def fake(argv, **kw):
        seen.update(argv=argv, kw=kw)
        return subprocess.CompletedProcess(argv, 0, stdout="copied\n", stderr="")

    s = {"hooks": {"after_final": "backup {short_dir} --file={mp4} {slug} {deliver_dir}"}}
    res = hooks.run_after_final(s, sdir, "x", sdir / "out" / "x.mp4", sdir / "final", run=fake)
    assert seen["argv"] == ["backup", str(sdir), f"--file={sdir / 'out' / 'x.mp4'}", "x", str(sdir / "final")]
    assert seen["kw"]["timeout"] == hooks.TIMEOUT_S and seen["kw"]["stdin"] == subprocess.DEVNULL
    assert res["ok"] and res["exit_code"] == 0 and res["output"] == "copied" and "warning" not in res


def test_hook_failure_and_timeout_are_warnings(tmp_path):
    s = {"hooks": {"after_final": "backup {mp4}"}}
    mp4 = tmp_path / "x.mp4"

    def fails(argv, **kw):
        return subprocess.CompletedProcess(argv, 3, stdout="", stderr="no disk")

    res = hooks.run_after_final(s, tmp_path, "x", mp4, None, run=fails)
    assert not res["ok"] and res["exit_code"] == 3 and "no disk" in res["output"] and "exited 3" in res["warning"]

    def slow(argv, **kw):
        raise subprocess.TimeoutExpired(argv, 1)

    assert "stopped after" in hooks.run_after_final(s, tmp_path, "x", mp4, None, run=slow, timeout=1)["warning"]

    def missing(argv, **kw):
        raise FileNotFoundError(2, "No such file")

    assert "couldn't start" in hooks.run_after_final(s, tmp_path, "x", mp4, None, run=missing)["warning"]


def test_hook_needing_deliver_dir_without_one_is_not_run(tmp_path):
    called = []
    s = {"hooks": {"after_final": "backup {deliver_dir}"}}
    res = hooks.run_after_final(s, tmp_path, "x", tmp_path / "x.mp4", None, run=lambda *a, **k: called.append(1))
    assert not called and "render.deliver_dir" in res["warning"]


def test_no_hook_set_means_nothing_runs(tmp_path):
    assert hooks.run_after_final({}, tmp_path, "x", tmp_path / "x.mp4", None) is None


def test_hook_really_runs_a_command(tmp_path):
    out = tmp_path / "ran.txt"
    code = f"import sys, pathlib; pathlib.Path({str(out)!r}).write_text(sys.argv[1])"
    s = {"hooks": {"after_final": f'"{sys.executable}" -c "{code}" "{{slug}}"'}}
    res = hooks.run_after_final(s, tmp_path, "my slug", tmp_path / "x.mp4", None)
    assert res["ok"], res
    assert out.read_text("utf-8") == "my slug"


# ---------------------------------------------------------------- post files (D-16, D-17)


def test_pack_headings_are_one_fixed_set():
    assert postpack.HEADINGS == ("Title", "Description", "Hashtags", "Tags", "Pinned comment", "Link", "Related video",
                                 "Files")
    odd = "# Post pack: x\n\n## Title\na\n\n## Pinned first comment (pin it)\nb\n"
    problems = postpack.heading_problems(odd)
    assert any("Pinned first comment" in p for p in problems) and any("'Pinned comment' is missing" in p for p in problems)


def test_a_hand_edited_pack_is_kept_and_the_new_text_goes_beside_it(tmp_path):
    sdir = pagefix.make_short(tmp_path, "done")
    short = _short(sdir)
    pack = sdir / postpack.PACK
    first = postpack.write_info(sdir, short)
    assert first["changed"] and not first["edited"] and pack.is_file()
    pack.write_text(pack.read_text("utf-8") + "\nmy own line\n", "utf-8")
    again = postpack.write_info(sdir, short)
    assert again["edited"] and again["path"] == sdir / postpack.GENERATED
    assert "my own line" in pack.read_text("utf-8")
    assert "my own line" not in (sdir / postpack.GENERATED).read_text("utf-8")


def test_a_pack_the_plugin_wrote_is_updated_in_place(tmp_path):
    sdir = pagefix.make_short(tmp_path, "done")
    postpack.write_info(sdir, _short(sdir))
    p = sdir / "source" / "short.json"
    d = json.loads(p.read_text("utf-8"))
    d["post"]["title"] = "A new title?"
    p.write_text(json.dumps(d), "utf-8")
    info = postpack.write_info(sdir, _short(sdir))
    assert info["changed"] and not info["edited"]
    assert "A new title?" in (sdir / postpack.PACK).read_text("utf-8")
    assert not (sdir / postpack.GENERATED).exists()


def test_a_pack_with_no_record_counts_as_edited(tmp_path):
    sdir = pagefix.make_short(tmp_path, "done")
    (sdir / "out").mkdir(exist_ok=True)
    (sdir / postpack.PACK).write_text("# written by hand\n", "utf-8")
    assert postpack.write_info(sdir, _short(sdir))["edited"]
    assert (sdir / postpack.PACK).read_text("utf-8") == "# written by hand\n"


def test_write_all_warns_about_edits_and_odd_headings(tmp_path):
    sdir = pagefix.make_short(tmp_path, "done")
    (sdir / "out").mkdir(exist_ok=True)
    (sdir / postpack.PACK).write_text("# x\n\n## Pinned first comment\nhi\n", "utf-8")
    res = postpack.write_all(sdir, _short(sdir), {})
    text = " ".join(res["warnings"])
    assert "edited by hand" in text and "post-pack.generated.md" in text and "Pinned first comment" in text


def test_instagram_file_only_when_instagram_is_a_platform(tmp_path):
    sdir = pagefix.make_short(tmp_path, "done")
    assert postpack.write_instagram(sdir, _short(sdir), {}) is None
    assert not (sdir / postpack.INSTAGRAM).exists()
    info = postpack.write_instagram(sdir, _short(sdir), IG)
    assert info["path"] == sdir / postpack.INSTAGRAM and (sdir / postpack.INSTAGRAM).is_file()


def test_instagram_file_has_the_hand_made_structure(tmp_path):
    sdir = pagefix.make_short(tmp_path, "done")
    p = sdir / "source" / "short.json"
    d = json.loads(p.read_text("utf-8"))
    d["post"].update(title="Why a pendulum? #shorts", link="https://example.com/p/",
                     description="A pendulum swings.\n\nIt keeps time.\n\n▶️ Try it: example.com/p/\n\nSources:\nA: https://x\n\nMade in Blender.\n\n#a #b #shorts",
                     hashtags=["#physics", "#pendulum", "#shorts"], tags=["pendulum", "time"])
    p.write_text(json.dumps(d), "utf-8")
    s = {"post_format": {"platforms": ["instagram"], "title_suffix": " #shorts"}}
    text, warnings = postpack.instagram_markdown(_short(sdir), s)
    lines = text.splitlines()
    assert lines[0].startswith("# Instagram post:") and "no music" in text
    assert [x for x in lines if x.startswith("## ")] == ["## Caption", "## Tags box"]
    caption = text.split("## Caption\n")[1].split("\n## Tags box")[0].strip()
    assert caption == ("Why a pendulum?\n\nA pendulum swings.\n\nIt keeps time.\n\nLink in bio.\n\n"
                       "#physics #pendulum")
    assert text.rstrip().endswith("pendulum, time") and warnings == []


def test_instagram_caption_is_cut_to_the_limit_with_a_warning(tmp_path):
    sdir = pagefix.make_short(tmp_path, "done")
    p = sdir / "source" / "short.json"
    d = json.loads(p.read_text("utf-8"))
    d["post"]["description"] = " ".join(["A sentence that goes on."] * 200)
    p.write_text(json.dumps(d), "utf-8")
    text, warnings = postpack.instagram_markdown(_short(sdir), IG)
    caption = text.split("## Caption\n")[1].split("\n## Tags box")[0].strip()
    assert len(caption) <= postpack.INSTAGRAM_LIMIT and warnings and "2,200" in warnings[0]


def test_instagram_file_is_protected_from_overwrite_too(tmp_path):
    sdir = pagefix.make_short(tmp_path, "done")
    postpack.write_instagram(sdir, _short(sdir), IG)
    ig = sdir / postpack.INSTAGRAM
    ig.write_text("my caption\n", "utf-8")
    info = postpack.write_instagram(sdir, _short(sdir), IG)
    assert info["edited"] and ig.read_text("utf-8") == "my caption\n"
    assert (sdir / postpack.INSTAGRAM_GENERATED).is_file()


def test_cli_post_writes_the_files_and_reports_edits(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    ws = tmp_path / "ws"
    ok(run(home, "settings", "set", "workspace", ws))
    sdir = pagefix.make_short(ws, "done")
    ok(run(home, "settings", "set", "post_format.platforms", '["instagram"]'))
    out = ok(run(home, "post", sdir))
    assert out["instagram"].endswith("instagram-post.md") and (sdir / "out" / "post-pack.md").is_file()
    (sdir / "out" / "post-pack.md").write_text("by hand\n", "utf-8")
    out = ok(run(home, "post", sdir))
    assert out["edited"] == ["out/post-pack.md"] and out["pack"].endswith("post-pack.generated.md")
    assert any("edited by hand" in w for w in out["warnings"])


# ---------------------------------------------------------------- thumbs freshness (D-15)


def test_thumbs_are_fresh_only_for_the_same_inputs_and_the_final(tmp_path):
    sdir = pagefix.make_short(tmp_path, "done")
    p = {"thumbnail": {"title": "A", "heroes": [], "chosen": None}}
    key = thumbs.inputs_key("h1", p)
    assert not thumbs.is_fresh(sdir, key)
    rows = [{"n": 1, "source": "final", "files": thumbs.files_for(1)}]
    thumbs.put_candidates(sdir, rows)
    for rel in thumbs.files_for(1).values():
        (sdir / rel).parent.mkdir(parents=True, exist_ok=True)
        (sdir / rel).write_bytes(b"x")
    thumbs.stamp(sdir, key)
    assert thumbs.is_fresh(sdir, key)
    assert thumbs.is_fresh(sdir, thumbs.inputs_key("h1", {"thumbnail": {"title": "A", "heroes": [], "chosen": 1}}))
    assert not thumbs.is_fresh(sdir, thumbs.inputs_key("h2", p))  # the timing changed
    assert not thumbs.is_fresh(sdir, thumbs.inputs_key("h1", {"thumbnail": {"title": "B", "heroes": []}}))
    thumbs.put_candidates(sdir, [{**rows[0], "source": "preview"}])
    assert not thumbs.is_fresh(sdir, key)  # made from preview frames


# ---------------------------------------------------------------- rename (D-18)


def test_slug_words_missing_from_the_title():
    assert workspace.slug_words_missing("22-how-does-a-knot-lock", "How does a lock work?") == ["knot"]
    assert workspace.slug_words_missing("22-pendulum", "Why a pendulum keeps time") == []
    assert workspace.slug_words_missing("22-why-doesnt-it-fall", "Why doesn't it fall?") == []


def test_validate_warns_when_the_folder_slug_has_a_dropped_word(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    ws = tmp_path / "ws"
    ok(run(home, "settings", "set", "workspace", ws))
    sdir = pagefix.make_short(ws, "done")
    sdir = sdir.rename(sdir.with_name("01-pendulum-knot"))
    out = ok(run(home, "validate", sdir))
    assert any("'knot'" in w and "workspace rename" in w for w in out["warnings"])


def test_workspace_rename_renames_the_folder_and_repoints_its_paths(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    ws = tmp_path / "ws"
    ok(run(home, "settings", "set", "workspace", ws))
    sdir = pagefix.make_short(ws, "done")
    state = sdir / "state.json"
    data = json.loads(state.read_text("utf-8"))
    data["where"] = str(sdir / "out" / "x.mp4")
    data["elsewhere"] = str(tmp_path / "other" / "x.mp4")
    state.write_text(json.dumps(data), "utf-8")
    out = ok(run(home, "workspace", "rename", "1", "Pendulum Time!"))
    new = ws / "01-pendulum-time"
    assert out["to"] == str(new) and new.is_dir() and not sdir.exists()
    moved = json.loads((new / "state.json").read_text("utf-8"))
    assert moved["where"] == str(new / "out" / "x.mp4") and moved["elsewhere"] == str(tmp_path / "other" / "x.mp4")
    assert any("state.json: where" in c for c in out["changed"])
    assert load_short(new / "source" / "short.json").title  # the title still comes from short.json


def test_workspace_rename_refuses_an_existing_folder_and_a_busy_workspace(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    ws = tmp_path / "ws"
    ok(run(home, "settings", "set", "workspace", ws))
    sdir = pagefix.make_short(ws, "done")
    (ws / "01-taken").mkdir()
    bad = run(home, "workspace", "rename", sdir, "taken")
    assert bad.returncode != 0 and "already exists" in bad.stderr and sdir.is_dir()
    from bsa import gpulock

    lock = gpulock.lock_path(ws)
    assert gpulock.acquire(lock, short="01-pendulum", command="render", wait=False)  # held by this test's process
    try:
        busy = run(home, "workspace", "rename", sdir, "calm")
    finally:
        gpulock.release(lock)
    assert busy.returncode != 0 and "GPU is in use" in busy.stderr and sdir.is_dir()


# ---------------------------------------------------------------- the final's after-steps (D-14, D-15, D-19)


def _with_thumbnail(sdir):
    p = sdir / "source" / "short.json"
    d = json.loads(p.read_text("utf-8"))
    d["post"]["thumbnail"] = {"title": "A pendulum", "heroes": []}
    p.write_text(json.dumps(d), "utf-8")
    return _short(sdir)


def test_final_makes_the_thumbnails_and_says_to_choose(tmp_path, monkeypatch):
    import bsa_blender

    sdir = pagefix.make_short(tmp_path, "done")
    short = _with_thumbnail(sdir)
    seen = []
    monkeypatch.setattr(bsa_blender, "cmd_thumbs", lambda a: seen.append(a) or {"candidates": [1, 2], "warnings": ["w"]})
    warnings = []
    res = bsa_blender._final_thumbs(sdir, short, {}, "h1", warnings)
    assert len(seen) == 1 and seen[0].only is None
    assert res["made"] and res["candidates"] == 2 and res["chosen"] is None and "thumbnail --choice N" in res["choose"]
    assert "w" in warnings and res["choose"] in warnings


def test_final_keeps_fresh_thumbnails_and_skips_a_short_without_thumbnail_text(tmp_path, monkeypatch):
    import bsa_blender

    sdir = pagefix.make_short(tmp_path, "done")
    short = _short(sdir)
    monkeypatch.setattr(bsa_blender, "cmd_thumbs", lambda a: pytest.fail("must not run"))
    p = {"thumbnail": {"title": "A pendulum", "heroes": []}}
    assert bsa_blender._final_thumbs(sdir, short, {}, "h1", []) is None  # no thumbnail text: skipped
    short = _with_thumbnail(sdir)
    thumbs.put_candidates(sdir, [{"n": 1, "source": "final", "files": thumbs.files_for(1)}])
    for rel in thumbs.files_for(1).values():
        (sdir / rel).parent.mkdir(parents=True, exist_ok=True)
        (sdir / rel).write_bytes(b"x")
    thumbs.stamp(sdir, thumbs.inputs_key("h1", {**p, "thumbnail": {"title": "A pendulum", "heroes": []}}))
    res = bsa_blender._final_thumbs(sdir, short, {}, "h1", [])
    assert res["made"] is False and "current" in res["note"]


def test_a_thumbs_failure_is_a_warning_not_a_failed_final(tmp_path, monkeypatch):
    import bsa_blender

    sdir = pagefix.make_short(tmp_path, "done")
    short = _with_thumbnail(sdir)

    def boom(a):
        raise bsa_blender.CliError("Render the short first")

    monkeypatch.setattr(bsa_blender, "cmd_thumbs", boom)
    warnings = []
    res = bsa_blender._final_thumbs(sdir, short, {}, "h1", warnings)
    assert res["made"] is False and warnings == ["thumbnails not made: Render the short first"]


def test_after_final_delivers_then_runs_the_hook_and_a_failing_hook_only_warns(tmp_path, monkeypatch):
    import bsa_blender

    sdir = pagefix.make_short(tmp_path, "done")
    mp4 = _final(sdir)
    short = _short(sdir)
    seen = {}

    def hook(s, sdir_, slug, mp4_, deliver_dir, **kw):
        seen["deliver_dir"] = deliver_dir
        seen["delivered"] = (sdir / "final" / "pendulum.mp4").is_file()  # the copy is there before the hook runs
        return {"ok": False, "exit_code": 2, "output": "nope", "warning": "after-final hook exited 2 (the final is fine)"}

    monkeypatch.setattr(hooks, "run_after_final", hook)
    out = {"mp4": str(mp4)}
    s = {"render": {"deliver_dir": "final"}, "hooks": {"after_final": "x"}}
    warnings = bsa_blender._after_final(sdir, short, out, s, "h1")
    assert seen == {"deliver_dir": sdir / "final", "delivered": True}
    assert out["deliver"]["files"] and out["hook"]["exit_code"] == 2
    assert "after-final hook exited 2 (the final is fine)" in warnings
