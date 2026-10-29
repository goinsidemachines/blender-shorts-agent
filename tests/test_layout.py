import pytest

from bsa.layout import H, Rect, W, caption_rect, inside_safe, safe_rect


def test_safe_rect():
    r = safe_rect()
    assert (r.x, r.y, r.w, r.h) == (60, 220, 880, 1280)
    assert inside_safe(r)
    assert not inside_safe(Rect(0, 0, W, H))


@pytest.mark.parametrize("n", [1, 2, 3])
def test_caption_rect_inside_safe(n):
    r = caption_rect(n, 64)
    assert inside_safe(r)
    assert r.bottom == safe_rect().bottom - 140
    assert r.h == n * 64 * 1.25
    assert r.x + r.w / 2 == W / 2 - (140 - 60) / 2


def test_caption_band_and_the_area_to_frame_heroes_in():
    from bsa.layout import caption_band, frame_area

    band = caption_band()
    assert (round(band.y), round(band.bottom)) == (1165, 1395)  # rows the build chapter quotes
    assert (band.x, band.w) == (safe_rect().x, safe_rect().w)
    area = frame_area(margin=20)
    s = safe_rect()
    assert (area.x, area.y, area.right) == (s.x + 20, s.y + 20, s.right - 20)
    assert area.bottom == pytest.approx(band.y - 20)
    assert frame_area(avoid=None, margin=0) == s
    top_band = Rect(s.x, s.y, s.w, 100)  # a band in the upper half pushes the area down instead
    assert frame_area(avoid=top_band, margin=0).y == s.y + 100


def test_place_label_sits_just_above_its_anchor():
    from bsa.layout import place_label

    x, bottom = place_label((540, 900), (200, 50), margin=19.2, gap=16)
    assert (x, bottom) == (540, 900 - 16 - 19.2)


def test_place_label_is_clamped_inside_the_safe_area():
    from bsa.layout import place_label

    s = safe_rect()
    w, h, m = 300, 50, 19.2
    for anchor in ((0, 0), (W, H), (W, 100), (-500, 3000)):
        x, bottom = place_label(anchor, (w, h), margin=m, gap=16)
        box = Rect(x - w / 2 - m, bottom - h - m, w + 2 * m, h + 2 * m)
        assert inside_safe(box), (anchor, box)
    assert place_label((0, 900), (w, h), margin=m, gap=16)[0] == pytest.approx(s.x + w / 2 + m, abs=1.01)


def test_place_label_never_sits_on_a_caption():
    from bsa.layout import place_label

    cap = caption_rect(2, 64)
    w, h, m = 200, 50, 19.2
    _, bottom = place_label((540, cap.y + 40), (w, h), margin=m, gap=16, avoid=[cap])
    assert bottom + m <= cap.y  # the label box ends above the caption box
    _, bottom = place_label((540, 600), (w, h), margin=m, gap=16, avoid=[cap])
    assert bottom == 600 - 16 - m  # far from the caption: unchanged


def test_end_card_band_moves_off_heroes():
    from bsa.layout import end_card_band

    s = safe_rect()
    block = Rect(340, 420, 400, 220)  # in the upper third
    cap_top = caption_rect(2, 64).y - 35
    assert end_card_band(block, [], cap_top) == ("upper third", 0.0, 0.0)
    name, dy, area = end_card_band(block, [Rect(500, 400, 100, 150)], cap_top)  # a hero in the upper third
    assert name == "caption zone" and area == 0  # free during the end card: it starts after the last caption
    heroes = [Rect(500, 400, 100, 150), Rect(500, s.y + s.h / 2 - 50, 100, 100)]  # upper third and middle
    name, dy, area = end_card_band(block, heroes, cap_top)
    assert name == "caption zone" and area == 0 and block.bottom + dy == s.bottom - 1
    in_caption_zone = Rect(500, s.bottom - 100, 100, 90)
    name, dy, area = end_card_band(block, [Rect(500, 400, 100, 150), in_caption_zone], cap_top)
    assert name == "middle" and area == 0 and abs(block.y + dy + block.h / 2 - (s.y + s.h / 2)) < 1e-6  # the order
    heroes = [Rect(500, 400, 100, 150), in_caption_zone, Rect(500, s.y + s.h / 2 - 50, 100, 100)]
    name, dy, area = end_card_band(block, heroes, cap_top)
    assert name == "lower" and area == 0 and block.bottom + dy == cap_top - 16
    everywhere = [Rect(s.x, s.y, s.w, s.h)]  # covers every band: the least overlap wins, and it is reported
    name, dy, area = end_card_band(block, everywhere + [Rect(500, 400, 100, 150)], cap_top)
    assert name != "upper third" and area > 0


