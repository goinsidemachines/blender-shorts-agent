"""Where the kit's cards, pointer lines and the progress bar sit, frame by frame (bsa.kitlayout): today's caption,
corner stack and near label anchors kept."""

import math
import string

import pytest
from fontbuild import build_font

from bsa import cards, kit, kitlayout, overlays
from bsa.layout import CAPTION_LIFT_PX, SAFE, H, Rect, inside_safe, overlap_area, safe_rect
from bsa.overlays import STACK_GAP_PX
from bsa.short import Short, Shot
from bsa.timeline import Card, Span, Timeline
from tests.kitfix import KIT

K = kit.kit_from(KIT)
BARS = {"items": [{"label": "Short arm", "value": 1}, {"label": "Long arm", "value": 3}], "verdict": "3x the lift"}


@pytest.fixture
def font(tmp_path):
    p = tmp_path / "card.ttf"
    p.write_bytes(build_font(string.ascii_letters + string.digits + string.punctuation, advance=500))
    return p


def _png(path, w, h):
    import struct
    import zlib

    raw = b"".join(b"\0" + b"\xff" * (4 * w) for _ in range(h))
    chunk = lambda t, d: struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d))
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
                     + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))  # fmt: skip
    return path


def _short(style=None, **shot):
    s = Shot(id="a", title="t", picture="p", scene="Main", **shot)
    return Short(slug="x", title="X", length_s=2, shots=[s], style=style or {})


def _tl():
    return Timeline(30, 60, [Span("a", 1, 60)], [], [Card("Hello there", "l1", 1, 20),
                                                     Card("A second caption card", "l1", 21, 40)])  # fmt: skip


@pytest.fixture
def short():
    return _short(badge="simplified", step=[1, 3], category="flow", title_card={"lines": ["How a lever"],
                  "highlight": 0}, bars=BARS, labels=["Stacked", {"text": "Pivot", "near": "Pivot"},
                  {"text": "2 m", "kind": "measure"}, {"text": "1 m", "kind": "measure", "near": "Arm"}])  # fmt: skip


@pytest.fixture
def tl():
    return _tl()


@pytest.fixture
def anchors():
    return {"a": {"Pivot": {"f0": 1, "points": [[540, 1000]] * 60, "category": "flow"},
                  "Arm": {"f0": 1, "points": [[300 + i, 900] for i in range(60)]}}}  # fmt: skip


def statics(ov, but=()):
    """The shot's static cards (not the captions, not the near cards that move)."""
    return [p for p in ov.cards if p.at is not None and p.spec.kind != "caption" and p.spec.kind not in but]


def caps(ov):
    return [p for p in ov.cards if p.channel == kitlayout.CH["caption"]]


def one(ov, channel):
    (p,) = [p for p in ov.cards if p.channel == channel]
    return p


def near(ov, i=1):
    return one(ov, kitlayout.CH["near"] + i)


def test_caption_bar_keeps_todays_bottom_and_centre(short, tl, anchors, font):
    ov = kitlayout.build(short, tl, K, font, anchors)
    for (f0, f1, _band, _box, bottom), p in zip(overlays.captions(short, tl), caps(ov), strict=True):
        assert (p.f0, p.f1) == (f0, f1) and p.at.x == safe_rect().x and p.at.w == safe_rect().w
        assert p.at.y + p.spec.box_h == pytest.approx(bottom + K.caption["pad_px"])
        assert p.spec.kind == "caption" and p.text in ("Hello there", "A second caption card")
    assert len(caps(ov)) == 2


