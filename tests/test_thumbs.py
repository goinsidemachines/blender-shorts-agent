import importlib.util
import json

import pytest

from bsa import closing, overlays, thumbs
from bsa.layout import Rect
from bsa.short import Short
from bsa.timeline import Span

STYLE = thumbs.ThumbStyle(
    title=(1.0, 1.0, 1.0, 1.0), title_box=(0.0, 0.0, 0.0, 0.55), kicker=(0.0, 0.0, 0.0, 1.0),
    kicker_box=(1.0, 0.8, 0.0, 1.0), fact=(1.0, 1.0, 1.0, 1.0), fact_box=(0.0, 0.0, 0.0, 0.85),
    handle=(1.0, 0.8, 0.35, 1.0), handle_box=(0.0, 0.0, 0.0, 0.85), panel=(0.08, 0.09, 0.1, 1.0),
    font=None, handle_text=None,
)  # fmt: skip
TEXT = thumbs.ThumbText(("Two rows,", "one pull"), "How a zipper works", "Teeth made to 0.01 mm")
# A hero whose top sits just under the top band's cards at zoom 1: zooming in pushes it clear.
LOW_HERO = Rect(300, 660, 480, 500)


def _with_handle(handle):
    return thumbs.ThumbStyle(**{**STYLE.__dict__, "handle_text": handle})


def _in_safe(c) -> bool:
    return c.rect.y >= 115.2 and c.rect.bottom <= 1536 and c.rect.right <= 950.4 and c.rect.x >= 48


def test_vertical_cards_stay_in_the_safe_box():
    lay = thumbs.vertical(TEXT, Rect(400, 900, 300, 400), STYLE)
    assert all(_in_safe(c) for c in lay.cards)
    assert lay.size == (1080, 1920) and lay.warnings == []
    assert lay.dst == Rect(0, 0, 1080, 1920) and lay.panel is None


def test_cards_clear_the_hero():
    lay = thumbs.vertical(TEXT, LOW_HERO, STYLE)
    assert thumbs.covered(lay.cards, lay.hero) <= 0.02 and lay.warnings == []


def test_zoom_moves_the_hero_before_the_placement_changes():
    lay = thumbs.vertical(TEXT, LOW_HERO, STYLE)
    assert lay.placement == "top band" and lay.src.w < 1080
    assert abs(lay.src.w / lay.src.h - 1080 / 1920) < 1e-6
    # the window stays inside the frame
    assert lay.src.x >= 0 and lay.src.y >= 0 and lay.src.right <= 1080 and lay.src.bottom <= 1920


def test_middle_left_when_zoom_cannot_clear_the_top_band():
    lay = thumbs.vertical(TEXT, Rect(700, 120, 380, 1700), STYLE)  # tall hero on the right edge
    assert lay.placement == "middle-left" and lay.warnings == []
    assert all(c.rect.x == 48 for c in lay.cards)


def test_fallback_order_then_the_warning():
    lay = thumbs.vertical(TEXT, Rect(0, 0, 1080, 1920), STYLE)
    assert lay.placement == "smaller title" and lay.warnings == [thumbs.COVERS_SUBJECT]
    assert thumbs.COVERS_SUBJECT == "the title covers the subject"


def test_handle_badge_only_with_a_handle():
    lay = thumbs.vertical(TEXT, Rect(400, 900, 300, 400), STYLE)
    assert "handle" not in [c.kind for c in lay.cards]
    lay = thumbs.vertical(TEXT, Rect(400, 900, 300, 400), _with_handle("@me"))
    badge = next(c for c in lay.cards if c.kind == "handle")
    assert (badge.rect.x, badge.rect.y) == (48, 115.2) and badge.text == "@me"
    title = next(c for c in lay.cards if c.kind == "title")
    assert title.rect.y >= badge.rect.bottom + thumbs.GAP
    assert all(_in_safe(c) for c in lay.cards)


def test_landscape_hero_right_text_in_the_panel():
    lay = thumbs.landscape(TEXT, Rect(400, 900, 300, 400), STYLE)
    assert lay.dst == Rect(576, 0, 704, 720) and all(c.rect.right <= 576 for c in lay.cards)
    assert abs(lay.src.w / lay.src.h - 704 / 720) < 0.01 and "fact" not in [c.kind for c in lay.cards]
    assert lay.size == (1280, 720) and lay.panel == Rect(0, 0, 576, 720) and lay.panel_colour == STYLE.panel
    assert all(c.rect.x == 48 for c in lay.cards)
    top, bottom = min(c.rect.y for c in lay.cards), max(c.rect.bottom for c in lay.cards)
    assert abs((top + bottom) / 2 - 360) < 1e-6  # the block is centred in the panel
    assert thumbs.covered(lay.cards, lay.hero) == 0
    # centred on the hero, sized around it
    assert abs(lay.src.x + lay.src.w / 2 - 550) < 1e-6 and abs(lay.src.y + lay.src.h / 2 - 1100) < 1e-6
    assert lay.src.w == pytest.approx(max(300, 400 * 704 / 720) * 1.25)


