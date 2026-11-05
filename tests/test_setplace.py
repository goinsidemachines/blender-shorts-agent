"""Where a scene set's props may go (bsa.setplace): the set chosen, the props, spots seeded by the short's slug,
the one clearance rule placement and set_clearance share, and sets.json."""

import json
import string
import threading

import pytest
from fontbuild import build_font

from bsa import closing, kit, overlays, setplace
from bsa.layout import H, Rect, W, overlap_area
from bsa.review import decisions
from bsa.short import Short, Shot
from bsa.timeline import Card, Span, Timeline
from tests.kitfix import KIT

K = kit.kit_from(KIT)
FOOT = (-0.5, -0.5, 0.5, 0.5)
FLOOR = (-4.0, -3.0, 4.0, 5.0)


@pytest.fixture
def font(tmp_path):
    p = tmp_path / "card.ttf"
    p.write_bytes(build_font(string.ascii_letters + string.digits + string.punctuation, advance=500))
    return p


@pytest.fixture
def short():
    s = Shot(id="a", title="t", picture="p", scene="Main", badge="simplified", step=[1, 3], category="flow",
             title_card={"lines": ["How a lever"], "highlight": 0}, labels=["Stacked", {"text": "2 m", "kind": "measure"}])  # fmt: skip
    return Short(slug="x", title="X", length_s=2, shots=[s], style={})


@pytest.fixture
def tl():
    return Timeline(30, 60, [Span("a", 1, 60)], [], [Card("Hello there", "l1", 1, 20),
                                                     Card("A second caption card", "l1", 21, 40)])  # fmt: skip


def test_auto_set_follows_size_and_topic():
    assert setplace.auto_set(0.2) == "workbench"
    assert setplace.auto_set(3) == "studio"
    assert setplace.auto_set(3, measuring=True) == "graph"
    assert setplace.auto_set(30) == "outdoor"
    assert setplace.auto_set(30, measuring=True) == "outdoor"
    assert setplace.resolve("auto", 0.2) == "workbench"
    assert setplace.resolve("none", 0.2) is None
    assert setplace.resolve("graph", 30) == "graph"


def test_props_are_the_sets_objects_only():
    names = lambda s, m=1.0: [p.name for p in setplace.props(s, m)]
    assert len(names("workbench")) == 7 and len(set(names("workbench"))) == 7
    assert [p.zone for p in setplace.props("workbench", 1)].count("wall") == 3
    assert len(names("graph")) == 3
    assert [p.zone for p in setplace.props("outdoor", 20)] == ["floor"] * 4 + ["sky"] * 3
    assert setplace.props("studio", 1) == []
    big, small = setplace.props("workbench", 10), setplace.props("workbench", 0.01)
    assert big[0].dims == tuple(d * 4.0 / 0.25 for d in small[0].dims)  # s is clamped to 0.25 .. 4
    assert setplace.props("outdoor", 20)[0].dims[2] == pytest.approx(20 * 0.4)
    banned = ("person", "man", "woman", "face", "head", "dog", "cat", "bird", "animal", "human", "figure")
    for s in setplace.SETS:
        for p in setplace.props(s, 1):
            assert not any(b in p.name or b in p.shape for b in banned)


def test_candidates_are_deterministic_per_slug_and_differ_between_slugs():
    a = setplace.candidates("workbench", "lever", "Main", 1.0, FOOT, FLOOR)
    assert a == setplace.candidates("workbench", "lever", "Main", 1.0, FOOT, FLOOR)
    assert a != setplace.candidates("workbench", "pulley", "Main", 1.0, FOOT, FLOOR)
    assert a != setplace.candidates("workbench", "lever", "Close", 1.0, FOOT, FLOOR)
    assert [len(c) for c in a] == [setplace.TRIES] * len(setplace.props("workbench", 1.0))
    assert [c[0].prop for c in a] == setplace.props("workbench", 1.0)
    assert setplace.candidates("studio", "lever", "Main", 1.0, FOOT, FLOOR) == []


