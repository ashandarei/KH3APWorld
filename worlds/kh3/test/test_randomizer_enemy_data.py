
from worlds.kh3.test.runtime_sources import native_project_root
import json
import re
import unittest
from unittest.mock import patch
from pathlib import Path

from pathspec.gitignore import GitIgnoreSpec
from .runtime_sources import read_composition_sources

REPO_ROOT = Path(__file__).resolve().parents[3]
KH3_ROOT = REPO_ROOT / "worlds" / "kh3"
RANDOMIZER_ROOT = REPO_ROOT / "worlds" / "kh3" / "randomizer"
EXCLUDED_ENEMY_FILE_NAMES = {
    "he_01_enemy_01",
    "he_01_enemy_02",
    "ra_01_enemy",
    "ts_02_enemy_01",
    "ts_02_enemy_04",
    "ts_02_enemy_04_Amber",
    "mi_02_enemy_05",
    "mi_04_enemy_01",
    "fz_01_enemy_15",
    "fz_01_enemy_16",
    "fz_01_enemy_65",
    "ca_02_island01_mission_bigfishbattle",
    "bx_02_enemy_04",
    "kg_01_enemy_01",
}
QOL_LEVEL_ASSET_BASE = "Content/Levels/fz/fz_03/umap/fz_03_gimmick_Avalanche"
REQUIRED_ENEMY_DATA_TABLE_FILES = {
    "e_ex021_BaseDataTable.uasset",
    "e_ex021_BaseDataTable.uexp",
}