def test_corner_stack_starts_where_todays_badge_does(short, tl, anchors, font):
    ov = kitlayout.build(short, tl, K, font, anchors)
    x, y = overlays.corner_stack(short.shots[0])[0][3:]
    badge = one(ov, kitlayout.CH["badge"])
    assert badge.at.x == pytest.approx(x) and badge.at.y == pytest.approx(y) and badge.text == "SIMPLIFIED"
    step = one(ov, kitlayout.CH["step"])
    stacked = one(ov, kitlayout.CH["stacked"] + 0)
    measure = one(ov, kitlayout.CH["stacked"] + 2)
    assert step.at.y == pytest.approx(badge.at.bottom + overlays.STACK_GAP_PX) and step.text == "Step 1 of 3"
    assert stacked.at.y == pytest.approx(step.at.bottom + overlays.STACK_GAP_PX) and stacked.spec.dot is None
    assert measure.at.y == pytest.approx(stacked.at.bottom + overlays.STACK_GAP_PX) and measure.spec.kind == "measure"
    assert all(p.at.x == pytest.approx(x) for p in (badge, step, stacked, measure))
    assert (badge.f0, badge.f1) == (1, 60)
    cat = one(ov, kitlayout.CH["category"])  # no logo: the category tag is right-aligned in the top row
    assert cat.at.y == pytest.approx(y) and cat.at.right == pytest.approx(safe_rect().right - overlays.SMALL_MARGIN * H - 1)


def test_near_labels_follow_like_todays_with_the_card_size(short, tl, anchors, font):
    ov = kitlayout.build(short, tl, K, font, anchors)
    p = near(ov)
    fixed = [c.at for c in statics(ov)]
    want = overlays.follow("Pivot", anchors["a"]["Pivot"], 1, 60, overlays.captions(short, tl), fixed, {}, None,
                           "Pivot", size=(p.spec.w, p.spec.h))  # fmt: skip
    assert set(p.track) == set(want)
    for f, r in p.track.items():
        x, bottom, _ = want[f]
        assert r.x + r.w / 2 == pytest.approx(x) and r.bottom == pytest.approx(bottom)
        assert (r.w, r.h) == (p.spec.w, p.spec.h)
    assert p.near == "Pivot" and p.text == "Pivot" and p.at is None and p.spec.kind == "label"
    m = near(ov, 3)  # the near measure: no dot, its own follow
    assert m.spec.kind == "measure" and m.spec.dot is None and m.near == "Arm"
    assert all(overlap_area(m.track[f], p.track[f]) == 0 for f in range(1, 61))


def test_a_near_label_without_anchors_is_todays_error(short, tl, font):
    with pytest.raises(ValueError, match="no anchors for label 'Pivot' near 'Pivot' in shot a: run BLENDER anchors"):
        kitlayout.build(short, tl, K, font, {})


def test_pointers_run_from_each_near_card_to_its_anchor(short, tl, anchors, font):
    ov = kitlayout.build(short, tl, K, font, anchors)
    ptr = next(p for p in ov.pointers if p.channel == kitlayout.CH["pointer"] + 1)
    assert (ptr.f0, ptr.f1) == (1, 60) and ptr.spec.kind == "pointer" and ptr.dot.kind == "dot"
    card = near(ov)
    for f, ends in ptr.ends.items():
        assert ends is not None and ends[1] == (540.0, 1000.0)
        assert ends[0] == kitlayout.pointer_end(card.track[f], (540, 1000))
        assert ptr.spec.w >= math.dist(*ends) - 1
    assert len(ov.pointers) == 2


def test_a_top_left_logo_pushes_the_stack_down_and_a_top_right_one_the_category(tmp_path, font):
    logo = _png(tmp_path / "logo.png", 200, 100)
    short = _short(badge="simplified", category="spark")
    edge = overlays.SMALL_MARGIN * H + 1
    left = kitlayout.build(short, _tl(), kit.kit_from({**KIT, "logo": {"file": str(logo)}}), font)
    lg, badge, cat = (one(left, kitlayout.CH[k]) for k in ("logo", "badge", "category"))
    assert (lg.at.x, lg.at.y) == (pytest.approx(safe_rect().x + edge), pytest.approx(safe_rect().y + edge))
    assert (lg.f0, lg.f1) == (1, 60) and lg.spec.kind == "logo"
    assert badge.at.y == pytest.approx(lg.at.bottom + overlays.STACK_GAP_PX) and cat.at.y == lg.at.y
    right = kitlayout.build(short, _tl(), kit.kit_from({**KIT, "logo": {"file": str(logo), "place": "top-right"}}),
                            font)  # fmt: skip
    lg, badge, cat = (one(right, kitlayout.CH[k]) for k in ("logo", "badge", "category"))
    assert lg.at.right == pytest.approx(safe_rect().right - edge) and lg.at.y == pytest.approx(safe_rect().y + edge)
    assert badge.at.y == lg.at.y
    assert cat.at.y == pytest.approx(lg.at.bottom + overlays.STACK_GAP_PX)
    assert cat.at.right == pytest.approx(safe_rect().right - edge)


