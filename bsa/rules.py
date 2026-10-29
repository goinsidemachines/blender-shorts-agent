"""Hard rules: content rules the user sets for every video ("no music", "no people", a dress code, a content limit).

Each rule is stored in the user's own words in the `rules` setting: {"id": "r1", "text": "...", "kind": ...,
"part_of": ...}. `kind` is optional and names one of KINDS, a rule the plugin knows how to check by itself (word
checks on the short, scene names in Blender, Poly Haven tags, the sound policy). A broad rule ("keep it family
friendly", "suitable for schools") is stored as written, without a kind: the plugin never interprets it. The user spells
out what it means for their videos as separate, concrete rules (each with `part_of` naming the broad one), and the
agent keeps them all.

A short may add rules of its own (`rules` in short.json, same shape, ids that are not the user's); it can never
remove or loosen one. The agent writes `rules_check` in short.json: rule id -> how this short keeps it, as text or
{"kept": text, "cleared": {word: why it is fine}} when a word check flagged something that does not break the rule
(the "hand" of a clock). Word checks are warnings, never proof: the agent and the user are the judges.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from bsa import settings


@dataclass(frozen=True)
class Kind:
    label: str
    words: frozenset = frozenset()  # words flagged in the short's lines, labels, pictures and sfx (warnings)
    asset_words: frozenset = frozenset()  # Poly Haven names, tags and categories refused by `assets plan`
    scene: bool = False  # BLENDER check also looks for `words` in object, mesh, material and collection names
    sound: str | None = None  # the sound_policy this rule forces


def _w(text: str) -> frozenset:
    return frozenset(text.split())


_HUMAN_BODY = _w("""person persons people human humans humanoid man men woman women boy boys girl girls child children
    kid baby babies adult guy lady ladies gentleman character figure figurine silhouette stickman stickfigure
    mannequin avatar crowd worker astronaut pilot driver player actor doll puppet body head face hand finger arm leg
    foot feet toe eye mouth nose ear hair skin torso skeleton skull portrait selfie statue bust""")
_ANIMALS = _w("""animal pet dog puppy cat kitten bird fish horse pony cow bull pig sheep lamb goat chicken hen duck goose
    geese mouse mice rat rabbit bunny lion tiger bear elephant monkey ape gorilla insect bug bee ant butterfly spider
    snake frog toad whale dolphin shark deer fox wolf wolves owl eagle parrot creature mammal reptile beast paw fur
    feather wing hoof""")
_FACES = _w("face facial eye eyes mouth nose lips smile smiley emoji portrait head mask selfie")
_PEOPLE_ASSETS = _w("""person people human humans humanoid character characters man men woman women child children
    mannequin statue bust figure figurine body skeleton anatomy portrait""")
_ANIMAL_ASSETS = _ANIMALS - _w("bug wing paw fur feather hoof")

_MUSIC = _w("music musical song melody tune soundtrack jingle piano guitar drum violin instrument singing singer chord")
_PEOPLE_LABEL = "No people: no bodies, body parts, faces, silhouettes or stick figures"
_LOGOS = _w("logo brand branded trademark emblem wordmark sponsor")

KINDS: dict[str, Kind] = {
    "no_music": Kind("No music (always on: shorts use sound effects only)", _MUSIC),
    "no_humans": Kind(_PEOPLE_LABEL, _HUMAN_BODY, _PEOPLE_ASSETS, scene=True),
    "no_animals": Kind("No animals", _ANIMALS, _ANIMAL_ASSETS, scene=True),
    "no_faces": Kind("No faces", _FACES, _FACES | _PEOPLE_ASSETS | _ANIMAL_ASSETS, scene=True),
    "no_text_on_screen_except_captions": Kind("No text on screen apart from the captions"),
    "natural_sounds_only": Kind("Natural sounds only (recordings, no synthetic effects)", sound="natural-only"),
    "no_brand_logos": Kind("No brand logos", _LOGOS, _LOGOS - _w("emblem wordmark sponsor"), scene=True),
}

_ID = re.compile(r"^r(\d+)$")


# --------------------------------------------------------------------------- the rules setting


def check_kind(kind: str | None) -> str | None:
    if kind in (None, ""):
        return None
    word = str(kind).strip().lower()
    if word not in KINDS:
        raise ValueError(f"{kind!r} is not a rule kind the plugin can check: use one of {', '.join(KINDS)}, or no "
                         "kind (the rule is kept as written and you keep it)")  # fmt: skip
    return word


def global_rules(s: dict) -> list[dict]:
    return [r for r in (s.get("rules") or []) if isinstance(r, dict)]


def add_rule(text: str, kind: str | None = None, part_of: str | None = None) -> dict:
    """Save a rule in the user's words (trimmed, never rewritten). ValueError for empty text, an unknown kind or
    a part_of that names no rule."""
    text = str(text or "").strip()
    if not text:
        raise ValueError("a rule can't be empty: give it in the user's own words")
    kind = check_kind(kind)
    s = settings.load_settings()
    have = global_rules(s)
    if part_of and part_of not in {r.get("id") for r in have}:
        raise ValueError(f"part_of {part_of!r} names no saved rule (rules: "
                         f"{', '.join(str(r.get('id')) for r in have) or 'none'})")  # fmt: skip
    used = [int(m.group(1)) for r in have if (m := _ID.match(str(r.get("id"))))]
    n = max(max(used, default=0) + 1, int(s.get("rules_next") or 1))
    rule = {"id": f"r{n}", "text": text}
    if kind:
        rule["kind"] = kind
    if part_of:
        rule["part_of"] = part_of
    s["rules"] = [*have, rule]
    s["rules_next"] = n + 1  # ids are never reused, so an old short's rules_check never names a new rule
    s["rules_asked"] = True
    settings.save_settings(s)
    return rule


def remove_rule(rule_id: str) -> dict:
    s = settings.load_settings()
    have = global_rules(s)
    gone = next((r for r in have if r.get("id") == rule_id), None)
    if gone is None:
        raise ValueError(f"there is no rule {rule_id!r} (rules: {', '.join(str(r.get('id')) for r in have) or 'none'})")
    used = [int(m.group(1)) for r in have if (m := _ID.match(str(r.get("id"))))]
    s["rules_next"] = max([int(s.get("rules_next") or 0), *(u + 1 for u in used)])
    s["rules"] = [r for r in have if r is not gone]
    settings.save_settings(s)
    return gone


def mark_none() -> None:
    """The user was asked and has no hard rules (for now): don't ask again."""
    s = settings.load_settings()
    s["rules_asked"] = True
    settings.save_settings(s)


