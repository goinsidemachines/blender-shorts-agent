from pathlib import Path

import pytest

from bsa import heavy

HERE = Path(__file__).resolve().parent


def test_the_size_warning_names_the_size_and_the_cause():
    msg = heavy.size_message(1096.6)
    assert "1097 MB" in msg and "many copied meshes" in msg


def test_copies_threshold_is_twenty():
    assert not heavy.over_copy_limit(19) and heavy.over_copy_limit(20)


def test_the_copies_message_says_how_to_share_a_mesh():
    assert "bpy.data.objects.new(name, shared_mesh)" in heavy.copies_message(100, "Tree.001")


def test_the_boolean_message_says_to_bake_it():
    msg = heavy.boolean_message("Drum", "Cut", "an animated parent (Spin)")
    assert "Drum" in msg and "Apply it once at build time" in msg


def test_the_freestyle_message_names_the_chunk_setting():
    assert "render.chunk_frames" in heavy.freestyle_message(900)


@pytest.mark.blender
def test_scenescan_in_blender(blender_run):
    result = blender_run(HERE / "blender" / "test_scenescan.py", timeout=300)
    assert result.returncode == 0, result.stdout + result.stderr
    for name in (
        "test_boolean_on_an_animated_object_is_warned",
        "test_boolean_on_an_animated_parent_is_warned",
        "test_boolean_on_an_animated_operand_is_warned",
        "test_boolean_on_a_static_object_is_not_warned",
        "test_many_copied_meshes_are_warned",
        "test_objects_sharing_one_mesh_are_not_warned",
        "test_different_shapes_are_not_copies",
        "test_big_blend_is_warned",
        "test_freestyle_over_a_long_range_is_info",
    ):
        assert f"PASS {name}" in result.stdout, result.stdout