def test_floor_candidates_never_sit_in_the_subject_footprint():
    x0, y0, x1, y1 = FOOT
    gx, gy = (x1 - x0) * 0.1, (y1 - y0) * 0.1
    for slug in ("lever", "pulley", "gear", "crane", "door-lock"):
        for set_name, size in (("workbench", 1.0), ("graph", 1.0), ("outdoor", 1.0)):
            for tries in setplace.candidates(set_name, slug, "Main", size, FOOT, FLOOR):
                for c in tries:
                    if c.prop.zone == "floor":
                        r = max(c.prop.dims[0], c.prop.dims[1]) / 2
                        inside = (x0 - gx - r < c.x < x1 + gx + r) and (y0 - gy - r < c.y < y1 + gy + r)
                        assert not inside, (slug, c)
                        assert FLOOR[0] <= c.x <= FLOOR[2] and FLOOR[1] <= c.y <= FLOOR[3]
                        assert c.z == 0
                        beside = c.y >= y0 - gy - r or c.x <= x0 - gx - r or c.x >= x1 + gx + r  # behind or beside
                        assert beside or set_name in setplace.FRONT_SETS  # graph's tools may lie in front too
                    elif c.prop.zone == "wall":
                        assert c.y == FLOOR[3] and c.z > 0
                    else:
                        assert c.prop.zone == "sky" and c.y == FLOOR[3] and c.z > size


def test_collision_names_subject_then_texts():
    subj = [Rect(400, 800, 200, 200)]
    texts = [("caption", Rect(60, 1300, 880, 160)), *[("covered", r) for r in setplace.covered_rects()]]
    m = setplace.SUBJECT_MARGIN_PX
    assert setplace.collision(Rect(380, 780, 30, 30), subj, texts, m) == "over the subject"
    assert setplace.collision(Rect(600 + m - 1, 900, 30, 30), subj, texts, m) == "over the subject"  # in the margin
    assert setplace.collision(Rect(600 + m + 1, 900, 30, 30), subj, texts, m) is None
    assert setplace.collision(Rect(100, 1350, 50, 50), subj, texts, m) == "over the caption"
    assert setplace.collision(Rect(1000, 300, 40, 40), subj, texts, m) == "over the strip the Shorts player covers"
    assert setplace.collision(Rect(100, 300, 40, 40), subj, texts, m) is None
    assert setplace.collision(Rect(100, 300, 40, 40), [], [], m) is None


def test_collision_names_each_text_kind_readably():
    r = Rect(0, 0, 10, 10)
    want = {"near": "over the near label", "covered": "over the strip the Shorts player covers",
            "caption": "over the caption", "badge": "over the badge", "label": "over the label",
            "stacked": "over the label", "measure": "over the measure", "title": "over the title",
            "bars": "over the bars", "logo": "over the logo", "category": "over the category tag",
            "step": "over the step tag"}  # fmt: skip
    for kind, why in want.items():
        assert setplace.collision(r, [], [(kind, r)], 0) == why, kind


def test_covered_rects_are_the_bottom_and_right_strips():
    bottom, right = setplace.covered_rects()
    assert bottom == Rect(0, H * (1 - closing.COVERED_BOTTOM), W, H * closing.COVERED_BOTTOM)
    assert right == Rect(W * (1 - closing.COVERED_RIGHT), 0, W * closing.COVERED_RIGHT, H)


@pytest.mark.parametrize("with_kit", [False, True])
def test_text_rects_match_shot_boxes_plus_the_covered_strips(short, tl, font, with_kit):
    k, f = (K, font) if with_kit else (None, None)
    frames = [5, 30, 50]
    got = setplace.text_rects(short, tl, short.shots[0], tl.shots[0], k, f, frames)
    boxes = overlays.shot_boxes(short, tl, short.shots[0], tl.shots[0], None, None, frames, kit=k, font=f)
    assert sorted(got) == frames
    for fr in frames:
        want = [(b[0], b[3]) for b in boxes[fr]] + [("covered", r) for r in setplace.covered_rects()]
        assert got[fr] == want
    assert any(kind == "caption" for kind, _ in got[5]) and not any(kind == "caption" for kind, _ in got[50])


