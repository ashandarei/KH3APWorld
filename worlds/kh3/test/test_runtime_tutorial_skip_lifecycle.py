
from worlds.kh3.test.runtime_sources import native_project_root
import re
import unittest
from pathlib import Path
from .runtime_sources import cpp_function_body, read_composition_sources

REPO_ROOT = Path(__file__).resolve().parents[3]
TUTORIAL_SKIP_SOURCE = (
    native_project_root()
    / "KH3APProbeCpp"
    / "kh3ap"
    / "features"
    / "tutorials"
    / "tutorial_skip_service.cpp"
)
TUTORIAL_PATCH_SOURCE = TUTORIAL_SKIP_SOURCE.with_name("tutorial_patch_helpers.cpp")
TUTORIAL_PATCH_HEADER = TUTORIAL_SKIP_SOURCE.with_name("tutorial_patch_helpers.h")
RUNTIME_OBJECT_OBSERVER_SOURCE = (
    native_project_root()
    / "KH3APProbeCpp"
    / "kh3ap"
    / "hooks"
    / "kh3_runtime_object_observer.cpp"
)
TUTORIAL_BYPASS_HEADER = (
    native_project_root()
    / "KH3APProbeCpp"
    / "kh3ap"
    / "features"
    / "tutorials"
    / "tutorial_popup_bypass.h"
)


def read_tutorial_source() -> str:
    # clang-format may split one log literal across adjacent C++ strings.
    return re.sub(r'"[ \t]*\n[ \t]*"', "", TUTORIAL_SKIP_SOURCE.read_text(encoding="utf-8"))


