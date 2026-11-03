"""The skill's instructions agree with what the code does."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILL = ROOT / "skills" / "make-short"


def _read(name: str) -> str:
    return (SKILL / name).read_text(encoding="utf-8")


def _flat(text: str) -> str:
    return " ".join(text.split())


def test_engine_is_approved_on_the_page_or_in_chat():
    text = _flat(_read("SKILL.md"))
    assert "three things on HTML pages" not in text and "HTML pages" not in text
    assert re.search(r"story, the storyboard and the render engine on one review page.*or in chat", text), text[:600]
    engine = _flat(_read("playbook/04-engine.md"))
    assert "CLI page decide SHORT engine approve --by settings --choice" in engine
    assert "CLI page decide SHORT engine approve --by chat --choice" in engine


def test_page_is_opened_at_the_interview_and_never_assumed():
    t = _flat(_read("playbook/01-interview.md"))
    assert "CLI page SHORT" in t and re.search(r"opened.{0,40}false.*link", t)
    skill = _flat(_read("SKILL.md"))
    assert "CLI page SHORT" in skill and re.search(r"opened.{0,40}false.*link", skill)


def test_approval_chapters_use_the_page_loop():
    for ch, stage in (("02-story.md", "story"), ("03-storyboard.md", "storyboard"), ("04-engine.md", "engine")):
        t = _flat(_read(f"playbook/{ch}"))
        assert f"CLI page wait SHORT --stage {stage}" in t and "run_in_background" in t, ch
        assert f"CLI page decide SHORT {stage} approve" in t and f"state SHORT done {stage}" not in t, ch


def test_no_doc_marks_an_approval_stage_done_by_hand():
    paths = [SKILL / "SKILL.md", *sorted((SKILL / "playbook").rglob("*.md"))]
    for path in paths:
        t = _flat(path.read_text(encoding="utf-8"))
        for stage in ("story", "storyboard", "engine"):
            assert f"state SHORT done {stage}" not in t and f"state done {stage}" not in t, (path.name, stage)
    cli = (ROOT / "bsa_cli.py").read_text(encoding="utf-8")
    assert not re.search(r"state SHORT done (story|storyboard|engine)\b", cli)


def test_page_notes_are_answered_before_the_next_step():
    t = _flat(_read("SKILL.md"))
    assert "page_notes" in t and "CLI page note SHORT" in t
    assert re.search(r"page_notes.{0,300}(before the next step|resum)", t), t


def test_no_chapter_points_at_the_retired_pages():
    for f in SKILL.rglob("*.md"):
        text = f.read_text("utf-8")
        assert "review/render-preview.html" not in text and "plugin-feedback.html" not in text, f.name
        assert "CLI review " not in text, f.name  # `CLI page SHORT` opens the one page
        assert not re.search(r"(render-status|feedback report)[^`]*--open", text), f.name


def test_long_commands_run_in_the_background():
    """The Bash tool kills a command after 2 minutes by default (10 at most)."""
    skill = _flat(_read("SKILL.md"))
    assert "run_in_background" in skill
    for cmd in ("render", "estimate", "voice install", "narrate", "frame-check"):
        assert cmd in skill.split("Long commands run in the background", 1)[1][:1500], cmd
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


def test_build_scripts_use_only_bpy_stdlib_and_numpy():
    """Pillow and other third-party packages are off Blender's path under --factory-startup."""
    text = _flat(_read("playbook/06-build.md"))
    assert "Pillow is not importable inside Blender" in text and "--factory-startup" in text
    assert "`bpy`, the standard library, numpy" in text


def test_scale_references_never_need_a_person():
    """A human figure for scale breaks a no_humans rule: every scale tip offers an object and names the rule."""
    bullets = []
    for path in sorted((SKILL / "playbook").rglob("*.md")):
        text = path.read_text(encoding="utf-8")
        for bullet in re.split(r"\n(?=\s*[-|#]|\s*\n)", text):
            flat = _flat(bullet)
            if re.search(r"for scale|next to a person|beside a person", flat, re.IGNORECASE):
                bullets.append((path.name, flat))
    assert len(bullets) >= 3, bullets
    for name, flat in bullets:
        assert "no_humans" in flat, (name, flat)
        assert re.search(r"\b(door|car|bench|container)\b", flat), (name, flat)


