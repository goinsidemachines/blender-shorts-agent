"""A thumbnail as a PNG, made in Blender's sequencer from a bsa.thumbs Layout: the hero frame as an image strip
(cropped and moved through its transform), the landscape panel as a flat image strip, and the cards as the edit's own
styled text strips (edit._Texts: the same font and glyph fallbacks as the video), or, with a brand kit, the kit's cards
as image strips (bsa.blender.cards.ensure: drawn once, cached by content). One frame, no scene render, no GPU, and the
render engine is never touched. Where everything goes is decided in bsa.thumbs (strip_placement, card_strips); this
only builds the strips."""

from __future__ import annotations

import tempfile
from pathlib import Path

import bpy

from bsa.blender.cards import ensure
from bsa.blender.edit import _load_font, _Texts
from bsa.image_checks import png_size
from bsa.layout import Rect
from bsa.thumbs import Layout, card_strips, strip_placement

SCENE = "Thumb"
FLAT = 16  # the flat panel image's side, in px (stretched over the panel)


def _flat_png(rgba, png: Path) -> None:
    """A FLAT x FLAT PNG of one colour, stored as its sRGB bytes (as closing_card._background)."""
    import numpy as np

    img = bpy.data.images.new("thumb_panel", FLAT, FLAT, alpha=False)
    try:
        img.colorspace_settings.name = "sRGB"
        img.pixels.foreach_set(np.tile([*rgba[:3], 1.0], FLAT * FLAT).astype(np.float32))
        img.filepath_raw = str(png)
        img.file_format = "PNG"
        img.save()
    finally:
        bpy.data.images.remove(img)


def _image(seq, name: str, png: Path, channel: int, place: dict):
    """An image strip at its own size, cropped and moved as `place` (bsa.thumbs.strip_placement) says."""
    s = seq.strips.new_image(name, str(png), channel, 1, fit_method="ORIGINAL")
    s.frame_final_duration = 1
    s.blend_type = "ALPHA_OVER"
    t = s.transform
    t.origin = place["origin"]
    t.scale_x, t.scale_y = place["scale_x"], place["scale_y"]
    t.offset_x, t.offset_y = place["offset_x"], place["offset_y"]
    c = place["crop"]
    s.crop.min_x, s.crop.max_x, s.crop.min_y, s.crop.max_y = c["left"], c["right"], c["bottom"], c["top"]
    return s


def render(lay: Layout, src_png: Path, out_png: Path, font_name: str | None, cache_dirs: list[Path] | None = None,
           notes: list[str] | None = None, card_dir: Path | None = None) -> Path:  # fmt: skip
    """Render `lay` with the hero frame `src_png` (any size: the layout's source rect is in 1080x1920 pixels) to
    `out_png`, an 8-bit RGB PNG at lay.size. `font_name` as the short's style font (edit._load_font, `cache_dirs`
    for downloaded weights). `notes`, when given, gets the font note and the glyph fallbacks. A brand kit's card (one
    with a `spec`) is its PNG from `card_dir` (drawn there when missing; a temp folder when None) as an image strip
    on the card's rect."""
    out_png = Path(out_png)
    out_png.parent.mkdir(parents=True, exist_ok=True)
    old = bpy.data.scenes.get(SCENE)
    if old is not None:
        bpy.data.scenes.remove(old)
    scene = bpy.data.scenes.new(SCENE)
    panel_png = out_png.with_name(out_png.stem + "-panel.png")
    fw, fh = lay.size
    try:
        r = scene.render
        r.resolution_x, r.resolution_y, r.resolution_percentage = fw, fh, 100
        r.use_sequencer, r.use_compositing = True, False
        scene.view_settings.view_transform = "Standard"  # the frames are already tone-mapped
        scene.view_settings.look = "None"
        scene.frame_start = scene.frame_end = 1
        seq = scene.sequence_editor_create()
        _image(seq, "Hero", Path(src_png), 1, strip_placement(lay.src, lay.dst, png_size(src_png), lay.size))
        if lay.panel is not None and lay.panel_colour is not None:  # over the hero: it hides any edge it bleeds
            _flat_png(lay.panel_colour, panel_png)
            _image(seq, "Panel", panel_png, 2,
                   strip_placement(Rect(0, 0, FLAT, FLAT), lay.panel, (FLAT, FLAT), lay.size, (FLAT, FLAT)))
        font, _, note = _load_font(font_name, cache_dirs)
        texts = _Texts(seq.strips, font, Path(bpy.path.abspath(font.filepath)) if font is not None else None)
        strips = iter(card_strips(lay))  # the text cards, in order (none for a kit's cards)
        for i, card in enumerate(lay.cards):
            if card.spec is not None:
                if card_dir is None:
                    card_dir = Path(tempfile.mkdtemp(prefix="bsa-cards-"))
                spec, size = card.spec, (card.spec.w, card.spec.h)
                png = ensure(spec, card_dir)
                s = _image(seq, f"K_{spec.kind}", png, 3 + i, strip_placement(Rect(0, 0, *size), card.rect, size,
                                                                               lay.size, size))  # fmt: skip
                s.alpha_mode = "STRAIGHT"
                continue
            c = next(strips)
            s = texts.add(c["text"], 1, 1, 3 + i, c["size"], c["loc"], c["anchor"], c["margin"], color=c["colour"],
                          bold=c["bold"], wrap_px=c["wrap_px"], box=c["box"], frame=lay.size)  # fmt: skip
            s.use_box = c["use_box"]
        if notes is not None:
            notes += [note] if note else []
            notes += texts.notes
        if hasattr(r.image_settings, "media_type"):
            r.image_settings.media_type = "IMAGE"
        r.image_settings.file_format = "PNG"
        r.image_settings.color_mode = "RGB"
        r.image_settings.color_depth = "8"
        r.use_file_extension = False
        r.filepath = str(out_png)
        bpy.ops.render.render(write_still=True, scene=scene.name)
    finally:
        bpy.data.scenes.remove(scene)
        panel_png.unlink(missing_ok=True)
    if not out_png.is_file() or out_png.stat().st_size == 0:
        raise RuntimeError(f"the thumbnail was not written to {out_png}")
    return out_png
