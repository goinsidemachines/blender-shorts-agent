"""Font files: cmap coverage, variable fonts (fvar) and which characters of a short's text a font cannot draw."""

import json
import platform
import subprocess
import sys
from pathlib import Path

import pytest
from fontbuild import build_font, build_variable

from bsa import fonts
from bsa.short import Line, Short, Shot

ROOT = Path(__file__).resolve().parent.parent


def _write(tmp_path, name, data):
    d = tmp_path / "fonts"
    d.mkdir(exist_ok=True)
    (d / name).write_bytes(data)
    return d / name


@pytest.mark.parametrize("fmt", [4, 12])
def test_cmap_coverage(tmp_path, fmt):
    path = _write(tmp_path, f"a{fmt}.ttf", build_font("Hello→", cmap_format=fmt))
    info = fonts.font_info(path)
    assert fonts.covers(info, "H") and fonts.covers(info, "→") and fonts.covers(info, " ")
    assert not fonts.covers(info, "≈") and not fonts.covers(info, "x")
    assert fonts.missing_glyphs("Hello ≈ x\n", path) == ["≈", "x"]  # in order, once each; whitespace never counts


def test_static_font_info(tmp_path):
    info = fonts.font_info(_write(tmp_path, "s.ttf", build_font("a", family="Testa", subfamily="Bold", weight=700)))
    assert info["family"] == "Testa" and info["subfamily"] == "Bold" and info["weight"] == 700
    assert info["variable"] is False and fonts.variable_warning(info, "Testa") is None


def test_variable_font_default_instance(tmp_path):
    path = _write(tmp_path, "Varia-VF.ttf", build_variable("abc", family="Varia", default_weight=300))
    info = fonts.font_info(path)
    assert info["variable"] is True
    assert [a["tag"] for a in info["axes"]] == ["wght"]
    assert info["default_weight"] == 300 and info["default_instance"] == "Light"
    warning = fonts.variable_warning(info, "Varia")
    assert "variable" in warning and "Light" in warning and "300" in warning


def test_bad_file_is_not_a_font(tmp_path):
    path = _write(tmp_path, "bad.ttf", b"not a font at all")
    with pytest.raises(ValueError):
        fonts.font_info(path)
    assert fonts.font_info_or_none(path) is None


def test_static_siblings(tmp_path):
    _write(tmp_path, "Varia-VF.ttf", build_variable("a", family="Varia"))
    _write(tmp_path, "Varia-Bold.ttf", build_font("a", family="Varia", subfamily="Bold", weight=700))
    _write(tmp_path, "Varia-SemiBold.ttf", build_font("a", family="Varia", subfamily="SemiBold", weight=600))
    got = fonts.static_weights("Varia", [tmp_path / "fonts"])
    assert [(g["weight"], Path(g["file"]).name) for g in got] == [(600, "Varia-SemiBold.ttf"), (700, "Varia-Bold.ttf")]


def test_cover_font_picks_a_font_with_every_glyph(tmp_path):
    main = _write(tmp_path, "main.ttf", build_font("abc"))
    _write(tmp_path, "segoeui.ttf", build_font("abc→"))
    _write(tmp_path, "seguisym.ttf", build_font("abc→≈"))
    dirs = [tmp_path / "fonts"]
    assert fonts.cover_font("a→", main, dirs, system="Windows").name == "segoeui.ttf"
    assert fonts.cover_font("a≈", main, dirs, system="Windows").name == "seguisym.ttf"
    assert fonts.cover_font("abc", main, dirs, system="Windows") == main  # nothing missing: the main font
    assert fonts.cover_font("ab✓", main, dirs, system="Windows") is None  # no installed font has it


def test_text_items_cover_captions_labels_badges_end_card():
    short = Short(slug="t", title="T", length_s=10, lines=[Line("l1", "Lift ≈ 2×", "s1")],
                  shots=[Shot("s1", "a", "p", "S", labels=["load", {"text": "pivot →", "near": "Pivot"}],
                              badge="simplified")])  # fmt: skip
    items = fonts.text_items(short, {"handle": "me", "end_card": "Play with it →"})
    assert ("line l1", "Lift ≈ 2×") in items
    assert ("shot s1 label", "pivot →") in items and ("shot s1 badge", "SIMPLIFIED") in items
    assert ("end card", "Play with it →") in items and ("end card", "@me") in items
    no_handle = fonts.text_items(short, {"handle": "", "end_card": "Play →"})
    assert all(w != "end card" for w, _ in no_handle)