def test_a_missing_logo_draws_no_badge_and_says_so(tmp_path, font):
    logo = _png(tmp_path / "logo.png", 200, 100)
    k = kit.kit_from({**KIT, "logo": {"file": str(logo)}})
    logo.unlink()
    ov = kitlayout.build(_short(badge="x"), _tl(), k, font)
    assert not [p for p in ov.cards if p.spec.kind == "logo"]
    want = f"your logo file {logo.resolve()} is missing, so no logo badge is drawn: set brand.logo.file again"
    assert ov.notes == [want]
    assert one(ov, kitlayout.CH["badge"]).at.y == pytest.approx(safe_rect().y + overlays.SMALL_MARGIN * H + 1)


def test_title_card_and_bars_sit_under_the_top_cards(short, tl, anchors, font):
    ov = kitlayout.build(short, tl, K, font, anchors)
    title, bars = one(ov, kitlayout.CH["title"]), one(ov, kitlayout.CH["bars"])
    lowest = max(p.at.bottom for p in statics(ov, but=("title", "bars")))
    s = safe_rect()
    assert title.at.y == pytest.approx(lowest + 24) and title.at.x == pytest.approx(s.x + (s.w - title.spec.w) / 2)
    assert bars.at.y == pytest.approx(title.at.bottom + 24) and (bars.at.x, bars.at.w) == (s.x, s.w)
    assert (title.f0, title.f1, bars.f0, bars.f1) == (1, 60, 1, 60)
    only_bars = kitlayout.build(_short(bars=BARS), tl, K, font)  # no top cards, no title: the bars take its place
    assert one(only_bars, kitlayout.CH["bars"]).at.y == pytest.approx(s.y + overlays.SMALL_MARGIN * H + 1)
    assert all(inside_safe(p.at) for p in ov.cards if p.at is not None)


def test_part_label_dot_takes_the_objects_category(short, tl, anchors, font):
    ov = kitlayout.build(short, tl, K, font, anchors)
    assert near(ov).spec.dot == "#3B8ED9"
    plain = {"a": {**anchors["a"], "Pivot": {k: v for k, v in anchors["a"]["Pivot"].items() if k != "category"}}}
    assert near(kitlayout.build(short, tl, K, font, plain)).spec.dot == K.palette["accent"]


def test_pointer_runs_from_the_card_edge_to_the_anchor():
    box = Rect(100, 100, 200, 50)
    assert kitlayout.pointer_end(box, (200, 300)) == (200, 150)  # below: the bottom middle
    assert kitlayout.pointer_end(box, (150, 120)) is None  # inside
    assert kitlayout.pointer_end(box, (200, 20)) == (200, 100)  # above: the top
    assert kitlayout.pointer_end(box, (500, 120)) == (300, 125)  # beside: the middle of the side
    assert kitlayout.pointer_end(box, (20, 120)) == (100, 125)
    assert kitlayout.pointer_end(box, (20, 400)) == (100, 150)  # below-left: the left end of the bottom


@pytest.mark.parametrize("start,end", [((100, 200), (400, 600)), ((500, 900), (200, 300)), ((300, 300), (300, 900))])
def test_pointer_transform_maps_both_ends(start, end):
    length0, t, fw, fh = 500.0, 6.0, 1080, 1920
    tr = kitlayout.pointer_transform(start, end, length0, t, (fw, fh))
    pivot = (0.0, t / 2)  # origin (0, 0.5) of a length0 x t image

    def to_frame(p):  # the sequencer: pivot + offset + (frame - in) / 2 + R(scale * (p - pivot)), from the bottom-left
        sx, sy = tr["scale_x"] * (p[0] - pivot[0]), p[1] - pivot[1]
        c, s = math.cos(tr["rotation"]), math.sin(tr["rotation"])
        x = pivot[0] + tr["offset_x"] + (fw - length0) / 2 + c * sx - s * sy
        y = pivot[1] + tr["offset_y"] + (fh - t) / 2 + s * sx + c * sy
        return x, fh - y  # back to px from the top-left

    assert to_frame((0, t / 2)) == pytest.approx(start)
    assert to_frame((length0, t / 2)) == pytest.approx(end)


