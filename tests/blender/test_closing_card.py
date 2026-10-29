import sys
import tempfile
from pathlib import Path

import numpy as np

from bsa import closing
from bsa.blender import closing_card, edit
from bsa.blender.pixels import read_png

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_edit import _fixture, _write_png

BRAND = {"palette": {"background": "#E7EEF6", "accent": "#FF6B00"}}


def test_brand_card_from_the_website_alone():
    tmp = Path(tempfile.mkdtemp())
    cfg = closing.resolve({"website": "example.com", "handle": "me"})
    png = closing_card.make_card(cfg, BRAND, tmp / "closing.png")
    px = read_png(png)
    assert px.shape[:2] == (1920, 1080)
    assert tuple(px[50, 50, :3]) == (0xE7, 0xEE, 0xF6)  # the brand background
    orange = (np.abs(px[..., 0].astype(int) - 0xFF) < 3) & (np.abs(px[..., 1].astype(int) - 0x6B) < 3)
    assert orange.sum() > 20000  # the pill in the accent colour
    ys, xs = np.nonzero(np.any(px[..., :3] != px[50, 50, :3], axis=2))
    assert ys.max() < 1920 * (1 - closing.COVERED_BOTTOM) and xs.max() < 1080 * (1 - closing.COVERED_RIGHT)
    assert closing.zone_warnings(px) == []


def test_user_image_fills_the_frame():
    tmp = Path(tempfile.mkdtemp())
    src = tmp / "mine.png"
    _write_png(src, (0.9, 0.1, 0.1))
    cfg = closing.resolve({"closing": {"image": str(src)}})
    px = read_png(closing_card.make_card(cfg, {}, tmp / "closing.png"))
    assert px.shape[:2] == (1920, 1080) and px[960, 540, 0] > 200 and px[960, 540, 1] < 60


def test_edit_ends_on_the_closing_card():
    short, tl, frames, wav, out = _fixture()
    tmp = Path(tempfile.mkdtemp())
    png = closing_card.make_card(closing.resolve({"website": "example.com"}), BRAND, tmp / "closing.png")
    end = {"f0": tl.n_frames + 1, "n_frames": 20, "png": str(png)}
    sc = edit.build_edit(short, tl, frames, wav, out, {"website": "example.com"}, preset="preview", closing=end)
    assert sc.frame_end == 50
    assert not [s for s in sc.sequence_editor.strips_all if s.type == "TEXT" and s.channel >= edit.CH_END]
    info = edit.probe(edit.encode(sc))
    assert info["frames"] == 50
