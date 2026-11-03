"""BLENDER style-sample inside Blender (runs via runner.py): one tile per style, a caption row under each, the engine the
user chose, the settings file untouched. Uses the made-up kit (tests/kitfix.py)."""

import json
import os
import sys
import tempfile
import types
from pathlib import Path

import bpy
import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

TMP = Path(tempfile.mkdtemp())
HOME = TMP / "home"
HOME.mkdir()
os.environ["BSA_HOME"] = str(HOME)

from kitfix import KIT

import bsa_blender
from bsa.blender import scene as scene_mod
from bsa.blender.pixels import read_png

scene_mod.use_gpu = lambda: "NONE"  # Cycles on the CPU, as the plan pins tests
SETTINGS = HOME / "settings.json"


def _args(**kw):
    base = {"kit": json.dumps(KIT), "styles": None, "set": "studio", "engine": None, "out": str(TMP / "sheet.png"),
            "scale": 0.1}
    return types.SimpleNamespace(**{**base, **kw})


def test_style_sample_sheet_has_one_tile_per_style():
    SETTINGS.write_text('{"brand": {"mode": "fresh"}}', encoding="utf-8")
    before = SETTINGS.read_bytes()
    out = bsa_blender.cmd_style_sample(_args(styles="toy,toon", engine="eevee"))
    px = read_png(Path(out["png"]))
    assert px.shape[:2] == (192 + 32, 2 * 108), px.shape
    assert out["engine"] == "eevee" and out["styles"] == ["toy", "toon"] and out["set"] == "studio"
    assert set(out["seconds"]) == {"toy", "toon"} and isinstance(out["warnings"], list)
    tiles = [px[:192, i * 108 : (i + 1) * 108, :3].astype(int) for i in range(2)]
    for t in tiles:
        assert t.reshape(-1, 3).std(axis=0).max() > 5, "a tile is flat"  # something is drawn
    assert np.abs(tiles[0] - tiles[1]).mean() > 3, "the two styles look the same"
    caption = px[192:, :, :3].astype(int)
    box = np.array([0x1B, 0x2A, 0x3A])  # the kit's caption box
    on_box = np.all(np.abs(caption - box) <= 2, axis=2)
    assert on_box.mean() > 0.5, on_box.mean()  # the row is the caption's colour...
    for i in range(2):
        assert (~on_box[:, i * 108 : (i + 1) * 108]).sum() > 20, f"no caption text under tile {i}"  # ...with text
    assert SETTINGS.read_bytes() == before  # nothing written to the settings
    assert not [s for s in bpy.data.scenes if s.name.startswith("StyleSample")]
    assert not [m for m in bpy.data.materials if m.users == 0 and m.name.startswith(("Crate", "Lever"))]


def test_the_engine_comes_from_the_settings_when_no_flag_is_given():
    SETTINGS.write_text('{"render": {"engine": "cycles"}}', encoding="utf-8")
    out = bsa_blender.cmd_style_sample(_args(styles="toy", set="none"))
    assert out["engine"] == "cycles" and out["set"] == "none", out
    assert read_png(Path(out["png"])).shape[:2] == (192 + 32, 108)
    SETTINGS.write_text('{"render": {"engine": "ask-always"}}', encoding="utf-8")
    try:
        bsa_blender.cmd_style_sample(_args(styles="toy"))
    except bsa_blender.CliError as exc:
        assert "needs an engine" in str(exc)
    else:
        raise AssertionError("ask-always with no --engine must refuse")


def test_the_kits_own_style_is_the_default():
    SETTINGS.write_text("{}", encoding="utf-8")
    out = bsa_blender.cmd_style_sample(_args(engine="eevee", set="none"))
    assert out["styles"] == ["toon"]  # the made-up kit's style