def test_landscape_window_clamps_and_carries_the_backdrop():
    lay = thumbs.landscape(TEXT, Rect(0, 1800, 50, 50), _with_handle("@me"), backdrop="set.png")
    assert lay.src.w == 432 and lay.src.x == 0 and lay.src.bottom == 1920 and lay.backdrop == "set.png"
    assert [c.kind for c in lay.cards] == ["title", "kicker", "handle"]


def test_crop_sets_the_window():
    lay = thumbs.vertical(TEXT, Rect(400, 900, 300, 400), STYLE, crop=(100, 200, 700, 1000))
    s = lay.src
    assert s.x <= 100 and s.y <= 200 and s.right >= 700 and s.bottom >= 1000
    assert abs(s.w / s.h - 9 / 16) < 1e-6
    lay = thumbs.landscape(TEXT, Rect(400, 900, 300, 400), STYLE, crop=(300, 500, 500, 700))
    assert lay.src.w == 432 and abs(lay.src.x + lay.src.w / 2 - 400) < 1e-6 and abs(lay.src.y + lay.src.h / 2 - 600) < 1e-6


def test_long_title_line_shrinks_but_not_below_the_minimum():
    text = thumbs.ThumbText(("A longer title line",), None, None)
    size = next(c for c in thumbs.vertical(text, Rect(400, 900, 300, 400), STYLE).cards if c.kind == "title").size
    assert thumbs.MIN_TITLE["1080x1920"] < size < thumbs.V_SIZES["title"]
    text = thumbs.ThumbText(("Pneumonoultramicroscopicsilicovolcanoconiosis everywhere",), None, None)
    size = next(c for c in thumbs.vertical(text, Rect(400, 900, 300, 400), STYLE).cards if c.kind == "title").size
    assert size == thumbs.MIN_TITLE["1080x1920"]
    size = next(c for c in thumbs.landscape(text, Rect(400, 900, 300, 400), STYLE).cards if c.kind == "title").size
    assert size == thumbs.MIN_TITLE["1280x720"]


def test_no_hero_uses_the_centre():
    assert thumbs.hero_box([None, None]) == (Rect(216, 384, 648, 1152), [thumbs.NO_HERO])
    assert thumbs.NO_HERO == "no subject box at this frame, so the centre 60 % of the frame is used"
    assert thumbs.hero_box([[10, 20, 110, 220], None, [-50, 100, 60, 2500]]) == (Rect(0, 20, 110, 1900), [])


def test_pick_heroes_payoff_hook_fill():
    spans = [Span("s01", 1, 60), Span("s02", 61, 200), Span("s03", 201, 260)]
    picked = thumbs.pick_heroes(spans, set(), {31: 0.5, 241: 0.9}, "s01")
    assert [(h.shot, h.frame) for h in picked] == [("s02", 130), ("s01", 31), ("s03", 241)]
    assert [h.why for h in picked] == ["the middle of the longest shot", "the hook shot's fullest frame",
                                       "the frame where the subject fills the most of the safe area"]  # fmt: skip


def test_pick_heroes_skips_flagged_frames():
    spans = [Span("s01", 1, 60), Span("s02", 61, 200), Span("s03", 201, 260)]
    picked = thumbs.pick_heroes(spans, {130, 31}, {31: 0.5, 36: 0.4, 241: 0.9}, "s01")
    assert [(h.shot, h.frame) for h in picked] == [("s02", 129), ("s01", 36), ("s03", 241)]


def test_pick_heroes_keeps_candidates_apart():
    picked = thumbs.pick_heroes([Span("s01", 1, 90)], set(), {1: 0.2, 51: 0.3, 86: 0.9}, "s01")
    frames = [h.frame for h in picked]
    assert len(picked) <= 2 and all(abs(a - b) >= thumbs.MIN_APART for a in frames for b in frames if a != b)
    assert frames == [45, 86]


def test_sample_frames_and_fill():
    assert thumbs.sample_frames(1, 12) == [1, 6, 11, 12]
    assert thumbs.sample_frames(5, 5) == [5]
    assert thumbs.fill(None) == 0 and thumbs.fill(thumbs.V_SAFE) == 1.0
    assert thumbs.fill(Rect(0, 0, 1080, 1920)) == 1.0


def test_flagged_frames(tmp_path):
    (tmp_path / "review").mkdir()
    assert thumbs.flagged_frames(tmp_path, "final") == set()
    rows = [{"frame": 12, "redo_frames": [40, 41]}, {"frame": None}, {"check": "x"}]
    (tmp_path / "review" / "frame-checks-final.json").write_text(json.dumps({"findings": rows}), encoding="utf-8")
    assert thumbs.flagged_frames(tmp_path, "final") == {12, 40, 41}


def test_check_heroes_names_the_frame_range():
    assert thumbs.check_heroes([thumbs.parse_hero("s03@9999")], {"s03": Span("s03", 241, 330)}) == [
        "s03@9999 is not in shot s03 (frames 241 to 330)"]
    assert thumbs.check_heroes([thumbs.Hero("s09", 3)], {"s03": Span("s03", 241, 330)}) == [
        "no shot 's09' in short.json"]
    assert thumbs.check_heroes([thumbs.Hero("s03", 241)], {"s03": Span("s03", 241, 330)}) == []


