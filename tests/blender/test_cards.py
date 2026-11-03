"""Cards drawn in Blender's sequencer (runs inside Blender via runner.py): transparent PNGs in the kit's exact colours,
their text inside the box, the logo on its badge, cached by content, and the edit's overlay strips where the kit
layout put them. Uses the made-up kit (tests/kitfix.py) and the first installed OS fallback font (else Blender's)."""

import os
import sys
import tempfile
from pathlib import Path

import bpy
import numpy as np

import bsa.blender.cards
from bsa import cards, kit, kitlayout
from bsa.blender.pixels import read_png
from bsa.layout import Rect
from bsa.overlays import hex_colour

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from kitfix import KIT

K = kit.kit_from(KIT)
FONT = cards.card_font(K)  # no kit font: the first installed OS fallback, else None (Blender's own font)
TMP = Path(tempfile.mkdtemp())
INK = (0x1B, 0x2A, 0x3A)


def _close(got, want, tol) -> bool:
    return all(abs(int(g) - int(w)) <= tol for g, w in zip(got, want, strict=True))


def _same(a: Rect, b: Rect, tol=1e-3) -> bool:
    return all(abs(getattr(a, k) - getattr(b, k)) < tol for k in ("x", "y", "w", "h"))


def _red_png(path: Path, w=64, h=64) -> Path:
    img = bpy.data.images.new("red", w, h, alpha=True)
    try:
        img.pixels.foreach_set(np.tile([1.0, 0.0, 0.0, 1.0], w * h).astype(np.float32))
        img.filepath_raw = str(path)
        img.file_format = "PNG"
        img.save()
    finally:
        bpy.data.images.remove(img)
    return path


