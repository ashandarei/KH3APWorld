from __future__ import annotations

from worlds.kh3.test.runtime_sources import native_project_root

import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
CPP_ROOT = native_project_root() / "KH3APProbeCpp"
KH3AP_ROOT = CPP_ROOT / "kh3ap"
MENU_SOURCE = (
    KH3AP_ROOT
    / "features"
    / "ui"
    / "keyblade_level"
    / "keyblade_level_menu_service.cpp"
)
LEVEL_HELPER_SOURCE = (
    KH3AP_ROOT
    / "features"
    / "grants"
    / "keyblades"
    / "progressive_keyblade_level_helpers.cpp"
)
GRANT_APPLY_SOURCE = (
    KH3AP_ROOT
    / "features"
    / "grants"
    / "delivery"
    / "item_grant_apply_helpers.cpp"
)
def read_gfx_sources() -> str:
    return "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((KH3AP_ROOT / "core" / "gfx").glob("gfx_*helpers.cpp"))
    )


class TestKeybladeLevelMenuRuntime(unittest.TestCase):
    def test_level_replaces_the_authored_bottom_help_text(self) -> None:
        source = MENU_SOURCE.read_text(encoding="utf-8")

        self.assertIn("/Game/UI/Scaleform/15_campmenu/CampMenu", source)
        self.assertIn('STR("equipReplaceAnm")', source)
        self.assertIn('STR("equipReplace")', source)
        self.assertIn('STR("replaceListVisibleAnm")', source)
        self.assertIn('STR("replaceList")', source)
        self.assertIn('STR("helpText")', source)
        self.assertIn('STR("txHelpText")', source)
        self.assertIn('STR("{} - KEYBLADE LEVEL {} / {}")', source)

    def test_selection_is_event_driven_and_does_not_poll_the_gfx_tree(self) -> None:
        source = MENU_SOURCE.read_text(encoding="utf-8")

        self.assertIn("OnListIndexChange", source)
        self.assertIn('STR("ListIndex")', source)
        self.assertIn('STR("AnchorComponent")', source)
        self.assertIn('STR("scrollPosition")', source)
        self.assertIn('STR("label")', source)
        self.assertIn("replace_list_anchor_index = 307", source)
        self.assertIn("immediate_child_scan_limit = 16", source)
        self.assertIn("maximum_deferred_attempts = 4", source)
        self.assertIn("object_lifetime=same_render_call", source)
        self.assertIn("polling=false", source)
        self.assertNotIn('STR("selected")', source)
        self.assertNotIn('STR("clone")', source)
        self.assertNotIn("selection_refresh_interval", source)
        self.assertNotIn("UObjectGlobals::FindAllOf", source)
        self.assertIn("FWeakObjectPtr menu_context_", source)
        self.assertIn("root_from_context(menu_context)", source)
        self.assertIn("Callback GFxObject arguments are transient wrappers", source)
        self.assertIn("try_read_selected_label", source)
        self.assertIn("selection_capture_failed_", source)
        self.assertNotIn("FWeakObjectPtr active_list_", source)
        self.assertNotIn("FWeakObjectPtr cached_root_", source)
        self.assertNotIn("FWeakObjectPtr help_text_", source)
        self.assertIn("if (stock_help_text_.empty())", source)
        self.assertNotIn("row_objects_", source)
        self.assertIn("kh3ap::is_uobject_valid_fast", source)


    def test_level_query_counts_the_authoritative_enhancement_flags(self) -> None:
        source = LEVEL_HELPER_SOURCE.read_text(encoding="utf-8")

        self.assertIn("auto try_query_keyblade_level(", source)
        self.assertIn("if (levels_start_at_max)", source)
        self.assertIn("flag_is_set(bank, group_it->first_row_id + level_offset)", source)
        self.assertIn("query.current_level = std::clamp", source)

    def test_duplicate_level_grants_skip_the_expensive_item_manager_refresh(self) -> None:
        source = LEVEL_HELPER_SOURCE.read_text(encoding="utf-8")

        refresh_guard = (
            "const bool should_refresh_item_manager =\n"
            "                applied.newly_set_count > 0 || force_item_manager_refresh;"
        )
        self.assertIn(refresh_guard, source)
        self.assertIn("if (should_refresh_item_manager)", source)
        self.assertIn("if (applied.newly_set_count > 0)", source)
        self.assertLess(
            source.index("refresh_item_manager(item_manager);"),
            source.index("NativeFlagBank after{};"),
        )

    def test_explicit_repair_and_diagnostic_grants_can_force_a_refresh(self) -> None:
        source = GRANT_APPLY_SOURCE.read_text(encoding="utf-8")

        self.assertIn("progressive_keyblade_refresh_is_forced(request)", source)
        self.assertIn('request.reapply_context_key == STR("specific_runtime_grant")', source)
        self.assertIn('request.action_kind == STR("runtime_grant_probe")', source)
        self.assertIn('request.action_kind == STR("manual_test")', source)

    def test_gfx_string_reads_use_the_reflected_as_string_payload(self) -> None:
        source = read_gfx_sources()

        self.assertIn("auto try_get_as_value_string(", source)
        self.assertIn('find_struct_field_by_name(script_struct, STR("ASString"))', source)
        self.assertIn("as_type != 5 && as_type != 6", source)
        self.assertIn("auto try_gfx_get_member_value_string(", source)


if __name__ == "__main__":
    unittest.main()