def test_progress_covers_the_story_only(short, tl, anchors, font):
    p = kitlayout.build(short, tl, K, font, anchors).progress
    s = safe_rect()
    assert (p.f0, p.f1, p.channel) == (1, tl.n_frames, kitlayout.CH["progress"])
    assert (p.at.x, p.at.w) == (s.x, s.w) and p.at.y == H - SAFE["bottom"] - CAPTION_LIFT_PX + 36
    assert p.track.kind == "progress_track" and p.fill.kind == "progress_fill"
    assert kitlayout.build(short, tl, kit.kit_from({**KIT, "progress": False}), font, anchors).progress is None


def test_caption_draw_false_draws_no_captions(tl, font):
    off = _short(style={"caption": {"draw": False}})
    assert overlays.captions(off, tl) == []
    assert caps(kitlayout.build(off, tl, K, font)) == []
    assert overlays.captions(_short(), tl) != []  # the default draws them
    assert caps(kitlayout.build(_short(), tl, kit.kit_from({**KIT, "caption": {"draw": False}}), font)) == []


def test_shot_boxes_with_a_kit_include_the_title_card(short, tl, anchors, font):
    boxes = overlays.shot_boxes(short, tl, short.shots[0], tl.shots[0], anchors["a"], frames=[5, 50], kit=K, font=font)
    ov = kitlayout.build(short, tl, K, font, anchors)
    kinds = sorted(k for k, *_ in boxes[5])
    assert kinds == sorted(["caption", "badge", "step", "category", "label", "measure", "title", "bars", "near",
                            "near"])  # fmt: skip
    assert "caption" not in [k for k, *_ in boxes[50]]
    title = next(r for k, _, _, r in boxes[5] if k == "title")
    assert title == one(ov, kitlayout.CH["title"]).at
    pivot = next(r for k, _, n, r in boxes[5] if k == "near" and n == "Pivot")
    assert pivot == near(ov).track[5]
    no_anchors = overlays.shot_boxes(short, tl, short.shots[0], tl.shots[0], None, frames=[5], kit=K, font=font)
    assert "near" not in [k for k, *_ in no_anchors[5]]  # left out, as without a kit
    plain = overlays.shot_boxes(short, tl, short.shots[0], tl.shots[0], anchors["a"], frames=[5])
    assert "title" not in [k for k, *_ in plain[5]]  # no kit: today's boxes


def test_top_band_covers_every_static_top_card(short, tl, anchors, font):
    band = kitlayout.top_band(short, short.shots[0], K, font)
    ov = kitlayout.build(short, tl, K, font, anchors)
    s = safe_rect()
    assert (band.x, band.y, band.w) == (s.x, s.y, s.w)
    assert band.bottom == pytest.approx(max(p.at.bottom for p in statics(ov)))
    assert kitlayout.top_band(short, _short().shots[0], K, font) is None


def test_sample_has_one_of_each_card(font):
    ov = kitlayout.sample(K, font)
    kinds = sorted(p.spec.kind for p in ov.cards)
    assert kinds == sorted(["caption", "category", "badge", "step", "title", "label", "measure", "bars"])
    assert all(p.f0 <= 1 <= p.f1 for p in ov.cards)
    assert next(p for p in ov.cards if p.spec.kind == "category").text == "Spark"
    assert next(p for p in ov.cards if p.spec.kind == "label").near == "Pivot"
    (ptr,) = ov.pointers
    assert ptr.ends[1][1] == (700.0, 400.0)
    p = ov.progress
    assert round(p.fill.w * (1 - p.f0 + 1) / (p.f1 - p.f0 + 1)) == round(p.fill.w * 0.4)
    no_cats = kitlayout.sample(kit.kit_from({**KIT, "categories": {}}), font)
    assert next(p for p in no_cats.cards if p.spec.kind == "category").text == "Force"


