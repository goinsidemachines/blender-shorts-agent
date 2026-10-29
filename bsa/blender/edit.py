"""The edit: frames, captions, badges, labels, sound and the H.264 + AAC MP4 encode, all in Blender's sequencer.

Text strip geometry: the VSE places text by a normalised anchor (0-1, origin bottom-left), the box grows
`box_margin * render height` pixels around the text on every side, and the text wraps at `wrap_width * width`.
`text_strip_rect` turns a strip's real settings back into a pixel Rect (origin top-left) so callers can check it.
"""

from __future__ import annotations

import math
import re
import sys
from pathlib import Path

import bpy

from bsa.fonts import (  # noqa: F401 - find_font_file, font_dirs re-exported
    cover_font,
    family_of,
    find_font_file,
    font_dirs,
    font_files,
    font_info_or_none,
    missing_chars,
    resolve,
)
from bsa.layout import H, Rect, W, caption_rect, end_card_band, place_label, safe_rect
from bsa.settings import end_card
from bsa.short import Short, label_near, label_text
from bsa.timeline import Timeline, end_card_start

CAPTION_SIZE = 64
BADGE_SIZE = 30
LABEL_SIZE = 40
END_SIZE = 72
END_LINE_SIZE = 44
LINE_H = 1.25  # same line height as layout.caption_rect
CHAR_W = 0.56  # average glyph advance in em, for estimating wrapping
CAPTION_MARGIN = 0.018  # box margin as a fraction of the render height
SMALL_MARGIN = 0.010
STACK_GAP_PX = 12
END_GAP_PX = 24  # clear space between the end-card line's box and the @handle box
NEAR_GAP_PX = 16  # a near label's box sits this far above its object's top
NEAR_WRAP_PX = 420  # near labels wrap narrower than the safe area
BOX_COLOR = (0.02, 0.025, 0.03, 0.55)
BADGE_COLOR = (1.0, 0.8, 0.35, 1.0)

CH_SOUND, CH_CAPTION, CH_BADGE, CH_LABEL, CH_END = 2, 10, 11, 12, 30

FONT_NOTE_PROP = "bsa_font_fallback"
FONT_USED_PROP = "bsa_font_used"
END_NOTE_PROP = "bsa_end_card_note"
GLYPH_NOTE_PROP = "bsa_glyph_fallbacks"


def _est_lines(text: str, size_px: float, wrap_px: float) -> int:
    per_line = max(1, int(wrap_px / (size_px * CHAR_W)))
    return max(1, sum(max(1, math.ceil(len(part) / per_line)) for part in text.split("\n")))


def _est_width(text: str, size_px: float, wrap_px: float) -> float:
    longest = max(len(part) for part in text.split("\n"))
    return min(wrap_px, longest * size_px * CHAR_W)


def text_strip_rect(strip) -> Rect:
    """Estimated pixel rectangle (origin top-left) a TEXT strip, including its box, covers."""
    size = strip.font_size
    wrap_px = (strip.wrap_width or 1.0) * W
    n = _est_lines(strip.text, size, wrap_px)
    w, h = _est_width(strip.text, size, wrap_px), n * size * LINE_H
    margin = strip.box_margin * H if strip.use_box else 0.0
    ax, ay = strip.location[0] * W, (1.0 - strip.location[1]) * H
    x = {"LEFT": ax, "CENTER": ax - w / 2, "RIGHT": ax - w}[strip.anchor_x]
    y = {"TOP": ay, "CENTER": ay - h / 2, "BOTTOM": ay - h}[strip.anchor_y]
    return Rect(x - margin, y - margin, w + 2 * margin, h + 2 * margin)


def _load_font(name: str | None):
    """(font datablock or None, family used or None, note): the style font from a file path or a family name in the
    OS font folders, else the first installed OS fallback (bsa.fonts.FALLBACKS), else Blender's default font. The
    note says which font is used when the named one is missing."""
    if not name:
        return None, None, None
    path, used, note = resolve(name)
    if path is not None:
        try:
            return bpy.data.fonts.load(str(path), check_existing=True), used, note
        except RuntimeError:
            pass
    return None, None, f"font {name!r} could not be loaded, so the text uses Blender's default font"


