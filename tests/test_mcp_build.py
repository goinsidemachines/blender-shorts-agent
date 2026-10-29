"""MCP mode builds with the plugin's current code, even in a Blender that imported an older bsa."""

import json
import runpy
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
BUILD = '''
import sys
from bsa import short as mod
from bsa.blender import bake

def build(short_dir):
    from pathlib import Path
    o = {}
    bake.tag(o, "hero:s03,s04")  # rejected by the old bake.tag
    (Path(short_dir) / "built.txt").write_text(mod.__file__ + "|" + o["bsa_tags"], encoding="utf-8")
'''


@pytest.fixture
def stale_bsa(monkeypatch):
    """A Blender session that imported an old bsa: stale module objects in sys.modules, the root not first."""
    saved_modules, saved_path = dict(sys.modules), list(sys.path)
    old = types.ModuleType("bsa")
    old.__path__ = []
    old_bake = types.ModuleType("bsa.blender.bake")
    old_bake.tag = lambda *a: (_ for _ in ()).throw(ValueError("old code"))
    sys.modules.update({"bsa": old, "bsa.blender": types.ModuleType("bsa.blender"), "bsa.blender.bake": old_bake,
                        "bsa.short": types.ModuleType("bsa.short")})  # fmt: skip
    sys.path.append(sys.path.pop(sys.path.index(str(ROOT))) if str(ROOT) in sys.path else str(ROOT))
    yield
    sys.modules.clear()
    sys.modules.update(saved_modules)
    sys.path[:] = saved_path


def test_fresh_bsa_drops_old_modules_and_puts_the_root_first(stale_bsa):
    ns = runpy.run_path(str(ROOT / "bsa_mcp_build.py"))
    dropped = ns["fresh_bsa"]()
    assert {"bsa", "bsa.blender", "bsa.blender.bake", "bsa.short"} <= set(dropped)
    assert not any(n == "bsa" or n.startswith("bsa.") for n in sys.modules)
    assert Path(sys.path[0]) == ROOT and sum(Path(p).resolve() == ROOT for p in sys.path if p) == 1


def test_build_short_runs_build_py_with_current_code(stale_bsa, tmp_path):
    short = tmp_path / "01-x"
    (short / "source").mkdir(parents=True)
    (short / "source" / "build.py").write_text(BUILD, encoding="utf-8")
    out = runpy.run_path(str(ROOT / "bsa_mcp_build.py"))["build_short"](short)
    path, tags = (short / "built.txt").read_text(encoding="utf-8").split("|")
    assert Path(path).resolve() == (ROOT / "bsa" / "short.py").resolve() and tags == "hero:s03,s04"
    assert out["reloaded"] >= 4 and out["blend"].endswith("short.blend")


def test_build_py_without_build_is_a_plain_error(stale_bsa, tmp_path):
    (tmp_path / "source").mkdir()
    (tmp_path / "source" / "build.py").write_text("x = 1\n", encoding="utf-8")
    with pytest.raises(TypeError, match="must define build"):
        runpy.run_path(str(ROOT / "bsa_mcp_build.py"))["build_short"](tmp_path)


# --------------------------------------------------------------------------- the open file, and the window afterwards

SCENE_BUILD = '''
import bpy

def build(short_dir):
    for name in ("Workshop", "Clock"):
        if bpy.data.scenes.get(name) is None:
            bpy.data.scenes.add(name)
'''


class _Scenes(list):
    def get(self, name):
        return next((s for s in self if s.name == name), None)

    def add(self, name, camera=True):
        sc = types.SimpleNamespace(name=name, camera=object() if camera else None)
        self.append(sc)
        return sc


