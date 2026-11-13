"""`BLENDER render` in chunks, with Blender's render stubbed: a child renders at most --max-frames fresh frames and says
whether more are left; the parent starts children until none is; a crash resumes from the frames on disk."""

import json
import sys
import types
from pathlib import Path

import pytest

from tests import test_cli
from tests.test_cli import ok, run

home, workspace, pendulum = test_cli.home, test_cli.workspace, test_cli.pendulum  # the CLI tests' fixtures


@pytest.fixture
def stubbed(home, pendulum, monkeypatch):
    """bsa_blender with Blender's render, scene and handlers stubbed; `calls` lists each render_range (shot, f0, f1)."""
    import bsa_blender

    ok(run(home, "timeline", pendulum.name))
    calls, trackers = [], []

    class Watch:
        cancelled = False

        def __init__(self, tracker, short_dir, preset):
            trackers.append(tracker)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def on_start(self, scene):
            pass

    def render_range(scene, f0, f1, folder, preset, engine=None, on_start=None):
        calls.append((Path(folder).name, f0, f1))
        Path(folder).mkdir(parents=True, exist_ok=True)
        for f in range(f0, f1 + 1):
            png = Path(folder) / f"{f:04d}.png"
            if png.exists() and png.stat().st_size > 0:
                continue  # skipping finished frames, as Blender does with overwrite off
            trackers[-1].frame_started(f)
            png.write_bytes(test_cli._png())
            trackers[-1].frame_written(f)
        return [Path(folder) / f"{f:04d}.png" for f in range(f0, f1 + 1)]

    def scene(data, short_, sid, tl_hash):
        shot = next(s for s in short_.shots if s.id == sid)
        return shot, types.SimpleNamespace(name=shot.scene)

    monkeypatch.setitem(sys.modules, "bsa.blender.render_watch", types.SimpleNamespace(Watch=Watch))
    monkeypatch.setitem(sys.modules, "bsa.blender.render", types.SimpleNamespace(render_range=render_range))
    monkeypatch.setattr(bsa_blender, "_open_blend", lambda sdir: types.SimpleNamespace(filepath=""))
    monkeypatch.setattr(bsa_blender, "_scene", scene)
    monkeypatch.setattr(bsa_blender, "_fingerprints", lambda *a, **kw: None)
    monkeypatch.setattr(bsa_blender, "_check_near_objects", lambda *a: None)
    monkeypatch.setattr(bsa_blender, "_write_anchors", lambda *a, **kw: {"s01": {}})
    monkeypatch.setenv("BSA_HOME", str(home))

    def no_child(*a, **k):  # never start a real Blender from a test unless it stands one in
        raise AssertionError("started a child Blender")

    monkeypatch.setattr(bsa_blender, "_spawn_chunk", no_child)
    tl = json.loads((pendulum / "source" / "timeline.json").read_text(encoding="utf-8"))
    return types.SimpleNamespace(calls=calls, shots=tl["shots"], total=sum(s["f1"] - s["f0"] + 1 for s in tl["shots"]),
                                 sdir=pendulum, home=home)


def _on_disk(env, preset="preview"):
    return sorted(p for p in (env.sdir / "frames" / preset).rglob("*.png") if p.stat().st_size > 0)


def _chunk(capsys, *extra):
    """One child as the parent runs it; its result dict."""
    import bsa_blender

    capsys.readouterr()
    assert bsa_blender.main(["render", "pendulum", "--preset", "preview", "--in-chunk", *extra]) == 0
    out = capsys.readouterr().out
    return json.loads(out.split("BSA_RESULT ", 1)[1]), out


def test_a_chunk_renders_at_most_its_frames_and_says_more_are_left(stubbed, capsys):
    res, out = _chunk(capsys, "--max-frames", "40")
    assert res["more"] is True and res["fresh_frames"] == 40 and len(_on_disk(stubbed)) == 40
    assert res["anchors"] is None and res["shots"][-1]["partial"] is True
    assert sum(r["rendered"] for r in res["shots"]) == 40
    assert "BSA_RENDER " in out  # its live status goes to the parent as lines, not to a file