def font_used(scene) -> str:
    """The font family the edit's text uses ("Blender's default font" when none was found or named)."""
    return scene.get(FONT_USED_PROP) or "Blender's default font"


def font_note(scene) -> str | None:
    """Why the edit fell back to Blender's default font, or None."""
    return scene.get(FONT_NOTE_PROP) or None


def _hex_colour(value, alpha: float):
    """'#RRGGBB' or '#RRGGBBAA' as an RGBA tuple of 0..1 floats; None when it is not one."""
    text = str(value or "").strip().lstrip("#")
    if not re.fullmatch(r"[0-9a-fA-F]{6}([0-9a-fA-F]{2})?", text):
        return None
    rgba = [int(text[i : i + 2], 16) / 255 for i in range(0, len(text), 2)]
    return tuple(rgba) if len(rgba) == 4 else (*rgba, alpha)


def caption_colours(short: Short, settings: dict) -> tuple[tuple, tuple]:
    """(text, box) colours of the captions: `brand.caption` `color` and `box` from settings (not for a short with its
    own look, "look": "fresh"), overridden by the short's own `style.caption`; white text on the dark box when
    neither sets them."""
    from bsa.settings import brand_for

    cap = {}
    for src in (brand_for(settings, short).get("caption"), short.style.get("caption")):
        if isinstance(src, dict):
            cap.update(src)
    text = _hex_colour(cap.get("color") or cap.get("colour"), 1.0) or (1.0, 1.0, 1.0, 1.0)
    box = _hex_colour(cap.get("box") or cap.get("box_color") or cap.get("box_colour"), BOX_COLOR[3]) or BOX_COLOR
    return text, box


def glyph_fallbacks(scene) -> list[str]:
    """Texts the style font has no glyphs for, and the font each was drawn in instead (or that none had them)."""
    return [str(x) for x in scene.get(GLYPH_NOTE_PROP, [])]


class _Texts:
    """Adds styled text strips to the sequencer. A text the style font can't fully draw goes in a font that has
    every glyph it needs (bsa.fonts.cover_font: the OS fallbacks, then symbol fonts) instead of showing boxes: a
    text strip has one font, so the fallback covers that strip's whole text."""

    def __init__(self, strips, font, font_path=None):
        self.strips = strips
        self.font = font
        self.font_path = font_path
        self.notes: list[str] = []
        self._files = None

    def _font_for(self, text: str):
        info = font_info_or_none(self.font_path) if self.font_path else None
        miss = missing_chars(text, info) if info else []
        if not miss:
            return self.font
        chars = ", ".join(f"'{c}' (U+{ord(c):04X})" for c in miss)
        if self._files is None:  # list the OS fonts once per edit
            self._files = font_files(font_dirs())
        alt = cover_font(text, self.font_path, files=self._files)
        if alt is not None:
            try:
                font = bpy.data.fonts.load(str(alt), check_existing=True)
                self.notes.append(f"{text!r}: {info['family']} has no {chars}, so this text uses {family_of(alt)}")
                return font
            except RuntimeError:
                pass
        self.notes.append(f"{text!r}: no installed font has {chars}; they show as boxes")
        return self.font

    def add(self, text, f0, f1, channel, size, loc, anchor, margin, color=(1, 1, 1, 1), bold=True, wrap_px=None,
            box=BOX_COLOR):
        s = self.strips.new_effect(f"T_{text[:18]}", "TEXT", channel, f0, length=f1 - f0 + 1)
        s.text = text
        font = self._font_for(text)
        if font is not None:
            s.font = font
        s.font_size = size
        s.use_bold = bold
        s.color = color
        s.location = loc
        s.anchor_x, s.anchor_y = anchor
        s.alignment_x = anchor[0]
        s.wrap_width = (wrap_px if wrap_px is not None else safe_rect().w - 2 * margin * H) / W
        s.use_shadow = False  # the box gives the contrast
        s.use_box = True
        s.box_color = box
        s.box_margin = margin
        s.box_roundness = 0.4
        s.blend_type = "ALPHA_OVER"
        return s


