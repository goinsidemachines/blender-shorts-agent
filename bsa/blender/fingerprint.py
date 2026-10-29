"""A content fingerprint per shot: a hash of everything that can change that shot's pixels, so a rebuild that
changes one shot leaves the other shots' frames, checks and anchors current.

Two parts:

- static: every datablock the shot's scene reaches (render settings, world, view layers and collections, objects
  and their modifiers, constraints and particle settings, mesh / curve / text data, materials and node trees, images
  and fonts with their files' size and time, lights, cameras, the compositor), walked generically through RNA, so a
  property nobody thought of is still in it. Animated and driven properties are left out of this part.
- per frame, for every frame of the shot: each object's evaluated world matrix and visibility, the scene camera,
  every animated or driven property's value, and the evaluated geometry of objects that change with time on their
  own (geometry nodes reading the scene time or simulating, physics, wave and build modifiers).

So a key moved inside shot 2 changes shot 2 (and shot 1 only if the curve moves there too), and a material that only
shot 1's scene uses changes shot 1 only. The objects' bsa_* custom properties (bsa_tags: hero, contact, grounded,
overlay, surface) change what the checks find but not a pixel: they are not in the fingerprint, only in its "tags"
part, which the checks key on. When unsure the walk includes a property: a false "changed" costs a render,
a false "unchanged" would ship stale frames. Runs inside Blender (bpy + numpy)."""

from __future__ import annotations

import hashlib
import os
import struct
from pathlib import Path

import bpy
import numpy as np

from bsa.blender.bake import _fcurves
from bsa.freshness import FINGERPRINT_VERSION as VERSION  # bump it there: every shot then counts as changed once

MAX_DEPTH = 10  # nested structs followed below an ID (RNA has cycles; visited structs are cut anyway)

_RENDER_CODE = Path(__file__).with_name("scene.py")  # the render presets apply_preset sets live here

# never part of the hash: runtime state, UI, identity, and what the per-frame part samples instead
_SKIP = frozenset({
    "rna_type", "bl_rna", "id_data", "original", "users", "session_uid", "is_evaluated", "is_dirty",
    "is_runtime_data", "is_missing", "is_editmode", "is_embedded_data", "is_library_indirect", "library",
    "library_weak_reference", "override_library", "preview", "asset_data", "use_fake_user", "use_extra_user",
    "tag", "animation_data", "depsgraph", "bound_box", "id_type", "name_full",
})  # fmt: skip
_SKIP_BY_TYPE = {
    "Scene": {"frame_current", "frame_current_final", "frame_float", "frame_subframe", "frame_preview_start", "frame_preview_end",
              "use_preview_range", "cursor", "tool_settings", "keying_sets", "keying_sets_all", "statistics",
              "transform_orientation_slots", "frame_start", "frame_end"},
    "Object": {"location", "rotation_euler", "rotation_quaternion", "rotation_axis_angle", "rotation_mode",
               "scale", "delta_location", "delta_rotation_euler", "delta_rotation_quaternion", "delta_scale",
               "matrix_world", "matrix_local", "matrix_basis", "matrix_parent_inverse", "dimensions", "mode",
               "active_material", "active_material_index", "active_shape_key_index", "motion_path",
               "hide_viewport", "hide_select"},
    "Mesh": {"attributes", "vertices", "edges", "polygons", "loops", "loop_triangles", "loop_triangle_polygons",
             "polygon_normals", "vertex_normals", "corner_normals", "normals_domain", "color_attributes",
             "uv_layers", "vertex_colors", "skin_vertices", "vertex_creases", "edge_creases", "total_vert_sel",
             "total_edge_sel", "total_face_sel", "texture_mesh", "vertex_paint_mask"},
    "Image": {"bindcode", "has_data", "pixels", "size", "resolution", "depth", "channels", "is_float",
              "frame_duration", "render_slots"},
    "PackedFile": {"data"},
    "RenderSettings": {"ffmpeg"},  # video encoding: frames are PNGs
    "View3DShading": {"wireframe_color_type"},
    "PoseBone": {"matrix", "matrix_channel", "head", "tail", "length"},
    "GreasePencilDrawing": {"strokes"},
    "ShapeKey": {"data", "points"},
    "Curves": {"points", "curves", "position_data", "curve_offset_data", "normals"},
    "PointCloud": {"points"},
    "ParticleSystem": {"particles", "child_particles"},
    "ViewLayer": {"active_layer_collection", "objects"},
    "Collection": {"all_objects", "children_recursive"},
    "LayerCollection": {"children_recursive"},
}  # fmt: skip
_NODE_UI = {"location", "location_absolute", "width", "height", "dimensions", "select", "show_options",
            "show_preview", "show_texture", "color", "use_custom_color", "parent", "internal_links", "hide",
            "show_expanded", "warning_propagation"}  # fmt: skip
