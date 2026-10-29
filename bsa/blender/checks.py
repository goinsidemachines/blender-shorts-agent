"""Scene checks run before a short is published: materials, textures, normals, shading, overlaps, framing,
grounding, pops, names, z-fighting inside one mesh, live Booleans and contact parts that follow different parents. All geometry is the evaluated (modifier-applied, animated) mesh at the frame checked.

Coverage: mesh objects get every check. Other renderable geometry (curves, text, surfaces, metaballs) gets the
materials and images checks (a grey text label, a pink curve). Instanced geometry (collection instances, geometry
node instances) gets the materials and images checks too, plus one "instances" warning per instancer, because
overlaps, normals, grounding and pops are not checked on instances: append the objects instead. An object tagged
"overlay" (a glow halo, a distance line, an arrow: not physical geometry) is left out of the overlap and z-fighting
checks (as either object of a pair), self_zfight, self_overlap, floating (neither checked nor counted as a support)
and pops; it still gets the materials, images, normals and faceted checks, and frame_edge like a hero.

Frames: materials, framing, grounding and every pair of objects are checked every `sample_every` frames plus each
shot's last frame. On every other frame, each object that moved or deformed since the frame before is checked
against every other object again (the sweep), so a part that passes through another between two samples is found;
pairs that did not change are not tested again, so static scenery costs nothing extra. A pair is measured again
only when one part moved against the other by more than POSE_TOL (0.25 mm) since a pose it was measured in: parts
that move together (a room swaying as a whole on its tower) are measured once. Pops use every frame.

Tolerances (scene units are metres):
- overlap: where two objects' faces intersect, they interpenetrate when two faces each cross the other's plane by
  more than OVERLAP_TOL (2 mm) on both sides, or when a vertex or face centre of an intersecting face lies more than
  OVERLAP_TOL behind the other object's surface (flush, axis-aligned overlaps). Surfaces that merely touch (a box
  resting on the floor, or sunk by up to 2 mm) are fine. Objects wholly inside another are not detected. Z-fighting
  is a pair of faces facing the same way (normals within 1 degree) that lie within the z-fight tolerance of each
  other and overlap with positive area. The tolerance is what can flicker where the pair is seen: COPLANAR_SAFETY
  (8) steps of depth precision at the shot camera's distance from the pair (a 24-bit depth buffer from the camera's
  clip start, or float32 rounding of the coordinates, whichever is coarser), never more than COPLANAR_TOL (0.1 mm)
  and never under COPLANAR_FLOOR (0.01 mm). So a small part 0.05 mm inside another, 30 cm from the camera, is fine,
  and the same gap 10 m away is z-fighting. Faces that share a plane exactly are always caught. The finding names
  both objects and their sizes, one place where the faces overlap and how far apart they are. Coplanar faces facing opposite ways are contact faces (a box's bottom on
  the floor's top) and are hidden, so they are not z-fighting; no extra allowance for "grounded" objects is needed.
  Each offending pair is one finding per shot naming the frames it overlaps in and how deep it goes. A pair that
  overlaps the same way in every frame of a shot (scenery that never moves) says "in every frame" and names no shot
  or frame, so the CLI lists it once with every shot it is in. Every finding of the first OVERLAP_MAX_FINDINGS pairs
  is listed (a pair counts once, however many findings it has); one more finding says how many pairs were not.
- contact: a pair tagged "contact:<other>" on either object, or a direct parent and child (not grandparents, not
  siblings under one parent or assembly), may overlap only up to a depth: by default the smaller of CONTACT_DEPTH
  (5 cm) and CONTACT_FRACTION (10%) of the thinner part's smallest dimension (its local bounding box times its
  scale), and never less than OVERLAP_TOL. "contact:<other>:<metres>" sets the depth for a part that is deep by
  design. "contact:<other>:moves" (with or without a depth: "contact:<other>:0.01:moves") says the two move against
  each other by design: they touch at some point and may part, slide, roll or hinge (pendulum balls that strike and
  swing apart, a pawl on a turning ratchet, a tyre on a scrolling floor). The depth limit holds as for any contact
  pair; only contact_parent leaves the pair alone. Deeper is an error naming the pair and the depth ("'Rod' is 4 cm inside 'Block'").
- depth: how far one object's surface reaches inside the other: points on its faces (the vertices plus a grid over
  each face, clipped to the other object's box, about half the thinner part's thickness apart) are tested against
  the other object and the deepest distance to its surface is kept, both ways round. Inside a closed mesh (every
  edge between two faces) a point is one that lies straight behind the nearest face, or, near edges and corners,
  one that an odd number of surface crossings separates from the outside (ray parity, majority of three rays; two
  crossings closer together than RAY_STEP times their distance from the world origin count as one hit). A flat
  open mesh (a floor plane) is one-sided: a point counts when it lies straight behind the nearest face. Any other
  open mesh (a shell with holes, a Boolean applied to a mesh that also held other parts) has no inside, so a depth
  into it can be any number: the finding says the mesh is not closed, with its count of open edges, instead.
- self_overlap: a hero mesh whose loose parts (connected pieces of one mesh) pass into each other deeper than the
  contact allowance: they read as one solid. Parts that only touch are fine. Detail sunk into a big body is how
  things are modelled (ledges set into a building's wall, a pin seated in a block), so a pair where one part's
  bounding box has at least SELF_BODY_RATIO (10) times the other's volume is excused: only parts of comparable size
  (two rods through each other) warn. A rigid hero is checked once. A hero whose mesh may deform (shape keys, an
  armature, a modifier that is not purely static) is checked at every sampled frame, and there a big-body pair
  warns too when its parts move against each other: it overlaps at some sampled frames and not others, or its
  depth changes by more than OVERLAP_TOL and SELF_DEPTH_CHANGE (10%).
- self_zfight: z-fighting inside one mesh, which the overlap check (pairs of objects) cannot see: two faces of
  different polygons facing the same way (normals within 1 degree), every corner of each within the z-fight
  tolerance (as for overlap: at most COPLANAR_TOL, 0.1 mm, less near the camera; measured in the world, so the
  object's scale counts) of the other's plane, overlapping by more than a sliver (the overlap's area over its
  longest side, its width, above that tolerance). Faces that only share an edge (a flat
  surface split into quads) do not overlap and are fine. Different materials is an error (the colour flickers),
  one material a warning (shading noise); one finding per mesh and pair of materials, with the overlap's area and
  centre. Coplanar faces facing opposite ways are not reported: inside one mesh they are almost always where two
  joined parts meet face to face (a box stacked on another), hidden inside the solid; a zero-thickness two-sided
  wall is not told apart from them. Checked on heroes and on every rendered mesh whose box is in the camera's view
  at a sampled frame, once per mesh and scale (again when a deforming mesh changes). Faces are bucketed by plane
  (normal and offset, with neighbouring buckets overlapping), then by a grid in the plane, so the cost follows the
  faces that share a plane with another face; at most PLANE_MAX_PAIRS overlapping face pairs are measured per mesh.
- boolean: a live Boolean modifier on a rendered mesh. It is evaluated again for every render and can come out
  differently (an EXACT cut on a hand-built or non-manifold mesh that shows in the stills and is gone in the final
  render): build the opening into the mesh, or apply the modifier in build.py.
- contact_parent: a "contact:<other>" pair whose two parts follow different moving ancestors (animated or
  constrained parents, grandparents...; the two parts themselves, and so their own animation, do not count), and
  whose meeting point (the centre of where their boxes meet at the first frame both render, carried along with
  each part) drifts apart by more than the pair's contact allowance during a shot. That is a part that lost its
  parent (a rebuilt constructor dropped it) and slides through or away from what it touches. A part that really
  moves against a fixed one through a moving parent (a foot stepping on a floor) warns too: it is a warning to
  read, not an error. A hinge (an arm swinging on a fixed pin) does not, as long as the pin runs through the
  meeting point. A pair tagged "contact:<other>:moves" is never reported: it moves against the other by design.
- faceted: at least FACETED_MIN_EDGES edges between 1 and 25 degrees that render as a crease: the rendered (corner)
  normals of the two faces jump by more than CREASE_DEG and by more than CREASE_FRACTION of the edge's own angle at
  either end of the edge (flat shading, a sharp edge without custom normals). Hardened bevels mark edges sharp but
  keep their custom normals continuous, or nearly (about 1 degree on a 15 degree edge of a curved part), so they
  render smooth and are not faceted. Edges under 1 degree are a flat surface, not a curved one.
- pops: a velocity discontinuity (bsa.pops): a move in one frame, or two in a row, over 8x the larger move on both
  sides of it (moves under 0.1 mm count as standing still) and over 5 mm or a tenth of the object's largest
  dimension. A teleport, a keyed jump or a one-frame snap pops; a ball that falls fast and then hangs almost still,
  or a still ball struck into motion, does not (its speed changes once and stays changed). The frame before and
  after each shot are read as references for its first and last moves, not judged (a cut may move anything).
- names: object names ending in ".001" (Blender renamed a duplicate: object names are one namespace across
  scenes) are one warning naming them; a contact tag naming no object in the scene, a hero tag naming a shot
  short.json does not have, or a near label naming an object its shot's scene does not have, is an error.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path

import bmesh
import bpy
import numpy as np
from bpy_extras.object_utils import world_to_camera_view
from mathutils import Vector
from mathutils.bvhtree import BVHTree

from bsa.blender.bake import is_hero, is_overlay, tags
from bsa.findings import cap_pairs, not_listed
from bsa.image_checks import Finding
from bsa.pops import find_pops
from bsa.short import label_near, label_text, load_short
from bsa.timeline import Timeline

__all__ = ["Finding", "OverlapFinding", "run_checks"]

FACETED_MIN_DEG = 1.0
FACETED_MAX_DEG = 25.0
FACETED_MIN_EDGES = 8
CREASE_DEG = 0.5
CREASE_FRACTION = 0.5  # a flat-shaded edge jumps by its whole angle; a hardened bevel's edge by a sliver of it
OVERLAP_TOL = 0.002
OVERLAP_MAX_FINDINGS = 20  # overlapping pairs listed; one more finding says how many were left out
FRAME_SPANS_SHOWN = 4
CONTACT_DEPTH = 0.05
CONTACT_FRACTION = 0.10
DEPTH_MIN_STEP = 0.0005
DEPTH_MAX_SUB = 48  # grid divisions per face edge when sampling a face for depth
DEPTH_MAX_POINTS = 2000  # sample points per object per pair (spread evenly when there are more)
SELF_MAX_PARTS = 2000  # heroes with more loose parts than this (a particle-like mesh) are not self-checked
SELF_BODY_RATIO = 10.0  # a part sunk into one with this many times its box volume is detail, not a joined solid
SELF_DEPTH_CHANGE = 0.10  # a deforming hero's pair moves when its depth changes by more than this fraction
COPLANAR_TOL = 0.0001  # the most two faces may be apart and still z-fight (a far camera, a coarse depth buffer)
COPLANAR_FLOOR = 0.00001  # ... and the least: faces this close always count, however near the camera
COPLANAR_SAFETY = 8.0  # faces closer than this many steps of depth precision at the camera's distance may z-fight
DEPTH_BITS = 24  # a rasteriser's depth buffer (EEVEE, the viewport); a ray tracer's float32 is finer than this
COPLANAR_COS = math.cos(math.radians(1.0))
PLANE_CELL = 0.25  # normal components are bucketed this coarsely to find faces that may share a plane...
PLANE_MARGIN = 2 * math.sin(math.radians(0.5)) * 1.01  # ...and a face within this of a bucket's edge joins both
PLANE_BIG_CELLS = 64  # faces covering more grid cells than this are tested against their plane group directly
PLANE_MAX_PAIRS = 20000  # overlapping face pairs measured per mesh (the area of a mesh with more is a lower bound)
GROUND_TOL = 0.002
GROUND_RAY_LIFT = 0.01  # rays start this far above the lowest point, so a support sunk into slightly is found
GROUND_MAX_RAYS = 64
POSE_TOL = 2.5e-4  # a pair's result is reused while its parts stay this close to where they were when measured
POSE_KEPT = 8  # poses remembered per pair
RAY_STEP = 5e-7  # a ray restarts this fraction of its distance from the origin past each hit (about 8 float32 steps)
OTHER_GEOMETRY = ("CURVE", "FONT", "SURFACE", "META")  # rendered, checked for materials and images only
RENAMED = re.compile(r"\.\d{3,}$")
RENAMED_LISTED = 12  # renamed objects named in the one names warning
# Modifiers that give the same mesh every frame unless their settings are animated (a changed mesh is still found at
# the next sampled frame, and the object is evaluated every frame from then on). Any other modifier, shape keys or
# an armature parent may deform the mesh, so such objects are evaluated on every frame of the sweep.
STATIC_MODIFIERS = {
    "BEVEL", "SUBSURF", "SOLIDIFY", "MIRROR", "TRIANGULATE", "WELD", "EDGE_SPLIT", "WEIGHTED_NORMAL", "DECIMATE",
    "REMESH", "SCREW", "SKIN", "WIREFRAME", "SMOOTH", "LAPLACIANSMOOTH", "MULTIRES", "NORMAL_EDIT",
}  # fmt: skip
_RAYS = [Vector(d).normalized() for d in ((0.5773, 0.5774, 0.5775), (-0.4472, 0.7746, 0.4472), (0.8165, -0.2, -0.54))]


@dataclass
class OverlapFinding(Finding):
    """An overlap finding with the pair of objects it is about (the cap on listed pairs counts pairs)."""

    pair: tuple[str, str] = ("", "")


@dataclass
class _Local:
    """One object's evaluated mesh in its own coordinates."""

    co: np.ndarray  # (nv, 3)
    idx: np.ndarray  # (n, 3) triangle corners
    key: int
    closed: bool
    mat: np.ndarray | None = None  # (n,) material slot index per triangle
    poly: np.ndarray | None = None  # (n,) the polygon each triangle comes from
    radius: float = 0.0  # the farthest vertex from the object's origin
    open_edges: int = 0  # edges not shared by exactly two triangles
    flat: bool = False  # every vertex within OVERLAP_TOL of one plane (a floor, a wall plane)


