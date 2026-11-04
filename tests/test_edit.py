from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent


@pytest.mark.blender
def test_edit_in_blender(blender_run):
    result = blender_run(HERE / "blender" / "test_edit.py", timeout=900)
    assert result.returncode == 0, result.stdout + result.stderr
    for name in (
        "test_edit_encodes_9x16_with_audio",
        "test_encode_without_audio_probes_no_audio",
        "test_final_preset_is_perceptually_lossless",
        "test_captions_inside_safe_area",
        "test_no_branding_without_handle",
        "test_badge_present_for_badged_shot",
        "test_shots_become_image_strips_in_order",
        "test_short_render_is_refused_not_padded",
        "test_font_family_names_match_files",
        "test_font_found_in_the_per_user_font_folder",
        "test_font_fallback_is_reported",
        "test_brand_caption_colours",
        "test_font_with_a_weight_loads_the_downloaded_static_file",
        "test_plain_captions_use_the_brand_font",
        "test_end_card_follows_the_last_line_in_the_upper_third",
        "test_end_card_line_is_optional_and_defaults",
        "test_near_label_follows_its_object",
        "test_near_label_without_anchors_is_refused",
        "test_near_labels_avoid_the_badge_stacked_labels_and_each_other",
        "test_end_card_moves_off_a_hero",
        "test_short_end_card_overrides_the_setting_with_a_second_line",
        "test_short_end_card_without_a_handle",
        "test_near_labels_keep_off_the_other_heroes",
        "test_end_card_keeps_off_other_meshes_after_heroes",
        "test_legacy_brand_without_a_style_changes_nothing",
        "test_kit_edit_places_card_images_and_no_caption_text",
        "test_kit_caption_pixels_are_the_kit_box_colour",
        "test_kit_progress_fill_grows",
        "test_kit_near_label_has_a_pointer_to_its_anchor",
        "test_kit_edit_without_its_logo_file_warns_and_draws_no_badge",
        "test_kit_edit_leaves_every_engine_alone",
        "test_caption_draw_false_draws_no_captions_in_either_path",
        "test_a_pointer_crossing_the_title_card_passes_behind_it",
        "test_kit_end_card_uses_the_kits_font_unless_the_short_names_one",
        "test_kit_edit_without_a_short_folder_draws_its_cards_in_its_own_temp_folder",
        "test_kit_progress_bar_is_half_full_at_the_middle_frame",
    ):
        assert f"PASS {name}" in result.stdout


@pytest.mark.blender
def test_anchors_in_blender(blender_run):
    result = blender_run(HERE / "blender" / "test_anchors.py", timeout=300)
    assert result.returncode == 0, result.stdout + result.stderr
    for name in ("test_projects_the_top_of_a_known_object", "test_follows_a_moving_object_per_frame",
                 "test_missing_object_and_behind_the_camera", "test_follows_a_camera_switched_by_markers",
                 "test_hero_boxes_of_the_shots_heroes", "test_scenery_boxes_are_the_other_meshes_on_screen",
                 "test_hero_boxes_at_given_frames", "test_at_point_is_projected_each_frame",
                 "test_an_empty_anchors_at_its_origin", "test_at_point_behind_the_camera_is_none"):  # fmt: skip
        assert f"PASS {name}" in result.stdout
