from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent


@pytest.mark.blender
def test_checks_in_blender(blender_run):
    result = blender_run(HERE / "blender" / "test_checks.py", timeout=600)
    assert result.returncode == 0, result.stdout + result.stderr
    for name in (
        "test_missing_material_found",
        "test_missing_image_found",
        "test_inside_out_found",
        "test_zfighting_found",
        "test_contact_tag_allows_touching",
        "test_resting_on_floor_is_not_overlap",
        "test_hero_leaving_frame_found",
        "test_per_shot_hero_only_checked_in_its_shots",
        "test_pop_found",
        "test_floating_found",
        "test_faceted_found",
        "test_clean_scene_has_no_errors",
        "test_findings_sorted_errors_first",
        "test_partly_flipped_found",
        "test_inside_out_shell_found",
        "test_deleted_image_file_found",
        "test_checks_use_the_given_scene",
        "test_hollow_shell_is_not_inside_out",
        "test_grey_text_label_found",
        "test_pink_curve_found",
        "test_instanced_pink_model_found",
        "test_faceted_reads_the_rendered_normals",
        "test_faceted_ignores_hardened_bevels_on_curved_parts",
        "test_contact_tag_is_depth_limited",
        "test_contact_depth_limit_scales_with_thin_parts",
        "test_only_direct_parent_child_is_exempt_and_only_shallow",
        "test_hero_loose_parts_passing_into_each_other_found",
        "test_every_frame_of_a_moving_object_is_checked",
        "test_every_offending_pair_is_listed_up_to_a_cap",
        "test_renamed_duplicates_are_warned",
        "test_tags_must_name_objects_in_the_scene",
        "test_near_labels_and_hero_shots_must_exist",
        "test_near_labels_read_from_the_short_beside_the_blend",
        "test_self_overlap_ignores_static_detail_sunk_into_a_big_body",
        "test_self_overlap_on_a_deforming_hero_reports_parts_that_move_into_each_other",
        "test_live_boolean_modifier_warned",
        "test_contact_parts_with_different_moving_parents_warned",
        "test_contact_parent_check_allows_sliding_and_hinged_parts",
        "test_arc_length_path_moves_without_pops",
        "test_coplanar_faces_inside_one_mesh_found",
        "test_coplanar_check_ignores_flat_surfaces_shared_edges_and_bevels",
        "test_coplanar_check_is_scale_aware_and_covers_heroes_and_meshes_in_view",
        "test_rigid_assembly_far_from_origin_is_measured_once",
        "test_inside_rays_far_from_origin_cross_each_face_once",
        "test_progress_counts_every_frame_and_changes_nothing",
        "test_overlay_objects_skip_physical_checks_but_stay_in_frame",
        "test_hard_rules_flag_object_mesh_material_and_collection_names",
        "test_cradle_swing_and_collision_are_not_pops_but_a_teleport_is",
        "test_zfight_tolerance_follows_what_the_camera_can_see",
        "test_contact_moves_tag_allows_designed_motion_but_keeps_the_depth_limit",
    ):
        assert f"PASS {name}" in result.stdout