def test_engine_from_short_json_is_recorded_never_asked():
    engine = _flat(_read("playbook/04-engine.md"))
    assert "propose it" not in engine and "go with EEVEE?" not in engine
    assert re.search(r"short\.json.{0,300}never ask about it.{0,300}--by settings --choice", engine), engine[:900]
    skill = _flat(_read("SKILL.md"))
    assert re.search(r"engine from short\.json or the `render\.engine` setting\*\* is never asked about", skill)


def test_only_sent_notes_are_acted_on_and_decisions_are_applied_by_wait():
    for name in ("SKILL.md", "playbook/02-story.md", "playbook/03-storyboard.md", "playbook/04-engine.md"):
        t = _flat(_read(name))
        assert "CLI page notes SHORT" in t, name
        assert re.search(r"act only on (notes whose `status` is )?`sent`", t, re.IGNORECASE), name
        assert "`open` note is the user still writing" in t, name
        assert re.search(r"page wait SHORT --stage <?[a-z ]+>?`?,? which (applies it and )?returns at once|applies it "
                         r"and returns at once", t), name  # fmt: skip


def test_resume_reruns_the_wait_for_an_approval_already_shown():
    skill = _flat(_read("SKILL.md"))
    assert re.search(r"Resuming.{0,900}`next` is in the `waiting_for_yes` list of `CLI page notes SHORT`.{0,250}run "
                     r"`CLI page wait SHORT --stage <next>` in the background again", skill)  # fmt: skip
    for ch, stage in (("02-story.md", "story"), ("03-storyboard.md", "storyboard"), ("04-engine.md", "engine")):
        t = _flat(_read(f"playbook/{ch}"))
        assert f"lists {stage} in `waiting_for_yes`" in t and "don't redo this chapter" in t, ch


def test_decisions_are_applied_before_notes_and_stale_ones_dropped():
    for name in ("SKILL.md", "playbook/02-story.md", "playbook/03-storyboard.md", "playbook/04-engine.md"):
        t = _flat(_read(name))
        first, notes = t.find("Decisions first, before you change anything"), t.find("act only on", 0)
        notes = t.lower().find("act only on")
        assert 0 <= first < notes, name
        assert re.search(r"older version of (a|the) \w+.{0,120}dropped automatically.{0,40}`superseded`.{0,60}asks "
                         r"the user again for the new version", t), name  # fmt: skip


def test_a_timed_out_wait_is_rerun_only_while_its_stage_still_waits():
    for name, stage in (("SKILL.md", None), ("playbook/02-story.md", "story"),
                        ("playbook/03-storyboard.md", "storyboard"), ("playbook/04-engine.md", "engine")):  # fmt: skip
        t = _flat(_read(name))
        assert "otherwise drop it" in t, name
        assert re.search(r"only while (that|the \w+) stage still waits for a yes|only while the \w+ still waits", t), name
        if stage:
            assert f"still has `next` {stage}" in t, name


def test_frame_notes_are_read_as_a_frame_to_look_at():
    t = _flat(_read("SKILL.md"))
    assert "look at frame N" in t and "BLENDER grab SHORT --preset final --at" in t
    assert "(N - 0.5) / 30" in t and "<run key>:<N>" in t and "--tag <tag>" in t


def test_retake_notes_renarrate_one_line_then_rebuild():
    skill = _flat(_read("SKILL.md"))
    t = skill + " " + _flat(_read("playbook/05-narration.md"))
    assert "CLI narrate SHORT --line ID" in t and "--say" in t and "BLENDER preview SHORT" in t
    retake = skill.split("Re-take this line", 1)[1][:1300]
    assert "run_in_background" in retake and "BLENDER preview SHORT" in retake and "CLI page note SHORT" in retake


def test_needs_a_source_adds_a_fact_or_rewords():
    t = _flat(_read("SKILL.md"))
    need = t.split("Needs a source", 1)[1][:700]
    assert "`facts`" in need and "reword" in need and "CLI validate SHORT" in need and "CLI page note SHORT" in need


def test_whisper_install_hint_is_documented():
    t = _flat(_read("playbook/05-narration.md"))
    assert "whisper-small.en" in t and "CLI voice plan whisper-small.en" in t and "why_not" in t
    assert "audio/vo/heard.json" in t and "ElevenLabs" in t.split("heard.json", 1)[1]


