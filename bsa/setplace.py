"""Where a scene set's props may go (no Blender code): which set a short gets, each set's props, the spots each prop
may try (seeded by the short's slug, so a re-run gives the same set), the one clearance rule placement and the
set_clearance check share, and the short's saved arrangement, <short>/source/sets.json.

World units are metres, z up; the camera looks along +y, so "behind" the subject is larger y and the back wall is
the floor's far edge (y = floor y1). Sets hold only objects: tools, rulers, trees and clouds; never people, faces or
animals."""

from __future__ import annotations

import hashlib
import json
import math
import random
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from bsa import closing, jobstate
from bsa.layout import H, Rect, W, overlap_area
from bsa.review import decisions, store

SETS = ("workbench", "graph", "outdoor", "studio")
SETS_JSON = "sets.json"  # <short>/source/sets.json
TRIES = 32  # spots tried per prop before it is dropped (a framed subject leaves narrow room beside it)
SUBJECT_MARGIN_PX = 40  # a prop keeps this far off the subject's projected box
FOOTPRINT_GROW = 0.10  # floor props keep out of the subject's footprint grown by this share of its size, each side
OTHER_PROP = "against another prop"
NO_ROOM = "no room for it in the set"
FRONT_SETS = ("graph",)  # sets whose floor props may also lie in front of the subject (the approved graph sample)
FRONT_TURN = 0.3  # radians: a prop in front lies at most this far off across the view
FRONT_DEPTH = 0.6  # how deep the band in front is, x the subject's footprint (just in front, not by the camera)
SKY_BAND = (1.05, 1.6)  # a sky prop's height, x the subject's size: low enough to sit in the frame's upper third
DIMS_TOLERANCE = 1e-3  # a saved prop whose size is further than this share off the prop's size now is placed again


@dataclass(frozen=True)
class Prop:
    name: str
    shape: str
    zone: str  # "floor" | "wall" | "sky"
    dims: tuple[float, float, float]  # x, y, z extent in metres


@dataclass(frozen=True)
class Candidate:
    """A spot for a prop. (x, y, z): a floor prop's base centre (z = 0, on the floor), a wall or sky prop's centre."""

    prop: Prop
    x: float
    y: float
    z: float
    rot: float  # radians: about z on the floor, about the wall's normal on the wall


def auto_set(size_m: float, measuring: bool = False) -> str:
    """The set for a subject of this real size: outdoor for big structures, graph for measuring and explaining,
    workbench for small machines, else the plain studio."""
    if size_m >= 5:
        return "outdoor"
    if measuring:
        return "graph"
    if size_m <= 1.5:
        return "workbench"
    return "studio"


def resolve(kit_set: str, size_m: float, measuring: bool = False) -> str | None:
    """brand.set (or a short's style.set) as a set name: "auto" picks one, "none" is no set (None)."""
    if kit_set == "auto":
        return auto_set(size_m, measuring)
    if kit_set == "none":
        return None
    return kit_set


def _dims(*d: float) -> tuple[float, float, float]:
    return tuple(round(v, 6) for v in d)  # type: ignore[return-value]