def _norm(x_px: float, y_px_from_top: float) -> tuple[float, float]:
    return x_px / W, 1.0 - y_px_from_top / H


def _add_shots(short: Short, tl: Timeline, frames_root: Path, strips) -> None:
    spans = {s.id: s for s in tl.shots}
    for shot in short.shots:
        span = spans[shot.id]
        folder = Path(frames_root) / shot.id
        names = [f"{f:04d}.png" for f in range(span.f0, span.f1 + 1)]  # the short's own frame numbers, never padded
        missing = [n for n in names if not (folder / n).is_file() or (folder / n).stat().st_size == 0]
        if missing:
            raise FileNotFoundError(f"shot {shot.id!r} is missing rendered frames in {folder}: {', '.join(missing[:5])}")
        s = strips.new_image(f"Shot {shot.id}", str(folder / names[0]), 1, span.f0, fit_method="FIT")
        for name in names[1:]:
            s.elements.append(name)


def _follow(strip, points: dict, span, captions, fixed=(), placed: dict | None = None, heroes: dict | None = None,
            near: str | None = None) -> None:  # fmt: skip
    """Keyframe a near label's position every frame of its shot: just above its object's top on screen (from
    anchors), inside the safe area and never on a caption shown at that frame, the shot's badge and stacked labels
    (`fixed`) or the near labels already placed in that frame (`placed`: {frame: [Rect]}, which this label's own box
    is added to). It keeps off the shot's heroes too where it can (`heroes`: the shot's {"f0", "boxes"} from
    anchors.json), except its own object `near` and a hero whose box holds the anchor (the assembly the part
    belongs to). Frames where the object is behind the camera keep the last position."""
    w = _est_width(strip.text, LABEL_SIZE, NEAR_WRAP_PX)
    h = _est_lines(strip.text, LABEL_SIZE, NEAR_WRAP_PX) * LABEL_SIZE * LINE_H
    m = SMALL_MARGIN * H
    pts = list(points.get("points") or [])
    f0 = int(points.get("f0", span.f0))
    first = next((p for p in pts if p is not None), None)
    last = first or [safe_rect().x + safe_rect().w / 2, safe_rect().y]
    placed = {} if placed is None else placed
    for frame in range(span.f0, span.f1 + 1):
        i = frame - f0
        p = pts[i] if 0 <= i < len(pts) and pts[i] is not None else last
        last = p
        avoid = [r for c0, c1, r in captions if c0 <= frame <= c1] + list(fixed) + placed.get(frame, [])
        prefer = _hero_rects(heroes, frame, skip=near, anchor=p)
        x, bottom = place_label((p[0], p[1]), (w, h), m, NEAR_GAP_PX, avoid, prefer)
        placed.setdefault(frame, []).append(Rect(x - w / 2 - m, bottom - h - m, w + 2 * m, h + 2 * m))
        strip.location = _norm(x, bottom)
        strip.keyframe_insert("location", frame=frame)


def _hero_rects(heroes: dict | None, frame: int, skip: str | None = None, anchor=None) -> list[Rect]:
    """The hero boxes on screen at `frame` from a shot's {"f0", "boxes": {name: [[x0, y0, x1, y1] or None, ...]}},
    leaving out `skip` and any box that holds the point `anchor`."""
    out = []
    if not isinstance(heroes, dict) or not isinstance(heroes.get("boxes"), dict):
        return out
    i = frame - int(heroes.get("f0", 1))
    for name, per_frame in heroes["boxes"].items():
        b = per_frame[i] if name != skip and isinstance(per_frame, list) and 0 <= i < len(per_frame) else None
        if not b or (anchor is not None and b[0] <= anchor[0] <= b[2] and b[1] <= anchor[1] <= b[3]):
            continue
        out.append(Rect(b[0], b[1], b[2] - b[0], b[3] - b[1]))
    return out