@dataclass
class _Geo:
    """One object's evaluated triangles in world space at one frame."""

    name: str
    tris: np.ndarray  # (n, 3, 3)
    lo: np.ndarray
    hi: np.ndarray
    key: int
    thin: float = 0.0  # smallest dimension of its own bounding box, in metres
    closed: bool = False
    local_key: int = 0
    m: np.ndarray | None = field(default=None, repr=False)
    radius: float = 0.0  # the farthest vertex from the object's origin, in its own coordinates
    open_edges: int = 0
    flat: bool = False
    _tree: BVHTree | None = field(default=None, repr=False)
    _bounds: tuple[np.ndarray, np.ndarray] | None = field(default=None, repr=False)

    @property
    def tree(self) -> BVHTree:
        if self._tree is None:
            self._tree = _bvh(self.tris)
        return self._tree

    def in_box(self, lo: np.ndarray, hi: np.ndarray) -> np.ndarray:
        """Per triangle: does its bounding box meet the box [lo, hi]?"""
        if self._bounds is None:
            self._bounds = (self.tris.min(1), self.tris.max(1))
        tlo, thi = self._bounds
        return np.all(tlo <= hi, axis=1) & np.all(thi >= lo, axis=1)


@dataclass
class _Contact:
    """How two objects meet: {"zfight", "penetrate"} for an unexempt pair, and the deepest reach of one inside the
    other (0 when not measured or not inside)."""

    kinds: set
    depth: float = 0.0
    inner: str | None = None
    outer: str | None = None
    zfight_at: tuple | None = None  # (world point, gap in metres) of one pair of z-fighting faces
    outer_open: int = 0  # the outer object is open and not flat (its open edges): the depth means nothing


def run_checks(scene, tl: Timeline, sample_every: int = 5, short=None, progress=None,
               rules: list[dict] | None = None, cap: bool = True) -> list[Finding]:  # fmt: skip
    """Run every check over each shot (every `sample_every` frames plus the shot's first and last; overlaps of moving
    objects and pops use every frame). `short` (the Short, for its near labels and shot ids) defaults to the
    short.json next to the open .blend (<short>/blend/short.blend), when there is one. Errors come first.
    scene.frame_current is restored afterwards. `progress(shot id, frames done, frames in the shot)` is called after
    each frame when given (it never changes what is found). `rules`: the hard rules (bsa.rules.effective); object,
    mesh, material, collection and library names with words a rule may forbid are "hard_rule" warnings. `cap`: list
    the findings of the first OVERLAP_MAX_FINDINGS overlapping pairs only (the CLI caps over every shot instead)."""
    original = scene.frame_current
    run = _Run(scene)
    short = _short_beside_blend() if short is None else short
    try:
        if scene.world and scene.world.node_tree:
            for problem in _image_problems(scene.world.node_tree):
                run.add("images", "error", None, None, f"world: {problem}", key=("world", problem))
        run.names(tl, short)
        if rules:
            run.hard_rules(short, rules)
        for shot in tl.shots:
            run.shot(shot, sample_every, progress)
        run.finish_self()
    finally:
        scene.frame_set(original)
    found = _cap_overlaps(run.findings) if cap else run.findings
    return sorted(found, key=lambda f: f.severity != "error")


def _short_beside_blend():
    """The short.json of the short whose blend/short.blend is open, or None."""
    if not bpy.data.filepath:
        return None
    path = Path(bpy.data.filepath).resolve().parent.parent / "source" / "short.json"
    try:
        return load_short(path) if path.is_file() else None
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _cap_overlaps(findings: list[Finding]) -> list[Finding]:
    kept, _, more = cap_pairs(findings, OVERLAP_MAX_FINDINGS)
    return [*kept, not_listed(more)] if more else kept