_SKIP_ID_TYPES = ("Action", "Library", "Screen", "WindowManager", "WorkSpace", "Brush", "Palette")
# modifiers that change an object's geometry with time even when nothing it reads is animated
_TIME_MODIFIERS = {"WAVE", "BUILD", "CLOTH", "SOFT_BODY", "FLUID", "DYNAMIC_PAINT", "PARTICLE_SYSTEM", "EXPLODE",
                   "OCEAN", "MESH_CACHE", "MESH_SEQUENCE_CACHE", "SURFACE", "COLLISION"}  # fmt: skip
_TIME_NODES = ("GeometryNodeInputSceneTime", "GeometryNodeSimulationInput", "GeometryNodeSimulationOutput",
               "GeometryNodeBake")  # fmt: skip
_TRANSFORM_PATHS = ("location", "rotation_euler", "rotation_quaternion", "rotation_axis_angle", "scale",
                    "delta_location", "delta_rotation_euler", "delta_rotation_quaternion", "delta_scale")  # fmt: skip


def _type_name(struct) -> str:
    return struct.bl_rna.identifier


def _skips(struct) -> frozenset:
    names = set(_SKIP)
    rna = struct.bl_rna
    while rna is not None:  # the type and its bases (a ShaderNodeBsdfPrincipled is a Node)
        names |= _SKIP_BY_TYPE.get(rna.identifier, set())
        if rna.identifier in ("Node", "NodeSocket", "NodeSocketInterface", "NodeTreeInterfaceSocket"):
            names |= _NODE_UI
        rna = rna.base
    return frozenset(names)


