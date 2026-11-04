"""What a viewer sees: text over the subject, framing, the ground (bsa.viewer), and the edit's text boxes
(bsa.overlays) they are measured with."""

from bsa import overlays, viewer
from bsa.layout import H, Rect, W, caption_band, overlap_area, safe_rect
from bsa.short import Short, Shot, validate
from bsa.timeline import Card, Span, Timeline, wide_frames


def _short(**shot):
    s = Shot(id="s1", title="t", picture="p", scene="Main", **shot)
    return Short(slug="x", title="X", length_s=2, shots=[s])


def _tl(cards=()):
    return Timeline(30, 60, [Span("s1", 1, 60)], [], list(cards))


# --------------------------------------------------------------------------- the edit's boxes


def test_caption_box_is_in_the_caption_band_and_as_wide_as_its_text():
    short = _short()
    (f0, f1, band, box, _), = overlays.captions(short, _tl([Card("Short words", "l1", 5, 20)]))
    assert (f0, f1) == (5, 20)
    assert band.w > box.w > 0  # the band is the full safe width; the drawn box only its text
    assert abs((box.x + box.w / 2) - (safe_rect().x + safe_rect().w / 2)) < 1
    cb = caption_band()
    assert cb.y - 1 <= box.y and box.bottom <= cb.bottom + 1


def test_corner_stack_puts_the_badge_first_and_skips_near_labels():
    shot = _short(badge="simplified", labels=["Stacked", {"text": "Bob", "near": "Bob"}]).shots[0]
    stack = overlays.corner_stack(shot)
    assert [(k, t) for k, t, *_ in stack] == [("badge", "SIMPLIFIED"), ("label", "Stacked")]
    badge = overlays.corner_rect(*[stack[0][i] for i in (0, 1, 3, 4)])
    label = overlays.corner_rect(*[stack[1][i] for i in (0, 1, 3, 4)])
    assert label.y > badge.bottom  # one under the other
    assert badge.x >= safe_rect().x and badge.y >= safe_rect().y


def test_shot_boxes_lists_every_text_on_screen_per_frame():
    short = _short(badge="simplified", labels=[{"text": "Bob", "near": "Bob"}])
    tl = _tl([Card("Hello there", "l1", 1, 10)])
    anchors = {"Bob": {"f0": 1, "points": [[540, 900]] * 60}}
    boxes = overlays.shot_boxes(short, tl, short.shots[0], tl.shots[0], anchors, frames=[5, 30])
    assert sorted(k for k, *_ in boxes[5]) == ["badge", "caption", "near"]
    assert sorted(k for k, *_ in boxes[30]) == ["badge", "near"]  # the caption is gone by frame 30
    near = next(b for k, _, _, b in boxes[5] if k == "near")
    assert near.bottom <= 900  # just above the object's top
    no_anchors = overlays.shot_boxes(short, tl, short.shots[0], tl.shots[0], None, frames=[5])
    assert "near" not in [k for k, *_ in no_anchors[5]]


# --------------------------------------------------------------------------- covered


def _sample(subject, texts):
    return {"subjects": {"Hero": subject}, "texts": texts}


def test_text_over_a_small_share_of_the_subject_is_fine():
    hero = Rect(100, 1200, 400, 400)
    cap = Rect(100, 1550, 400, 60)  # 10% of the hero
    assert not viewer.coverage_findings("s1", {f: _sample(hero, [("caption", "Hi", None, cap)]) for f in (1, 6)}, 5, 30)


def test_caption_over_the_subject_is_a_warning_and_long_and_heavy_an_error():
    hero = Rect(100, 1200, 400, 400)
    cap = Rect(0, 1150, 1080, 300)  # 62% of the hero
    short = viewer.coverage_findings("s1", {1: _sample(hero, [("caption", "Hi", None, cap)])}, 5, 30)
    assert [(f.check, f.severity, f.obj, f.frame) for f in short] == [("covered", "warning", "Hero", 1)]
    assert "caption 'Hi'" in short[0].message and "subject 'Hero'" in short[0].message
    long = viewer.coverage_findings("s1", {f: _sample(hero, [("caption", "Hi", None, cap)]) for f in range(1, 40, 5)},
                                    5, 30)  # fmt: skip
    assert [f.severity for f in long] == ["error"]
    assert "frames 1-36" in long[0].message


