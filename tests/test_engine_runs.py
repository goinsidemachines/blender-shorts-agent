"""Issue 16: the render engine per short (short.json) and per run (--engine / --tag), and the comparison of what
each engine's runs measured."""

import json
import types
from pathlib import Path

import pytest

from bsa import engine_runs as er
from bsa.short import validate
from tests import test_cli
from tests.test_cli import _fake_edit, _rendered, ok, run
from tests.test_short import _short

home, workspace, pendulum = test_cli.home, test_cli.workspace, test_cli.pendulum  # the CLI tests' fixtures


# --------------------------------------------------------------------------- choosing the run


def test_no_options_or_the_approved_engine_is_the_approved_run():
    assert er.resolve("cycles") == er.EngineRun("cycles")
    assert er.resolve("cycles", "cycles") == er.EngineRun("cycles")
    assert er.resolve(None) == er.EngineRun(None)
    assert er.EngineRun("cycles").suffix == "" and er.EngineRun("cycles").flags() == ""


def test_another_engine_is_tagged_with_its_name():
    run_ = er.resolve("cycles", "eevee")
    assert run_ == er.EngineRun("eevee", "eevee")
    assert run_.suffix == "-eevee" and run_.flags() == " --engine eevee --tag eevee"


def test_a_tag_makes_a_side_run_even_with_the_approved_engine():
    assert er.resolve("cycles", None, "Test-1") == er.EngineRun("cycles", "test-1")
    assert er.resolve("cycles", "eevee", "fast") == er.EngineRun("eevee", "fast")


@pytest.mark.parametrize(("engine", "tag"), [("workbench", None), (None, "preview"), (None, "final"),
                                             (None, "bad tag"), (None, "../x"), (None, "")])  # fmt: skip
def test_bad_engines_and_tags_are_refused(engine, tag):
    with pytest.raises(ValueError):
        er.resolve("cycles", engine, tag)


def test_side_runs_never_share_the_approved_paths(tmp_path):
    approved, side = er.EngineRun("cycles"), er.EngineRun("eevee", "eevee")
    for preset in ("preview", "final"):
        assert er.frames_root(tmp_path, preset, approved) == tmp_path / "frames" / preset
        assert er.frames_root(tmp_path, preset, side) == tmp_path / "frames" / f"{preset}-eevee"
        assert er.frame_checks_path(tmp_path, preset, side).name == f"frame-checks-{preset}-eevee.json"
    assert er.mp4_path(tmp_path, "pend", "final", approved).name == "pend.mp4"
    assert er.mp4_path(tmp_path, "pend", "preview", approved).name == "pend-preview.mp4"
    assert er.mp4_path(tmp_path, "pend", "preview", side).name == "pend-preview-eevee.mp4"
    assert er.mp4_path(tmp_path, "pend", "final", side).name == "pend-final-eevee.mp4"


def test_the_engine_comes_from_the_stage_then_short_json_then_settings(tmp_path):
    assert er.pick("cycles", "eevee", "eevee") == ("cycles", "engine stage")
    assert er.pick(None, "eevee", "cycles") == ("eevee", "short.json")
    assert er.pick(None, None, "cycles") == ("cycles", "settings")
    assert er.pick(None, None, "ask") == (None, None)
    (tmp_path / "source").mkdir()
    assert er.short_engine(tmp_path) is None
    (tmp_path / "source" / "short.json").write_text(json.dumps({"render": {"engine": "eevee"}}), encoding="utf-8")
    assert er.short_engine(tmp_path) == "eevee"


# --------------------------------------------------------------------------- short.json


def test_short_json_render_engine_is_validated():
    assert validate(_short(render={"engine": "eevee"})) == []
    assert validate(_short(render={})) == []
    assert any("render.engine" in e for e in validate(_short(render={"engine": "workbench"})))
    assert any("render has unknown keys" in e for e in validate(_short(render={"engine": "cycles", "samples": 9})))
    assert any("render must be" in e for e in validate(_short(render="eevee")))


# --------------------------------------------------------------------------- what the runs measured


def test_the_comparison_reports_only_what_was_measured(tmp_path):
    cyc, eev = er.EngineRun("cycles"), er.EngineRun("eevee", "eevee")
    assert er.record_render(tmp_path, "preview", cyc, frames=0, seconds=0) is None  # all kept: nothing measured
    assert er.compare(tmp_path) == []
    er.record_render(tmp_path, "preview", cyc, frames=10, seconds=50.0, clock=lambda: 1.0)
    er.record_render(tmp_path, "preview", eev, frames=4, seconds=2.0, clock=lambda: 2.0)
    mp4 = tmp_path / "out" / "x-preview-eevee.mp4"
    er.record_mp4(tmp_path, "preview", eev, mp4)
    rows = er.compare(tmp_path, "preview")
    assert [(r["engine"], r["tag"], r["approved"], r["s_per_frame"], r["frames_measured"]) for r in rows] == [
        ("cycles", None, True, 5.0, 10), ("eevee", "eevee", False, 0.5, 4)]
    assert rows[1]["mp4"] is None  # recorded, but the file is not there
    mp4.parent.mkdir()
    mp4.write_bytes(b"mp4")
    assert er.compare(tmp_path, "preview")[1]["mp4"] == str(mp4)
    er.record_render(tmp_path, "preview", eev, frames=0, seconds=0)  # a later run that kept every frame
    assert er.compare(tmp_path, "preview")[1]["s_per_frame"] == 0.5
    assert er.compare(tmp_path, "final") == []


