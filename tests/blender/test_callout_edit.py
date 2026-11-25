"""The edit's callouts (bsa.blender.callout_edit): the label plate pops in at the cue frame, the arrow strips follow the
part, the culprit's colours, the arrow dropped where the part is off screen, and the encode itself."""

import tempfile
from pathlib import Path

import bpy
import numpy as np

from bsa.blender import edit
from bsa.layout import H, W
from bsa.overlays import caption_highlight, hex_colour
from bsa.short import Line, Short, Shot
from bsa.timeline import Card, Span, Timeline

BG = (0.2, 0.4, 0.8)
POINT = [540.0, 1100.0]


def _write_png(path: Path) -> None:
    img = bpy.data.images.new("tmp_color", W, H)
    img.pixels.foreach_set(np.tile([*BG, 1.0], W * H).astype(np.float32))
    img.filepath_raw = str(path)
    img.file_format = "PNG"
    img.save()
    bpy.data.images.remove(img)


def _fixture(culprit=False):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    tmp = Path(tempfile.mkdtemp())
    frames = tmp / "frames"
    for shot, first in (("a", 1), ("b", 16)):
        (frames / shot).mkdir(parents=True)
        for i in range(first, first + 15):
            _write_png(frames / shot / f"{i:04d}.png")
    call = {"text": "Valve", "near": "Valve", "on": "valve", "culprit": culprit}
    short = Short(slug="t", title="t", length_s=1.0,
                  shots=[Shot("a", "A", "p", "s", min_s=0.5, callouts=[call]), Shot("b", "B", "p", "s", min_s=0.5)],
                  lines=[Line("l1", "The valve opens", "a"), Line("l2", "A second card", "b")])  # fmt: skip
    tl = Timeline(30, 30, [Span("a", 1, 15), Span("b", 16, 30)], [Span("l1", 3, 12), Span("l2", 18, 28)],
                  [Card("The valve opens", "l1", 3, 12), Card("A second card", "l2", 18, 28)])  # fmt: skip
    cue = {"i": 0, "text": "Valve", "near": "Valve", "at": None, "f_on": 4, "f_off": 9, "glow": True,
           "culprit": culprit, "estimated": False}  # fmt: skip
    callouts = {"cues": {"a": [cue]}, "anchors": {"a": {"0": {"f0": 1, "points": [POINT] * 15}}}}
    return short, tl, frames, tmp / "out.mp4", callouts


def _strips(scene, prefix):
    return [s for s in scene.sequence_editor.strips_all if s.name.startswith(prefix)]


def _plate(scene):
    return next(s for s in scene.sequence_editor.strips_all if s.type == "TEXT" and s.text == "Valve")


def _value(strip, path, frame):
    """The animated value of `path` ("location", "font_size", ...; "transform.scale_x") of `strip` at `frame`, from
    its F-curves."""
    action = strip.id_data.animation_data.action
    owner, _, prop = path.rpartition(".")
    name = (getattr(strip, owner) if owner else strip).path_from_id(prop)
    curves = []
    if hasattr(action, "fcurves"):
        curves = [fc for fc in action.fcurves if fc.data_path == name]
    else:
        for layer in action.layers:
            for st in layer.strips:
                for bag in st.channelbags:
                    curves += [fc for fc in bag.fcurves if fc.data_path == name]
    return [fc.evaluate(frame) for fc in sorted(curves, key=lambda fc: fc.array_index)]


def _render_frame(scene, frame):
    r = scene.render
    saved = (getattr(r.image_settings, "media_type", None), r.image_settings.file_format, r.filepath,
             scene.frame_start, scene.frame_end)  # fmt: skip
    out_dir = Path(tempfile.mkdtemp())
    try:
        if saved[0] is not None:
            r.image_settings.media_type = "IMAGE"
        r.image_settings.file_format = "PNG"
        r.filepath = str(out_dir / "f_")
        scene.frame_start = scene.frame_end = frame
        bpy.ops.render.render(animation=True, scene=scene.name)
    finally:
        if saved[0] is not None:
            r.image_settings.media_type = saved[0]
        r.image_settings.file_format, r.filepath = saved[1], saved[2]
        scene.frame_start, scene.frame_end = saved[3], saved[4]
    img = bpy.data.images.load(str(next(out_dir.glob("f_*.png"))))
    a = np.array(img.pixels[:], dtype=np.float32).reshape(H, W, 4)[::-1, :, :3]
    bpy.data.images.remove(img)
    return np.abs(a - a[5, 5]).max(axis=2) > 0.03  # top-down mask of what is drawn over the frame