class _Hasher:
    """Hashes datablocks once each per run (a material shared by two scenes is walked once)."""

    def __init__(self):
        self.id_hash: dict[tuple, bytes] = {}
        self.children: dict[tuple, list] = {}  # the datablocks each one points to
        self.files: dict[str, list] = {}  # external files that went in: {path: [size, mtime_ns]}
        self._props: dict[str, tuple] = {}
        self._skip: dict[str, frozenset] = {}

    # ---- identity

    @staticmethod
    def key(idb) -> tuple:
        lib = idb.library.filepath if getattr(idb, "library", None) else ""
        return (type(idb).__name__, idb.name, lib)

    # ---- one datablock

    def id_digest(self, idb, found: list) -> bytes:
        """The static hash of one datablock; datablocks it points to are appended to `found` (walked later)."""
        k = self.key(idb)
        if k not in self.id_hash:
            h = hashlib.sha256()
            h.update(repr(k).encode())
            mine: list = []
            self._walk(idb, h, 0, set(), _animated_paths(idb), mine)
            self._extra(idb, h)
            self.id_hash[k], self.children[k] = h.digest(), mine
        found += self.children[k]
        return self.id_hash[k]

    def _extra(self, idb, h) -> None:
        """What the generic walk leaves out on purpose: big arrays (as numpy bytes) and files on disk."""
        if isinstance(idb, bpy.types.Mesh):
            _mesh_digest(idb, h)
        if isinstance(idb, bpy.types.Key):
            for kb in idb.key_blocks:
                co = np.empty(len(kb.data) * 3, np.float32)
                if len(kb.data):
                    try:
                        kb.data.foreach_get("co", co)
                    except (AttributeError, TypeError, RuntimeError):
                        co = np.array([c for p in kb.data for c in getattr(p, "co", ())], np.float32)
                h.update(kb.name.encode() + co.tobytes())
        if isinstance(idb, bpy.types.Image):
            if idb.packed_file is not None:
                h.update(b"packed%d" % idb.packed_file.size)
            elif idb.source in ("FILE", "SEQUENCE", "MOVIE", "TILED"):
                self._file(idb.filepath, idb, h)
        if isinstance(idb, bpy.types.VectorFont) and idb.packed_file is None and idb.filepath != "<builtin>":
            self._file(idb.filepath, idb, h)
        if isinstance(idb, (bpy.types.Sound, bpy.types.MovieClip, bpy.types.CacheFile, bpy.types.Volume)):
            self._file(getattr(idb, "filepath", ""), idb, h)

    def _file(self, filepath: str, idb, h) -> None:
        path = bpy.path.abspath(filepath, library=getattr(idb, "library", None)) if filepath else ""
        try:
            st = os.stat(path)
            stat = [st.st_size, st.st_mtime_ns]
        except OSError:
            stat = None
        if path:
            self.files[path] = stat
        h.update(repr((path, stat)).encode())

    # ---- the generic RNA walk

    def _props_of(self, struct) -> tuple:
        name = _type_name(struct)
        props = self._props.get(name)
        if props is None:
            skip = _skips(struct)
            props = tuple((p.identifier, p.type, getattr(p, "is_array", False) or getattr(p, "array_length", 0) > 0)
                          for p in struct.bl_rna.properties if p.identifier not in skip)  # fmt: skip
            self._props[name] = props
        return props

    def _walk(self, struct, h, depth: int, seen: set, animated: set, found: list) -> None:
        try:
            ptr = struct.as_pointer()
        except (AttributeError, ReferenceError):
            ptr = id(struct)
        if ptr in seen:
            h.update(b"@" + _identity(struct).encode())
            return
        seen.add(ptr)
        base = _path_from_id(struct) if animated else None
        for ident, kind, _is_array in self._props_of(struct):
            if animated and base is not None and (f"{base}.{ident}" if base else ident) in animated:
                h.update(b"~" + ident.encode())  # animated: sampled per frame instead
                continue
            try:
                value = getattr(struct, ident)
            except Exception:  # noqa: BLE001 - some properties raise in background mode; their name still counts
                h.update(b"!" + ident.encode())
                continue
            h.update(ident.encode())
            if kind in ("BOOLEAN", "INT", "FLOAT", "STRING", "ENUM"):
                h.update(_enc(value))
            elif kind == "POINTER":
                self._pointer(value, h, depth, seen, animated, found)
            elif kind == "COLLECTION":
                if ident == "attributes":
                    _hash_attributes(value, h)
                    continue
                try:
                    items = list(value)
                except TypeError:
                    continue
                h.update(b"[%d" % len(items))
                for item in items:
                    self._pointer(item, h, depth, seen, animated, found)
                h.update(b"]")

    def _pointer(self, value, h, depth, seen, animated, found) -> None:
        if value is None:
            h.update(b"None")
        elif isinstance(value, bpy.types.ID):
            if type(value).__name__ in _SKIP_ID_TYPES:
                h.update(b"id:" + value.name.encode())
            elif getattr(value, "is_embedded_data", False):  # a material's node tree, a scene's master collection
                h.update(b"embedded:")  # its animation is sampled per frame through its owner (_anim_channels)
                self._walk(value, h, depth + 1, seen, _animated_paths(value), found)
            else:
                h.update(b"id:" + repr(self.key(value)).encode())
                found.append(value)
        elif isinstance(value, bpy.types.bpy_struct):
            if depth >= MAX_DEPTH:
                h.update(b"deep:" + _identity(value).encode())
            else:
                self._walk(value, h, depth + 1, seen, animated, found)
        else:
            h.update(_enc(value))


