"""A font with a weight, "Family@600": an installed static file of that weight, else the static weight `CLI assets`
cached in `<workspace>/.assets-cache/fonts/`, else a clear warning saying how to get it."""

import json
import os
import sys
from pathlib import Path

from fontbuild import build_font, build_variable

from bsa import fonts
from bsa import proc as _proc
from bsa.short import Line, Short, Shot

ROOT = Path(__file__).resolve().parent.parent
PLAN = 'CLI assets plan SHORT "font:Varia@600"'


def _fonts(tmp_path, *files):
    d = tmp_path / "fonts"
    d.mkdir(exist_ok=True)
    for name, data in files:
        (d / name).write_bytes(data)
    return [d]


def _cache(tmp_path, name="Varia-600.ttf", weight=600):
    cache = tmp_path / "ws" / ".assets-cache"
    (cache / "fonts").mkdir(parents=True)
    (cache / "fonts" / name).write_bytes(build_font("Lift", family="Varia", subfamily="SemiBold", weight=weight))
    return cache


VF = ("Varia-VF.ttf", build_variable("Lift", family="Varia"))
SEMI = ("Varia-SemiBold.ttf", build_font("Lift", family="Varia", subfamily="SemiBold", weight=600))
BOLD = ("Varia-Bold.ttf", build_font("Lift", family="Varia", subfamily="Bold", weight=700))


def test_parse_weight():
    assert fonts.parse_weight("Varia@600") == ("Varia", 600)
    assert fonts.parse_weight(" Open Sans @ 700 ") == ("Open Sans", 700)
    assert fonts.parse_weight("Varia") == ("Varia", None)
    assert fonts.parse_weight("Varia@heavy") == ("Varia@heavy", None)
    assert fonts.parse_weight("Varia@1200") == ("Varia@1200", None)


def test_installed_static_weight_wins(tmp_path):
    dirs = _fonts(tmp_path, VF, SEMI, BOLD)
    path, used, note = fonts.resolve("Varia@600", dirs, system="Windows", cache_dirs=[_cache(tmp_path)])
    assert path.name == "Varia-SemiBold.ttf" and note is None
    assert used == "Varia SemiBold"
    assert fonts.resolve("Varia@700", dirs, system="Windows", cache_dirs=[])[0].name == "Varia-Bold.ttf"


def test_cached_static_weight_from_assets(tmp_path):
    dirs = _fonts(tmp_path, VF)
    cache = _cache(tmp_path)
    path, used, note = fonts.resolve("Varia@600", dirs, system="Windows", cache_dirs=[tmp_path / "nope", cache])
    assert path == cache / "fonts" / "Varia-600.ttf" and note is None and used == "Varia SemiBold"


def test_family_with_spaces_uses_the_assets_file_name(tmp_path):
    cache = tmp_path / "ws" / ".assets-cache"
    (cache / "fonts").mkdir(parents=True)
    (cache / "fonts" / "OpenSans-700.ttf").write_bytes(build_font("a", family="Open Sans", weight=700))
    path, _, note = fonts.resolve("Open Sans@700", _fonts(tmp_path), system="Windows", cache_dirs=[cache])
    assert path.name == "OpenSans-700.ttf" and note is None


def test_missing_weight_falls_back_to_the_family_and_says_how_to_get_it(tmp_path):
    dirs = _fonts(tmp_path, VF, BOLD)
    path, used, note = fonts.resolve("Varia@600", dirs, system="Windows", cache_dirs=[])
    assert path.name == "Varia-VF.ttf"  # the family as installed (the variable font), not the Bold weight
    assert "600" in note and PLAN in note and "Varia" in used


def test_missing_family_falls_back_to_the_os_font_and_says_how_to_get_it(tmp_path):
    dirs = _fonts(tmp_path, ("segoeui.ttf", build_font("a", family="Segoe UI")))
    path, used, note = fonts.resolve("Varia@600", dirs, system="Windows", cache_dirs=[])
    assert path.name == "segoeui.ttf" and used == "Segoe UI"
    assert PLAN in note and "Segoe UI" in note


def test_a_file_path_with_an_at_sign_is_still_a_file(tmp_path):
    odd = tmp_path / "x@600.ttf"
    odd.write_bytes(build_font("a"))
    assert fonts.resolve(str(odd), [], system="Windows", cache_dirs=[])[0] == odd


