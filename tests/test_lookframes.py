import re
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent


@pytest.mark.blender
def test_lookframes_in_blender(blender_run):
    result = blender_run(HERE / "blender" / "test_lookframes.py", timeout=900)
    assert result.returncode == 0, result.stdout + result.stderr
    for name in ("test_look_writes_three_frames_with_cards",
                 "test_look_with_another_style_leaves_the_blend_unchanged",
                 "test_look_never_changes_the_engine",
                 "test_look_without_a_kit_refuses"):
        assert f"PASS {name}" in result.stdout, result.stdout + result.stderr
    assert "user decrement error" not in result.stdout + result.stderr


def test_look_never_sets_or_saves():
    """The look command and its module never assign an engine and never save the .blend."""
    import inspect

    import bsa_blender

    root = Path(bsa_blender.__file__).resolve().parent
    src = inspect.getsource(bsa_blender.cmd_look) + inspect.getsource(bsa_blender._look_engine)
    src += (root / "bsa" / "blender" / "lookframes.py").read_text(encoding="utf-8")
    assert not re.search(r"\.engine\s*=(?!=)|\"CYCLES\"\s*$|BLENDER_EEVEE", src), "an engine is set in the look"
    assert "save_mainfile" not in src and "save_as_mainfile" not in src
    assert "engine=engine" in src  # the user's value goes through to the still render


def test_look_refuses_outside_a_background_blender(monkeypatch):
    """In the user's open session (MCP mode) look would change their scenes and revert their file: it refuses first."""
    import sys
    import types

    import bsa_blender

    fake = types.SimpleNamespace(app=types.SimpleNamespace(background=False))
    monkeypatch.setitem(sys.modules, "bpy", fake)
    args = types.SimpleNamespace(short="no-such-short", style=None, set=None, engine=None)
    with pytest.raises(bsa_blender.CliError, match="look runs in a background Blender"):
        bsa_blender.cmd_look(args)