def _add_overlays(short: Short, tl: Timeline, texts: _Texts, settings: dict, anchors: dict | None = None,
                  label_heroes: dict | None = None) -> None:  # fmt: skip
    safe = safe_rect()
    size = int((short.style.get("caption") or {}).get("size") or CAPTION_SIZE)
    wrap_px = safe.w - 2 * CAPTION_MARGIN * H
    cx = safe.x + safe.w / 2
    colour, box = caption_colours(short, settings)
    captions = []  # (f0, f1, caption box) for keeping near labels off the captions
    for card in tl.cards:
        rect = caption_rect(_est_lines(card.text, size, wrap_px), size, LINE_H)
        pad = CAPTION_MARGIN * H
        captions.append((card.f0, card.f1, Rect(rect.x - pad, rect.y - pad, rect.w + 2 * pad, rect.h + 2 * pad)))
        texts.add(
            card.text, card.f0, card.f1, CH_CAPTION, size, _norm(cx, rect.bottom), ("CENTER", "BOTTOM"), CAPTION_MARGIN,
            color=colour, box=box,
        )  # fmt: skip

    spans = {s.id: s for s in tl.shots}
    left = safe.x + SMALL_MARGIN * H + 1  # +1 px keeps float noise off the safe edge
    for shot in short.shots:
        span = spans[shot.id]
        y = safe.y + SMALL_MARGIN * H + 1
        fixed = []  # the badge and the stacked labels: near labels keep off them
        if shot.badge:
            strip = texts.add(
                shot.badge.upper(), span.f0, span.f1, CH_BADGE, BADGE_SIZE, _norm(left, y), ("LEFT", "TOP"),
                SMALL_MARGIN, color=BADGE_COLOR,
            )  # fmt: skip
            fixed.append(text_strip_rect(strip))
            y += BADGE_SIZE * LINE_H + 2 * SMALL_MARGIN * H + STACK_GAP_PX
        near_labels = []
        for i, label in enumerate(shot.labels):
            text, near = label_text(label), label_near(label)
            if near:
                points = ((anchors or {}).get(shot.id) or {}).get(near)
                if points is None:
                    raise ValueError(f"no anchors for label {text!r} near {near!r} in shot {shot.id}: run BLENDER "
                                     "anchors SHORT")  # fmt: skip
                near_labels.append((i, text, points, near))
                continue
            strip = texts.add(
                text, span.f0, span.f1, CH_LABEL + i, LABEL_SIZE, _norm(left, y), ("LEFT", "TOP"), SMALL_MARGIN,
                bold=False,
            )  # fmt: skip
            fixed.append(text_strip_rect(strip))
            n = _est_lines(text, LABEL_SIZE, safe.w - 2 * SMALL_MARGIN * H)
            y += n * LABEL_SIZE * LINE_H + 2 * SMALL_MARGIN * H + STACK_GAP_PX
        placed = {}  # {frame: [near label boxes]}: each near label keeps off the ones placed before it
        for i, text, points, near in near_labels:
            strip = texts.add(text, span.f0, span.f1, CH_LABEL + i, LABEL_SIZE, (0.5, 0.5), ("CENTER", "BOTTOM"),
                              SMALL_MARGIN, bold=False, wrap_px=NEAR_WRAP_PX)  # fmt: skip
            _follow(strip, points, span, captions, fixed, placed, (label_heroes or {}).get(shot.id), near)