def test_font_caches_from_the_short_and_the_workspace_setting(tmp_path, bsa_home):
    from bsa import settings

    sdir = tmp_path / "ws" / "01-x"
    assert fonts.font_caches(sdir)[0] == tmp_path / "ws" / ".assets-cache"
    settings.set_value("workspace", str(tmp_path / "other"))
    assert fonts.font_caches(sdir) == [tmp_path / "ws" / ".assets-cache", tmp_path / "other" / ".assets-cache"]
    assert fonts.font_caches(None) == [tmp_path / "other" / ".assets-cache"]


def _short(font):
    return Short(slug="t", title="T", length_s=10, style={"font": font}, lines=[Line("l1", "Lift", "s1")],
                 shots=[Shot("s1", "a", "p", "S")])  # fmt: skip


def test_glyph_warnings_with_a_weight(tmp_path):
    dirs = _fonts(tmp_path, VF)
    plain = fonts.glyph_warnings(_short("Varia"), {"handle": ""}, dirs, system="Windows")
    assert any("variable" in w for w in plain)
    found = fonts.glyph_warnings(_short("Varia@600"), {"handle": ""}, dirs, system="Windows",
                                 cache_dirs=[_cache(tmp_path)])  # fmt: skip
    assert found == []  # the cached static weight: no variable-font warning
    missing = fonts.glyph_warnings(_short("Varia@600"), {"handle": ""}, dirs, system="Windows", cache_dirs=[])
    assert len(missing) == 1 and PLAN in missing[0]


def test_report_for_cli_fonts(tmp_path):
    dirs = _fonts(tmp_path, VF, SEMI)
    got = fonts.report("Varia@600", dirs=dirs, cache_dirs=[])
    assert got["installed"] is True and got["file"].endswith("Varia-SemiBold.ttf") and got["weight"] == 600
    assert got["requested_weight"] == 600 and got["variable"] is False and not got.get("warning")
    gone = fonts.report("Varia@800", dirs=dirs, cache_dirs=[])
    assert gone["installed"] is False and gone["file"] is None
    assert 'CLI assets plan SHORT "font:Varia@800"' in gone["warning"]
    plain = fonts.report("Varia", dirs=dirs, cache_dirs=[])
    assert plain["installed"] is True and plain["file"].endswith("Varia-VF.ttf") and plain["variable"] is True


def test_cli_fonts_with_a_weight(tmp_path):
    env = {**os.environ, "BSA_HOME": str(tmp_path / "home")}
    out = _proc.run([sys.executable, str(ROOT / "bsa_cli.py"), "fonts", "No Such Font Anywhere@600"],
                         capture_output=True, text=True, check=True, env=env).stdout  # fmt: skip
    data = json.loads(out)
    assert data["installed"] is False and data["requested_weight"] == 600
    assert 'CLI assets plan SHORT "font:No Such Font Anywhere@600"' in data["warning"]


def test_a_plain_variable_family_with_a_downloaded_weight_says_to_use_it(tmp_path):
    """The static weight is already in the font cache: the warning says to name it ("Varia@600"), not to plan the
    download again."""
    dirs = _fonts(tmp_path, VF)
    cache = _cache(tmp_path)
    assert fonts.cached_weights("Varia", [tmp_path / "nope", cache]) == [600]
    style = fonts.glyph_warnings(_short("Varia"), {"handle": ""}, dirs, system="Windows", cache_dirs=[cache])
    assert len(style) == 1 and '"Varia@600"' in style[0] and "style.font" in style[0]
    assert "assets plan" not in style[0]
    s = {"handle": "", "brand": {"mode": "brand", "font": "Varia"}}
    brand = fonts.glyph_warnings(_short("Varia"), s, dirs, system="Windows", cache_dirs=[cache])
    assert len(brand) == 1 and 'brand.font "Varia@600"' in brand[0] and "assets plan" not in brand[0]
    got = fonts.brand_font_check("Varia", dirs, system="Windows", cache_dirs=[cache])
    assert 'settings set brand.font "Varia@600"' in got["warning"] and "assets plan" not in got["warning"]
    # with nothing cached, the advice to plan the download stays
    plain = fonts.glyph_warnings(_short("Varia"), {"handle": ""}, dirs, system="Windows", cache_dirs=[])
    assert any(PLAN in w for w in plain)
