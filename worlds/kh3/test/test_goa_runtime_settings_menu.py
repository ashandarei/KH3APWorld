from __future__ import annotations

from worlds.kh3.test.runtime_sources import native_project_root


import unittest
from pathlib import Path
from .runtime_sources import cpp_function_body, read_composition_sources, read_options_menu_sources

REPO_ROOT = Path(__file__).resolve().parents[3]
CPP_ROOT = native_project_root() / "KH3APProbeCpp"
KH3AP_ROOT = CPP_ROOT / "kh3ap"
UI_ROOT = KH3AP_ROOT / "features" / "ui"
MENU_SOURCE = (
    UI_ROOT / "options_menu_service.cpp"
)
MENU_MODEL_HEADER = UI_ROOT / "options_menu.h"
MENU_MODEL_SOURCE = UI_ROOT / "options_menu.cpp"
MENU_SERVICE_HEADER = UI_ROOT / "options_menu_service.h"
UI_SOUND_HEADER = UI_ROOT / "ui_sound_service.h"
UI_SOUND_SOURCE = UI_ROOT / "ui_sound_service.cpp"
CPP_CMAKE = CPP_ROOT / "CMakeLists.txt"
GOA_MENU_SOURCES = tuple(UI_ROOT / name for name in (
    "goa_settings_menu.cpp", "goa_randomization_menu.cpp", "goa_music_menu.cpp",
    "goa_mom_menu.cpp", "goa_credits_menu.cpp",
))
REMAP_SOURCE = (
    KH3AP_ROOT
    / "features"
    / "enemies"
    / "enemy_remap_service.cpp"
)
EXP_SOURCE = (
    KH3AP_ROOT
    / "features"
    / "enemies"
    / "experience_multiplier_service.cpp"
)
SETTINGS_SOURCE = KH3AP_ROOT / "core" / "settings/goa_settings_service.cpp"
BATTLE_SOURCE = (
    KH3AP_ROOT
    / "features"
    / "grants"
    / "battle_level_sync_service.cpp"
)
BATTLE_PROGRESS_HEADER = (
    KH3AP_ROOT
    / "features"
    / "grants"
    / "battle_level_progress.h"
)


def read_gfx_sources() -> str:
    return "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((KH3AP_ROOT / "core" / "gfx").glob("gfx_*helpers.cpp"))
    )