def _grid_project(c, frame):
    """A made-up camera: world x -4..4 -> 0..1080 px, world z 0..6 -> 1920..0 px (height), y ignored."""
    px = (c.x + 4) / 8 * W
    w = max(c.prop.dims[0], 0.1) / 8 * W
    zc = c.z + (c.prop.dims[2] / 2 if c.prop.zone == "floor" else 0)
    py = H - zc / 6 * H
    h = max(c.prop.dims[2], 0.1) / 6 * H
    return Rect(px - w / 2, py - h / 2, w, h)


def test_no_room_drops_every_prop_and_says_why():
    cands = setplace.candidates("workbench", "lever", "Main", 1.0, FOOT, FLOOR)
    frames = [1, 2, 3]
    full = {f: [Rect(0, 0, W, H)] for f in frames}
    kept, dropped = setplace.place(cands, _grid_project, frames, full, {f: [] for f in frames})
    assert kept == []
    assert [d["name"] for d in dropped] == [p.name for p in setplace.props("workbench", 1.0)]
    assert all(d["why"] == "over the subject" for d in dropped)


def test_place_keeps_the_first_clear_spot_on_every_frame():
    cands = setplace.candidates("workbench", "lever", "Main", 1.0, FOOT, FLOOR)
    frames = [1, 2]
    none = {f: [] for f in frames}
    kept, dropped = setplace.place(cands, _grid_project, frames, none, none)
    assert dropped == [] or all(d["why"] == "against another prop" for d in dropped)
    assert kept[0] == cands[0][0]  # nothing in the way: the first spot
    # A subject on frame 2 only, over the toolbox's first spot: the toolbox moves to a later spot.
    first = _grid_project(cands[0][0], 1)
    subj = {1: [], 2: [first]}
    kept2, _ = setplace.place(cands, _grid_project, frames, subj, none)
    tb = next(c for c in kept2 if c.prop.name == "toolbox")
    assert tb != cands[0][0] and tb in cands[0]
    for f in frames:
        assert setplace.collision(_grid_project(tb, f), subj[f], [], setplace.SUBJECT_MARGIN_PX) is None
    # The caption's rect on every frame drops what it covers, naming it; a prop off camera (None) is clear.
    texts = {f: [("caption", Rect(0, 0, W, H))] for f in frames}
    _, dropped3 = setplace.place(cands, _grid_project, frames, none, texts)
    assert {d["why"] for d in dropped3} == {"over the caption"}
    kept4, dropped4 = setplace.place(cands, lambda c, f: None, frames, {f: [Rect(0, 0, W, H)] for f in frames}, none)
    assert kept4 and (dropped4 == [] or all(d["why"] == "against another prop" for d in dropped4))


def _entry(set_name="workbench", size=1.0):
    p = setplace.props(set_name, size)[0]
    return {"set": set_name, "floor": list(FLOOR),
            "props": [{"name": p.name, "at": [1.5, 2.0, 0.0], "rot": 0.3, "dims": list(p.dims)}],
            "dropped": [{"name": "jar", "why": "over the subject"}]}  # fmt: skip


def _approve(sdir, version=1):
    return decisions.record(sdir, "storyboard", version, "approve", "page", current=version)