class _Run:
    def __init__(self, scene):
        self.scene = scene
        self.view_layer = _view_layer(scene)
        self.depsgraph = _depsgraph(scene, self.view_layer)
        self.findings: list[Finding] = []
        self.seen: set = set()
        self.static_done: set[str] = set()
        self.material_done: set[str] = set()
        self.pair_cache = _PairCache()
        self.local: dict[str, _Local] = {}  # each object's last evaluated mesh, to place it again when only it moved
        self.deforming: set[str] = set()  # objects whose mesh may change from frame to frame
        self.cur: dict[str, _Geo] = {}  # the geometry at the frame before
        self.changed: dict[int, set[str]] = {}  # frame: objects that moved, deformed, appeared or vanished
        self.hits: dict[tuple[str, str, str], dict] = {}  # (a, b, kind): {"frames": {frame: _Contact}, "note"}
        self.self_done: set[str] = set()  # rigid heroes whose loose parts were checked
        self.coplanar_done: set[tuple] = set()  # (object, mesh, shape of its transform) checked for self_zfight
        self.self_track: dict[str, dict] = {}  # deforming heroes: {"samples": n, "pairs": {key: [(frame, res, body)]}}
        self.contact_pairs = _contact_pairs(scene)  # (a, b, what moves a, what moves b) that follow different parents
        self.drift: dict[tuple[str, str], dict] = {}  # per shot: where each such pair meets, and how far it drifts
        self.overlay = {o.name for o in scene.objects if is_overlay(o)}  # glows, guide lines: not physical

    def add(self, check, severity, obj, frame, message, key=None, pair=None):
        key = (check, obj, message) if key is None else (check, *key)
        if key not in self.seen:
            self.seen.add(key)
            if pair is None:
                self.findings.append(Finding(check, severity, obj, frame, message))
            else:
                self.findings.append(OverlapFinding(check, severity, obj, frame, message, pair))

    def shot(self, shot, every, progress=None):
        sampled = set(range(shot.f0, shot.f1 + 1, max(1, every))) | {shot.f1}
        positions: dict[str, list[tuple[int, np.ndarray]]] = {}
        views: dict[str, list[tuple[int, str]]] = {}
        self.cur, self.changed, self.hits, self.drift = {}, {}, {}, {}
        sizes: dict[str, float] = {}
        self.track_positions(shot.f0 - 1, positions)  # how each object came into the shot: a reference for pops
        for frame in range(shot.f0, shot.f1 + 1):
            self.scene.frame_set(frame)
            self.depsgraph.update()  # a scene that is not the context scene is not evaluated by frame_set alone
            objs = _render_meshes(self.scene, self.view_layer)
            for obj in objs:
                moved = obj.evaluated_get(self.depsgraph).matrix_world.translation
                positions.setdefault(obj.name, []).append((frame, np.array(moved)))
                sizes.setdefault(obj.name, max(obj.dimensions))
            if frame in sampled:
                self.sample(frame, objs, views, shot.id)
                self.surfaces(frame, _render_meshes(self.scene, self.view_layer, OTHER_GEOMETRY))
                self.instances(frame)
            else:
                self.sweep(frame, objs)
            self.track_contacts(frame)
            if progress is not None:
                progress(shot.id, frame - shot.f0 + 1, shot.f1 - shot.f0 + 1)
        self.track_positions(shot.f1 + 1, positions)  # and how it leaves it
        self.scene.frame_set(shot.f1)
        self.depsgraph.update()
        self.pops(shot, positions, sizes)
        self.report_overlaps(shot, sampled)
        self.report_contacts(shot)
        for name, states in views.items():
            if any(s != "out" for _, s in states):  # the hero is in this shot: it must stay inside the frame
                bad = next(((f, s) for f, s in states if s != "in"), None)
                if bad:
                    where = "partly outside" if bad[1] == "partial" else "outside"
                    what = "overlay" if name in self.overlay else "hero"
                    self.add("frame_edge", "warning", name, bad[0], f"{what} is {where} the camera frame in shot "
                             f"{shot.id!r}", key=(name, shot.id))  # fmt: skip

    def track_positions(self, frame, positions):
        """Positions at a frame just outside a shot, kept with the shot's own for the pops check only."""
        self.scene.frame_set(frame)
        self.depsgraph.update()
        for obj in _render_meshes(self.scene, self.view_layer):
            moved = obj.evaluated_get(self.depsgraph).matrix_world.translation
            positions.setdefault(obj.name, []).append((frame, np.array(moved)))

    def geo(self, obj) -> _Geo | None:
        """The object's geometry now, from its evaluated mesh (noting a mesh that changed since it was last read)."""
        local = _local_mesh(obj, self.depsgraph)
        if local is None:
            self.local.pop(obj.name, None)
            return None
        old = self.local.get(obj.name)
        if _may_deform(obj) if old is None else old.key != local.key:
            self.deforming.add(obj.name)
        self.local[obj.name] = local
        return _place(obj.name, local, obj.evaluated_get(self.depsgraph).matrix_world)

    def note_changes(self, frame, geos) -> set[str]:
        changed = set(self.cur) ^ set(geos)
        changed |= {n for n, g in geos.items() if n in self.cur and self.cur[n].key != g.key}
        self.changed[frame] = changed
        self.cur = geos
        return changed

    def sample(self, frame, objs, views, shot_id=None):
        depsgraph = self.depsgraph
        cam = self.scene.camera
        geos = {}
        for obj in objs:
            geo = self.geo(obj)
            if geo is None:
                continue
            geos[obj.name] = geo
            state = _view_state(self.scene, cam, geo) if cam else "in"
            if cam and (is_hero(obj, shot_id) or obj.name in self.overlay):  # an overlay stays in frame too
                views.setdefault(obj.name, []).append((frame, state))
            if obj.name not in self.static_done:
                self.static_done.add(obj.name)
                self.static(obj, depsgraph, frame)
            self.material(obj.name, obj.evaluated_get(depsgraph).material_slots, frame, state)
            if (state != "out" or is_hero(obj)) and obj.name not in self.overlay:
                self.coplanar(obj, geo, frame)
        self.note_changes(frame, geos)
        self.overlaps(frame, objs, geos)
        for obj in objs:
            if "grounded" in tags(obj) and obj.name in geos and obj.name not in self.overlay:
                self.floating(frame, obj.name, geos)
            if obj.name in geos and is_hero(obj) and obj.name not in self.overlay:
                self.loose_parts(obj.name, geos[obj.name], frame)

    def sweep(self, frame, objs):
        """A frame between samples: place again only what moved or deformed, and check only its pairs."""
        geos = {}
        for obj in objs:
            old = self.cur.get(obj.name)
            if old is None or obj.name in self.deforming or obj.name not in self.local:
                geo = self.geo(obj)
            else:
                m = np.array(obj.evaluated_get(self.depsgraph).matrix_world, dtype=np.float64)
                geo = old if np.array_equal(m, old.m) else _place(obj.name, self.local[obj.name], m)
            if geo is not None:
                geos[obj.name] = geo
        changed = self.note_changes(frame, geos) & set(geos)
        if not changed:
            return
        self.overlaps(frame, objs, geos, only=changed)
        for obj in objs:
            if obj.name in changed and "grounded" in tags(obj) and obj.name not in self.overlay:
                self.floating(frame, obj.name, geos)

    def images(self, name, slots, frame):
        for slot in slots:
            if slot.material and slot.material.node_tree:
                for problem in _image_problems(slot.material.node_tree):
                    self.add("images", "error", name, frame, problem, key=(slot.material.name, problem))

    def material(self, name, slots, frame, state):
        if state != "out" and name not in self.material_done:
            self.material_done.add(name)
            if not any(slot.material for slot in slots):
                self.add("materials", "error", name, frame, "geometry in view has no material (renders grey)")

    def surfaces(self, frame, objs):
        """Curves, text, surfaces and metaballs: materials and images (geometry checks are for meshes)."""
        cam = self.scene.camera
        for obj in objs:
            geo = _world_geo(obj, self.depsgraph)
            if geo is None:  # a curve without bevel or extrusion renders nothing
                continue
            slots = obj.evaluated_get(self.depsgraph).material_slots
            if obj.name not in self.static_done:
                self.static_done.add(obj.name)
                self.images(obj.name, slots, frame)
            self.material(obj.name, slots, frame, _view_state(self.scene, cam, geo) if cam else "in")

    def instances(self, frame):
        """Instanced geometry: materials and images of each instanced object, one warning per instancer."""
        cam = self.scene.camera
        for inst in self.depsgraph.object_instances:
            if not inst.is_instance or inst.object.type not in ("MESH", *OTHER_GEOMETRY) or inst.parent is None:
                continue
            ob, owner = inst.object, inst.parent
            if ob.original == owner.original:  # a curve or text object's own evaluated mesh, already checked
                continue
            owner_name = owner.original.name
            name = f"{owner_name}/{ob.original.name}"
            self.add("instances", "warning", owner_name, frame, "instanced geometry is checked for materials and "
                     "images only (not overlaps, normals, grounding or pops): append the objects instead of "
                     "instancing a collection", key=(owner_name,))  # fmt: skip
            slots = ob.material_slots
            if name not in self.static_done:
                self.static_done.add(name)
                self.images(name, slots, frame)
            if name in self.material_done:
                continue
            m = np.array(inst.matrix_world, dtype=np.float64)
            corners = np.array([tuple(c) for c in ob.bound_box], dtype=np.float64) @ m[:3, :3].T + m[:3, 3]
            box = _Geo(name, np.empty((0, 3, 3)), corners.min(0), corners.max(0), 0)
            self.material(name, slots, frame, _view_state(self.scene, cam, box) if cam else "in")

    def static(self, obj, depsgraph, frame):
        ev = obj.evaluated_get(depsgraph)
        self.images(obj.name, ev.material_slots, frame)
        self.booleans(obj, frame)
        mesh = ev.to_mesh()
        bm = bmesh.new()
        try:
            self.faceted(obj, mesh, frame)
            bm.from_mesh(mesh)
            self.normals(obj, bm, frame)
        finally:
            bm.free()
            ev.to_mesh_clear()

    def booleans(self, obj, frame):
        for mod in obj.modifiers:
            if mod.type == "BOOLEAN" and mod.show_render:
                solver = getattr(mod, "solver", "")
                how = f" ({solver} solver)" if solver else ""
                self.add("boolean", "warning", obj.name, frame, f"live Boolean modifier {mod.name!r}{how}: it is "
                         "evaluated again for every render and can come out differently (an opening that shows in "
                         "the stills can vanish in the final render). Build the opening into the mesh itself (leave "
                         "out the faces, close the edges), or apply the modifier in build.py and check the face count",
                         key=(obj.name, mod.name))  # fmt: skip

    def normals(self, obj, bm, frame):
        open_edges = sum(1 for e in bm.edges if not e.is_manifold)
        if bm.faces and not open_edges:
            mixed = sum(1 for e in bm.edges if not e.is_contiguous)
            if mixed:
                self.add("normals", "error", obj.name, frame,
                         f"some faces are flipped ({mixed} edges join faces wound opposite ways)")  # fmt: skip
            else:
                shells = _shells(bm)
                bad = [sh for sh in shells if sh.volume < 0 and not _cavity(sh, shells)]
                if bad:
                    self.add("normals", "error", obj.name, frame, f"mesh is inside out ({len(bad)} of "
                             f"{len(shells)} shells have negative signed volume, e.g. {bad[0].volume:.4g})")  # fmt: skip
        if open_edges and is_hero(obj):
            self.add("normals", "warning", obj.name, frame, f"hero mesh has {open_edges} non-manifold edges")

    def faceted(self, obj, mesh, frame):
        count = _creases(mesh, math.radians(FACETED_MIN_DEG), math.radians(FACETED_MAX_DEG))
        if count >= FACETED_MIN_EDGES:
            self.add("faceted", "warning", obj.name, frame,
                     f"{count} edges under {FACETED_MAX_DEG:g} degrees are shaded flat (curved surface looks faceted)")  # fmt: skip

    def loose_parts(self, name, geo, frame):
        """A hero's loose parts that pass into each other deeper than the contact allowance: once for a rigid hero,
        at every sampled frame for one that may deform (reported by finish_self)."""
        deforming = name in self.deforming
        if not deforming and name in self.self_done:
            return
        self.self_done.add(name)
        if name not in self.local or geo.m is None:
            return
        parts = _parts(self.local[name], geo.m)
        found = _self_overlaps(parts)
        if not deforming:
            self.report_self(name, [(frame, res) for _, res, body in found if not body])
            return
        rec = self.self_track.setdefault(name, {"samples": 0, "pairs": {}})
        rec["samples"] += 1
        for key, res, body in found:
            rec["pairs"].setdefault((len(parts), *key), []).append((frame, res, body))

    def coplanar(self, obj, geo, frame):
        """Faces of one mesh on the same plane, facing the same way and overlapping (see self_zfight): once per mesh
        and shape of its transform (a mesh that deforms is checked again whenever it changes)."""
        local = self.local.get(obj.name)
        if local is None or local.mat is None or geo.m is None:
            return
        lin = geo.m[:3, :3]
        gram = lin.T @ lin  # lengths and angles in the world: unchanged when the object only moves or turns
        size = float(np.sqrt(np.abs(gram).max())) or 1.0  # matrix_world is single precision: compare to 6 digits
        step = self.zfight_step(geo.lo, geo.hi)
        done = (obj.name, local.key, round(math.log(size), 6), (np.round(gram / size**2, 6) + 0.0).tobytes(), step)
        if done in self.coplanar_done:
            return
        self.coplanar_done.add(done)
        centre = (local.co.min(0) + local.co.max(0)) / 2
        tris = ((local.co - centre) @ lin.T)[local.idx]
        found = _coplanar_faces(tris, local.poly, _step_tol(step))
        if not len(found):
            return
        slots = obj.evaluated_get(self.depsgraph).material_slots
        names = [s.material.name if s.material else "" for s in slots]

        def slot(i):
            return names[i] if 0 <= i < len(names) and names[i] else f"slot {i} (no material)"

        origin = geo.m[:3, :3] @ centre + geo.m[:3, 3]
        groups: dict[tuple[str, str], list] = {}
        for ta, tb, area, at in found:
            pair = tuple(sorted((slot(int(local.mat[ta])), slot(int(local.mat[tb])))))
            groups.setdefault(pair, []).append((area, at))
        for (a, b), hits in sorted(groups.items()):
            area = sum(h[0] for h in hits)
            c = sum(h[0] * h[1] for h in hits) / area + origin
            where = f"({round(c[0], 2) + 0.0:.2f}, {round(c[1], 2) + 0.0:.2f}, {round(c[2], 2) + 0.0:.2f})"
            if a != b:
                severity, mats = "error", f"materials {a!r} and {b!r}"
            else:
                severity, mats = "warning", f"material {a!r} (the same on both)"
            self.add("self_zfight", severity, obj.name, frame, f"z-fighting inside one mesh: faces with {mats} lie "
                     f"on the same plane, face the same way and overlap ({_area(area)} around {where}), so they "
                     "flicker in the render. Joined boxes are the usual cause: tuck one face inside the other, or "
                     "stop one part at the other's face", key=(obj.name, a, b))  # fmt: skip

    def finish_self(self):
        """Deforming heroes: pairs of comparable size, and big-body pairs whose parts move against each other."""
        for name, rec in self.self_track.items():
            bad = []
            for hits in rec["pairs"].values():
                depths = [res.depth for _, res, _ in hits]
                change = max(depths) - min(depths)
                moved = len(hits) < rec["samples"] or change > max(OVERLAP_TOL, SELF_DEPTH_CHANGE * max(depths))
                if not hits[0][2] or moved:
                    frame, res, _ = max(hits, key=lambda h: (h[1].depth, -h[0]))
                    bad.append((frame, res))
            self.report_self(name, bad)

    def report_self(self, name, bad):
        if not bad:
            return
        frame, worst = max(bad, key=lambda b: (b[1].depth, -b[0]))
        more = f" ({len(bad)} pairs of parts in all)" if len(bad) > 1 else ""
        self.add("self_overlap", "warning", name, frame, f"hero mesh has loose parts that pass into each other: the "
                 f"{worst.inner} is {_metres(worst.depth)} inside the {worst.outer}{more}. They read as one solid: "
                 "end one part at the other's surface, or make them separate objects", key=(name,))  # fmt: skip

    def track_contacts(self, frame):
        """For contact pairs that follow different moving parents: carry the point where they meet along with each
        part and keep how far the two copies drift apart."""
        for a, b, _, _ in self.contact_pairs:
            ga, gb = self.cur.get(a), self.cur.get(b)
            if ga is None or gb is None or ga.m is None or gb.m is None:
                continue
            rec = self.drift.get((a, b))
            if rec is None:
                lo, hi = np.maximum(ga.lo, gb.lo), np.minimum(ga.hi, gb.hi)
                p = np.append((lo + hi) / 2, 1.0)
                try:
                    pa, pb = np.linalg.solve(ga.m, p), np.linalg.solve(gb.m, p)
                except np.linalg.LinAlgError:  # a part scaled to nothing
                    continue
                self.drift[(a, b)] = {"pa": pa, "pb": pb, "worst": (0.0, frame), "limit": _allowance(ga, gb)}
                continue
            d = float(np.linalg.norm((ga.m @ rec["pa"])[:3] - (gb.m @ rec["pb"])[:3]))
            if d > rec["worst"][0]:
                rec["worst"] = (d, frame)

    def report_contacts(self, shot):
        for a, b, moves_a, moves_b in self.contact_pairs:
            rec = self.drift.get((a, b))
            if rec is None or rec["worst"][0] <= rec["limit"]:
                continue
            d, frame = rec["worst"]
            self.add("contact_parent", "warning", a, frame, f"{a!r} and {b!r} are tagged contact but follow "
                     f"different moving parents ({_follows(a, moves_a)}, {_follows(b, moves_b)}), and where they "
                     f"meet drifts {_metres(d)} apart in shot {shot.id!r} (most at frame {frame}). A part rebuilt "
                     "with a new constructor may have lost its parent: parent both to the moving part they share. If "
                     "one really moves against the other through its parent (a foot stepping on a floor), ignore "
                     "this", key=(a, b))  # fmt: skip

    def overlaps(self, frame, objs, geos, only=None):
        """Every pair of objects whose boxes touch (only pairs with an object in `only` when given)."""
        by_name = {o.name: o for o in objs}
        names = sorted(n for n in geos if n not in self.overlay)  # overlays are not solid: no overlap, no z-fight
        if only is not None:
            only = {n for n in only if n not in self.overlay}
        if only is None:
            pairs = [(a, b) for i, a in enumerate(names) for b in names[i + 1 :]]
        else:
            pairs = sorted({(min(a, b), max(a, b)) for a in only for b in names if b != a})
        for a, b in pairs:
            ga, gb = geos[a], geos[b]
            if not _boxes_touch(ga, gb, OVERLAP_TOL):
                continue
            exempt = _exemption(by_name[a], by_name[b])
            limit = None if exempt is None else _allowance(ga, gb, exempt[1])
            step = None if exempt is not None else self.zfight_step(np.maximum(ga.lo, gb.lo), np.minimum(ga.hi, gb.hi))
            rule = ("strict", step) if exempt is None else ("contact", exempt[1])  # not the allowance: it jitters
            res = self.pair_cache.get(ga, gb, rule)
            if res is None:
                res = self.pair_cache.put(ga, gb, rule, _pair(ga, gb, limit, _step_tol(step)))
            if exempt is None:
                for kind in sorted(res.kinds):
                    self.hit(a, b, kind, frame, res)
            elif res.depth > limit:
                self.hit(a, b, "deep", frame, res, f"the {exempt[0]} allows ({_metres(limit)})")

    def zfight_step(self, lo: np.ndarray, hi: np.ndarray) -> int:
        """The z-fight tolerance where the box [lo, hi] is seen by the scene camera now, as a half-octave step
        (`_step_tol` gives metres): steps keep cached pair results valid while the camera moves a little."""
        cam = self.scene.camera
        if cam is None:
            return _tol_step(COPLANAR_TOL)
        pos = np.array(cam.evaluated_get(self.depsgraph).matrix_world.translation, dtype=np.float64)
        return _tol_step(_zfight_tol(cam.data, pos, lo, hi))

    def hit(self, a, b, kind, frame, res, note=None):
        rec = self.hits.setdefault((a, b, kind), {"frames": {}, "note": note})
        rec["frames"][frame] = res

    def report_overlaps(self, shot, sampled):
        """One finding per offending pair in this shot. A pair seen at a frame still holds at the frames after it
        that were not checked because neither object changed. A pair that overlaps the same way in every frame of
        the shot is reported with no shot or frame (the same finding in every shot it is in)."""
        for (a, b, kind), rec in sorted(self.hits.items(), key=lambda kv: (min(kv[1]["frames"]), kv[0])):
            frames = dict(rec["frames"])
            for f in sorted(rec["frames"]):
                g = f + 1
                while g <= shot.f1 and g not in sampled and g not in frames and not {a, b} & self.changed.get(g, set()):
                    frames[g] = frames[f]
                    g += 1
            first = min(frames)
            depths = [frames[f].depth for f in frames]
            static = len(frames) == shot.f1 - shot.f0 + 1 and max(depths) - min(depths) < 1e-6
            key, pair = (None if static else shot.id, a, b, kind), (a, b)
            if kind == "zfight":
                res = frames[first]
                at = ""
                if res.zfight_at is not None:
                    point, gap = res.zfight_at
                    apart = "flush" if gap < 1e-7 else f"{gap * 1000:.2g} mm apart"
                    when = "" if static else f", frame {first}"
                    at = f", for one at {_point(point)} ({apart}{when})"
                where = "in every frame" if static else f"in shot {shot.id!r}, {_spans(sorted(frames))}"
                self.add("overlap", "error", a, None if static else first, f"z-fighting: {a!r} ({self.size(a)}) and "
                         f"{b!r} ({self.size(b)}) have coplanar overlapping faces {where}{at}. Move one face 0.5 to "
                         "1 mm in (or out), or delete the duplicate face", key=key, pair=pair)  # fmt: skip
                continue
            deepest = max(frames, key=lambda f: (frames[f].depth, -f))
            res = frames[deepest]
            how = f"{res.inner!r} is {_metres(res.depth)} inside {res.outer!r}" if res.depth > 0 else ""
            if res.depth > 0 and res.outer_open:
                how = (f"{res.inner!r} reaches into {res.outer!r}, whose mesh is not closed ({res.outer_open} open "
                       "edges), so how deep cannot be measured. Close it, or keep its open parts as their own object")
            at = f" (deepest at frame {deepest})" if res.depth > 0 and len(frames) > 1 else ""
            where = "in every frame" if static else f"in shot {shot.id!r}, {_spans(sorted(frames))}{at}"
            if kind == "penetrate":
                message = (
                    f"{a!r} and {b!r} interpenetrate {where}{': ' + how if how else ''}. Fix the geometry or "
                    "the motion; tag contact:<other> only if they really touch at a surface, and write why"
                )
                frame = None if static else deepest if how else first
                self.add("overlap", "error", a, frame, message, key=key, pair=pair)
            else:
                self.add("overlap", "error", res.inner, None if static else deepest, f"{how} {where}, deeper than "
                         f"{rec['note']}. End one at the other's surface; if it is deep by design, tag "
                         "contact:<other>:<max depth in metres> and write why", key=key, pair=pair)  # fmt: skip

    def size(self, name: str) -> str:
        """The object's size (its local box times its scale), such as "6.4 x 6.4 x 1.2 mm"."""
        obj = self.scene.objects.get(name)
        return "?" if obj is None else _dims(tuple(obj.dimensions))

    def hard_rules(self, short, rules):
        """Names in the scene with words a hard rule may forbid (a "Worker" object, a "Skin" material under no
        people): warnings to look at, since a name is not a picture (a clock's "HourHand" is fine, and is cleared in
        short.json rules_check)."""
        from bsa.rules import name_hits

        names = []
        for obj in sorted(self.scene.objects, key=lambda o: o.name):
            names.append(("object", obj.name, obj.name))
            if obj.data is not None:
                names.append(("mesh" if obj.type == "MESH" else "data", obj.data.name, obj.name))
            for slot in obj.material_slots:
                if slot.material is not None:
                    names.append(("material", slot.material.name, obj.name))
            for coll in obj.users_collection:
                if coll is not self.scene.collection:
                    names.append(("collection", coll.name, obj.name))
            if obj.instance_collection is not None:
                names.append(("collection", obj.instance_collection.name, obj.name))
            lib = obj.library or (obj.data.library if obj.data is not None else None)
            if lib is not None:
                names.append(("library", Path(bpy.path.abspath(lib.filepath)).stem, obj.name))
        seen, unique = set(), []
        for what, name, obj in names:  # a material or collection shared by many objects is reported once
            if (what, name) not in seen:
                seen.add((what, name))
                unique.append((what, name, obj))
        for hit in name_hits(rules, short, unique):
            self.add("hard_rule", "warning", hit["obj"], None, hit["message"])

    def names(self, tl, short):
        """Names that point at the wrong object: renamed duplicates, and tags or labels naming what is not there."""
        present = {o.name for o in self.scene.objects}
        shot_ids = {s.id for s in short.shots} if short is not None else None
        renamed = sorted(o.name for o in self.scene.objects if RENAMED.search(o.name))
        if renamed:
            shown = ", ".join(repr(n) for n in renamed[:RENAMED_LISTED])
            more = f" and {len(renamed) - RENAMED_LISTED} more" if len(renamed) > RENAMED_LISTED else ""
            what = f"{renamed[0]!r} ends" if len(renamed) == 1 else f"{len(renamed)} object names end"
            self.add("names", "warning", renamed[0], None, f"{what} in a number like '.001' ({shown}{more}): "
                     "Blender renamed a duplicate (object names are shared by every scene in the .blend), so labels "
                     "and tags using the plain name point at another object. Give each object a unique name",
                     key=("renamed",))  # fmt: skip
        for obj in sorted(self.scene.objects, key=lambda o: o.name):
            for t in tags(obj):
                if t.startswith("contact:"):
                    target = _contact_target(t[len("contact:") :], present)
                    if target not in present:
                        self.add("names", "error", obj.name, None, f"tag {t!r} names {target!r}, which scene "
                                 f"{self.scene.name!r} does not have{_did_you_mean(target, present)}")  # fmt: skip
                elif t.startswith("hero:") and shot_ids is not None:
                    for sid in t[len("hero:") :].split(","):
                        if sid not in shot_ids:
                            self.add("names", "error", obj.name, None, f"tag {t!r} names shot {sid!r}, which "
                                     "short.json does not have")  # fmt: skip
        if short is None:
            return
        here = {s.id for s in tl.shots}
        for shot in short.shots:
            if shot.id not in here:
                continue
            for label in shot.labels:
                name = label_near(label)
                if name and name not in present:
                    self.add("names", "error", name, None, f"shot {shot.id!r} label {label_text(label)!r} is near "
                             f"{name!r}, which scene {self.scene.name!r} does not have"
                             f"{_did_you_mean(name, present)}", key=(shot.id, name))  # fmt: skip

    def floating(self, frame, name, geos):
        geo = geos[name]
        verts = geo.tris.reshape(-1, 3)
        zmin = float(verts[:, 2].min())
        bottom = np.unique(verts[verts[:, 2] <= zmin + GROUND_TOL], axis=0)
        if len(bottom) > GROUND_MAX_RAYS:
            bottom = bottom[np.linspace(0, len(bottom) - 1, GROUND_MAX_RAYS).astype(int)]
        best = None  # (gap, other name) with the smallest |gap|
        for p in bottom:
            origin = (float(p[0]), float(p[1]), zmin + GROUND_RAY_LIFT)
            top = None
            for other, og in geos.items():
                if other == name or other in self.overlay or not (og.lo[0] <= p[0] <= og.hi[0] and og.lo[1] <= p[1] <= og.hi[1]):
                    continue
                if og.lo[2] > origin[2]:
                    continue
                hit = og.tree.ray_cast(origin, (0.0, 0.0, -1.0))
                if hit[0] is not None and (top is None or hit[0].z > top[0]):
                    top = (hit[0].z, other)
            if top is not None:
                gap = float(p[2]) - top[0]
                if best is None or abs(gap) < abs(best[0]):
                    best = (gap, top[1])
        if best is None:
            self.add("floating", "error", name, frame, "grounded object has nothing below it", key=(name,))
        elif abs(best[0]) > GROUND_TOL:
            how = "floats" if best[0] > 0 else "sinks"
            self.add("floating", "error", name, frame, f"grounded object {how} {abs(best[0]) * 1000:.1f} mm "
                     f"{'above' if best[0] > 0 else 'into'} {best[1]!r}", key=(name,))  # fmt: skip

    def pops(self, shot, positions, sizes):
        for name, track in positions.items():
            if name in self.overlay:  # a glow or guide may snap into place
                continue
            for frame, d, limit in find_pops(track, sizes.get(name, 0.0), judged=(shot.f0, shot.f1)):
                self.add("pops", "error", name, frame, f"jumps {d:.3f} m in one frame (limit {limit:.3f} m from the "
                         f"moves around it) in shot {shot.id!r}", key=(name, frame))  # fmt: skip


