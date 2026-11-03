"""The kit's cards in Blender: each card drawn once as a transparent PNG in a one-frame sequencer scene (the shape as an
image strip from cards.shape_rgba, the logo as an image strip, the text as text strips), cached by cards.cache_key,
and placed in the edit as image strips where bsa.kitlayout put them.

Only the sequencer draws here: no scene strip, no 3D render, no GPU, and the render engine of every scene is left as
the user set it. Where no strip draws, the PNG is transparent."""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import bpy
import numpy as np

from bsa import cards
from bsa.cards import CardSpec
from bsa.image_checks import png_size
from bsa.kitlayout import CH, KitOverlay, pointer_transform
from bsa.layout import H, Rect, W
from bsa.overlays import hex_colour
from bsa.thumbs import strip_placement

SCENE = "Card"


def _write_rgba(px: np.ndarray, png: Path) -> None:
    """An (h, w, 4) uint8 straight-alpha image (top row first) as an sRGB RGBA PNG through a bpy image."""
    h, w = px.shape[:2]
    img = bpy.data.images.new(f"card_shape_{png.stem}", w, h, alpha=True)
    try:
        img.colorspace_settings.name = "sRGB"
        img.alpha_mode = "STRAIGHT"
        img.pixels.foreach_set((px[::-1].astype(np.float32) / 255.0).ravel())  # Blender's rows run from the bottom
        img.filepath_raw = str(png)
        img.file_format = "PNG"
        img.save()
    finally:
        bpy.data.images.remove(img)


def _image(seq, name: str, png: Path, channel: int, f0: int, f1: int, place: dict):
    """An image strip at its own size (fit "ORIGINAL"), from f0 to f1, moved and cropped as `place` says
    (bsa.thumbs.strip_placement)."""
    s = seq.strips.new_image(name, str(png), channel, f0, fit_method="ORIGINAL")
    s.frame_final_duration = f1 - f0 + 1
    s.blend_type = "ALPHA_OVER"
    s.alpha_mode = "STRAIGHT"
    t = s.transform
    t.origin = place["origin"]
    t.scale_x, t.scale_y = place["scale_x"], place["scale_y"]
    t.offset_x, t.offset_y = place["offset_x"], place["offset_y"]
    c = place.get("crop")
    if c:
        s.crop.min_x, s.crop.max_x, s.crop.min_y, s.crop.max_y = c["left"], c["right"], c["bottom"], c["top"]
    return s


def _text(seq, run: cards.Run, channel: int, font, spec: CardSpec) -> None:
    """A text strip for one line: its box (the first line's box height, as cards sized it) centred on run.y, its
    x the middle (CENTER) or the left edge (LEFT) of the text. No box, no shadow, no wrapping."""
    _, box_h = cards._metrics(spec.font)  # the line metrics cards sized the card with
    top = run.y - box_h * run.size / 2
    s = seq.strips.new_effect(f"T_{run.text[:18]}", "TEXT", channel, 1, length=1)
    s.text = run.text
    if font is not None:
        s.font = font
    s.font_size = run.size
    s.use_bold = False
    s.color = hex_colour(run.colour) or (0.0, 0.0, 0.0, 1.0)
    s.location = (run.x / spec.w, 1.0 - top / spec.h)
    s.anchor_x, s.anchor_y = run.anchor, "TOP"
    s.alignment_x = run.anchor
    s.wrap_width = 1.0
    s.use_shadow = False
    s.use_box = False
    s.blend_type = "ALPHA_OVER"


def render(spec: CardSpec, out_png: Path) -> Path:
    """Draw `spec` as a `spec.w` x `spec.h` 8-bit RGBA PNG at `out_png` (written to a temp name, then moved in), in a
    one-frame sequencer scene "Card" that is removed afterwards."""
    out_png = Path(out_png)
    out_png.parent.mkdir(parents=True, exist_ok=True)
    tag = uuid.uuid4().hex[:12]
    shape_png = out_png.with_name(f".{out_png.stem}-{tag}-shape.png")
    tmp_png = out_png.with_name(f".{out_png.stem}-{tag}.tmp.png")
    old = bpy.data.scenes.get(SCENE)
    if old is not None:
        bpy.data.scenes.remove(old)
    scene = bpy.data.scenes.new(SCENE)
    try:
        r = scene.render
        r.resolution_x, r.resolution_y, r.resolution_percentage = spec.w, spec.h, 100
        r.use_sequencer, r.use_compositing = True, False
        scene.view_settings.view_transform = "Standard"
        scene.view_settings.look = "None"
        scene.frame_start = scene.frame_end = 1
        seq = scene.sequence_editor_create()
        size = (spec.w, spec.h)
        _write_rgba(cards.shape_rgba(spec), shape_png)
        _image(seq, "Shape", shape_png, 1, 1, 1, strip_placement(Rect(0, 0, *size), Rect(0, 0, *size), size, size,
                                                                 size))  # fmt: skip
        if spec.logo and spec.logo_rect:
            lw, lh = png_size(Path(spec.logo))
            _image(seq, "Logo", Path(spec.logo), 2, 1, 1,
                   strip_placement(Rect(0, 0, lw, lh), Rect(*spec.logo_rect), (lw, lh), size, (lw, lh)))
        font = bpy.data.fonts.load(str(spec.font), check_existing=True) if spec.font else None
        for i, run in enumerate(spec.runs):
            _text(seq, run, 3 + i, font, spec)
        if hasattr(r.image_settings, "media_type"):
            r.image_settings.media_type = "IMAGE"
        r.image_settings.file_format = "PNG"
        r.image_settings.color_mode = "RGBA"
        r.image_settings.color_depth = "8"
        r.use_file_extension = False
        r.filepath = str(tmp_png)
        bpy.ops.render.render(write_still=True, scene=scene.name)
        if not tmp_png.is_file() or tmp_png.stat().st_size == 0:
            raise RuntimeError(f"the card {spec.kind!r} was not written to {tmp_png}")
        os.replace(tmp_png, out_png)
    finally:
        bpy.data.scenes.remove(scene)
        shape_png.unlink(missing_ok=True)
        tmp_png.unlink(missing_ok=True)
    return out_png