def test_end_card_clears_the_real_pendulum_heroes():
    """The owner's pendulum short, s04: two bobs whose boxes reach y 1107 and a short pendulum in the upper third.
    During the hold no caption shows, so the caption zone runs down to the safe bottom and the card fits under them."""
    from bsa.layout import end_card_band

    s = safe_rect()
    heroes = [Rect(354.23, 992.03, 687.03 - 354.23, 1107.0 - 992.03), Rect(124.88, 997.67, 409.16 - 124.88, 1107.03 - 997.67),
              Rect(784.29, 574.41, 923.31 - 784.29, 655.32 - 574.41)]  # fmt: skip
    block = Rect(260, 420, 560, 230)  # the line and @handle, built in the upper third
    middle = Rect(500, s.y + s.h / 2 - 160, 100, 320)  # and the bobs' swing through the middle
    name, dy, area = end_card_band(block, [*heroes, middle], caption_rect(2, 64).y - 35)
    moved = Rect(block.x, block.y + dy, block.w, block.h)
    assert name == "caption zone" and area == 0
    assert inside_safe(moved) and moved.y > 1107.03 and moved.bottom == s.bottom - 1


def _label_box(x, bottom, w, h, m):
    return Rect(x - w / 2 - m, bottom - h - m, w + 2 * m, h + 2 * m)


def test_place_label_never_sits_on_the_badge():
    """A near object high in the frame (a spire, an arrow) must not put its label on the top-left badge."""
    from bsa.layout import overlap_area, place_label

    s = safe_rect()
    badge = Rect(s.x, s.y, 340, 76)  # "MOTION EXAGGERATED" at the safe area's top-left
    w, h, m = 200, 50, 10.0
    for anchor in ((s.x, s.y), (s.x + 150, s.y + 60), (s.x + 300, s.y + 120), (200, 330)):
        x, bottom = place_label(anchor, (w, h), margin=m, gap=16, avoid=[badge])
        box = _label_box(x, bottom, w, h, m)
        assert overlap_area(box, badge) == 0, (anchor, box)
        assert inside_safe(box), (anchor, box)
    far = (s.right - 200, s.y + 200)  # high but on the right: the badge is not in the way, so it is unchanged
    assert place_label(far, (w, h), margin=m, gap=16, avoid=[badge]) == place_label(far, (w, h), margin=m, gap=16)


def test_place_label_avoids_labels_already_placed():
    """Two near labels on neighbouring objects ("No damper", "With damper") must not touch."""
    from bsa.layout import overlap_area, place_label

    w, h, m = 220, 50, 10.0
    first = _label_box(*place_label((540, 900), (w, h), margin=m, gap=16), w, h, m)
    for anchor in ((540, 900), (600, 910), (400, 880)):
        x, bottom = place_label(anchor, (w, h), margin=m, gap=16, avoid=[first])
        assert overlap_area(_label_box(x, bottom, w, h, m), first) == 0, anchor


def test_place_label_avoids_badge_labels_and_caption_together():
    from bsa.layout import overlap_area, place_label

    s = safe_rect()
    badge = Rect(s.x, s.y, 340, 76)
    cap = caption_rect(2, 64)
    w, h, m = 200, 50, 10.0
    other = _label_box(s.x + 150, s.y + 160, w, h, m)  # a label just under the badge
    avoid = [badge, other, cap]
    for anchor in ((s.x + 120, s.y + 100), (s.x + 150, s.y + 240), (540, cap.y + 20)):
        x, bottom = place_label(anchor, (w, h), margin=m, gap=16, avoid=avoid)
        box = _label_box(x, bottom, w, h, m)
        assert all(overlap_area(box, r) == 0 for r in avoid), (anchor, box)
        assert inside_safe(box)


def test_place_label_keeps_off_hero_boxes_when_it_can():
    """A near label on one part must not sit on the other heroes (the ball row under a cradle's string label)."""
    from bsa.layout import overlap_area, place_label

    w, h, m = 220, 50, 10.0
    row = Rect(200, 700, 680, 140)  # the ball row, just above the anchor
    x, bottom = place_label((540, 860), (w, h), margin=m, gap=16, prefer=[row])
    assert overlap_area(_label_box(x, bottom, w, h, m), row) == 0
    assert place_label((540, 500), (w, h), margin=m, gap=16, prefer=[row]) == place_label((540, 500), (w, h), m, 16)
    s = safe_rect()
    everywhere = Rect(s.x, s.y, s.w, s.h)  # nowhere free of heroes: the hard rects still win, then least hero overlap
    cap = caption_rect(2, 64)
    x, bottom = place_label((540, cap.y + 40), (w, h), margin=m, gap=16, avoid=[cap], prefer=[everywhere])
    assert overlap_area(_label_box(x, bottom, w, h, m), cap) == 0


def test_end_card_band_prefers_bands_free_of_every_mesh():
    """The end card keeps off heroes first, then off the other meshes on screen (a cradle's top rails)."""
    from bsa.layout import end_card_band

    s = safe_rect()
    block = Rect(340, 420, 400, 220)
    cap_top = caption_rect(2, 64).y - 35
    rails = Rect(150, 450, 780, 60)  # scenery across the upper third
    assert end_card_band(block, [], cap_top, others=[rails])[0] == "caption zone"
    low = Rect(150, s.bottom - 120, 780, 100)
    name, _, area = end_card_band(block, [low], cap_top, others=[rails])
    assert name == "middle" and area == 0  # the hero rules out the caption zone, the rails the upper third
    everywhere = [Rect(s.x, s.y, s.w, s.h)]  # scenery everywhere: a band free of heroes, least scenery
    name, _, area = end_card_band(block, [], cap_top, others=everywhere + [rails])
    assert area == 0 and name != "upper third"
