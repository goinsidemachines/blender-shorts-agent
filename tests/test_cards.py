"""Cards, pure: sizes from real font advances, the builders per card kind, the shapes as pixels and the cache key."""

import os
import re
import string
from pathlib import Path

import numpy as np
import pytest
from fontbuild import build_font

from bsa import cards, fonts, kit
from bsa.layout import safe_rect
from tests.kitfix import KIT

K = kit.kit_from(KIT)
HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
INK = (0x1B, 0x2A, 0x3A)


@pytest.fixture
def font(tmp_path):
    """Every printable ASCII character, each 500 units wide (0.5 em), upm 1000; line 1.0 em, box 1.2 em."""
    p = tmp_path / "card.ttf"
    p.write_bytes(build_font(string.ascii_letters + string.digits + string.punctuation, advance=500))
    return p


def test_measure_wrap_and_height_use_the_font(font):
    assert cards.measure("ABAB", 40, font) == pytest.approx(80)
    assert cards.BLENDER_CHAR_W == 0.60  # Blender 5.2's own font: monospaced, measured in Blender
    assert cards.measure("ABAB", 40, None) == pytest.approx(4 * 40 * 0.60)
    assert cards.wrap("aa bb cc", 10, font, 25) == ["aa bb", "cc"]  # "aa bb" is 5 chars = 25 px
    assert cards.wrap("aa " + "x" * 10 + " bb", 10, font, 25) == ["aa", "x" * 10, "bb"]  # a long word on its own
    assert cards.text_height(1, 40, font) == pytest.approx(1.2 * 40)
    assert cards.text_height(3, 40, font) == pytest.approx((1.2 + 2 * 1.0) * 40)
    assert cards.text_height(2, 10, None) == pytest.approx((fonts.BLENDER_BOX_H + fonts.BLENDER_LINE_H) * 10)


def test_a_font_without_readable_widths_and_blenders_font_metrics(tmp_path, font):
    junk = tmp_path / "junk.ttf"
    junk.write_bytes(b"not a font")
    assert cards.measure("abc", 10, junk) == pytest.approx(3 * 10 * cards.BLENDER_CHAR_W)
    assert cards.metrics(None) == (fonts.BLENDER_LINE_H, fonts.BLENDER_BOX_H)
    assert cards.metrics(font) == pytest.approx((1.0, 1.2))
    assert cards.text_drop(None) == cards.BLENDER_TEXT_DROP == 0.045 and cards.text_drop(font) == 0.0


def test_a_pill_is_its_text_plus_padding(font):
    c = cards.pill(K, "label", "ABAB", font)
    assert c.w == round(4 * 0.5 * 40 + 2 * cards.PILL_PAD[0]) and c.h == c.box_h + K.shadow_px
    assert c.box_h == round(1.2 * 40 + 2 * cards.PILL_PAD[1])
    assert c.fill == "#FFFFFF" and c.outline == "#1B2A3A" and c.outline_px == 4 and c.radius == c.box_h / 2
    (run,) = c.runs
    assert (run.text, run.size, run.colour, run.anchor) == ("ABAB", 40, "#1B2A3A", "CENTER")
    assert run.x == pytest.approx(c.w / 2) and run.y == pytest.approx(c.box_h / 2)  # centred on the box, not shadow
    assert c.font == str(font)
    assert cards.pill(K, "measure", "3 m", font).runs[0].colour == K.palette["highlight"]
    assert cards.pill(kit.kit_from({**KIT, "pill_corner_px": 10}), "badge", "A", font).radius == 10


def test_a_part_label_with_a_dot_is_wider_by_the_dot(font):
    plain = cards.pill(K, "label", "Gear", font)
    dotted = cards.pill(K, "label", "Gear", font, dot="#D9483B")
    assert dotted.w == plain.w + cards.DOT_D + cards.DOT_GAP and dotted.dot == "#D9483B"
    (run,) = dotted.runs
    assert run.anchor == "LEFT" and run.x == pytest.approx(cards.PILL_PAD[0] + cards.DOT_D + cards.DOT_GAP)