def props(set_name: str, size_m: float) -> list[Prop]:
    """The set's props, sized for a subject `size_m` metres across. Studio has none."""
    s = min(max(size_m, 0.25), 4.0)
    if set_name == "workbench":
        return [
            Prop("toolbox", "toolbox", "floor", _dims(0.30 * s, 0.16 * s, 0.14 * s)),
            Prop("jar", "jar", "floor", _dims(0.08 * s, 0.08 * s, 0.12 * s)),
            Prop("small box 1", "box", "floor", _dims(0.10 * s, 0.10 * s, 0.08 * s)),
            Prop("small box 2", "box", "floor", _dims(0.08 * s, 0.12 * s, 0.06 * s)),
            Prop("hammer", "hammer", "wall", _dims(0.12 * s, 0.03 * s, 0.30 * s)),
            Prop("wrench", "wrench", "wall", _dims(0.06 * s, 0.02 * s, 0.24 * s)),
            Prop("pliers", "pliers", "wall", _dims(0.07 * s, 0.02 * s, 0.20 * s)),
        ]
    if set_name == "graph":
        return [
            Prop("ruler", "ruler", "floor", _dims(0.32 * s, 0.035 * s, 0.006 * s)),
            Prop("pencil", "pencil", "floor", _dims(0.20 * s, 0.012 * s, 0.012 * s)),
            Prop("set square", "set_square", "floor", _dims(0.18 * s, 0.13 * s, 0.006 * s)),
        ]
    if set_name == "outdoor":
        t, c = size_m * 0.4, size_m * 0.6
        trees = [Prop(f"tree {i}", "tree", "floor", _dims(0.6 * t, 0.6 * t, t)) for i in range(1, 5)]
        clouds = [Prop(f"cloud {i}", "cloud", "sky", _dims(c, 0.4 * c, 0.35 * c)) for i in range(1, 4)]
        return trees + clouds
    return []


def rng(slug: str, set_name: str, scene: str) -> random.Random:
    seed = int.from_bytes(hashlib.sha256(f"{slug}|{set_name}|{scene}".encode()).digest()[:8], "big")
    return random.Random(seed)


def _floor_regions(p: Prop, footprint, floor, front: bool = False) -> list[tuple[float, float, float, float]]:
    """Where a floor prop's centre may go: beside the subject (left, right, from its front edge back) and behind
    it, and with `front` (FRONT_SETS: a ruler lies in front of what it measures) in front of it too, inside the floor,
    its whole base outside the footprint grown by FOOTPRINT_GROW."""
    x0, y0, x1, y1 = footprint
    fx0, fy0, fx1, fy1 = floor
    gx, gy = (x1 - x0) * FOOTPRINT_GROW, (y1 - y0) * FOOTPRINT_GROW
    r = max(p.dims[0], p.dims[1]) / 2
    lo_x, hi_x, lo_y, hi_y = fx0 + r, fx1 - r, fy0 + r, fy1 - r
    side_y0 = max(lo_y, y0 - gy)
    regions = [
        (lo_x, side_y0, min(hi_x, x0 - gx - r), hi_y),  # left
        (max(lo_x, x1 + gx + r), side_y0, hi_x, hi_y),  # right
        (lo_x, max(lo_y, y1 + gy + r), hi_x, hi_y),  # behind
    ]
    if front:
        near = y0 - gy - r
        depth = FRONT_DEPTH * max(x1 - x0, y1 - y0)
        regions.append((lo_x, max(lo_y, near - depth), hi_x, min(hi_y, near)))  # just in front
    return [g for g in regions if g[2] > g[0] and g[3] > g[1]]


def _floor_spot(p: Prop, regions, r: random.Random) -> tuple[float, float, float]:
    areas = [(g[2] - g[0]) * (g[3] - g[1]) for g in regions]
    pick = r.uniform(0, sum(areas))
    g = regions[-1]
    for g, a in zip(regions, areas):
        if pick <= a:
            break
        pick -= a
    return r.uniform(g[0], g[2]), r.uniform(g[1], g[3]), 0.0