def _add_end_card(tl: Timeline, texts: _Texts, settings: dict, heroes: dict | None = None, short=None) -> str | None:
    """The end card: the user's @handle (the only identity there ever is), with the end-card line smaller above it
    and an optional second line (the user's site, say) smaller below it, a clear gap between the boxes; without a
    handle the short's own line is the big one. The short's own `end_card` wins over the setting
    (bsa.settings.end_card). From the frame after the last caption to the end (the timeline holds the last shot
    END_CARD_S for it). It goes in the upper third of the safe area, else the caption zone, else the middle, else
    low above the caption zone: the first band that no hero of the last shot and no other mesh on screen covers
    during those frames, else the first no hero covers (the least other mesh), else the least hero (`heroes`: that
    shot's {"f0", "boxes", "scenery"} from anchors.json; "scenery" is {"f0", "boxes": [[box, ...] per frame]}, the
    other meshes). Returns a note when every band covers a hero (the least covered one is used)."""
    card = end_card(settings, short)
    if card["handle"]:
        rows = [(card["text"], False), ("@" + card["handle"], True), (card["second"], False)]
    else:
        rows = [(card["text"], True), (card["second"], False)]
    rows = [(text, big) for text, big in rows if text]
    if not rows:
        return None
    f0 = end_card_start(tl)
    if f0 > tl.n_frames:  # no room after the last line: never cover it
        return None
    safe = safe_rect()
    cx = safe.x + safe.w / 2
    big_i = next(i for i, (_, big) in enumerate(rows) if big) if any(big for _, big in rows) else 0
    strips = [None] * len(rows)

    def add(i, bottom):
        text, big = rows[i]
        size, margin = (END_SIZE, CAPTION_MARGIN) if big else (END_LINE_SIZE, SMALL_MARGIN)
        strips[i] = texts.add(text, f0, tl.n_frames, CH_END + i, size, _norm(cx, bottom - margin * H),
                              ("CENTER", "BOTTOM"), margin, bold=big)  # fmt: skip

    add(big_i, safe.y + safe.h / 3 - 1)  # the main box ends at the upper third
    for i in range(big_i - 1, -1, -1):  # lines above it
        add(i, text_strip_rect(strips[i + 1]).y - END_GAP_PX)
    for i in range(big_i + 1, len(rows)):  # lines below it: their box's top END_GAP_PX under the one above
        text, big = rows[i]
        size, margin = (END_SIZE, CAPTION_MARGIN) if big else (END_LINE_SIZE, SMALL_MARGIN)
        h = _est_lines(text, size, safe.w - 2 * margin * H) * size * LINE_H + 2 * margin * H
        add(i, text_strip_rect(strips[i - 1]).bottom + END_GAP_PX + h)
    rects = [text_strip_rect(s) for s in strips]
    x0, y0 = min(r.x for r in rects), min(r.y for r in rects)
    block = Rect(x0, y0, max(r.right for r in rects) - x0, max(r.bottom for r in rects) - y0)
    boxes = [r for frame in range(f0, tl.n_frames + 1) for r in _hero_rects(heroes, frame)]
    others = []
    scenery = heroes.get("scenery") if isinstance(heroes, dict) else None
    if isinstance(scenery, dict) and isinstance(scenery.get("boxes"), list):
        s0 = int(scenery.get("f0", 1))
        for frame in range(f0, tl.n_frames + 1):
            if 0 <= frame - s0 < len(scenery["boxes"]):
                others += [Rect(b[0], b[1], b[2] - b[0], b[3] - b[1]) for b in scenery["boxes"][frame - s0] or []]
    caption_top = caption_rect(2, CAPTION_SIZE, LINE_H).y - CAPTION_MARGIN * H
    band, dy, area = end_card_band(block, boxes, caption_top, others=others)
    for s in strips:
        s.location[1] -= dy / H
    if area:
        return f"the end card covers a hero in every position; it uses the {band}, which covers the least"
    return None


def end_card_note(scene) -> str | None:
    """Why the end card could not be kept clear of the heroes, or None."""
    return scene.get(END_NOTE_PROP) or None


def _configure_output(scene, out_mp4: Path, preset: str) -> None:
    r = scene.render
    r.resolution_x, r.resolution_y, r.resolution_percentage = W, H, 100
    r.fps, r.fps_base = 30, 1.0
    r.use_sequencer = True
    r.use_compositing = False
    scene.view_settings.view_transform = "Standard"  # frames are already tone-mapped by their own scenes
    scene.view_settings.look = "None"
    if hasattr(r.image_settings, "media_type"):
        r.image_settings.media_type = "VIDEO"
    r.image_settings.file_format = "FFMPEG"
    r.ffmpeg.format, r.ffmpeg.codec = "MPEG4", "H264"
    r.ffmpeg.constant_rate_factor = "PERC_LOSSLESS" if preset == "final" else "MEDIUM"
    r.ffmpeg.ffmpeg_preset = "GOOD"
    r.ffmpeg.audio_codec, r.ffmpeg.audio_bitrate = "AAC", 256
    r.ffmpeg.audio_channels, r.ffmpeg.audio_mixrate = "STEREO", 48000
    r.filepath = str(out_mp4)