def describe(rule: dict) -> str:
    kind = rule.get("kind")
    return f"{rule.get('id')} \"{rule.get('text')}\"" + (f" ({kind})" if kind else "")


# --------------------------------------------------------------------------- a short's rules


def effective(s: dict, short) -> list[dict]:
    """The user's rules and then the short's own, each with "scope": "every short" or "this short"."""
    out = [{**r, "scope": "every short"} for r in global_rules(s)]
    own = getattr(short, "rules", None) if short is not None else None
    out += [{**r, "scope": "this short"} for r in (own or []) if isinstance(r, dict)]
    return out


def forced_sound_policy(rules: list[dict]) -> str | None:
    for r in rules:
        k = KINDS.get(str(r.get("kind") or ""))
        if k and k.sound:
            return k.sound
    return None


def _entry(short, rule_id: str):
    rc = getattr(short, "rules_check", None) if short is not None else None
    return rc.get(rule_id) if isinstance(rc, dict) else None


def kept_note(entry) -> str | None:
    text = entry.get("kept") if isinstance(entry, dict) else entry
    return text.strip() if isinstance(text, str) and text.strip() else None


def cleared(entry) -> dict:
    c = entry.get("cleared") if isinstance(entry, dict) else None
    return {str(k): v for k, v in c.items()} if isinstance(c, dict) else {}


def tokens(text: str) -> list[str]:
    """Lower-case words of a caption or an object name: "HourHand.001" -> hour, hand; digits dropped."""
    text = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", str(text))
    return [t for t in re.split(r"[^A-Za-z]+", text.lower()) if t]


def _base(word: str, words: frozenset) -> str | None:
    if word in words:
        return word
    for suffix in ("es", "s"):
        if word.endswith(suffix) and word[: -len(suffix)] in words:
            return word[: -len(suffix)]
    return None


def _hits(words: frozenset, text: str, skip: set[str] = frozenset()) -> list[str]:
    out = []
    for t in tokens(text):
        base = _base(t, words)
        if base and base not in skip and t not in skip and t not in out:
            out.append(t)
    return out


def hits(kind: str, text: str) -> list[str]:
    """The words of `text` that a rule of `kind` flags, as they appear (whole words, plurals included)."""
    return _hits(KINDS[kind].words, text)


def _skip(entry, words: frozenset) -> set[str]:
    out = set()
    for w in cleared(entry):
        for t in tokens(w) or [w.lower()]:
            out.add(t)
            out.add(_base(t, words) or t)
    return out


def _places(short) -> list[tuple[str, str]]:
    """(where, text) for everything in a short a viewer sees or hears."""
    out = []
    for ln in short.lines:
        out.append((f"line {ln.id}", " ".join(x for x in (ln.text, ln.say) if isinstance(x, str))))
    for sh in short.shots:
        from bsa.short import label_text

        out.append((f"shot {sh.id}", " ".join([sh.title, sh.picture, *(label_text(lb) for lb in sh.labels),
                                               sh.badge or ""])))  # fmt: skip
    for fx in short.sfx:
        out.append((f"sfx {fx.kind!r} in shot {fx.shot}", fx.kind))
    for a in short.assets:
        out.append((f"asset {a.id}", f"{a.name} {a.id}"))
    return out