def test_a_chunk_writes_no_status_file_and_takes_no_lock(stubbed, capsys):
    _chunk(capsys, "--max-frames", "40")
    assert not list((stubbed.sdir / "review").glob("render-status-*.json"))
    assert not (stubbed.sdir.parent / ".gpu.lock").exists()


def test_chunks_in_a_row_render_every_frame_once_and_the_last_places_the_anchors(stubbed, capsys):
    results = []
    while True:
        res, _ = _chunk(capsys, "--max-frames", "70")
        results.append(res)
        if not res["more"]:
            break
        assert len(results) < 50
    assert len(_on_disk(stubbed)) == stubbed.total
    assert sum(r["fresh_frames"] for r in results) == stubbed.total  # no frame rendered twice
    assert all(r["anchors"] is None for r in results[:-1]) and results[-1]["anchors"] == {"s01": {}}


def test_a_chunk_after_a_crash_skips_finished_frames_and_redoes_the_empty_one(stubbed, capsys):
    _chunk(capsys, "--max-frames", "40")
    victim = _on_disk(stubbed)[-1]
    victim.write_bytes(b"")  # the frame the crash was writing
    res, _ = _chunk(capsys, "--max-frames", "1000")
    assert res["more"] is False and len(_on_disk(stubbed)) == stubbed.total
    assert res["fresh_frames"] == stubbed.total - 39  # the 39 finished frames were kept
    assert victim.stat().st_size > 0


def test_a_chunk_that_finds_everything_rendered_renders_nothing(stubbed, capsys):
    _chunk(capsys, "--max-frames", "100000")
    stubbed.calls.clear()
    res, _ = _chunk(capsys, "--max-frames", "10")
    assert res["more"] is False and res["fresh_frames"] == 0 and stubbed.calls == []