def test_stale_timeline_means_edit_again():
    t = _flat(_read("playbook/08-render.md"))
    assert "older timing" in t and re.search(r"older timing.{0,400}BLENDER edit SHORT --preset", t)


def test_post_chapter_names_the_final_review_tools():
    t = _flat(_read("playbook/09-post.md"))
    assert "timeline" in t and "Facts behind every line" in t and "Compare with version N" in t


def test_finding_notes_are_read_from_their_finding_row():
    t = _flat(_read("SKILL.md"))
    assert '"finding"' in t and '"gone": true' in t


def test_post_chapter_asks_for_the_users_format_once():
    t = _flat(_read("playbook/09-post.md"))
    assert "CLI settings post-format" in t and "Paste an earlier post" in t
    assert "CLI settings set post_format" in t and "post_format_asked true" in t


def test_post_chapter_makes_thumbnails_and_caption_files():
    t = _flat(_read("playbook/09-post.md"))
    for s in ("BLENDER thumbs SHORT", "--only N", "CLI captions SHORT", "run_in_background: true",
              "CLI page decide SHORT thumbnail --choice N", "Use this frame for the thumbnail", "CLI post SHORT",
              "BLENDER thumbs SHORT --only N --hero <shot>@<frame>"):  # fmt: skip
        assert s in t, s
    render = _flat(_read("playbook/08-render.md"))
    assert ".srt" in render and "-transcript.txt" in render


def test_post_chapter_records_where_it_went():
    t = _flat(_read("playbook/09-post.md"))
    assert "--posted" in t and "CLI post related SHORT" in t


def test_thumbnail_notes_are_documented():
    t = _flat(_read("SKILL.md"))
    assert "Use this frame for the thumbnail" in t and "`thumbnail` note" in t
    for c in ("BLENDER thumbs", "CLI captions", "CLI post"):
        assert c in t, c


def test_no_personal_post_format_in_the_plugin():
    root = ROOT
    for d in ("skills", "bsa"):
        for f in (root / d).rglob("*"):
            if f.is_file() and f.suffix in {".md", ".py", ".html", ".js", ".json", ".css"}:
                assert "\u25b6\ufe0f" not in f.read_text(encoding="utf-8", errors="ignore"), f


def test_first_run_asks_for_the_brand_kit_and_previews_it():
    t = _flat(_read("playbook/00-first-run.md"))
    assert "Do you have a brand? Colours, a logo, a font." in t and "BLENDER kit-sample" in t
    assert "settings check" in t and "brand.mode fresh" in t
    for style in ("realistic", "toy", "toon", "clay", "blueprint", "paper", "pencil"):
        assert style in t, style
    assert "brand.mode brand" in t and "brand.style" in t
    section = t.split("## 4.")[1].split("## 5.")[0]
    assert "goinsidemachines" not in section.lower() and "#FF6B00" not in section
    # the style sample is drawn in the user's own engine: ask for it, never pick one for them
    assert "--engine <their engine>" in section and "never pick one for them" in section
    assert "BLENDER style-sample --styles realistic,toy,toon,clay,blueprint,paper,pencil" in section


def test_look_and_brand_documents_the_kit():
    t = _flat(_read("playbook/look-and-brand.md"))
    for s in ("brand.style", "title_card", '"kind": "measure"', "brand.progress", "caption.draw", "settings check",
              "BLENDER kit-sample", "realistic, toy, toon, clay, blueprint, paper, pencil"):
        assert s in t, s


def test_build_doc_names_category_tags_and_the_kit_top_band():
    t = _flat(_read("playbook/06-build.md"))
    assert "category:<name>" in t and "kitlayout.top_band" in t


def test_checks_doc_says_covered_sees_kit_cards():
    assert "kit" in _flat(_read("playbook/07-checks.md")).split("`covered`")[1][:800]


STYLE_DOCS = ("look-and-brand.md", "06-build.md", "07-checks.md", "03-storyboard.md", "00-first-run.md")


def test_docs_name_every_style_and_set():
    from bsa import kit, setplace

    t = _flat(_read("playbook/look-and-brand.md"))
    for style in kit.STYLES:
        assert f"`{style}`" in t, style
    for name in setplace.SETS:
        assert f"`{name}`" in t, name
    for s in ("BLENDER look", "--style", "--set", "sets.json", "look.match_engine(scene)", "look.toy_water",
              "BLENDER style-sample"):
        assert s in t, s


