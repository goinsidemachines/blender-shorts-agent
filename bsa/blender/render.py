"""Resumable PNG frame rendering and render-time estimation."""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path

import bpy

from bsa.blender.scene import apply_preset, preserved_render_state, resolve_engine


def render_range(
    scene, f0: int, f1: int, out_dir: Path, preset: str, percent: int | None = None, engine: str | None = None,
    on_start: Callable[[object], None] | None = None,
) -> list[Path]:
    """Render frames f0..f1 as ####.png into out_dir, skipping frames already on disk.

    `engine` is "cycles" | "eevee"; None uses the scene's current engine. Zero-byte PNGs (placeholders left by an
    interrupted render) are deleted first. `on_start(scene)` runs once the preset is applied, just before rendering
    (the live render status reads the settings in effect there). Returns every frame path; raises RuntimeError if
    any is missing or empty.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for p in out_dir.glob("*.png"):
        if p.stat().st_size == 0:
            p.unlink()
    engine = resolve_engine(scene, engine)
    r = scene.render
    saved = (r.filepath, r.use_overwrite, r.use_placeholder, scene.frame_start, scene.frame_end)
    try:
        with preserved_render_state(scene):
            apply_preset(scene, engine, preset, percent)
            r.filepath = str(out_dir) + "/"
            r.use_overwrite = False
            r.use_placeholder = True
            scene.frame_start, scene.frame_end = f0, f1
            if on_start is not None:
                on_start(scene)
            result = bpy.ops.render.render(animation=True, scene=scene.name)
    finally:
        r.filepath, r.use_overwrite, r.use_placeholder = saved[0], saved[1], saved[2]
        scene.frame_start, scene.frame_end = saved[3], saved[4]
    paths = [out_dir / f"{f:04d}.png" for f in range(f0, f1 + 1)]
    bad = [p.name for p in paths if not p.exists() or p.stat().st_size == 0]
    if bad:
        raise RuntimeError(f"render {sorted(result)} left missing or empty frames: {', '.join(bad)}")
    return paths


MARGIN = 1.1  # the estimate's safety margin over the measured time per frame


def _timed_render(scene, frame: int) -> float:
    scene.frame_set(frame)
    t0 = time.perf_counter()
    bpy.ops.render.render(write_still=True, scene=scene.name)
    return time.perf_counter() - t0


def estimate_seconds(scene, frame: int, engine: str, n_frames: int, percent: int | None = None) -> float:
    """Seconds to render n_frames at the "final" preset, from timing one frame. Scene state is restored.

    Cycles: one frame's time * n_frames * MARGIN. EEVEE: its first render compiles the scene's shaders, which can take
    as long as many frames, so timing that cold frame made the estimate about twice the real render (22 min estimated,
    11.5 min real for 1,556 frames). It renders a warm frame next (frame + 1) and returns the cold start once plus the
    warm frame's time * n_frames * MARGIN."""
    engine = resolve_engine(scene, engine)
    r = scene.render
    saved = (r.filepath, scene.frame_current)
    tmp = Path(bpy.app.tempdir or ".") / "bsa_estimate.png"
    try:
        with preserved_render_state(scene):
            apply_preset(scene, engine, "final", percent)
            r.filepath = str(tmp)
            first = _timed_render(scene, frame)
            if engine != "eevee":
                return first * n_frames * MARGIN
            warm = _timed_render(scene, frame + 1)
            return max(first - warm, 0.0) + warm * n_frames * MARGIN
    finally:
        r.filepath = saved[0]
        scene.frame_set(saved[1])
        tmp.unlink(missing_ok=True)