def test_glyph_warnings_name_the_text_and_the_fallback(tmp_path):
    main = _write(tmp_path, "Brand.ttf", build_font("Lift2PlywthiPLAYWT@meload"))
    _write(tmp_path, "segoeui.ttf", build_font("Lift2PlywthiPLAYWT@meload→≈× ", family="Segoe UI"))
    short = Short(slug="t", title="T", length_s=10, style={"font": str(main)}, lines=[Line("l1", "Lift ≈ 2×", "s1")],
                  shots=[Shot("s1", "a", "p", "S")])  # fmt: skip
    warnings = fonts.glyph_warnings(short, {"handle": "me", "end_card": "Play with it →"}, [tmp_path / "fonts"],
                                    system="Windows")  # fmt: skip
    text = " | ".join(warnings)
    assert "line l1" in text and "'≈'" in text and "'×'" in text and "U+2248" in text
    assert "end card" in text and "'→'" in text and "Segoe UI" in text
    clean = Short(slug="t", title="T", length_s=10, style={"font": str(main)}, lines=[Line("l1", "Lift", "s1")],
                  shots=[Shot("s1", "a", "p", "S")])  # fmt: skip
    assert fonts.glyph_warnings(clean, {"handle": "", "end_card": ""}, [tmp_path / "fonts"], system="Windows") == []


def test_glyph_warnings_warn_about_a_variable_style_font(tmp_path):
    vf = _write(tmp_path, "Varia-VF.ttf", build_variable("Lift", family="Varia"))
    short = Short(slug="t", title="T", length_s=10, style={"font": str(vf)}, lines=[Line("l1", "Lift", "s1")],
                  shots=[Shot("s1", "a", "p", "S")])  # fmt: skip
    warnings = fonts.glyph_warnings(short, {"handle": ""}, [tmp_path / "fonts"], system="Windows")
    assert any("variable" in w and "Light" in w for w in warnings)


@pytest.mark.skipif(platform.system() != "Windows", reason="Windows system fonts")
def test_real_windows_fonts():
    seg = fonts.find_font_file("Segoe UI", fonts.font_dirs())
    info = fonts.font_info(seg)
    assert info["variable"] is False and fonts.covers(info, "A") and fonts.covers(info, "→")
    var = Path(fonts.font_dirs()[0]) / "SegUIVar.ttf"
    if var.is_file():
        assert fonts.font_info(var)["variable"] is True


def test_brand_playbook_covers_variable_fonts_and_glyphs():
    text = (ROOT / "skills/make-short/playbook/look-and-brand.md").read_text(encoding="utf-8")
    assert "variable font" in text and "font:Fredoka@600" in text and "--short SHORT" in text and "glyph" in text


def _cli(*args, env_home):
    import os

    env = {**os.environ, "BSA_HOME": str(env_home)}
    return subprocess.run([sys.executable, str(ROOT / "bsa_cli.py"), *args], capture_output=True, text=True, env=env,
                          check=False)  # fmt: skip


def test_cli_fonts_reports_a_variable_font(tmp_path, bsa_home):
    vf = _write(tmp_path, "Varia-VF.ttf", build_variable("abc", family="Varia"))
    out = _cli("fonts", str(vf), env_home=bsa_home)
    assert out.returncode == 0, out.stderr
    data = json.loads(out.stdout)
    assert data["installed"] is True and data["variable"] is True and data["default_weight"] == 300
    assert "Light" in data["warning"] and "font:" in data["warning"]  # how to plan a static weight
    out = _cli("fonts", str(vf), "--text", "ab≈", env_home=bsa_home)
    assert json.loads(out.stdout)["missing_glyphs"] == ["≈"]
