"""Hard rules: the user's own content rules for every video, stored in their words, checked where the plugin can."""

import copy
import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from bsa import rules, settings
from bsa.short import load_short

SAMPLE = Path(__file__).parent / "e2e" / "pendulum" / "source" / "short.json"


@pytest.fixture
def short():
    return load_short(SAMPLE)


def _s(*items):
    s = copy.deepcopy(settings.DEFAULTS)
    s["rules"] = [dict(r) for r in items]
    s["rules_asked"] = True
    return s


NO_PEOPLE = {"id": "r1", "text": "No people, no body parts, no stick figures", "kind": "no_humans"}
NO_MUSIC = {"id": "r2", "text": "No music", "kind": "no_music"}
BROAD = {"id": "r3", "text": "Keep it family friendly"}
NATURAL = {"id": "r4", "text": "Only natural sounds", "kind": "natural_sounds_only"}


# --------------------------------------------------------------------------- storing rules


def test_add_and_remove_keep_the_users_words_and_number_ids(bsa_home):
    s = settings.load_settings()
    assert s["rules"] == [] and s["rules_asked"] is False
    r = rules.add_rule("No people, no hands, no stick figures", kind="no_humans")
    assert r == {"id": "r1", "text": "No people, no hands, no stick figures", "kind": "no_humans"}
    broad = rules.add_rule("  Keep it family friendly  ")
    assert broad == {"id": "r2", "text": "Keep it family friendly"}  # stored verbatim (trimmed), no kind guessed
    part = rules.add_rule("No women shown uncovered", part_of="r2")
    assert part["part_of"] == "r2" and part["id"] == "r3"
    s = settings.load_settings()
    assert [r["id"] for r in s["rules"]] == ["r1", "r2", "r3"] and s["rules_asked"] is True
    rules.remove_rule("r1")
    assert rules.add_rule("No animals", kind="no_animals")["id"] == "r4"  # ids are never reused
    with pytest.raises(ValueError, match="r9"):
        rules.remove_rule("r9")


def test_bad_rules_are_refused(bsa_home):
    with pytest.raises(ValueError, match="no_humans"):  # the message lists the kinds there are
        rules.add_rule("No robots", kind="no_robots")
    with pytest.raises(ValueError, match="empty"):
        rules.add_rule("   ")
    with pytest.raises(ValueError, match="r7"):
        rules.add_rule("Detail", part_of="r7")
    assert settings.load_settings()["rules"] == []


def test_mark_none_records_the_answer(bsa_home):
    assert "rules" in settings.missing(settings.load_settings())
    rules.mark_none()
    s = settings.load_settings()
    assert s["rules"] == [] and "rules" not in settings.missing(s)


def test_missing_asks_for_rules_after_the_style_question():
    order = settings.missing(settings.DEFAULTS)
    assert order.index("brand.mode") < order.index("rules") < order.index("handle")
    old_user = {**copy.deepcopy(settings.DEFAULTS), "rules_asked": False, "rules": []}
    assert "rules" in settings.missing(old_user)  # existing users are asked once
    assert "rules" not in settings.missing({**old_user, "rules": [NO_MUSIC]})  # rules saved by hand count as asked


def test_summary_counts_the_rules():
    s = _s(NO_PEOPLE, NO_MUSIC)
    assert settings.summary_line(s).endswith(" · 2 hard rules")
    assert settings.summary_line(_s(NO_MUSIC)).endswith(" · 1 hard rule")
    assert "hard rule" not in settings.summary_line(_s())


def test_kinds_are_known_and_described():
    for kind in ("no_music", "no_humans", "no_animals", "no_faces", "no_text_on_screen_except_captions",
                 "natural_sounds_only", "no_brand_logos"):
        assert kind in rules.KINDS and rules.KINDS[kind].label


# --------------------------------------------------------------------------- the short's rules


def test_effective_rules_add_the_shorts_own(short):
    short.rules = [{"id": "t1", "text": "No red in this one"}]
    eff = rules.effective(_s(NO_PEOPLE), short)
    assert [(r["id"], r["scope"]) for r in eff] == [("r1", "every short"), ("t1", "this short")]


def test_every_rule_needs_a_rules_check_entry(short):
    errors, _ = rules.problems(short, _s(NO_PEOPLE, BROAD))
    assert len(errors) == 2 and "r1" in errors[0] and "rules_check" in errors[0]
    short.rules_check = {"r1": "Only the pendulum and the clock: no people", "r3": {"kept": "Nothing here is "
                         "against the rules you listed under it"}}  # fmt: skip
    errors, _ = rules.problems(short, _s(NO_PEOPLE, BROAD))
    assert errors == []
    short.rules_check["r3"] = {"kept": "  "}
    errors, _ = rules.problems(short, _s(NO_PEOPLE, BROAD))
    assert len(errors) == 1 and "r3" in errors[0]