def _identity(struct) -> str:
    path = _path_from_id(struct)
    return f"{_type_name(struct)}:{path if path is not None else getattr(struct, 'name', '')}"


def _path_from_id(struct) -> str | None:
    if isinstance(struct, bpy.types.ID):
        return ""
    try:
        return struct.path_from_id()
    except (ValueError, AttributeError, TypeError):
        return None


def _enc(value) -> bytes:
    if isinstance(value, bool):
        return b"T" if value else b"F"
    if isinstance(value, int):
        return b"i%d" % value
    if isinstance(value, float):
        return struct.pack("<f", value)
    if isinstance(value, str):
        return b"s" + value.encode("utf-8", "replace") + b"\0"
    if isinstance(value, (set, frozenset)):
        return repr(sorted(value)).encode()
    try:
        arr = np.asarray(value)
        if arr.dtype.kind in "fiub":
            return arr.astype(np.float32 if arr.dtype.kind == "f" else np.int64).tobytes() + repr(arr.shape).encode()
    except Exception:  # noqa: BLE001, S110 - fall back to repr below
        pass
    return repr(value).encode("utf-8", "replace")


_ATTR_WIDTH = {"FLOAT_VECTOR": 3, "FLOAT2": 2, "FLOAT_COLOR": 4, "BYTE_COLOR": 4, "INT32_2D": 2, "INT16_2D": 2,
               "QUATERNION": 4, "FLOAT4X4": 16}  # fmt: skip
_ATTR_KEY = {"FLOAT_VECTOR": "vector", "FLOAT2": "vector", "FLOAT_COLOR": "color", "BYTE_COLOR": "color"}


def _attr_array(attr) -> np.ndarray | bytes:
    """One attribute's values: (n, width) numpy array, or bytes for strings."""
    data = attr.data
    n = len(data)
    if attr.data_type == "STRING":
        return "\0".join(d.value for d in data).encode("utf-8", "replace")
    width = _ATTR_WIDTH.get(attr.data_type, 1)
    floaty = attr.data_type.startswith(("FLOAT", "BYTE_COLOR", "QUATERNION"))
    buf = np.empty(n if attr.data_type == "BOOLEAN" else n * width,
                   bool if attr.data_type == "BOOLEAN" else np.float32 if floaty else np.int32)  # fmt: skip
    key = _ATTR_KEY.get(attr.data_type, "value")
    try:
        if n:
            data.foreach_get(key, buf)
    except (TypeError, RuntimeError, AttributeError):
        buf = np.asarray([getattr(d, key) for d in data], dtype=np.float64)
    return buf.reshape(n, -1) if n else buf.reshape(0, 1)


def _hash_attributes(attributes, h, order: dict | None = None, skip=()) -> None:
    """Every geometry attribute (positions, faces, UVs, sharpness, material indices, colours...) as raw bytes;
    `order` ({domain: index array}) puts a domain's elements in a canonical order first."""
    for attr in attributes:
        name = attr.name
        if "select" in name or name in skip:  # edit-mode selection never renders
            continue
        values = _attr_array(attr)
        h.update(f"{name}|{attr.data_type}|{attr.domain}|{len(attr.data)}".encode())
        if isinstance(values, bytes):
            h.update(values)
            continue
        if order and attr.domain in order and len(values) == len(order[attr.domain]):
            values = values[order[attr.domain]]
        h.update(np.ascontiguousarray(values).tobytes())