def candidates(set_name: str, slug: str, scene: str, size_m: float, footprint, floor) -> list[list[Candidate]]:
    """Per prop of `props(set_name, size_m)`, up to TRIES spots in its zone, in a fixed order from `rng`: floor
    props behind and beside `footprint` (x0, y0, x1, y1) inside `floor` (x0, y0, x1, y1), never in the footprint
    grown by 10%; wall props on the back wall plane (y = floor y1); sky props above the far floor edge. A prop with
    no room for its base gets no spots (placement drops it)."""
    fx0, _, fx1, fy1 = floor
    s = min(max(size_m, 0.25), 4.0)
    r = rng(slug, set_name, scene)
    out: list[list[Candidate]] = []
    for p in props(set_name, size_m):
        tries: list[Candidate] = []
        half_w = p.dims[0] / 2
        if p.zone == "floor":
            regions = _floor_regions(p, footprint, floor, set_name in FRONT_SETS)
            for _ in range(TRIES if regions else 0):
                x, y, z = _floor_spot(p, regions, r)
                turn = FRONT_TURN if y < footprint[1] else math.pi  # in front: lying across the view, as a ruler is
                tries.append(Candidate(p, x, y, z, r.uniform(-turn, turn)))
        elif p.zone == "wall" and fx1 - fx0 > p.dims[0]:
            z_lo = max(p.dims[2] / 2 + 0.02 * s, 0.35 * s)
            for _ in range(TRIES):
                x, z = r.uniform(fx0 + half_w, fx1 - half_w), r.uniform(z_lo, z_lo + 0.9 * s)
                tries.append(Candidate(p, x, fy1, z, r.uniform(-0.35, 0.35)))
        elif p.zone == "sky" and fx1 - fx0 > p.dims[0]:
            for _ in range(TRIES):
                x, z = r.uniform(fx0 + half_w, fx1 - half_w), r.uniform(*(k * size_m for k in SKY_BAND))
                tries.append(Candidate(p, x, fy1, z, 0.0))
        out.append(tries)
    return out


def covered_rects() -> list[Rect]:
    """The strips the Shorts player covers: its title and channel at the bottom 18%, its buttons at the right 12%."""
    b, r = closing.COVERED_BOTTOM, closing.COVERED_RIGHT
    return [Rect(0, H * (1 - b), W, H * b), Rect(W * (1 - r), 0, W * r, H)]


def text_rects(short, tl, shot, span, kit, font, frames, anchors: dict | None = None,
               heroes: dict | None = None) -> dict[int, list[tuple[str, Rect]]]:  # fmt: skip
    """{frame: [(kind, rect)]}: every card and caption the edit puts on screen at each frame (overlays.shot_boxes,
    with the kit's cards when there is a kit) plus ("covered", r) for each covered strip. `anchors`: {object: {"f0",
    "points", "category"?}} for the shot's near labels (each label's object top on screen; a near label without
    them is left out) and `heroes`: the shot's {"f0", "boxes"} (near labels keep off them), as overlays.shot_boxes
    takes them. The one rule placement and set_clearance share."""
    from bsa import overlays

    boxes = overlays.shot_boxes(short, tl, shot, span, anchors, heroes, frames, kit=kit, font=font)
    covered = [("covered", r) for r in covered_rects()]
    return {f: [(b[0], b[3]) for b in items] + covered for f, items in boxes.items()}


def _grow(r: Rect, m: float) -> Rect:
    return Rect(r.x - m, r.y - m, r.w + 2 * m, r.h + 2 * m)


def collision(prop: Rect, subjects: list[Rect], texts: list[tuple[str, Rect]], margin: float) -> str | None:
    """Why a prop's projected rect may not be there: "over the subject" when it meets any subject rect grown by
    `margin`, else "over the <kind>" for the first text rect it meets, else None. Meeting is a positive overlap."""
    if any(overlap_area(prop, _grow(s, margin)) > 0 for s in subjects):
        return "over the subject"
    for kind, r in texts:
        if overlap_area(prop, r) > 0:
            return f"over the {kind}"
    return None


def _at(per_frame, f) -> list:
    return per_frame.get(f, []) if isinstance(per_frame, Mapping) else per_frame


def _world_box(c: Candidate) -> tuple[float, float, float, float]:
    """A prop's box in its zone's plane: the floor (x, y; rotation-proof square) or the wall and sky (x, z)."""
    if c.prop.zone == "floor":
        r = max(c.prop.dims[0], c.prop.dims[1]) / 2
        return c.x - r, c.y - r, c.x + r, c.y + r
    w, h = c.prop.dims[0] / 2, c.prop.dims[2] / 2
    return c.x - w, c.z - h, c.x + w, c.z + h


def _clash(c: Candidate, kept: list[Candidate]) -> bool:
    a = _world_box(c)
    for k in kept:
        if k.prop.zone != c.prop.zone:
            continue
        b = _world_box(k)
        if a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]:
            return True
    return False


