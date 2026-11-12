import dataclasses
import importlib.util
import json
from pathlib import Path

import numpy as np
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


def test_bottom_band_when_the_hero_is_high():
    lay = thumbs.vertical(TEXT, Rect(300, 260, 480, 500), STYLE)
    assert lay.placement == "bottom band" and lay.covered <= 0.02 and thumbs.covered(lay.cards, lay.hero) <= 0.02
    assert thumbs.COVERS_SUBJECT not in lay.warnings
    assert max(c.rect.bottom for c in lay.cards) == 1536 and all(_in_safe(c) for c in lay.cards)


def test_neither_band_fits_a_hero_filling_most_of_the_frame():
    lay = thumbs.vertical(TEXT, Rect(100, 150, 880, 1600), STYLE)
    assert lay.warnings == [thumbs.COVERS_SUBJECT] and lay.covered > 0.02
    assert lay.covered == thumbs.covered(lay.cards, lay.hero)


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
    assert lay.src.w == 432
    assert abs(lay.src.x + lay.src.w / 2 - 400) < 1e-6 and abs(lay.src.y + lay.src.h / 2 - 600) < 1e-6


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
    rows = [  # as BLENDER frame-check writes them: a Finding plus its shot, and the redo for frame findings
        {"check": "flicker", "severity": "error", "obj": None, "frame": 41, "message": "flicker", "shot": "s02",
         "redo_frames": [40, 41, 42], "redo": 'BLENDER render "x" --preset final --shot s02 --frames 40-42'},
        {"check": "stuck_frame", "severity": "warning", "obj": None, "frame": 12, "message": "stuck", "shot": "s01",
         "redo_frames": [12], "redo": "..."},
        {"check": "shading", "severity": "warning", "obj": "Lid", "frame": None, "message": "dark", "shot": "s03"},
    ]  # fmt: skip
    data = {"errors": 1, "warnings": 2, "redo_all": "...", "findings": rows}
    (tmp_path / "review" / "frame-checks-final.json").write_text(json.dumps(data), encoding="utf-8")
    assert thumbs.flagged_frames(tmp_path, "final") == {12, 40, 41, 42}
    assert thumbs.flagged_frames(tmp_path, "preview") == set()


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
    assert isinstance(d["cards"][0]["colour"], list) and d["covered"] == 0
    d = thumbs.layout_dict(thumbs.vertical(TEXT, Rect(0, 0, 1080, 1920), STYLE))
    assert d["covered"] > 0.02 and d["warnings"] == [thumbs.COVERS_SUBJECT]


def test_landscape_title_too_long_for_the_panel():
    long = thumbs.ThumbText(("Why the zipper teeth never slip apart", "even under a heavy pull"), "How it works", None)
    lay = thumbs.landscape(long, Rect(400, 900, 300, 400), STYLE)
    assert lay.cards[0].size == thumbs.MIN_TITLE["1280x720"]
    assert lay.warnings == [thumbs.TITLE_TOO_LONG]
    assert thumbs.TITLE_TOO_LONG == "the title doesn't fit beside the subject: shorten it"
    assert all(c.rect.right <= 576 for c in lay.cards)
    assert thumbs.landscape(TEXT, Rect(400, 900, 300, 400), STYLE).warnings == []  # a short title fits


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


# --------------------------------------------------------------------------- the candidates store


def _row(n, colour=None):
    return {"n": n, "hero": {"shot": "s01", "frame": 10 * n, "crop": None, "why": ""}, "source": "final",
            "files": thumbs.files_for(n), "layouts": {}, "findings": [], "warnings": [], "colour": colour}  # fmt: skip


def test_files_and_paths():
    assert thumbs.THUMBS_JSON == "review/thumbs.json" and thumbs.DIR == "review/thumbs"
    assert thumbs.files_for(2) == {"1080x1920": "review/thumbs/2-1080x1920.png",
                                   "1280x720": "review/thumbs/2-1280x720.png"}  # fmt: skip


def test_load_without_and_with_a_damaged_store(tmp_path):
    from bsa.review.store import StoreDamaged

    assert thumbs.load(tmp_path) == {"candidates": [], "chosen": None}
    (tmp_path / "review").mkdir()
    (tmp_path / "review" / "thumbs.json").write_text("{", encoding="utf-8")
    with pytest.raises(StoreDamaged):
        thumbs.load(tmp_path)


def test_put_candidates_replaces_or_remakes_one(tmp_path):
    thumbs.put_candidates(tmp_path, [_row(1), _row(2), _row(3)])
    assert [c["n"] for c in thumbs.load(tmp_path)["candidates"]] == [1, 2, 3]
    data = thumbs.put_candidates(tmp_path, [_row(2, "new")], only=2)
    assert [(c["n"], c["colour"]) for c in data["candidates"]] == [(1, None), (2, "new"), (3, None)]
    assert thumbs.load(tmp_path) == data
    thumbs.put_candidates(tmp_path, [_row(1, "a"), _row(2, "b")])  # all again: two now
    assert [(c["n"], c["colour"]) for c in thumbs.load(tmp_path)["candidates"]] == [(1, "a"), (2, "b")]
    thumbs.put_candidates(tmp_path, [_row(4)], only=4)  # a new one goes in order
    assert [c["n"] for c in thumbs.load(tmp_path)["candidates"]] == [1, 2, 4]
    with pytest.raises(ValueError, match="no candidate 5 among the rows"):
        thumbs.put_candidates(tmp_path, [_row(4)], only=5)
    assert [c["n"] for c in thumbs.load(tmp_path)["candidates"]] == [1, 2, 4]


def test_remaking_the_chosen_candidate_clears_the_choice(tmp_path):
    import pagefix

    sdir = pagefix.make_short(tmp_path, "story")
    pagefix.write_thumbs(sdir, 2)
    path = sdir / thumbs.THUMBS_JSON
    data = json.loads(path.read_text(encoding="utf-8"))
    data["chosen"] = {"n": 2, "files": {"1080x1920": "out/thumbnail-1080x1920.png"}}
    path.write_text(json.dumps(data), encoding="utf-8")
    (sdir / "out").mkdir(exist_ok=True)
    for size in thumbs.SIZES:
        (sdir / "out" / f"thumbnail-{size}.png").write_bytes(b"png")
    row2 = thumbs.load(sdir)["candidates"][1]
    thumbs.put_candidates(sdir, [_row(1)], only=1)  # another one: the choice stays
    assert thumbs.load(sdir)["chosen"]["n"] == 2
    thumbs.put_candidates(sdir, [row2], only=2)
    assert thumbs.load(sdir)["chosen"] is None and (sdir / "out" / "thumbnail-1080x1920.png").is_file()