def _creases(mesh, lo: float, hi: float) -> int:
    """Edges joining two faces at an angle in [lo, hi) radians whose rendered corner normals jump by more than
    CREASE_DEG at either end of the edge."""
    n_loops = len(mesh.loops)
    if not n_loops:
        return 0
    loop_vert = np.empty(n_loops, np.int64)
    loop_edge = np.empty(n_loops, np.int64)
    mesh.loops.foreach_get("vertex_index", loop_vert)
    mesh.loops.foreach_get("edge_index", loop_edge)
    normals = np.empty(n_loops * 3, np.float64)
    mesh.corner_normals.foreach_get("vector", normals)
    normals = normals.reshape(-1, 3)
    n_faces = len(mesh.polygons)
    start = np.empty(n_faces, np.int64)
    total = np.empty(n_faces, np.int64)
    mesh.polygons.foreach_get("loop_start", start)
    mesh.polygons.foreach_get("loop_total", total)
    face_n = np.empty(n_faces * 3, np.float64)
    mesh.polygons.foreach_get("normal", face_n)
    face_n = face_n.reshape(-1, 3)
    loop_face = np.repeat(np.arange(n_faces), total)
    nxt = np.arange(n_loops) + 1
    nxt[start + total - 1] = start  # each face's last corner wraps to its first
    order = np.argsort(loop_edge, kind="stable")
    _, first, counts = np.unique(loop_edge[order], return_index=True, return_counts=True)
    two = counts == 2
    a, b = order[first[two]], order[first[two] + 1]  # the edge's corner in each of its two faces
    cos = np.clip(np.einsum("ij,ij->i", face_n[loop_face[a]], face_n[loop_face[b]]), -1.0, 1.0)
    angle = np.arccos(cos)

    def jump(p, q):  # degrees between corner normals at the same vertex
        c = np.einsum("ij,ij->i", normals[p], normals[q])
        return np.degrees(np.arccos(np.clip(c, -1.0, 1.0)))

    # a's corner sits at the edge's first vertex and nxt[a] at its second; b's face runs the other way round
    same = loop_vert[a] == loop_vert[b]
    j1 = np.where(same, jump(a, b), jump(a, nxt[b]))
    j2 = np.where(same, jump(nxt[a], nxt[b]), jump(nxt[a], b))
    crease = np.maximum(j1, j2) > np.maximum(CREASE_DEG, CREASE_FRACTION * np.degrees(angle))
    return int(np.count_nonzero((angle >= lo) & (angle < hi) & crease))