def _mesh_digest(mesh, h) -> None:
    """A mesh's geometry independent of the order its edges, faces and face corners happen to be stored in (a
    rebuild can make the same mesh in another order, which renders the same): faces in a canonical order, each
    starting at its lowest vertex, edges sorted; vertices keep their order."""
    n_faces, n_corners = len(mesh.polygons), len(mesh.loops)
    order: dict = {}
    if n_faces and n_corners:
        starts = np.empty(n_faces, np.int64)
        totals = np.empty(n_faces, np.int64)
        mesh.polygons.foreach_get("loop_start", starts)
        mesh.polygons.foreach_get("loop_total", totals)
        verts = np.empty(n_corners, np.int64)
        mesh.loops.foreach_get("vertex_index", verts)
        face = np.repeat(np.arange(n_faces), totals)
        local = np.arange(n_corners) - starts[face]
        lowest = np.minimum.reduceat(verts, starts) if np.all(totals > 0) else np.zeros(n_faces, np.int64)
        at = np.flatnonzero(verts == lowest[face])
        first = at[np.unique(face[at], return_index=True)[1]]  # the first corner of each face at its lowest vertex
        shift = first - starts
        canon_local = (local - shift[face]) % totals[face]
        corner_of = np.empty(n_corners, np.int64)  # canonical (face, position) -> stored corner
        corner_of[starts[face] + canon_local] = np.arange(n_corners)
        cverts = verts[corner_of]

        def nth(k):
            return cverts[starts + np.minimum(k, totals - 1)]

        face_order = np.lexsort((nth(3), nth(2), nth(1), nth(0), totals))
        ft = totals[face_order]
        within = np.arange(n_corners) - np.repeat(np.cumsum(ft) - ft, ft)
        corner_order = corner_of[np.repeat(starts[face_order], ft) + within]
        order = {"FACE": face_order, "CORNER": corner_order}
        h.update(totals[face_order].tobytes() + verts[corner_order].tobytes())
    n_edges = len(mesh.edges)
    if n_edges:
        ev = np.empty(n_edges * 2, np.int64)
        mesh.edges.foreach_get("vertices", ev)
        ev = np.sort(ev.reshape(-1, 2), axis=1)
        edge_order = np.lexsort((ev[:, 1], ev[:, 0]))
        order["EDGE"] = edge_order
        h.update(ev[edge_order].tobytes())
    _hash_attributes(mesh.attributes, h, order, skip=(".edge_verts", ".corner_vert", ".corner_edge"))


# --------------------------------------------------------------------------- animation


_EMBEDDED = ("node_tree", "collection")  # datablocks that live inside their owner (material, world, scene...)


def _anim_channels(idb, prefix: str = "") -> list[tuple[str, int, object]]:
    """(data path, index, fcurve or None for a driver) of everything animated or driven on one datablock, and on
    the datablocks embedded in it (a material's node tree: paths from the material, "node_tree.nodes[...]...")."""
    out = []
    if not prefix:
        for attr in _EMBEDDED:
            inner = getattr(idb, attr, None)
            if isinstance(inner, bpy.types.ID) and getattr(inner, "is_embedded_data", False):
                out += _anim_channels(inner, f"{attr}.")
    ad = getattr(idb, "animation_data", None)
    if ad is None:
        return out
    actions = [ad.action] if ad.action else []
    for track in ad.nla_tracks:
        actions += [strip.action for strip in track.strips if strip.action]
    for action in actions:
        out += [(prefix + fc.data_path, fc.array_index, fc) for fc in _fcurves(action)]
    out += [(prefix + fc.data_path, fc.array_index, None) for fc in ad.drivers]
    return out


def _animated_paths(idb) -> set[str]:
    """Paths animated or driven on this datablock itself (relative to it), left out of the static walk."""
    ad = getattr(idb, "animation_data", None)
    if ad is None:
        return set()
    actions = [ad.action] if ad.action else []
    for track in ad.nla_tracks:
        actions += [strip.action for strip in track.strips if strip.action]
    return {fc.data_path for a in actions for fc in _fcurves(a)} | {fc.data_path for fc in ad.drivers}


def _driver_digest(idb, h) -> None:
    for attr in _EMBEDDED:
        inner = getattr(idb, attr, None)
        if isinstance(inner, bpy.types.ID) and getattr(inner, "is_embedded_data", False):
            _driver_digest(inner, h)
    ad = getattr(idb, "animation_data", None)
    if ad is None:
        return
    for fc in ad.drivers:
        d = fc.driver
        h.update(f"{fc.data_path}[{fc.array_index}]{d.type}{d.expression}".encode())
        for var in d.variables:
            h.update(f"{var.name}{var.type}".encode())
            for t in var.targets:
                h.update(f"{getattr(t.id, 'name', None)}{t.data_path}{t.transform_type}{t.bone_target}".encode())