def test_sample_with_a_logo_shows_it(tmp_path, font):
    logo = _png(tmp_path / "logo.png", 200, 100)
    ov = kitlayout.sample(kit.kit_from({**KIT, "logo": {"file": str(logo), "name": "Acme"}}), font)
    assert [p.spec.kind for p in ov.cards].count("logo") == 1


def _sample_rects(ov):
    rects = [(p.spec.kind, p.at if p.track is None else p.track[1]) for p in ov.cards]
    if ov.progress is not None:
        rects.append(("progress", ov.progress.at))
    return rects


@pytest.mark.parametrize("with_logo", [False, True])
def test_no_two_sample_cards_overlap(tmp_path, font, with_logo):
    extra = {"logo": {"file": str(_png(tmp_path / "logo.png", 200, 100)), "name": "Acme"}} if with_logo else {}
    ov = kitlayout.sample(kit.kit_from({**KIT, **extra}), font)
    rects = _sample_rects(ov)
    assert ("logo" in [k for k, _ in rects]) == with_logo
    s = safe_rect()
    for kind, r in rects:
        assert s.x - 1 <= r.x and r.right <= s.right + 1 and s.y - 1 <= r.y and r.bottom <= s.bottom + 1, (kind, r)
    for i, (ka, a) in enumerate(rects):
        for kb, b in rects[i + 1 :]:
            assert overlap_area(a, b) == 0, (ka, a, kb, b)
    bars = next(r for k, r in rects if k == "bars")
    caption = next(r for k, r in rects if k == "caption")
    assert caption.y - bars.bottom >= STACK_GAP_PX
    for ptr in ov.pointers:  # the pointer line ends inside the safe area and passes over no other card
        start, end = ptr.ends[1]
        assert s.x <= end[0] <= s.right and s.y <= end[1] <= s.bottom, end
        for i in range(101):
            x, y = (start[0] + (end[0] - start[0]) * i / 100, start[1] + (end[1] - start[1]) * i / 100)
            hit = [k for k, r in rects if k != "label" and r.x < x < r.right and r.y < y < r.bottom]
            assert not hit, (hit, x, y)


def test_a_card_in_a_cover_font_is_noted_once(monkeypatch, short, tl, anchors, font, tmp_path):
    alt = tmp_path / "alt.ttf"
    alt.write_bytes(font.read_bytes())
    real = cards.font_for

    def font_for(text, f):
        if "Pivot" in text:
            return alt, f"{text!r}: no Pivot glyphs"
        return real(text, f)

    monkeypatch.setattr(cards, "font_for", font_for)
    ov = kitlayout.build(short, tl, K, font, anchors)
    assert near(ov).spec.font == str(alt)
    assert ov.notes.count("'Pivot': no Pivot glyphs") == 1


def test_a_long_badge_pushes_a_long_category_below_it(font):
    short = _short(badge="Simplified: sizes and speeds are not to scale here", category="a very long category name")
    ov = kitlayout.build(short, _tl(), K, font)
    badge, cat = one(ov, kitlayout.CH["badge"]), one(ov, kitlayout.CH["category"])
    assert overlap_area(badge.at, cat.at) == 0 and cat.at.y == pytest.approx(badge.at.bottom + overlays.STACK_GAP_PX)
    assert inside_safe(cat.at) and cat.at.right == pytest.approx(safe_rect().right - overlays.SMALL_MARGIN * H - 1)
    title = kitlayout.build(_short(badge="b", category="spark", title_card="T"), _tl(), K, font)
    assert one(title, kitlayout.CH["category"]).at.y == one(title, kitlayout.CH["badge"]).at.y  # short ones share