def ensure(spec: CardSpec, cache_dir: Path) -> Path:
    """The card's PNG in `cache_dir` (named by cards.cache_key), drawn only when it isn't there yet."""
    png = Path(cache_dir) / f"{cards.cache_key(spec)}.png"
    if not png.is_file() or png.stat().st_size == 0:
        render(spec, png)
    return png


def _at(spec: CardSpec, rect: Rect, frame) -> dict:
    size = (spec.w, spec.h)
    return strip_placement(Rect(0, 0, *size), rect, size, frame, size)


def _key(t, values: dict, frame: int) -> None:
    for name, value in values.items():
        setattr(t, name, value)
        t.keyframe_insert(name, frame=frame)


def add_overlay(seq, ov: KitOverlay, cache_dir: Path, frame=(W, H)) -> None:
    """The kit's cards as image strips in `seq` (the edit's sequence editor), drawn into `cache_dir` when missing:
    static cards where the layout put them, near cards following their object (offsets keyed every frame), pointer
    lines from the card to the anchor (offset, scale and rotation keyed every frame; none while the anchor is under
    the card) with their dots, and the progress bar (track, fill cropped frame by frame, then the track's outline
    again over the fill)."""
    for p in ov.cards:
        png = ensure(p.spec, cache_dir)
        if p.track is None:
            _image(seq, f"K_{p.spec.kind}", png, p.channel, p.f0, p.f1, _at(p.spec, p.at, frame))
            continue
        first = p.track[min(p.track)]
        s = _image(seq, f"K_{p.spec.kind}", png, p.channel, p.f0, p.f1, _at(p.spec, first, frame))
        for f in range(p.f0, p.f1 + 1):
            rect = p.track.get(f)
            if rect is not None:
                place = _at(p.spec, rect, frame)
                _key(s.transform, {"offset_x": place["offset_x"], "offset_y": place["offset_y"]}, f)
    for ptr in ov.pointers:
        _pointer(seq, ptr, cache_dir, frame)
    if ov.progress is not None:
        _progress(seq, ov.progress, cache_dir, frame)


def _pointer(seq, ptr, cache_dir: Path, frame) -> None:
    line, dot = ptr.spec, ptr.dot
    line_png, dot_png = ensure(line, cache_dir), ensure(dot, cache_dir)
    zero = {"origin": (0.0, 0.5), "scale_x": 0.0, "scale_y": 1.0, "offset_x": 0.0, "offset_y": 0.0}
    ls = _image(seq, "K_pointer", line_png, ptr.channel, ptr.f0, ptr.f1, zero)
    dot_ch = CH["dot"] + ptr.channel - CH["pointer"]  # the label's index on from the dot channel
    ds = _image(seq, "K_dot", dot_png, dot_ch, ptr.f0, ptr.f1, _at(dot, Rect(0, 0, dot.w, dot.h), frame))
    for f in range(ptr.f0, ptr.f1 + 1):
        end = ptr.ends.get(f)
        if end is None:  # the anchor is under the card: no line, no dot
            _key(ls.transform, {"scale_x": 0.0}, f)
            _key(ds.transform, {"scale_x": 0.0, "scale_y": 0.0}, f)
            continue
        start, anchor = end
        t = pointer_transform(start, anchor, line.w, line.h, frame)
        _key(ls.transform, {"offset_x": t["offset_x"], "offset_y": t["offset_y"], "scale_x": t["scale_x"],
                            "rotation": t["rotation"]}, f)  # fmt: skip
        place = _at(dot, Rect(anchor[0] - dot.w / 2, anchor[1] - dot.h / 2, dot.w, dot.h), frame)
        _key(ds.transform, {"offset_x": place["offset_x"], "offset_y": place["offset_y"], "scale_x": 1.0,
                            "scale_y": 1.0}, f)  # fmt: skip


def _progress(seq, prog, cache_dir: Path, frame) -> None:
    track, fill = prog.track, prog.fill
    outline = CardSpec("progress_outline", track.w, track.h, track.box_h, None, track.outline, track.outline_px, 0,
                       track.radius, (), None)  # fmt: skip
    o = track.outline_px
    _image(seq, "K_progress", ensure(track, cache_dir), prog.channel, prog.f0, prog.f1, _at(track, prog.at, frame))
    fs = _image(seq, "K_progress_fill", ensure(fill, cache_dir), prog.channel + 1, prog.f0, prog.f1,
                _at(fill, Rect(prog.at.x + o, prog.at.y + o, fill.w, fill.h), frame))  # fmt: skip
    _image(seq, "K_progress_outline", ensure(outline, cache_dir), prog.channel + 2, prog.f0, prog.f1,
           _at(outline, prog.at, frame))  # fmt: skip
    n = prog.f1 - prog.f0 + 1
    for f in range(prog.f0, prog.f1 + 1):
        shown = round(fill.w * (f - prog.f0 + 1) / n)
        fs.crop.max_x = fill.w - shown
        fs.crop.keyframe_insert("max_x", frame=f)