# --------------------------------------------------------------------------- through the commands


def test_cli_engine_proposes_short_json_engine_and_shows_runs(home, pendulum):
    out = ok(run(home, "engine", pendulum.name))
    assert out["engine"] is None and out["runs"] == []
    spec = json.loads((pendulum / "source" / "short.json").read_text(encoding="utf-8"))
    spec["render"] = {"engine": "eevee"}
    (pendulum / "source" / "short.json").write_text(json.dumps(spec), encoding="utf-8")
    out = ok(run(home, "engine", pendulum.name))
    assert (out["engine"], out["from"], out["approved"]) == ("eevee", "short.json", None)
    ok(run(home, "state", pendulum.name, "done", "engine", "--info", '{"engine": "cycles"}'))
    er.record_render(pendulum, "preview", er.EngineRun("cycles"), frames=2, seconds=8.0)
    out = ok(run(home, "engine", pendulum.name))
    assert (out["engine"], out["from"], out["approved"], out["short_json"]) == ("cycles", "engine stage", "cycles",
                                                                                "eevee")  # fmt: skip
    assert out["runs"][0]["s_per_frame"] == 4.0


def test_bsa_blender_engine_falls_back_to_short_json(home, pendulum, monkeypatch):
    import bsa_blender

    monkeypatch.setenv("BSA_HOME", str(home))
    assert bsa_blender._engine(pendulum) is None
    spec = json.loads((pendulum / "source" / "short.json").read_text(encoding="utf-8"))
    spec["render"] = {"engine": "eevee"}
    (pendulum / "source" / "short.json").write_text(json.dumps(spec), encoding="utf-8")
    assert bsa_blender._engine(pendulum) == "eevee"
    ok(run(home, "state", pendulum.name, "done", "engine", "--info", '{"engine": "cycles"}'))
    assert bsa_blender._engine(pendulum) == "cycles"


def _approve(home, short: Path, engine: str) -> None:
    ok(run(home, "state", short.name, "done", "engine", "--info", json.dumps({"engine": engine})))


def test_a_side_edit_writes_its_own_mp4_and_leaves_the_approved_run(home, pendulum, monkeypatch, capsys):
    import bsa_blender

    _approve(home, pendulum, "cycles")
    _rendered(home, pendulum, "preview", engine="cycles")
    tl = _rendered(home, pendulum, "preview-eevee", engine="eevee")  # the side run's frames, in their own folder
    for s in tl["shots"]:  # stamped as the side render stamps them: with the real preset
        stamp_path = pendulum / "frames" / "preview-eevee" / s["id"] / ".render.json"
        stamp = json.loads(stamp_path.read_text(encoding="utf-8"))
        stamp_path.write_text(json.dumps({**stamp, "preset": "preview"}), encoding="utf-8")
    approved = pendulum / "out" / "pendulum-preview.mp4"
    approved.parent.mkdir(parents=True, exist_ok=True)
    approved.write_bytes(b"approved")
    seen = {}
    probe = {"width": 1080, "height": 1920, "fps": 30.0, "frames": tl["n_frames"], "has_audio": False}
    _fake_edit(monkeypatch, seen, probe=lambda mp4: probe)
    built = {}
    fake = __import__("sys").modules["bsa.blender.edit"]
    inner = fake.build_edit

    def build_edit(short, tl_, frames_root, mix, out_mp4, *a, **kw):
        built["frames_root"], built["out_mp4"] = Path(frames_root), Path(out_mp4)
        return inner(short, tl_, frames_root, mix, out_mp4, *a, **kw)

    monkeypatch.setattr(fake, "build_edit", build_edit)
    monkeypatch.setattr(fake, "encode", lambda scene, progress=None: built["out_mp4"])
    monkeypatch.setenv("BSA_HOME", str(home))
    assert bsa_blender.main(["edit", "pendulum", "--preset", "preview", "--engine", "eevee"]) == 0, \
        capsys.readouterr().err
    assert built["frames_root"] == pendulum / "frames" / "preview-eevee"
    assert built["out_mp4"] == pendulum / "out" / "pendulum-preview-eevee.mp4"
    assert approved.read_bytes() == b"approved"
    state = ok(run(home, "state", pendulum.name))
    assert state["stages"]["engine"]["engine"] == "cycles" and "preview" not in state["stages"]
    rows = er.compare(pendulum, "preview")
    assert [(r["engine"], r["tag"]) for r in rows] == [("eevee", "eevee")]


