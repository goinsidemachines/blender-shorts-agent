from bsa.findings import cap_pairs, not_listed, pair_of
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
