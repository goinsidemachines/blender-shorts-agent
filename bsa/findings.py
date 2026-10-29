"""Scene-check findings shared by the checks (inside Blender) and the CLI: the cap on listed overlapping pairs.

An overlap finding names its pair of objects ("pair", on a Finding or a review/checks.json row). One pair can give
several findings (it interpenetrates and z-fights, or overlaps differently in two shots), so the cap counts pairs:
every finding of the first `most` pairs is listed, the rest are held back, and one more finding says how many pairs
were not listed. Pure Python, no Blender."""

from __future__ import annotations

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
    return Finding("overlap", "error", None, None, f"{count} more overlapping pairs not listed: fix the ones above "
                   "and check again")  # fmt: skip