def test_labels_per_shot_are_capped_so_channels_never_meet(font):
    six = [f"L{i}" for i in range(kitlayout.MAX_LABELS)]
    ov = kitlayout.build(_short(labels=six), _tl(), K, font)
    chans = [p.channel for p in ov.cards if p.spec.kind == "label"]
    assert max(chans) < 30 and kitlayout.CH["near"] + kitlayout.MAX_LABELS <= kitlayout.CH["dot"]
    assert kitlayout.CH["pointer"] + kitlayout.MAX_LABELS <= kitlayout.CH["near"]
    with pytest.raises(ValueError, match="shot a has 7 labels: a brand kit draws at most 6 per shot"):
        kitlayout.build(_short(labels=[*six, "one more"]), _tl(), K, font)
    with pytest.raises(ValueError, match="at most 6"):
        kitlayout.top_band(None, _short(labels=[*six, "x"]).shots[0], K, font)


def test_pointer_lines_sit_under_every_static_card_and_dots_and_near_cards_over_them():
    """A pointer line crossing a static card (title, bars, caption, logo, category, badge, step, stacked) passes
    behind it; the near cards and their dots stay on top of everything."""
    top_pointer = kitlayout.CH["pointer"] + kitlayout.MAX_LABELS - 1
    statics = [kitlayout.CH[k] for k in ("title", "bars", "caption", "logo", "category", "badge", "step", "stacked")]
    assert top_pointer < min(statics) and top_pointer < kitlayout.CH["progress"]
    assert min(kitlayout.CH["near"], kitlayout.CH["dot"]) > max(statics) + kitlayout.MAX_LABELS
    assert kitlayout.CH["pointer"] >= 3  # over the shot frames (1) and the sound (2)
    assert issubclass(kitlayout.TooManyLabels, ValueError)


BARS3 = {"items": [{"label": "Short arm", "value": 1}, {"label": "Long arm", "value": 3}, {"label": "Mid", "value": 2}],
         "verdict": "3x the lift"}  # fmt: skip


def _tall(n_labels=2, lines=("How a", "lever"), bars=BARS):
    return _short(badge="simplified", step=[1, 3], category="flow", title_card={"lines": list(lines), "highlight": 1},
                  bars=bars, labels=[f"Part {i}" for i in range(n_labels)]).shots[0]  # fmt: skip


def test_stacked_labels_move_to_the_right_column_when_the_left_stack_would_reach_the_captions(font):
    shot = _tall()  # badge, step, 2 labels, a 2-line title and 2 bars: too tall with the labels on the left
    assert kitlayout._bottom(kitlayout._lay(shot, K, font, None, False)) > kitlayout.bottom_limit(K)
    static = kitlayout._static(shot, K, font, None, [])
    rects = {k + str(i): r for i, (k, _, _, r, _) in enumerate(static)}
    labels = [r for k, _, _, r, _ in static if k == "label"]
    cat = next(r for k, _, _, r, _ in static if k == "category")
    assert len(labels) == 2 and all(r.right == pytest.approx(cat.right) and r.y > cat.bottom for r in labels)
    assert max(r.bottom for r in rects.values()) <= kitlayout.bottom_limit(K)
    assert all(overlap_area(a, b) == 0 for i, a in enumerate(rects.values()) for b in list(rects.values())[i + 1:])


def test_a_shot_whose_top_cards_still_reach_the_captions_is_an_error_naming_what_to_drop(font):
    shot = _tall(n_labels=4, bars=BARS3)  # badge, step, 4 labels, a 2-line title, 3 bars and a verdict
    with pytest.raises(kitlayout.CardsTooTall, match=r"shot a's top cards reach \d+ px into the captions") as exc:
        kitlayout._static(shot, K, font, None, [])
    assert "drop" in str(exc.value) and "the value bars" in str(exc.value)
    assert issubclass(kitlayout.CardsTooTall, kitlayout.LayoutError)
    with pytest.raises(kitlayout.CardsTooTall):
        kitlayout.build(_short(**{k: getattr(shot, k) for k in ("badge", "step", "category", "title_card", "bars",
                                                                 "labels")}), _tl(), K, font)  # fmt: skip
    band = kitlayout.top_band(None, shot, K, font)  # framing never fails: it frames under what is there
    assert band is not None and band.bottom > kitlayout.bottom_limit(K)


