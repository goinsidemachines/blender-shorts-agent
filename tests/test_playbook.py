"""The skill's instructions agree with what the code does."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILL = ROOT / "skills" / "make-short"


def _read(name: str) -> str:
    return (SKILL / name).read_text(encoding="utf-8")


def _flat(text: str) -> str:
    return " ".join(text.split())


def test_engine_is_approved_in_chat_not_on_a_page():
    text = _flat(_read("SKILL.md"))
    assert "three things on HTML pages" not in text
    assert re.search(r"story and the storyboard on HTML pages.*render engine in chat", text), text[:600]


def test_long_commands_run_in_the_background():
    """The Bash tool kills a command after 2 minutes by default (10 at most)."""
    skill = _flat(_read("SKILL.md"))
    assert "run_in_background" in skill
    for cmd in ("render", "estimate", "voice install", "narrate", "frame-check"):
        assert cmd in skill.split("run_in_background", 1)[1][:1500], cmd
    for chapter, cmds in (("04-engine.md", ["estimate"]), ("05-narration.md", ["narrate", "voice install"]),
                          ("08-render.md", ["render SHORT --preset final", "frame-check SHORT --preset final"])):
        text = _flat(_read(f"playbook/{chapter}"))
        assert "run_in_background" in text, chapter
        for cmd in cmds:
            assert cmd in text, (chapter, cmd)


def test_shorts_are_named_by_number():
    assert "use the short's number" in _flat(_read("SKILL.md"))


def test_assets_location_is_documented_consistently():
    for name in ("playbook/assets.md", "playbook/06-build.md"):
        text = _flat(_read(name))
        assert ".assets-cache/<file path>" in text or "asset_files(" in text, name
    assert "asset_files(" in _flat(_read("playbook/06-build.md"))


def test_appended_objects_not_collection_instances():
    text = _flat(_read("playbook/assets.md"))
    assert "collection instance" in text.lower() and "append" in text.lower()


def test_narration_resume_is_described_for_every_engine():
    text = _flat(_read("playbook/05-narration.md"))
    assert "only that take is remade" in text
    assert "local voices" in text.lower() and "resume" in text.lower()


def test_mcp_builds_reload_the_plugin_code():
    for name in ("playbook/mcp-mode.md", "playbook/06-build.md"):
        text = _flat(_read(name))
        assert 'runpy.run_path(r"<root>/bsa_mcp_build.py")["build_short"](r"<short>")' in text, name
    for path in [SKILL / "SKILL.md", *sorted((SKILL / "playbook").rglob("*.md"))]:
        assert "exec(open(" not in path.read_text(encoding="utf-8"), path
    assert (ROOT / "bsa_mcp_build.py").is_file()


def test_playbook_does_not_leak_a_demo_topic():
    """Demo shorts are researched fresh: no running example from the earlier demo shorts."""
    owned_by_others = {"07-checks.md", "08-render.md", "mcp-mode.md"}
    paths = [SKILL / "SKILL.md", *sorted((SKILL / "playbook").rglob("*.md"))]
    for path in paths:
        if path.name in owned_by_others:
            continue
        text = path.read_text(encoding="utf-8")
        for word in ("Taipei", "660", "6.8 s", "40%", "×70", "fights the sway", "pin-tumbler", "Pin-tumbler",
                     "skyscraper hangs"):  # fmt: skip
            assert word not in text, (path.name, word)


def test_playbook_path_is_unambiguous():
    text = _flat(_read("SKILL.md"))
    assert "<root>/skills/make-short/playbook/" in text
    assert "not `<root>/playbook/`" in text


def test_caption_band_rows_match_the_layout():
    from bsa.layout import H, caption_rect

    margin = 0.018 * H  # edit.CAPTION_MARGIN: the caption box around the text
    two = caption_rect(2, 64)
    top, bottom = round(two.y - margin), round(two.bottom + margin)
    text = _flat(_read("playbook/06-build.md"))
    assert f"rows about {top}-{bottom}" in text, (top, bottom)
    assert f"{round(100 * top / H)}-{round(100 * bottom / H)}% down" in text
    assert f"about {round(caption_rect(1, 64).y - margin)}-{bottom}" in text


def test_build_chapter_has_the_quality_bar_and_the_review_commands():
    text = _flat(_read("playbook/06-build.md"))
    for phrase in ("Moving parts live in hollow parts", "start and end on surfaces", "never share a volume",
                   "Fluids are bounded by what moves them", "through openings", "Clearances are computed",
                   "`contact:` only for real contact", "Run the checks", "extremes of the motion",
                   "contact:<name>:<max_depth_m>", "BLENDER extremes SHORT", "BLENDER grab SHORT --preset preview",
                   "BLENDER preview SHORT"):  # fmt: skip
        assert phrase in text, phrase
    skill = _flat(_read("SKILL.md"))
    for cmd in ("BLENDER extremes SHORT", "BLENDER grab SHORT --preset preview|final --at <s>...", "BLENDER preview SHORT"):
        assert cmd in skill, cmd


def test_build_chapter_has_the_lessons_from_fixing_artifacts():
    text = _flat(_read("playbook/06-build.md"))
    for phrase in ("Each effect gets its own lane", "Move along paths by arc length", "bake.keyframe_path(",
                   "Every fix can make a new artifact", "BLENDER check SHORT` again", "no live Booleans",
                   "Parts in contact share the moving parent"):  # fmt: skip
        assert phrase in text, phrase


def test_hard_rules_are_asked_kept_and_shown():
    from bsa import rules

    skill = _flat(_read("SKILL.md"))
    assert "CLI settings rules" in skill and "before each approval" in skill and "never guess" in skill.lower()
    first = _flat(_read("playbook/00-first-run.md"))
    assert ("Any hard rules every video must follow? (for example: no music, no people, no animals, a particular "
            "dress code or content limit) — I'll follow them in every short and check what I can.") in first
    assert "settings rule add" in first and "settings rule remove" in first and "settings rule none" in first
    assert "--part-of" in first and "Never guess" in first
    for kind in rules.KINDS:
        assert f"`{kind}`" in first, kind
    for chapter in ("01-interview.md", "02-story.md", "03-storyboard.md", "06-build.md", "assets.md", "sound.md",
                    "09-post.md"):  # fmt: skip
        assert "rule" in _read(f"playbook/{chapter}").lower(), chapter
    assert "rules_check" in _flat(_read("playbook/02-story.md"))


def test_checks_chapter_lists_every_scene_check():
    import ast

    source = (ROOT / "bsa" / "blender" / "checks.py").read_text(encoding="utf-8")
    names = set()
    for node in ast.walk(ast.parse(source)):
        if (isinstance(node, ast.Call) and getattr(node.func, "attr", None) == "add" and node.args
                and getattr(node.func.value, "id", None) in ("self", "run")):
            first = node.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                names.add(first.value)
    assert {"self_overlap", "boolean", "contact_parent"} <= names, names
    text = _read("playbook/07-checks.md")
    for name in names:
        assert f"| `{name}`" in text, name
    assert "10 times the other's bounding-box volume" in _flat(text)