def test_label_and_arrow_strips_run_from_the_cue_frame():
    short, tl, frames, out, callouts = _fixture()
    sc = edit.build_edit(short, tl, frames, None, out, {}, preset="preview", callouts=callouts)
    plate = _plate(sc)
    assert plate.frame_final_start == 4 and plate.frame_final_end - 1 == 9 + 5  # f_on to the end of the fade
    shaft, head = _strips(sc, "Callout_shaft"), _strips(sc, "Callout_head")
    assert len(shaft) == len(head) == 1 and shaft[0].frame_final_start == head[0].frame_final_start == 4
    small, full = _value(plate, "font_size", 4)[0], _value(plate, "font_size", 12)[0]
    assert small < full  # popped in from smaller
    assert _value(plate, "blend_alpha", 4)[0] < _value(plate, "blend_alpha", 8)[0] <= 1.0


def test_callout_hidden_before_f_on_and_drawn_after():
    short, tl, frames, out, callouts = _fixture()
    sc = edit.build_edit(short, tl, frames, None, out, {}, preset="preview", callouts=callouts)
    with_before, with_after = _render_frame(sc, 2), _render_frame(sc, 8)  # (building another edit removes this one)
    base = edit.build_edit(short, tl, frames, None, out, {}, preset="preview")  # the same edit without the callout
    assert (with_before == _render_frame(base, 2)).all()  # nothing of it before f_on
    drawn = with_after & ~_render_frame(base, 8)  # what the callout adds at f_on + 4
    assert drawn.any()
    ys, xs = np.where(drawn)
    # the label is above the part and the arrow ends near it: pixels within 14 px above the part, none below it
    near = drawn[int(POINT[1]) - 14 : int(POINT[1]) - 4, int(POINT[0]) - 14 : int(POINT[0]) + 14]
    assert near.any() and ys.max() < POINT[1], (xs.min(), xs.max(), ys.min(), ys.max())


def test_arrow_dropped_where_the_part_is_off_screen():
    short, tl, frames, out, callouts = _fixture()
    callouts["anchors"]["a"]["0"]["points"][6] = None  # frame 7
    sc = edit.build_edit(short, tl, frames, None, out, {}, preset="preview", callouts=callouts)
    shaft, head = _strips(sc, "Callout_shaft")[0], _strips(sc, "Callout_head")[0]
    for strip in (shaft, head):
        assert _value(strip, "transform.scale_x", 7) == [0.0]  # no arrow on frame 7
        assert _value(strip, "transform.scale_x", 8)[0] > 0.0  # and back on the next
    assert _plate(sc).frame_final_start == 4  # the label is still there


def test_culprit_uses_highlight_colour_and_a_thicker_arrow():
    short, tl, frames, out, callouts = _fixture(culprit=True)
    plain_short, tl2, frames2, out2, callouts2 = _fixture(culprit=False)
    sc = edit.build_edit(short, tl, frames, None, out, {}, preset="preview", callouts=callouts)
    colour = tuple(_plate(sc).box_color)
    thick = _strips(sc, "Callout_shaft")[0].elements[0].orig_height
    want = hex_colour(caption_highlight(short, {}))
    assert tuple(round(c, 3) for c in colour[:3]) == tuple(round(c, 3) for c in want[:3])
    sc2 = edit.build_edit(plain_short, tl2, frames2, None, out2, {}, preset="preview", callouts=callouts2)
    assert tuple(_plate(sc2).box_color) != colour
    assert thick > _strips(sc2, "Callout_shaft")[0].elements[0].orig_height


def test_outline_style_draws_no_plate_and_an_edged_arrow():
    short, tl, frames, out, callouts = _fixture()
    short.style = {"caption": {"style": "outline"}}
    sc = edit.build_edit(short, tl, frames, None, out, {}, preset="preview", callouts=callouts)
    assert _plate(sc).use_box is False and _plate(sc).use_outline is True
    from bsa.callout_layout import OUTLINE_PX, SHAFT_PX

    assert _strips(sc, "Callout_shaft")[0].elements[0].orig_height == SHAFT_PX + 2 * OUTLINE_PX


def test_no_callouts_the_edit_is_unchanged():
    short, tl, frames, out, _callouts = _fixture()
    base = edit.build_edit(short, tl, frames, None, out, {}, preset="preview")
    names = sorted(s.name for s in base.sequence_editor.strips_all)
    assert not _strips(base, "Callout")
    for extra in (None, {}):
        sc = edit.build_edit(short, tl, frames, None, out, {}, preset="preview", callouts=extra)
        assert sorted(s.name for s in sc.sequence_editor.strips_all) == names


def test_callout_edit_encodes():
    short, tl, frames, out, callouts = _fixture()
    sc = edit.build_edit(short, tl, frames, None, out, {}, preset="preview", callouts=callouts)
    info = edit.probe(edit.encode(sc))
    assert info["frames"] == 30 and info["width"] == W and info["height"] == H
