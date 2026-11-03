import platform
import tempfile
from pathlib import Path

import bpy
import numpy as np

import bsa.blender.thumbs
from bsa import thumbs
from bsa.blender.pixels import read_png
from bsa.layout import Rect

TEXT = thumbs.ThumbText(("Two rows,", "one pull"), "How a zipper works", None)
HERO = Rect(540, 1000, 300, 300)
BOX = (0x20 / 255, 0x40 / 255, 0xA0 / 255, 1.0)
STYLE = thumbs.ThumbStyle(
    title=(1.0, 1.0, 1.0, 1.0), title_box=BOX, kicker=(0.0, 0.0, 0.0, 1.0), kicker_box=(1.0, 0.8, 0.0, 1.0),
    fact=(1.0, 1.0, 1.0, 1.0), fact_box=(0.0, 0.0, 0.0, 0.85), handle=(1.0, 0.8, 0.35, 1.0),
    handle_box=(0.0, 0.0, 0.0, 0.85), panel=(0x14 / 255, 0x16 / 255, 0x1A / 255, 1.0), font=None, handle_text="@me",
)  # fmt: skip


def _source(path: Path, scale: float = 1.0) -> None:
    """A grey frame with a red 300x300 square at HERO (in 1080x1920 pixels), `scale` times that size."""
    w, h = round(1080 * scale), round(1920 * scale)
    px = np.full((h, w, 4), 0.5, np.float32)
    px[..., 3] = 1.0
    y0, y1, x0, x1 = (round(v * scale) for v in (HERO.y, HERO.bottom, HERO.x, HERO.right))
    px[y0:y1, x0:x1, :3] = (1.0, 0.0, 0.0)
    img = bpy.data.images.new("src", w, h)
    img.pixels.foreach_set(px[::-1].ravel())  # Blender's rows run from the bottom
    img.filepath_raw = str(path)
    img.file_format = "PNG"
    img.save()
    bpy.data.images.remove(img)


def _close(got, want, tol) -> bool:
    return all(abs(int(g) - int(w)) <= tol for g, w in zip(got, want, strict=True))


def _check(lay, px) -> None:
    assert px.shape[:2] == (lay.size[1], lay.size[0]), px.shape
    t = next(c for c in lay.cards if c.kind == "title")
    y, x = int(t.rect.y + t.pad - 6), int(t.rect.x + t.rect.w / 2)  # in the box, above the text
    assert _close(px[y, x, :3], (0x20, 0x40, 0xA0), 3), (y, x, px[y, x, :3])
    s = lay.dst.w / lay.src.w
    hx = int(lay.dst.x + (HERO.x + HERO.w / 2 - lay.src.x) * s)
    hy = int(lay.dst.y + (HERO.y + HERO.h / 2 - lay.src.y) * s)
    assert px[hy, hx, 0] > 200 and px[hy, hx, 1] < 60, (hy, hx, px[hy, hx, :3])
    edge = (int(lay.dst.y + 2), int(lay.dst.right - 3))  # the frame reaches the destination's edge: no seam
    assert _close(px[edge][:3], (0x7F, 0x7F, 0x7F), 30) or px[edge][0] > 200, px[edge][:3]


def test_both_sizes_at_exact_pixels_with_the_title_and_hero_where_the_layout_said():
    tmp = Path(tempfile.mkdtemp())
    src = tmp / "src.png"
    _source(src)
    for lay in (thumbs.vertical(TEXT, HERO, STYLE), thumbs.landscape(TEXT, HERO, STYLE)):
        notes = []
        px = read_png(bsa.blender.thumbs.render(lay, src, tmp / f"out-{lay.size[0]}.png", None, notes=notes))
        _check(lay, px)
        if lay.panel is not None:
            assert _close(px[20, 20, :3], (0x14, 0x16, 0x1A), 2), px[20, 20, :3]  # the panel colour
            assert _close(px[lay.size[1] - 3, int(lay.panel.right) - 2, :3], (0x14, 0x16, 0x1A), 2)
    assert not list(tmp.glob("*-panel.png")) and bpy.data.scenes.get("Thumb") is None


def test_a_preview_frame_at_half_size_fills_the_same_layout():
    tmp = Path(tempfile.mkdtemp())
    src = tmp / "half.png"
    _source(src, 0.5)
    for lay in (thumbs.vertical(TEXT, HERO, STYLE), thumbs.landscape(TEXT, HERO, STYLE)):
        _check(lay, read_png(bsa.blender.thumbs.render(lay, src, tmp / f"half-{lay.size[0]}.png", None)))


def _drawn_rows(px, card) -> tuple[int, int] | None:
    """The first and last rows where the card's box colour shows, in the columns around the card's centre."""
    box = np.array([round(v * 255) for v in card.box[:3]])
    cx = int(card.rect.x + card.rect.w / 2)
    cols = px[:, max(cx - 20, 0) : cx + 20, :3].astype(int)
    rows = np.where((np.abs(cols - box).max(axis=2) <= 4).any(axis=1))[0]
    return (int(rows.min()), int(rows.max()) + 1) if len(rows) else None


def test_drawn_boxes_match_the_cards_in_the_fonts_used():
    """A two-line title and a kicker, in Blender's own font and in the OS fallback font (Segoe UI here, taller than
    the estimate the layout once used): each box is drawn where its card is, and the gap between them stays."""
    from bsa import fonts

    installed = next((name for name in fonts.FALLBACKS.get(platform.system(), fonts.FALLBACKS["Linux"])
                      if fonts.find_font_file(name, fonts.font_dirs())), None)  # fmt: skip
    tmp = Path(tempfile.mkdtemp())
    src = tmp / "src.png"
    _source(src)
    for font in [None, *([installed] if installed else [])]:
        style, _ = thumbs.with_font(thumbs.ThumbStyle(**{**STYLE.__dict__, "font": font}), TEXT)
        for lay in (thumbs.vertical(TEXT, HERO, style), thumbs.landscape(TEXT, HERO, style)):
            px = read_png(bsa.blender.thumbs.render(lay, src, tmp / f"f-{lay.size[0]}.png", style.font))
            cards = [c for c in lay.cards if c.kind in ("title", "kicker")]
            drawn = [_drawn_rows(px, c) for c in cards]
            for c, d in zip(cards, drawn, strict=True):
                assert d is not None and abs(d[0] - c.rect.y) <= 4 and abs(d[1] - c.rect.bottom) <= 4, (
                    font, lay.size, c.kind, c.rect, d)
            assert drawn[1][0] - drawn[0][1] >= thumbs.GAP - 4, (font, lay.size, drawn)
    if installed is None:
        print("SKIP the fallback font: none of bsa.fonts.FALLBACKS is installed")