def _view_layer(scene):
    """The context view layer when `scene` is the context scene, else the scene's first view layer."""
    return bpy.context.view_layer if bpy.context.scene == scene else scene.view_layers[0]


def _depsgraph(scene, view_layer):
    """The evaluated depsgraph of `scene` (created when the scene is not the context scene)."""
    if bpy.context.scene == scene:
        return bpy.context.evaluated_depsgraph_get()
    with bpy.context.temp_override(scene=scene, view_layer=view_layer):
        return bpy.context.evaluated_depsgraph_get()


def _render_meshes(scene, view_layer, types=("MESH",)) -> list:
    """Objects of `types` that render: not hidden for render, in a collection that is neither excluded nor hidden."""
    reach: set[str] = set()

    def walk(lc):
        if lc.exclude or lc.collection.hide_render:
            return
        reach.update(o.name for o in lc.collection.objects)
        for child in lc.children:
            walk(child)

    walk(view_layer.layer_collection)
    return [o for o in scene.objects if o.type in types and not o.hide_render and o.name in reach]


def _local_mesh(obj, depsgraph) -> _Local | None:
    """The object's evaluated triangles in its own coordinates, or None when it has none."""
    ev = obj.evaluated_get(depsgraph)
    mesh = ev.to_mesh()
    try:
        mesh.calc_loop_triangles()
        n, nv = len(mesh.loop_triangles), len(mesh.vertices)
        if not n:
            return None
        co = np.empty(nv * 3, np.float32)
        mesh.vertices.foreach_get("co", co)
        idx = np.empty(n * 3, np.int32)
        mesh.loop_triangles.foreach_get("vertices", idx)
        mat = np.empty(n, np.int32)
        mesh.loop_triangles.foreach_get("material_index", mat)
        poly = np.empty(n, np.int32)
        mesh.loop_triangles.foreach_get("polygon_index", poly)
    finally:
        ev.to_mesh_clear()
    co64, idx64 = co.reshape(nv, 3).astype(np.float64), idx.reshape(n, 3).astype(np.int64)
    radius = float(np.sqrt((co64**2).sum(1).max()))
    gaps = _open_edges(idx64)
    return _Local(co64, idx64, hash((co.tobytes(), idx.tobytes())), gaps == 0, mat, poly, radius, gaps,
                  bool(gaps) and _flat(co64[np.unique(idx64)]))  # only an open mesh needs to know


def _open_edges(idx: np.ndarray) -> int:
    """How many edges of these triangles are not shared by exactly two of them (0: a closed, manifold surface; -1
    for no triangles)."""
    if not len(idx):
        return -1
    e = np.sort(np.concatenate([idx[:, [0, 1]], idx[:, [1, 2]], idx[:, [2, 0]]]), axis=1)
    _, counts = np.unique(e[:, 0] * (int(idx.max()) + 1) + e[:, 1], return_counts=True)
    return int(np.count_nonzero(counts != 2))


def _closed(idx: np.ndarray) -> bool:
    """Is every edge of these triangles shared by exactly two of them (a closed, manifold surface)?"""
    return _open_edges(idx) == 0


def _flat(co: np.ndarray) -> bool:
    """Do these points lie within OVERLAP_TOL of one plane?"""
    if len(co) < 4:
        return True
    d = co - co.mean(0)
    normal = np.linalg.eigh(d.T @ d)[1][:, 0]  # the direction they spread least along
    return float(np.abs(d @ normal).max()) <= OVERLAP_TOL


def _place(name: str, local: _Local, matrix) -> _Geo:
    """The object's triangles in world space under `matrix`."""
    m = np.array(matrix, dtype=np.float64)
    world = local.co @ m[:3, :3].T + m[:3, 3]
    tris = world[local.idx]
    flat = tris.reshape(-1, 3)
    dims = (local.co.max(0) - local.co.min(0)) * np.linalg.norm(m[:3, :3], axis=0)
    key = hash((local.key, (np.round(m, 9) + 0.0).tobytes()))
    return _Geo(name, tris, flat.min(0), flat.max(0), key, float(dims.min()), local.closed, local.key, m, local.radius,
                local.open_edges, local.flat)


def _world_geo(obj, depsgraph) -> _Geo | None:
    local = _local_mesh(obj, depsgraph)
    return None if local is None else _place(obj.name, local, obj.evaluated_get(depsgraph).matrix_world)


def _may_deform(obj) -> bool:
    """Can the object's evaluated mesh change shape from frame to frame (not only move)?"""
    if obj.data is not None and getattr(obj.data, "shape_keys", None) is not None:
        return True
    if obj.parent is not None and obj.parent.type == "ARMATURE":
        return True
    return any(mod.type not in STATIC_MODIFIERS for mod in obj.modifiers)


class _PairCache:
    """Pair results by the two meshes and where one sits relative to the other. Parts that move together (a parent
    and child, a whole room swaying on its tower) are measured once: a result is reused while every point of the pair
    stays within POSE_TOL of where it was when measured. matrix_world is single precision, so far from the world
    origin the relative pose of two parts that move together jitters by tens of micrometres from frame to frame: an
    exact match would measure them again on every frame."""

    def __init__(self):
        self.entries: dict[tuple, list] = {}
        self.measured = 0  # pairs measured (for tests)

    @staticmethod
    def _pose(ga: _Geo, gb: _Geo):
        if ga.m is None or gb.m is None:
            return None
        try:
            rel = np.linalg.solve(ga.m, gb.m)
        except np.linalg.LinAlgError:
            return None
        return rel[:3, :3], rel[:3, 3], np.linalg.norm(ga.m[:3, :3], axis=0)

    def get(self, ga: _Geo, gb: _Geo, rule) -> _Contact | None:
        pose = self._pose(ga, gb)
        if pose is None:
            hits = self.entries.get((ga.name, gb.name, rule, ga.key, gb.key))
            return hits[0][1] if hits else None
        hits = self.entries.get((ga.name, gb.name, rule, ga.local_key, gb.local_key))
        if not hits:
            return None
        rot, move, scale = pose
        for i, (old, res) in enumerate(hits):
            r0, t0, s0 = old
            reach = gb.radius * float(np.linalg.norm(rot)) + float(np.linalg.norm(move))
            drift = (float(np.linalg.norm(rot - r0)) * gb.radius + float(np.linalg.norm(move - t0))) * float(scale.max())
            drift += float(np.abs(scale - s0).max()) * max(ga.radius, reach)
            if drift <= POSE_TOL:
                if i:
                    hits.insert(0, hits.pop(i))  # the pose seen last is the likeliest next
                return res
        return None

    def put(self, ga: _Geo, gb: _Geo, rule, res: _Contact) -> _Contact:
        self.measured += 1
        pose = self._pose(ga, gb)
        if pose is None:
            self.entries[(ga.name, gb.name, rule, ga.key, gb.key)] = [(None, res)]
            return res
        hits = self.entries.setdefault((ga.name, gb.name, rule, ga.local_key, gb.local_key), [])
        hits.insert(0, (pose, res))
        del hits[POSE_KEPT:]
        return res


def _self_overlaps(parts: list[_Geo]) -> list[tuple[tuple[int, int], _Contact, bool]]:
    """Pairs of loose parts that pass into each other deeper than the contact allowance: ((i, j), result, big body),
    where big body means one part has at least SELF_BODY_RATIO times the other's box volume (detail sunk into it)."""
    if not 2 <= len(parts) <= SELF_MAX_PARTS:
        return []
    lo = np.array([g.lo for g in parts])
    hi = np.array([g.hi for g in parts])
    vol = np.prod(np.maximum(hi - lo, 1e-6), axis=1)
    near = np.all(lo[:, None, :] <= hi[None, :, :] + OVERLAP_TOL, axis=2)
    near &= near.T
    out = []
    for i, j in zip(*np.nonzero(np.triu(near, 1)), strict=True):
        limit = _allowance(parts[i], parts[j])
        res = _pair(parts[i], parts[j], limit)
        if res.depth > limit:
            body = max(vol[i], vol[j]) >= SELF_BODY_RATIO * min(vol[i], vol[j])
            out.append(((int(i), int(j)), res, bool(body)))
    return out


def _animated(obj) -> bool:
    """Does the object move by itself: keyframes, drivers, NLA strips or constraints?"""
    ad = obj.animation_data
    if ad is not None and (ad.action is not None or len(ad.drivers) or len(ad.nla_tracks)):
        return True
    return len(obj.constraints) > 0


def _moving_ancestors(obj, skip) -> tuple[str, ...]:
    """The names of the object's animated ancestors (nearest first), leaving out the objects in `skip`."""
    out, p = [], obj.parent
    while p is not None:
        if p not in skip and _animated(p):
            out.append(p.name)
        p = p.parent
    return tuple(out)


