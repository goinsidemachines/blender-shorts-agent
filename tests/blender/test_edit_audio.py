"""Inside Blender: the edit's sound strip and the AAC encode keep the first 50 ms of the voice (a first word is not
clipped where the mix meets the video)."""

import tempfile
import wave
from pathlib import Path

import aud
import bpy
import numpy as np

from bsa.blender import edit
from bsa.layout import H, W
from bsa.short import Line, Short, Shot
from bsa.timeline import Card, Span, Timeline

SR = 48000
HEAD = round(0.05 * SR)


def _head_take() -> np.ndarray:
    """One second: a 2 kHz burst for the first 50 ms from sample 0, then a 300 Hz tone."""
    t = np.arange(SR) / SR
    x = 0.3 * np.sin(2 * np.pi * 300 * t)
    x[:HEAD] = 0.3 * np.sin(2 * np.pi * 2000 * t[:HEAD])
    return x


def _ratio_db(x: np.ndarray) -> float:
    return float(10 * np.log10(np.sum(x[:HEAD] ** 2) / np.sum(x[HEAD : 2 * HEAD] ** 2)))


def _edit(x: np.ndarray):
    """A 30-frame edit whose mix is `x` (stereo WAV); returns the Edit scene."""
    bpy.ops.wm.read_factory_settings(use_empty=True)
    tmp = Path(tempfile.mkdtemp())
    (tmp / "frames" / "a").mkdir(parents=True)
    img = bpy.data.images.new("tmp", W, H)
    img.pixels.foreach_set(np.tile([0.2, 0.4, 0.8, 1.0], W * H).astype(np.float32))
    img.file_format = "PNG"
    for i in range(1, 31):
        img.filepath_raw = str(tmp / "frames" / "a" / f"{i:04d}.png")
        img.save()
    bpy.data.images.remove(img)
    wav = tmp / "mix.wav"
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(np.repeat((x * 32767).astype("<i2"), 2).tobytes())
    short = Short(slug="t", title="t", length_s=1.0, shots=[Shot("a", "A", "p", "s", min_s=0.5)],
                  lines=[Line("l1", "Hello there", "a")])  # fmt: skip
    tl = Timeline(30, 30, [Span("a", 1, 30)], [Span("l1", 1, 30)], [Card("Hello there", "l1", 1, 30)])
    return edit.build_edit(short, tl, tmp / "frames", wav, tmp / "out.mp4", {"handle": None}, preset="preview")


def _prop(strip, *names):
    """The first of `names` the strip has (Blender 5 renamed the strip timing properties; older ones lack them)."""
    for name in names:
        if name in strip.bl_rna.properties:
            return getattr(strip, name)
    raise AssertionError(f"sound strip has none of {names}")


def test_the_mix_strip_starts_on_frame_one_with_no_offset():
    sc = _edit(_head_take())
    sounds = [s for s in sc.sequence_editor.strips_all if s.type == "SOUND"]
    assert len(sounds) == 1, sounds
    s = sounds[0]
    assert _prop(s, "content_start", "frame_start") == 1
    assert _prop(s, "left_handle", "frame_final_start") == 1
    assert _prop(s, "left_handle_offset", "frame_offset_start") == 0
    assert _prop(s, "content_trim_start", "animation_offset_start") == 0
    assert s.sound_offset == 0
    assert sc.frame_start == 1


def test_the_encode_keeps_the_first_50_ms():
    """The MP4's AAC, decoded back through Blender's own ffmpeg: the burst is still at sample 0 and the first 50 ms
    keep their energy against the next 50 ms."""
    x = _head_take()
    sc = _edit(x)
    snd = aud.Sound(str(edit.encode(sc)))
    y = np.asarray(snd.data(), dtype=np.float64).mean(axis=1)
    lag = int(np.argmax(np.correlate(y[: SR // 4], x[:HEAD], mode="valid")))
    assert abs(lag) <= 1, lag
    assert abs(_ratio_db(y) - _ratio_db(x)) <= 1.0, (_ratio_db(y), _ratio_db(x))
