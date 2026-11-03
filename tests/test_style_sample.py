from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent


@pytest.mark.blender
def test_style_sample_in_blender(blender_run):
    result = blender_run(HERE / "blender" / "test_style_sample.py", timeout=900)
    assert result.returncode == 0, result.stdout + result.stderr
    for name in ("test_style_sample_sheet_has_one_tile_per_style",
                 "test_the_engine_comes_from_the_settings_when_no_flag_is_given",
                 "test_the_kits_own_style_is_the_default"):
        assert f"PASS {name}" in result.stdout, result.stdout + result.stderr


def test_style_sample_only_applies_the_users_engine():
    """The one engine assignment is made by scene.setup_scene / stills.render_still from the user's value (--engine or
    the setting): the command and its module never assign or default an engine themselves."""
    import inspect
    import re

    import bsa_blender

    root = Path(bsa_blender.__file__).resolve().parent
    src = inspect.getsource(bsa_blender.cmd_style_sample) + inspect.getsource(bsa_blender._style_engine)
    src += (root / "bsa" / "blender" / "stylesample.py").read_text(encoding="utf-8")
    assert not re.search(r"\.engine\s*=(?!=)|\"CYCLES\"|BLENDER_EEVEE", src), "an engine is set in the style sample"
    assert "engine=engine" in src and "setup_scene(scene, short, engine" in src  # the user's value goes through
    assert not re.search(r"save_settings|write_text\(.*settings", src)