def _contact_pairs(scene) -> list[tuple[str, str, tuple[str, ...], tuple[str, ...]]]:
    """Each contact-tagged pair whose two parts follow different moving ancestors: (tagged, other, its moving
    ancestors, the other's). Each pair once, however many tags name it."""
    names = {o.name for o in scene.objects}
    out, seen = [], set()
    for obj in sorted(scene.objects, key=lambda o: o.name):
        for t in tags(obj):
            if not t.startswith("contact:"):
                continue
            target, _, moves = _contact_tag(t[len("contact:") :], names)
            other = scene.objects.get(target)
            pair = frozenset((obj.name, getattr(other, "name", "")))
            if other is None or other == obj or pair in seen:
                continue
            seen.add(pair)
            if moves or _moves_tagged(other, obj.name):
                continue  # designed to move against each other
            skip = {obj, other}
            ma, mb = _moving_ancestors(obj, skip), _moving_ancestors(other, skip)
            if set(ma) != set(mb):
                out.append((obj.name, other.name, ma, mb))
    return out


def _follows(name: str, moves: tuple[str, ...]) -> str:
    if not moves:
        return f"{name!r} follows nothing that moves"
    return f"{name!r} moves with " + " and ".join(repr(m) for m in moves)


MOVES = "moves"  # contact:<other>:moves - the pair moves against each other by design


def _contact_tag(rest: str, names) -> tuple[str, float | None, bool]:
    """(the object a contact tag names, the depth it sets or None, whether it says "moves"): "Yoke" in
    "contact:Yoke", "contact:Yoke:0.05", "contact:Yoke:moves" and "contact:Yoke:0.05:moves" (unless an object is
    really called "Yoke:0.05")."""
    if rest in names:
        return rest, None, False
    target, depth, moves = rest, None, False
    for _ in range(2):  # at most a depth and the qualifier, in either order
        head, sep, tail = target.rpartition(":")
        if not sep or target in names:
            break
        if tail == MOVES and not moves:
            moves, target = True, head
        elif depth is None and _metres_value(tail) is not None:
            depth, target = _metres_value(tail), head
        else:
            break
    return target, depth, moves


def _contact_target(rest: str, names) -> str:
    """The object a contact tag names (see `_contact_tag`)."""
    return _contact_tag(rest, names)[0]


def _moves_tagged(obj, other: str) -> bool:
    """Does obj carry "contact:<other>:moves" (with or without a depth)?"""
    for t in tags(obj):
        if t.startswith("contact:"):
            target, _, moves = _contact_tag(t[len("contact:") :], ())
            if moves and target == other:
                return True
    return False


def _metres_value(text: str) -> float | None:
    try:
        value = float(text)
    except ValueError:
        return None
    return value if value >= 0 and math.isfinite(value) else None


def _exemption(a, b) -> tuple[str, float | None] | None:
    """Why the pair may overlap a little ("contact:B tag on 'A'", "parent/child pair") and the depth a contact tag
    sets (None for the default), or None when it may not overlap at all."""
    found = []
    for x, y in ((a, b), (b, a)):
        for t in tags(x):
            if t.startswith("contact:"):
                rest = t[len("contact:") :]
                if rest == y.name:
                    found.append((f"{t} tag on {x.name!r}", None))
                else:
                    target, depth, _ = _contact_tag(rest, ())
                    if target == y.name:
                        found.append((f"{t} tag on {x.name!r}", depth))
    if found:
        return max(found, key=lambda w: -1.0 if w[1] is None else w[1])
    if a.parent == b or b.parent == a:
        return ("parent/child pair", None)
    return None


def _allowance(ga: _Geo, gb: _Geo, depth: float | None = None) -> float:
    """How deep an exempt pair may overlap: the tag's depth, else min(CONTACT_DEPTH, CONTACT_FRACTION of the thinner
    part's smallest dimension), never under OVERLAP_TOL."""
    if depth is not None:
        return max(depth, OVERLAP_TOL)
    return max(OVERLAP_TOL, min(CONTACT_DEPTH, CONTACT_FRACTION * min(ga.thin, gb.thin)))


def _area(a: float) -> str:
    if a >= 1.0:
        return f"{a:.1f} m²"
    if a >= 1e-4:
        return f"{a * 1e4:.0f} cm²"
    return f"{a * 1e6:.1f} mm²"


def _did_you_mean(name: str, present) -> str:
    close = sorted(n for n in present if RENAMED.sub("", n) == name and n != name)
    return f" (did you mean {close[0]!r}?)" if close else ""


def _metres(d: float) -> str:
    if d >= 1.0:
        return f"{d:.1f} m"
    if d >= 0.01:
        return f"{d * 100:.0f} cm"
    return f"{d * 1000:.0f} mm"


def _spans(frames: list[int]) -> str:
    """ "frames 3-9, 20" for sorted frame numbers, at most FRAME_SPANS_SHOWN spans."""
    spans: list[list[int]] = []
    for f in frames:
        if spans and f == spans[-1][1] + 1:
            spans[-1][1] = f
        else:
            spans.append([f, f])
    text = ", ".join(str(a) if a == b else f"{a}-{b}" for a, b in spans[:FRAME_SPANS_SHOWN])
    if len(spans) > FRAME_SPANS_SHOWN:
        text += f" and {len(spans) - FRAME_SPANS_SHOWN} more spans"
    return ("frame " if len(frames) == 1 else "frames ") + text


def _bvh(tris: np.ndarray) -> BVHTree:
    n = len(tris)
    return BVHTree.FromPolygons(tris.reshape(-1, 3).tolist(), np.arange(n * 3).reshape(n, 3).tolist())


def _view_state(scene, cam, geo: _Geo) -> str:
    """ "in" (bounding box fully inside the camera frame), "partial", or "out"."""
    corners = [
        (x, y, z) for x in (geo.lo[0], geo.hi[0]) for y in (geo.lo[1], geo.hi[1]) for z in (geo.lo[2], geo.hi[2])
    ]
    pts = [world_to_camera_view(scene, cam, Vector(c)) for c in corners]
    if all(0.0 <= p.x <= 1.0 and 0.0 <= p.y <= 1.0 and p.z > 0 for p in pts):
        return "in"
    front = [p for p in pts if p.z > 0]
    if not front:
        return "out"
    if len(front) < len(pts):
        return "partial"  # straddles the camera plane
    xs, ys = [p.x for p in front], [p.y for p in front]
    return "partial" if max(xs) >= 0 and min(xs) <= 1 and max(ys) >= 0 and min(ys) <= 1 else "out"


def _boxes_touch(a: _Geo, b: _Geo, tol: float) -> bool:
    return bool(np.all(a.lo <= b.hi + tol) and np.all(b.lo <= a.hi + tol))


def _unit_normals(tris: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    n = np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0])
    length = np.linalg.norm(n, axis=1)
    valid = length > 1e-12
    return n / np.where(valid, length, 1.0)[:, None], valid


def _pair(ga: _Geo, gb: _Geo, limit: float | None = None, zfight_tol: float = COPLANAR_TOL) -> _Contact:
    """How two objects meet. Strict (no limit): {"zfight", "penetrate"} (see the module docstring for the
    tolerances; faces within `zfight_tol` of each other's plane z-fight), with the depth measured when they
    interpenetrate and one z-fighting place (`zfight_at`). Exempt (the depth they may overlap by): the depth,
    measured only when their faces cross or reach inside each other by more than that limit (else 0)."""
    tol = OVERLAP_TOL if limit is None else limit
    a = ga.tris[ga.in_box(gb.lo - OVERLAP_TOL, gb.hi + OVERLAP_TOL)]
    b = gb.tris[gb.in_box(ga.lo - OVERLAP_TOL, ga.hi + OVERLAP_TOL)]
    if not len(a) or not len(b):
        return _Contact(set())
    kinds = set()
    tree_a, tree_b = _bvh(a), _bvh(b)
    pairs = np.array(tree_a.overlap(tree_b), dtype=np.int64).reshape(-1, 2)
    if len(pairs) and (
        np.any(_crosses(a[pairs[:, 0]], b[pairs[:, 1]], tol) & _crosses(b[pairs[:, 1]], a[pairs[:, 0]], tol))
        or _reaches_inside(a[np.unique(pairs[:, 0])], tree_b, tol)
        or _reaches_inside(b[np.unique(pairs[:, 1])], tree_a, tol)
    ):
        kinds.add("penetrate")
    at = _coplanar_overlap(a, b, tree_b, zfight_tol) if limit is None else None
    if at is not None:
        kinds.add("zfight")
    if "penetrate" not in kinds:
        return _Contact(kinds, zfight_at=at)
    if limit is not None:
        kinds = set()
    depth, inner, outer = _depth(ga, gb)
    og = ga if outer == ga.name else gb
    unsure = 0 if og.closed or og.flat else max(og.open_edges, 1)
    return _Contact(kinds, depth, inner, outer, zfight_at=at, outer_open=unsure)


def _depth(ga: _Geo, gb: _Geo) -> tuple[float, str, str]:
    """(depth, inner, outer): the deepest point of either object's surface inside the other."""
    step = max(min(ga.thin, gb.thin) / 2, DEPTH_MIN_STEP)
    da, db = _depth_into(ga, gb, step), _depth_into(gb, ga, step)
    return (da, ga.name, gb.name) if da >= db else (db, gb.name, ga.name)


def _depth_into(ga: _Geo, gb: _Geo, step: float) -> float:
    """How far points on a's faces reach inside b (0 when none is inside)."""
    grow = OVERLAP_TOL
    if not gb.closed:  # one-sided: a part sunk into a floor plane lies below the floor's flat box
        grow = max(OVERLAP_TOL, min(float(np.linalg.norm(ga.hi - ga.lo)), float(np.linalg.norm(gb.hi - gb.lo))))
    lo = np.maximum(gb.lo - grow, ga.lo - OVERLAP_TOL)
    hi = np.minimum(gb.hi + grow, ga.hi + OVERLAP_TOL)
    if np.any(lo > hi):
        return 0.0
    pts = _samples(ga.tris[ga.in_box(lo, hi)], lo, hi, step)
    if len(pts) > DEPTH_MAX_POINTS:
        pts = pts[np.linspace(0, len(pts) - 1, DEPTH_MAX_POINTS).astype(np.int64)]
    tree, best = gb.tree, 0.0
    for p in pts.tolist():
        best = max(best, _inside_depth(tree, gb.closed, p))
    return best


def _inside_depth(tree: BVHTree, closed: bool, p) -> float:
    """Distance from p to the surface when p is inside it, else 0 (see "depth" in the module docstring)."""
    loc, normal, _, dist = tree.find_nearest(p)
    if loc is None or dist <= OVERLAP_TOL:
        return 0.0
    s = (p[0] - loc[0]) * normal[0] + (p[1] - loc[1]) * normal[1] + (p[2] - loc[2]) * normal[2]
    if s <= -0.99 * dist:
        return dist  # straight behind the nearest face
    if s >= 0.99 * dist or not closed:
        return 0.0
    return dist if _parity(tree, p) else 0.0


def _parity(tree: BVHTree, p) -> bool:
    """Inside a closed surface: an odd number of crossings along a ray, by a majority of three skewed rays."""
    return sum(_crossings(tree, Vector(p), direction) % 2 for direction in _RAYS) >= 2


def _crossings(tree: BVHTree, origin: Vector, direction: Vector) -> int:
    """How many times a ray from origin crosses the surface. Each hit restarts the ray just past it: a step that
    grows with the distance from the world origin, since the tree is single precision (at 400 m a fixed micrometre
    step does not move the point, and the ray would hit the same face again and again)."""
    crossings = 0
    for _ in range(10000):
        loc = tree.ray_cast(origin, direction)[0]
        if loc is None:
            break
        crossings += 1
        origin = loc + direction * max(1e-6, RAY_STEP * max(abs(loc.x), abs(loc.y), abs(loc.z)))
    return crossings


_GRIDS: dict[int, np.ndarray] = {}


def _grid(n: int) -> np.ndarray:
    """Barycentric weights of a triangle's points n divisions apart along each edge (corners included)."""
    if n not in _GRIDS:
        w = [(i / n, j / n, (n - i - j) / n) for i in range(n + 1) for j in range(n + 1 - i)]
        _GRIDS[n] = np.array(w, dtype=np.float64)
    return _GRIDS[n]