def test_no_rules_no_problems(short):
    assert rules.problems(short, _s()) == ([], [])


def test_short_rules_must_be_well_formed_and_never_clash(short):
    short.rules = [{"id": "r1", "text": "dup"}, {"id": "t2", "text": ""}, {"id": "t3", "text": "x", "kind": "nope"},
                   "loose text"]  # fmt: skip
    short.rules_check = {"r1": "kept", "t2": "kept", "t3": "kept"}
    errors, _ = rules.problems(short, _s(NO_PEOPLE))
    joined = " ".join(errors)
    assert "r1" in joined and "t2" in joined and "nope" in joined and "loose text" in joined


def test_keyword_hits_are_warnings_until_answered(short):
    short.shots[0].picture = "A man's hand lets go of the brass bob."
    short.lines[3].text = "Clock faces kept time this way."
    short.rules_check = {"r1": "No people"}
    errors, warnings = rules.problems(short, _s(NO_PEOPLE))
    assert errors == []
    text = " ".join(warnings)
    assert "shot s01" in text and "'man'" in text and "'hand'" in text and "line l04" in text and "'faces'" in text
    short.rules_check = {"r1": {"kept": "Clock parts only", "cleared": {"face": "the clock's face, not a person's"}}}
    _, warnings = rules.problems(short, _s(NO_PEOPLE))
    text = " ".join(warnings)
    assert "line l04" not in text and "shot s01" in text  # 'faces' is cleared by 'face'; the man is not
    short.rules_check["r1"]["cleared"] = {"man": "x", "hand": "x", "face": "x"}
    assert rules.problems(short, _s(NO_PEOPLE)) == ([], [])


def test_cleared_words_must_say_why(short):
    short.rules_check = {"r1": {"kept": "ok", "cleared": {"hand": ""}}}
    errors, _ = rules.problems(short, _s(NO_PEOPLE))
    assert errors and "hand" in errors[0]


def test_words_are_found_inside_names():
    assert rules.tokens("HourHand.001") == ["hour", "hand"]
    assert rules.tokens("stick_figure-Arm2") == ["stick", "figure", "arm"]
    assert rules.hits("no_humans", "The handle turns") == []  # whole words only
    assert rules.hits("no_humans", "Two women and their children") == ["women", "children"]
    assert rules.hits("no_animals", "a dog chases the birds") == ["dog", "birds"]
    assert rules.hits("no_faces", "a smiley face") == ["smiley", "face"]
    assert rules.hits("no_brand_logos", "the Logo on the box") == ["logo"]


def test_music_words_in_sound_effects_and_assets(short):
    short.sfx[0].kind = "music_sting"
    short.rules_check = {"r2": "Sound effects only"}
    _, warnings = rules.problems(short, _s(NO_MUSIC))
    assert any("sfx" in w and "'music'" in w for w in warnings), warnings


def test_text_rule_warns_about_labels_badges_and_end_card(short):
    rule = {"id": "r5", "text": "No words on screen apart from the captions", "kind": "no_text_on_screen_except_captions"}
    short.rules_check = {"r5": "captions only"}
    s = _s(rule)
    s["handle"] = "me"
    _, warnings = rules.problems(short, s)
    text = " ".join(warnings)
    assert "shot s02" in text and "labels" in text and "shot s03" in text and "badge" in text and "end card" in text


def test_natural_sounds_rule_cannot_be_loosened_by_a_short(short):
    short.sound_policy = "any"
    short.rules_check = {"r4": "recordings only"}
    errors, _ = rules.problems(short, _s(NATURAL))
    assert errors and "sound_policy" in errors[0] and "r4" in errors[0]


def test_sound_policy_follows_the_rules(short):
    from bsa.audio import recorded

    assert recorded.sound_policy(short, _s()) == "any"
    assert recorded.sound_policy(short, _s(NATURAL)) == "natural-only"
    short.sound_policy = "any"
    assert recorded.sound_policy(short, _s(NATURAL)) == "natural-only"  # a short never loosens a hard rule
    short.sound_policy = ""
    short.rules = [{"id": "t1", "text": "natural only here", "kind": "natural_sounds_only"}]
    assert recorded.sound_policy(short, _s()) == "natural-only"