def test_parse_hero():
    assert thumbs.parse_hero("s03@289") == thumbs.Hero("s03", 289)
    for bad in ("s03", "s03@x", "@12"):
        with pytest.raises(ValueError, match="SHOT@FRAME"):
            thumbs.parse_hero(bad)


def _short(style=None):
    return Short(slug="t", title="T", length_s=4, style=style or {})


def test_style_follows_the_captions_and_the_brand():
    st = thumbs.style_for(_short({"caption": {"color": "#112233"}}), {})
    assert st.title == (0x11 / 255, 0x22 / 255, 0x33 / 255, 1.0) and st.title_box == overlays.BOX_COLOR
    assert st.kicker_box == overlays.hex_colour(closing.ACCENT) and st.kicker == closing.on_colour(st.kicker_box)
    assert st.fact_box == (*overlays.BOX_COLOR[:3], 0.85) and st.fact == (1.0, 1.0, 1.0, 1.0)
    assert st.handle == overlays.BADGE_COLOR and st.handle_box == st.fact_box
    assert st.panel == overlays.hex_colour(closing.BACKGROUND) and st.handle_text is None and st.font is None
    s = {"handle": "@me", "brand": {"mode": "brand", "font": "Inter", "palette": {"accent": "#102030",
                                                                                 "background": "#FFFFFF"}}}
    st = thumbs.style_for(_short(), s)
    assert st.kicker_box == overlays.hex_colour("#102030") and st.kicker == closing.LIGHT
    assert st.panel == (1.0, 1.0, 1.0, 1.0) and st.handle_text == "@me" and st.font == "Inter"
    st = thumbs.style_for(_short({"palette": {"accent": "#FFFFFF"}, "font": "Mono"}), s)  # the short's own wins
    assert st.kicker_box == (1.0, 1.0, 1.0, 1.0) and st.kicker == closing.DARK and st.font == "Mono"


def test_text_from_post():
    p = {"thumbnail": {"title": ["Two rows,", "one pull"], "kicker": "How a zipper works", "fact": " "}}
    assert thumbs.text_from_post(p) == thumbs.ThumbText(("Two rows,", "one pull"), "How a zipper works", None)
    assert thumbs.text_from_post({"thumbnail": {"title": "One\nTwo"}}).title == ("One", "Two")
    assert thumbs.text_from_post({"thumbnail": None}) is None
    assert thumbs.text_from_post({"thumbnail": {"title": ""}}) is None


def test_only_the_users_text_and_handle_are_drawn():
    for lay in (thumbs.vertical(TEXT, Rect(400, 900, 300, 400), _with_handle("@me")),
                thumbs.landscape(TEXT, Rect(400, 900, 300, 400), _with_handle("@me"))):  # fmt: skip
        texts = sorted(c.text for c in lay.cards)
        want = ["Two rows,\none pull", "How a zipper works", "@me"]
        if lay.size == thumbs.VERTICAL:
            want.append("Teeth made to 0.01 mm")
        assert texts == sorted(want)


def test_layout_dict_is_json():
    d = thumbs.layout_dict(thumbs.landscape(TEXT, Rect(400, 900, 300, 400), STYLE, backdrop=None))
    assert json.loads(json.dumps(d)) == d
    assert d["dst"] == [576, 0, 704, 720] and d["panel"] == [0, 0, 576, 720] and d["size"] == [1280, 720]
    assert d["cards"][0]["kind"] == "title" and len(d["cards"][0]["rect"]) == 4
    assert isinstance(d["cards"][0]["colour"], list)


def test_constants():
    assert thumbs.SIZES == {"1080x1920": (1080, 1920), "1280x720": (1280, 720)}
    assert thumbs.V_SAFE == Rect(48, 115.2, 902.4, 1420.8)
    assert thumbs.FROM_PREVIEW == "from the preview: remake after the final render"


def test_moved_colour_helpers_keep_their_old_names():
    assert overlays.hex_colour("#FF0000") == (1.0, 0.0, 0.0, 1.0)
    assert closing.on_colour((1.0, 1.0, 1.0, 1.0)) == closing.DARK
    if importlib.util.find_spec("bpy") is None:
        pytest.skip("bpy is not installed: the Blender modules re-export these, checked in Blender")
    from bsa.blender import closing_card, edit

    assert edit._hex_colour is overlays.hex_colour and edit.caption_colours is overlays.caption_colours
    assert edit.BOX_COLOR == overlays.BOX_COLOR and edit.BADGE_COLOR == overlays.BADGE_COLOR
    assert closing_card._on is closing.on_colour and closing_card.BACKGROUND == closing.BACKGROUND
    assert closing_card.ACCENT == closing.ACCENT and (closing_card.DARK, closing_card.LIGHT) == (closing.DARK,
                                                                                               closing.LIGHT)
