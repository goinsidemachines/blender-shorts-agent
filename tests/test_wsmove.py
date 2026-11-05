"""Moving the workspace: what refuses a move, which stored paths it repoints, and what it checks after a copy."""

import json
import os
from pathlib import Path

from bsa import gpulock, wsmove


def test_move_refuses_inside_itself_and_the_same_folder(tmp_path):
    old = tmp_path / "Shorts"
    old.mkdir()
    (old / "01-a").mkdir()
    free = {"server": None, "running": False, "gpu": None}

    if os.name == "nt":  # D:\Shorts and d:\shorts are one folder on Windows
        assert "same folder" in wsmove.refusal(old, tmp_path / "SHORTS", **free)
    assert "same folder" in wsmove.refusal(old, type(old)(str(old) + os.sep), **free)
    assert "same folder" in wsmove.refusal(type(old)(str(old) + os.sep), old, **free)
    assert "inside the workspace" in wsmove.refusal(old, old / "sub", **free)
    assert "inside the workspace" in wsmove.refusal(old, old / "01-a" / "x", **free)
    assert "inside the new folder" in wsmove.refusal(old, tmp_path, **free)
    assert "inside the new folder" in wsmove.refusal(old / "01-a", old, **free)

    new = tmp_path / "Moved"
    assert wsmove.refusal(old, new, **free) is None  # missing
    new.mkdir()
    assert wsmove.refusal(old, new, **free) is None  # empty
    (new / "x.txt").write_text("x", encoding="utf-8")
    assert "not empty" in wsmove.refusal(old, new, **free)

    other = tmp_path / "Other"
    assert wsmove.refusal(old, other, server={"pid": 4321}, running=False, gpu=None) == (
        "the review page server for this workspace is running (pid 4321): close its tabs and let it stop, then move"
    )
    assert wsmove.refusal(old, other, server=None, running=True, gpu=None) == (
        "a render or another job is running in this workspace: wait for it to finish"
    )
    gpu = {"pid": 99, "short": "pendulum", "command": "render"}
    text = wsmove.refusal(old, other, server=None, running=False, gpu=gpu)
    assert gpulock.describe_holder(gpu) in text
    # the same folder is named first, before anything live
    assert "same folder" in wsmove.refusal(old, old, server={"pid": 1}, running=True, gpu=gpu)


def test_under_and_repoint_ignore_case_and_trailing_separators(tmp_path):
    old, new = tmp_path / "Shorts", tmp_path / "Moved"
    assert wsmove.under(str(old), old)
    assert wsmove.under(str(old) + os.sep, old)
    assert wsmove.under(str(old / "01-a" / "logo.png"), old)
    assert not wsmove.under(str(tmp_path / "Shorts2" / "x"), old)  # a sibling sharing the prefix
    assert not wsmove.under("hello", old)
    assert not wsmove.under("01-a/logo.png", old)  # relative text is never a path under the root
    assert wsmove.repoint(str(old / "01-a" / "logo.png"), old, new) == str(new / "01-a" / "logo.png")
    assert wsmove.repoint(str(old), old, new) == str(new)
    assert wsmove.repoint(str(tmp_path / "elsewhere.png"), old, new) is None
    if os.name == "nt":
        assert wsmove.repoint(str(old).upper() + "\\01-a\\Logo.PNG", old, new) == str(new / "01-a" / "Logo.PNG")


def test_rewrite_repoints_only_paths_under_the_old_root(tmp_path):
    old, new = tmp_path / "Shorts", tmp_path / "Moved"
    s = {
        "workspace": str(old),
        "blender_exe": str(tmp_path / "Blender" / "blender.exe"),
        "handle": "someone",
        "closing": {"image": str(old / "brand" / "end.png"), "voice_line": "Thanks for watching"},
        "brand": {"logo": {"file": str(old / "brand" / "logo.png")}, "mode": "brand"},
        "count": 3,
    }
    out, keys = wsmove.rewrite(s, old, new)
    assert keys == ["workspace", "closing.image", "brand.logo.file"]
    assert out["workspace"] == str(new)
    assert out["closing"]["image"] == str(new / "brand" / "end.png")
    assert out["brand"]["logo"]["file"] == str(new / "brand" / "logo.png")
    assert out["blender_exe"] == s["blender_exe"]
    assert out["closing"]["voice_line"] == "Thanks for watching"
    assert out["handle"] == "someone" and out["count"] == 3 and out["brand"]["mode"] == "brand"
    assert s["workspace"] == str(old)  # the input is not changed in place

    lists, keys = wsmove.rewrite({"files": ["x", str(old / "a.png")]}, old, new)
    assert lists == {"files": ["x", str(new / "a.png")]} and keys == ["files[1]"]