def test_a_side_edit_refuses_frames_of_the_approved_engine(home, pendulum, monkeypatch, capsys):
    import bsa_blender

    _approve(home, pendulum, "cycles")
    _rendered(home, pendulum, "preview", engine="cycles")
    _fake_edit(monkeypatch, {})
    monkeypatch.setenv("BSA_HOME", str(home))
    assert bsa_blender.main(["edit", "pendulum", "--preset", "preview", "--engine", "eevee"]) == 1
    assert "no preview frames" in capsys.readouterr().err


def test_a_side_frame_check_keeps_its_own_findings_and_redo_commands(home, pendulum, monkeypatch):
    import bsa_blender
    from bsa.image_checks import Finding

    _approve(home, pendulum, "cycles")
    tl = _rendered(home, pendulum, "preview-eevee", engine="eevee")
    for s in tl["shots"]:
        p = pendulum / "frames" / "preview-eevee" / s["id"] / ".render.json"
        p.write_text(json.dumps({**json.loads(p.read_text(encoding="utf-8")), "preset": "preview"}), encoding="utf-8")
    monkeypatch.setenv("BSA_HOME", str(home))
    short = bsa_blender._load(pendulum)

    def frame_checks(frames, load=None, progress=None, **_kw):
        return [Finding("stuck_frame", "warning", None, int(frames[1].stem), "frame identical")]

    class Run:
        def progress(self, *a, **kw):
            pass

    args = types.SimpleNamespace(preset="preview", engine="eevee", tag=None)
    bsa_blender._cmd_frame_check(args, pendulum, short, Run(), frame_checks, None)
    assert not (pendulum / "review" / "frame-checks-preview.json").exists()
    data = json.loads((pendulum / "review" / "frame-checks-preview-eevee.json").read_text(encoding="utf-8"))
    assert data["findings"][0]["redo"].endswith("--engine eevee --tag eevee")
    assert data["redo_all"].endswith("--redo-flagged --engine eevee --tag eevee")


def test_render_and_preview_take_engine_and_tag():
    import bsa_blender

    p = bsa_blender.build_parser()
    for cmd in (["render", "x", "--preset", "preview"], ["preview", "x"], ["edit", "x", "--preset", "final"],
                ["frame-check", "x", "--preset", "preview"], ["grab", "x", "--preset", "preview", "--sheet"]):
        args = p.parse_args([*cmd, "--engine", "eevee", "--tag", "fast"])
        assert (args.engine, args.tag) == ("eevee", "fast")
    with pytest.raises(SystemExit):
        p.parse_args(["render", "x", "--preset", "preview", "--engine", "workbench"])


def test_a_side_render_uses_its_own_folder_and_records_what_it_measured(home, pendulum, monkeypatch, capsys):
    """BLENDER render --engine eevee next to approved Cycles frames: the side run renders into frames/preview-eevee
    with Eevee, the Cycles frames stay, and its measured seconds per frame go into the comparison (Blender's render
    and its handlers are stubbed)."""
    import sys
    import time

    import bsa_blender

    _approve(home, pendulum, "cycles")
    _rendered(home, pendulum, "preview", engine="cycles")
    approved = {p: p.read_bytes() for p in (pendulum / "frames" / "preview").rglob("*") if p.is_file()}
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
        calls.append((Path(folder), engine))
        Path(folder).mkdir(parents=True, exist_ok=True)
        for f in range(f0, f1 + 1):
            trackers[-1].frame_started(f)
            time.sleep(0.002)
            (Path(folder) / f"{f:04d}.png").write_bytes(b"png")
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
    monkeypatch.setattr(bsa_blender, "_write_anchors", lambda *a, **kw: {})
    monkeypatch.setenv("BSA_HOME", str(home))
    assert bsa_blender.main(["render", "pendulum", "--preset", "preview", "--engine", "eevee"]) == 0, \
        capsys.readouterr().err
    out = json.loads(capsys.readouterr().out.split("BSA_RESULT ", 1)[1])
    assert calls and all(folder.parent == pendulum / "frames" / "preview-eevee" and engine == "eevee"
                         for folder, engine in calls)  # fmt: skip
    assert {p: p.read_bytes() for p in (pendulum / "frames" / "preview").rglob("*") if p.is_file()} == approved
    assert (out["engine"], out["tag"]) == ("eevee", "eevee") and out["s_per_frame"] > 0
    (row,) = out["engines"]
    side_frames = list((pendulum / "frames" / "preview-eevee").rglob("*.png"))
    assert row["engine"] == "eevee" and row["s_per_frame"] > 0 and row["frames_measured"] == len(side_frames)
    assert ok(run(home, "state", pendulum.name))["stages"]["engine"]["engine"] == "cycles"
