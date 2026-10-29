"""Font lookup: a family name in the OS font folders, falling back through fonts that ship with the OS."""

import platform

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
