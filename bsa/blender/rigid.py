"""Blender's own rigid-body physics (Bullet), run in a throwaway scene and sampled into per-frame matrices.

    def build(sc):                       # make the bodies in the throwaway scene, return the ones to sample
        ...link objects into sc.collection...
        rigid.add_body(sc, anchor, "PASSIVE")
        rigid.add_body(sc, bob, mass=1.0, shape="SPHERE")
        rigid.add_constraint(sc, anchor, bob, "HINGE", location=pivot, rotation=(pi / 2, 0, 0))
        return [bob]

    mats = rigid.rigid_body_sim(build, f0, f1)["Bob"]   # one matrix_world per frame f0..f1
    rigid.keyframe_matrices(render_bob, mats, frame0=f0)

The sim runs at the short's fps (a scene from bpy.data.scenes.new is 24 fps, which runs everything 25% slow when
sampled at 30), on a freshly freed point cache, and the scene is deleted afterwards. Objects that lived only in it
are removed too, so build proxies there and bake their motion onto the render objects. Read the "Blender rigid
bodies" section of playbook/fields/physics.md for when to use Bullet and its traps.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

import bpy

from bsa.blender.bake import keyframe_series
from bsa.short import FPS


def rigid_body_sim(
    build: Callable[[bpy.types.Scene], Sequence[bpy.types.Object]],
    frame_start: int,
    frame_end: int,
    *,
    fps: int = FPS,
    substeps: int = 10,
    iterations: int = 20,
    gravity: Sequence[float] = (0.0, 0.0, -9.81),
    name: str = "bsa_rigid_sim",
) -> dict[str, list]:
    """Run Bullet in a throwaway scene and return {object name: [matrix_world per frame, frame_start..frame_end]}.

    `build(scene)` makes the bodies (add_body, add_constraint) in the scene it is given, which already has a
    rigid-body world, and returns the objects to sample. Frame frame_start is the bodies' starting pose. Raise
    `substeps` for fast or small bodies (they tunnel through thin parts at a few steps per frame)."""
    sc = bpy.data.scenes.new(name)
    try:
        sc.render.fps, sc.render.fps_base = int(fps), 1.0
        sc.frame_start, sc.frame_end = frame_start, frame_end
        sc.use_gravity = True
        sc.gravity = tuple(gravity)
        with bpy.context.temp_override(scene=sc):
            bpy.ops.rigidbody.world_add()
        world = sc.rigidbody_world
        world.substeps_per_frame = substeps
        world.solver_iterations = iterations
        world.time_scale = 1.0
        objs = list(build(sc))
        cache = world.point_cache
        cache.frame_start, cache.frame_end = frame_start, frame_end  # the default ends at 250: nothing moves after
        with bpy.context.temp_override(scene=sc, point_cache=cache):
            bpy.ops.ptcache.free_bake()
        dg = sc.view_layers[0].depsgraph
        out: dict[str, list] = {o.name: [] for o in objs}
        sc.frame_set(frame_start)  # reset: the sim restarts from the starting pose at the cache's first frame
        for f in range(frame_start, frame_end + 1):
            sc.frame_set(f)  # one frame at a time: Bullet only steps forward from the frame before
            for o in objs:
                out[o.name].append(o.evaluated_get(dg).matrix_world.copy())
        return out
    finally:
        _delete_scene(sc)


def add_body(
    scene,
    obj,
    type: str = "ACTIVE",
    *,
    mass: float = 1.0,
    shape: str = "CONVEX_HULL",
    friction: float = 0.5,
    restitution: float = 0.0,
    margin: float | None = None,
    damping: float = 0.0,
    kinematic: bool = False,
):
    """Make obj (already linked to `scene`) a rigid body of that scene's world: type "ACTIVE" or "PASSIVE", shape
    "BOX", "SPHERE", "CAPSULE", "CYLINDER", "CONE", "CONVEX_HULL" or "MESH". Damping defaults to 0 (Blender's
    own defaults, 0.04 linear and 0.1 angular, quietly slow a swing down). `margin` sets the collision margin
    (keep it well under the thickness of thin parts). The origin must be the centre of mass. Returns obj.rigid_body."""
    with bpy.context.temp_override(scene=scene, object=obj, active_object=obj, selected_objects=[obj]):
        bpy.ops.rigidbody.object_add(type=type)
    rb = obj.rigid_body
    rb.mass = mass
    rb.collision_shape = shape
    rb.friction = friction
    rb.restitution = restitution
    rb.linear_damping = rb.angular_damping = damping
    rb.kinematic = kinematic
    if margin is not None:
        rb.use_margin = True
        rb.collision_margin = margin
    return rb


def add_constraint(
    scene, a, b, type: str = "HINGE", *, location: Sequence[float] = (0, 0, 0), rotation: Sequence[float] = (0, 0, 0)
):
    """Join bodies a and b with a constraint ("HINGE", "FIXED", "POINT", "SLIDER", "PISTON", "GENERIC", ...) held
    by a new empty at `location` with euler `rotation`. A hinge turns about the empty's local Z (rotation
    (pi/2, 0, 0) turns about world y). Collisions between a and b are off. Returns the empty."""
    empty = bpy.data.objects.new(f"{a.name}-{b.name} {type.lower()}", None)
    empty.location, empty.rotation_euler = tuple(location), tuple(rotation)
    scene.collection.objects.link(empty)
    with bpy.context.temp_override(scene=scene, object=empty, active_object=empty, selected_objects=[empty]):
        bpy.ops.rigidbody.constraint_add(type=type)
    con = empty.rigid_body_constraint
    con.object1, con.object2 = a, b
    con.disable_collisions = True
    return empty


def keyframe_matrices(obj, matrices: Sequence, frame0: int = 1) -> None:
    """Key obj's location and rotation on every frame from frame0 from one world matrix per frame (rigid_body_sim's
    output), with linear interpolation. Eulers stay continuous (no 360 degree flips). obj's parent, if any, must
    not move while these frames play. Scale is left alone. The object is put back to the pose it had before
    (matrix_basis), keys unchanged, so code that reads it next sees the rest pose."""
    rest = obj.matrix_basis.copy()
    locs: list[tuple[float, ...]] = []
    rots: list[tuple[float, ...]] = []
    quat = obj.rotation_mode == "QUATERNION"
    prev = None
    for m in matrices:
        obj.matrix_world = m
        bpy.context.view_layer.update()
        locs.append(tuple(obj.location))
        if quat:
            q = obj.rotation_quaternion.copy()
            if prev is not None and q.dot(prev) < 0:
                q.negate()
            prev = q
            rots.append(tuple(q))
        else:
            e = obj.matrix_basis.to_euler(obj.rotation_mode, prev) if prev is not None else obj.rotation_euler.copy()
            prev = e
            rots.append(tuple(e))
    for axis in range(3):
        keyframe_series(obj, "location", axis, [p[axis] for p in locs], frame0=frame0)
    path = "rotation_quaternion" if quat else "rotation_euler"
    for axis in range(len(rots[0]) if rots else 0):
        keyframe_series(obj, path, axis, [r[axis] for r in rots], frame0=frame0)
    obj.matrix_basis = rest


def _delete_scene(sc) -> None:
    """Delete the throwaway scene, its rigid-body collections and every object that lived only in it."""
    objs = list(sc.objects)
    world = sc.rigidbody_world
    cols = [c for c in (world.collection, world.constraints) if c is not None] if world else []
    bpy.data.scenes.remove(sc)
    for o in objs:
        if not o.users_scene:
            data = o.data
            bpy.data.objects.remove(o)
            if data is not None and data.users == 0 and isinstance(data, bpy.types.Mesh):
                bpy.data.meshes.remove(data)
    for c in cols:
        if c.users == 0 or not c.objects:
            bpy.data.collections.remove(c)