def place(cands: list[list[Candidate]], project: Callable[[Candidate, int], Rect | None], frames, subjects, texts,
          margin: float = SUBJECT_MARGIN_PX) -> tuple[list[Candidate], list[dict]]:  # fmt: skip
    """Per prop, the first candidate whose projected rect (`project(candidate, frame)`, None when off camera) is
    clear of the subjects and texts (each {frame: [...]} or one list for every frame) on every frame, and does not
    sit on a prop already kept. A prop with no clear spot is dropped, not squeezed: {"name", "why"}, `why` the
    reason most of its spots failed (the first such on a tie)."""
    kept: list[Candidate] = []
    dropped: list[dict] = []
    for tries in cands:
        whys: dict[str, int] = {}
        for c in tries:
            why = None
            for f in frames:
                rect = project(c, f)
                if rect is not None:
                    why = collision(rect, _at(subjects, f), _at(texts, f), margin)
                    if why:
                        break
            if why is None and _clash(c, kept):
                why = OTHER_PROP
            if why is None:
                kept.append(c)
                break
            whys[why] = whys.get(why, 0) + 1
        else:
            if tries:
                dropped.append({"name": tries[0].prop.name, "why": max(whys, key=whys.get)})
    return kept, dropped


def dropped_without_spots(set_name: str, size_m: float, cands: list[list[Candidate]]) -> list[dict]:
    """The props `candidates` gave no spots at all (no room for their base): {"name", "why"} for sets.json."""
    return [{"name": p.name, "why": NO_ROOM} for p, tries in zip(props(set_name, size_m), cands) if not tries]


def sets_path(sdir) -> Path:
    return Path(sdir) / "source" / SETS_JSON


def _read(sdir) -> tuple[dict | None, str | None]:
    """sets.json's data, or None and why it can't be read (None, None when there is none)."""
    path = sets_path(sdir)
    try:
        data = store.read_json(path, None, dict)
    except (store.StoreDamaged, OSError) as e:
        return None, str(e)
    if data is None:
        return None, None
    if not isinstance(data.get("scenes", {}), dict):
        return None, f"{path} is damaged (its scenes are not an object)"
    return data, None


def load(sdir) -> dict:
    """The short's sets.json ({} when it is missing or can't be read)."""
    data, _ = _read(sdir)
    return data or {}


def save_scene(sdir, scene: str, entry: dict, slug: str | None = None) -> None:
    """Save one scene's arrangement into sets.json, keeping the other scenes: locked, read, merged, written
    atomically. A damaged file is started over. `slug` defaults to the one saved, else the short folder's name.
    entry: {"set", "floor": [x0, y0, x1, y1], "props": [{"name", "at": [x, y, z], "rot", "dims"}],
    "dropped": [{"name", "why"}]}."""
    path = sets_path(sdir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with store.locked(path):
        try:
            data = store.read_json(path, {}, dict)
        except store.StoreDamaged:
            data = {}
        scenes = data.get("scenes")
        scenes = dict(scenes) if isinstance(scenes, dict) else {}
        scenes[scene] = entry
        new = {"slug": slug or data.get("slug") or Path(sdir).name, "scenes": scenes}
        jobstate.write_atomic(path, json.dumps(new, indent=2, ensure_ascii=False).encode("utf-8"))


def approved(sdir, current: int | None = None) -> bool:
    """The storyboard's latest live decision is an approval (one superseded or followed by changes does not count),
    and with `current` (the storyboard's version now) an approval of that version: one of an older storyboard
    approved content the user has not seen since."""
    try:
        d = decisions.latest(Path(sdir), "storyboard")
    except (store.StoreDamaged, OSError, KeyError, TypeError):
        return False
    return bool(d) and d.get("decision") == "approve" and (current is None or d.get("version") == current)


def _numbers(v, n: int) -> bool:
    return (isinstance(v, list) and len(v) == n
            and all(isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) for x in v))  # fmt: skip