def test_caption_is_the_full_width_and_wraps_by_real_widths(font):
    c = cards.caption(K, "word " * 30, font, 900)
    assert c.w == safe_rect().w  # never wider than the safe area
    c = cards.caption(K, "aaaa bbbb cccc", font, 600)
    size, pad = K.caption["size"], K.caption["pad_px"]
    room = 600 - 2 * pad
    assert c.w == 600 and c.radius == K.caption["corner_px"] and c.shadow_px == 0 and c.h == c.box_h
    assert [r.text for r in c.runs] == cards.wrap("aaaa bbbb cccc", size, font, room) == ["aaaa bbbb cccc"]
    assert len(cards.caption(K, "aaaa " * 12, font, 600).runs) == 4  # 3 words (14 chars at 32 px) fit in 530
    assert c.box_h == round(cards.text_height(len(c.runs), size, font) + 2 * pad)
    assert c.fill == "#1B2A3A" and all(r.colour == "#FFFFFF" for r in c.runs)
    half = cards.caption(kit.kit_from({**KIT, "caption": {**KIT["caption"], "opacity": 0.5}}), "a", font, 600)
    assert half.fill == "#1B2A3A80"
    assert cards.shape_rgba(half)[half.box_h // 2, half.w // 2, 3] == 128


def test_an_8_digit_caption_box_keeps_its_alpha_times_the_opacity(font):
    """brand.caption.box "#1B2A3A80" is half see-through; with opacity 0.5 on top it is a quarter (one alpha, never a
    second one appended)."""
    see = {**KIT, "caption": {**KIT["caption"], "box": "#1b2a3a80", "color": "#FFFFFFCC"}}
    c = cards.caption(kit.kit_from(see), "a", font, 600)
    assert c.fill == "#1B2A3A80" and c.runs[0].colour == "#FFFFFFCC"
    q = cards.caption(kit.kit_from({**see, "caption": {**see["caption"], "opacity": 0.5}}), "a", font, 600)
    assert q.fill == "#1B2A3A40"
    assert cards.shape_rgba(q)[q.box_h // 2, q.w // 2, 3] == 64


def test_title_highlight_line_is_bigger_and_shrinks_to_fit(font):
    c = cards.title(K, ("Push", "Lift"), 1, font, safe_rect().w)
    assert [(r.text, r.size, r.colour) for r in c.runs] == [
        ("Push", cards.SIZES["title"], K.palette["text"]),
        ("Lift", cards.SIZES["title_highlight"], K.palette["highlight"])]
    assert c.runs[0].y < c.runs[1].y and c.fill == K.palette["card"] and c.radius == K.corner_px
    room = 600 - 2 * cards.CARD_PAD
    c = cards.title(K, ("x" * 12,), None, font, 600)  # 12 * 45 = 540 px at 90 in 504: shrinks to 84
    assert c.runs[0].size == int(cards.SIZES["title"] * room / (12 * 45)) and len(c.runs) == 1
    c = cards.title(K, ("aaaa bbbb cccc dddd eeee",), None, font, 600)  # at TITLE_MIN still too wide: wraps
    assert all(r.size == cards.TITLE_MIN for r in c.runs) and len(c.runs) > 1
    assert all(cards.measure(r.text, r.size, font) <= room for r in c.runs)


def test_no_card_is_wider_than_the_safe_area(font):
    long_bars = {"items": [{"label": "z" * 70, "value": 2}, {"label": "b", "value": 1}], "verdict": "v" * 99}
    for c in (cards.title(K, ("W" * 60,), None, font, safe_rect().w), cards.pill(K, "category", "x" * 80, font),
              cards.pill(K, "label", "a long near label that keeps going and going", font),
              cards.caption(K, "y" * 90, font, 2000), cards.bars(K, long_bars, font, 2000),
              cards.progress_track(K, 2000)):  # fmt: skip
        assert c.w <= safe_rect().w, c.kind
        pad = {"title": cards.CARD_PAD, "bars": cards.CARD_PAD, "caption": K.caption["pad_px"]}.get(c.kind,
                                                                                                cards.PILL_PAD[0])
        assert all(cards.measure(r.text, r.size, font) <= c.w - 2 * pad + 1e-6 for r in c.runs), c.kind
    near = cards.pill(K, "label", "a long near label that keeps going and going", font)
    assert len(near.runs) > 1 and near.w <= cards.NEAR_WRAP_PX + 2 * cards.PILL_PAD[0]


def test_a_card_with_a_missing_glyph_uses_the_cover_font(monkeypatch, font, tmp_path):
    other = tmp_path / "other.ttf"
    other.write_bytes(build_font("Push lift→", family="Coverly", advance=800))
    seen = []

    def cover(text, main, *a, **k):
        seen.append((text, main))
        return other

    monkeypatch.setattr(fonts, "cover_font", cover)
    path, note = cards.font_for("Push → lift", font)
    assert path == other and "'→' (U+2192)" in note and "Coverly" in note and "Push → lift" in note
    assert cards.font_for("Push", font) == (font, None)
    assert cards.font_for("Push →", None) == (None, None)
    c = cards.title(K, ("Push → lift",), None, font, safe_rect().w)
    assert c.font == str(other) and cards.measure("Push → lift", 10, other) == pytest.approx(11 * 0.8 * 10)
    assert c.w == round(cards.measure("Push → lift", c.runs[0].size, other) + 2 * cards.CARD_PAD)
    monkeypatch.setattr(fonts, "cover_font", lambda *a, **k: None)
    path, note = cards.font_for("Push → lift", font)
    assert path == font and "no installed font" in note


def test_shape_pixels_fill_outline_shadow_and_transparent_corners(font):
    c = cards.pill(K, "label", "AB", font)
    px = cards.shape_rgba(c)
    assert px.shape == (c.h, c.w, 4) and px.dtype == np.uint8 and px[0, 0, 3] == 0
    assert tuple(px[c.box_h // 2, c.w // 2, :3]) == (255, 255, 255)
    assert tuple(px[c.box_h // 2, 1, :3]) == INK  # the outline
    assert tuple(px[c.h - 2, c.w // 2, :3]) == INK and px[c.h - 2, c.w // 2, 3] == 255  # the shadow
    edge = px[c.box_h // 2, : c.w // 2, 3]
    assert 0 < px[c.box_h // 2, 0, 3] <= 255 and edge.min() > 0  # a full row inside the box, its edge antialiased


def test_the_dot_is_its_colour_ringed_in_ink(font):
    c = cards.pill(K, "label", "AB", font, dot="#D9483B")
    px = cards.shape_rgba(c)
    cx, cy = round(cards.PILL_PAD[0] + cards.DOT_D / 2), round(c.box_h / 2)
    assert tuple(px[cy, cx, :3]) == (0xD9, 0x48, 0x3B)
    assert tuple(px[cy, cx - cards.DOT_D // 2 + 1, :3]) == INK


def test_category_tag_reads_on_its_colour(font):
    c = cards.pill(K, "category", "Spark", font)
    assert c.fill == "#D9483B" and c.runs[0].colour == "#FFFFFF"  # on_colour: white on a dark red
    light = kit.kit_from({**KIT, "categories": {"spark": "#F2E86D"}})
    assert cards.pill(light, "category", "Spark", font).runs[0].colour == "#14161A"
    assert cards.pill(K, "category", "Other", font).fill == K.palette["accent"]


def test_bars_lengths_follow_values(font):
    spec = {"items": [{"label": "Lever", "value": 2, "category": "flow"},
                      {"label": "Ramp", "value": 4, "text": "4 kg"}],
            "verdict": "Ramp wins"}
    c = cards.bars(K, spec, font, 800)
    (x1, y1, w1, h1, col1), (x2, y2, w2, h2, col2) = c.bars
    assert w2 == pytest.approx(2 * w1) and x1 == x2 and y2 > y1 and h1 == h2 == cards.BAR_H
    assert col1 == "#3B8ED9" and col2 == K.palette["accent"]
    texts = [r.text for r in c.runs]
    assert texts == ["Lever", "2", "Ramp", "4 kg", "Ramp wins"]
    assert c.runs[-1].colour == K.palette["highlight"] and c.runs[-1].size == cards.SIZES["verdict"]
    assert x2 + w2 + cards.DOT_GAP + cards.measure("4 kg", cards.SIZES["bar"], font) <= c.w - cards.CARD_PAD + 1e-6
    assert cards.bars(K, {**spec, "max": 8}, font, 800).bars[1][2] == pytest.approx(w2 / 2)
    px = cards.shape_rgba(c)
    assert tuple(px[round(y1 + h1 / 2), round(x1 + w1 / 2), :3]) == (0x3B, 0x8E, 0xD9)


def test_progress_pointer_and_dot(font):
    track, fill = cards.progress_track(K, 700), cards.progress_fill(K, 700)
    assert (track.w, track.box_h, track.fill, track.outline) == (700, cards.PROGRESS_H, "#FFFFFF", "#1B2A3A")
    assert (fill.w, fill.box_h, fill.fill, fill.outline) == (700 - 8, cards.PROGRESS_H - 8, K.palette["accent"], None)
    line = cards.pointer(K, 120.4)
    assert (line.w, line.box_h, line.fill) == (120, 4, "#1B2A3A")
    assert cards.pointer(kit.kit_from({**KIT, "outline_px": 1}), 50).box_h == 3
    d = cards.dot(K)
    assert d.w == d.box_h == cards.POINTER_DOT and d.fill == "#1B2A3A" and d.radius == cards.POINTER_DOT / 2


def _png(path, w, h):
    import struct
    import zlib

    raw = b"".join(b"\0" + b"\xff" * (4 * w) for _ in range(h))
    chunk = lambda t, d: struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d))
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
                     + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))  # fmt: skip
    return path


def test_logo_badge_keeps_the_logos_aspect(font, tmp_path):
    logo = _png(tmp_path / "logo.png", 200, 100)
    k = kit.kit_from({**KIT, "logo": {"file": str(logo)}})
    c = cards.logo_badge(k, font, None)
    x, y, w, h = c.logo_rect
    assert (w, h) == (2 * cards.LOGO_IMG, cards.LOGO_IMG) and c.box_h == cards.LOGO_H and c.logo == str(logo.resolve())
    assert y == pytest.approx((cards.LOGO_H - cards.LOGO_IMG) / 2) and c.runs == ()
    named = cards.logo_badge(k, font, "Acme")
    assert named.runs[0].text == "Acme" and named.runs[0].size == cards.SIZES["logo_name"]
    assert named.w > c.w and named.runs[0].x >= x + w
    assert cards.logo_badge(K, font, "Acme") is None  # no logo set: no badge


def test_cache_key_changes_with_text_kit_and_font_file(font, tmp_path):
    a = cards.pill(K, "label", "AB", font)
    assert cards.cache_key(a) == cards.cache_key(cards.pill(K, "label", "AB", font)) and len(cards.cache_key(a)) == 20
    assert cards.cache_key(a) != cards.cache_key(cards.pill(K, "label", "BA", font))
    thin = kit.kit_from({**KIT, "outline_px": 2})
    assert cards.cache_key(a) != cards.cache_key(cards.pill(thin, "label", "AB", font))
    before = cards.cache_key(a)
    st = font.stat()
    os.utime(font, ns=(st.st_atime_ns, st.st_mtime_ns + 10**9))
    assert cards.cache_key(a) != before
    assert cards.CARD_VERSION == 2


def test_card_font_falls_back_to_an_os_font(monkeypatch, tmp_path):
    d = tmp_path / "fonts"
    d.mkdir()
    (d / "arial.ttf").write_bytes(build_font("A"))
    monkeypatch.setattr(fonts, "font_dirs", lambda: [d])
    monkeypatch.setattr(cards.platform, "system", lambda: "Windows")
    assert cards.card_font(K) == d / "arial.ttf"  # no kit font: the first installed OS fallback
    (d / "Brandy.ttf").write_bytes(build_font("A"))
    assert cards.card_font(kit.kit_from({**KIT, "font": "Brandy"}), cache_dirs=[]) == d / "Brandy.ttf"
    (d / "arial.ttf").unlink()
    assert cards.card_font(K) is None  # nothing installed: Blender's own font


def test_a_long_bar_label_wraps_at_a_readable_size(font):
    spec = {"items": [{"label": "Fixed pulley with one rope", "value": 2}, {"label": "Lever", "value": 4}]}
    c = cards.bars(K, spec, font, safe_rect().w)
    label = [r for r in c.runs if r.text not in ("2", "4", "Lever")]
    assert len(label) == 2 and all(r.size == cards.BAR_LABEL_MIN for r in label)  # two lines at the floor size
    assert " ".join(r.text for r in label) == "Fixed pulley with one rope"
    (x1, y1, _, h1, _), (x2, y2, _, _, _) = c.bars
    first_mid = (label[0].y + label[1].y) / 2
    assert x1 == x2 and y1 + h1 / 2 == pytest.approx(first_mid)  # the bar sits in the middle of its taller row
    assert y2 - y1 > cards.BAR_H + cards.BAR_GAP  # the first row grew
    room = (c.w - 2 * cards.CARD_PAD) * cards.BAR_LABEL_SHARE - cards.DOT_GAP
    assert all(cards.measure(r.text, r.size, font) <= room + 1e-6 for r in label)


def test_a_bar_value_that_is_not_a_number_counts_as_zero(font):
    c = cards.bars(K, {"items": [{"label": "a", "value": "lots"}, {"label": "b", "value": 2}]}, font, 800)
    assert c.bars[0][2] == 0 and c.bars[1][2] > 0


def test_a_very_wide_logo_is_scaled_to_the_safe_width(font, tmp_path):
    logo = _png(tmp_path / "wide.png", 400, 20)  # 20:1
    k = kit.kit_from({**KIT, "logo": {"file": str(logo)}})
    c = cards.logo_badge(k, font, "Acme")
    _, y, w, h = c.logo_rect
    side = (cards.LOGO_H - cards.LOGO_IMG) / 2
    assert w == pytest.approx(safe_rect().w - 2 * side) and h == pytest.approx(w / 20)
    assert y == pytest.approx((cards.LOGO_H - h) / 2) and c.w <= safe_rect().w
    assert c.runs == ()  # no room left for the name


@pytest.mark.blender
def test_cards_in_blender(blender_run):
    result = blender_run(HERE / "blender" / "test_cards.py", timeout=600)
    assert result.returncode == 0, result.stdout + result.stderr
    for name in ("test_a_card_png_is_its_size_with_exact_kit_colours_and_transparent_corners",
                 "test_card_text_is_drawn_inside_the_box", "test_the_logo_shows_on_the_logo_badge",
                 "test_ensure_renders_once", "test_rendering_cards_leaves_every_engine_alone",
                 "test_overlay_strips_are_placed_where_the_layout_said", "test_pointer_strip_reaches_the_anchor",
                 "test_blenders_own_font_fits_its_padding_and_is_centred",
                 "test_a_tracked_card_without_a_track_is_left_out",
                 "test_a_scene_named_card_is_left_alone"):
        assert f"PASS {name}" in result.stdout, result.stdout + result.stderr


def test_no_kit_code_touches_the_render_engine():
    for f in ("bsa/kit.py", "bsa/cards.py", "bsa/kitlayout.py", "bsa/thumbs.py", "bsa/closing.py", "bsa/overlays.py",
              "bsa/blender/cards.py", "bsa/blender/edit.py", "bsa/blender/thumbs.py", "bsa/blender/closing_card.py"):
        assert not re.search(r"\.engine\s*=|render\.engine", (ROOT / f).read_text(encoding="utf-8")), f


def test_card_font_and_note_resolves_once_and_says_why():
    from bsa import kit

    k = kit.kit_from(KIT)
    path, note = cards.card_font_and_note(k)
    assert path == cards.card_font(k) and note is None  # no kit font: the first OS fallback, nothing to say
    missing = kit.kit_from({**KIT, "font": "No Such Font Anywhere"})
    path, note = cards.card_font_and_note(missing)
    assert note and "No Such Font Anywhere" in note and path == cards.card_font(missing)
