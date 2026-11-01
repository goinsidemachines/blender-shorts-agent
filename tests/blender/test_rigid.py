import math

import bpy

from bsa.blender import rigid
from bsa.fields.physics import verify
from bsa.short import FPS

G = 9.81
L = 1.0


def _pendulum(theta0):
    """A build function for rigid_body_sim: a passive anchor at the origin, a small ball hung L below it on a hinge
    about y, released from rest at theta0 (rad)."""

    def build(sc):
        anchor_mesh = bpy.data.meshes.new("Anchor")
        anchor_mesh.from_pydata(*_box(0.02))
        anchor = bpy.data.objects.new("Anchor", anchor_mesh)
        bob_mesh = bpy.data.meshes.new("Bob")
        bob_mesh.from_pydata(*_box(0.05))
        bob = bpy.data.objects.new("Bob", bob_mesh)
        bob.location = (L * math.sin(theta0), 0.0, -L * math.cos(theta0))
        for o in (anchor, bob):
            sc.collection.objects.link(o)
        rigid.add_body(sc, anchor, "PASSIVE", shape="BOX")
        rigid.add_body(sc, bob, mass=1.0, shape="SPHERE")
        rigid.add_constraint(sc, anchor, bob, "HINGE", location=(0, 0, 0), rotation=(math.pi / 2, 0, 0))
        return [bob]

    return build


def _box(h):
    verts = [(x, y, z) for x in (-h, h) for y in (-h, h) for z in (-h, h)]
    faces = [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)]
    return verts, [], faces


def _angles(mats):
    return [math.atan2(m.translation.x, -m.translation.z) for m in mats]


def _run(theta0, frames=300):
    return rigid.rigid_body_sim(_pendulum(theta0), 1, frames)["Bob"]


def test_hinge_pendulum_period_at_the_shorts_fps():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scenes = len(bpy.data.scenes)
    mats = _run(0.1)
    assert len(mats) == 300
    assert len(bpy.data.scenes) == scenes, "the throwaway scene must be deleted"
    assert "Bob" not in bpy.data.objects, "objects made only for the sim are removed with it"
    th = _angles(mats)
    assert abs(th[0] - 0.1) < 1e-6, th[0]
    t = [i / FPS for i in range(len(th))]
    period = verify.period_from_series(t, th)
    expected = 2 * math.pi * math.sqrt(L / G)
    assert abs(period - expected) / expected < 0.01, (period, expected)
    assert 0.09 < max(th[len(th) // 2 :]) < 0.101, max(th[len(th) // 2 :])  # no damping by default


def test_a_second_run_in_the_same_session_repeats_and_never_reuses_a_stale_cache():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    first = _run(0.1, 120)
    again = _run(0.1, 120)
    assert max(max(abs(a[i][j] - b[i][j]) for i in range(4) for j in range(4)) for a, b in zip(first, again)) < 1e-6
    small = _angles(_run(0.05, 120))
    assert abs(small[0] - 0.05) < 1e-6 and 0.045 < max(small) < 0.0505, max(small)


def test_keyframe_matrices_bakes_the_motion_onto_a_render_object():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    sc = bpy.context.scene
    mats = _run(0.1, 60)
    ball = bpy.data.objects.new("Ball", None)
    sc.collection.objects.link(ball)
    rigid.keyframe_matrices(ball, mats, frame0=10)
    for k in (0, 17, 59):
        sc.frame_set(10 + k)
        assert (ball.matrix_world.translation - mats[k].translation).length < 1e-5, k
        assert ball.matrix_world.to_quaternion().rotation_difference(mats[k].to_quaternion()).angle < 1e-4, k
