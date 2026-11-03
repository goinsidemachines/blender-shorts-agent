"""The style sampler: the demo machine (bsa.blender.demo) rendered in each style, on a set or none, tiled left to right
into one PNG with a 32 px caption row (the style's name) under each tile. The engine is the caller's: it is passed in
(the user's choice) and handed on to scene setup and the still render, never picked or defaulted here."""

from __future__ import annotations

import dataclasses
import tempfile
import time
import uuid
from pathlib import Path

import bpy
import numpy as np

from bsa import fonts
from bsa.blender import cards, demo, sets, styles
from bsa.blender import scene as scene_mod
from bsa.blender.pixels import read_png
from bsa.blender.stills import render_still
from bsa.cards import card_font_and_note
from bsa.overlays import hex_colour
from bsa.short import Short

CAPTION_H = 32  # px under each tile
SUBJECT_M = 3.6  # the demo machine's size in metres
SCENE = "StyleSample"
FRAME = (1080, 1920)


def _tile(kit, style: str, set_name: str, engine: str, percent: int, png: Path, warnings: list) -> None:
    """One style's picture of the demo machine, rendered to `png` at `percent` of 1080x1920 in a scene of its own that
    is removed afterwards."""
    k = dataclasses.replace(kit, style=style)
    scene_mod.remove_scenes([SCENE])
    scene = bpy.data.scenes.new(SCENE)
    try:
        short = Short(slug="style-sample", title="Style sample", length_s=1.0)
        scene_mod.setup_scene(scene, short, engine, "still")
        scene.frame_start = scene.frame_end = scene.frame_current = 1
        bare = set_name == "none"
        if not bare:
            sets.base(scene, k, set_name, SUBJECT_M)
        built = demo.lever(scene, k, room=bare)  # no set: the sampler's own floor and wall
        if not bare:
            warnings.extend(sets.dress(scene, k, None, None, None, save=False).get("warnings", []))
        got = styles.apply(scene, k, target=built["target"], size=built["size"], key=built["key"], style=style)
        warnings.extend(got["warnings"])
        render_still(scene, 1, png, percent=percent, engine=engine, file_format="PNG")
    finally:
        scene_mod.remove_scenes([SCENE])


def _rgb(hex_: str) -> np.ndarray:
    c = hex_colour(hex_) or (0.0, 0.0, 0.0, 1.0)
    return np.array([round(v * 255) for v in c[:3]], np.uint8)


def _caption_strip(seq, text: str, centre_x: float, centre_y: float, font, colour) -> None:
    s = seq.strips.new_effect(f"T_{text[:18]}", "TEXT", 2, 1, length=1)
    s.text = text
    if font is not None:
        s.font = font
    s.font_size = 18
    s.use_bold = False
    s.color = colour
    s.location = (centre_x, centre_y)
    s.anchor_x, s.anchor_y = "CENTER", "CENTER"
    s.alignment_x = "CENTER"
    s.wrap_width = 1.0
    s.use_shadow = False
    s.use_box = False
    s.blend_type = "ALPHA_OVER"


def _sheet(kit, tiles: list[np.ndarray], names: list[str], out_png: Path, font_path, tmp: Path) -> None:
    """Stitch the tile pixels (rows top first) into one image with a caption row, and draw the names on it with the
    sequencer (one text strip each)."""
    h, w = tiles[0].shape[:2]
    sheet = np.zeros((h + CAPTION_H, w * len(tiles), 4), np.uint8)
    sheet[..., 3] = 255
    sheet[h:, :, :3] = _rgb(str(kit.caption.get("box")))
    for i, t in enumerate(tiles):
        sheet[:h, i * w : (i + 1) * w, :3] = t[..., :3]
    base = tmp / f"sheet-{uuid.uuid4().hex[:8]}.png"
    cards._write_rgba(sheet, base)
    total_w, total_h = sheet.shape[1], sheet.shape[0]
    added: list[str] = []
    scene = bpy.data.scenes.new(f"{SCENE}Sheet")
    try:
        r = scene.render
        r.resolution_x, r.resolution_y, r.resolution_percentage = total_w, total_h, 100
        r.use_sequencer, r.use_compositing = True, False
        scene.view_settings.view_transform = "Standard"
        scene.view_settings.look = "None"
        scene.frame_start = scene.frame_end = 1
        seq = scene.sequence_editor_create()
        seq.strips.new_image("Sheet", str(base), 1, 1, fit_method="ORIGINAL")
        had = set(bpy.data.fonts.keys())
        font = bpy.data.fonts.load(str(font_path), check_existing=True) if font_path else None
        added = [n for n in bpy.data.fonts.keys() if n not in had]  # noqa: SIM118 - ID collection, not a dict
        colour = hex_colour(str(kit.caption.get("color"))) or (1.0, 1.0, 1.0, 1.0)
        for i, name in enumerate(names):
            _caption_strip(seq, name, (i + 0.5) / len(tiles), 1.0 - (h + CAPTION_H / 2) / total_h, font, colour)
        if hasattr(r.image_settings, "media_type"):
            r.image_settings.media_type = "IMAGE"
        r.image_settings.file_format = "PNG"
        r.image_settings.color_mode = "RGB"
        r.image_settings.color_depth = "8"
        r.use_file_extension = False
        part = out_png.with_name(out_png.name + ".part")
        r.filepath = str(part)
        bpy.ops.render.render(write_still=True, scene=scene.name)
        if not part.is_file() or part.stat().st_size == 0:
            raise RuntimeError(f"the style sample was not written to {part}")
        part.replace(out_png)
    finally:
        bpy.data.scenes.remove(scene)
        for n in added:
            if n in bpy.data.fonts:
                bpy.data.fonts.remove(bpy.data.fonts[n])
        base.unlink(missing_ok=True)


def render_sheet(kit, names: list[str], set_name: str, engine: str, scale: float, out_png: Path):
    """Render `names` (style names) on `set_name` ("none" = no set) with `engine` ("cycles" | "eevee", the user's
    choice) at `scale` x 1080x1920 per tile, into `out_png`. Returns ({style: seconds}, warnings)."""
    out_png = Path(out_png)
    out_png.parent.mkdir(parents=True, exist_ok=True)
    percent = max(1, round(scale * 100))
    warnings: list[str] = []
    seconds: dict[str, float] = {}
    tiles = []
    font_path, note = card_font_and_note(kit, fonts.font_caches())
    if note:
        warnings.append(note)
    keeper = bpy.data.scenes.new(f"{SCENE}Keeper")  # a file always keeps one scene: the tiles' scenes come and go
    try:
        with tempfile.TemporaryDirectory(prefix="bsa-style-sample-", ignore_cleanup_errors=True) as tmp:
            for name in names:
                png = Path(tmp) / f"{name}.png"
                t0 = time.perf_counter()
                _tile(kit, name, set_name, engine, percent, png, warnings)
                seconds[name] = round(time.perf_counter() - t0, 1)
                tiles.append(read_png(png))
            _sheet(kit, tiles, names, out_png, font_path, Path(tmp))
    finally:
        if len(bpy.data.scenes) > 1:
            bpy.data.scenes.remove(keeper)
        elif "Scene" not in bpy.data.scenes:  # the only scene left: an empty file's own name
            keeper.name = scene_mod.FACTORY_SCENE
    return seconds, list(dict.fromkeys(warnings))