class KH3RandomizerEnemyDataTest(unittest.TestCase):
    def test_apworld_enemy_asset_policy_keeps_only_reachable_inputs(self) -> None:
        apignore = GitIgnoreSpec.from_lines((KH3_ROOT / ".apignore").read_text(encoding="utf-8").splitlines())

        level_paths = {
            path.relative_to(RANDOMIZER_ROOT).as_posix()
            for path in (RANDOMIZER_ROOT / "Content" / "Levels").rglob("*.*")
        }
        self.assertEqual(level_paths, {
            f"{QOL_LEVEL_ASSET_BASE}.umap",
            f"{QOL_LEVEL_ASSET_BASE}.uexp",
        })

        enemy_paths = list((RANDOMIZER_ROOT / "Content" / "DataTable" / "Enemy").rglob("*.*"))
        self.assertEqual({path.name for path in enemy_paths}, REQUIRED_ENEMY_DATA_TABLE_FILES)
        for relative_path in level_paths | {
            path.relative_to(RANDOMIZER_ROOT).as_posix() for path in enemy_paths
        }:
            self.assertFalse(apignore.match_file(f"randomizer/{relative_path}"), relative_path)

        from ..seedgen.remaps import EXCLUDED_ENEMY_FILES
        self.assertEqual(EXCLUDED_ENEMY_FILES, EXCLUDED_ENEMY_FILE_NAMES)

    def test_runtime_enemy_target_catalog_covers_every_patch_address(self) -> None:
        all_entities = json.loads((RANDOMIZER_ROOT / "wwwroot" / "DefaultEnemies.json").read_text(encoding="utf-8"))
        catalog = json.loads(
            (RANDOMIZER_ROOT / "wwwroot" / "DefaultEnemyRuntimeTargets.json").read_text(encoding="utf-8")
        )

        self.assertEqual(catalog["schema_version"], 1)
        self.assertEqual(catalog["group_count"], 718)
        self.assertEqual(catalog["target_count"], 5085)
        self.assertEqual(set(catalog["targets"]), {
            group_name
            for entities in all_entities.values()
            for group_name in entities
        })

        seen_object_paths: set[str] = set()
        active_target_count = 0
        for entities in all_entities.values():
            for group_name, entity in entities.items():
                target_group = catalog["targets"][group_name]
                expected_package = entity["FilePath"].replace("Content/", "/Game/", 1).replace("\\", "/")
                expected_package = f"{expected_package}.{expected_package.rsplit('/', 1)[-1]}:PersistentLevel"
                self.assertEqual(target_group["package"], expected_package)
                self.assertEqual(len(target_group["actors"]), len(entity["Addresses"]))

                for actor_name in target_group["actors"]:
                    self.assertTrue(actor_name)
                    object_path = f"{target_group['package']}.{actor_name}"
                    self.assertNotIn(object_path, seen_object_paths)
                    seen_object_paths.add(object_path)

                if Path(entity["FilePath"]).name not in EXCLUDED_ENEMY_FILE_NAMES:
                    active_target_count += len(target_group["actors"])

        self.assertEqual(len(seen_object_paths), 5085)
        self.assertEqual(active_target_count, 4320)

    def test_enemy_compatibility_catalog_is_exhaustive_and_pins_scripted_variants(self) -> None:
        all_entities = json.loads((RANDOMIZER_ROOT / "wwwroot" / "DefaultEnemies.json").read_text(encoding="utf-8"))
        catalog = json.loads(
            (RANDOMIZER_ROOT / "wwwroot" / "DefaultEnemyCompatibility.json").read_text(encoding="utf-8")
        )

        expected_categories = ["ground", "flying", "aquatic", "stationary", "large"]
        self.assertEqual(catalog["schema_version"], 1)
        self.assertEqual(catalog["enemy_count"], 111)
        self.assertEqual(catalog["category_order"], expected_categories)
        self.assertEqual(set(catalog["categories"]), set(expected_categories))
        self.assertEqual(
            {category: len(catalog["categories"][category]) for category in expected_categories},
            {"ground": 31, "flying": 33, "aquatic": 4, "stationary": 10, "large": 33},
        )

        category_by_path: dict[str, str] = {}
        for category in expected_categories:
            for enemy_path in catalog["categories"][category]:
                self.assertNotIn(enemy_path, category_by_path)
                category_by_path[enemy_path] = category

        source_pool_by_path: dict[str, str] = {}
        for entities in all_entities.values():
            for group_name, enemy in entities.items():
                if "(Boss)" in group_name and "(Mini-Boss)" not in group_name:
                    continue
                enemy_path = enemy["EnemyPath"].rstrip("\0")
                pool = "mini_boss" if "(Mini-Boss)" in group_name else "ordinary"
                self.assertIn(enemy_path, category_by_path)
                self.assertEqual(source_pool_by_path.setdefault(enemy_path, pool), pool)

        self.assertEqual(set(category_by_path), set(source_pool_by_path))
        expected_mini_bosses = {
            path for path, pool in source_pool_by_path.items() if pool == "mini_boss"
        }
        self.assertEqual(set(catalog["mini_bosses"]), expected_mini_bosses)
        self.assertEqual(len(catalog["mini_bosses"]), 24)

        scripted_variants = {
            enemy_path
            for enemy_path in source_pool_by_path
            if re.search(r"/[^/]+_s\d+/", enemy_path)
        }
        encounter_specific_satyr = "/Game/Blueprints/Enemy/e_ex006a/e_ex006a_Pawn.e_ex006a_Pawn_C"
        self.assertEqual(set(catalog["pinned"]), scripted_variants | {encounter_specific_satyr})
        self.assertEqual(len(catalog["pinned"]), 39)
        self.assertTrue(all(catalog["pinned"][path] == "scripted_variant" for path in scripted_variants))
        self.assertEqual(catalog["pinned"][encounter_specific_satyr], "encounter_specific_variant")

        representative_categories = {
            "/Game/Blueprints/Enemy/e_ex009/e_ex009_Pawn.e_ex009_Pawn_C": "ground",
            "/Game/Blueprints/Enemy/e_ex010/e_ex010_Pawn.e_ex010_Pawn_C": "flying",
            "/Game/Blueprints/Enemy/e_ex028/e_ex028_Pawn.e_ex028_Pawn_C": "aquatic",
            "/Game/Blueprints/Enemy/e_ex044/e_ex044_Pawn.e_ex044_Pawn_C": "stationary",
            "/Game/Blueprints/Enemy/e_ex093/e_ex093_Pawn.e_ex093_Pawn_C": "large",
        }
        for enemy_path, expected_category in representative_categories.items():
            self.assertEqual(category_by_path[enemy_path], expected_category)

    def test_enemy_randomization_wires_compatibility_into_shuffle_and_chaos(self) -> None:
        from .reference_remaps import enemy_remap, enemy_rules
        rules = enemy_rules(RANDOMIZER_ROOT)
        groups = {name: group for world in json.loads((RANDOMIZER_ROOT / "wwwroot/DefaultEnemies.json").read_text()).values() for name, group in world.items()}
        targets = json.loads((RANDOMIZER_ROOT / "wwwroot/DefaultEnemyRuntimeTargets.json").read_text())["targets"]
        sources = {(target["package"], actor): groups[name]["EnemyPath"].rstrip("\0") for name, target in targets.items() for actor in target["actors"]}
        for chaos in (False, True):
            remap = enemy_remap(RANDOMIZER_ROOT, "compatibility-test", {"Enemy Chaos": chaos})
            self.assertEqual(remap["entry_count"], len(remap["enemy_runtime_entries"]))
            for entry in remap["enemy_runtime_entries"]:
                package, actor, replacement = entry.split("|")
                source = sources[(remap["enemy_runtime_packages"][int(package)], actor)]
                if source not in rules:
                    continue  # Bosses have separate arena rules.
                candidate = remap["enemy_runtime_classes"][int(replacement)]
                category, mini, pinned = rules[source]
                other_category, other_mini, other_pinned = rules[candidate]
                self.assertTrue(category == other_category or category == "flying" and other_category == "ground")
                self.assertEqual(mini, other_mini)
                if pinned:
                    self.assertEqual(candidate, source)
                else:
                    self.assertFalse(other_pinned)

    def test_chaos_candidates_include_each_eligible_class_once(self) -> None:
        from .reference_remaps import compatible_category, enemy_remap, enemy_rules
        from ..seedgen.rng import SeedRandom

        rules = enemy_rules(RANDOMIZER_ROOT)
        allowed_pools = {
            frozenset(path for path, (other_category, other_mini, pinned) in rules.items()
                      if not pinned and other_mini == mini and compatible_category(category, other_category))
            for category, mini, pinned in rules.values() if not pinned
        }
        populations = set()
        original_choice = SeedRandom.choice

        def record_choice(rng, candidates):
            # Inspect the actual draw population, rather than relying on rare
            # enemies happening to be sampled in a particular test seed.
            self.assertEqual(len(candidates), len(set(candidates)))
            populations.add(frozenset(candidates))
            return original_choice(rng, candidates)

        seed = "AP_56819127573328778958_P1_AllRandom"
        with patch.object(SeedRandom, "choice", record_choice):
            remap = enemy_remap(RANDOMIZER_ROOT, seed, {"Enemy Chaos": True})
        self.assertEqual(populations, allowed_pools)
        eligible = {path for path, (_, _, pinned) in rules.items() if not pinned}
        self.assertEqual(set().union(*populations), eligible)
        self.assertEqual(remap, enemy_remap(RANDOMIZER_ROOT, seed, {"Enemy Chaos": True}))

    def test_runtime_enemy_remap_is_lifecycle_event_driven(self) -> None:
        cpp_root = native_project_root() / "KH3APProbeCpp"
        service_source = (
            cpp_root / "kh3ap" / "features" / "enemies" / "enemy_remap_service.cpp"
        ).read_text(encoding="utf-8")
        enemy_root = cpp_root / "kh3ap" / "features" / "enemies"
        service_source += (enemy_root / "level_entity_remap.cpp").read_text(encoding="utf-8")
        service_source += (enemy_root / "remap_runtime.h").read_text(encoding="utf-8")
        service_header = (
            cpp_root / "kh3ap" / "features" / "enemies" / "enemy_remap_service.h"
        ).read_text(encoding="utf-8")
        composition_source = read_composition_sources()
        periodic_source = (cpp_root / "kh3ap" / "app" / "periodic_bridge_service.cpp").read_text(
            encoding="utf-8"
        )

        self.assertNotIn("Hook::RegisterBeginPlayPreCallback", service_source)
        self.assertNotIn("Hook::RegisterStaticConstructObjectPostCallback", service_source)
        self.assertIn("PLH::x64Detour", service_source)
        self.assertIn("TresLevelEntity::SerializePost", service_source)
        self.assertIn("level_entity_serialize_detour", service_source)
        self.assertIn('UObject::VTableLayoutMap.find(STR("Serialize"))', service_source)
        serialize_detour = service_source.split("auto level_entity_serialize_detour(", 1)[1].split(
            "auto install_level_entity_serialize_detour(", 1
        )[0]
        original_serialize = re.search(r"reinterpret_cast<LevelEntitySerializeFn>\(trampoline\)\s*\(", serialize_detour)
        replacement = re.search(r"apply_runtime_enemy_replacement\s*\(", serialize_detour)
        self.assertIsNotNone(original_serialize)
        self.assertIsNotNone(replacement)
        self.assertLess(original_serialize.start(), replacement.start())
        self.assertIn("m_ActorClassPath", service_source)
        self.assertIn("m_ActorClass", service_source)
        self.assertIn("find_loaded_actor_class", service_source)
        self.assertNotIn("LoadAsset_Blocking", service_source)
        self.assertIn("pending_class_rebinds", service_source)
        self.assertIn('#include "kh3ap/core/objects/asset_load_helpers.h"', service_source)
        self.assertNotIn('#include "kh3ap/features/world/portals/portal_spawn_helpers.h"', service_source)
        self.assertIn("request_runtime_asset_class_load", service_source)
        self.assertIn("queue_actor_class_prewarm_path", service_source)
        self.assertIn("process_pending_class_prewarm", service_source)
        self.assertIn("eager_toggle_class_prewarm_enabled = false", service_source)
        self.assertIn("class_prewarm=disabled_on_demand_toggle_load", service_source)
        self.assertIn("prewarm_requests_per_update", service_source)
        self.assertIn("rebind_actor_class_cache", service_source)
        self.assertIn("SetObjectPropertyValue(value_ptr, desired_class)", service_source)
        self.assertIn("class_cache_rebound", service_source)
        self.assertIn("blocking_class_load_us", service_source)
        self.assertNotIn("SetObjectPropertyValue(value_ptr, nullptr)", service_source)
        self.assertNotIn("ForEachUObject", service_source)
        self.assertIn("apply=next_same_map_encounter_spawn", service_source)
        self.assertIn("mode=event_driven polling=false", service_source)
        self.assertEqual(composition_source.count("m_enemy_remaps.install_once();"), 1)
        self.assertEqual(composition_source.count("m_enemy_remaps.process_pending_class_prewarm();"), 1)
        self.assertNotIn("RuntimeEnemyRemapService", periodic_source)
        self.assertNotIn("RuntimeEnemyRemapService::on_update", service_source)
        for forbidden in ("tick(", "sleep_for", "SetTimer"):
            self.assertNotIn(forbidden, service_source)
            self.assertNotIn(forbidden, service_header)


    def test_runtime_boss_remap_reuses_serialized_class_substitution(self) -> None:
        runtime_source = (
            native_project_root()
            / "KH3APProbeCpp"
            / "kh3ap"
            / "features"
            / "enemies"
            / "enemy_remap_service.cpp"
        ).read_text(encoding="utf-8")

        enemy_root = native_project_root() / "KH3APProbeCpp" / "kh3ap" / "features" / "enemies"
        components = ["remap_runtime.h", "remap_catalog.cpp", "level_entity_remap.cpp",
                      "bosses/binding.cpp", "bosses/completion.cpp",
                      "bosses/encounter_repairs.cpp", "bosses/hp_limits.cpp"]
        runtime_source += "\n".join((enemy_root / component).read_text(encoding="utf-8")
                                    for component in components)

        from .reference_remaps import PINNED_BOSSES, boss_compatible, boss_remap
        self.assertLessEqual({"BOSS_004", "BOSS_007", "BOSS_058", *(f"BOSS_{n:03}" for n in range(35, 42))}, PINNED_BOSSES)
        for exceptions in ({}, {"Default Data Battles": True, "Default End Game Battles": True, "Default Yozora Battle": True}):
            remap = boss_remap(RANDOMIZER_ROOT, "boss-contract-test", exceptions)
            self.assertEqual(remap["mode"], "serialized_class_substitution")
            for entry in remap["boss_runtime_entries"]:
                boss, _, replacement = entry.split("|")
                self.assertNotIn(boss, PINNED_BOSSES)
                pawn = remap["boss_runtime_classes"][int(replacement)].split("/")[-1].split(".")[0]
                self.assertTrue(boss_compatible(boss, pawn))
        self.assertIn("remove_lich_boss_replacements(replacements)", runtime_source)
        self.assertIn("remove_final_xehanort_replacements(replacements)", runtime_source)
        self.assertIn("boss_class_replacements", runtime_source)
        self.assertIn("source_class_path", runtime_source)
        self.assertNotIn("BeginDeferredActorSpawnFromClass", runtime_source)
        self.assertIn("m_EntityActor", runtime_source)
        self.assertIn("m_LevelEntity", runtime_source)
        self.assertIn("OnCtorStateEvent", runtime_source)
        self.assertIn("boss_state_hook_ids", runtime_source)
        self.assertIn("boss_state_filter=appear_and_death_only", runtime_source)
        self.assertIn("has_runtime_boss_replacement_class", runtime_source)
        self.assertIn("std::wstring{ai_ctor_state_event_path}", runtime_source)
        self.assertNotIn("runtime_boss_state_event_detour", runtime_source)
        self.assertNotIn("boss_state_candidates", runtime_source)
        self.assertIn("RaiseWipeOutEvent", runtime_source)
        self.assertIn("prepare_runtime_boss_state", runtime_source)
        self.assertIn("route=level_script.OnWipeOut", runtime_source)
        self.assertIn("m_SpawnCount", runtime_source)
        self.assertIn("m_KillCount", runtime_source)
        self.assertIn("boss_wipe_out_group_members", runtime_source)
        self.assertIn("register_level_entity_wipe_out_groups", runtime_source)
        self.assertIn("evaluate_runtime_boss_wipe_out_group", runtime_source)
        self.assertIn("reason=wipe_out_group_incomplete", runtime_source)
        self.assertIn("aggregate_kill_count", runtime_source)
        self.assertIn("group_gate=complete", runtime_source)
        self.assertNotIn("try_write_int32_object_property", runtime_source)
        self.assertIn("replacement_retained_for_story_flow=true", runtime_source)
        self.assertIn("remapped_boss_source_classes", runtime_source)
        self.assertIn("tracked_boss_source_class", runtime_source)
        self.assertIn("e_dw401_s0_REvt_tt_mission_escape", runtime_source)
        self.assertIn("route=level_script.RemoteEvent", runtime_source)
        self.assertIn("custom_source_event_exclusive=true", runtime_source)
        self.assertIn("generic_wipeout_skipped=true", runtime_source)
        self.assertNotIn("UObject::IsReal", runtime_source)
        completion_body = runtime_source.split(
            "auto dispatch_runtime_boss_wipe_out(", maxsplit=1
        )[1].split("auto prepare_runtime_boss_state(", maxsplit=1)[0]
        source_branch = completion_body.split(
            "if (!source_event_name.empty())", maxsplit=1
        )[1].split("UFunction* const on_wipe_out", maxsplit=1)[0]
        self.assertIn("dispatch_source_boss_completion(", source_branch)
        self.assertIn("generic_wipeout_skipped=true", source_branch)
        self.assertIn("return;", source_branch)
        self.assertNotIn("remote_event_hook_ids", runtime_source)
        self.assertNotIn("destroy_completed_runtime_boss_actor", runtime_source)
        self.assertIn("replacement_is_event_actor=true", runtime_source)
        self.assertIn("original_proxy=false", runtime_source)
        self.assertIn("polling=false", runtime_source)

    def test_exp_multiplier_does_not_add_enemy_exp_assets_to_seed_tables(self) -> None:
        from ..seedgen.tables import Tables
        tables = Tables(RANDOMIZER_ROOT)
        tables.rewards({"EXP": {"e_ex001": {"BaseExp": "999"}}}, {"stylesanity": 0, "keyblade_levels": 0})
        encoded = tables.encode()
        self.assertFalse(any("EnemyBaseStatus" in name for name in encoded))
        self.assertFalse(any("BaseDataTable" in name for name in encoded))


if __name__ == "__main__":
    unittest.main()