LONG_CAPTION = "This sentence is long enough that the caption wraps onto a second line of text"


def _captioned(shot, text):
    """A short of `shot` whose one caption says `text` (its narration line, so validate sees it as the edit does)."""
    from bsa.short import Line

    short = Short(slug="x", title="X", length_s=2, shots=[shot], lines=[Line("l1", text, shot.id)])
    return short, Timeline(30, 60, [Span(shot.id, 1, 60)], [], [Card(text, "l1", 1, 60)])


def test_one_line_captions_give_the_top_cards_more_room(font, monkeypatch):
    shot = _tall(n_labels=3, bars=BARS3)  # squeezed it ends ~1189: over the two-line band (1141), under the one-line
    two, tl2 = _captioned(shot, LONG_CAPTION)
    one_line, tl1 = _captioned(shot, "Short caption")
    assert kitlayout.caption_lines(two, tl2, K, font) == 2 and kitlayout.caption_lines(one_line, tl1, K, font) == 1
    with pytest.raises(kitlayout.CardsTooTall):
        kitlayout.build(two, tl2, K, font)
    ov = kitlayout.build(one_line, tl1, K, font)
    assert max(p.at.bottom for p in statics(ov)) <= kitlayout.bottom_limit(K, 1)
    assert kitlayout.bottom_limit(K, 1) > kitlayout.bottom_limit(K, 2)
    cap = next(p for p in ov.cards if p.spec.kind == "caption")
    assert all(overlap_area(p.at, cap.at) == 0 for p in statics(ov))
    monkeypatch.setattr(cards, "card_font", lambda *a, **kw: font)  # validate measures in the test font too
    # validate and the edit agree: validate (no timeline) refuses the two-line short and passes the one-line one
    assert len(kit.errors({"brand": KIT}, two)) == 1 and kit.errors({"brand": KIT}, one_line) == []
    assert kitlayout.top_band(one_line, shot, K, font).bottom <= kitlayout.bottom_limit(K, 1)


def test_caption_lines_is_two_when_unknown_and_a_short_without_captions_measures_none(font, monkeypatch):
    shot = _tall(n_labels=3)
    assert kitlayout.caption_lines(None, None, K, font) == 2
    off, tl = _captioned(shot, "Short caption")
    off.style = {"caption": {"draw": False}}
    assert kitlayout.caption_lines(off, tl, K, font) == 0
    assert kitlayout.bottom_limit(K, 0) > kitlayout.bottom_limit(K, 1)  # no caption band at all
    ov = kitlayout.build(off, tl, K, font)  # the cards that were too tall for a caption fit with none
    assert max(p.at.bottom for p in statics(ov)) <= kitlayout.bottom_limit(K, 0)
    monkeypatch.setattr(cards, "card_font", lambda *a, **kw: font)
    assert kit.errors({"brand": KIT}, off) == []
    bare = kit.kit_from({**KIT, "caption": {"draw": False}})
    assert kitlayout.caption_lines(_captioned(shot, "x")[0], None, bare, font) == 0


def test_title_shrinks_then_bars_tighten_before_refusing(font):
    shot = _tall(n_labels=2, bars=BARS3)  # reaches ~1188: over by ~47 px with the full-size title
    plain = kitlayout._lay(shot, K, font, None, True)
    assert kitlayout._bottom(plain) > kitlayout.bottom_limit(K)
    static = kitlayout._static(shot, K, font, None, [])  # no CardsTooTall
    assert max(r.bottom for *_, r, _ in static) <= kitlayout.bottom_limit(K)
    title = next(spec for k, _, spec, _, _ in static if k == "title")
    small = cards.title(K, ["How a", "lever"], 1, font, safe_rect().w, size=cards.TITLE_MIN)
    assert title.h == small.h < cards.title(K, ["How a", "lever"], 1, font, safe_rect().w).h
    rs = [r for *_, r, _ in static]
    assert all(overlap_area(a, b) == 0 for i, a in enumerate(rs) for b in rs[i + 1:])