def build_edit(
    short: Short,
    tl: Timeline,
    frames_root: Path,
    mix_wav: Path | None,
    out_mp4: Path,
    settings: dict,
    preset: str = "final",
    anchors: dict | None = None,
    hero_boxes: dict | None = None,
    label_heroes: dict | None = None,
    closing: dict | None = None,
):
    """Create the `Edit` scene: shot frames, captions, badges, labels, optional @handle end card, the closing card
    and sound. `closing` ({"png", "f0", "n_frames"}, bsa.closing.plan) holds the closing card's PNG after the story,
    from frame f0 (the frame after the timeline) for n_frames; the mix already covers them. `anchors`
    ({shot: {object: {"f0", "points"}}}, from source/anchors.json) places labels that are near an object; `hero_boxes`
    (the last shot's {"f0", "boxes", "scenery"} from anchors.json) keeps the end card off its heroes, then off its
    other meshes; `label_heroes` ({shot: {"f0", "boxes"}}) keeps near labels off the other heroes of their shot."""
    old = bpy.data.scenes.get("Edit")
    if old is not None:
        bpy.data.scenes.remove(old)
    scene = bpy.data.scenes.new("Edit")
    scene.frame_start, scene.frame_end = 1, tl.n_frames + (closing["n_frames"] if closing else 0)
    _configure_output(scene, Path(out_mp4), preset)  # first: the image strips fit to this resolution
    seq = scene.sequence_editor_create()
    _add_shots(short, tl, Path(frames_root), seq.strips)
    if closing:
        card = seq.strips.new_image("Closing card", str(closing["png"]), 1, closing["f0"], fit_method="FIT")
        card.frame_final_duration = closing["n_frames"]
    font, used, note = _load_font(short.style.get("font"))
    if note:
        scene[FONT_NOTE_PROP] = note
    if used:
        scene[FONT_USED_PROP] = used
    texts = _Texts(seq.strips, font, Path(bpy.path.abspath(font.filepath)) if font is not None else None)
    _add_overlays(short, tl, texts, settings, anchors, label_heroes)
    note = _add_end_card(tl, texts, settings, hero_boxes, short)
    if note:
        scene[END_NOTE_PROP] = note
    if texts.notes:
        scene[GLYPH_NOTE_PROP] = list(dict.fromkeys(texts.notes))
    if mix_wav is not None:
        seq.strips.new_sound("Mix", str(mix_wav), CH_SOUND, 1)
    if mix_wav is None:  # captions-only short: no audio stream at all, not a silent one
        scene.render.ffmpeg.audio_codec = "NONE"
    return scene


def encode(scene, progress=None) -> Path:
    """Render the edit's animation to its MP4 and return the path. `progress(frames done, frames)` is called after
    each frame when given (a failing callback never stops the encode)."""
    total = scene.frame_end - scene.frame_start + 1
    done = [0]

    def _post(sc, *_):
        if sc is not scene:
            return
        done[0] += 1
        try:
            progress(min(done[0], total), total)
        except Exception as exc:  # noqa: BLE001 - status only
            print(f"encode progress: {exc}", file=sys.stderr)

    if progress is not None:
        bpy.app.handlers.render_post.append(_post)
    try:
        bpy.ops.render.render(animation=True, scene=scene.name)
    finally:
        if _post in bpy.app.handlers.render_post:
            bpy.app.handlers.render_post.remove(_post)
    out = Path(bpy.path.abspath(scene.render.filepath))
    if not out.exists() or out.stat().st_size == 0:
        raise RuntimeError(f"encode produced no MP4 at {out}")
    return out


def probe(mp4: Path) -> dict:
    """width, height, fps, frames and has_audio of an MP4, read through Blender (no ffprobe)."""
    clip = bpy.data.movieclips.load(str(mp4))
    try:
        info = {
            "width": int(clip.size[0]),
            "height": int(clip.size[1]),
            "fps": float(clip.fps),
            "frames": int(clip.frame_duration),
        }
    finally:
        bpy.data.movieclips.remove(clip)
    info["has_audio"] = _has_audio(Path(mp4))
    return info


def _has_audio(mp4: Path) -> bool:
    import aud

    try:
        return aud.Sound(str(mp4)).length > 0
    except (RuntimeError, aud.error):
        return False