def _problem(entry, scene: str, set_name: str, size_m: float) -> str | None:
    if not isinstance(entry, dict):
        return f"its scene {scene} is not an object"
    missing = [k for k in ("set", "floor", "props", "dropped") if k not in entry]
    if missing:
        return f"its scene {scene} has no {', '.join(missing)}"
    if entry["set"] != set_name:
        return f"its scene {scene} was set as {entry['set']!r}, not {set_name!r}"
    if not _numbers(entry["floor"], 4):
        return f"its scene {scene}'s floor is not four numbers"
    if not isinstance(entry["props"], list) or not isinstance(entry["dropped"], list):
        return f"its scene {scene}'s props or dropped is not a list"
    sizes = {p.name: p.dims for p in props(set_name, size_m)}
    names = set(sizes)
    for p in entry["props"]:
        if not isinstance(p, dict) or not {"name", "at", "rot", "dims"} <= set(p):
            return f"a prop in its scene {scene} is missing its name, at, rot or dims"
        if p["name"] not in names:
            return f"its scene {scene} has a prop {p['name']!r} the {set_name} set does not have"
        if not _numbers(p["at"], 3) or not _numbers(p["dims"], 3) or not _numbers([p["rot"]], 1):
            return f"its prop {p['name']!r} in scene {scene} has a bad position, rotation or size"
        want = sizes[p["name"]]
        if any(abs(a - b) > DIMS_TOLERANCE * max(abs(b), 1e-3) for a, b in zip(p["dims"], want)):
            return (f"its prop {p['name']!r} in scene {scene} was sized for another subject: its size "
                    f"{p['dims']} is not {list(want)} now")  # fmt: skip
    for d in entry["dropped"]:
        if not isinstance(d, dict) or not {"name", "why"} <= set(d):
            return f"a dropped prop in its scene {scene} is missing its name or why"
    return None


def saved_for(sdir, scene: str, set_name: str, size_m: float,
              current: int | None = None) -> tuple[dict | None, str | None]:  # fmt: skip
    """The scene's saved arrangement to reuse: only while the storyboard approval stands and the entry is valid for
    this set and these prop sizes. With `current` (the storyboard's version now) the approvals not applied yet that
    answered another version are retired first (decisions.supersede_stale) and only an approval of `current`
    counts. (None, None) when nothing is saved; (None, why) when one is saved but can't be trusted, so the scene is
    placed again and the caller warns with `why`."""
    data, why = _read(sdir)
    if why:
        return None, f"sets.json can't be read ({why}), so the props are placed again"
    if data is None:
        return None, None
    if current is not None:
        try:
            decisions.supersede_stale(Path(sdir), "storyboard", current)
        except (store.StoreDamaged, OSError) as e:
            return None, f"the storyboard's answers can't be read ({e}), so the props are placed again"
    if not approved(sdir, current):
        return None, "the storyboard is not approved (or its approval was superseded), so the props are placed again"
    scenes = data.get("scenes") or {}
    if scene not in scenes:
        return None, f"sets.json has no arrangement for scene {scene}, so its props are placed"
    problem = _problem(scenes[scene], scene, set_name, size_m)
    if problem:
        return None, f"sets.json can't be trusted ({problem}), so the props are placed again"
    return scenes[scene], None


def storyboard_version(sdir) -> int | None:
    """The storyboard's version now (bsa.review.snapshot.stage_version): None when it has no content yet."""
    from bsa.review import snapshot

    return snapshot.stage_version(Path(sdir), "storyboard")


def reusable(sdir, scene: str, set_name: str, size_m: float) -> tuple[dict | None, str | None]:
    """`saved_for` against the storyboard as it is now (`storyboard_version`), so an approval of content that has
    changed since is never trusted; when the version can't be worked out, (None, why): placed again."""
    try:
        current = storyboard_version(sdir)
    except Exception as e:  # noqa: BLE001 - a damaged short never stops a build: the props are placed again
        if sets_path(sdir).exists():
            return None, f"the storyboard's version can't be worked out ({e}), so the props are placed again"
        return None, None
    return saved_for(sdir, scene, set_name, size_m, current)
