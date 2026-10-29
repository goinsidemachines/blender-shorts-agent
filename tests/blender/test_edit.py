import tempfile
from pathlib import Path

import bpy
import numpy as np

from bsa.blender import edit
from bsa.layout import H, Rect, W, inside_safe, safe_rect
from bsa.short import Line, Short, Shot
from bsa.timeline import Card, Span, Timeline

BG = (0.2, 0.4, 0.8)


def _write_png(path: Path, color) -> None:
    img = bpy.data.images.new("tmp_color", W, H)
    img.pixels.foreach_set(np.tile([*color, 1.0], W * H).astype(np.float32))
    img.filepath_raw = str(path)
    img.file_format = "PNG"
    img.save()
    bpy.data.images.remove(img)


def _write_wav(path: Path, seconds=1.0, rate=48000) -> None:
    import wave

    t = np.arange(int(seconds * rate)) / rate
    data = (np.sin(2 * np.pi * 440 * t) * 0.3 * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(np.repeat(data, 2).tobytes())


def _fixture(badge="Motion exaggerated", labels=("Label A",), with_audio=True):
    """Two shots x 15 frames, two caption cards; returns (short, tl, frames_root, wav, out_mp4)."""
    bpy.ops.wm.read_factory_settings(use_empty=True)
    tmp = Path(tempfile.mkdtemp())
    frames = tmp / "frames"
    for shot, first in (("a", 1), ("b", 16)):  # frames carry the short's own frame numbers
        (frames / shot).mkdir(parents=True)
        for i in range(first, first + 15):
            _write_png(frames / shot / f"{i:04d}.png", BG)
    wav = tmp / "mix.wav"
    _write_wav(wav)
    short = Short(
        slug="t",
        title="t",
        length_s=1.0,
        shots=[
            Shot("a", "A", "p", "s", min_s=0.5, labels=list(labels), badge=badge),
            Shot("b", "B", "p", "s", min_s=0.5),
        ],
        lines=[Line("l1", "Hello there everybody", "a"), Line("l2", "A second caption card", "b")],
    )
    tl = Timeline(
        30,
        30,
        [Span("a", 1, 15), Span("b", 16, 30)],
        [Span("l1", 3, 12), Span("l2", 18, 28)],
        [Card("Hello there everybody", "l1", 3, 12), Card("A second caption card", "l2", 18, 28)],
    )
    return short, tl, frames, (wav if with_audio else None), tmp / "out.mp4"


def _texts(scene):
    return [s for s in scene.sequence_editor.strips_all if s.type == "TEXT"]


def test_edit_encodes_9x16_with_audio():
    short, tl, frames, wav, out = _fixture()
    sc = edit.build_edit(short, tl, frames, wav, out, {"handle": None}, preset="preview")
    assert sc.name == "Edit"
    assert sc.render.ffmpeg.constant_rate_factor == "MEDIUM"
    assert sc.render.ffmpeg.audio_codec == "AAC" and sc.render.ffmpeg.audio_bitrate == 256
    assert sc.render.ffmpeg.audio_mixrate == 48000 and sc.render.ffmpeg.audio_channels == "STEREO"
    mp4 = edit.encode(sc)
    assert mp4 == out and mp4.exists() and mp4.stat().st_size > 0
    info = edit.probe(mp4)
    assert info["width"] == 1080 and info["height"] == 1920
    assert round(info["fps"]) == 30
    assert info["frames"] == 30
    assert info["has_audio"] is True


def test_encode_without_audio_probes_no_audio():
    short, tl, frames, _wav, out = _fixture(badge=None, labels=())
    sc = edit.build_edit(short, tl, frames, None, out, {}, preset="preview")
    seen = []
    info = edit.probe(edit.encode(sc, progress=lambda done, total: seen.append((done, total))))
    assert info["frames"] == 30 and info["has_audio"] is False
    assert seen and seen[-1] == (30, 30) and all(t == 30 for _, t in seen)  # progress per frame of the encode


def test_final_preset_is_perceptually_lossless():
    short, tl, frames, wav, out = _fixture()
    sc = edit.build_edit(short, tl, frames, wav, out, {}, preset="final")
    assert sc.render.ffmpeg.constant_rate_factor == "PERC_LOSSLESS"
    assert sc.render.ffmpeg.codec == "H264" and sc.render.ffmpeg.format == "MPEG4"


def _render_overlay_bbox(scene, frame):
    """Render one frame of the edit; return the pixel bbox (x0, y0, x1, y1, top-left origin) of non-background."""
    r = scene.render
    saved = (
        getattr(r.image_settings, "media_type", None),
        r.image_settings.file_format,
        r.filepath,
        scene.frame_start,
        scene.frame_end,
    )
    out_dir = Path(tempfile.mkdtemp())
    try:
        if saved[0] is not None:
            r.image_settings.media_type = "IMAGE"
        r.image_settings.file_format = "PNG"
        r.filepath = str(out_dir / "f_")
        # an animation render of one frame: a still render would use the context scene's frame, not this scene's
        scene.frame_start = scene.frame_end = frame
        bpy.ops.render.render(animation=True, scene=scene.name)
    finally:
        if saved[0] is not None:
            r.image_settings.media_type = saved[0]
        r.image_settings.file_format, r.filepath = saved[1], saved[2]
        scene.frame_start, scene.frame_end = saved[3], saved[4]
    img = bpy.data.images.load(str(next(out_dir.glob("f_*.png"))))
    a = np.array(img.pixels[:], dtype=np.float32).reshape(H, W, 4)[::-1, :, :3]  # top-down
    bpy.data.images.remove(img)
    diff = np.abs(a - a[5, 5]).max(axis=2) > 0.03
    ys, xs = np.where(diff)
    if xs.size == 0:
        return None
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


def test_captions_inside_safe_area():
    short, tl, frames, wav, out = _fixture()
    sc = edit.build_edit(short, tl, frames, wav, out, {"handle": "@example_user"}, preset="preview")
    strips = _texts(sc)
    assert len(strips) >= 5  # two captions, a badge, a label, an end card
    for s in strips:
        rect = edit.text_strip_rect(s)
        assert inside_safe(rect), (s.text, rect)
    # a long caption that wraps to several lines must stay inside too
    tl.cards.append(Card(("word " * 40).strip(), "l1", 5, 6))
    sc2 = edit.build_edit(short, tl, frames, wav, out, {}, preset="preview")
    assert all(inside_safe(edit.text_strip_rect(s)) for s in _texts(sc2))
    # and the real render agrees: everything drawn over the frames sits inside the safe area
    tl.cards.pop()
    sc = edit.build_edit(short, tl, frames, wav, out, {"handle": "@example_user"}, preset="preview")
    s = safe_rect()
    for frame in (4, 20, 30):  # caption + badge + label, caption, end card
        bb = _render_overlay_bbox(sc, frame)
        assert bb is not None, frame
        x0, y0, x1, y1 = bb
        assert x0 >= s.x - 1 and y0 >= s.y - 1 and x1 <= s.right + 1 and y1 <= s.bottom + 1, (frame, bb)


def test_no_branding_without_handle():
    for handle in (None, ""):
        short, tl, frames, wav, out = _fixture()
        sc = edit.build_edit(short, tl, frames, wav, out, {"handle": handle}, preset="preview")
        for s in _texts(sc):
            low = s.text.lower()
            assert "@" not in low and "goinsidemachines" not in low and "follow" not in low, s.text
    short, tl, frames, wav, out = _fixture()
    sc = edit.build_edit(short, tl, frames, wav, out, {"handle": "@example_user"}, preset="preview")
    ends = [s for s in _texts(sc) if s.text == "@example_user"]
    assert len(ends) == 1 and ends[0].frame_final_end - 1 == tl.n_frames
    assert not any("goinsidemachines" in s.text.lower() for s in _texts(sc))


def test_badge_present_for_badged_shot():
    short, tl, frames, wav, out = _fixture()
    sc = edit.build_edit(short, tl, frames, wav, out, {}, preset="preview")
    badges = [s for s in _texts(sc) if s.text == "MOTION EXAGGERATED"]
    assert len(badges) == 1
    assert (badges[0].frame_final_start, badges[0].frame_final_end - 1) == (1, 15)
    # labels are shown for the whole shot, stacked below the badge
    labels = [s for s in _texts(sc) if s.text == "Label A"]
    assert len(labels) == 1 and (labels[0].frame_final_start, labels[0].frame_final_end - 1) == (1, 15)
    b, lab = edit.text_strip_rect(badges[0]), edit.text_strip_rect(labels[0])
    assert lab.y >= b.bottom


def test_shots_become_image_strips_in_order():
    short, tl, frames, wav, out = _fixture()
    sc = edit.build_edit(short, tl, frames, wav, out, {}, preset="preview")
    imgs = sorted((s for s in sc.sequence_editor.strips_all if s.type == "IMAGE"), key=lambda s: s.frame_final_start)
    assert [(s.frame_final_start, s.frame_final_end - 1) for s in imgs] == [(1, 15), (16, 30)]
    assert (sc.frame_start, sc.frame_end) == (1, 30)


def test_short_render_is_refused_not_padded():
    short, tl, frames, wav, out = _fixture()
    (frames / "b" / "0030.png").unlink()
    try:
        edit.build_edit(short, tl, frames, wav, out, {}, preset="preview")
    except FileNotFoundError as exc:
        assert "0030.png" in str(exc), exc
    else:
        raise AssertionError("a shot with a missing frame was padded with its last frame")


def _captions(scene):
    return [s for s in _texts(scene) if s.channel == edit.CH_CAPTION]


def test_font_family_names_match_files():
    import platform

    if platform.system() != "Windows":
        return
    assert edit.find_font_file("Segoe UI", [Path("C:/Windows/Fonts")]).name.lower() == "segoeui.ttf"
    assert edit.find_font_file("arial", [Path("C:/Windows/Fonts")]).name.lower() == "arial.ttf"
    assert edit.find_font_file("No Such Font Anywhere", [Path("C:/Windows/Fonts")]) is None


def test_font_found_in_the_per_user_font_folder():
    import os
    import platform
    import shutil

    if platform.system() != "Windows":
        return
    local = Path(tempfile.mkdtemp())
    fonts = local / "Microsoft" / "Windows" / "Fonts"
    fonts.mkdir(parents=True)
    shutil.copyfile("C:/Windows/Fonts/arial.ttf", fonts / "MyBrandSans-Regular.ttf")
    old = os.environ.get("LOCALAPPDATA")
    os.environ["LOCALAPPDATA"] = str(local)
    try:
        short, tl, frames, wav, out = _fixture()
        short.style["font"] = "My Brand Sans"
        sc = edit.build_edit(short, tl, frames, wav, out, {}, preset="preview")
    finally:
        if old is None:
            os.environ.pop("LOCALAPPDATA")
        else:
            os.environ["LOCALAPPDATA"] = old
    caption = _captions(sc)[0]
    assert caption.font is not None and Path(caption.font.filepath).name == "MyBrandSans-Regular.ttf"
    assert edit.font_note(sc) is None


def test_font_fallback_is_reported():
    short, tl, frames, wav, out = _fixture()
    short.style["font"] = "No Such Font Anywhere"
    sc = edit.build_edit(short, tl, frames, wav, out, {}, preset="preview")
    note = edit.font_note(sc)
    assert note and "No Such Font Anywhere" in note
    import platform

    if platform.system() == "Windows":  # the first installed OS fallback, not Blender's default
        assert edit.font_used(sc) == "Segoe UI" and "Segoe UI" in note
        assert Path(_captions(sc)[0].font.filepath).name.lower() == "segoeui.ttf"
    short.style["font"] = "Segoe UI"
    sc = edit.build_edit(short, tl, frames, wav, out, {}, preset="preview")
    assert edit.font_note(sc) is None


def test_brand_caption_colours():
    short, tl, frames, wav, out = _fixture()
    plain = _captions(edit.build_edit(short, tl, frames, wav, out, {}, preset="preview"))[0]
    assert tuple(plain.color) == (1, 1, 1, 1) and tuple(round(c, 3) for c in plain.box_color) == edit.BOX_COLOR
    brand = {"brand": {"mode": "brand", "caption": {"color": "#FFD000", "box": "#10203080"}}}
    cap = _captions(edit.build_edit(short, tl, frames, wav, out, brand, preset="preview"))[0]
    assert [round(c, 3) for c in cap.color] == [1.0, round(0xD0 / 255, 3), 0.0, 1.0]
    assert [round(c, 3) for c in cap.box_color] == [round(v / 255, 3) for v in (0x10, 0x20, 0x30, 0x80)]


def _end_strips(scene):
    return [s for s in _texts(scene) if s.channel >= edit.CH_END]


def test_end_card_follows_the_last_line_in_the_upper_third():
    short, tl, frames, wav, out = _fixture()
    sc = edit.build_edit(short, tl, frames, wav, out, {"handle": "@me", "end_card": "Follow for more"}, preset="preview")
    ends = {s.text: s for s in _end_strips(sc)}
    assert sorted(ends) == ["@me", "Follow for more"]
    last = max(c.f1 for c in tl.cards)
    for s in ends.values():
        assert (s.frame_final_start, s.frame_final_end - 1) == (last + 1, tl.n_frames), s.text
        for card in tl.cards:  # never over a caption
            assert s.frame_final_start > card.f1 or s.frame_final_end - 1 < card.f0, (s.text, card)
    line, handle = edit.text_strip_rect(ends["Follow for more"]), edit.text_strip_rect(ends["@me"])
    assert ends["Follow for more"].font_size < ends["@me"].font_size
    assert handle.y - line.bottom >= 16  # the line's box sits clearly above the handle's, no touching
    s = safe_rect()
    for r in (line, handle):
        assert inside_safe(r) and r.bottom <= s.y + s.h / 3, r  # the upper third, clear of the captions


def test_end_card_line_is_optional_and_defaults():
    short, tl, frames, wav, out = _fixture()
    sc = edit.build_edit(short, tl, frames, wav, out, {"handle": "me", "end_card": ""}, preset="preview")
    assert [s.text for s in _end_strips(sc)] == ["@me"]
    sc = edit.build_edit(short, tl, frames, wav, out, {"handle": "me"}, preset="preview")
    assert sorted(s.text for s in _end_strips(sc)) == ["@me", "Follow for more"]


def test_short_end_card_overrides_the_setting_with_a_second_line():
    short, tl, frames, wav, out = _fixture()
    short.end_card = {"text": "Play with it", "second": "example.com"}
    sc = edit.build_edit(short, tl, frames, wav, out, {"handle": "me", "end_card": "Follow for more"}, preset="preview")
    ends = {s.text: s for s in _end_strips(sc)}
    assert sorted(ends) == ["@me", "Play with it", "example.com"]
    top, handle, second = (edit.text_strip_rect(ends[k]) for k in ("Play with it", "@me", "example.com"))
    assert handle.y - top.bottom >= 16 and second.y - handle.bottom >= 16  # line, @handle, second line, no touching
    assert ends["example.com"].font_size < ends["@me"].font_size
    for r in (top, handle, second):
        assert inside_safe(r), r


def test_short_end_card_without_a_handle():
    short, tl, frames, wav, out = _fixture()
    short.end_card = {"text": "Play with it", "second": "example.com"}
    sc = edit.build_edit(short, tl, frames, wav, out, {"handle": ""}, preset="preview")
    ends = {s.text: s for s in _end_strips(sc)}
    assert sorted(ends) == ["Play with it", "example.com"]  # the line is the big one, the second below it
    assert ends["Play with it"].font_size > ends["example.com"].font_size
    assert edit.text_strip_rect(ends["example.com"]).y > edit.text_strip_rect(ends["Play with it"]).bottom
    short.end_card = "Just this"
    sc = edit.build_edit(short, tl, frames, wav, out, {"handle": "me", "end_card": "Follow for more"}, preset="preview")
    assert sorted(s.text for s in _end_strips(sc)) == ["@me", "Just this"]


def _location_curve(scene, strip):
    from bsa.blender.bake import _fcurves

    path = strip.path_from_id("location")
    return {fc.array_index: fc for fc in _fcurves(scene.animation_data.action) if fc.data_path == path}


def test_near_label_follows_its_object():
    from bsa.layout import place_label

    short, tl, frames, wav, out = _fixture(badge=None, labels=())
    short.shots[0].labels = [{"text": "Ball", "near": "Ball"}]
    pts = [[200 + 30 * i, 700] for i in range(15)]
    pts[0] = [0, 700]  # at the left edge: clamped inside the safe area
    pts[5] = [600, 1400]  # where a caption is on screen (frames 3-12): pushed above it
    pts[13] = None  # behind the camera: keeps the last place
    anchors = {"a": {"Ball": {"f0": 1, "points": pts}}}
    sc = edit.build_edit(short, tl, frames, wav, out, {}, preset="preview", anchors=anchors)
    strip = next(s for s in _texts(sc) if s.text == "Ball")
    assert (strip.frame_final_start, strip.frame_final_end - 1) == (1, 15)
    curves = _location_curve(sc, strip)
    xs = [curves[0].evaluate(f) * W for f in range(1, 16)]
    bottoms = [(1 - curves[1].evaluate(f)) * H for f in range(1, 16)]
    w = edit._est_width("Ball", edit.LABEL_SIZE, edit.NEAR_WRAP_PX)
    m = edit.SMALL_MARGIN * H
    assert abs(xs[0] - (safe_rect().x + w / 2 + m + 1)) < 0.5  # clamped
    for f in (2, 3, 4, 7):
        x, b = place_label(tuple(pts[f - 1]), (w, edit.LABEL_SIZE * edit.LINE_H), m, edit.NEAR_GAP_PX)
        assert abs(xs[f - 1] - x) < 0.5 and abs(bottoms[f - 1] - b) < 0.5, f
    assert xs[6] > xs[2]  # it moves with the object
    cap_top = min(edit.text_strip_rect(s).y for s in _captions(sc) if s.frame_final_start <= 6 < s.frame_final_end)
    assert bottoms[5] + m <= cap_top + 0.5  # never under the caption
    assert abs(xs[13] - xs[12]) < 0.5  # behind the camera: stays put
    for f in range(1, 16):
        r = Rect(xs[f - 1] - w / 2 - m, bottoms[f - 1] - edit.LABEL_SIZE * edit.LINE_H - m, w + 2 * m,
                 edit.LABEL_SIZE * edit.LINE_H + 2 * m)  # fmt: skip
        assert inside_safe(r), (f, r)


def _label_rects(scene, strip):
    """The near label's box at every frame of shot a (1-15), from its location keyframes."""
    curves = _location_curve(scene, strip)
    w = edit._est_width(strip.text, edit.LABEL_SIZE, edit.NEAR_WRAP_PX)
    h = edit._est_lines(strip.text, edit.LABEL_SIZE, edit.NEAR_WRAP_PX) * edit.LABEL_SIZE * edit.LINE_H
    m = edit.SMALL_MARGIN * H
    out = {}
    for f in range(1, 16):
        x, bottom = curves[0].evaluate(f) * W, (1 - curves[1].evaluate(f)) * H
        out[f] = Rect(x - w / 2 - m, bottom - h - m, w + 2 * m, h + 2 * m)
    return out


def test_near_labels_avoid_the_badge_stacked_labels_and_each_other():
    """A near object high in the frame never puts its label on the badge or a stacked label, and two near labels on
    neighbouring objects ("No damper", "With damper") never touch."""
    from bsa.layout import overlap_area

    short, tl, frames, wav, out = _fixture(badge="Motion exaggerated", labels=())
    s = safe_rect()
    short.shots[0].labels = ["Stacked", {"text": "No damper", "near": "A"}, {"text": "With damper", "near": "B"}]
    top_left = [[s.x + 120, s.y + 60]] * 15  # on the badge and the stacked label
    beside = [[s.x + 180, s.y + 80]] * 15  # right next to the first
    anchors = {"a": {"A": {"f0": 1, "points": top_left}, "B": {"f0": 1, "points": beside}}}
    sc = edit.build_edit(short, tl, frames, wav, out, {}, preset="preview", anchors=anchors)
    texts = _texts(sc)
    badge = edit.text_strip_rect(next(t for t in texts if t.text == "MOTION EXAGGERATED"))
    stacked = edit.text_strip_rect(next(t for t in texts if t.text == "Stacked"))
    a = _label_rects(sc, next(t for t in texts if t.text == "No damper"))
    b = _label_rects(sc, next(t for t in texts if t.text == "With damper"))
    for f in range(1, 16):
        for name, r in (("No damper", a[f]), ("With damper", b[f])):
            assert overlap_area(r, badge) == 0, (f, name, r, badge)
            assert overlap_area(r, stacked) == 0, (f, name, r, stacked)
            assert inside_safe(r), (f, name, r)
        assert overlap_area(a[f], b[f]) == 0, (f, a[f], b[f])


def test_near_label_without_anchors_is_refused():
    short, tl, frames, wav, out = _fixture(badge=None, labels=())
    short.shots[0].labels = [{"text": "Ball", "near": "Ball"}]
    try:
        edit.build_edit(short, tl, frames, wav, out, {}, preset="preview", anchors={})
    except ValueError as exc:
        assert "run BLENDER anchors SHORT" in str(exc)
    else:
        raise AssertionError("a near label without anchors was placed anyway")


def _end_block(scene):
    rects = [edit.text_strip_rect(s) for s in _end_strips(scene)]
    return min(r.y for r in rects), max(r.bottom for r in rects)


def test_end_card_moves_off_a_hero():
    short, tl, frames, wav, out = _fixture()
    s = safe_rect()
    settings = {"handle": "me"}
    sc = edit.build_edit(short, tl, frames, wav, out, settings, preset="preview", hero_boxes={"f0": 16, "boxes": {}})
    top, bottom = _end_block(sc)
    assert bottom <= s.y + s.h / 3 and edit.end_card_note(sc) is None  # no heroes: the upper third
    upper = {"f0": 16, "boxes": {"Short bob": [[400, top, 700, bottom]] * 15}}  # a hero right where the card sits
    sc = edit.build_edit(short, tl, frames, wav, out, settings, preset="preview", hero_boxes=upper)
    top2, bottom2 = _end_block(sc)
    assert top2 > bottom and abs(bottom2 - (s.bottom - 1)) < 1  # moved to the caption zone, down to the safe bottom
    assert edit.end_card_note(sc) is None
    assert all(inside_safe(edit.text_strip_rect(x)) for x in _end_strips(sc))
    mid = s.y + s.h / 2
    upper_and_middle = {"f0": 16, "boxes": {"A": [[400, top, 700, bottom]] * 15,
                                            "B": [[400, mid - 150, 700, mid + 150]] * 15}}  # fmt: skip
    sc = edit.build_edit(short, tl, frames, wav, out, settings, preset="preview", hero_boxes=upper_and_middle)
    _, bottom3 = _end_block(sc)
    assert abs(bottom3 - (s.bottom - 1)) < 1 and edit.end_card_note(sc) is None  # caption zone
    upper_and_captions = {"f0": 16, "boxes": {"A": [[400, top, 700, bottom]] * 15,
                                              "C": [[400, bottom3 - 100, 700, bottom3]] * 15}}  # fmt: skip
    sc = edit.build_edit(short, tl, frames, wav, out, settings, preset="preview", hero_boxes=upper_and_captions)
    top4, bottom4 = _end_block(sc)
    assert abs((top4 + bottom4) / 2 - mid) < 1 and edit.end_card_note(sc) is None  # then the middle
    assert all(inside_safe(edit.text_strip_rect(x)) for x in _end_strips(sc))
    last_card = max(c.f1 for c in tl.cards)
    assert all(x.frame_final_start > last_card for x in _end_strips(sc))  # no caption shares its frames
    everywhere = {"f0": 16, "boxes": {"Big": [[s.x, s.y, s.right, s.bottom]] * 15}}
    sc = edit.build_edit(short, tl, frames, wav, out, settings, preset="preview", hero_boxes=everywhere)
    assert "covers a hero" in edit.end_card_note(sc)


def test_near_labels_keep_off_the_other_heroes():
    """A label near a string must not sit on the ball row (other heroes); a hero box holding the anchor (the
    assembly the part belongs to) and the label's own object are not in the way."""
    from bsa.layout import overlap_area

    short, tl, frames, wav, out = _fixture(badge=None, labels=())
    short.shots[0].labels = [{"text": "String", "near": "String"}]
    anchors = {"a": {"String": {"f0": 1, "points": [[540, 900]] * 15}}}
    row = [200, 760, 880, 880]  # the ball row just above the string's top
    heroes = {"a": {"f0": 1, "boxes": {"Balls": [row] * 15, "String": [[530, 900, 550, 1100]] * 15,
                                      "Cradle": [[100, 500, 980, 1300]] * 15}}}  # fmt: skip
    sc = edit.build_edit(short, tl, frames, wav, out, {}, preset="preview", anchors=anchors, label_heroes=heroes)
    rects = _label_rects(sc, next(t for t in _texts(sc) if t.text == "String"))
    r0 = Rect(row[0], row[1], row[2] - row[0], row[3] - row[1])
    for f in range(1, 16):
        assert overlap_area(rects[f], r0) == 0, (f, rects[f])
        assert inside_safe(rects[f])
    plain = edit.build_edit(short, tl, frames, wav, out, {}, preset="preview", anchors=anchors)
    assert _label_rects(plain, next(t for t in _texts(plain) if t.text == "String"))[1] != rects[1]


def test_end_card_keeps_off_other_meshes_after_heroes():
    short, tl, frames, wav, out = _fixture()
    s = safe_rect()
    settings = {"handle": "me"}
    sc = edit.build_edit(short, tl, frames, wav, out, settings, preset="preview", hero_boxes={"f0": 16, "boxes": {}})
    top, bottom = _end_block(sc)
    rails = {"f0": 16, "boxes": {}, "scenery": {"f0": 16, "boxes": [[[150, top, 930, bottom]]] * 15}}
    sc = edit.build_edit(short, tl, frames, wav, out, settings, preset="preview", hero_boxes=rails)
    top2, bottom2 = _end_block(sc)
    assert top2 > bottom and abs(bottom2 - (s.bottom - 1)) < 1 and edit.end_card_note(sc) is None
    hero_low = {"f0": 16, "boxes": {"Wheel": [[400, top2, 700, bottom2]] * 15},
                "scenery": {"f0": 16, "boxes": [[[150, top, 930, bottom]]] * 15}}  # fmt: skip
    sc = edit.build_edit(short, tl, frames, wav, out, settings, preset="preview", hero_boxes=hero_low)
    top3, bottom3 = _end_block(sc)
    assert abs((top3 + bottom3) / 2 - (s.y + s.h / 2)) < 1  # the middle: off the hero and the rails
