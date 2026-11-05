"""Guides drawn on a copy of a frame (bsa.guides): the caption zone, the top cards and the framing band."""

import string

import numpy as np
import pytest
from fontbuild import build_font

from bsa import guides, kit
from bsa.image_checks import read_png
from bsa.layout import caption_band, target_area
from bsa.short import Short, Shot
from tests.kitfix import KIT


def _short(**shot):
    s = Shot(id="a", title="t", picture="p", scene="Main", **shot)
    return Short(slug="x", title="X", length_s=2, shots=[s], style={})


def _rows_with(img, colour):
    return set(np.nonzero(np.all(img[..., :3] == colour, axis=-1).any(axis=1))[0].tolist())


@pytest.mark.parametrize("scale", [1.0, 0.4])
def test_guides_are_drawn_in_the_right_rows(scale):
    h, w = round(1920 * scale), round(1080 * scale)
    frame = np.zeros((h, w, 3), np.uint8)
    short = _short()
    boxes = guides.rects(short, short.shots[0])
    assert set(boxes) == {"caption", "framing"}
    out = guides.draw(frame, boxes)
    assert not frame.any()  # a copy: the frame itself is unchanged
    cap, band = caption_band(), target_area()
    yellow = np.array(guides.COLOURS["caption"])
    top = round(cap.y * scale)
    x0, x1 = round(cap.x * scale), round(cap.right * scale)
    near = [r for r in range(top - 1, top + 2) if np.all(out[r, x0:x1] == yellow)]
    assert near, f"no caption line across row {top}"
    bottom = round(cap.bottom * scale) - 1
    assert any(np.all(out[r, x0:x1] == yellow) for r in range(bottom - 1, bottom + 2))
    magenta = guides.COLOURS["framing"]
    assert any(abs(r - band.y * scale) <= 1 for r in _rows_with(out, magenta))
    assert min(_rows_with(out, magenta)) >= round(band.y * scale) - 1
    assert not _rows_with(out, guides.COLOURS["top_cards"])


def test_draw_keeps_alpha_and_line_width():
    frame = np.zeros((1920, 1080, 4), np.uint8)
    out = guides.draw(frame, {"caption": caption_band()})
    cap = caption_band()
    col = round(cap.x) + 100
    rows = np.nonzero(out[:, col, 0] == 255)[0]
    assert len(rows) == 2 * guides.LINE_PX and out[rows[0], col, 3] == 255
    small = guides.draw(np.zeros((192, 108, 3), np.uint8), {"caption": cap})  # 0.1 scale: still 1 px
    assert len(np.nonzero(small[:, round(cap.x * 0.1) + 10, 0] == 255)[0]) == 2


def test_top_cards_only_with_a_kit(tmp_path):
    font = tmp_path / "card.ttf"
    font.write_bytes(build_font(string.ascii_letters + string.digits + string.punctuation, advance=500))
    k = kit.kit_from(KIT)
    short = _short(badge="simplified", category="flow", title_card={"lines": ["How a lever"], "highlight": 0})
    boxes = guides.rects(short, short.shots[0], k, font)
    assert set(boxes) == {"caption", "top_cards", "framing"}
    assert boxes["framing"].y > boxes["top_cards"].bottom
    assert boxes["framing"].y > target_area().y  # the title card reaches further down than the plain badge row
    out = guides.draw(np.zeros((1920, 1080, 3), np.uint8), boxes)
    assert _rows_with(out, guides.COLOURS["top_cards"])
    bare = _short()
    plain = guides.rects(bare, bare.shots[0], k, font)  # a kit, but no top cards in this shot
    assert set(plain) == {"caption", "framing"} and plain["framing"] == target_area(top=None)


@pytest.mark.parametrize("channels", [3, 4])
def test_write_png_round_trips(tmp_path, channels):
    rng = np.random.default_rng(1)
    a = rng.integers(0, 256, (37, 23, channels), dtype=np.uint8)
    p = guides.write_png(tmp_path / "x.png", a)
    assert p == tmp_path / "x.png" and not (tmp_path / "x.png.part").exists()
    assert np.array_equal(read_png(p), a)


def test_guides_path_sits_beside_the_frame(tmp_path):
    assert guides.guides_path(tmp_path / "s01-050-f0012.png") == tmp_path / "s01-050-f0012-guides.png"
    assert guides.guides_path(tmp_path / "s01.jpg") == tmp_path / "s01-guides.png"