def _value(idb, path: str, index: int):
    try:
        v = idb.path_resolve(path)
    except (ValueError, AttributeError, TypeError):
        return None
    try:
        return v[index]
    except (TypeError, IndexError, KeyError):
        return v


# --------------------------------------------------------------------------- per frame


def _depsgraph(scene):
    if bpy.context.scene == scene:
        return bpy.context.evaluated_depsgraph_get()
    with bpy.context.temp_override(scene=scene, view_layer=scene.view_layers[0]):
        return bpy.context.evaluated_depsgraph_get()


def _time_dependent(obj) -> bool:
    for mod in getattr(obj, "modifiers", ()):
        if mod.type in _TIME_MODIFIERS:
            return True
        if mod.type == "NODES" and mod.node_group is not None and _tree_uses_time(mod.node_group, set()):
            return True
    return bool(getattr(obj, "particle_systems", ()))


def _tree_uses_time(tree, seen: set) -> bool:
    if tree.name in seen:
        return False
    seen.add(tree.name)
    for node in tree.nodes:
        if node.bl_idname in _TIME_NODES:
            return True
        sub = getattr(node, "node_tree", None)
        if sub is not None and _tree_uses_time(sub, seen):
            return True
    return False


def _geometry_digest(obj, depsgraph, h) -> None:
    ev = obj.evaluated_get(depsgraph)
    data = getattr(ev, "data", None)
    if data is not None and hasattr(data, "attributes"):
        _hash_attributes(data.attributes, h)
        return
    try:
        mesh = ev.to_mesh()
    except RuntimeError:
        h.update(b"no-geometry")
        return
    try:
        _hash_attributes(mesh.attributes, h)
    finally:
        ev.to_mesh_clear()


def _matrices(objects, depsgraph) -> bytes:
    out = np.empty((len(objects), 16), np.float32)
    for i, obj in enumerate(objects):
        out[i] = np.asarray(obj.evaluated_get(depsgraph).matrix_world, np.float32).ravel()
    return out.tobytes()


# --------------------------------------------------------------------------- the fingerprints


def _reachable(hasher: _Hasher, scene) -> tuple[bytes, list]:
    """The static hash of everything `scene` reaches, and those datablocks (to sample their animation)."""
    todo, done, order = [scene], {}, []
    while todo:
        idb = todo.pop()
        k = hasher.key(idb)
        if k in done:
            continue
        found: list = []
        done[k] = hasher.id_digest(idb, found)
        order.append(idb)
        todo += found
    h = hashlib.sha256()
    for k in sorted(done):
        h.update(repr(k).encode() + done[k])
    return h.digest(), order


class _Tee:
    """Feeds the same bytes to two hashes: the fingerprint and one of its parts."""

    def __init__(self, *hashes):
        self.hashes = hashes

    def update(self, data: bytes) -> None:
        for h in self.hashes:
            h.update(data)


def _short(h) -> str:
    return h.hexdigest()[:16]


def _tags(scene) -> str:
    """A hash of every object's bsa_* custom properties in `scene` (the tags the checks read)."""
    h = hashlib.sha256()
    for obj in sorted(scene.objects, key=lambda o: o.name):
        props = sorted((k, str(obj[k])) for k in obj.keys() if k.startswith("bsa_"))  # noqa: SIM118 - an ID, not a dict
        if props:
            h.update(repr((obj.name, props)).encode("utf-8", "replace"))
    return _short(h)


def _static_parts(hasher: _Hasher, ids) -> dict[str, str]:
    """{"data:<type>": hash} of the static part per datablock type (Object, Camera, Material...), so a changed
    fingerprint can say what kind of data changed. Information only: the fingerprint does not use it."""
    by_type: dict[str, list] = {}
    for idb in ids:
        k = hasher.key(idb)
        by_type.setdefault(k[0], []).append((k, hasher.id_hash[k]))
    out = {}
    for kind, items in by_type.items():
        h = hashlib.sha256()
        for k, digest in sorted(items):
            h.update(repr(k).encode() + digest)
        out[f"data:{kind}"] = _short(h)
    return out