class TestGoaRuntimeSettingsMenuSourceContract(unittest.TestCase):
    def test_generic_host_injects_dynamic_private_anchor_pools(self) -> None:
        source = read_options_menu_sources()

        for required in (
            "/Script/TresGame.TresUIParts:OnFocusIn",
            "/Script/TresGame.TresUIParts:OnFocusOut",
            "/Script/TresGame.TresUIParts:OnAnchorIn",
            "/Script/TresGame.TresUIParts:OnAnchorOut",
            'STR("clone")',
            'STR("addChild")',
            'STR("menuSnd")',
            'STR("optionList")',
            'STR("setNewMark")',
            'STR("focusAnchor")',
            "try_gfx_set_member_value_object",
            "try_gfx_invoke_string_arg_object",
            'STR("getChildByName")',
            'STR("textField")',
            "RuntimeOptionsScreenMode::page",
            "RuntimeOptionsScreenMode::choice",
            "clone_detached_item(",
            "ensure_detached_items(",
            "constexpr int32_t first_parent_anchor_index = 900",
            "constexpr int32_t preferred_first_choice_anchor_index = 910",
            "page_anchor_base_ = first_parent_anchor_index + 1",
            "parent_anchor_indices_.assign(config_.menus.size(), -1)",
            "choice_anchor_base_ = std::max(",
            "measure_menu_capacity(",
            "scaleform_identity_token(",
            'STR("__kh3ap{}Options")',
            'STR("__kh3ap{}{}")',
            "parent_menu_index_for_anchor",
            "std::vector<UObject*> parents(config_.menus.size())",
            "page_capacity_",
            "choice_capacity_",
            "page_scroll_host(",
            'STR("scrollbar")',
            "viewport_input_key_vtable_slot = 8",
            'STR("ViewportConsole")',
            "PLH::x64Detour",
            "input=viewport_consume",
            "native_page_reuse=false",
            "native_option_callbacks=false",
            "asset_patch=false",
        ):
            with self.subTest(required=required):
                self.assertIn(required, " ".join(source.split()))

        self.assertNotIn("LoadObject", source)
        self.assertNotIn("StaticLoadObject", source)
        self.assertNotIn("SettingMenu.gfx", source)
        self.assertNotIn('STR("menuGpad")', source)
        self.assertNotIn("GetAsyncKeyState", source)
        self.assertNotIn("XInputGetState", source)
        self.assertNotIn("RuntimeEnemyRemapStatus", source)
        self.assertNotIn("RuntimeBattleLevelSyncStatus", source)
        self.assertNotIn('STR("Enemy Randomization")', source)
        self.assertNotIn('STR("Boss Randomization")', source)
        self.assertNotIn("kh3apGoa", source)

    def test_builder_scaffolds_toggle_choice_and_nested_submenus(self) -> None:
        header = MENU_MODEL_HEADER.read_text(encoding="utf-8")
        source = MENU_MODEL_SOURCE.read_text(encoding="utf-8")
        service_header = MENU_SERVICE_HEADER.read_text(encoding="utf-8")

        for required in (
            "struct RuntimeOptionsChoice",
            "RuntimeOptionsAvailability available{}",
            "struct RuntimeOptionsItemDefinition",
            "std::vector<RuntimeOptionsItemDefinition> children",
            "class RuntimeOptionsPageBuilder",
            "auto toggle(",
            "auto choice(",
            "auto submenu(",
            "std::function<void(RuntimeOptionsPageBuilder&)> scaffold",
            "class RuntimeOptionsMenuBuilder",
            "auto on_host_ready(",
            "auto build() &&",
            "menu.page().toggle(...).submenu(...);",
        ):
            with self.subTest(required=required):
                self.assertIn(required, header)

        for required in (
            '{{L"on", L"ON"}, {L"off", L"OFF"}}',
            "write(selected == 0)",
            "RuntimeOptionsItemKind::choice",
            "RuntimeOptionsItemKind::submenu",
            "RuntimeOptionsPageBuilder child_page{submenu.children}",
            "scaffold(child_page)",
            "return std::move(definition_)",
        ):
            with self.subTest(required=required):
                self.assertIn(required, " ".join(source.split()))

        self.assertIn("std::vector<RuntimeOptionsMenuDefinition> menus", service_header)
        self.assertIn("class RuntimeOptionsMenuService", service_header)
        self.assertNotIn("Goa", header)
        self.assertNotIn("EnemyRemap", service_header)
        self.assertNotIn("HostProfile", header)

    def test_host_supports_multiple_menus_and_nested_backtracking(self) -> None:
        source = read_options_menu_sources()

        for required in (
            "for (size_t i = 0; i < config_.menus.size(); ++i)",
            "parent_anchor_index(parents.size() - 1)",
            "i + 1 < parents.size() ? parent_anchor_index(i + 1)",
            "reset_navigation(",
            "std::vector<RuntimeOptionsPageFrame> page_stack_",
            "page_stack_.push_back(RuntimeOptionsPageFrame",
            ".items = &item->children",
            "page_stack_.pop_back()",
            "RuntimeOptionsItemKind::submenu",
            "RuntimeOptionsItemKind::choice",
            "RuntimeOptionsItemKind::information",
            "page_anchor_index(frame->selected_index)",
            "choice_anchor_index(selected_choice_index_)",
        ):
            with self.subTest(required=required):
                self.assertIn(required, " ".join(source.split()))

        self.assertNotIn("goa_enemy_index", source)
        self.assertNotIn("selected_child_index_", source)
        self.assertNotIn("show_child", source)

    def test_goa_menu_is_a_declarative_definition(self) -> None:
        source = "\n".join(path.read_text(encoding="utf-8") for path in GOA_MENU_SOURCES)
        host = read_options_menu_sources()

        for required in (
            "RuntimeOptionsMenuBuilder menu",
            'STR("goa")',
            'STR("enemy")',
            'STR("boss")',
            'STR("party")',
            'STR("party_chaos")',
            'STR("party_friend_1")',
            'STR("party_friend_2")',
            'STR("battle_sync")',
            'STR("battle_offset")',
            'STR("exp_multiplier")',
            'STR("AP Options")',
            'STR("Enemy Randomization")',
            'STR("Boss Randomization")',
            'STR("Randomize Party Members")',
            'STR("Reroll Party Every Room")',
            'STR("Friend 1")',
            'STR("Friend 2")',
            'STR("EXP Multiplier")',
            'STR("Battle Level Sync")',
            'STR("Battle Level")',
            "page.toggle(",
            "page.choice(",
            "set_enemy_enabled(enabled)",
            "set_boss_enabled(enabled)",
            "party_members.set_randomization_enabled(enabled)",
            "party_members.set_reroll_every_room(enabled)",
            "party_members.set_friend_choice(first_party_friend_slot, party_choice_order.at(selected))",
            "party_members.set_friend_choice(second_party_friend_slot, party_choice_order.at(selected))",
            "party_members.choice_available(",
            "battle_level_sync.set_enabled(enabled)",
            "battle_level_sync.set_offset(",
            "experience_multiplier.set_multiplier_half_steps(",
            "runtime_experience_multiplier_half_steps",
            "offsets.reserve(battle_level_offsets.size())",
            "battle_level_offsets[selected]",
            "format_battle_level_offset",
            "request_loaded_toggle_class_prewarm()",
            "goa_settings.set_color_randomization_mode(",
            "color_randomization_mode",
            'STR("Music Shuffle")',
            'STR("Form Themes")',
            "goa_settings.set_form_themes_enabled(",
            'STR("Include Custom Music")',
            "goa_settings.set_include_custom_music(",
            "goa_settings.set_music_shuffle_mode(",
            "return std::move(menu).build()",
        ):
            with self.subTest(required=required):
                self.assertIn(required, " ".join(source.split()))

        self.assertEqual(source.count("page.toggle("), 10)
        self.assertIn('STR("auto_equip_movement_abilities")', source)
        self.assertIn("goa_settings.set_auto_equip_movement_abilities(enabled)", source)
        self.assertEqual(source.count("page.choice("), 7)
        option_order = (
            'STR("battle_sync")',
            'STR("battle_offset")',
            'STR("exp_multiplier")',
            'STR("party")',
            'STR("party_chaos")',
            'STR("party_friend_1")',
            'STR("party_friend_2")',
            'STR("enemy")',
            'STR("boss")',
            'STR("color")',
            'STR("music")',
        )
        option_positions = [source.index(option) for option in option_order]
        self.assertEqual(option_positions, sorted(option_positions))
        offset_block = source.split("battle_level_offsets{{", 1)[1].split(
            "}};", 1
        )[0]
        self.assertEqual(
            [
                int(line.strip().rstrip(","))
                for line in offset_block.splitlines()
                if line.strip()
            ],
            [-3, -2, -1, 0, 1, 2, 3, 4, 5, 10],
        )
        self.assertNotIn("set_enemy_enabled(", host)
        self.assertNotIn("set_boss_enabled(", host)
        self.assertNotIn("request_loaded_toggle_class_prewarm()", host)
        self.assertNotIn("kh3apGoa", host)
        self.assertNotIn("host_profile", source)

    def test_credits_are_directly_below_ap_options_in_plain_white(self) -> None:
        source = "\n".join(path.read_text(encoding="utf-8") for path in GOA_MENU_SOURCES)
        model_header = MENU_MODEL_HEADER.read_text(encoding="utf-8")
        model_source = MENU_MODEL_SOURCE.read_text(encoding="utf-8")
        host = read_options_menu_sources()
        composition = read_composition_sources()

        self.assertIn("information,", model_header)
        self.assertIn("RuntimeOptionsPageBuilder::information", model_source)
        self.assertNotIn("emphasized_text_color", host)
        self.assertNotIn("item.emphasized", host)

        goa = composition.index("make_goa_runtime_options_menu(")
        randomization = composition.index("make_randomization_runtime_options_menu(", goa)
        music = composition.index("make_music_runtime_options_menu(", randomization)
        mom = composition.index("make_mom_runtime_options_menu(", music)
        credits = composition.index("make_goa_credits_menu()", goa)
        gfx_config = composition.index(".gfx_paths", credits)
        self.assertLess(goa, credits)
        self.assertLess(randomization, music)
        self.assertLess(music, mom)
        self.assertLess(credits, gfx_config)

        for required in (
            'STR("AP Credits")',
            'STR("Lan144")',
            'STR("Lanahi")',
            'STR("Gusxandi")',
            'STR("Cee")',
            'STR("Raphael2512")',
            'STR("CODE")',
            'STR("LOGIC")',
            'STR("ART")',
            "Initial battle level computer options",
            "initial cutscene skips",
            "Stylesanity option",
            "initial fight and chest logic",
            "Archipelago icon and other shop icon",
            "inactive portal Keyblade images",
        ):
            with self.subTest(required=required):
                self.assertIn(required, " ".join(source.split()))

    def test_multiple_custom_parent_rows_preserve_stock_height_and_spacing(self) -> None:
        source = read_options_menu_sources()
        injection = source.split("if (parent_clone_required)", 1)[1].split(
            "configure_anchor(stock_rows.front()", 1
        )[0]

        self.assertIn("stock_row_count = stock_quit_index - stock_first_index + 1", source)
        self.assertIn("(y_last - y_first) / static_cast<double>(stock_row_count - 1)", injection)
        self.assertIn("parents.begin(), parents.end()", injection)
        self.assertRegex(injection, r'visible_rows\[i\],\s*STR\("scaleY"\),\s*1\.0')
        self.assertRegex(injection, r"y_first\s*\+\s*stock_spacing \* static_cast<double>\(i\)")
        self.assertNotIn("stock_spacing * compact_scale", injection)

    def test_keyboard_arrows_can_cross_the_visible_page_window(self) -> None:
        source = read_options_menu_sources()
        paint_page = cpp_function_body(source, "paint_page_rows")

        self.assertIn("expose_keyboard_focus_sentinel", paint_page)
        self.assertIn("(window_start + item_count - 1) % item_count", paint_page)
        self.assertIn("window_end % item_count", paint_page)
        self.assertRegex(paint_page, r'STR\("mouseEnabled"\),\s*false')
        self.assertIn('STR("alpha"), 0.0', paint_page)
        self.assertIn('STR("UpArrow")', source)
        self.assertIn('STR("DownArrow")', source)

    def test_custom_route_never_borrows_or_mutates_gamepad_settings(self) -> None:
        source = read_options_menu_sources()

        self.assertIn("constexpr int32_t stock_quit_index = 8", source)
        self.assertIn("write_integral_function_param(", source)
        self.assertIn("handle_anchor_in_pre(context);", source)
        self.assertIn("handle_anchor_in_post(context);", source)
        self.assertIn("handle_anchor_out_pre(context);", source)
        self.assertIn('STR("txMessage")', source)
        self.assertIn("FWeakObjectPtr menu_context_", source)
        self.assertIn("FWeakObjectPtr menu_root_", source)
        self.assertIn('STR("m_ASProxyUIComponent")', source)
        self.assertIn('STR("m_ASObject")', source)
        self.assertIn("live_menu_root()", source)
        self.assertIn("live_parent_button(", source)
        self.assertIn("capture_active_", source)
        self.assertIn("injection_ready_", source)
        self.assertIn("injection_pending_", source)
        self.assertIn("deferred_injection_tick_count", source)
        self.assertIn("max_deferred_injection_attempts", source)
        self.assertIn("deferred_parent_label_refresh_tick_count", source)
        self.assertIn("parent_label_refresh_ticks_", source)
        self.assertIn("refresh_parent_labels(root);", source)
        self.assertIn("active_custom_anchor_index_", source)
        self.assertIn("pending_custom_anchor_index_", source)
        self.assertIn("handle_viewport_key(", source)
        self.assertIn("consumed_key_name_", source)
        self.assertIn("event_type != input_event_pressed", source)
        self.assertIn("belongs_to_claimed_press", source)
        self.assertIn("recover_capture_from_selected_row(", source)
        self.assertIn("display_child_named(", source)
        self.assertIn("parent_instances_", source)
        self.assertIn("page_instances_", source)
        self.assertIn("consume=confirm_back_scroll_navigation", source)
        self.assertIn("return true;", source)
        self.assertIn("source_index", source)
        self.assertIn("private_anchor_index", source)
        self.assertIn("apply_selected_choice(", source)
        self.assertNotIn('STR("unfocus")', source)
        self.assertNotIn('STR("menuGpad")', source)
        self.assertNotIn('STR("m_pGamePadMenu")', source)
        self.assertNotIn('STR("m_pGamePadLists")', source)
        self.assertNotIn("GetAsyncKeyState", source)
        self.assertNotIn("XInputGetState", source)
        self.assertNotIn('STR("m_index")', source)
        self.assertNotIn('STR("m_focusIndex")', source)
        self.assertNotIn("set_list_component_index_silent(", source)
        self.assertNotIn("goa_option_callback_index", source)
        self.assertNotIn("queue_native_option_restore(", source)
        self.assertNotIn("restore_native_option_selection(", source)
        self.assertNotIn("restore_gamepad_page(", source)
        self.assertNotIn("customize_gamepad_page(", source)
        self.assertNotIn("stock_gamepad_index", source)
        self.assertIn("choice_apply=definition_callback", source)

        ensure_injected = cpp_function_body(source, "ensure_injected")
        success_log = ensure_injected.index("Runtime options injected")
        self.assertLess(ensure_injected.index("hide_detached_items(root)"), success_log)
        self.assertLess(ensure_injected.index("normalize_parent_button(parent)"), success_log)
        self.assertLess(ensure_injected.index("refresh_parent_labels(root)"), success_log)

        anchor_pre = cpp_function_body(source, "handle_anchor_in_pre")
        focus_in = cpp_function_body(source, "handle_focus_in")
        focus_out = cpp_function_body(source, "handle_focus_out")
        self.assertNotIn("ensure_injected(root)", anchor_pre)
        self.assertNotIn("ensure_injected(root)", focus_in)
        self.assertNotIn("hide_detached_items(root)", focus_out)
        self.assertNotIn("set_button_selected(", focus_out)

        for removed_diagnostic in (
            "viewport key observed",
            "viewport key claimed",
            "capture recovered",
            "private-anchor choice applied",
            "trace_private_rows_once",
            "viewport_trace_count_",
            "anchor_trace_count_",
            "row_trace_mask_",
        ):
            with self.subTest(removed_diagnostic=removed_diagnostic):
                self.assertNotIn(removed_diagnostic, source)

    def test_non_menu_keys_exit_before_uobject_resolution(self) -> None:
        source = read_options_menu_sources()
        handler = cpp_function_body(source, "handle_viewport_key")

        key_filter = handler.index(
            "if (!confirm && !back && !page_navigation && !choice_navigation)"
        )
        root_resolution = handler.index("live_menu_root()")
        context_resolution = handler.index("menu_context_.Get()")
        self.assertLess(key_filter, root_resolution)
        self.assertLess(key_filter, context_resolution)

    def test_long_choice_lists_use_a_readable_scrolling_window(self) -> None:
        source = read_options_menu_sources()
        paint_choices = cpp_function_body(source, "paint_choice_rows")
        handler = cpp_function_body(source, "handle_viewport_key")

        for required in (
            "max_visible_choice_rows = 8",
            "window_start",
            "window_end",
            "i - window_start",
            "choice_first_transform_",
            "choice_second_transform_",
        ):
            with self.subTest(required=required):
                self.assertIn(required, " ".join(source.split()))
        self.assertIn("i < window_start || i >= window_end", paint_choices)
        self.assertIn("is_previous_navigation_key", handler)
        self.assertIn("is_next_navigation_key", handler)
        self.assertIn("paint_choice_rows(root)", handler)
        self.assertIn("choice_anchor_index(selected_choice_index_)", handler)

    def test_long_option_pages_reuse_the_stock_five_row_scrollbar(self) -> None:
        source = read_options_menu_sources()
        gfx_header = (
            KH3AP_ROOT / "core" / "gfx" / "gfx_reflection_helpers.h"
        ).read_text(encoding="utf-8")
        gfx_source = read_gfx_sources()
        paint_page = cpp_function_body(source, "paint_page_rows")
        handler = cpp_function_body(source, "handle_viewport_key")

        for required in (
            "max_visible_page_rows = 5",
            "scroll_position",
            "clone_detached_scroll_host(",
            "page_scroll_host_",
            "page_scroll_host_transform_",
            "page_first_transform_",
            "page_second_transform_",
            'STR("setScrollProperty")',
            'STR("setScrollPosition")',
            "try_gfx_invoke_int_int_number_args",
        ):
            with self.subTest(required=required):
                self.assertIn(required, " ".join(source.split()))
        self.assertIn("i < window_start || i >= window_end", paint_page)
        self.assertIn("i - window_start", paint_page)
        self.assertIn("page_navigation", handler)
        self.assertIn("paint_page_rows(root)", handler)
        self.assertIn("page_anchor_index(frame->selected_index)", handler)
        self.assertIn("MouseScrollUp", source)
        self.assertIn("MouseScrollDown", source)
        self.assertIn("try_gfx_invoke_int_int_number_args", gfx_header)
        self.assertIn("constexpr int32_t argument_count = 3", gfx_source)
        self.assertIn("header->num = argument_count", gfx_source)
        self.assertIn("header->max = argument_count", gfx_source)


    def test_reused_controller_labels_are_reset_to_plain_text(self) -> None:
        source = read_options_menu_sources()

        self.assertIn('{7, 0, 0}', source)
        self.assertIn('STR("labelText1")', source)
        self.assertIn('STR("labelText"), STR("txText")', source)
        self.assertIn("set_adjusted_text(value_adjusted_text, value)", source)
        self.assertIn("show_adjustable_text_only(value_text, value_adjusted_text)", source)
        self.assertIn("show_adjustable_text_only(label_name, label_text)", source)
        self.assertIn("set_button_visible(child, false)", source)
        self.assertIn('STR("alpha"), 1.0', source)
        self.assertIn("prepare_detached_item(item);", source)
        self.assertIn("normalize_parent_button", source)
        self.assertIn('STR("enabled"), true', source)
        self.assertIn('STR("gotoAndStop")', source)
        self.assertIn('STR("checkMark")', source)
        self.assertIn("checked ? 5 : 1", source)

    def test_reused_rows_apply_availability_after_timeline_reset(self) -> None:
        source = read_options_menu_sources()

        setting_item = cpp_function_body(source, "set_setting_item")
        option_item = cpp_function_body(source, "set_option_item")
        for body in (setting_item, option_item):
            self.assertGreater(
                body.rindex('STR("enabled")'),
                body.index('STR("gotoAndStop")'),
            )

    def test_custom_navigation_latches_logical_direction_aliases(self) -> None:
        source = read_options_menu_sources()
        handler = cpp_function_body(source, "handle_viewport_key")

        self.assertNotIn("dynamic_row_refresh_ticks_", source)
        self.assertNotIn("deferred_dynamic_row_refresh_tick_count", source)
        self.assertIn("repaint_button_selected(row", source)
        self.assertIn("repaint_button_selected(choice", source)
        self.assertIn("if (consumed_key_name_ == key)", handler)
        self.assertIn("focus_custom_anchor", source)
        self.assertIn('STR("focusAnchor")', source)
        self.assertIn("!navigation_frame->items->empty()", handler)
        self.assertIn("!navigation_item->choices.empty()", handler)
        self.assertIn("navigation_direction_latched_", handler)
        self.assertIn("navigation_direction_latched_ == navigation_direction", handler)
        self.assertIn("navigation_direction_latched_ = 0", handler)

    def test_reused_rows_paint_injected_text_with_stock_disabled_tints(self) -> None:
        source = read_options_menu_sources()

        self.assertIn("disabled_setting_text_color = 0x737373", source)
        self.assertIn("disabled_option_text_color = 0x999999", source)
        self.assertIn('STR("textColor")', source)
        self.assertRegex(source, r"set_adjusted_text_color\(\s*value_adjusted_text")
        self.assertRegex(source, r"set_adjusted_text_color\(\s*label_text")

    def test_keyboard_space_matches_the_movie_cancel_prompt(self) -> None:
        source = read_options_menu_sources()

        confirm_body = cpp_function_body(source, "is_confirm_key")
        back_body = cpp_function_body(source, "is_back_key")
        self.assertNotIn('STR("SpaceBar")', confirm_body)
        self.assertIn('STR("SpaceBar")', back_body)

    def test_active_parent_row_remains_selected_inside_custom_pages(self) -> None:
        source = read_options_menu_sources()

        show_parent = cpp_function_body(source, "show_parent")
        show_page = cpp_function_body(source, "show_page")
        show_choice = cpp_function_body(source, "show_choice")
        self.assertIn("paint_page_rows(root)", show_parent)
        self.assertIn("set_active_parent_selected(root)", show_parent)
        self.assertIn("set_active_parent_selected(root)", show_page)
        self.assertIn("set_active_parent_selected(root)", show_choice)

    def test_injected_labels_use_scaleform_wide_strings(self) -> None:
        source = read_gfx_sources()

        self.assertIn("const uint8_t type_string = 6", source)
        self.assertIn("AS_StringW (6)", source)

    def test_toggles_gate_immutable_seed_maps(self) -> None:
        source = REMAP_SOURCE.read_text(encoding="utf-8")
        source += (REMAP_SOURCE.parent / "level_entity_remap.cpp").read_text(encoding="utf-8")
        source += (REMAP_SOURCE.parent / "remap_runtime.h").read_text(encoding="utf-8")

        for required in (
            "std::atomic_bool enemy_enabled{true}",
            "std::atomic_bool boss_enabled{true}",
            "RuntimeLoadedLevelEntityRemap",
            "FWeakObjectPtr level_entity",
            "slot_guarded_level_entity",
            "is_uobject_valid_fast(slot_guarded)",
            "slot_guarded->GetInternalIndex() != remap.object_index",
            "source_class_path",
            "replacement_class_path",
            "remember_loaded_level_entity_remap(",
            "reconcile_loaded_level_entity_remaps(",
            "apply=next_same_map_encounter_spawn",
            "current_actors_unchanged=true",
            "config_.goa_settings.set_enemy_randomization_enabled(enabled)",
            "config_.goa_settings.set_boss_randomization_enabled(enabled)",
            "is_uobject_a_fast(object, UClass::StaticClass())",
        ):
            with self.subTest(required=required):
                self.assertIn(required, " ".join(source.split()))

        self.assertNotIn("enemy_replacements.clear()", source)
        self.assertNotIn("boss_class_replacements.clear()", source)
        self.assertNotIn("ForEachUObject", source)
        self.assertNotIn("object->IsA<UClass>()", source)


    def test_runtime_exp_multiplier_hooks_the_authoritative_award_path(self) -> None:
        source = EXP_SOURCE.read_text(encoding="utf-8")

        for required in (
            "steam_experience_award_prologue",
            "egs_experience_award_prologue",
            "0xE9E830",
            "0x1135CC0",
            "TimeDateStamp",
            "RtlLookupFunctionEntry(",
            "PLH::x64Detour",
            "scale_experience_for_runtime_award(",
            "Runtime EXP award hook installed",
            "Runtime EXP award intercepted",
            "route=native_award_before_game_modifiers",
            "apply=future_exp_awards_immediately",
            "existing_total_unchanged=true",
        ):
            with self.subTest(required=required):
                self.assertIn(required, " ".join(source.split()))

        # The storefront fingerprints are shared, but this award hook must
        # still select both fields from the matching storefront profile.
        fingerprints = (KH3AP_ROOT / "core" / "platform/game_build.h").read_text(encoding="utf-8")
        for storefront, timestamp, image_size in (
            ("steam", "0x66743F34", "0xA969000"),
            ("egs", "0x60547D11", "0xAEBF000"),
        ):
            with self.subTest(storefront=storefront):
                self.assertRegex(
                    fingerprints,
                    rf"Fingerprint\s+{storefront}\s*\{{\s*{timestamp},\s*{image_size}\s*\}}",
                )
                self.assertRegex(
                    source,
                    rf'STR\("{storefront}"\),\s*kh3ap::native_build::{storefront}\.image_size,\s*'
                    rf'kh3ap::native_build::{storefront}\.timestamp',
                )

        self.assertNotIn("EnemyBaseStatus", source)
        self.assertNotIn("m_BaseExp", source)
        self.assertNotIn("IsReal", source)
        self.assertNotIn("ForEachUObject", source)
        self.assertNotIn("Debug", source)
        self.assertNotIn("table_mutation", source)
        self.assertNotIn("request_reapply(", source)

    def test_composition_installs_settings_then_remaps_before_menu_hooks(self) -> None:
        composition = read_composition_sources()
        cmake = (CPP_ROOT / "CMakeLists.txt").read_text(encoding="utf-8")

        self.assertLess(
            composition.index("m_goa_settings.install_once();"),
            composition.index("m_experience_multiplier.install_once();"),
        )
        self.assertLess(
            composition.index("m_experience_multiplier.install_once();"),
            composition.index("m_enemy_remaps.install_once();"),
        )
        self.assertLess(
            composition.index("m_enemy_remaps.install_once();"),
            composition.index("m_party_members.install_once();"),
        )
        self.assertLess(
            composition.index("m_party_members.install_once();"),
            composition.index("m_goa_settings_menu.install_once();"),
        )
        self.assertIn(
            "kh3ap/features/ui/options_menu_service.cpp",
            cmake,
        )
        self.assertIn("kh3ap/features/ui/options_menu.cpp", cmake)
        self.assertIn(
            "kh3ap/features/ui/goa_settings_menu.cpp", cmake
        )
        self.assertIn("kh3ap/core/settings/goa_settings_service.cpp", cmake)
        self.assertIn(
            "kh3ap/features/enemies/experience_multiplier_service.cpp",
            cmake,
        )
        self.assertIn("RuntimeOptionsMenuService m_goa_settings_menu", composition)
        self.assertRegex(composition, r"\.menus\s*=\s*\{")
        self.assertIn("make_goa_runtime_options_menu(", composition)
        self.assertIn("m_experience_multiplier.tick();", composition)
        self.assertIn("m_party_members.tick();", composition)
        self.assertNotIn("m_experience_multiplier.request_reapply(", composition)
        self.assertIn("m_goa_settings_menu.tick();", composition)


if __name__ == "__main__":
    unittest.main()