class KH3RuntimeTutorialSkipLifecycleTest(unittest.TestCase):
    def test_link_grants_and_popups_patch_before_begin_play_and_after_reload(self) -> None:
        tutorial = read_tutorial_source()
        observer = RUNTIME_OBJECT_OBSERVER_SOURCE.read_text(encoding="utf-8")
        composition = read_composition_sources()
        self.assertIn("try_apply_links_for_context(context_hint, source)", tutorial)
        self.assertIn("links_.fill({})", cpp_function_body(tutorial, "reset_playable_context"))
        self.assertRegex(tutorial, r"find_constructed_or_live_function\(\s*"
                         r"object,\s*runtime_link_tutorial_function_paths\[index\]\)")
        block = observer.split("if (observe_link_tutorial_patches_.load", 1)[1].split(
            "const uint8_t toy_box_enabled_mask", 1)[0]
        self.assertIn("try_apply_runtime_link_tutorial_early_bypass", block)
        self.assertNotIn(".store(false", block)
        self.assertEqual(composition.count("s_enable_link_tutorial_bypasses,"), 2)

    def test_both_final_world_variants_apply_controller_disable_patch(self) -> None:
        source = TUTORIAL_PATCH_SOURCE.read_text(encoding="utf-8")
        start = source.index("auto try_patch_final_world_function(")
        end = source.index("auto try_patch_tutorial_function_object(", start)
        patch = source[start:end]
        self.assertIn("try_patch_runtime_final_world_ability_disables_bytecode", patch)
        self.assertNotIn("function_path ==", patch)  # EW04 must not be excluded.
        self.assertIn("EWControllerAbilityDisables result={}", patch)
        attempt = TUTORIAL_PATCH_HEADER.read_text(encoding="utf-8")
        self.assertIn(
            "ability_disables_result != RuntimeTutorialPopupBypassResult::Rejected", attempt
        )

    def test_retry_does_not_follow_stale_tutorial_function_outers(self) -> None:
        source = read_tutorial_source() + TUTORIAL_PATCH_SOURCE.read_text(encoding="utf-8")

        self.assertIn("is_live_function_patch_target", source)
        self.assertIn("is_uobject_valid_fast(current)", source)
        self.assertIn("UObjectArray::IsValid(object_item, false)", source)
        self.assertIn("RF_BeginDestroyed | RF_FinishDestroyed", source)
        self.assertIn("UObject* const outer = current->GetOuterPrivate()", source)
        self.assertNotIn("GetFullName()", source)

        toy_box_context = source[
            source.index("RuntimeTutorialSkipService::try_apply_toy_box_for_context") :
            source.index("RuntimeTutorialSkipService::try_apply_twilight_town_for_context")
        ]
        self.assertIn("find_live_function(function_path)", source)
        self.assertIn("config_.toy_box_function_paths[index]", toy_box_context)
        self.assertIn("retrying on later map-ready event", toy_box_context)

        twilight_town_context = source[
            source.index("RuntimeTutorialSkipService::try_apply_twilight_town_for_context") :
            source.index("RuntimeTutorialSkipService::try_apply_final_world_for_context")
        ]
        # Both map-ready retries and pre-BeginPlay use the same ordered batch.
        # Check the batch contents as well as each caller, so an unused patch
        # definition cannot satisfy this lifecycle contract.
        batch = source.split("s_twilight_town_patchers{", 1)[1].split("};", 1)[0]
        patchers = (
            "try_patch_runtime_twilight_town_taking_photos_help_bytecode",
            "try_patch_runtime_twilight_town_lucky_emblems_help_bytecode",
            "try_patch_runtime_twilight_town_links_help_bytecode",
            "try_patch_runtime_twilight_town_meow_wow_help_bytecode",
        )
        for patcher in patchers:
            self.assertEqual(batch.count("&" + patcher), 1)
        self.assertEqual([batch.index("&" + patcher) for patcher in patchers],
                         sorted(batch.index("&" + patcher) for patcher in patchers))
        early_context = cpp_function_body(source, "try_apply_runtime_twilight_town_early_bypass")
        for caller in (twilight_town_context, early_context):
            self.assertRegex(caller, r"apply_tutorial_patch_batch\(\s*script,\s*script_size,\s*s_twilight_town_patchers\)")
        self.assertIn("camera_help_flag=preserved", twilight_town_context)

    def test_olympus_completion_bypass_runs_before_begin_play_on_every_he01_load(self) -> None:
        tutorial_source = read_tutorial_source()
        observer_source = RUNTIME_OBJECT_OBSERVER_SOURCE.read_text(encoding="utf-8")
        composition_source = read_composition_sources()

        self.assertRegex(tutorial_source,
                         r"try_apply_runtime_olympus_completion_early_bypass\(\s*"
                         r"UObject\* gameflow_object\)")
        self.assertIn("gameflow_object->GetFunctionByNameInChain(", tutorial_source)
        self.assertIn("constructed_object_function_chain", tutorial_source)
        self.assertIn("timing=uobject_create_pre_begin_play", tutorial_source)
        observer_config = composition_source.split("RuntimeObjectObservationConfig{", 1)[1].split("}};", 1)[0]
        self.assertIn("s_enable_olympus_completion_tutorial_bypass,", observer_config)

        block_start = observer_source.index(
            "if (observe_olympus_completion_gameflow_patch_.load("
        )
        block_end = observer_source.index("\n        }", block_start)
        completion_observer = observer_source[block_start:block_end]
        self.assertIn(
            "fname_equals(*class_name, olympus_completion_gameflow_class_name_)",
            completion_observer,
        )
        self.assertIn(
            "return try_apply_runtime_olympus_completion_early_bypass(",
            completion_observer,
        )
        self.assertIn("Keep this observer armed", completion_observer)
        self.assertNotIn(".store(false", completion_observer)

    def test_caribbean_sea_spectacle_bypass_preserves_cinematic_cleanup(self) -> None:
        tutorial_source = read_tutorial_source()
        observer_source = RUNTIME_OBJECT_OBSERVER_SOURCE.read_text(encoding="utf-8")
        composition_source = read_composition_sources()
        bypass_header = TUTORIAL_BYPASS_HEADER.read_text(encoding="utf-8")

        self.assertIn(
            "runtime_caribbean_sea_spectacle_post_dismiss_offset = 0xE7",
            bypass_header,
        )
        self.assertIn(
            "try_patch_runtime_caribbean_sea_spectacle_help_bytecode",
            tutorial_source,
        )
        self.assertIn(
            "tutorial_actor->GetFunctionByNameInChain(",
            tutorial_source,
        )
        self.assertIn("constructed_object_function_chain", tutorial_source)
        self.assertIn("popup=removed delay=preserved cinematic_cleanup=preserved", tutorial_source)
        self.assertIn("ca_island_event3_logic=untouched", tutorial_source)
        observer_config = composition_source.split("RuntimeObjectObservationConfig{", 1)[1].split("}};", 1)[0]
        self.assertIn("s_enable_caribbean_sea_spectacle_tutorial_bypass,", observer_config)

        block_start = observer_source.index(
            "if (observe_caribbean_sea_spectacle_patch_.load("
        )
        block_end = observer_source.index("\n        }", block_start)
        caribbean_observer = observer_source[block_start:block_end]
        self.assertIn(
            "fname_equals(*class_name, caribbean_sea_spectacle_class_name_)",
            caribbean_observer,
        )
        self.assertIn(
            "return try_apply_runtime_caribbean_sea_spectacle_early_bypass(\n",
            caribbean_observer,
        )
        self.assertIn("Keep the observer armed", caribbean_observer)
        self.assertNotIn(".store(false", caribbean_observer)

    def test_tutorial_lifecycle_preserves_retry_and_terminal_policies(self) -> None:
        source = read_tutorial_source()
        lookup = cpp_function_body(source, "find_pending_tutorial_function")
        self.assertLess(lookup.index("lifecycle.should_attempt("),
                        lookup.index("find_live_function(function_path)"))

        reset = cpp_function_body(source, "reset_playable_context")
        for family in ("informational_help", "links", "toy_box"):
            self.assertIn(f"{family}_.fill({{}})", reset)
        for family in ("olympus_completion", "olympus_early", "olympus_save_points",
                       "caribbean_sea_spectacle", "twilight_town", "final_world"):
            self.assertIn(f"{family}_.reset()", reset)

        completion = cpp_function_body(source, "try_apply_olympus_completion_for_context")
        self.assertIn("olympus_completion_.record_result(true)", completion)
        self.assertNotIn("record_result(false)", completion)
        self.assertNotIn("note_unavailable()", completion)

        for family in ("olympus_early", "olympus_save_points", "caribbean_sea_spectacle",
                       "twilight_town"):
            body = cpp_function_body(source, f"try_apply_{family}_for_context")
            self.assertIn(f"{family}_.note_unavailable()", body)
            self.assertIn(f"{family}_.record_result(true)", body)
            self.assertIn(f"{family}_.record_result(false)", body)

        final_world = cpp_function_body(source, "try_apply_final_world_for_context")
        self.assertLess(final_world.index("if (applied_bypass)"),
                        final_world.index("if (rejected_function)"))


if __name__ == "__main__":
    unittest.main()