def scene_fingerprints(scene, spans, hasher: _Hasher | None = None, reached=None,
                       parts: dict | None = None) -> dict[str, str]:  # fmt: skip
    """{shot id: fingerprint} for the shots (bsa.timeline.Span) that `scene` renders. The scene's current frame is
    restored. `reached` is _reachable's result when the caller walked the scene already. `parts`, when given, gets
    {shot id: {part: hash}}: the static data per datablock type, drivers, animation (the per-frame part) and code
    (version, Blender, render presets), so a changed fingerprint can say which part changed."""
    hasher = hasher or _Hasher()
    static, ids = reached or _reachable(hasher, scene)
    head = hashlib.sha256()
    code = hashlib.sha256()
    tee = _Tee(head, code)
    tee.update(b"bsa-fingerprint %d %s " % (VERSION, bpy.app.version_string.encode()))
    try:
        tee.update(_RENDER_CODE.read_bytes())
    except OSError:
        pass
    head.update(static)
    drivers = hashlib.sha256()
    tee = _Tee(head, drivers)
    for idb in ids:
        _driver_digest(idb, tee)
    common = {**_static_parts(hasher, ids), "drivers": _short(drivers), "code": _short(code), "tags": _tags(scene)}
    objects = sorted(scene.objects, key=lambda o: o.name)
    in_scene = {o.name for o in objects}  # their motion is sampled as evaluated matrices below
    channels = [(idb, path, index, fc) for idb in ids for path, index, fc in _anim_channels(idb)
                if not (isinstance(idb, bpy.types.Object) and idb.name in in_scene and path in _TRANSFORM_PATHS)]
    timed = [o for o in objects if _time_dependent(o)]
    depsgraph = _depsgraph(scene)
    original = scene.frame_current
    out = {}
    try:
        for span in spans:
            fp = head.copy()
            motion = hashlib.sha256()
            h = _Tee(fp, motion)  # the per-frame bytes go into the fingerprint and its "animation" part
            h.update(b"span %d %d" % (span.f0, span.f1))
            for frame in range(span.f0, span.f1 + 1):
                scene.frame_set(frame)
                depsgraph.update()  # a scene that is not the context scene is not evaluated by frame_set alone
                h.update(b"f%d" % frame)
                h.update(_matrices(objects, depsgraph))
                h.update(repr([(o.hide_render, o.visible_get(view_layer=scene.view_layers[0])) for o in objects])
                         .encode())  # fmt: skip
                h.update(_enc(scene.camera.name if scene.camera else None))
                for idb, path, index, fc in channels:
                    ev = idb.evaluated_get(depsgraph) if hasattr(idb, "evaluated_get") else idb
                    h.update(_enc(_value(ev, path, index)))
                    if fc is not None:
                        h.update(_enc(fc.evaluate(frame)))
                for obj in timed:
                    _geometry_digest(obj, depsgraph, h)
            out[span.id] = fp.hexdigest()[:32]
            if parts is not None:
                parts[span.id] = {**common, "animation": _short(motion)}
    finally:
        scene.frame_set(original)
    return out


def fingerprints(groups, parts: dict | None = None) -> tuple[dict[str, str], dict[str, list]]:
    """{shot id: fingerprint} for every (scene, [spans]) in `groups`, and the external files that went in
    ({path: [size, mtime_ns] or None}): a cache of the fingerprints is valid only while those are unchanged.
    `parts`, when given, gets each shot's parts (scene_fingerprints)."""
    hasher = _Hasher()
    groups = list(groups)
    reached = [_reachable(hasher, scene) for scene, _ in groups]  # every static walk before any frame changes
    out: dict[str, str] = {}
    for (scene, spans), r in zip(groups, reached, strict=True):
        out.update(scene_fingerprints(scene, spans, hasher, r, parts))
    return out, hasher.files