def test_tight_bars_are_shorter_and_only_used_when_the_title_shrink_is_not_enough(font):
    full, tight = cards.bars(K, BARS3, font, 900), cards.bars(K, BARS3, font, 900, tight=True)
    assert tight.h < full.h and all(b[3] == cards.BAR_H * 0.75 for b in tight.bars)
    assert all(b[3] == cards.BAR_H for b in full.bars)
    # a shot the title shrink alone doesn't save is drawn with the tight bars (and the small title)
    shot = _tall(n_labels=3, bars=BARS3)
    k = kit.kit_from({**KIT, "caption": {**KIT["caption"], "size": 50}})  # a smaller caption: a bit more room
    small = kitlayout._lay(shot, k, font, None, True, True, False)
    both = kitlayout._lay(shot, k, font, None, True, True, True)
    assert kitlayout._bottom(both) <= kitlayout.bottom_limit(k) < kitlayout._bottom(small)
    static = kitlayout._static(shot, k, font, None, [])
    bars = next(spec for kd, _, spec, _, _ in static if kd == "bars")
    assert bars.h == cards.bars(k, BARS3, font, safe_rect().w, tight=True).h
    assert max(r.bottom for *_, r, _ in static) <= kitlayout.bottom_limit(k)


def test_still_refuses_when_nothing_fits(font):
    shot = _tall(n_labels=4, bars=BARS3)
    with pytest.raises(kitlayout.CardsTooTall, match=r"shot a's top cards reach \d+ px into the captions") as exc:
        kitlayout._static(shot, K, font, None, [])
    msg = str(exc.value)
    assert "drop" in msg and "the value bars" in msg and "the title card" in msg
    assert "or move it" not in msg and "drop one of the value bars, the title card, or" not in msg
    assert msg.endswith("or move some of them to another shot")


def test_static_cards_always_stay_above_the_caption_band_and_the_progress_bar(font, tmp_path):
    """Every mix of top cards either fits above the caption band (a two-line caption in the kit's size) and the
    progress bar, without overlapping, or is a CardsTooTall error: never cards over the captions."""
    from bsa.layout import caption_band

    top = min(caption_band(2, K.caption["size"]).y, H - SAFE["bottom"] - CAPTION_LIFT_PX + kitlayout.PROGRESS_DROP_PX)
    fitted = refused = 0
    png = _png(tmp_path / "logo.png", 8, 8)
    for n in range(5):
        for lines in ((), ("Lever",), ("How a", "lever")):
            for bars in (None, BARS, BARS3):
                for logo in (None, "top-left", "top-right"):
                    k = K if logo is None else kit.kit_from({**KIT, "logo": {"file": str(png), "place": logo}})
                    spec = cards.pill(k, "badge", "LOGO", font) if logo else None
                    shot = _short(badge="simplified", step=[1, 3], category="flow", bars=bars,
                                  title_card={"lines": list(lines)} if lines else None,
                                  labels=[f"Part {i}" for i in range(n)]).shots[0]  # fmt: skip
                    try:
                        static = kitlayout._static(shot, k, font, spec, [])
                    except kitlayout.CardsTooTall:
                        refused += 1
                        continue
                    fitted += 1
                    rs = [r for *_, r, _ in static]
                    assert max(r.bottom for r in rs) <= top - kitlayout.TITLE_GAP_PX, (n, lines, bars, logo)
                    assert all(overlap_area(a, b) == 0 for i, a in enumerate(rs) for b in rs[i + 1:])
    assert fitted and refused


def test_kit_errors_name_a_shot_too_tall_for_the_kit():
    shot = _tall(n_labels=4, bars=BARS3)
    errs = kit.errors({"brand": KIT}, Short(slug="x", title="X", length_s=2, shots=[shot]))
    assert len(errs) == 1 and errs[0].startswith("shot a's top cards reach") and "drop" in errs[0], errs
    assert kit.errors({}, Short(slug="x", title="X", length_s=2, shots=[shot])) == []  # no kit: today's labels