def test_size_mismatches_and_leftovers(tmp_path):
    old, new = tmp_path / "Shorts", tmp_path / "Moved"
    (old / "01-a" / "review").mkdir(parents=True)
    (old / "01-a" / "blend").mkdir(parents=True)
    (old / "01-a" / "frames.bin").write_bytes(b"x" * 100)
    (old / "01-a" / "note.txt").write_text("hello", encoding="utf-8")
    (old / "01-a" / "gone.txt").write_text("hello", encoding="utf-8")
    (old / "top.json").write_text("{}", encoding="utf-8")
    import shutil

    shutil.copytree(old, new)
    assert wsmove.size_mismatches(old, new) == []
    (new / "01-a" / "frames.bin").write_bytes(b"x" * 40)  # truncated
    (new / "01-a" / "gone.txt").unlink()
    assert wsmove.size_mismatches(old, new) == ["01-a/frames.bin", "01-a/gone.txt"]

    (new / "01-a" / "review" / "x.json").write_text(
        json.dumps({"a": {"b": str(old / "01-a" / "y.png")}, "c": "free text"}), encoding="utf-8"
    )
    (new / "01-a" / "blend" / "short.fingerprints.json").write_text(
        json.dumps({"k": str(old / "01-a" / "blend" / "short.blend")}), encoding="utf-8"
    )
    (new / "01-a" / "review" / "bad.json").write_text("{not json", encoding="utf-8")
    assert wsmove.leftovers(new, old) == [("01-a/review/x.json", "a.b")]


def test_live_holder_is_a_live_lock_only(tmp_path):
    path = gpulock.lock_path(tmp_path)
    assert gpulock.live_holder(path) is None  # no lock
    path.write_text(json.dumps({"pid": 2_000_000_000, "short": "a", "command": "render"}), encoding="utf-8")
    assert gpulock.live_holder(path) is None  # its process is gone
    assert gpulock.acquire(path, short="a", command="render", wait=False)
    try:
        assert gpulock.live_holder(path)["pid"] == os.getpid()
    finally:
        gpulock.release(path)


def test_size_mismatches_covers_empty_folders_and_broken_links(tmp_path):
    old, new = tmp_path / "old", tmp_path / "new"
    (old / "a" / "empty").mkdir(parents=True)
    (old / "a" / "f.txt").write_text("x", encoding="utf-8")
    new.mkdir()
    (new / "a").mkdir()
    (new / "a" / "f.txt").write_text("x", encoding="utf-8")
    assert wsmove.size_mismatches(old, new) == ["a/empty/"]  # an empty folder the copy lost
    (new / "a" / "empty").mkdir()
    assert wsmove.size_mismatches(old, new) == []
    try:
        os.symlink(old / "gone.txt", old / "a" / "link.txt")
    except (OSError, NotImplementedError):
        return  # no symlinks without privileges on this machine: the folder part is tested
    assert wsmove.size_mismatches(old, new) == ["a/link.txt"]  # a broken link with nothing at its place
    os.symlink(new / "gone.txt", new / "a" / "link.txt")
    assert wsmove.size_mismatches(old, new) == []


def test_missing_drive_names_a_drive_that_is_not_there(tmp_path):
    assert wsmove.missing_drive(tmp_path) is None
    if os.name == "nt":
        free = next((f"{c}:" for c in "QRSTUVWXYZ" if not os.path.exists(f"{c}:\\")), None)
        if free:
            assert wsmove.missing_drive(Path(free + "\\Shorts")) == free


def test_busy_names_the_server_a_job_or_the_gpu(tmp_path):
    assert wsmove.busy(server=None, running=False, gpu=None) is None
    assert "page server" in wsmove.busy(server={"pid": 7}, running=False, gpu=None)
    assert "job is running" in wsmove.busy(server=None, running=True, gpu=None)
    assert "GPU is in use" in wsmove.busy(server=None, running=False, gpu={"pid": 7, "short": "x", "command": "render"})


def test_plugin_files_are_state_and_review_json_only(tmp_path):
    sdir = tmp_path / "01-x"
    for rel in ("state.json", "state.json.bak", "review/checks.json", "review/camtest/camtest.json",
                "review/notes.txt", "source/short.json", "frames/final/s01/.render.json"):  # fmt: skip
        (sdir / rel).parent.mkdir(parents=True, exist_ok=True)
        (sdir / rel).write_text("{}", encoding="utf-8")
    got = [p.relative_to(sdir).as_posix() for p in wsmove.plugin_files(sdir)]
    assert sorted(got) == ["review/camtest/camtest.json", "review/checks.json", "state.json", "state.json.bak"]


def test_refusal_sees_a_folder_inside_the_workspace_through_a_junction(tmp_path):
    old = tmp_path / "Shorts"
    old.mkdir()
    link = tmp_path / "alias"
    import subprocess

    from bsa import proc

    try:
        if os.name == "nt":
            proc.run(["cmd", "/c", "mklink", "/J", str(link), str(old)], check=True, capture_output=True)
        else:
            os.symlink(old, link)
    except (OSError, subprocess.SubprocessError):
        return
    why = wsmove.refusal(old, link / "inner", server=None, running=False, gpu=None)
    assert why and "inside the workspace" in why
