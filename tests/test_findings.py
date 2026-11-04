from bsa.findings import cap_pairs, family, family_lines, not_listed, overlap_families, pair_of
from bsa.image_checks import Finding


def _row(a, b, shot, kind="interpenetrate"):
    return {"check": "overlap", "severity": "error", "obj": a, "frame": None, "message": f"{a} {b} {kind} {shot}",
            "pair": [a, b], "shot": shot}


def test_the_overlap_cap_counts_pairs_not_findings():
    """A pair found in two shots, or both interpenetrating and z-fighting, is one pair: the cap lists every finding
    of the first pairs and holds back the rest."""
    rows = [_row("A", "B", "s01"), _row("A", "B", "s02"), _row("A", "B", "s01", "z-fight"), _row("C", "D", "s01"),
            {"check": "names", "severity": "warning", "obj": "X.001", "frame": None, "message": "renamed"},
            _row("E", "F", "s02"), _row("G", "H", "s02")]
    kept, hidden, more = cap_pairs(rows, 2)
    assert [pair_of(r) for r in kept] == [("A", "B")] * 3 + [("C", "D"), None]
    assert [pair_of(r) for r in hidden] == [("E", "F"), ("G", "H")] and more == 2
    assert cap_pairs(rows, 4) == (rows, [], 0)
    assert "2 more overlapping pairs not listed" in not_listed(2).message


def test_a_finding_with_no_pair_is_never_held_back():
    found = [Finding("overlap", "error", None, None, "an old row"), Finding("pops", "error", "Ball", 4, "jumps")]
    assert cap_pairs(found, 0) == (found, [], 0)


def test_framing_notes_read_what_a_scene_keeps():
    import json
    import types

    from bsa.findings import FRAMING_PROP, framing_notes

    note = {"camera": "Cam", "key": 5, "shot": "s02", "message": "the fit did not settle"}
    assert framing_notes({FRAMING_PROP: json.dumps([note, "junk"])}) == [note]
    assert framing_notes({}) == [] and framing_notes({FRAMING_PROP: "not json"}) == []
    assert framing_notes({FRAMING_PROP: json.dumps({"a": 1})}) == []
    assert framing_notes(types.SimpleNamespace(name="Scene")) == []  # no custom properties at all


def _pair(a, b, shot, kind="deep", depth=0.01):
    return {"check": "overlap", "severity": "error", "obj": a, "frame": None, "message": f"{a} {b}", "pair": [a, b],
            "kind": kind, "depth": depth, "shots": [shot]}


def test_family_of_a_name():
    assert family("Tooth left 01") == ("Tooth left", " ", "01")
    assert family("Tooth01") == ("Tooth", "", "01")
    assert family("Cam.001") == ("Cam", ".", "001")
    assert family("Tooth A") == ("Tooth", " ", "A")
    assert family("Tooth_B") == ("Tooth", "_", "B")
    assert family("Tape left") == ("Tape left", "", None)


def test_family_keeps_inner_digits_and_writes_gaps():
    """Digits inside a name are part of it ("M8 bolt"); "Gear 2" and "Gear 12" are not a run; a name that is only
    digits has no index."""
    assert family("M8 bolt") == ("M8 bolt", "", None)
    assert family("12") == ("12", "", None)
    assert family("Gear 12") == ("Gear", " ", "12")
    rows = [_pair("Gear 2", "Case", "s01", "penetrate", 0.0), _pair("Case", "Gear 12", "s02", "penetrate", 0.0)]
    assert family_lines(rows, ["s01", "s02"]) == ["Case vs Gear 2, 12: 2 pairs, interpenetrating, s01-s02"]
    letters = [_pair("Pin", f"Tooth {c}", "s01", "zfight", None) for c in "ABDEG"]
    assert family_lines(letters, ["s01"]) == ["Pin vs Tooth A-B, D-E, G: 5 pairs, z-fighting, s01"]
    many = [_pair("Pin", f"Rod {i}", "s01") for i in range(1, 12, 2)]
    assert family_lines(many, ["s01"]) == ["Pin vs Rod 1, 3, 5, 7 and 2 more: 6 pairs, up to 1 cm, s01"]


def test_overlap_summary_groups_every_pair_with_real_clips_first():
    shots = ["s01", "s02", "s03", "s04", "s05"]
    rows = [_pair("Panel 1", "Frame", "s02", "zfight", None)]
    rows += [_pair("Tape left", f"Tooth left {i:02d}", shots[(i - 1) % 5]) for i in range(1, 23)]
    rows.append({**rows[1], "kind": "zfight", "depth": None})  # the same pair z-fighting too is still one pair
    rows.append({"check": "names", "severity": "warning", "obj": "X.001", "frame": None, "message": "renamed"})
    assert family_lines(rows, shots) == ["Tape left vs Tooth left 01-22: 22 pairs, up to 1 cm, s01-s05",
                                         "Frame vs Panel 1: 1 pair, z-fighting, s02"]
    first = overlap_families(rows, shots)[0]
    assert first["pairs"] == 22 and first["real"] and first["shots"] == shots
    assert family_lines([], shots) == []