def test_bad_or_stale_sets_json_is_placed_again_not_trusted(tmp_path):
    sdir = tmp_path / "lever"
    (sdir / "source").mkdir(parents=True)
    path = sdir / "source" / setplace.SETS_JSON
    assert setplace.saved_for(sdir, "Main", "workbench", 1.0) == (None, None)  # nothing saved: no warning
    setplace.save_scene(sdir, "Main", _entry())
    # Not approved: placed again.
    entry, why = setplace.saved_for(sdir, "Main", "workbench", 1.0)
    assert entry is None and "approv" in why
    _approve(sdir)
    assert setplace.approved(sdir)
    assert setplace.saved_for(sdir, "Main", "workbench", 1.0) == (_entry(), None)
    # Another scene, another set, an unknown prop, a missing key, bad JSON.
    entry, why = setplace.saved_for(sdir, "Close", "workbench", 1.0)
    assert entry is None and "Close" in why
    entry, why = setplace.saved_for(sdir, "Main", "graph", 1.0)
    assert entry is None and "graph" in why and "workbench" in why
    bad = _entry()
    bad["props"][0]["name"] = "teddy"
    setplace.save_scene(sdir, "Main", bad)
    entry, why = setplace.saved_for(sdir, "Main", "workbench", 1.0)
    assert entry is None and "teddy" in why
    bad = _entry()
    del bad["props"][0]["at"]
    setplace.save_scene(sdir, "Main", bad)
    entry, why = setplace.saved_for(sdir, "Main", "workbench", 1.0)
    assert entry is None and why
    bad = _entry()
    del bad["floor"]
    setplace.save_scene(sdir, "Main", bad)
    assert setplace.saved_for(sdir, "Main", "workbench", 1.0)[0] is None
    path.write_text("{not json", encoding="utf-8")
    assert setplace.load(sdir) == {}
    entry, why = setplace.saved_for(sdir, "Main", "workbench", 1.0)
    assert entry is None and "sets.json" in why
    path.write_text("[1, 2]", encoding="utf-8")
    assert setplace.load(sdir) == {}
    assert setplace.saved_for(sdir, "Main", "workbench", 1.0)[0] is None
    # Superseded approval: placed again.
    path.unlink()
    setplace.save_scene(sdir, "Main", _entry())
    assert setplace.saved_for(sdir, "Main", "workbench", 1.0)[0] == _entry()
    decisions.record(sdir, "storyboard", 2, "changes", "page", current=2)
    assert not setplace.approved(sdir)
    assert setplace.saved_for(sdir, "Main", "workbench", 1.0)[0] is None
    sdir2 = tmp_path / "pulley"
    (sdir2 / "source").mkdir(parents=True)
    setplace.save_scene(sdir2, "Main", _entry())
    _approve(sdir2, 1)
    decisions.supersede_stale(sdir2, "storyboard", 2)
    assert not setplace.approved(sdir2)
    entry, why = setplace.saved_for(sdir2, "Main", "workbench", 1.0)
    assert entry is None and why