def test_a_label_covering_its_own_part_counts():
    part = Rect(500, 800, 100, 100)
    label = Rect(480, 790, 160, 70)
    found = viewer.coverage_findings("s1", {1: {"subjects": {"Gear": part}, "texts": [("near", "Gear", "Gear", label)]}},
                                     5, 30)  # fmt: skip
    assert len(found) == 1 and "its own object 'Gear'" in found[0].message


# --------------------------------------------------------------------------- framing


def test_a_well_framed_subject_passes():
    boxes = {f: [Rect(200, 500, 600, 800)] for f in range(1, 60, 5)}
    assert not viewer.framing_findings("s1", boxes)


def test_a_tiny_subject_is_reported_unless_the_shot_is_wide():
    boxes = {f: [Rect(500, 900, 100, 100)] for f in range(1, 60, 5)}
    found = viewer.framing_findings("s1", boxes)
    assert any(f.check == "framing" and "small" in f.message for f in found), found
    assert not viewer.framing_findings("s1", boxes, skip=set(boxes))


def test_an_empty_half_is_reported():
    boxes = {f: [Rect(200, 250, 600, 500)] for f in range(1, 60, 5)}  # rows 250-750: all in the top half
    found = viewer.framing_findings("s1", boxes)
    assert [f.message.split(" half")[0] for f in found] == ["the bottom"], found
    some = {f: [Rect(200, 250, 600, 500)] if f < 20 else [Rect(200, 500, 600, 900)] for f in range(1, 60, 5)}
    assert not viewer.framing_findings("s1", some)  # only for a part of the shot


def test_a_wide_subject_centred_in_the_framing_band_passes():
    from bsa.layout import target_area

    band = target_area()  # where camera.frame_objects / frame_box put the subject by default
    w = band.w
    h = w / 1.6  # a wide 1.6:1 box, centred in the band
    box = Rect(band.x, band.y + band.h / 2 - h / 2, w, h)
    assert not viewer.framing_findings("s1", {f: [box] for f in range(1, 60, 5)})


def test_heroes_off_screen_are_not_framing():
    assert not viewer.framing_findings("s1", {1: [None], 6: [Rect(-500, -500, 100, 100)]})


def test_wide_opt_out_frames_and_validation():
    short = _short(wide="establishing shot of the whole bridge")
    assert wide_frames(short.shots[0], Span("s1", 1, 60), 30) == set(range(1, 61))
    ranged = _short(wide=[[0, 0.5]]).shots[0]
    assert wide_frames(ranged, Span("s1", 1, 60), 30) == set(range(1, 17))
    assert not wide_frames(_short().shots[0], Span("s1", 1, 60), 30)
    assert any("wide must be" in e for e in validate(_short(wide=[[2, 1]])))
    assert not any("wide" in e for e in validate(short))


# --------------------------------------------------------------------------- ground


def test_ground_is_large_flat_and_under_the_subject():
    subject = ((-1, -1, 0), (1, 1, 10))
    assert viewer.is_ground((-50, -50, -1), (50, 50, 0), *subject)  # a slab under a tower
    assert viewer.is_ground((-10, -10, 0), (10, 10, 0), *subject)  # a plane
    assert not viewer.is_ground((-50, -50, 5), (50, 50, 5.1), *subject)  # a deck half way up: not under it
    assert not viewer.is_ground((-0.4, -0.4, -1), (0.4, 0.4, 0), *subject)  # a small plinth
    assert not viewer.is_ground((-50, -50, -30), (50, 50, 0), *subject)  # a block, not a floor
    assert not viewer.is_ground((60, 60, -1), (100, 100, 0), *subject)  # beside it