def test_write_thumbs_fixture(tmp_path):
    import pagefix

    from bsa import image_checks

    sdir = pagefix.make_short(tmp_path, "story")
    pagefix.write_thumbs(sdir, 2)
    data = thumbs.load(sdir)
    assert [c["n"] for c in data["candidates"]] == [1, 2] and data["chosen"] is None
    c2 = data["candidates"][1]
    assert c2["hero"]["shot"] == "s02" and c2["hero"]["frame"] == 30 and c2["source"] == "final"
    assert set(c2["layouts"]) == set(thumbs.SIZES) and c2["layouts"]["1280x720"]["size"] == [1280, 720]
    assert image_checks.png_size(sdir / c2["files"]["1080x1920"]) == (1080, 1920)
    assert image_checks.png_size(sdir / c2["files"]["1280x720"]) == (1280, 720)


# --------------------------------------------------------------------------- how the sequencer draws a layout


def _maps(p, point):
    """Where an image pixel (x, y from the bottom-left) lands in the frame (from the bottom-left), the way the
    sequencer places an image strip: centred at its own size, `origin` the pivot, scaled, then offset."""
    (iw, ih), (fw, fh) = p["in_size"], p["frame"]
    px, py = p["origin"][0] * iw, p["origin"][1] * ih
    return (px + p["offset_x"] + (fw - iw) / 2 + p["scale_x"] * (point[0] - px),
            py + p["offset_y"] + (fh - ih) / 2 + p["scale_y"] * (point[1] - py))  # fmt: skip


@pytest.mark.parametrize("in_size", [(1080, 1920), (540, 960)])
def test_strip_placement_maps_the_source_rect_onto_the_destination(in_size):
    for lay in (thumbs.vertical(TEXT, LOW_HERO, STYLE), thumbs.landscape(TEXT, LOW_HERO, STYLE)):
        p = thumbs.strip_placement(lay.src, lay.dst, in_size, lay.size)
        k = in_size[0] / 1080
        fh = lay.size[1]
        x0, y0 = lay.src.x * k, in_size[1] - lay.src.bottom * k  # the source rect's corners, from the bottom-left
        x1, y1 = lay.src.right * k, in_size[1] - lay.src.y * k
        assert _maps(p, (x0, y0)) == pytest.approx((lay.dst.x, fh - lay.dst.bottom))
        assert _maps(p, (x1, y1)) == pytest.approx((lay.dst.right, fh - lay.dst.y))
        c = p["crop"]  # whole pixels, never more than the source rect leaves out
        assert all(isinstance(v, int) and v >= 0 for v in c.values())
        assert c["left"] <= x0 and c["bottom"] <= y0 and in_size[0] - c["right"] >= x1 - 1e-6
        assert in_size[1] - c["top"] >= y1 - 1e-6


def test_card_strips_place_each_card_in_its_box():
    lay = thumbs.vertical(TEXT, LOW_HERO, _with_handle("@me"))
    strips = thumbs.card_strips(lay)
    assert [s["kind"] for s in strips] == [c.kind for c in lay.cards]
    title = next(s for s in strips if s["kind"] == "title")
    card = next(c for c in lay.cards if c.kind == "title")
    assert title["anchor"] == ("CENTER", "TOP")  # a band: centred
    assert title["loc"] == pytest.approx(((card.rect.x + card.rect.w / 2) / 1080, 1 - (card.rect.y + card.pad) / 1920))
    assert title["margin"] == pytest.approx(card.pad / 1080) and title["wrap_px"] == pytest.approx(thumbs.V_SAFE.w - 56)
    assert title["use_box"] and title["text"] == "Two rows,\none pull" and title["size"] == card.size
    handle = next(s for s in strips if s["kind"] == "handle")
    assert handle["anchor"] == ("LEFT", "TOP") and handle["loc"][0] == pytest.approx((48 + thumbs.PAD["pill"]) / 1080)
    land = thumbs.card_strips(thumbs.landscape(TEXT, LOW_HERO, STYLE))
    assert all(s["anchor"] == ("LEFT", "TOP") for s in land)
    assert land[0]["loc"][0] == pytest.approx((48 + thumbs.PAD["title"]) / 1280)
    clear = thumbs.ThumbStyle(**{**STYLE.__dict__, "title_box": (0.0, 0.0, 0.0, 0.0)})
    assert not thumbs.card_strips(thumbs.vertical(TEXT, LOW_HERO, clear))[0]["use_box"]


def test_heroes_from_post_and_source_frames(tmp_path):
    th = {"title": "x", "heroes": [{"shot": "s01", "frame": 12, "crop": [0, 0, 540, 960]}, {"shot": "s02", "frame": 40}]}
    hs = thumbs.heroes_from_post({"thumbnail": th})
    assert hs == [thumbs.Hero("s01", 12, (0, 0, 540, 960), thumbs.WHY_POST), thumbs.Hero("s02", 40, None, thumbs.WHY_POST)]
    assert thumbs.heroes_from_post({"thumbnail": None}) == [] and thumbs.heroes_from_post({}) == []
    assert thumbs.source_frame(tmp_path, "s01", 12) is None
    import pagefix

    pagefix.write_png(tmp_path / "frames" / "preview" / "s01" / "0012.png", np.zeros((4, 4, 3), np.uint8))
    assert thumbs.source_frame(tmp_path, "s01", 12) == (tmp_path / "frames" / "preview" / "s01" / "0012.png", "preview")
    pagefix.write_png(tmp_path / "frames" / "final" / "s01" / "0012.png", np.zeros((4, 4, 3), np.uint8))
    assert thumbs.source_frame(tmp_path, "s01", 12) == (tmp_path / "frames" / "final" / "s01" / "0012.png", "final")
    assert thumbs.flag_key(tmp_path) == "preview"
    (tmp_path / "review").mkdir()
    (tmp_path / "review" / "frame-checks-final.json").write_text("{}", encoding="utf-8")
    assert thumbs.flag_key(tmp_path) == "final"


