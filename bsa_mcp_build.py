"""MCP mode: build a short inside an already open Blender with the plugin's current code.

An open Blender keeps every module it has imported, so after a plugin update `import bsa...` in build.py would still
get the old code. Run this file fresh each time (runpy does not cache it) through the MCP's execute_blender_code:

    import runpy; runpy.run_path(r"<root>/bsa_mcp_build.py")["build_short"](r"<short>")

It refuses an open file that is another short's .blend or holds scenes the short does not name, drops every `bsa` and
`bsa.*` module, puts the plugin root first on sys.path, runs source/build.py and calls its build(SHORT_DIR) (which
saves blend/short.blend), then shows the first shot's scene in the window, looking through its camera.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent


def fresh_bsa(plugin_root: Path | str = PLUGIN_ROOT) -> list[str]:
    """Forget every loaded `bsa` / `bsa.*` module and put `plugin_root` first on sys.path, so the next import reads
    the plugin's current files. Returns the names dropped."""
    dropped = sorted(name for name in sys.modules if name == "bsa" or name.startswith("bsa."))
    for name in dropped:
        del sys.modules[name]
    root = str(Path(plugin_root).resolve())
    sys.path[:] = [root] + [p for p in sys.path if not p or Path(p).resolve() != Path(root)]
    importlib.invalidate_caches()
    return dropped


DEFAULT_SCENE = "Scene"  # the one scene of File > New: fine to build next to


def _bpy():
    try:
        import bpy
    except ImportError:  # not inside Blender (tests): nothing to guard or show
        return None
    return bpy


def check_open_file(bpy, short_dir: Path, scene_names: set[str]) -> None:
    """Refuse to build into an open file that is another short's .blend, or that holds scenes this short does not name
    (build() would save them into this short's blend/short.blend)."""
    own = (short_dir / "blend" / "short.blend").resolve()
    path = bpy.data.filepath
    new = "Ask the user to start from File > New > General (or to confirm it is fine), then build again"
    if path and Path(path).resolve() != own:
        raise RuntimeError(f"the open Blender file is {path}, not this short's {own}: building would save its scenes "
                           f"into this short. {new} (build_short(..., force=True) once they confirm).")  # fmt: skip
    foreign = sorted(s.name for s in bpy.data.scenes if s.name not in scene_names and s.name != DEFAULT_SCENE)
    if foreign:
        raise RuntimeError(f"the open Blender file has scenes this short does not use ({', '.join(map(repr, foreign))})"
                           f": building would save them into this short. {new} (build_short(..., force=True) once "
                           "they confirm).")  # fmt: skip


def show_scene(bpy, name: str) -> str | None:
    """Show scene `name` in the open window, with a 3D viewport looking through its camera, so the user watching sees
    the shot rather than the empty default scene. Returns the scene shown, or None without a window (background)."""
    wm = bpy.context.window_manager
    scene = bpy.data.scenes.get(name)
    if getattr(bpy.app, "background", False) or wm is None or not wm.windows or scene is None:
        return None
    win = wm.windows[0]
    win.scene = scene
    if scene.camera is not None:
        for area in win.screen.areas:
            space = area.spaces.active
            if area.type == "VIEW_3D" and space is not None and getattr(space, "region_3d", None) is not None:
                space.region_3d.view_perspective = "CAMERA"
                break
    return scene.name


def build_short(short_dir: Path | str, plugin_root: Path | str = PLUGIN_ROOT, force: bool = False) -> dict:
    """Run the short's source/build.py with fresh plugin code and call its build(short_dir), then show its first
    shot's scene through the camera. Refuses (RuntimeError) an open file that is another short's or holds scenes the
    short does not name, unless `force` (the user confirmed)."""
    short_dir = Path(short_dir)
    dropped = fresh_bsa(plugin_root)
    bpy = _bpy()
    first = None
    if bpy is not None:
        from bsa.short import load_short

        short = load_short(short_dir / "source" / "short.json")
        first = short.shots[0].scene if short.shots else None
        if not force:
            check_open_file(bpy, short_dir, {s.scene for s in short.shots})
    build_py = short_dir / "source" / "build.py"
    ns = {"__name__": "bsa_build", "__file__": str(build_py)}
    exec(compile(build_py.read_text(encoding="utf-8"), str(build_py), "exec"), ns)  # noqa: S102 - the short's own build file
    if not callable(ns.get("build")):
        raise TypeError(f"{build_py} must define build(short_dir)")
    ns["build"](str(short_dir))
    showing = show_scene(bpy, first) if bpy is not None and first else None
    return {"short": str(short_dir), "reloaded": len(dropped), "blend": str(short_dir / "blend" / "short.blend"),
            "showing": showing}  # fmt: skip
