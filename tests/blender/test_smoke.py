import bpy


def test_blender_version():
    assert bpy.app.version >= (4, 5, 0)