def _samples(tris: np.ndarray, lo: np.ndarray, hi: np.ndarray, step: float) -> np.ndarray:
    """Points about `step` apart over the parts of these triangles inside the box [lo, hi]."""
    if not len(tris):
        return np.empty((0, 3))
    inside = np.all(tris.min(1) >= lo, axis=1) & np.all(tris.max(1) <= hi, axis=1)
    pieces = [tris[inside]]
    for t in tris[~inside]:
        poly = _clip(t, lo, hi)
        if len(poly) >= 3:
            pieces.append(np.array([(poly[0], poly[i], poly[i + 1]) for i in range(1, len(poly) - 1)]))
    tris = np.concatenate(pieces)
    if not len(tris):
        return np.empty((0, 3))
    edge = np.linalg.norm(tris - np.roll(tris, 1, axis=1), axis=2).max(1)
    n = np.ceil(edge / step)
    budget = 4 * DEPTH_MAX_POINTS
    total = float(((n + 1) * (n + 2) / 2).sum())
    if total > budget:  # coarser, so a big face does not cost millions of points
        n = np.ceil(n * math.sqrt(budget / total))
    n = np.clip(n, 1, DEPTH_MAX_SUB).astype(np.int64)
    out = [np.einsum("mk,tkj->tmj", _grid(int(k)), tris[n == k]).reshape(-1, 3) for k in np.unique(n)]
    return np.concatenate(out)


def _clip(tri: np.ndarray, lo: np.ndarray, hi: np.ndarray) -> list[np.ndarray]:
    """The polygon of a triangle inside an axis-aligned box (Sutherland-Hodgman)."""
    poly = list(tri)
    for axis in range(3):
        for bound, keep in ((lo[axis], 1.0), (hi[axis], -1.0)):
            out = []
            for i, cur in enumerate(poly):
                prev = poly[i - 1]
                dc, dp = keep * (cur[axis] - bound), keep * (prev[axis] - bound)
                if dc >= 0:
                    if dp < 0:
                        out.append(prev + (cur - prev) * (dp / (dp - dc)))
                    out.append(cur)
                elif dp >= 0:
                    out.append(prev + (cur - prev) * (dp / (dp - dc)))
            poly = out
            if not poly:
                return []
    return poly


def _parts(local: _Local, matrix) -> list[_Geo]:
    """The mesh's loose parts (pieces connected by shared vertices) in world space, named by where they are."""
    m = np.array(matrix, dtype=np.float64)
    world = local.co @ m[:3, :3].T + m[:3, 3]
    label = _components(local.idx, len(local.co))[local.idx[:, 0]]
    order = np.argsort(label, kind="stable")
    _, starts = np.unique(label[order], return_index=True)
    if len(starts) > SELF_MAX_PARTS:
        return []
    parts = []
    for idx in np.split(order, starts[1:]):
        corners = local.idx[idx]
        tris = world[corners]
        flat = tris.reshape(-1, 3)
        lo, hi = flat.min(0), flat.max(0)
        c = (lo + hi) / 2
        name = f"part around ({c[0]:.2f}, {c[1]:.2f}, {c[2]:.2f})"
        gaps = _open_edges(corners)
        parts.append(_Geo(name, tris, lo, hi, 0, float((hi - lo).min()), gaps == 0, open_edges=gaps, flat=_flat(flat)))
    return parts


def _components(idx: np.ndarray, nv: int) -> np.ndarray:
    """Per vertex, the smallest vertex index of its connected piece (label propagation with pointer jumping)."""
    label = np.arange(nv)
    e = np.concatenate([idx[:, [0, 1]], idx[:, [1, 2]]])
    while True:
        a, b = label[e[:, 0]], label[e[:, 1]]
        low = np.minimum(a, b)
        new = label.copy()
        np.minimum.at(new, a, low)
        np.minimum.at(new, b, low)
        new = new[new]
        if np.array_equal(new, label):
            return label[label]
        label = new


def _crosses(t: np.ndarray, other: np.ndarray, tol: float = OVERLAP_TOL) -> np.ndarray:
    """Per pair: does triangle t reach more than tol to both sides of the other triangle's plane?"""
    n, valid = _unit_normals(other)
    d = np.einsum("kvj,kj->kv", t - other[:, :1, :], n)
    return valid & (d.max(1) > tol) & (d.min(1) < -tol)


def _reaches_inside(tris: np.ndarray, other: BVHTree, tol: float = OVERLAP_TOL) -> bool:
    """Does a vertex or centre of these (intersecting) triangles lie more than tol behind the other object's
    surface, straight below the face nearest to it? Catches flush, axis-aligned overlaps where no pair of faces
    crosses."""
    points = np.unique(np.concatenate([tris.reshape(-1, 3), tris.mean(1)]), axis=0)
    for p in points.tolist():
        loc, normal, _, dist = other.find_nearest(p)
        if loc is None or dist <= tol:
            continue
        depth = -sum((p[i] - loc[i]) * normal[i] for i in range(3))
        if depth >= 0.99 * dist:
            return True
    return False


def _zfight_tol(cam_data, cam_pos: np.ndarray, lo: np.ndarray, hi: np.ndarray) -> float:
    """How far apart two faces in the box [lo, hi] may be and still z-fight, seen by a camera at `cam_pos`:
    COPLANAR_SAFETY steps of depth precision there (a DEPTH_BITS depth buffer from the clip start, linear for an
    orthographic camera; or float32 rounding of the coordinates), between COPLANAR_FLOOR and COPLANAR_TOL."""
    lo, hi = np.minimum(lo, hi), np.maximum(lo, hi)
    d = float(np.linalg.norm(np.clip(cam_pos, lo, hi) - cam_pos))
    start = max(float(getattr(cam_data, "clip_start", 0.1)), 1e-6)
    end = max(float(getattr(cam_data, "clip_end", 100.0)), start)
    if getattr(cam_data, "type", "PERSP") == "ORTHO":
        depth = (end - start) / 2**DEPTH_BITS
    else:
        d = max(d, start)
        depth = d * d / (start * 2**DEPTH_BITS)
    coords = float(max(np.abs(lo).max(), np.abs(hi).max(), np.abs(cam_pos).max())) * 2.0**-23
    return min(COPLANAR_TOL, max(COPLANAR_FLOOR, COPLANAR_SAFETY * max(depth, coords)))


def _tol_step(tol: float) -> int:
    """A tolerance as a half-octave step (rounded down: never looser than asked)."""
    return math.floor(2 * math.log2(tol) + 1e-9)


def _step_tol(step: int | None) -> float:
    if step is None:
        return COPLANAR_TOL
    return min(COPLANAR_TOL, max(COPLANAR_FLOOR, 2.0 ** (step / 2)))


def _point(p) -> str:
    return "(" + ", ".join(f"{round(float(v), 4) + 0.0:.4f}" for v in p) + ")"


def _dims(d) -> str:
    """Three lengths in one unit: "6.4 x 6.4 x 1.2 mm", "2 x 0.5 x 1 m"."""
    big = max(d) if d else 0.0
    unit, k = ("mm", 1000.0) if big < 0.1 else ("cm", 100.0) if big < 1.0 else ("m", 1.0)
    return " x ".join(f"{float(v) * k:.3g}" for v in d) + f" {unit}"


def _coplanar_overlap(a: np.ndarray, b: np.ndarray, tree_b: BVHTree, tol: float = COPLANAR_TOL):
    """One pair of faces of a and b that face the same way, lie within tol of each other's plane and overlap with
    positive area: (the centre of the face of a, in the world; how far apart the two are), or None."""
    cand = []
    centres = a.mean(1)
    radii = np.linalg.norm(a - centres[:, None, :], axis=2).max(1) + tol
    for i, (c, r) in enumerate(zip(centres.tolist(), radii.tolist(), strict=True)):
        cand.extend((i, hit[2]) for hit in tree_b.find_nearest_range(c, r))
    if not cand:
        return None
    cand = np.unique(np.array(cand, dtype=np.int64), axis=0)
    s, t = a[cand[:, 0]], b[cand[:, 1]]
    ns, vs = _unit_normals(s)
    nt, vt = _unit_normals(t)
    same = vs & vt & (np.einsum("kj,kj->k", ns, nt) > COPLANAR_COS)
    gap = np.abs(np.einsum("kvj,kj->kv", s - t[:, :1, :], nt)).max(1)
    keep = np.nonzero(same & (gap < tol))[0]
    if not len(keep):
        return None
    hit = keep[_overlap_2d(s[keep], t[keep], nt[keep])]
    if not len(hit):
        return None
    k = int(hit[np.argmax(gap[hit])])  # the widest gap that still z-fights: the one to look at
    return tuple(float(v) for v in s[k].mean(0)), float(gap[k])


def _overlap_2d(s: np.ndarray, t: np.ndarray, n: np.ndarray, eps: float = 1e-6) -> np.ndarray:
    """Per pair of coplanar triangles: do they overlap with positive area? (separating axis test in the plane)"""
    helper = np.where(np.abs(n[:, :1]) < 0.9, [[1.0, 0.0, 0.0]], [[0.0, 1.0, 0.0]])
    u = np.cross(n, helper)
    u /= np.linalg.norm(u, axis=1)[:, None]
    v = np.cross(n, u)
    s2 = np.stack([np.einsum("kvj,kj->kv", s, u), np.einsum("kvj,kj->kv", s, v)], axis=2)  # (k, 3, 2)
    t2 = np.stack([np.einsum("kvj,kj->kv", t, u), np.einsum("kvj,kj->kv", t, v)], axis=2)
    overlap = np.ones(len(s), dtype=bool)
    for tri in (s2, t2):
        for i in range(3):
            edge = tri[:, (i + 1) % 3] - tri[:, i]
            axis = np.stack([-edge[:, 1], edge[:, 0]], axis=1)
            axis /= np.maximum(np.linalg.norm(axis, axis=1), 1e-12)[:, None]
            ps, pt = np.einsum("kvj,kj->kv", s2, axis), np.einsum("kvj,kj->kv", t2, axis)
            depth = np.minimum(ps.max(1), pt.max(1)) - np.maximum(ps.min(1), pt.min(1))
            overlap &= depth > eps
    return overlap


