"""Scene setup: render presets, engine selection, 9:16 output, GPU compute."""

from __future__ import annotations

import contextlib

import bpy

from bsa.short import Short

PRESETS = {
    "still": {
        "percent": 50,
        "cycles": {"samples": 16, "denoise": True},
        "eevee": {},
    },
    "preview": {
        "percent": 50,
        "cycles": {"samples": 32, "denoise": True},
        "eevee": {},
    },
    "final": {
        "percent": 100,
        "cycles": {"samples": 256, "adaptive_threshold": 0.01, "denoise": True, "motion_blur": True},
        "eevee": {"samples": 64, "motion_blur": True},
    },
}

_GPU_ORDER = ("OPTIX", "CUDA", "HIP", "METAL", "ONEAPI")


def eevee_id() -> str:
    """EEVEE's engine id in the running Blender (BLENDER_EEVEE_NEXT in 4.x, BLENDER_EEVEE in 5.x)."""
    ids = [i.identifier for i in bpy.types.RenderSettings.bl_rna.properties["engine"].enum_items]
    for cand in ("BLENDER_EEVEE_NEXT", "BLENDER_EEVEE"):
        if cand in ids:
            return cand
    raise RuntimeError(f"no EEVEE engine among {ids}")


def use_gpu() -> str:
    """Enable the best available Cycles device; return its type or "NONE". Never raises."""
    try:
        addon = bpy.context.preferences.addons.get("cycles")
        if addon is None:
            return "NONE"
        prefs = addon.preferences
        for kind in _GPU_ORDER:
            try:
                prefs.compute_device_type = kind
                prefs.get_devices()
            except (TypeError, ValueError, RuntimeError):
                continue
            if any(d.type == kind for d in prefs.devices):
                for d in prefs.devices:
                    d.use = d.type == kind
                return kind
        prefs.compute_device_type = "NONE"
    except Exception:  # noqa: BLE001, S110 - GPU probing must never break a render
        pass
    return "NONE"


def current_engine(scene) -> str:
    """The scene's engine as "cycles" or "eevee"."""
    return "cycles" if scene.render.engine == "CYCLES" else "eevee"


def resolve_engine(scene, engine: str | None) -> str:
    """`engine` if given ("cycles" | "eevee"), otherwise the scene's current engine."""
    if engine is None:
        return current_engine(scene)
    if engine not in ("cycles", "eevee"):
        raise ValueError(f"unknown engine {engine!r}")
    return engine


@contextlib.contextmanager
def preserved_render_state(scene):
    """Restore every render setting a preset touches (engine, resolution %, samples, denoise, blur, device)."""
    r, cy, ev = scene.render, scene.cycles, scene.eevee
    snap = {
        "engine": r.engine,
        "percent": r.resolution_percentage,
        "blur": r.use_motion_blur,
        "cy": (cy.device, cy.samples, cy.use_denoising, cy.use_adaptive_sampling, cy.adaptive_threshold),
        "ev_samples": ev.taa_render_samples,
        "ev_blur": getattr(ev, "use_motion_blur", None),
        "ev_rays": getattr(ev, "use_raytracing", None),
    }
    try:
        yield
    finally:
        r.engine = snap["engine"]
        r.resolution_percentage = snap["percent"]
        r.use_motion_blur = snap["blur"]
        cy.device, cy.samples, cy.use_denoising, cy.use_adaptive_sampling, cy.adaptive_threshold = snap["cy"]
        ev.taa_render_samples = snap["ev_samples"]
        if snap["ev_blur"] is not None:
            ev.use_motion_blur = snap["ev_blur"]
        if snap["ev_rays"] is not None:
            ev.use_raytracing = snap["ev_rays"]


def _motion_blur(scene, on: bool) -> None:
    scene.render.use_motion_blur = on
    if hasattr(scene.eevee, "use_motion_blur"):
        scene.eevee.use_motion_blur = on


def apply_preset(scene, engine: str, preset: str, percent: int | None = None) -> None:
    """Apply a named preset for the given engine. `percent` overrides the resolution percentage. Every EEVEE preset
    turns ray tracing on (Blender 4.2 and later): without it glossy water, glass and metal reflect nothing and
    render flat."""
    if preset not in PRESETS:
        raise ValueError(f"unknown preset {preset!r}; choose from {sorted(PRESETS)}")
    if engine not in ("cycles", "eevee"):
        raise ValueError(f"unknown engine {engine!r}")
    p = PRESETS[preset]
    r = scene.render
    r.resolution_percentage = p["percent"] if percent is None else percent
    cfg = p[engine]
    if engine == "cycles":
        r.engine = "CYCLES"
        cy = scene.cycles
        cy.device = "GPU" if use_gpu() != "NONE" else "CPU"
        cy.samples = cfg["samples"]
        cy.use_denoising = cfg["denoise"]
        thr = cfg.get("adaptive_threshold")
        cy.use_adaptive_sampling = thr is not None
        if thr is not None:
            cy.adaptive_threshold = thr
    else:
        r.engine = eevee_id()
        if "samples" in cfg:
            scene.eevee.taa_render_samples = cfg["samples"]
        if hasattr(scene.eevee, "use_raytracing"):
            scene.eevee.use_raytracing = True
    _motion_blur(scene, cfg.get("motion_blur", False))