@pytest.mark.blender
def test_thumbs_in_blender(blender_run):
    result = blender_run(Path(__file__).resolve().parent / "blender" / "test_thumbs.py", timeout=600)
    assert result.returncode == 0, result.stdout + result.stderr
    for name in ("test_both_sizes_at_exact_pixels_with_the_title_and_hero_where_the_layout_said",
                 "test_a_preview_frame_at_half_size_fills_the_same_layout",
                 "test_drawn_boxes_match_the_cards_in_the_fonts_used",
                 "test_kit_thumbnail_draws_card_images_at_their_rects",
                 "test_kit_thumbnail_with_a_logo_draws_the_badge",
                 "test_without_a_kit_no_card_images_are_made",
                 "test_kit_cards_without_a_card_dir_go_in_a_temp_folder_removed_after_the_render"):  # fmt: skip
        assert f"PASS {name}" in result.stdout


# --------------------------------------------------------------------------- BLENDER thumbs, Blender stubbed

NO_TEXT = "Write the thumbnail text first: short.json post.thumbnail title (one or two lines)"
TH = {"title": ["Why it", "keeps time"], "kicker": "Pendulums"}


def _thumb_short(tmp_path, thumbnail=None):
    """A pendulum short with a 90-frame timeline (s01 1-30, s02 31-60, s03 61-90) and the given post.thumbnail."""
    import pagefix

    sdir = pagefix.make_short(tmp_path, "story")
    path = sdir / "source" / "short.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    if thumbnail is not None:
        data["post"]["thumbnail"] = thumbnail
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    pagefix.write_timeline(sdir, frames=90)
    return sdir


def _frame(sdir, preset, shot, frame):
    import pagefix

    pagefix.write_png(sdir / "frames" / preset / shot / f"{frame:04d}.png", np.full((4, 4, 3), 99, np.uint8))


def _tl(sdir):
    from bsa.timeline import Card, Span, Timeline

    data = json.loads((sdir / "source" / "timeline.json").read_text(encoding="utf-8"))
    return Timeline(data["fps"], data["n_frames"], [Span(**s) for s in data["shots"]],
                    [Span(**s) for s in data["lines"]], [Card(**c) for c in data["cards"]])  # fmt: skip


def _go(monkeypatch, sdir, *argv, boxes=None, s=None):
    """bsa_blender._cmd_thumbs with Blender stubbed: render writes a flat PNG of the layout's size, read_png reads
    it back, and the .blend's hero boxes are `boxes` ({shot: {frame: [box, ...]}}; None: no .blend). `s`: the
    settings ({"handle": "me"} when None)."""
    import types

    import pagefix

    import bsa_blender
    from bsa import image_checks, settings

    monkeypatch.setattr(bsa_blender, "_timeline", lambda sdir_, short: (_tl(sdir), "h" * 16))
    monkeypatch.setattr(settings, "load_settings", lambda: {"handle": "me"} if s is None else s)
    calls = {"render": [], "boxes": [], "card_dir": []}

    def render(lay, src, out, font, cache_dirs=None, notes=None, card_dir=None):
        calls["render"].append((lay, Path(src), Path(out), font))
        calls["card_dir"].append(card_dir)
        pagefix.write_png(out, np.full((lay.size[1], lay.size[0], 3), 128, np.uint8))
        return Path(out)

    def hero_boxes(sdir_, short, tl_hash, shot, frames):
        calls["boxes"].append((shot, list(frames)))
        if boxes is None:
            return None
        return {f: (boxes.get(shot) or {}).get(f, []) for f in frames}

    api = types.SimpleNamespace(render=render, read_png=image_checks.read_png, boxes=hero_boxes)
    args = bsa_blender.build_parser().parse_args(["thumbs", "pendulum", *argv])
    short = bsa_blender._load(sdir)
    return (lambda: bsa_blender._cmd_thumbs(args, sdir, short, api)), calls


def test_thumbs_needs_the_thumbnail_text_and_a_good_post(tmp_path, monkeypatch):
    import re

    import bsa_blender

    sdir = _thumb_short(tmp_path)
    go, _ = _go(monkeypatch, sdir)
    with pytest.raises(bsa_blender.CliError, match="^" + re.escape(NO_TEXT) + "$"):
        go()
    sdir = _thumb_short(tmp_path / "b", {"title": ["a", "b", "c"]})
    go, _ = _go(monkeypatch, sdir)
    with pytest.raises(bsa_blender.CliError, match="one or two non-empty lines"):
        go()


def test_thumbs_named_heroes_make_candidates_in_both_sizes(tmp_path, monkeypatch):
    from bsa import image_checks

    sdir = _thumb_short(tmp_path, TH)
    _frame(sdir, "final", "s01", 15)
    _frame(sdir, "preview", "s03", 70)
    go, calls = _go(monkeypatch, sdir, "--hero", "s01@15", "--hero", "s03@70",
                    boxes={"s01": {15: [[300, 900, 700, 1300]]}})  # fmt: skip
    out = go()
    assert out["thumbs"] == "review/thumbs.json" and [c["n"] for c in out["candidates"]] == [1, 2]
    one, two = out["candidates"]
    assert one["hero"] == {"shot": "s01", "frame": 15, "crop": None, "why": thumbs.WHY_FLAG}
    assert (one["source"], two["source"]) == ("final", "preview")
    busy = [r for r in two["findings"] if r["check"] == "busy"]  # a preview frame: the busy check is skipped
    assert [(r["severity"], r["message"], r["value"]) for r in busy] == [
        ("info", "busy background not measured on a preview frame", None)] * 2
    assert one["files"] == thumbs.files_for(1) and set(one["layouts"]) == set(thumbs.SIZES)
    assert one["layouts"]["1080x1920"]["hero"] != two["layouts"]["1080x1920"]["hero"]
    assert thumbs.FROM_PREVIEW in two["warnings"] and thumbs.NO_HERO in two["warnings"]
    assert thumbs.FROM_PREVIEW not in one["warnings"] and thumbs.NO_HERO not in one["warnings"]
    assert "busy" in {r["check"] for r in one["findings"]}
    assert all(r["size"] in thumbs.SIZES for r in one["findings"])
    for c in out["candidates"]:
        for size, rel in c["files"].items():
            assert image_checks.png_size(sdir / rel) == thumbs.SIZES[size]
    assert [(src.parent.parent.name, src.name) for _, src, _, _ in calls["render"]] == [
        ("final", "0015.png"), ("final", "0015.png"), ("preview", "0070.png"), ("preview", "0070.png")]
    assert f"candidate 2: {thumbs.FROM_PREVIEW}" in out["warnings"]
    assert thumbs.load(sdir)["candidates"] == out["candidates"]