def test_panel_rows_carry_the_kept_note_and_cleared_words(short):
    short.rules_check = {"r1": {"kept": "Clock parts only", "cleared": {"hand": "the clock's hand"}}}
    rows = rules.panel_rows(rules.effective(_s(NO_PEOPLE, BROAD), short), short)
    assert rows[0]["kept"] == "Clock parts only" and rows[0]["cleared"] == {"hand": "the clock's hand"}
    assert rows[1]["kept"] is None and rows[1]["checked"] is False and rows[0]["checked"] is True


# --------------------------------------------------------------------------- scene names and assets


def test_scene_names_are_flagged_unless_cleared():
    short = SimpleNamespace(rules=[], rules_check={"r1": {"kept": "x", "cleared": {"hand": "clock hand"}}})
    eff = rules.effective(_s(NO_PEOPLE, NO_MUSIC), short)
    names = [("object", "HourHand", "HourHand"), ("object", "Worker", "Worker"),
             ("material", "Skin_Face", "Desk"), ("object", "Gear", "Gear")]  # fmt: skip
    found = rules.name_hits(eff, short, names)
    assert [(h["rule"]["id"], h["obj"], h["words"]) for h in found] == [("r1", "Worker", ["worker"]),
                                                                         ("r1", "Desk", ["skin", "face"])]
    assert "material 'Skin_Face'" in found[1]["message"] and "r1" in found[1]["message"]


def test_scene_scan_ignores_rules_without_scene_words():
    eff = rules.effective(_s(BROAD, NATURAL), SimpleNamespace(rules=[], rules_check={}))
    assert rules.name_hits(eff, None, [("object", "Person", "Person")]) == []
    assert rules.scene_key(eff, None) is None


def test_poly_haven_assets_are_refused_by_tags(short):
    eff = rules.effective(_s(NO_PEOPLE), short)
    assert rules.asset_conflicts(eff, "Wooden chair", ["furniture", "chair"]) == []
    hit = rules.asset_conflicts(eff, "Old mannequin", ["people", "clothing"])
    assert hit and hit[0]["rule"]["id"] == "r1" and "people" in hit[0]["words"]
    animals = rules.effective(_s({"id": "r2", "text": "No animals", "kind": "no_animals"}), short)
    assert rules.asset_conflicts(animals, "Toy horse", ["toys"])


def test_polyhaven_check_reads_the_assets_info():
    from bsa.assets import polyhaven

    def http(url):
        assert url.endswith("/info/mannequin_01")
        return io.BytesIO(json.dumps({"name": "Mannequin", "tags": ["human"], "categories": ["props"]}).encode())

    eff = rules.effective(_s(NO_PEOPLE), SimpleNamespace(rules=[], rules_check={}))
    with pytest.raises(ValueError, match="r1"):
        polyhaven.check_rules("mannequin_01", eff, http=http)
    polyhaven.check_rules("mannequin_01", [], http=http)  # no rule about people: fine


def test_polyhaven_search_hides_assets_the_rules_refuse():
    from bsa.assets import polyhaven

    listing = {"chair": {"name": "Chair", "tags": ["wood"], "categories": ["furniture"]},
               "statue": {"name": "Wooden chair statue", "tags": ["statue", "human"], "categories": ["decorative"]}}

    def http(url):
        return io.BytesIO(json.dumps(listing).encode())

    eff = rules.effective(_s(NO_PEOPLE), SimpleNamespace(rules=[], rules_check={}))
    found, hidden = polyhaven.search_with_rules("models", "chair", eff, http=http)
    assert [f["id"] for f in found] == ["chair"] and hidden == [{"id": "statue", "rule": "r1", "words": ["statue", "human"]}]


def test_word_hits_are_grouped_by_word_with_context_and_one_cleared_hint(short):
    """An engineering word ('face' of a tooth) said in many places is one warning naming each place with the words
    around it, and the hint to clear it comes once per rule. The rule itself is never narrowed."""
    short.lines[0].text = "Each tooth has a flat face that the pawl catches."
    short.lines[1].text = "The other face is a ramp, so the face slides past."
    short.shots[0].picture = "Close on the tooth face and the freehub body."
    short.rules_check = {"r1": "No people"}
    _, warnings = rules.problems(short, _s(NO_PEOPLE))
    face = [w for w in warnings if "'face'" in w]
    assert len(face) == 1, warnings
    w = face[0]
    assert "line l01" in w and "line l02" in w and "shot s01" in w and "3 places" in w
    assert '"…has a flat face that the pawl…"' in w  # the words around it
    assert sum("'body'" in x for x in warnings) == 1
    hints = [x for x in warnings if "rules_check.r1.cleared" in x]
    assert len(hints) == 1 and '"face"' in hints[0] and '"body"' in hints[0]