def test_a_card_png_is_its_size_with_exact_kit_colours_and_transparent_corners():
    spec = cards.pill(K, "label", "Pivot", FONT)
    png = bsa.blender.cards.render(spec, TMP / "c.png")
    px = read_png(png)
    assert px.shape[:2] == (spec.h, spec.w), px.shape
    assert px[0, 0, 3] == 0 and px[0, spec.w - 1, 3] == 0 and px[spec.h - 1, 0, 3] == 0  # transparent corners
    assert _close(px[3, spec.w // 2, :3], INK, 1) and px[3, spec.w // 2, 3] == 255, px[3, spec.w // 2]  # outline
    y, x = spec.box_h // 2, 8 + K.outline_px
    assert _close(px[y, x, :3], (255, 255, 255), 1) and px[y, x, 3] == 255, px[y, x]  # fill, left of the text
    assert _close(px[spec.h - 2, spec.w // 2, :3], INK, 1), px[spec.h - 2, spec.w // 2]  # the shadow
    assert not [s for s in bpy.data.scenes if s.name.startswith("Card")]
    assert not [p for p in TMP.iterdir() if p.name != "c.png"]


def test_card_text_is_drawn_inside_the_box():
    for spec in (cards.pill(K, "label", "Pivot gear", FONT), cards.pill(K, "label", "Gear", FONT, dot="#D9483B"),
                 cards.title(K, ("How a lever", "lifts more"), 1, FONT, 900)):  # fmt: skip
        px = read_png(bsa.blender.cards.render(spec, TMP / f"t-{spec.kind}-{len(spec.runs)}.png"))
        text = np.abs(px.astype(int) - cards.shape_rgba(spec).astype(int)).max(axis=2) > 24  # what the text drew
        ys, xs = np.where(text)
        assert len(xs), spec.kind
        o = K.outline_px + 4  # inside the outline ring, by 4 px more
        assert xs.min() >= o and ys.min() >= o and xs.max() < spec.w - o and ys.max() < spec.box_h - o, (
            spec.kind, (xs.min(), ys.min(), xs.max(), ys.max()), spec.w, spec.box_h)
        for run in spec.runs:  # each line in its colour, its ink around its Run.y (the middle of that line's box)
            rgb = np.array([round(c * 255) for c in hex_colour(run.colour)[:3]])
            mine = text & (np.abs(px[..., :3].astype(int) - rgb).max(axis=2) <= 6)
            mine[: max(0, round(run.y - run.size * 0.8))] = False
            mine[round(run.y + run.size * 0.8) :] = False
            ys, xs = np.where(mine)
            assert len(xs) > 20, (spec.kind, run)
            mid = (ys.min() + ys.max() + 1) / 2
            assert abs(mid - run.y) <= run.size * 0.35, (spec.kind, run.text, mid, run.y)
            left = run.x if run.anchor == "LEFT" else run.x - cards.measure(run.text, run.size, spec.font) / 2
            assert abs(xs.min() - left) <= run.size * 0.25, (spec.kind, run.text, xs.min(), left)


def test_the_logo_shows_on_the_logo_badge():
    (TMP / "logo-src").mkdir(exist_ok=True)
    logo = _red_png(TMP / "logo-src" / "red.png")
    k = kit.kit_from({**KIT, "logo": {"file": str(logo), "name": "Acme"}})
    spec = cards.logo_badge(k, FONT, "Acme")
    px = read_png(bsa.blender.cards.render(spec, TMP / "logo.png"))
    x, y, w, h = spec.logo_rect
    red = (px[..., 0] > 230) & (px[..., 1] < 30) & (px[..., 2] < 30) & (px[..., 3] == 255)
    ys, xs = np.where(red)
    assert len(xs), "no red pixels"
    assert xs.min() >= int(x) and xs.max() < x + w + 1 and ys.min() >= int(y) and ys.max() < y + h + 1, (
        (xs.min(), ys.min(), xs.max(), ys.max()), spec.logo_rect)
    assert red[round(y + h / 2), round(x + w / 2)]  # the middle of the logo rect is the logo
    logo.unlink()  # moved or deleted after setup: no badge, nothing to draw, nothing fails
    assert cards.logo_badge(k, FONT, "Acme") is None


def test_ensure_renders_once():
    spec = cards.pill(K, "badge", "SIMPLIFIED", FONT)
    cache = TMP / "cache"
    first = bsa.blender.cards.ensure(spec, cache)
    assert first == cache / f"{cards.cache_key(spec)}.png" and first.is_file()
    mtime = first.stat().st_mtime_ns
    os.utime(first, ns=(mtime, mtime))
    second = bsa.blender.cards.ensure(spec, cache)
    assert second == first and second.stat().st_mtime_ns == mtime
    assert [p.name for p in cache.iterdir()] == [first.name]  # no temp files left behind


def test_rendering_cards_leaves_every_engine_alone():
    before = {s.name: s.render.engine for s in bpy.data.scenes}
    bsa.blender.cards.render(cards.pill(K, "step", "2 / 4", FONT), TMP / "engine.png")
    bsa.blender.cards.render(cards.progress_track(K, 700), TMP / "engine2.png")
    assert {s.name: s.render.engine for s in bpy.data.scenes} == before


def _edit_scene(name: str):
    old = bpy.data.scenes.get(name)
    if old is not None:
        bpy.data.scenes.remove(old)
    scene = bpy.data.scenes.new(name)
    r = scene.render
    r.resolution_x, r.resolution_y, r.resolution_percentage = 1080, 1920, 100
    r.use_sequencer, r.use_compositing = True, False
    scene.view_settings.view_transform = "Standard"
    scene.view_settings.look = "None"
    return scene


def _at(scene, strip, path: str, frame: int) -> float:
    """A strip property at `frame`: its keyframed curve evaluated there, else its value (frame_set doesn't evaluate a
    scene other than the context's, as test_edit notes)."""
    from bsa.blender.bake import _fcurves

    full = f"{strip.path_from_id()}.{path}"
    ad = scene.animation_data
    for fc in _fcurves(ad.action) if ad and ad.action else []:
        if fc.data_path == full:
            return fc.evaluate(frame)
    return strip.path_resolve(path)


def _strip_rect(scene, strip, frame_no: int = 1, frame=(1080, 1920)) -> Rect:
    """Where an unrotated image strip (fit ORIGINAL, origin 0, 0) draws at `frame_no`, px from the top-left."""
    iw, ih = strip.elements[0].orig_width, strip.elements[0].orig_height
    sx, sy = _at(scene, strip, "transform.scale_x", frame_no), _at(scene, strip, "transform.scale_y", frame_no)
    left = _at(scene, strip, "transform.offset_x", frame_no) + (frame[0] - iw) / 2
    bottom = _at(scene, strip, "transform.offset_y", frame_no) + (frame[1] - ih) / 2
    return Rect(left, frame[1] - bottom - ih * sy, iw * sx, ih * sy)


def _overlay():
    """A small KitOverlay by hand: a static card, a tracked card, a pointer with its dot, the progress bar."""
    from bsa.layout import safe_rect

    pill = cards.pill(K, "label", "Gear", FONT, dot="#D9483B")
    tag = cards.pill(K, "category", "Spark", FONT)
    static = kitlayout.Placed(tag, 1, 3, kitlayout.CH["category"], at=Rect(700, 300, tag.w, tag.h), text="Spark")
    track = {f: Rect(200 + 10 * f, 900, pill.w, pill.h) for f in (1, 2, 3)}
    near = kitlayout.Placed(pill, 1, 3, kitlayout.CH["near"], track=track, text="Gear", near="Gear")
    anchor = (520, 1300)
    ends = {f: (kitlayout.pointer_end(r, anchor), anchor) for f, r in track.items()}
    ends[3] = None
    length0 = max(np.hypot(e[1][0] - e[0][0], e[1][1] - e[0][1]) for e in ends.values() if e)
    ptr = kitlayout.Pointer(1, 3, kitlayout.CH["pointer"], cards.pointer(K, np.ceil(length0)), cards.dot(K), ends)
    s = safe_rect()
    t, f = cards.progress_track(K, s.w), cards.progress_fill(K, s.w)
    prog = kitlayout.Progress(t, f, Rect(s.x, 1500, t.w, t.h), 1, 3, kitlayout.CH["progress"])
    return kitlayout.KitOverlay([static, near], [ptr], prog, []), static, near, ptr, prog


def test_overlay_strips_are_placed_where_the_layout_said():
    scene = _edit_scene("CardsEdit")
    try:
        seq = scene.sequence_editor_create()
        ov, static, near, ptr, prog = _overlay()
        bsa.blender.cards.add_overlay(seq, ov, TMP / "ov-cache")
        strips = {s.channel: s for s in seq.strips_all}
        a = strips[static.channel]
        assert (a.frame_final_start, a.frame_final_end) == (1, 4) and a.blend_type == "ALPHA_OVER"
        assert _same(_strip_rect(scene, a), static.at), (_strip_rect(scene, a), static.at)
        b = strips[near.channel]
        for frame, want in near.track.items():
            assert _same(_strip_rect(scene, b, frame), want), (frame, _strip_rect(scene, b, frame), want)
        p, d = strips[ptr.channel], strips[kitlayout.CH["dot"]]
        for frame in (1, 2):
            want = kitlayout.pointer_transform(*ptr.ends[frame], ptr.spec.w, ptr.spec.h)
            for name in ("offset_x", "offset_y", "scale_x", "rotation"):
                assert abs(_at(scene, p, f"transform.{name}", frame) - want[name]) < 1e-3, (frame, name)
            ax, ay = ptr.ends[frame][1]
            dot = _strip_rect(scene, d, frame)
            assert abs(dot.x + dot.w / 2 - ax) < 1e-3 and abs(dot.y + dot.h / 2 - ay) < 1e-3, (frame, dot)
        assert tuple(p.transform.origin) == (0.0, 0.5)
        assert _at(scene, p, "transform.scale_x", 3) == 0 and _at(scene, d, "transform.scale_x", 3) == 0  # under it
        track, fill, outline = strips[prog.channel], strips[prog.channel + 1], strips[prog.channel + 2]
        assert _same(_strip_rect(scene, track), prog.at) and _same(_strip_rect(scene, outline), prog.at)
        o = K.outline_px
        assert _same(_strip_rect(scene, fill), Rect(prog.at.x + o, prog.at.y + o, prog.fill.w, prog.fill.h))
        shown = [prog.fill.w - round(_at(scene, fill, "crop.max_x", f)) for f in (1, 2, 3)]
        assert shown == [round(prog.fill.w * (f - prog.f0 + 1) / (prog.f1 - prog.f0 + 1)) for f in (1, 2, 3)], shown
    finally:
        bpy.data.scenes.remove(scene)


def test_pointer_strip_reaches_the_anchor():
    scene = _edit_scene("CardsPointer")
    out = Path(tempfile.mkdtemp())
    try:
        seq = scene.sequence_editor_create()
        _, _, _, ptr, _ = _overlay()
        bsa.blender.cards.add_overlay(seq, kitlayout.KitOverlay([], [ptr], None, []), TMP / "ptr-cache")
        r = scene.render
        if hasattr(r.image_settings, "media_type"):
            r.image_settings.media_type = "IMAGE"
        r.image_settings.file_format = "PNG"
        r.image_settings.color_mode = "RGBA"
        r.filepath = str(out / "f_")
        # an animation render: a still render would use the context scene's frame and skip the keyframes
        scene.frame_start, scene.frame_end = 1, 3
        bpy.ops.render.render(animation=True, scene=scene.name)
    finally:
        bpy.data.scenes.remove(scene)
    ink = []
    for f in (1, 3):
        px = read_png(out / f"f_{f:04d}.png")
        ink.append((np.abs(px[..., :3].astype(int) - np.array(INK)).max(axis=2) <= 8) & (px[..., 3] > 200))
    (sx, sy), (ex, ey) = ptr.ends[1]
    for x, y in ((sx, sy), (ex, ey), ((sx + ex) / 2, (sy + ey) / 2)):
        x, y = round(x), round(y)
        assert ink[0][max(0, y - 6) : y + 7, max(0, x - 6) : x + 7].any(), (x, y)
    assert not ink[1].any()  # frame 3: the anchor is under the card, no line and no dot


def _text_ink(spec, px) -> np.ndarray:
    """The pixels the card's text drew: where the render differs from the shapes alone."""
    return np.abs(px.astype(int) - cards.shape_rgba(spec).astype(int)).max(axis=2) > 24


def test_blenders_own_font_fits_its_padding_and_is_centred():
    """Blender's own font (font None): long text stays inside the design padding (2 px for side bearings and
    antialiasing) and the ink, cap tops to descenders, is centred in the box within 3 px."""
    for spec in (cards.pill(K, "label", "Pivot gear, the part that gets the push", None),
                 cards.title(K, ("Push the long end", "and lift the heavy load"), 1, None, 1000)):  # fmt: skip
        assert spec.font is None
        px = read_png(bsa.blender.cards.render(spec, TMP / f"none-{spec.kind}.png"))
        ys, xs = np.where(_text_ink(spec, px))
        pad = cards.PILL_PAD[0] if spec.kind == "label" else cards.CARD_PAD
        assert xs.min() >= pad - 2 and xs.max() < spec.w - pad + 2, (spec.kind, xs.min(), xs.max(), spec.w, pad)
        above, below = ys.min(), spec.box_h - (ys.max() + 1)
        assert abs(above - below) <= 3, (spec.kind, above, below)


def test_a_tracked_card_without_a_track_is_left_out():
    scene = _edit_scene("CardsEmpty")
    try:
        seq = scene.sequence_editor_create()
        pill = cards.pill(K, "label", "Gear", FONT)
        empty = kitlayout.Placed(pill, 1, 3, kitlayout.CH["near"], track={}, text="Gear", near="Gear")
        bsa.blender.cards.add_overlay(seq, kitlayout.KitOverlay([empty], [], None, []), TMP / "empty-cache")
        assert len(seq.strips_all) == 0
    finally:
        bpy.data.scenes.remove(scene)


def test_a_scene_named_card_is_left_alone():
    mine = bpy.data.scenes.new("Card")  # the user's own scene of that name
    try:
        bsa.blender.cards.render(cards.pill(K, "label", "Gear", FONT), TMP / "named.png")
        assert bpy.data.scenes.get("Card") == mine
        assert not [s for s in bpy.data.scenes if s.name.startswith("Card-")]  # the drawing scene is gone
    finally:
        bpy.data.scenes.remove(mine)
