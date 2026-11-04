"""Scene-check findings shared by the checks (inside Blender) and the CLI: the cap on listed overlapping pairs, and the
framing warnings a build keeps on its scenes.

An overlap finding names its pair of objects ("pair", on a Finding or a review/checks.json row). One pair can give
several findings (it interpenetrates and z-fights, or overlaps differently in two shots), so the cap counts pairs:
every finding of the first `most` pairs is listed, the rest are held back, and one more finding says how many pairs
were not listed. Every pair, listed or not, is also written grouped by name family (overlap_summary): "Tape left vs
Tooth left 01-22: 22 pairs, up to 1 cm, s01-s05". Pure Python, no Blender."""

from __future__ import annotations

import json
import re

from bsa.image_checks import Finding

CAPPED = "capped"  # a review/checks.json row's key holding the number of pairs its "not listed" finding stands for


def _get(item, key: str):
    return item.get(key) if isinstance(item, dict) else getattr(item, key, None)


def pair_of(item) -> tuple[str, ...] | None:
    """The pair of objects an overlap finding (a Finding or a row) names, or None."""
    if _get(item, "check") != "overlap":
        return None
    pair = _get(item, "pair")
    return tuple(pair) if pair else None


def cap_pairs(items: list, most: int) -> tuple[list, list, int]:
    """(the items with every finding of the first `most` overlapping pairs, the findings of the pairs after them, how
    many pairs those are). Items that name no pair are always kept. Order is kept."""
    pairs: list[tuple] = []
    for item in items:
        pair = pair_of(item)
        if pair is not None and pair not in pairs:
            pairs.append(pair)
    listed = set(pairs[:most])
    kept = [i for i in items if pair_of(i) is None or pair_of(i) in listed]
    hidden = [i for i in items if pair_of(i) is not None and pair_of(i) not in listed]
    return kept, hidden, max(0, len(pairs) - most)


def not_listed(count: int) -> Finding:
    """The finding that stands for `count` overlapping pairs held back by the cap."""
    return Finding("overlap", "error", None, None, f"{count} more overlapping pairs not listed: every pair is in "
                   "overlap_summary, grouped by name. Fix the ones above and check again")  # fmt: skip


def metres(d: float) -> str:
    """A length for a message: "1.2 m", "4 cm", "3 mm"."""
    if d >= 1.0:
        return f"{d:.1f} m"
    if d >= 0.01:
        return f"{d * 100:.0f} cm"
    return f"{d * 1000:.0f} mm"


FAMILY = re.compile(
    r"^(?P<stem>.*?\S)(?P<sep>[ ._-]?)(?P<idx>\d+)$|^(?P<lstem>.*?\S)(?P<lsep>[ ._-])(?P<lidx>[A-Za-z])$"
)
MOST_RUNS = 4  # index runs written on one side of a family line; the rest are counted


def family(name: str) -> tuple[str, str, str | None]:
    """(stem, separator, index) of an object name: a trailing digit run ("Tooth left 01", "Tooth01", "Cam.001") or a
    single trailing letter after a separator ("Tooth A") is its index; any other name ("M8 bolt", "Tape left", "12")
    is (name, "", None)."""
    m = FAMILY.match(name)
    if m is None:
        return name, "", None
    stem, sep, idx = m.group("stem", "sep", "idx") if m.group("stem") is not None else m.group("lstem", "lsep", "lidx")
    return (name, "", None) if stem.isdigit() else (stem, sep, idx)


def index_key(name: str) -> int | str:
    """The order of a name in its family: its number, its letter, or "" with no index."""
    idx = family(name)[2]
    return "" if idx is None else int(idx) if idx.isdigit() else idx


def _order(name: str) -> tuple:
    key = index_key(name)  # the rank keeps numbers, letters and no index apart, so they never compare
    return family(name)[0], 1 if isinstance(key, int) else 2 if key else 0, key


def _runs(items: list, follows) -> list[list]:
    runs: list[list] = []
    for item in items:
        if runs and follows(runs[-1][-1], item):
            runs[-1].append(item)
        else:
            runs.append([item])
    return runs


def _next(a: int | str, b: int | str) -> bool:
    if isinstance(a, int) and isinstance(b, int):
        return b == a + 1
    return isinstance(a, str) and isinstance(b, str) and len(a) == len(b) == 1 and ord(b) == ord(a) + 1