def test_a_long_render_runs_as_child_chunks_under_one_status(stubbed, home, monkeypatch, capsys):
    """The parent runs the children one after another; each is the very same code path (`--in-chunk`)."""
    import bsa_blender

    ok(run(home, "settings", "set", "render.chunk_frames", 90))
    children = []

    def spawn(args, sdir, number, size, run_, started, reasons=None):
        children.append((number, size))
        monkeypatch.setattr(bsa_blender._ChunkReport, "set_render", lambda self, st: run_.set_render(st))  # the lines
        child = types.SimpleNamespace(short=args.short, preset=args.preset, shot=None, engine=None, tag=None,
                                      frames=None, redo_flagged=False, no_wait=False, max_wait=None,
                                      max_frames=size, in_chunk=True)  # fmt: skip
        return bsa_blender.cmd_render(child)

    monkeypatch.setattr(bsa_blender, "_spawn_chunk", spawn)
    assert bsa_blender.main(["render", "pendulum", "--preset", "preview"]) == 0
    out = json.loads(capsys.readouterr().out.split("BSA_RESULT ", 1)[1].split("\n", 1)[0])
    want = -(-stubbed.total // 90)
    assert [n for n, _ in children] == list(range(1, want + 1)) and {s for _, s in children} == {90}
    assert out["chunks"] == want and out["chunk_frames"] == 90 and out["anchors"] == {"s01": {}}
    assert sum(r["rendered"] for r in out["shots"]) == stubbed.total and len(_on_disk(stubbed)) == stubbed.total
    assert [r["shot"] for r in out["shots"]] == [s["id"] for s in stubbed.shots]
    status = json.loads((stubbed.sdir / "review" / "render-status-preview.json").read_text(encoding="utf-8"))
    assert status["state"] == "done" and status["render"]["frames"]["done"] == stubbed.total


def test_a_render_that_fits_one_chunk_or_has_chunks_off_runs_in_one_process(stubbed, home, monkeypatch, capsys):
    import bsa_blender

    ok(run(home, "settings", "set", "render.chunk_frames", stubbed.total))  # fits exactly: one process
    assert bsa_blender.main(["render", "pendulum", "--preset", "preview"]) == 0
    out = json.loads(capsys.readouterr().out.split("BSA_RESULT ", 1)[1].split("\n", 1)[0])
    assert "chunks" not in out and len(_on_disk(stubbed)) == stubbed.total
    for f in _on_disk(stubbed):
        f.unlink()
    ok(run(home, "settings", "set", "render.chunk_frames", "off"))
    assert bsa_blender.main(["render", "pendulum", "--preset", "preview"]) == 0
    assert len(_on_disk(stubbed)) == stubbed.total


def test_a_redo_of_some_frames_is_never_chunked(stubbed, home, monkeypatch, capsys):
    import bsa_blender

    ok(run(home, "settings", "set", "render.chunk_frames", "off"))
    assert bsa_blender.main(["render", "pendulum", "--preset", "preview"]) == 0
    capsys.readouterr()
    ok(run(home, "settings", "set", "render.chunk_frames", 2))
    first = stubbed.shots[0]
    assert bsa_blender.main(["render", "pendulum", "--preset", "preview", "--shot", first["id"], "--frames",
                             f"{first['f0']}-{first['f0'] + 9}"]) == 0


def test_a_chunk_child_is_started_like_the_side_check(monkeypatch):
    import bsa_blender

    monkeypatch.setitem(sys.modules, "bpy", types.SimpleNamespace(app=types.SimpleNamespace(binary_path="/b/blender")))
    args = types.SimpleNamespace(preset="final", shot="s02", engine="cycles", tag="fast")
    cmd = bsa_blender._chunk_command(args, Path("/ws/pendulum"), 250)
    assert cmd[:6] == ["/b/blender", "-b", "--factory-startup", "--python-exit-code", "1", "--python"]
    assert cmd[cmd.index("--") + 1 :] == ["render", "/ws/pendulum", "--preset", "final", "--max-frames", "250",
                                          "--in-chunk", "--shot", "s02", "--engine", "cycles", "--tag", "fast"]


def test_a_real_child_process_reports_status_and_a_result(tmp_path, monkeypatch, capsys):
    """_spawn_chunk against a stand-in child: status lines reach the run (with the parent as the writer), stderr is
    echoed with the chunk's number, and the result comes back; a child that dies says why."""
    import bsa_blender

    class Run:
        def __init__(self):
            self.renders = []

        def set_render(self, status):
            self.renders.append(status)

    def command(code):
        return lambda args, sdir, size: [sys.executable, "-c", code]

    good = ("import json, sys\n"
            "print('BSA_RENDER ' + json.dumps({'state': 'running', 'pid': 1, 'frames': {'done': 3}}), flush=True)\n"
            "print('chunk says hi', file=sys.stderr)\n"
            "print('BSA_RESULT ' + json.dumps({'more': False, 'fresh_frames': 3}))\n")
    monkeypatch.setattr(bsa_blender, "_chunk_command", command(good))
    run_ = Run()
    res = bsa_blender._spawn_chunk(types.SimpleNamespace(), tmp_path, 2, 10, run_, 100.0)
    assert res == {"more": False, "fresh_frames": 3}
    (status,) = run_.renders
    import os

    assert status["pid"] == os.getpid() and status["started"] == 100.0 and status["chunk"] == 2
    assert "[chunk 2] chunk says hi" in capsys.readouterr().err
    monkeypatch.setattr(bsa_blender, "_chunk_command",
                        command("import sys; print('Killed: memory guard', file=sys.stderr); sys.exit(137)"))
    with pytest.raises(bsa_blender.CliError, match="^Killed: memory guard$"):
        bsa_blender._spawn_chunk(types.SimpleNamespace(), tmp_path, 1, 10, Run(), 1.0)
