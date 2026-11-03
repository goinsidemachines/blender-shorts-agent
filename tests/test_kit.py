import base64
import copy

import pytest

from bsa import kit
from bsa.short import Short
from tests.kitfix import KIT

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGP4z8DwHwAFAAH/q842iQAAAABJRU5ErkJggg==")


def _brand(**over):
    b = copy.deepcopy(KIT)
    b.update(over)
    return b


def test_style_names_and_aliases():
    assert [kit.style_name(v) for v in ("Toon", " 3D", "flat", "pencil")] == ["toon", "realistic", "toon", "pencil"]
    with pytest.raises(ValueError, match="is not a style: use one of realistic, toy"):
        kit.style_name("watercolour")


def test_check_each_key():
    assert kit.check("palette.accent", "#7a4ce0") == "#7A4CE0"
    assert kit.check("outline_px", "4") == 4 and kit.check("caption.opacity", 0.5) == 0.5
    assert kit.check("set", "Studio") == "studio" and kit.check("style", "flat") == "toon"
    assert kit.check("material", "glossy toy") == "glossy toy" and kit.check("whatever", 5) == 5
    assert kit.check("pill_corner_px", None) is None and kit.check("progress", False) is False
    for key, bad, msg in (("palette.accent", "orange", "must be a colour like #EAF2F8"),
                          ("outline_px", 13, "from 0 to 12"), ("palette.force", "#123456", "go in brand.categories"),
                          ("set", "forest", "is not a set"), ("caption.size", 20, "from 32 to 120"),
                          ("logo.place", "middle", "top-left or top-right"), ("outline_px", True, "from 0 to 12")):
        with pytest.raises(ValueError, match=msg):
            kit.check(key, bad)


def test_logo_must_be_an_existing_png(tmp_path):
    png = tmp_path / "logo.png"
    png.write_bytes(PNG)
    assert kit.check("logo.file", str(png)) == str(png.resolve())
    assert kit.check("logo", str(png)) == {"file": str(png.resolve())}
    svg = tmp_path / "logo.svg"
    svg.write_text("<svg/>")
    with pytest.raises(ValueError, match="is an SVG: save the logo as a PNG"):
        kit.check("logo.file", str(svg))
    with pytest.raises(ValueError, match="must be a PNG file"):
        kit.check("logo.file", str(tmp_path / "gone.png"))
    fake = tmp_path / "fake.png"
    fake.write_text("not a png")
    with pytest.raises(ValueError, match="must be a PNG file"):
        kit.check("logo.file", str(fake))


def test_whole_brand_object_is_checked_key_by_key():
    with pytest.raises(ValueError, match="from 0 to 12"):
        kit.check("", {**KIT, "outline_px": 99})
    out = kit.check("", KIT)
    assert out["palette"]["accent"] == "#7A4CE0" and out["mode"] == "brand" and out["categories"]["spark"]


def test_no_style_no_kit(bsa_home):
    s = {"brand": {**KIT}}
    del s["brand"]["style"]
    assert kit.kit_for(s) is None


def test_kit_for_merges_plain_brand_and_short():
    k = kit.kit_for({"brand": KIT}, Short("x", "x", 1.0, style={"categories": {"grip": "#000000"}, "shadow_px": 0}))
    assert k.style == "toon" and k.palette["accent"] == "#7A4CE0" and k.categories["grip"] == "#000000"
    assert k.categories["spark"] == "#D9483B"
    assert k.shadow_px == 0 and k.caption["size"] == 64 and k.caption["draw"] is True and k.progress is True
    assert k.colour("spark") == "#D9483B" and k.colour("nope") == "#7A4CE0" and k.colour(None) == "#7A4CE0"


def test_a_fresh_short_has_no_kit_unless_its_own_style_says():
    s = {"brand": KIT}
    assert kit.kit_for(s, Short("x", "x", 1.0, look="fresh")) is None
    k = kit.kit_for(s, Short("x", "x", 1.0, look="fresh", style={"style": "toy"}))
    assert k.style == "toy" and k.palette == kit.PLAIN["palette"]


def test_legacy_palette_categories_are_read_once_style_is_set():
    k = kit.kit_for({"brand": {"mode": "brand", "style": "3d", "palette": {"accent": "#7A4CE0", "spark": "#D9483B"}}})
    assert k.style == "realistic" and k.categories == {"spark": "#D9483B"} and "spark" not in k.palette
    k = kit.kit_for({"brand": {"style": "toy", "background": "#101010", "palette": {"flow": "#111111"},
                               "categories": {"flow": "#222222"}}})
    assert k.palette["background"] == "#101010" and k.categories == {"flow": "#222222"}


def test_bad_stored_values_fall_back_and_are_named():  # Review Focus 2
    s = {"brand": {**KIT, "outline_px": "thick", "palette": {**KIT["palette"], "accent": "orange"}, "style": "3D "}}
    k = kit.kit_for(s)
    assert k.style == "realistic" and k.outline_px == 0 and k.palette["accent"] == kit.PLAIN["palette"]["accent"]
    probs = kit.problems(s)
    assert any("brand.outline_px" in p and "it is ignored" in p for p in probs)
    assert any("brand.palette.accent" in p for p in probs)
    own = kit.problems({"brand": KIT}, Short("x", "x", 1.0, style={"shadow_px": 99}))
    assert any(p.startswith("style.shadow_px") for p in own)


def test_missing_logo_is_a_problem_not_a_crash(tmp_path):  # Review Focus 1
    png = tmp_path / "logo.png"
    png.write_bytes(PNG)
    s = {"brand": {**KIT, "logo": str(png)}}
    assert kit.problems(s) == []
    png.unlink()
    k = kit.kit_for(s)
    assert k.logo["file"] == str(png.resolve())
    assert any("your logo file" in p and "is missing, so no logo badge is drawn" in p for p in kit.problems(s))


def test_advice_suggests_moving_legacy_categories_and_setting_a_style():
    s = {"brand": {"mode": "brand", "palette": {"accent": "#7A4CE0", "spark": "#D9483B"}, "material": "matte"}}
    text = " | ".join(kit.advice(s))
    assert "brand.palette.spark is read as the category colour spark" in text
    assert "settings set brand.categories '{\"spark\": \"#D9483B\"}'" in text
    assert "brand.material is a note Claude reads" in text and "brand.style is not set" in text
    assert kit.advice({"brand": KIT}) == []


def test_json_scalar_keys():
    assert all(kit.json_scalar(k) for k in ("brand.outline_px", "brand.progress", "brand.caption.size",
                                            "brand.pill_corner_px", "brand.caption.draw"))
    assert not kit.json_scalar("brand.style") and not kit.json_scalar("brand.font")


def test_kit_from_is_strict():
    assert kit.kit_from({"style": "pencil", "outline_px": 2}).outline_px == 2
    with pytest.raises(ValueError, match="from 0 to 12"):
        kit.kit_from({"style": "pencil", "outline_px": 50})


def test_kit_values_are_not_the_owners():
    banned = {"#F3F2EE", "#202326", "#FF6B00", "#B34700", "#FFC800", "#00A86B", "#0072CE", "#C9D3DD"}
    assert banned.isdisjoint({*kit.DEFAULT_CODE.values(), *kit.PLAIN["palette"].values()})
