"""Inside Blender: the edit draws a text its style font has no glyphs for in a font that has them, and OGG files
decode through Blender's aud."""

import tempfile
from pathlib import Path

import aud
import bpy
import numpy as np

from bsa import fonts
from bsa.audio import decode
from bsa.blender import edit
from bsa.layout import H, W
from bsa.short import Line, Short, Shot
from bsa.timeline import Card, Span, Timeline

CANDIDATES = "✓≈→✔★⚙"


def _frames(root: Path, n: int) -> None:
    img = bpy.data.images.new("tmp", W, H)
    img.pixels.foreach_set(np.tile([0.2, 0.4, 0.8, 1.0], W * H).astype(np.float32))
    img.file_format = "PNG"
    (root / "a").mkdir(parents=True)
    for i in range(1, n + 1):
        img.filepath_raw = str(root / "a" / f"{i:04d}.png")
        img.save()
    bpy.data.images.remove(img)


def _style_font_and_char():
    """An installed style font and a character it lacks that some other installed fallback has."""
    for name in fonts.FALLBACKS.get(__import__("platform").system(), ()):
        path = fonts.find_font_file(name, fonts.font_dirs())
        info = fonts.font_info_or_none(path) if path else None
        if info is None:
            continue
        for ch in CANDIDATES:
            if not fonts.covers(info, ch):
                alt = fonts.cover_font(ch, path)
                if alt is not None:
                    return name, path, ch, alt
    return None


def test_text_with_a_missing_glyph_uses_a_covering_font():
    found = _style_font_and_char()
    if found is None:
        print("no installed font pair to test with; skipped")
        return
    _, path, ch, alt = found
    bpy.ops.wm.read_factory_settings(use_empty=True)
    tmp = Path(tempfile.mkdtemp())
    _frames(tmp / "frames", 12)
    short = Short(slug="t", title="t", length_s=1, style={"font": str(path)},
                  shots=[Shot("a", "A", "p", "s", min_s=0.1, labels=[f"done {ch}"], badge="simplified")],
                  lines=[Line("l1", "Plain words", "a")])  # fmt: skip
    # the end card shows after the last caption: frames 7-12
    tl = Timeline(30, 12, [Span("a", 1, 12)], [Span("l1", 1, 6)], [Card("Plain words", "l1", 1, 6)])
    scene = edit.build_edit(short, tl, tmp / "frames", None, tmp / "o.mp4", {"handle": "me", "end_card": f"Play {ch}"})
    strips = {s.text: s for s in scene.sequence_editor.strips_all if s.type == "TEXT"}
    main = Path(bpy.path.abspath(strips["Plain words"].font.filepath)).resolve()
    assert main == Path(path).resolve()
    for text in (f"done {ch}", f"Play {ch}"):
        used = Path(bpy.path.abspath(strips[text].font.filepath)).resolve()
        assert used == Path(alt).resolve(), (text, used, alt)
    assert Path(bpy.path.abspath(strips["@me"].font.filepath)).resolve() == main
    notes = edit.glyph_fallbacks(scene)
    assert len(notes) == 2 and all(f"U+{ord(ch):04X}" in n for n in notes)


def test_ogg_decodes_with_aud():
    tmp = Path(tempfile.mkdtemp())
    path = tmp / "tone.ogg"
    snd = aud.Sound.sine(440, 44100).limit(0, 0.5)
    snd.write(str(path), rate=44100, channels=aud.CHANNELS_MONO, container=aud.CONTAINER_OGG,
              codec=aud.CODEC_VORBIS, bitrate=96000)  # fmt: skip
    x = decode.load(path)
    assert abs(x.shape[0] - 0.5 * 48000) < 2400, x.shape
    spec = np.abs(np.fft.rfft(x[:, 0]))
    peak = np.fft.rfftfreq(x.shape[0], 1 / 48000)[spec.argmax()]
    assert abs(peak - 440) < 10, peak
    assert not decode.sidecar(path).exists()  # inside Blender, no sidecar is needed
