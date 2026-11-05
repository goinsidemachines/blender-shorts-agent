import sys
import tempfile
from pathlib import Path

import numpy as np

from bsa import closing, kit
from bsa.blender import closing_card, edit
from bsa.blender.pixels import read_png

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from kitfix import KIT
from test_edit import _fixture, _write_png

K = kit.kit_from(KIT)
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


def test_kit_closing_card_background_and_pill_colours():
    tmp = Path(tempfile.mkdtemp())
    cfg = closing.resolve({"website": "example.com", "handle": "maker"})
    png = closing_card.make_card(cfg, BRAND, tmp / "closing.png", kit=K, card_dir=tmp / "cards")
    px = read_png(png)
    assert px.shape[:2] == (1920, 1080)
    bg = np.abs(px[50, 50, :3].astype(int) - np.array([0xEA, 0xF2, 0xF8]))
    assert (bg <= 1).all()  # the kit's background, not the brand's
    accent = (np.abs(px[..., :3].astype(int) - np.array([0x7A, 0x4C, 0xE0])) <= 2).all(axis=2)
    assert accent.sum() > 10000  # the website pill in the kit's accent
    ys, xs = np.nonzero(np.any(np.abs(px[..., :3].astype(int) - px[50, 50, :3].astype(int)) > 2, axis=2))
    assert ys.min() >= closing.TOP_PX - 2 and ys.max() < 1920 * (1 - closing.COVERED_BOTTOM)
    assert xs.max() < 1080 * (1 - closing.COVERED_RIGHT)
    assert closing.zone_warnings(px) == []
    assert any((tmp / "cards").glob("*.png"))  # the cards are cached for the next time


def test_kit_never_restyles_the_users_own_image():
    tmp = Path(tempfile.mkdtemp())
    src = tmp / "mine.png"
    _write_png(src, (0.9, 0.1, 0.1))
    cfg = closing.resolve({"closing": {"image": str(src)}})
    px = read_png(closing_card.make_card(cfg, {}, tmp / "closing.png", kit=K, card_dir=tmp / "cards"))
    assert px[960, 540, 0] > 200 and px[960, 540, 1] < 60 and not (tmp / "cards").exists()


def test_closing_card_without_a_kit_is_unchanged():
    tmp = Path(tempfile.mkdtemp())
    cfg = closing.resolve({"website": "example.com", "handle": "me"})
    png = closing_card.make_card(cfg, BRAND, tmp / "closing.png", kit=None, card_dir=tmp / "cards")
    px = read_png(png)
    assert tuple(px[50, 50, :3]) == (0xE7, 0xEE, 0xF6)  # the brand background, the text rows as before
    assert not (tmp / "cards").exists()  # no card images drawn
    plain = read_png(closing_card.make_card(cfg, BRAND, tmp / "again.png"))
    assert np.array_equal(px, plain)


def test_kit_closing_card_without_a_card_dir_uses_a_temp_folder_removed_after_the_render():
    tmp = Path(tempfile.mkdtemp())
    (tmp / "t").mkdir()
    saved, tempfile.tempdir = tempfile.tempdir, str(tmp / "t")
    try:
        png = closing_card.make_card(closing.resolve({"website": "example.com"}), BRAND, tmp / "closing.png", kit=K)
    finally:
        tempfile.tempdir = saved
    assert read_png(png).shape[:2] == (1920, 1080)
    assert not list((tmp / "t").iterdir())