def _shape_errors(short, global_ids: set[str]) -> list[str]:
    errors = []
    own = getattr(short, "rules", None)
    if own is None:
        own = []
    if not isinstance(own, list):
        return [f"rules must be a list of {{\"id\", \"text\"}} rules, got {own!r}"]
    seen: set[str] = set()
    for r in own:
        if not isinstance(r, dict):
            errors.append(f"rules entry {r!r} must be {{\"id\": ..., \"text\": ...}}")
            continue
        rid = str(r.get("id") or "").strip()
        if not rid:
            errors.append(f"rules entry {r!r} needs an id")
        elif rid in global_ids:
            errors.append(f"short rule id {rid!r} is already one of the user's rules: give it its own id (t1, t2...)")
        elif rid in seen:
            errors.append(f"duplicate short rule id {rid!r}")
        seen.add(rid)
        if not isinstance(r.get("text"), str) or not r["text"].strip():
            errors.append(f"short rule {rid or r!r} needs its text (the user's words)")
        if r.get("kind") not in (None, "") and r.get("kind") not in KINDS:
            errors.append(f"short rule {rid!r} kind {r.get('kind')!r} is unknown: use one of {', '.join(KINDS)}")
    rc = getattr(short, "rules_check", None)
    if rc is not None and not isinstance(rc, dict):
        errors.append(f"rules_check must map rule ids to how the short keeps them, got {rc!r}")
    return errors


CONTEXT_WORDS = 3  # words shown either side of a flagged word
MAX_PLACES = 6  # places listed per flagged word; the rest are counted


def context(text: str, token: str, n: int = CONTEXT_WORDS) -> str:
    """The words around the first `token` (as tokens() splits it) in `text`: "…has a flat face that the pawl…"."""
    words = str(text).split()
    i = next((k for k, w in enumerate(words) if token in tokens(w)), None)
    if i is None:
        return token
    lo, hi = max(0, i - n), min(len(words), i + n + 1)
    snippet = " ".join(words[lo:hi]).strip(" .,;:!?")
    return ("…" if lo > 0 else "") + snippet + ("…" if hi < len(words) else "")


def _word_warnings(r: dict, kind: Kind, places, skip: set[str]) -> list[str]:
    """One warning per flagged word (its places, each with the words around it), then one hint to clear words that
    keep the rule. The rule's words are never narrowed: this only says the same thing with less noise."""
    by_word: dict[str, list[tuple[str, str, str]]] = {}
    for where, text in places:
        for t in _hits(kind.words, text, skip):
            by_word.setdefault(_base(t, kind.words) or t, []).append((where, t, context(text, t)))
    out = []
    for base, found in by_word.items():
        variants = list(dict.fromkeys(t for _, t, _ in found if t != base))
        also = f" (as {', '.join(repr(v) for v in variants)})" if variants else ""
        shown = "; ".join(f'{where} "{ctx}"' for where, _, ctx in found[:MAX_PLACES])
        more = f"; and {len(found) - MAX_PLACES} more" if len(found) > MAX_PLACES else ""
        n = len(found)
        out.append(f"hard rule {describe(r)}: {base!r}{also} in {n} place{'s' if n != 1 else ''}: {shown}{more}")
    if by_word:
        example = json.dumps({w: "why it keeps the rule here" for w in by_word}, ensure_ascii=False)
        out.append(f"hard rule {describe(r)}: change the places above where a word means what the rule forbids. "
                   f"Where it does not (a tooth's face, a clock's hand), say why once in "
                   f"rules_check.{r.get('id')}.cleared, e.g. {example}: that clears the word everywhere in this short."
                   + (" Clearing a word changes what the scene checks look for, so run BLENDER check SHORT again "
                      "after." if kind.scene else ""))  # fmt: skip
    return out