def test_build_doc_calls_sets_and_styles_in_order():
    t = _read("playbook/06-build.md")
    base, dress, apply = t.index("sets.base("), t.index("sets.dress("), t.index("styles.apply(")
    assert base < dress < apply
    flat = _flat(t)
    for s in ("kit_for(load_settings(), short)", "setplace.resolve(kit.set, size_m, measuring)", "look.kit_material(",
              "lines:off", "set:floor", "key=["):
        assert s in flat, s
    import ast

    names = {}  # bsa.blender.look imports bpy: read its two tuples from the source
    for node in ast.parse((ROOT / "bsa" / "blender" / "look.py").read_text(encoding="utf-8")).body:
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", None) in ("KIT_KINDS", "PATTERNS"):
            names[node.targets[0].id] = [v for v in ast.literal_eval(node.value) if v]
    assert names["KIT_KINDS"] and names["PATTERNS"]
    for word in names["KIT_KINDS"] + names["PATTERNS"]:
        assert f"`{word}`" in flat, word


def test_checks_doc_has_set_clearance():
    t = _flat(_read("playbook/07-checks.md"))
    assert "`set_clearance`" in t and "replace=[" in t and "set:prop" in t
    assert "no geometry" in t


def test_skill_documents_the_look_note():
    t = _flat(_read("SKILL.md"))
    for s in ("`look`", "Use the", "BLENDER look SHORT --style", "--set", "render.engine"):
        assert s in t, s


def test_docs_never_pick_an_engine():
    for name in STYLE_DOCS + ("../SKILL.md",):
        t = _flat(_read(f"playbook/{name}")).lower()
        assert not re.search(r"defaults? to (eevee|cycles)|(eevee|cycles) by default|pick the engine", t), name
        assert not re.search(r"(suits|best in|use) (eevee|cycles) (for|with) (the )?(toon|toy|clay|paper|pencil|blueprint)",
                             t), name  # fmt: skip
    t = _flat(_read("playbook/look-and-brand.md"))
    assert "the user's choice" in t and "never changes it" in t


def test_docs_use_no_owner_values():
    banned = ("#F3F2EE", "#202326", "#FF6B00", "#B34700", "#FFC800", "#00A86B", "#0072CE", "#C9D3DD", "Fredoka",
              "#EEF1F4", "#1F2430", "#E4572E", "#3A7DDA")
    for name in STYLE_DOCS:
        t = _read(f"playbook/{name}")
        for value in banned:
            assert value.lower() not in t.lower(), (name, value)
    for name in ("01-interview.md", "05-narration.md"):  # the voice docs: nothing from the owner's own voice tool
        t = _read(f"playbook/{name}")
        for value in ("exaggeration", "tts-take", "tts-leadin"):
            assert value not in t.lower(), (name, value)


def test_readme_has_your_brand_with_both_images():
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "## Your brand" in text
    base = "https://raw.githubusercontent.com/goinsidemachines/blender-shorts-agent/media/"
    for name in ("brand-styles.jpg", "brand-sets.jpg"):
        assert base + name in text, name
    section = text.split("## Your brand", 1)[1].split("\n## ", 1)[0]
    flat = _flat(section)
    assert "your engine" in flat.lower() and "your choice" in flat.lower()
    for banned in ("goinsidemachines.com", "Inside Machines", "Mojood", "#F4B400"):
        assert banned not in section, banned


def test_interview_offers_make_it_like_that_one():
    t = _flat(_read("playbook/01-interview.md"))
    for word in ("CLI shorts", "--like", "skipped", "Started from", "last_finished"):
        assert word in t, word
    assert "never ask for or set the engine" in t.lower()


def test_narration_documents_take_mode_and_lead_in():
    t = _flat(_read("playbook/05-narration.md"))
    for word in ("take_mode", "paragraph", "{take}", "lead_in", "--take", "fallback", "sources"):
        assert word in t, word
    assert "NO_TAKE_PLACEHOLDER" not in t and "must accept an empty" in t


def test_skill_lists_shorts_like_and_take():
    t = _flat((ROOT / "skills" / "make-short" / "SKILL.md").read_text(encoding="utf-8"))
    for word in ("CLI shorts", "new-short", "--like", "narrate SHORT --line ID --take"):
        assert word in t, word
