"""Font lookup: a family name in the OS font folders, falling back through fonts that ship with the OS."""

import platform

import pytest
from fontbuild import build_font

from bsa import fonts


def _dir(tmp_path, *names):
    d = tmp_path / "fonts"
    d.mkdir(parents=True, exist_ok=True)
    for n in names:
        (d / n).write_bytes(b"\0")
    return [d]


def test_family_name_matches_files(tmp_path):
    dirs = _dir(tmp_path, "segoeui.ttf", "segoeuib.ttf", "Inter-Regular.otf", "Inter-Bold.otf")
    assert fonts.find_font_file("Segoe UI", dirs).name == "segoeui.ttf"
    assert fonts.find_font_file("inter", dirs).name == "Inter-Regular.otf"
    assert fonts.find_font_file("Nope", dirs) is None


def test_missing_font_falls_back_through_the_os_list(tmp_path):
    dirs = _dir(tmp_path, "arial.ttf")
    path, used, note = fonts.resolve("Inter", dirs, system="Windows")
    assert path.name == "arial.ttf" and used == "Arial"
    assert "Inter" in note and "Arial" in note
    dirs = _dir(tmp_path, "segoeui.ttf")
    assert fonts.resolve("Inter", dirs, system="Windows")[1] == "Segoe UI"  # Segoe UI comes first
    path, used, note = fonts.resolve("Inter", _dir(tmp_path / "x"), system="Linux")
    assert path is None and used is None and "Blender's default font" in note


def test_found_font_has_no_note(tmp_path):
    path, used, note = fonts.resolve("Segoe UI", _dir(tmp_path, "segoeui.ttf"), system="Windows")
    assert path.name == "segoeui.ttf" and used == "Segoe UI" and note is None


def test_fallback_lists_per_os():
    assert fonts.FALLBACKS["Windows"] == ("Segoe UI", "Arial")
    assert fonts.FALLBACKS["Darwin"] == ("Helvetica Neue", "Helvetica")
    assert fonts.FALLBACKS["Linux"] == ("DejaVu Sans", "Liberation Sans")


def test_suggestions_are_installed_fonts(tmp_path):
    dirs = _dir(tmp_path, "arial.ttf", "verdana.ttf", "segoeui.ttf")
    got = fonts.suggestions(dirs, system="Windows")
    assert got[:2] == ["Segoe UI", "Arial"] and "Verdana" in got and "Helvetica" not in got


def test_cli_fonts(tmp_path):
    import json
    import subprocess
    import sys
    from pathlib import Path

    cli = Path(__file__).resolve().parent.parent / "bsa_cli.py"
    out = subprocess.run([sys.executable, str(cli), "fonts", "No Such Font Anywhere"], capture_output=True, text=True,
                         check=True).stdout  # fmt: skip
    data = json.loads(out)
    assert data["name"] == "No Such Font Anywhere" and data["installed"] is False and data["file"] is None
    assert isinstance(data["suggestions"], list)
    if platform.system() == "Windows":
        assert "Segoe UI" in data["suggestions"]
        data = json.loads(subprocess.run([sys.executable, str(cli), "fonts", "Segoe UI"], capture_output=True,
                                         text=True, check=True).stdout)  # fmt: skip
        assert data["installed"] is True and data["file"].lower().endswith("segoeui.ttf")


def test_text_items_include_card_text():
    from bsa.short import Line, Short, Shot
    from tests.kitfix import KIT

    sh = Shot("s1", "t", "p", "S", title_card=["Push", "lift"], category="spark", step=[2, 4],
              bars={"items": [{"label": "Bar", "value": 2, "text": "2 kg"}, {"label": "Cup", "value": 1}],
                    "verdict": "Bar wins"})  # fmt: skip
    short = Short(slug="t", title="T", length_s=10, lines=[Line("l1", "Hi", "s1")], shots=[sh])
    items = fonts.text_items(short, {"handle": "", "brand": {**KIT, "logo": {"file": "x.png", "name": "Acme"}}})
    for item in (("shot s1 title card", "Push"), ("shot s1 title card", "lift"), ("shot s1 category", "Spark"),
                 ("shot s1 step", "Step 2 of 4"), ("shot s1 value bar", "Bar"), ("shot s1 value bar", "2 kg"),
                 ("shot s1 value bar", "1"), ("shot s1 verdict", "Bar wins"), ("logo badge", "Acme")):
        assert item in items, item


def test_advance_em_sums_hmtx(tmp_path):
    p = tmp_path / "a.ttf"
    p.write_bytes(build_font("AB", advance=500))  # upm 1000
    assert fonts.advance_em(p, "ABA") == pytest.approx(1.5)
    p.write_bytes(build_font("AB", advance={"A": 600, "B": 300, "": 250}, cmap_format=12))
    assert fonts.advance_em(p, "AB A") == pytest.approx(0.6 + 0.3 + 0.25 + 0.6)
    assert fonts.advance_em(p, "Z") == pytest.approx(0.25)  # a missing glyph counts glyph 0
    p.write_bytes(build_font("AB", advance={"": 700, " ": 100, "A": 200, "B": 900}, n_metrics=2))
    assert fonts.advance_em(p, "AB") == pytest.approx(0.2)  # ids past numberOfHMetrics take the last advance
    assert fonts.advance_em(p, "") == 0
    assert fonts.advance_em(tmp_path / "missing.ttf", "A") is None
    p.write_bytes(build_font("AB"))  # no hmtx
    assert fonts.advance_em(p, "A") is None
