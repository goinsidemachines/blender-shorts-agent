"""review/checklist.json: the before-you-post checklist's ticks, {item id: done}, ticked on the page.

Only the ids of bsa.postpack.CHECK_IDS are kept; the copy of each item is built fresh (postpack.checklist_items),
so a tick never goes stale with the words."""

from __future__ import annotations

from pathlib import Path

from bsa.postpack import CHECK_IDS
from bsa.review import store

FILE = "review/checklist.json"


def path(sdir: Path) -> Path:
    return Path(sdir) / FILE


def _clean(data: dict) -> dict[str, bool]:
    return {k: v for k, v in data.items() if k in CHECK_IDS and isinstance(v, bool)}


def load(sdir: Path) -> dict[str, bool]:
    """The ticks ({} when none). Raises bsa.review.store.StoreDamaged when the file is damaged."""
    return _clean(store.read_json(path(sdir), {}, expect=dict))


def tick(sdir: Path, item: str, done: bool) -> dict[str, bool]:
    """Tick (or untick) one item; ValueError for an id not in postpack.CHECK_IDS or a `done` that is not a bool."""
    if not isinstance(item, str) or item not in CHECK_IDS:
        raise ValueError(f"unknown checklist item {item!r}; expected one of {', '.join(CHECK_IDS)}")
    if not isinstance(done, bool):
        raise ValueError("done must be true or false")  # noqa: TRY004 - a 400 for the client

    def change(data: dict) -> dict:
        return {**_clean(data), item: done}

    return _clean(store.update_json(path(sdir), {}, change, expect=dict))