def problems(short, s: dict) -> tuple[list[str], list[str]]:
    """(errors, warnings) of the short against the hard rules. Errors: a rule with no rules_check entry, a
    malformed short rule or entry, a short that would loosen a rule. Warnings: words that may break a rule (an
    answer in rules_check "cleared" silences one), on-screen text under the captions-only rule."""
    errors, warnings = [], []
    errors += _shape_errors(short, {str(r.get("id")) for r in global_rules(s)})
    rules = effective(s, short)
    if not rules:
        return errors, warnings
    rc = getattr(short, "rules_check", None)
    rc = rc if isinstance(rc, dict) else {}
    ids = {str(r.get("id")) for r in rules}
    for r in rules:
        entry = rc.get(r.get("id"))
        if kept_note(entry) is None:
            errors.append(f"hard rule {describe(r)} has no rules_check entry: write in short.json rules_check how "
                          "this short keeps it")  # fmt: skip
        c = entry.get("cleared") if isinstance(entry, dict) else None
        if c is not None and (not isinstance(c, dict) or not all(isinstance(v, str) and v.strip() for v in c.values())):
            errors.append(f"rules_check {r.get('id')} cleared must map each word to why it keeps the rule, got {c!r}")
    for rid in sorted(set(rc) - ids):
        warnings.append(f"rules_check has an entry for {rid!r}, which is not a rule (any more): remove it")
    from bsa import closing

    card = closing.resolve(s, short)
    places = _places(short) + closing.places(card)
    for r in rules:
        kind = KINDS.get(str(r.get("kind") or ""))
        if kind is None:
            continue
        if kind.sound and getattr(short, "sound_policy", "") not in ("", kind.sound):
            errors.append(f"sound_policy {short.sound_policy!r} would loosen hard rule {describe(r)}: remove it "
                          f"(the rule makes it {kind.sound})")  # fmt: skip
        if kind.words:
            warnings += _word_warnings(r, kind, places, _skip(rc.get(r.get("id")), kind.words))
        if r.get("kind") == "no_text_on_screen_except_captions":
            for sh in short.shots:
                if sh.labels:
                    warnings.append(f"hard rule {describe(r)}: shot {sh.id} has on-screen labels: remove them")
                if sh.badge:
                    warnings.append(f"hard rule {describe(r)}: shot {sh.id} has a badge ({sh.badge!r}): ask the user "
                                    "(say it in the narration instead, or show the motion true to life)")  # fmt: skip
            if any(settings.end_card(s, short).values()):
                warnings.append(f"hard rule {describe(r)}: the end card shows text (the handle or its line): ask the "
                                "user whether the end card is allowed")  # fmt: skip
            if card is not None:
                warnings.append(f"hard rule {describe(r)}: the closing card shows text (the website or the user's "
                                "image): ask the user whether the closing card is allowed, or turn it off for this "
                                'short ("closing": false)')  # fmt: skip
    return errors, warnings


def panel_rows(rules: list[dict], short) -> list[dict]:
    """What the review pages show per rule: its words, scope, kind label and how this short keeps it."""
    rows = []
    for r in rules:
        entry = _entry(short, r.get("id"))
        kind = KINDS.get(str(r.get("kind") or ""))
        rows.append({"id": r.get("id"), "text": r.get("text"), "scope": r.get("scope"), "part_of": r.get("part_of"),
                     "kind": kind.label if kind else None, "kept": kept_note(entry), "cleared": cleared(entry),
                     "checked": kept_note(entry) is not None})  # fmt: skip
    return rows


# --------------------------------------------------------------------------- Blender scenes and assets


def _scene_rules(rules: list[dict]) -> list[dict]:
    return [r for r in rules if (k := KINDS.get(str(r.get("kind") or ""))) and k.scene]


def scene_key(rules: list[dict], short) -> list | None:
    """What the scene-name scan depends on (for the checks' freshness stamp), or None when no rule scans names."""
    found = _scene_rules(rules)
    if not found:
        return None
    return [[r.get("id"), r.get("kind"), sorted(cleared(_entry(short, r.get("id"))))] for r in found]


def name_hits(rules: list[dict], short, names) -> list[dict]:
    """Scene names that may break a rule: `names` is (what, name, object) such as ("material", "Skin", "Desk").
    One hit per rule and name: {"rule", "obj", "words", "message"}."""
    out = []
    for r in _scene_rules(rules):
        kind = KINDS[r["kind"]]
        skip = _skip(_entry(short, r.get("id")), kind.words)
        for what, name, obj in names:
            found = _hits(kind.words, name, skip)
            if found:
                where = f"{what} {name!r}" + (f" (on {obj!r})" if obj and obj != name else "")
                out.append({"rule": r, "obj": obj, "words": found,
                            "message": f"{where} has {', '.join(repr(w) for w in found)}, which hard rule "
                                       f"{describe(r)} may forbid: look at it, remove it, or say why it keeps the "
                                       f"rule in rules_check.{r.get('id')}.cleared"})  # fmt: skip
    return out


def asset_conflicts(rules: list[dict], name: str, tags) -> list[dict]:
    """Rules an outside asset breaks by its name, tags or categories: [{"rule", "words"}]."""
    text = " ".join([str(name or ""), *map(str, tags or [])])
    out = []
    for r in rules:
        kind = KINDS.get(str(r.get("kind") or ""))
        if kind and kind.asset_words:
            found = _hits(kind.asset_words, text)
            if found:
                out.append({"rule": r, "words": found})
    return out
