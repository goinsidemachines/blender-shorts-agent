"""The closing card as a 1080x1920 PNG, made in Blender's sequencer: the user's own image scaled to fill the frame, or
a card made from the brand (its background, font and accent colour) with "More at", the website in a pill and the
@handle. The card in the video and on the review pages is this PNG (bsa.closing)."""

from __future__ import annotations

from pathlib import Path

import bpy

from bsa.blender.edit import CAPTION_MARGIN, CHAR_W, LINE_H, _hex_colour, _load_font, _norm, _Texts
from bsa.closing import COVERED_RIGHT, card_rows
from bsa.layout import H, W, safe_rect

BACKGROUND = "#14161A"  # without a brand background
ACCENT = "#FFC800"  # without a brand accent
DARK, LIGHT = (0.08, 0.086, 0.1, 1.0), (1.0, 1.0, 1.0, 1.0)
SIZES = {"lead": 56, "pill": 84, "handle": 52}
GAP_PX = 36
TOP_PX = 640  # the block's top: in the upper half, far from the covered bottom


def _luma(rgba) -> float:
    return 0.2126 * rgba[0] + 0.7152 * rgba[1] + 0.0722 * rgba[2]


def _on(rgba):
    """Dark or white text, whichever reads better on `rgba`."""
    return DARK if _luma(rgba) > 0.5 else LIGHT


def image_size(path: Path) -> tuple[int, int]:
    img = bpy.data.images.load(str(path), check_existing=False)
    try:
        return int(img.size[0]), int(img.size[1])
    finally:
        bpy.data.images.remove(img)


def _background(seq, rgba, png: Path) -> None:
    """A full-frame image strip of one colour (a sequencer COLOR strip renders empty here, an image never does). The
    colour is stored as its sRGB bytes, as the hex was given."""
    import numpy as np

    img = bpy.data.images.new("closing_background", 16, 16, alpha=False)
    try:
        img.colorspace_settings.name = "sRGB"
        img.pixels.foreach_set(np.tile([*rgba[:3], 1.0], 16 * 16).astype(np.float32))
        img.filepath_raw = str(png)
        img.file_format = "PNG"
        img.save()
    finally:
        bpy.data.images.remove(img)
    seq.strips.new_image("Background", str(png), 1, 1, fit_method="STRETCH")


def _text_rows(scene, cfg: dict, brand: dict, font_name: str | None, bg_png: Path) -> None:
    seq = scene.sequence_editor
    palette = brand.get("palette") if isinstance(brand.get("palette"), dict) else {}
    bg = _hex_colour(palette.get("background"), 1.0) or _hex_colour(BACKGROUND, 1.0)
    accent = _hex_colour(palette.get("accent"), 1.0) or _hex_colour(ACCENT, 1.0)
    _background(seq, bg, bg_png)
    font, _, _ = _load_font(font_name)
    texts = _Texts(seq.strips, font, Path(bpy.path.abspath(font.filepath)) if font is not None else None)
    safe = safe_rect()
    cx = W / 2
    wrap = 2 * (W * (1 - COVERED_RIGHT) - cx) - 2 * CAPTION_MARGIN * H - 40  # clear of the covered right edge
    y = TOP_PX
    for i, (text, kind) in enumerate(card_rows(cfg)):
        size = SIZES[kind]
        size = int(min(size, wrap / max(len(text) * CHAR_W, 1)))  # a long website gets smaller, never wraps
        pill = kind == "pill"
        margin = CAPTION_MARGIN if pill else 0.0
        h = size * LINE_H
        top = y + margin * H
        s = texts.add(text, 1, 1, 2 + i, size, _norm(cx, top), ("CENTER", "TOP"), margin,
                      color=_on(accent) if pill else _on(bg), bold=kind != "lead", wrap_px=wrap,
                      box=accent if pill else (0, 0, 0, 0))  # fmt: skip
        s.use_box = pill
        if pill:
            s.box_roundness = 1.0
        y = top + h + margin * H + GAP_PX
    assert y < safe.bottom


def make_card(cfg: dict, brand: dict, out_png: Path, font_name: str | None = None) -> Path:
    """Render the closing card to `out_png` (PNG, 1080x1920, RGB): `cfg` from bsa.closing.resolve, with an "image"
    that exists or None; `brand` the settings' brand for this short (bsa.settings.brand_for)."""
    out_png = Path(out_png)
    out_png.parent.mkdir(parents=True, exist_ok=True)
    old = bpy.data.scenes.get("ClosingCard")
    if old is not None:
        bpy.data.scenes.remove(old)
    scene = bpy.data.scenes.new("ClosingCard")
    bg_png = out_png.with_name("closing-background.png")
    try:
        r = scene.render
        r.resolution_x, r.resolution_y, r.resolution_percentage = W, H, 100
        r.use_sequencer, r.use_compositing = True, False
        scene.view_settings.view_transform = "Standard"
        scene.view_settings.look = "None"
        scene.frame_start = scene.frame_end = 1
        seq = scene.sequence_editor_create()
        if cfg.get("image"):
            seq.strips.new_image("Card", str(cfg["image"]), 1, 1, fit_method="FILL")
        else:
            _text_rows(scene, cfg, brand or {}, font_name, bg_png)
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
        bg_png.unlink(missing_ok=True)
    if not out_png.is_file() or out_png.stat().st_size == 0:
        raise RuntimeError(f"the closing card was not written to {out_png}")
    return out_png