def _side(names: set[str]) -> str:
    """One side of a family: its one name, or "Tooth left 01-22", "Gear 2, 12", "Rod 1, 3, 5, 7 and 2 more"."""
    if len(names) == 1:
        return next(iter(names))
    ordered = sorted(names, key=_order)
    bare = [n for n in ordered if family(n)[2] is None]
    indexed = [n for n in ordered if family(n)[2] is not None]
    if not indexed:
        return ", ".join(bare)
    stem, sep, _ = family(indexed[0])
    runs = _runs(indexed, lambda a, b: _next(index_key(a), index_key(b)))
    shown = [family(r[0])[2] + (f"-{family(r[-1])[2]}" if len(r) > 1 else "") for r in runs[:MOST_RUNS]]
    rest = sum(len(r) for r in runs[MOST_RUNS:])
    text = stem + sep + ", ".join(shown) + (f" and {rest} more" if rest else "")
    return ", ".join([*bare, text])


def _in_order(shots: set[str], shot_order: list[str]) -> list[str]:
    """`shots` in shot_order, any not in it after, by name."""
    pos = {s: i for i, s in enumerate(shot_order)}
    return sorted(shots, key=lambda s: (s not in pos, pos.get(s, 0), s))


def _shot_spans(shots: list[str], shot_order: list[str]) -> str:
    """ "s01-s05, s07" for shots in shot_order (_in_order)."""
    pos = {s: i for i, s in enumerate(shot_order)}
    runs = _runs(shots, lambda a, b: a in pos and b in pos and pos[b] == pos[a] + 1)
    return ", ".join(r[0] if len(r) == 1 else f"{r[0]}-{r[-1]}" for r in runs)


def overlap_families(rows: list, shot_order: list[str]) -> list[dict]:
    """Every overlapping pair among `rows` (Findings or review/checks.json rows), grouped by the name families of its
    two objects: [{"a", "b" (each side as written), "pairs", "depth" (the deepest, 0 when none is measured), "real"
    (any pair interpenetrates or is too deep, not only z-fights), "shots", "line"}]. Real clips first, deepest first;
    then the rest; ties by line."""
    groups: dict[tuple[str, str], dict] = {}
    for row in rows:
        pair = pair_of(row)
        if pair is None or len(pair) != 2:
            continue
        a, b = pair if _order(pair[0]) <= _order(pair[1]) else (pair[1], pair[0])
        g = groups.setdefault((family(a)[0], family(b)[0]), {"a": set(), "b": set(), "pairs": set(), "depth": 0.0,
                                                             "real": False, "shots": set()})  # fmt: skip
        g["a"].add(a)
        g["b"].add(b)
        g["pairs"].add((a, b))
        g["depth"] = max(g["depth"], _get(row, "depth") or 0.0)
        kind = _get(row, "kind")
        if kind is None:  # a row from before findings had a kind: z-fighting says so in its message
            kind = "zfight" if "z-fight" in (_get(row, "message") or "") else "penetrate"
        g["real"] = g["real"] or kind in ("penetrate", "deep")
        g["shots"] |= {s for s in _get(row, "shots") or [_get(row, "shot")] if s}
    out = []
    for g in groups.values():
        n, side_a, side_b, shots = len(g["pairs"]), _side(g["a"]), _side(g["b"]), _in_order(g["shots"], shot_order)
        how = f"up to {metres(g['depth'])}" if g["depth"] > 0 else "interpenetrating" if g["real"] else "z-fighting"
        where = f", {_shot_spans(shots, shot_order)}" if shots else ""
        line = f"{side_a} vs {side_b}: {n} pair{'s' if n != 1 else ''}, {how}{where}"
        out.append({"a": side_a, "b": side_b, "pairs": n, "depth": g["depth"], "real": g["real"], "shots": shots,
                    "line": line})  # fmt: skip
    return sorted(out, key=lambda f: (not f["real"], -f["depth"] if f["real"] else 0.0, f["line"]))


def family_lines(rows: list, shot_order: list[str]) -> list[str]:
    """overlap_families as lines, one per family."""
    return [f["line"] for f in overlap_families(rows, shot_order)]


FRAMING_PROP = "bsa_framing"  # a scene's custom property: its framing warnings, JSON (bsa.blender.camera)


def framing_notes(scene) -> list[dict]:
    """The framing warnings `scene` keeps (bsa.blender.camera.frame_objects / frame_box): [{"camera", "key", "shot",
    "message"}]; [] when it keeps none or what it keeps is not that."""
    get = getattr(scene, "get", None)
    try:
        notes = json.loads((get(FRAMING_PROP) if callable(get) else None) or "[]")
    except (TypeError, ValueError):
        return []
    return [n for n in notes if isinstance(n, dict)] if isinstance(notes, list) else []