def test_clip_and_union():
    assert viewer.clip(Rect(-10, -10, 20, 20)) == Rect(0, 0, 10, 10)
    assert viewer.clip(Rect(W + 1, 0, 5, 5)) is None
    u = viewer.union([Rect(0, 0, 10, 10), None, Rect(20, 20, 10, 10)])
    assert (u.x, u.y, u.right, u.bottom) == (0, 0, 30, 30)
    assert overlap_area(u, Rect(0, 0, W, H)) == 900


def test_edge_findings_say_how_much_is_outside_and_where_it_is_worst():
    """A hero a little outside is a warning naming the worst share and frame; most of a hero outside for half a
    second or more is an error; an overlay never is."""
    clipped = {"Housing": {"overlay": False, "samples": [(10, 0.0), (15, 0.03), (20, 0.0)]}}
    (f,) = viewer.edge_findings("s02", clipped, 5, 30)
    assert f.severity == "warning" and f.frame == 15 and "3% of 'Housing'" in f.message and "'s02'" in f.message
    gone = {"Housing": {"overlay": False, "samples": [(f, 0.94 if f >= 20 else 0.0) for f in range(0, 50, 5)]}}
    (f,) = viewer.edge_findings("s02", gone, 5, 30)
    assert f.severity == "error" and "94% of 'Housing' is outside the camera frame at frame 20" in f.message, f
    brief = {"Housing": {"overlay": False, "samples": [(f, 0.94 if f == 20 else 0.0) for f in range(0, 50, 5)]}}
    assert viewer.edge_findings("s02", brief, 5, 30)[0].severity == "warning"  # one sample: under half a second
    glow = {"Glow": {"overlay": True, "samples": [(f, 0.94) for f in range(0, 50, 5)] + [(50, 0.0)]}}
    (f,) = viewer.edge_findings("s02", glow, 5, 30)
    assert f.severity == "warning" and "overlay" in f.message
    # a push-in on a part of a big hero: most of its box outside, but the part fills the frame: only a warning
    close = {"Tower": {"overlay": False, "samples": [(f, 0.9, 0.6) for f in range(0, 50, 5)]}}
    assert viewer.edge_findings("s02", close, 5, 30)[0].severity == "warning"
    lost = {"Tower": {"overlay": False, "samples": [(f, 0.9, 0.01) for f in range(0, 50, 5)]}}
    assert viewer.edge_findings("s02", lost, 5, 30)[0].severity == "error"
    unknown = {"Tower": {"overlay": False, "samples": [(f, 0.9, None) for f in range(0, 50, 5)]}}
    assert viewer.edge_findings("s02", unknown, 5, 30)[0].severity == "warning"  # the camera inside it: not lost
    assert viewer.edge_findings("s02", {"Far": {"overlay": False, "samples": [(10, 1.0), (15, 1.0)]}}, 5, 30) == []
    assert viewer.edge_findings("s02", {"In": {"overlay": False, "samples": [(10, 0.0)]}}, 5, 30) == []


def test_swap_problem():
    r = Rect(400, 800, 200, 300)
    assert viewer.SWAP_FRAMES == 1 and viewer.SWAP_BOX_TOL == 0.02
    assert viewer.swap_problem(40, 40, r, r) is None
    assert viewer.swap_problem(40, 41, r, r) is None
    assert "3 frames apart" in viewer.swap_problem(40, 43, r, r)
    assert "never switched off" in viewer.swap_problem(40, None, r, r)
    moved = Rect(r.x + 0.03 * W, r.y, r.w, r.h)
    assert "3%" in viewer.swap_problem(40, 40, moved, r)
    assert viewer.swap_problem(40, 40, Rect(r.x + 21, r.y, r.w, r.h), r) is None  # under 2% of the width
    taller = Rect(r.x, r.y, r.w, r.h + 0.05 * H)  # only its bottom edge moves
    assert "5%" in viewer.swap_problem(40, 40, r, taller)
    assert viewer.swap_problem(40, 40, None, r) is None and viewer.swap_problem(40, 40, r, None) is None