def test_save_scene_is_atomic_and_keeps_other_scenes(tmp_path):
    sdir = tmp_path / "lever"
    (sdir / "source").mkdir(parents=True)
    setplace.save_scene(sdir, "Main", _entry(), slug="lever")
    setplace.save_scene(sdir, "Close", _entry("graph"))
    data = setplace.load(sdir)
    assert data == {"slug": "lever", "scenes": {"Main": _entry(), "Close": _entry("graph")}}
    assert json.loads((sdir / "source" / "sets.json").read_text(encoding="utf-8")) == data
    # Writers racing on different scenes all land (locked read-merge-write).
    threads = [threading.Thread(target=setplace.save_scene, args=(sdir, f"S{i}", _entry())) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert set(setplace.load(sdir)["scenes"]) == {"Main", "Close", *[f"S{i}" for i in range(8)]}
    assert not [p for p in (sdir / "source").iterdir() if p.name != "sets.json"]  # no temp or lock files left
    # A damaged file is replaced, not crashed on; a missing slug defaults to the folder's name.
    (sdir / "source" / "sets.json").write_text("{oops", encoding="utf-8")
    setplace.save_scene(sdir, "Main", _entry())
    assert setplace.load(sdir) == {"slug": "lever", "scenes": {"Main": _entry()}}


def test_overlap_area_is_the_measure_of_meeting():
    # collision counts only a positive overlap: rects that only touch at an edge do not meet.
    a, b = Rect(0, 0, 10, 10), Rect(10, 0, 10, 10)
    assert overlap_area(a, b) == 0
    assert setplace.collision(a, [], [("caption", b)], 0) is None


def test_kept_dropped_and_spotless_props_cover_every_prop():
    # A floor so small the floor props have no spots at all: they come back from dropped_without_spots.
    small = (-0.6, -0.6, 0.6, 0.6)
    cands = setplace.candidates("workbench", "lever", "Main", 1.0, FOOT, small)
    frames = [1, 2]
    none = {f: [] for f in frames}
    kept, dropped = setplace.place(cands, _grid_project, frames, none, none)
    spotless = setplace.dropped_without_spots("workbench", 1.0, cands)
    assert spotless and all(d["why"] == setplace.NO_ROOM for d in spotless)
    names = [c.prop.name for c in kept] + [d["name"] for d in dropped + spotless]
    assert sorted(names) == sorted(p.name for p in setplace.props("workbench", 1.0))


def test_saved_props_whose_size_no_longer_matches_are_placed_again(tmp_path):
    sdir = tmp_path / "lever"
    (sdir / "source").mkdir(parents=True)
    setplace.save_scene(sdir, "Main", _entry(size=1.0))
    _approve(sdir)
    assert setplace.saved_for(sdir, "Main", "workbench", 1.0)[0] == _entry(size=1.0)
    assert setplace.saved_for(sdir, "Main", "workbench", 1.0005)[0] == _entry(size=1.0)  # within the tolerance
    entry, why = setplace.saved_for(sdir, "Main", "workbench", 2.0)
    assert entry is None and "toolbox" in why and "size" in why


def test_an_approval_of_an_older_storyboard_is_not_reused(tmp_path):
    sdir = tmp_path / "lever"
    (sdir / "source").mkdir(parents=True)
    setplace.save_scene(sdir, "Main", _entry())
    d = _approve(sdir, 1)
    assert setplace.saved_for(sdir, "Main", "workbench", 1.0, current=1)[0] == _entry()
    decisions.mark_applied(sdir, d["id"])  # applied: supersede_stale no longer retires it, the version still counts
    entry, why = setplace.saved_for(sdir, "Main", "workbench", 1.0, current=2)
    assert entry is None and "approv" in why
    # An approval not applied yet that answered another version is retired on the way.
    sdir2 = tmp_path / "pulley"
    (sdir2 / "source").mkdir(parents=True)
    setplace.save_scene(sdir2, "Main", _entry())
    _approve(sdir2, 1)
    assert setplace.saved_for(sdir2, "Main", "workbench", 1.0, current=2)[0] is None
    assert decisions.latest(sdir2, "storyboard") is None  # superseded


def test_reusable_asks_the_storyboard_for_its_current_version(tmp_path, monkeypatch):
    from bsa.review import snapshot

    sdir = tmp_path / "lever"
    (sdir / "source").mkdir(parents=True)
    setplace.save_scene(sdir, "Main", _entry())
    _approve(sdir, 3)
    asked = []
    monkeypatch.setattr(snapshot, "stage_version", lambda s, stage: asked.append(stage) or 3)
    assert setplace.reusable(sdir, "Main", "workbench", 1.0) == (_entry(), None)
    assert asked == ["storyboard"]
    monkeypatch.setattr(snapshot, "stage_version", lambda s, stage: 4)
    entry, why = setplace.reusable(sdir, "Main", "workbench", 1.0)
    assert entry is None and why

    def broken(s, stage):
        raise OSError("disk gone")

    monkeypatch.setattr(snapshot, "stage_version", broken)
    entry, why = setplace.reusable(sdir, "Main", "workbench", 1.0)
    assert entry is None and "disk gone" in why


def test_graph_tools_may_lie_in_front_of_the_subject():
    # The approved graph sample has the ruler and set square on the paper in front of the lever.
    def ahead(set_name):
        return [c for tries in setplace.candidates(set_name, "lever", "Main", 1.0, FOOT, FLOOR) for c in tries
                if c.prop.zone == "floor" and c.y < FOOT[1]]  # fmt: skip

    assert ahead("graph")
    assert not ahead("workbench") and not ahead("outdoor")  # tools on the bench ends, trees to the sides and back