def test_thumbs_with_a_kit_lays_out_the_kit_cards_and_says_a_missing_logo(tmp_path, monkeypatch):
    from tests.kitfix import KIT

    sdir = _thumb_short(tmp_path, TH)
    _frame(sdir, "final", "s01", 15)
    gone = tmp_path / "gone.png"
    go, calls = _go(monkeypatch, sdir, "--hero", "s01@15", s={"handle": "me", "brand": {**KIT,
                    "logo": {"file": str(gone)}}})  # fmt: skip
    out = go()
    lay = out["candidates"][0]["layouts"]
    assert [c["card"] for c in lay["1080x1920"]["cards"]] == ["label", "title", "category"]
    assert [c["card"] for c in lay["1280x720"]["cards"]] == ["title", "category", "label"]
    assert calls["card_dir"] == [sdir / "cards"] * 2  # the edit's card cache
    said = [w for w in out["warnings"] if "no logo badge" in w]
    assert said == [f"your logo file {gone} is missing, so no logo badge is drawn: set brand.logo.file again"]
    go, calls = _go(monkeypatch, sdir, "--hero", "s01@15")  # no kit: today's text cards
    out = go()
    assert all("card" not in c for c in out["candidates"][0]["layouts"]["1080x1920"]["cards"])


def test_thumbs_refuses_bad_heroes_and_missing_frames(tmp_path, monkeypatch):
    import bsa_blender

    sdir = _thumb_short(tmp_path, TH)
    for argv, match in ((["--hero", "s01@99"], r"s01@99 is not in shot s01 \(frames 1 to 30\)"),
                        (["--hero", "s09@1"], "no shot 's09'"), (["--hero", "nope"], "SHOT@FRAME"),
                        (["--hero", "s01@1", "--hero", "s01@2", "--hero", "s02@40", "--hero", "s03@70"], "at most 3"),
                        (["--hero", "s02@40"], (r"^Render the short first: no frame 40 of s02 in frames/final or "
                                                r"frames/preview$"))):  # fmt: skip
        go, calls = _go(monkeypatch, sdir, *argv)
        with pytest.raises(bsa_blender.CliError, match=match):
            go()
        assert calls["render"] == []
    assert not (sdir / thumbs.THUMBS_JSON).exists()


def test_thumbs_heroes_from_short_json_and_a_flagged_frame(tmp_path, monkeypatch):
    sdir = _thumb_short(tmp_path, {**TH, "heroes": [{"shot": "s02", "frame": 41, "crop": [0, 0, 540, 960]}]})
    _frame(sdir, "final", "s02", 41)
    (sdir / "review" / "frame-checks-final.json").write_text(json.dumps(
        {"findings": [{"check": "flicker", "severity": "warning", "frame": 41, "redo_frames": [40, 41, 42]}]}),
        encoding="utf-8")  # fmt: skip
    go, _ = _go(monkeypatch, sdir)
    (c,) = go()["candidates"]
    assert c["hero"] == {"shot": "s02", "frame": 41, "crop": [0, 0, 540, 960], "why": thumbs.WHY_POST}
    assert "frame-check flagged frame 41 of s02" in c["warnings"]


def test_thumbs_picks_heroes_without_names(tmp_path, monkeypatch):
    sdir = _thumb_short(tmp_path, TH)
    for shot, frames in (("s01", range(1, 31)), ("s02", range(31, 61)), ("s03", range(61, 91))):
        for f in frames:
            _frame(sdir, "preview", shot, f)
    (sdir / "review" / "frame-checks-preview.json").write_text(json.dumps({"findings": [{"frame": 15}]}),
                                                               encoding="utf-8")  # fmt: skip
    go, calls = _go(monkeypatch, sdir, boxes={"s03": {86: [[100, 200, 1000, 1500]]}, "s01": {1: [[500, 500, 600, 600]]}})
    out = go()
    # the longest shot (all are 30 frames long: the last wins) at its middle; the hook shot (s01) at its fullest
    # sample; the fullest frame elsewhere (s03@86) is too near the first pick
    assert [(c["hero"]["shot"], c["hero"]["frame"]) for c in out["candidates"]] == [("s03", 75), ("s01", 1)]
    assert ("s02", [31, 36, 41, 46, 51, 56, 60]) in calls["boxes"]  # sampled every 5th frame from the .blend
    assert [c["hero"]["why"] for c in out["candidates"]] == ["the middle of the longest shot",
                                                             "the hook shot's fullest frame"]  # fmt: skip
    (sdir / "review" / "frame-checks-preview.json").write_text(json.dumps({"findings": [{"frame": 75}]}),
                                                               encoding="utf-8")  # fmt: skip
    go, _ = _go(monkeypatch, sdir, boxes={"s03": {86: [[100, 200, 1000, 1500]]}, "s01": {1: [[500, 500, 600, 600]]}})
    assert go()["candidates"][0]["hero"]["frame"] == 74  # a flagged frame is never picked


