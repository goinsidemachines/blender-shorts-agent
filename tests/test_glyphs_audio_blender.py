import platform
import subprocess
from pathlib import Path

import numpy as np
import pytest

HERE = Path(__file__).resolve().parent


@pytest.mark.blender
def test_glyphs_and_audio_in_blender(blender_run):
    result = blender_run(HERE / "blender" / "test_glyphs_audio.py", timeout=600)
    assert result.returncode == 0, result.stdout + result.stderr
    for name in ("test_text_with_a_missing_glyph_uses_a_covering_font", "test_ogg_decodes_with_aud"):
        assert f"PASS {name}" in result.stdout
    if platform.system() == "Windows":  # Segoe UI lacks symbols that Segoe UI Symbol has: the test really ran
        assert "no installed font pair" not in result.stdout


@pytest.mark.blender
def test_ogg_decodes_outside_blender_through_a_blender_run(tmp_path, bsa_home):
    """CLI mix runs in Blender's Python, which has no aud: an OGG is decoded once by a background Blender into a
    48 kHz WAV next to it."""
    from bsa.audio import decode
    from bsa.locate import find_blender

    ogg = tmp_path / "tone.ogg"
    expr = (f"import aud; aud.Sound.sine(330, 44100).limit(0, 0.4).write({str(ogg)!r}, rate=44100, "
            "channels=aud.CHANNELS_MONO, container=aud.CONTAINER_OGG, codec=aud.CODEC_VORBIS, bitrate=96000)")  # fmt: skip
    subprocess.run([str(find_blender()), "-b", "--factory-startup", "--python-exit-code", "1", "--python-expr", expr],
                   check=True, capture_output=True, timeout=120)  # fmt: skip
    assert ogg.is_file()
    x = decode.load(ogg)
    assert decode.sidecar(ogg).is_file()
    assert abs(x.shape[0] - 0.4 * 48000) < 2400
    spec = np.abs(np.fft.rfft(x[:, 0]))
    assert abs(np.fft.rfftfreq(x.shape[0], 1 / 48000)[spec.argmax()] - 330) < 10
