"""Watches a running render through Blender's render handlers and feeds bsa.render_status.Tracker: per-frame timing,
live frame checks and a thumbnail of the latest checked frame per shot.

Nothing here may touch bpy.data while the render runs (adding or removing an image mid-animation crashes Blender's
depsgraph), so frames are read with the `imbuf` module, which works outside bpy.data. Every handler swallows its own
errors: a status problem must never stop a render."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import bpy
import numpy as np

from bsa import render_status as rs
from bsa.image_checks import Flicker, luminance, pixel_findings

THUMB_W = 216  # thumbnail width in pixels (9:16 -> 216 x 384)
VRAM_EVERY_S = 60.0  # nvidia-smi at most this often


def _vram() -> list[dict]:
    if not shutil.which("nvidia-smi"):
        return []
    from bsa.machine import _nvidia

    now = time.time()
    return [{"name": g.name, "total_gb": g.vram_gb, "used_gb": g.vram_used_gb, "at": now}
            for g in _nvidia(subprocess.run)]  # fmt: skip


def _gpu_name() -> str | None:
    try:
        import gpu

        return gpu.platform.renderer_get() or None
    except Exception:  # noqa: BLE001 - no GPU context in this Blender
        return None


def render_settings(scene) -> dict:
    """The render settings in effect (after the preset was applied): engine, resolution and %, samples, denoise,
    motion blur, device and GPU names."""
    r = scene.render
    cycles = r.engine == "CYCLES"
    out = {
        "engine": "cycles" if cycles else "eevee",
        "resolution": [r.resolution_x, r.resolution_y],
        "percent": r.resolution_percentage,
        "fps": round(r.fps / (r.fps_base or 1.0), 3),
        "motion_blur": bool(r.use_motion_blur),
        "output": f"{r.image_settings.file_format} {r.image_settings.color_depth}-bit {r.image_settings.color_mode}",
        "blender": bpy.app.version_string,
    }
    if cycles:
        cy = scene.cycles
        out["samples"] = cy.samples
        out["denoise"] = bool(cy.use_denoising)
        if cy.use_adaptive_sampling:
            out["adaptive_threshold"] = round(cy.adaptive_threshold, 4)
        out["device"] = cy.device
        try:
            prefs = bpy.context.preferences.addons["cycles"].preferences
            backend = prefs.compute_device_type
            if cy.device == "GPU" and backend != "NONE":
                out["backend"] = backend
                out["gpus"] = [d.name for d in prefs.devices if d.use and d.type == backend]
            else:
                out["gpus"] = [d.name for d in prefs.devices if d.type == "CPU"][:1]
        except Exception:  # noqa: BLE001, S110 - device names are a nicety
            pass
    else:
        out["samples"] = scene.eevee.taa_render_samples
        out["denoise"] = None
        out["device"] = "GPU"
        name = _gpu_name()
        if name:
            out["gpu"] = name
    return out


def check_frame(png: Path, frame: int, thumb: Path, flicker: Flicker | None = None) -> tuple[list, bool]:
    """Black-frame and firefly findings for one rendered frame, and a JPEG thumbnail of it at `thumb`; with
    `flicker` (the shot's), also flicker at the frame before when the frames are checked one after another.
    Returns (findings, pixel checks ran): Blender before 5.0's imbuf cannot hand over pixels, so only the
    thumbnail is written there."""
    import imbuf

    ib = imbuf.load(str(png))
    try:
        findings, live = [], hasattr(ib, "with_buffer")
        if live:
            with ib.with_buffer() as buf:
                pixels = np.asarray(buf)
                lum = luminance(pixels)
                findings = pixel_findings(pixels, frame, lum)
                if flicker is not None:  # imbuf's rows run bottom up: flip so boxes measure from the top
                    findings += flicker.add(frame, lum[::-1], black=any(f.check == "black_frame" for f in findings))
        w, h = ib.size
        ib.resize((THUMB_W, max(1, round(h * THUMB_W / w))), method="BILINEAR")
        try:
            ib.file_type = "JPEG"
            ib.quality = 80
        except (AttributeError, TypeError, ValueError):
            pass  # an older imbuf writes the loaded format (PNG); browsers show it whatever the extension
        thumb.parent.mkdir(parents=True, exist_ok=True)
        tmp = thumb.with_name(f".{thumb.stem}.{os.getpid()}.tmp{thumb.suffix}")
        imbuf.write(ib, filepath=str(tmp))
        try:
            os.replace(tmp, thumb)
        except PermissionError:
            tmp.unlink(missing_ok=True)
    finally:
        ib.free()
    return findings, live


class Watch:
    """Registers render_pre / render_write / render_cancel handlers for the length of a `with` block."""

    def __init__(self, tracker: rs.Tracker, short_dir: Path, preset: str):
        self.tracker = tracker
        self.short_dir = Path(short_dir)
        self.preset = preset
        self.cancelled = False
        self.history: list[tuple[int, rs.FrameKey]] = []
        self.flicker = Flicker()  # per shot; only frames checked one after another are compared
        self._vram_at = float("-inf")
        self._errors = 0
        self._gpu_tries = 0

    # ---- lifecycle

    def __enter__(self):
        h = bpy.app.handlers
        h.render_pre.append(self._pre)
        h.render_write.append(self._write)
        h.render_cancel.append(self._cancel)
        return self

    def __exit__(self, *exc):
        h = bpy.app.handlers
        for lst, fn in ((h.render_pre, self._pre), (h.render_write, self._write), (h.render_cancel, self._cancel)):
            if fn in lst:
                lst.remove(fn)
        return False

    def on_start(self, scene) -> None:
        """Called by render_range once the preset is applied, before the shot renders."""
        t0 = time.perf_counter()
        before = self.tracker.overhead_s
        self.history = []
        self.flicker = Flicker()
        try:
            self.tracker.set_settings(render_settings(scene))
            self._maybe_vram(force=self._vram_at == float("-inf"))
            self.tracker.write(force=True)
        except Exception as exc:  # noqa: BLE001
            self._report(exc)
        self.tracker.add_overhead(time.perf_counter() - t0 - (self.tracker.overhead_s - before))

    # ---- handlers

    def _pre(self, scene, *_):
        try:
            self.tracker.frame_started(scene.frame_current)
        except Exception as exc:  # noqa: BLE001
            self._report(exc)

    def _write(self, scene, *_):
        t0 = time.perf_counter()
        before = self.tracker.overhead_s
        try:
            frame = scene.frame_current
            cur = self.tracker.current or {}
            shot = cur.get("shot")
            png = Path(scene.render.frame_path(frame=frame))
            findings = []
            key = rs.frame_key(png)
            if key is not None:
                self.history = [*self.history[-3:], (frame, key)]
                findings += rs.stuck_from_digests(self.history)
            if shot and self.tracker.should_check():
                c0 = time.perf_counter()
                found, live = check_frame(png, frame, rs.thumb_path(self.short_dir, self.preset, shot), self.flicker)
                findings += found
                self.tracker.note_check(shot, time.perf_counter() - c0)
                self.tracker.set_thumb(shot, frame)
                if not live:
                    self.tracker.checks_live = False
            if self._gpu_tries < 3 and "gpu" not in self.tracker.settings and self.tracker.settings.get("engine") == "eevee":
                self._gpu_tries += 1  # EEVEE has a GPU context once a frame is rendered; don't ask forever
                name = _gpu_name()
                if name:
                    self.tracker.set_settings({"gpu": name})
            self._maybe_vram()
            self.tracker.frame_written(frame)
            if shot:
                self.tracker.add_findings(shot, findings)
        except Exception as exc:  # noqa: BLE001
            self._report(exc)
        self.tracker.add_overhead(time.perf_counter() - t0 - (self.tracker.overhead_s - before))

    def _cancel(self, scene, *_):
        self.cancelled = True

    # ---- helpers

    def _maybe_vram(self, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._vram_at < VRAM_EVERY_S:
            return
        self._vram_at = now
        vram = _vram()
        if vram:
            self.tracker.set_settings({"vram": vram})

    def _report(self, exc: Exception) -> None:
        self._errors += 1
        if self._errors <= 3:
            print(f"render status: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