def test_thumbs_only_remakes_one_and_clears_its_choice(tmp_path, monkeypatch):
    import pagefix

    import bsa_blender

    sdir = _thumb_short(tmp_path, TH)
    go, _ = _go(monkeypatch, sdir, "--only", "3")
    with pytest.raises(bsa_blender.CliError, match="^There is no candidate 3 yet: run BLENDER thumbs SHORT first$"):
        go()
    pagefix.write_thumbs(sdir, 2)
    path = sdir / thumbs.THUMBS_JSON
    data = json.loads(path.read_text(encoding="utf-8"))
    path.write_text(json.dumps({**data, "chosen": {"n": 1}}), encoding="utf-8")
    _frame(sdir, "final", "s01", 15)  # candidate 1's own hero: s01@15 (the fixture's)
    go, _ = _go(monkeypatch, sdir, "--only", "1")
    out = go()
    assert [c["n"] for c in out["candidates"]] == [1] and out["candidates"][0]["hero"]["frame"] == 15
    store = thumbs.load(sdir)
    assert [c["n"] for c in store["candidates"]] == [1, 2] and store["chosen"] is None
    assert store["candidates"][1]["hero"]["shot"] == "s02"  # candidate 2 untouched
    _frame(sdir, "final", "s03", 75)
    go, _ = _go(monkeypatch, sdir, "--only", "2", "--hero", "s03@75")
    assert go()["candidates"][0]["hero"]["frame"] == 75
    go, _ = _go(monkeypatch, sdir, "--only", "2", "--hero", "s03@75", "--hero", "s01@15")
    with pytest.raises(bsa_blender.CliError, match="one --hero"):
        go()
    data = json.loads(path.read_text(encoding="utf-8"))
    data["candidates"][1]["hero"] = {"frame": 40}  # a damaged row: no shot
    path.write_text(json.dumps(data), encoding="utf-8")
    go, _ = _go(monkeypatch, sdir, "--only", "2")
    with pytest.raises(bsa_blender.CliError, match=r"review/thumbs.json is damaged \(candidate 2 has no hero shot "
                                                   r"and frame\)"):  # fmt: skip
        go()


def test_thumbs_only_uses_short_json_heroes_only_when_the_candidates_came_from_them(tmp_path, monkeypatch):
    import pagefix

    sdir = _thumb_short(tmp_path, {**TH, "heroes": [{"shot": "s03", "frame": 75}]})
    pagefix.write_thumbs(sdir, 1)  # candidate 1: s01@15, made from short.json's heroes
    _frame(sdir, "final", "s01", 15)
    _frame(sdir, "final", "s03", 75)
    go, _ = _go(monkeypatch, sdir, "--only", "1")
    assert go()["candidates"][0]["hero"]["frame"] == 75  # short.json's hero changed since: it is the one remade
    path = sdir / thumbs.THUMBS_JSON
    data = json.loads(path.read_text(encoding="utf-8"))
    data["candidates"][0]["hero"] = {"shot": "s01", "frame": 15, "crop": None, "why": "the hook shot's fullest frame"}
    path.write_text(json.dumps(data), encoding="utf-8")  # picked by the plugin, not from short.json
    go, _ = _go(monkeypatch, sdir, "--only", "1")
    (c,) = go()["candidates"]
    assert (c["hero"]["shot"], c["hero"]["frame"]) == ("s01", 15)  # its own hero, not short.json's first
    data["candidates"][0]["hero"]["why"] = thumbs.WHY_FLAG  # named with --hero: also its own
    path.write_text(json.dumps(data), encoding="utf-8")
    go, _ = _go(monkeypatch, sdir, "--only", "1")
    assert go()["candidates"][0]["hero"]["frame"] == 15


def test_thumbs_all_again_drops_old_candidate_files(tmp_path, monkeypatch):
    import pagefix

    sdir = _thumb_short(tmp_path, TH)
    pagefix.write_thumbs(sdir, 3)
    _frame(sdir, "final", "s01", 15)
    go, _ = _go(monkeypatch, sdir, "--hero", "s01@15")
    go()
    assert sorted(p.name for p in (sdir / thumbs.DIR).iterdir()) == ["1-1080x1920.png", "1-1280x720.png"]


# --------------------------------------------------------------------------- cards as tall as the font draws them

ASCII = "".join(map(chr, range(32, 127)))
SEGOE_LIKE = (1.33, 1.709)  # a tall font: Segoe UI's line height and bounding box


def _metrics(style, line_h, box_h):
    return thumbs.ThumbStyle(**{**style.__dict__, "line_h": line_h, "box_h": box_h})


def test_card_height_follows_the_font():
    for line_h, box_h in ((thumbs.BLENDER_LINE_H, thumbs.BLENDER_BOX_H), SEGOE_LIKE):
        lay = thumbs.vertical(TEXT, LOW_HERO, _metrics(STYLE, line_h, box_h))
        title, kicker = lay.cards[0], lay.cards[1]
        assert title.rect.h == pytest.approx((box_h + line_h) * title.size + 2 * thumbs.PAD["title"])  # two lines
        assert kicker.rect.h == pytest.approx(box_h * kicker.size + 2 * thumbs.PAD["pill"])


def _apart(a, b) -> float:
    """The vertical clear space between two cards that share columns (inf when they don't)."""
    if a.rect.right <= b.rect.x or b.rect.right <= a.rect.x:
        return float("inf")
    return max(b.rect.y - a.rect.bottom, a.rect.y - b.rect.bottom)


def test_cards_in_one_layout_never_meet_and_keep_the_gap():
    long = thumbs.ThumbText(("Why the zipper teeth never slip apart", "even under a heavy pull"), "How it works",
                            "Teeth made to 0.01 mm")  # fmt: skip
    for text in (TEXT, long, thumbs.ThumbText(("One",), None, None)):
        for style in (STYLE, _with_handle("@someone"), _metrics(_with_handle("@me"), *SEGOE_LIKE)):
            for hero in (LOW_HERO, Rect(300, 200, 400, 300), Rect(0, 0, 1080, 1920), Rect(400, 900, 300, 400)):
                for lay in (thumbs.vertical(text, hero, style), thumbs.landscape(text, hero, style)):
                    for i, a in enumerate(lay.cards):
                        for b in lay.cards[i + 1 :]:
                            assert _apart(a, b) >= thumbs.GAP - 1e-6, (lay.placement, a, b)