def _fake_bpy(filepath="", scenes=("Scene",), ui=True):
    data = types.SimpleNamespace(filepath=filepath, scenes=_Scenes())
    for name in scenes:
        data.scenes.add(name)
    r3d = types.SimpleNamespace(view_perspective="PERSP")
    view3d = types.SimpleNamespace(type="VIEW_3D", spaces=types.SimpleNamespace(
        active=types.SimpleNamespace(type="VIEW_3D", region_3d=r3d)))  # fmt: skip
    outliner = types.SimpleNamespace(type="OUTLINER", spaces=types.SimpleNamespace(active=None))
    win = types.SimpleNamespace(scene=data.scenes[0] if data.scenes else None,
                                screen=types.SimpleNamespace(areas=[outliner, view3d]))  # fmt: skip
    wm = types.SimpleNamespace(windows=[win] if ui else [])
    return types.SimpleNamespace(data=data, app=types.SimpleNamespace(background=not ui),
                                 context=types.SimpleNamespace(window_manager=wm)), win, r3d  # fmt: skip


def _short(tmp_path, name="02-clock"):
    import shutil

    short = tmp_path / name
    (short / "source").mkdir(parents=True)
    shutil.copyfile(ROOT / "tests" / "e2e" / "pendulum" / "source" / "short.json", short / "source" / "short.json")
    (short / "source" / "build.py").write_text(SCENE_BUILD, encoding="utf-8")
    return short


@pytest.fixture
def fake_bpy(monkeypatch, stale_bsa):
    def make(**kw):
        bpy, win, r3d = _fake_bpy(**kw)
        monkeypatch.setitem(sys.modules, "bpy", bpy)
        return bpy, win, r3d

    return make


def test_build_shows_the_first_scene_through_its_camera(fake_bpy, tmp_path):
    _, win, r3d = fake_bpy()
    short = _short(tmp_path)
    out = runpy.run_path(str(ROOT / "bsa_mcp_build.py"))["build_short"](short)
    first = json.loads((short / "source" / "short.json").read_text(encoding="utf-8"))["shots"][0]["scene"]
    assert win.scene.name == first and r3d.view_perspective == "CAMERA"
    assert out["showing"] == first


def test_build_without_a_window_still_builds(fake_bpy, tmp_path):
    bpy, _, _ = fake_bpy(ui=False)
    out = runpy.run_path(str(ROOT / "bsa_mcp_build.py"))["build_short"](_short(tmp_path))
    assert out["showing"] is None and {"Workshop", "Clock"} <= {s.name for s in bpy.data.scenes}


def test_build_refuses_another_shorts_file(fake_bpy, tmp_path):
    short = _short(tmp_path)
    other = tmp_path / "01-lab" / "blend" / "short.blend"
    bpy, _, _ = fake_bpy(filepath=str(other), scenes=("Lab",))
    build_short = runpy.run_path(str(ROOT / "bsa_mcp_build.py"))["build_short"]
    with pytest.raises(RuntimeError, match="01-lab") as exc:
        build_short(short)
    assert "File > New" in str(exc.value)
    assert [s.name for s in bpy.data.scenes] == ["Lab"]  # nothing built
    out = build_short(short, force=True)  # the user said it is fine
    assert out["blend"].endswith("short.blend")


def test_build_refuses_scenes_the_short_does_not_name(fake_bpy, tmp_path):
    short = _short(tmp_path)
    bpy, _, _ = fake_bpy(scenes=("Scene", "Lab", "Workshop"))
    build_short = runpy.run_path(str(ROOT / "bsa_mcp_build.py"))["build_short"]
    with pytest.raises(RuntimeError, match="'Lab'"):
        build_short(short)
    bpy.data.scenes.remove(bpy.data.scenes.get("Lab"))
    build_short(short)  # the default "Scene" of File > New and the short's own scenes are fine


def test_build_accepts_its_own_file(fake_bpy, tmp_path):
    short = _short(tmp_path)
    fake_bpy(filepath=str(short / "blend" / "short.blend"), scenes=("Workshop", "Clock"))
    runpy.run_path(str(ROOT / "bsa_mcp_build.py"))["build_short"](short)