def _coplanar_faces(tris: np.ndarray, poly: np.ndarray, tol: float = COPLANAR_TOL) -> list[tuple]:
    """Pairs of triangles from different polygons of one mesh that face the same way (normals within 1 degree), lie
    within tol of each other's plane and overlap by more than a sliver (a strip wider than tol): [(a, b, area,
    centre)]. Faces are bucketed by plane (normal and offset), then by a grid in the plane, so a big mesh costs
    about as much as its faces that share a plane with another face."""
    n, valid = _unit_normals(tris)
    ids = np.nonzero(valid)[0]
    if len(ids) < 2:
        return []
    reach = float(np.linalg.norm(tris.reshape(-1, 3), axis=1).max())
    d_margin = 2 * tol + math.sin(math.radians(1.0)) * reach  # offsets of one plane's faces differ by up to this
    feat = np.column_stack([n[ids], np.einsum("kj,kj->k", n[ids], tris[ids, 0])])
    q = np.array([PLANE_CELL] * 3 + [max(8 * d_margin, 1e-3)])
    margin = np.array([PLANE_MARGIN] * 3 + [d_margin])
    # buckets centred on 0 and 1 (axis-aligned normals, a flat mesh's own plane), so those faces are not doubled
    hi = np.floor((feat + margin) / q + 0.5).astype(np.int64)
    rows, row_tri = np.floor((feat - margin) / q + 0.5).astype(np.int64), np.arange(len(ids))
    for k in range(4):  # a face near a bucket's edge goes into the bucket next to it as well
        dup = hi[row_tri, k] != rows[:, k]
        if dup.any():
            extra = rows[dup].copy()
            extra[:, k] = hi[row_tri[dup], k]
            rows, row_tri = np.concatenate([rows, extra]), np.concatenate([row_tri, row_tri[dup]])
    gid = _group_ids(rows)
    keep = np.bincount(gid)[gid] >= 2
    if not keep.any():
        return []
    tri = ids[row_tri[keep]]
    pairs = _plane_group_pairs(_group_ids(gid[keep][:, None]), tris[tri], n[tri], poly[tri], tol)
    if not len(pairs):
        return []
    a, b = tri[pairs[:, 0]], tri[pairs[:, 1]]
    code = np.unique(np.minimum(a, b) * len(tris) + np.maximum(a, b))
    ab = np.column_stack([code // len(tris), code % len(tris)])
    s, t, ns, nt = tris[ab[:, 0]], tris[ab[:, 1]], n[ab[:, 0]], n[ab[:, 1]]
    same = np.einsum("kj,kj->k", ns, nt) > COPLANAR_COS
    near = (np.abs(np.einsum("kvj,kj->kv", s - t[:, :1, :], nt)).max(1) < tol) & (
        np.abs(np.einsum("kvj,kj->kv", t - s[:, :1, :], ns)).max(1) < tol
    )
    keep = same & near
    if keep.any():
        keep[keep] = _overlap_2d(s[keep], t[keep], nt[keep], 1e-12 * max(1.0, reach))
    out = []
    for k in np.nonzero(keep)[0][:PLANE_MAX_PAIRS].tolist():
        nb = nt[k]
        u = np.cross(nb, (1.0, 0.0, 0.0) if abs(nb[0]) < 0.9 else (0.0, 1.0, 0.0))
        u /= np.linalg.norm(u)
        v = np.cross(nb, u)
        clip = _clip_convex([(float(p @ u), float(p @ v)) for p in s[k]], [(float(p @ u), float(p @ v)) for p in t[k]])
        area, cu, cv = _polygon_area(clip)
        longest = max((math.dist(clip[i - 1], clip[i]) for i in range(len(clip))), default=0.0)
        if area > tol * longest:  # wider than a sliver
            out.append((int(ab[k, 0]), int(ab[k, 1]), area, cu * u + cv * v + float(t[k, 0] @ nb) * nb))
    return out


def _group_ids(cols: np.ndarray) -> np.ndarray:
    """Per row of an integer array, the id of its distinct row (0, 1, ... in sorted order)."""
    order = np.lexsort(cols.T[::-1])
    srt = cols[order]
    new = np.empty(len(cols), dtype=bool)
    new[:1] = True
    new[1:] = np.any(srt[1:] != srt[:-1], axis=1)
    out = np.empty(len(cols), dtype=np.int64)
    out[order] = np.cumsum(new) - 1
    return out


def _plane_group_pairs(gid: np.ndarray, tris: np.ndarray, n: np.ndarray, poly: np.ndarray, tol: float) -> np.ndarray:
    """Pairs of rows in the same plane group, from different polygons, whose boxes in the plane (projected along the
    axis the group's normal leans on most) overlap by more than tol both ways: (k, 2) row indices. An overlap wider
    than tol needs that, so faces of one flat surface that only share edges are dropped here, cheaply. Candidates
    come from a grid in the plane; a face covering many cells is tested against its whole group instead."""
    gn = np.zeros((int(gid.max()) + 1, 3))
    np.add.at(gn, gid, n)
    ax = np.argmax(np.abs(gn), axis=1)[gid]
    flat = np.stack([np.take_along_axis(tris, ((ax + k) % 3)[:, None, None].repeat(3, 1), axis=2)[:, :, 0]
                     for k in (1, 2)], axis=2)  # (rows, 3, 2): the two other coordinates  # fmt: skip
    lo, hi = flat.min(1), flat.max(1)
    cell = max(float(np.median((hi - lo).max(1))), 1e-6)
    c0, c1 = np.floor(lo / cell).astype(np.int64), np.floor(hi / cell).astype(np.int64)
    w = c1 - c0 + 1
    nc = w[:, 0] * w[:, 1]
    big = nc > PLANE_BIG_CELLS
    out = []

    def keep(a, b):
        ok = (poly[a] != poly[b]) & (np.einsum("kj,kj->k", n[a], n[b]) > COPLANAR_COS)
        ok &= np.all(np.minimum(hi[a], hi[b]) - np.maximum(lo[a], lo[b]) > tol, axis=1)
        if ok.any():
            out.append(np.column_stack([a[ok], b[ok]]))

    small = np.nonzero(~big)[0]
    if len(small):
        rep = np.repeat(small, nc[small])
        local = np.arange(len(rep)) - np.repeat(np.cumsum(nc[small]) - nc[small], nc[small])
        cid = _group_ids(np.column_stack([gid[rep], c0[rep, 0] + local % w[rep, 0], c0[rep, 1] + local // w[rep, 0]]))
        order = np.argsort(cid, kind="stable")
        cid, rep = cid[order], rep[order]
        counts = np.bincount(cid)
        later = (np.cumsum(counts) - 1)[cid] - np.arange(len(cid))  # entries after this one in its cell
        ends = np.cumsum(later)
        cut = 0
        while cut < len(cid):  # every pair of entries in one cell, a few million pairs at a time
            stop = max(int(np.searchsorted(ends, (ends[cut - 1] if cut else 0) + 2_000_000)), cut + 1)
            pos = np.arange(cut, min(stop, len(cid)))
            k = later[pos]
            first = np.repeat(pos, k)
            offset = np.arange(len(first)) - np.repeat(np.cumsum(k) - k, k) + 1
            keep(rep[first], rep[first + offset])
            cut = stop
    by_group = np.argsort(gid, kind="stable")
    starts = np.searchsorted(gid[by_group], np.arange(len(gn) + 1))
    big = np.nonzero(big)[0]
    sizes = np.diff(starts)[gid[big]]
    ends = np.cumsum(sizes)
    cut = 0
    while cut < len(big):  # each big face against every face of its group, a few million pairs at a time
        stop = max(int(np.searchsorted(ends, (ends[cut - 1] if cut else 0) + 2_000_000)), cut + 1)
        rows, k = big[cut:stop], sizes[cut:stop]
        local = np.arange(int(k.sum())) - np.repeat(np.cumsum(k) - k, k)
        keep(np.repeat(rows, k), by_group[np.repeat(starts[gid[rows]], k) + local])
        cut = stop
    return np.concatenate(out) if out else np.empty((0, 2), np.int64)


def _clip_convex(subject: list, clip: list) -> list:
    """The part of 2D polygon `subject` inside the convex polygon `clip` (Sutherland-Hodgman)."""
    if _polygon_area(clip)[0] == 0:
        return []
    if sum(clip[i - 1][0] * clip[i][1] - clip[i][0] * clip[i - 1][1] for i in range(len(clip))) < 0:
        clip = clip[::-1]  # counter-clockwise, so inside is to the left of each edge
    poly = subject
    for i in range(len(clip)):
        if not poly:
            break
        (ax, ay), (bx, by) = clip[i - 1], clip[i]
        side = [(bx - ax) * (y - ay) - (by - ay) * (x - ax) for x, y in poly]
        out = []
        for k in range(len(poly)):
            p, q, sp, sq = poly[k - 1], poly[k], side[k - 1], side[k]
            if (sp < 0) != (sq < 0):
                f = sp / (sp - sq)
                out.append((p[0] + (q[0] - p[0]) * f, p[1] + (q[1] - p[1]) * f))
            if sq >= 0:
                out.append(q)
        poly = out
    return poly


def _polygon_area(poly: list) -> tuple[float, float, float]:
    """(area, centroid x, centroid y) of a simple 2D polygon (area 0 when degenerate)."""
    if len(poly) < 3:
        return 0.0, 0.0, 0.0
    a = cx = cy = 0.0
    for i in range(len(poly)):
        (x0, y0), (x1, y1) = poly[i - 1], poly[i]
        cross = x0 * y1 - x1 * y0
        a += cross
        cx += (x0 + x1) * cross
        cy += (y0 + y1) * cross
    if abs(a) < 1e-30:
        return 0.0, 0.0, 0.0
    return abs(a) / 2, cx / (3 * a), cy / (3 * a)


def _image_problems(tree, seen: set[str] | None = None) -> list[str]:
    """Image texture nodes that feed something but have no image, or whose image has no data and no file."""
    seen = set() if seen is None else seen
    problems = []
    for node in tree.nodes:
        if node.type == "GROUP" and node.node_tree and node.node_tree.name not in seen:
            seen.add(node.node_tree.name)
            problems += _image_problems(node.node_tree, seen)
        elif node.type in ("TEX_IMAGE", "TEX_ENVIRONMENT") and not node.mute:
            if not any(out.is_linked for out in node.outputs):
                continue
            img = node.image
            if img is None:
                problems.append(f"image node {node.name!r} in {tree.name!r} has no image (renders pink)")
            elif not _image_ok(img):
                problems.append(f"image {img.name!r} is missing: {img.filepath} (renders pink)")
    return problems


def _image_ok(img) -> bool:
    """Packed and generated images are fine; file-backed ones need their file on disk (a buffer still loaded from a
    file deleted since does not survive saving and reopening)."""
    if img.packed_file or img.source in ("GENERATED", "VIEWER"):
        return True
    if img.source not in ("FILE", "TILED", "SEQUENCE", "MOVIE"):
        return bool(img.has_data)
    path = bpy.path.abspath(img.filepath, library=img.library)
    if img.source == "TILED":
        return bool(img.tiles) and all(Path(path.replace("<UDIM>", str(t.number))).is_file() for t in img.tiles)
    return Path(path).is_file()


@dataclass
class _Shell:
    """A connected shell (faces joined by edges) of a closed mesh, in local coordinates."""

    volume: float
    faces: list[list[tuple[float, float, float]]]
    lo: np.ndarray
    hi: np.ndarray


def _shells(bm) -> list[_Shell]:
    """Each connected shell of a closed bmesh with its signed volume."""
    seen: set = set()
    shells = []
    for start in bm.faces:
        if start in seen:
            continue
        seen.add(start)
        stack, volume, faces = [start], 0.0, []
        while stack:
            face = stack.pop()
            faces.append([tuple(v.co) for v in face.verts])
            v0 = face.verts[0].co
            for i in range(1, len(face.verts) - 1):  # fan of tetrahedra from the origin
                volume += v0.dot(face.verts[i].co.cross(face.verts[i + 1].co)) / 6.0
            for edge in face.edges:
                for other in edge.link_faces:
                    if other not in seen:
                        seen.add(other)
                        stack.append(other)
        pts = np.array([p for f in faces for p in f])
        shells.append(_Shell(volume, faces, pts.min(0), pts.max(0)))
    return shells


def _cavity(shell: _Shell, shells: list[_Shell]) -> bool:
    """Is this negative shell the inner wall of a hollow, i.e. enclosed by a positive shell of the same mesh?"""
    p = np.array(shell.faces[0][0])
    for outer in shells:
        if outer is shell or outer.volume <= 0:
            continue
        if np.all(outer.lo <= shell.lo) and np.all(shell.hi <= outer.hi) and _inside(p, outer):
            return True
    return False


def _inside(p: np.ndarray, shell: _Shell) -> bool:
    """Ray parity: an odd number of crossings along a skewed ray means p is inside the closed shell."""
    verts = [v for f in shell.faces for v in f]
    polys, k = [], 0
    for f in shell.faces:
        polys.append(list(range(k, k + len(f))))
        k += len(f)
    tree = BVHTree.FromPolygons(verts, polys)
    return _crossings(tree, Vector(p.tolist()), _RAYS[0]) % 2 == 1