def test_with_font_resolves_the_file_and_its_metrics(tmp_path):
    from fontbuild import build_font

    m = {"upm": 1000, "box": (-400, 1300), "hhea": (1000, -300, 0), "typo": (800, -200, 0), "use_typo": False}
    main = tmp_path / "Brand.ttf"
    main.write_bytes(build_font(ASCII, family="Brand", metrics=m))
    branded = thumbs.ThumbStyle(**{**_with_handle("@me").__dict__, "font": str(main)})
    style, notes = thumbs.with_font(branded, TEXT, dirs=[tmp_path], system="Linux")
    assert (style.font, style.line_h, style.box_h, notes) == (str(main), 1.3, 1.7, [])
    other = tmp_path / "DejaVuSans.ttf"  # the Linux fallback, with every glyph
    other.write_bytes(build_font(ASCII + "É", family="DejaVu Sans",
                                 metrics={**m, "hhea": (900, -300, 0)}))  # fmt: skip
    accented = thumbs.ThumbText(("Éclair",), None, None)
    style, notes = thumbs.with_font(thumbs.ThumbStyle(**{**STYLE.__dict__, "font": str(main)}), accented,
                                    dirs=[tmp_path], system="Linux")  # fmt: skip
    assert Path(style.font) == other and style.line_h == pytest.approx(1.2) and len(notes) == 1
    assert "DejaVu Sans" in notes[0]
    assert thumbs.with_font(STYLE, TEXT) == (STYLE, [])  # no style font: Blender's own, its measured metrics


# --------------------------------------------------------------------------- choosing one


def _short_json(sdir) -> Path:
    return sdir / "source" / "short.json"


def test_choose_copies_both_sizes_and_records_it(tmp_path):
    import pagefix

    from bsa import postpack
    from bsa.short import load_short

    sdir = pagefix.make_short(tmp_path, "post")
    pagefix.write_thumbs(sdir, 2)
    before = json.loads(_short_json(sdir).read_text(encoding="utf-8"))
    out = thumbs.choose(sdir, 2, "page", now=1234.5)
    assert out == {"chosen": 2, "files": list(postpack.OUT_FILES.values())}
    for size, rel in postpack.OUT_FILES.items():
        assert (sdir / rel).read_bytes() == (sdir / f"review/thumbs/2-{size}.png").read_bytes()
    assert thumbs.load(sdir)["chosen"] == {"n": 2, "by": "page", "at": 1234.5}
    assert load_short(_short_json(sdir)).post["thumbnail"] == {"chosen": 2}
    after = json.loads(_short_json(sdir).read_text(encoding="utf-8"))
    assert {k: v for k, v in after.items() if k != "post"} == {k: v for k, v in before.items() if k != "post"}
    assert {k: v for k, v in after["post"].items() if k != "thumbnail"} == before["post"]
    assert not list((sdir / "out").glob("*.tmp")) and not list((sdir / "out").glob(".*.tmp"))


def test_choose_keeps_the_thumbnail_text_and_the_line_endings(tmp_path):
    import pagefix

    sdir = pagefix.make_short(tmp_path, "post")
    pagefix.write_thumbs(sdir, 2)
    data = json.loads(_short_json(sdir).read_text(encoding="utf-8"))
    data["post"]["thumbnail"] = {"title": "Why it keeps time", "heroes": [], "chosen": None}
    _short_json(sdir).write_bytes((json.dumps(data, indent=2, ensure_ascii=False) + "\n").replace("\n", "\r\n")
                                  .encode("utf-8"))  # fmt: skip
    thumbs.choose(sdir, 1, "chat")
    raw = _short_json(sdir).read_bytes()
    assert raw.count(b"\r\n") == raw.count(b"\n")  # CRLF stays CRLF
    th = json.loads(raw.decode("utf-8"))["post"]["thumbnail"]
    assert th == {"title": "Why it keeps time", "heroes": [], "chosen": 1}


def test_choose_refuses_a_missing_candidate_or_file(tmp_path):
    import pagefix

    sdir = pagefix.make_short(tmp_path, "post")
    with pytest.raises(ValueError, match="There is no thumbnail candidate 1"):
        thumbs.choose(sdir, 1, "page")
    pagefix.write_thumbs(sdir, 2)
    before = _short_json(sdir).read_bytes()
    with pytest.raises(ValueError, match="There is no thumbnail candidate 5"):
        thumbs.choose(sdir, 5, "page")
    (sdir / "review/thumbs/2-1280x720.png").unlink()
    with pytest.raises(ValueError, match="review/thumbs/2-1280x720.png"):
        thumbs.choose(sdir, 2, "page")
    with pytest.raises(ValueError, match="page or chat"):
        thumbs.choose(sdir, 1, "settings")
    assert thumbs.load(sdir)["chosen"] is None and _short_json(sdir).read_bytes() == before
    assert not (sdir / "out" / "thumbnail-1080x1920.png").exists()


