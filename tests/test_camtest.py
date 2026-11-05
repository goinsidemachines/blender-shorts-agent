import json
import sys
import types
from pathlib import Path

import pytest
from test_cli import home, ok, pendulum, run, workspace  # noqa: F401 - fixtures

from bsa import camtest

HERE = Path(__file__).resolve().parent


def test_percent_rounds_to_even_sizes():
    assert camtest.percent(0.33) == 35
    assert camtest.percent(0.05) == 5
    assert camtest.percent(1.0) == 100
    assert camtest.percent(0.5) == 50
    assert camtest.percent(0.02) == 5
    for s in range(1, 101):
        p = camtest.percent(s / 100)
        assert 5 <= p <= 100 and p % 5 == 0
        assert (1080 * p // 100) % 2 == 0 and (1920 * p // 100) % 2 == 0, p  # H.264 needs even sides


def test_stale_says_why():
    rec = camtest.record([10, 20], "abc", ["s01", "s02"], 0.33, 90)
    assert rec["shots"] == ["s01", "s02"] and rec["frames"] == 90 and rec["percent"] == 35
    assert camtest.stale(rec, [10, 20], "abc") is None
    assert camtest.stale(json.loads(json.dumps(rec)), [10, 20], "abc") is None  # as read back from camtest.json
    assert camtest.stale(rec, [10, 21], "abc") == "the scene changed since the camera test"
    assert camtest.stale(rec, [10, 20], "xyz") == "the timing changed since the camera test"
    assert camtest.stale(rec, None, "abc") == "the scene changed since the camera test"
    assert camtest.stale(None, [10, 20], "abc") == "no camera test yet"


def _faked(pendulum, monkeypatch, home, cameras: dict):  # noqa: F811
    """bsa_blender with the .blend, its scenes and the render module faked: `cameras` maps scene name to its
    camera (None for none). Returns (bsa_blender, the list of render calls)."""
    import bsa_blender

    ok(run(home, "timeline", "pendulum"))
    (pendulum / "blend").mkdir(exist_ok=True)
    (pendulum / "blend" / "short.blend").write_bytes(b"BLENDER")
    short = bsa_blender._load(pendulum)

    def scene(data, sh, shot_id, tl_hash):
        shot = next(s for s in sh.shots if s.id == shot_id)
        return shot, types.SimpleNamespace(name=shot.scene, camera=cameras.get(shot.scene, object()))

    calls = []
    fake = types.ModuleType("bsa.blender.camtest")
    fake.render_frames = lambda *a, **k: calls.append(("render", a)) or []
    fake.encode = lambda *a, **k: calls.append(("encode", a))
    monkeypatch.setitem(sys.modules, "bsa.blender.camtest", fake)
    monkeypatch.setattr(bsa_blender, "_open_blend", lambda sdir: object())
    monkeypatch.setattr(bsa_blender, "_scene", scene)
    monkeypatch.setattr(bsa_blender, "_scenes_current", lambda *a: True)
    monkeypatch.setenv("BSA_HOME", str(home))
    return bsa_blender, short, calls


def test_camtest_refuses_a_shot_without_a_camera(home, pendulum, monkeypatch, capsys):  # noqa: F811
    import bsa_blender

    named = bsa_blender._load(pendulum).shots[-1]
    mod, short, calls = _faked(pendulum, monkeypatch, home, {named.scene: None})
    first = next(s for s in short.shots if s.scene == named.scene)
    assert mod.main(["camtest", "pendulum", "--no-wait"]) == 1
    err = capsys.readouterr().err.strip()
    assert err == f"shot {first.id}: scene {named.scene!r} has no camera"
    assert calls == []  # nothing rendered
    assert not (pendulum / "review" / "camtest").exists()


def test_camtest_refuses_before_anything_renders(home, pendulum, monkeypatch, capsys):  # noqa: F811
    mod, _, calls = _faked(pendulum, monkeypatch, home, {})
    assert mod.main(["camtest", "pendulum", "--shots", "s01,s99", "--no-wait"]) == 1
    assert capsys.readouterr().err.strip() == "no shot 's99' in short.json"
    assert mod.main(["camtest", "pendulum", "--shots", " , ", "--no-wait"]) == 1
    assert "names no shot" in capsys.readouterr().err
    assert mod.main(["camtest", "pendulum", "--scale", "1.5", "--no-wait"]) == 1
    assert "--scale 1.5" in capsys.readouterr().err
    monkeypatch.setattr(mod, "_scenes_current", lambda *a: False)
    assert mod.main(["camtest", "pendulum", "--no-wait"]) == 1
    assert "build first" in capsys.readouterr().err
    (pendulum / "blend" / "short.blend").unlink()
    assert mod.main(["camtest", "pendulum", "--no-wait"]) == 1
    assert "short.blend does not exist" in capsys.readouterr().err
    assert calls == []


def test_camtest_writes_the_clip_the_sheet_and_the_record(home, pendulum, monkeypatch):  # noqa: F811
    from bsa import freshness

    mod, short, calls = _faked(pendulum, monkeypatch, home, {})
    tl, tl_hash = mod._timeline(pendulum, short)
    spans = {s.id: s for s in tl.shots}
    fake = sys.modules["bsa.blender.camtest"]

    def render_frames(scene, f0, f1, folder, percent):
        calls.append(("render", scene.name, f0, f1, percent))
        return [Path(folder) / f"{f:04d}.png" for f in range(f0, f1 + 1)]

    def encode(parts, out_mp4, percent, fps):
        calls.append(("encode", [(f0, f1) for _, f0, f1 in parts], percent, fps))
        Path(out_mp4).write_bytes(b"mp4")
        return Path(out_mp4)

    fake.render_frames, fake.encode = render_frames, encode
    grab = types.ModuleType("bsa.blender.grab")
    grab.sheet_frames = lambda n, count: [n, count]
    grab.contact_sheet = lambda mp4, frames, out, **k: calls.append(("sheet", frames, Path(mp4).name)) or Path(out)
    monkeypatch.setitem(sys.modules, "bsa.blender.grab", grab)
    folder = pendulum / "review" / "camtest"
    folder.mkdir(parents=True)
    (folder / camtest.RECORD).write_text("{}", encoding="utf-8")
    out = mod.cmd_camtest(mod.build_parser().parse_args(["camtest", "pendulum", "--shots", "s03,s01"]))
    n = sum(spans[s].f1 - spans[s].f0 + 1 for s in ("s01", "s03"))
    assert calls == [("render", "Workshop", spans["s01"].f0, spans["s01"].f1, 35),
                     ("render", "Clock", spans["s03"].f0, spans["s03"].f1, 35),
                     ("encode", [(spans["s01"].f0, spans["s01"].f1), (spans["s03"].f0, spans["s03"].f1)], 35, 30),
                     ("sheet", [n, camtest.SHEET_FRAMES], camtest.MP4)]  # fmt: skip
    assert out == {"mp4": str(folder / camtest.MP4), "sheet": str(folder / camtest.SHEET), "shots": ["s01", "s03"],
                   "frames": n, "percent": 35, "clip_seconds": round(n / 30, 2)}  # fmt: skip
    assert (folder / camtest.MP4).read_bytes() == b"mp4"
    assert not list(folder.glob("*.part*"))
    rec = json.loads((folder / camtest.RECORD).read_text(encoding="utf-8"))
    assert camtest.stale(rec, freshness.blend_stat(pendulum), tl_hash) is None
    assert rec["shots"] == ["s01", "s03"] and rec["frames"] == n


def test_camtest_is_a_gpu_command():
    import bsa_blender

    assert "camtest" in bsa_blender.GPU_COMMANDS
    args = bsa_blender.build_parser().parse_args(["camtest", "x"])
    assert (args.scale, args.shots, args.no_wait) == (0.33, None, False)


@pytest.mark.blender
def test_camtest_in_blender(blender_run):
    result = blender_run(HERE / "blender" / "test_camtest.py", timeout=900)
    assert result.returncode == 0, result.stdout + result.stderr
    for name in ("test_camtest_makes_an_mp4_and_a_sheet", "test_camtest_restores_every_setting_it_touches",
                 "test_camtest_restores_the_rest_when_one_restore_fails"):
        assert f"PASS {name}" in result.stdout