def setup_scene(scene, short: Short, engine: str, preset: str) -> None:
    """1080x1920 (from the short), 30 fps, AgX, PNG 8-bit RGB, GPU compute when available. A short building its own
    scene also removes the untouched factory scene (`remove_factory_scene`), so its names stay clean. Stamps the
    short's own look ("fresh" or "brand") on the scene as `bsa_look`, so look.brand_view keeps the brand's view off a
    fresh short even when build.py does not pass the short."""
    remove_factory_scene(keep=scene)
    scene["bsa_look"] = str(getattr(short, "look", "") or "")
    r = scene.render
    r.resolution_x, r.resolution_y = short.width, short.height
    r.fps = short.fps
    r.fps_base = 1.0
    scene.view_settings.view_transform = "AgX"
    if hasattr(r.image_settings, "media_type"):
        r.image_settings.media_type = "IMAGE"
    r.image_settings.file_format = "PNG"
    r.image_settings.color_mode = "RGB"
    r.image_settings.color_depth = "8"
    apply_preset(scene, engine, preset)



def remove_scenes(names) -> None:
    """Remove the named scenes (missing ones are skipped) and the data only they used (objects, their meshes,
    cameras, lights, materials, images, node groups, actions, worlds, collections), so a rebuild in the same Blender
    session (MCP mode) gets its exact object names back instead of "Stand.001" (contact:<name> tags and the checks
    refer to objects by name). Data the scenes did not use, or still used elsewhere, is never touched: in MCP mode the
    open file is the user's. The untouched factory scene goes too (`remove_factory_scene`)."""
    scenes = [bpy.data.scenes[n] for n in names if n in bpy.data.scenes]
    owned: set = set()
    for sc in scenes:
        _own(sc, owned)
    for sc in scenes:
        bpy.data.scenes.remove(sc)
    _purge(owned)
    remove_factory_scene()


FACTORY_SCENE = "Scene"
FACTORY_OBJECTS = {"Cube": "MESH", "Light": "LIGHT", "Camera": "CAMERA"}  # what a new Blender file holds


def remove_factory_scene(keep=None) -> bool:
    """Remove the factory scene ("Scene", holding nothing but the factory Cube, Light and Camera, or nothing) and the
    data only it used, when the short builds its own scenes: else its "Camera" makes the short's camera "Camera.001"
    (object names are one namespace across scenes). A scene the user changed (any other object, or a factory object
    of another type) is never touched, nor `keep`, nor a scene a short built (stamped "bsa_timeline"). When it is the
    only scene left, only its objects go (Blender keeps one scene); the next `setup_scene` removes it. Returns
    whether anything was removed."""
    sc = bpy.data.scenes.get(FACTORY_SCENE)
    if sc is None or sc == keep or "bsa_timeline" in sc:
        return False
    objs = list(sc.collection.all_objects)
    if any(FACTORY_OBJECTS.get(o.name) != o.type or len(o.users_scene) > 1 for o in objs):
        return False
    alone = len(bpy.data.scenes) == 1
    if alone and not objs:
        return False
    owned: set = set()
    if alone:
        for obj in objs:
            _collect(obj, owned)
        bpy.data.batch_remove(objs)
        owned.difference_update(objs)
    else:
        _own(sc, owned)
        bpy.data.scenes.remove(sc)
    _purge(owned)
    return True


def _own(sc, owned: set) -> None:
    """The scene's world, collections and objects (with the data they use) into `owned`."""
    _collect(sc.world, owned)
    for coll in sc.collection.children_recursive:
        owned.add(coll)
    for obj in sc.collection.all_objects:
        _collect(obj, owned)


def _purge(owned: set) -> None:
    """Remove what in `owned` nothing uses any more, until nothing more is freed."""
    while True:  # a freed object frees its mesh, which frees its materials, and so on
        unused = [i for i in owned if _alive(i) and i.users == 0]
        if not unused:
            return
        owned.difference_update(unused)
        bpy.data.batch_remove(unused)


def _alive(id_block) -> bool:
    try:
        return id_block.users >= 0
    except ReferenceError:
        return False


def _collect(id_block, owned: set) -> None:
    """id_block and the data it uses, recursively."""
    if id_block is None or id_block in owned:
        return
    owned.add(id_block)
    anim = getattr(id_block, "animation_data", None)
    if anim is not None and anim.action is not None:
        _collect(anim.action, owned)
    if isinstance(id_block, bpy.types.Object):
        _collect(id_block.data, owned)
        for slot in id_block.material_slots:
            _collect(slot.material, owned)
    for mat in getattr(id_block, "materials", None) or []:  # mesh, curve and text data
        _collect(mat, owned)
    # a material's or world's own node tree goes with it; node groups and images are data of their own
    tree = id_block if isinstance(id_block, bpy.types.NodeTree) else getattr(id_block, "node_tree", None)
    if tree is not None:
        for node in tree.nodes:
            _collect(getattr(node, "image", None), owned)
            if node.type == "GROUP":
                _collect(node.node_tree, owned)