def test_two_choices_at_once_leave_one_consistent_pair(tmp_path):  # Review Focus 3
    import threading

    import pagefix

    from bsa import postpack
    from bsa.short import load_short

    sdir = pagefix.make_short(tmp_path, "post")
    pagefix.write_thumbs(sdir, 2)
    for _ in range(5):
        go = threading.Barrier(2)
        errors = []

        def pick(n, go=go, errors=errors):
            go.wait()
            try:
                thumbs.choose(sdir, n, "page")
            except Exception as e:  # noqa: BLE001 - collected for the assert
                errors.append(e)

        threads = [threading.Thread(target=pick, args=(n,)) for n in (1, 2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors
        n = thumbs.load(sdir)["chosen"]["n"]
        for size, rel in postpack.OUT_FILES.items():
            assert (sdir / rel).read_bytes() == (sdir / f"review/thumbs/{n}-{size}.png").read_bytes()
        assert load_short(_short_json(sdir)).post["thumbnail"]["chosen"] == n


def test_choose_changes_nothing_while_short_json_is_locked(tmp_path, monkeypatch):
    import pagefix

    from bsa.review import store

    sdir = pagefix.make_short(tmp_path, "post")
    pagefix.write_thumbs(sdir, 2)
    monkeypatch.setattr(thumbs, "LOCK_S", 0.2)
    before = _short_json(sdir).read_bytes()
    with store.locked(_short_json(sdir)), pytest.raises(TimeoutError):
        thumbs.choose(sdir, 1, "page")
    assert thumbs.load(sdir)["chosen"] is None and _short_json(sdir).read_bytes() == before
    assert not (sdir / "out" / "thumbnail-1080x1920.png").exists()
    assert not (sdir / "out" / "thumbnail-1280x720.png").exists()


def test_a_failed_short_json_write_puts_back_the_earlier_choice(tmp_path, monkeypatch):
    import pagefix

    from bsa import jobstate, postpack

    sdir = pagefix.make_short(tmp_path, "post")
    pagefix.write_thumbs(sdir, 2)
    thumbs.choose(sdir, 1, "page", now=1.0)
    store_before = (sdir / thumbs.THUMBS_JSON).read_bytes()
    short_before = _short_json(sdir).read_bytes()
    real = jobstate.write_atomic

    def failing(path, data, *a, **k):
        if Path(path).name == "short.json":
            raise OSError("disk full")
        return real(path, data, *a, **k)

    monkeypatch.setattr(jobstate, "write_atomic", failing)
    with pytest.raises(OSError, match="disk full"):
        thumbs.choose(sdir, 2, "page", now=2.0)
    assert (sdir / thumbs.THUMBS_JSON).read_bytes() == store_before
    assert _short_json(sdir).read_bytes() == short_before
    for size, rel in postpack.OUT_FILES.items():
        assert (sdir / rel).read_bytes() == (sdir / f"review/thumbs/1-{size}.png").read_bytes()
    monkeypatch.setattr(jobstate, "write_atomic", real)
    thumbs.choose(sdir, 2, "page")  # the locks were let go
    assert thumbs.load(sdir)["chosen"]["n"] == 2


# --------------------------------------------------------------------------- with a brand kit


@pytest.fixture
def short():
    return _short()


@pytest.fixture
def font(tmp_path):
    """Every printable ASCII character, each 0.5 em wide; line 1.0 em, box 1.2 em."""
    import string

    from fontbuild import build_font

    p = tmp_path / "card.ttf"
    p.write_bytes(build_font(string.ascii_letters + string.digits + string.punctuation + " ", advance=500))
    return p


def _png(path, w, h):
    import struct
    import zlib

    raw = b"".join(b"\0" + b"\xff" * (4 * w) for _ in range(h))
    chunk = lambda t, d: struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d))
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
                     + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))  # fmt: skip
    return path


HERO = Rect(400, 900, 300, 400)
KTEXT = thumbs.ThumbText(("Two rows,", "one pull"), "How a zipper works", None)


def _kit_style(short, font, **brand):
    from dataclasses import replace

    from tests.kitfix import KIT

    return replace(thumbs.style_for(short, {"handle": "me", "brand": {**KIT, **brand}}), font=str(font))


def test_without_a_kit_layouts_are_unchanged(short):
    from tests.kitfix import KIT

    plain = {"handle": "me", "brand": {"mode": "brand", "palette": dict(KIT["palette"])}}
    spark = {"handle": "me", "brand": {"mode": "brand", "palette": {**KIT["palette"], "spark": "#D9483B"}}}
    a, b = thumbs.style_for(short, plain), thumbs.style_for(short, spark)
    assert a.kit is None and b.kit is None
    for make in (thumbs.vertical, thumbs.landscape):
        da, db = thumbs.layout_dict(make(TEXT, HERO, a)), thumbs.layout_dict(make(TEXT, HERO, b))
        assert da == db and all("card" not in c for c in da["cards"])
        assert all(c.spec is None for c in make(TEXT, HERO, a).cards)
    assert thumbs.with_font(a, TEXT) == (a, [])  # no font, no kit: Blender's own font as before


def test_kit_thumbnail_cards_are_kit_cards(short, font):
    from bsa import cards

    st = _kit_style(short, font)
    assert st.kit is not None and st.kit.style == "toon"
    lay = thumbs.vertical(KTEXT, HERO, st)
    assert {c.kind: c.spec.kind for c in lay.cards} == {"title": "title", "kicker": "category", "handle": "label"}
    d = thumbs.layout_dict(lay)
    assert [c["card"] for c in d["cards"]] == ["label", "title", "category"]  # the @handle badge comes first
    assert json.loads(json.dumps(d)) == d
    for c in lay.cards:
        assert (c.rect.w, c.rect.h) == (c.spec.w, c.spec.h) and _in_safe(c)
        assert c.box == overlays.hex_colour(c.spec.fill) and c.colour == overlays.hex_colour(c.spec.runs[0].colour)
    title = next(c for c in lay.cards if c.kind == "title")
    assert title.spec.fill == "#FFFFFF" and title.spec.outline == "#1B2A3A"
    assert [r.colour for r in title.spec.runs] == ["#1B2A3A", "#2E7D6B"]  # the second line highlighted
    assert title.spec.runs[0].size == title.size and title.spec.runs[1].size > title.size
    kicker = next(c for c in lay.cards if c.kind == "kicker")
    assert kicker.spec.fill == "#7A4CE0" and kicker.spec.runs[0].size == cards.SIZES["thumb_kicker"]
    handle = next(c for c in lay.cards if c.kind == "handle")
    assert handle.spec.runs[0].text == "@me" and (handle.rect.x, handle.rect.y) == (48, 115.2)
    fact = thumbs.vertical(TEXT, HERO, st)
    got = next(c for c in fact.cards if c.kind == "fact")
    assert got.spec.kind == "measure" and got.spec.runs[0].size == cards.SIZES["thumb_fact"]
    assert got.spec.runs[0].colour == "#2E7D6B"
    land = thumbs.landscape(KTEXT, HERO, st)
    assert [c.spec.kind for c in land.cards] == ["title", "category", "label"]
    assert all(c.rect.right <= thumbs.PANEL_W and c.rect.x == 48 for c in land.cards)
    assert land.panel_colour == overlays.hex_colour("#EAF2F8") and land.warnings == []
    assert thumbs.card_strips(lay) == [] and thumbs.card_strips(land) == []  # drawn as images, not text strips


def test_kit_cards_keep_off_the_subject_and_apart(short, font):
    st = _kit_style(short, font)
    long = thumbs.ThumbText(("Why the zipper teeth never slip apart", "even under a heavy pull"), "How it works",
                            "Teeth made to 0.01 mm")  # fmt: skip
    for text in (TEXT, KTEXT, long, thumbs.ThumbText(("One",), None, None)):
        for hero in (LOW_HERO, Rect(300, 200, 400, 300), Rect(0, 0, 1080, 1920), HERO):
            for lay in (thumbs.vertical(text, hero, st), thumbs.landscape(text, hero, st)):
                assert lay.covered == thumbs.covered(lay.cards, lay.hero)
                if lay.size == thumbs.VERTICAL:
                    assert all(_in_safe(c) for c in lay.cards), lay.cards
                else:
                    assert all(c.rect.right <= thumbs.PANEL_W for c in lay.cards)
                for i, a in enumerate(lay.cards):
                    for b in lay.cards[i + 1 :]:
                        assert _apart(a, b) >= thumbs.GAP - 1e-6, (lay.placement, a, b)
    lay = thumbs.vertical(TEXT, LOW_HERO, st)
    assert lay.covered <= thumbs.COVER_LIMIT and lay.warnings == []
    assert thumbs.vertical(TEXT, Rect(0, 0, 1080, 1920), st).warnings == [thumbs.COVERS_SUBJECT]


def test_kit_thumbnail_with_a_logo_uses_the_logo_badge(tmp_path, short, font):
    from bsa import cards

    logo = _png(tmp_path / "logo.png", 200, 100)
    st = _kit_style(short, font, logo={"file": str(logo), "name": "Acme"})
    for lay in (thumbs.vertical(KTEXT, HERO, st), thumbs.landscape(KTEXT, HERO, st)):
        handle = next(c for c in lay.cards if c.kind == "handle")
        assert handle.spec.kind == "logo" and handle.spec.logo == str(logo.resolve())
        assert handle.spec.runs[0].text == "Acme" and handle.spec.box_h == cards.LOGO_H
        assert thumbs.layout_dict(lay)["cards"][[c.kind for c in lay.cards].index("handle")]["card"] == "logo"
    st = _kit_style(short, font, logo={"file": str(logo)})  # no name: the @handle beside the logo
    handle = next(c for c in thumbs.vertical(KTEXT, HERO, st).cards if c.kind == "handle")
    assert handle.spec.kind == "logo" and handle.spec.runs[0].text == "@me"
    gone = tmp_path / "gone.png"
    st = _kit_style(short, font, logo={"file": str(logo)})
    st = thumbs.ThumbStyle(**{**st.__dict__, "kit": dataclasses.replace(st.kit, logo={"file": str(gone)})})
    handle = next(c for c in thumbs.vertical(KTEXT, HERO, st).cards if c.kind == "handle")
    assert handle.spec.kind == "label"  # the logo file is gone: the @handle pill
    _, notes = thumbs.with_font(st, KTEXT)
    assert any("no logo badge" in n for n in notes)


def test_a_logo_badge_too_wide_for_the_panel_drops_its_name(tmp_path, short, font):
    logo = _png(tmp_path / "logo.png", 200, 100)
    st = _kit_style(short, font, logo={"file": str(logo), "name": "A rather long company name here"})
    for lay in (thumbs.vertical(KTEXT, HERO, st), thumbs.landscape(KTEXT, HERO, st)):
        handle = next(c for c in lay.cards if c.kind == "handle")
        assert handle.spec.kind == "logo"
        if lay.size == thumbs.LANDSCAPE:
            assert handle.spec.runs == () and handle.rect.right <= thumbs.PANEL_W  # the logo alone
        else:
            assert handle.spec.runs[0].text == "A rather long company name here" and _in_safe(handle)
    wide = _png(tmp_path / "wide.png", 2000, 100)  # even alone it is wider than the panel: the @handle pill
    st = _kit_style(short, font, logo={"file": str(wide)})
    handle = next(c for c in thumbs.landscape(KTEXT, HERO, st).cards if c.kind == "handle")
    assert handle.spec.kind == "label" and handle.rect.right <= thumbs.PANEL_W


def test_with_a_kit_a_font_name_is_resolved_not_read_as_a_file(short, monkeypatch):
    from bsa import cards
    from tests.kitfix import KIT

    st = thumbs.style_for(short, {"brand": {**KIT, "font": "No Such Font"}})
    assert st.kit.font == "No Such Font" and st.font == "No Such Font"
    seen = []
    monkeypatch.setattr(cards, "card_font_and_note", lambda kit, cache_dirs=None: seen.append(kit.font) or (None,
                        "no No Such Font: Blender's own"))  # fmt: skip
    got, notes = thumbs.with_font(st, KTEXT)
    assert seen == ["No Such Font"] and got.font is None and notes == ["no No Such Font: Blender's own"]


def test_with_font_and_a_kit_uses_the_card_font(short, font, monkeypatch):
    from bsa import cards, fonts

    st = _kit_style(short, font)
    monkeypatch.setattr(cards, "card_font_and_note", lambda kit, cache_dirs=None: (font, "a note"))
    got, notes = thumbs.with_font(thumbs.ThumbStyle(**{**st.__dict__, "font": None}), KTEXT)
    assert got.font == str(font) and (got.line_h, got.box_h) == fonts.line_metrics(font) and notes == ["a note"]
    accented = thumbs.ThumbText(("Éclair",), None, None)  # the test font has no É
    _, notes = thumbs.with_font(st, accented)
    assert any("Éclair" in n for n in notes)


def test_hidden_captions_never_hide_the_thumbnail_title():
    """Shorts 20 and 21: a short that hides the plugin's captions (#FFFFFF00 on #20232600, its own cards draw them)
    got an invisible thumbnail title. The title falls back to white on the dark box, and says so."""
    st = thumbs.style_for(_short({"caption": {"color": "#FFFFFF00", "box": "#20232600"}}), {})
    assert st.title == (1.0, 1.0, 1.0, 1.0) and st.title_box == (*overlays.BOX_COLOR[:3], 0.85)
    assert st.notes and "caption" in st.notes[0]
    _, notes = thumbs.with_font(st, TEXT)
    assert notes[: len(st.notes)] == list(st.notes)  # the thumbs command reports it with its warnings
    st = thumbs.style_for(_short({"caption": {"color": "#FFFFFF", "box": "#20232600"}}), {})
    assert st.title == (1.0, 1.0, 1.0, 1.0) and st.title_box[3] == 0.0 and not st.notes  # a see-through box is a choice
    st = thumbs.style_for(_short({"caption": {"color": "#FFFFFF00", "box": "#202326"}}), {})
    assert st.title == (1.0, 1.0, 1.0, 1.0) and st.title_box == overlays.hex_colour("#202326", 1.0) and st.notes
