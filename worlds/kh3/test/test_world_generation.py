from __future__ import annotations

import json
import os
import re
import zipfile
from collections import Counter
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from BaseClasses import ItemClassification, PlandoOptions
from Fill import distribute_planned_blocks, parse_planned_blocks, resolve_early_locations_for_planned
from Options import PlandoItems

from .. import (
    KH3_CONTAINER_PAYLOAD,
    RETIRED_GROWTH_PATH_ABILITY_POOL_ITEMS,
    SECONDARY_VBONUS_ITEM_POOL_REWARDS,
    KH3Container,
    KH3World,
    PakGenerator,
)
from ..Bridge import (
    BRIDGE_PROTOCOL_NAME,
    BRIDGE_PROTOCOL_VERSION,
    KH3_WORLD_VERSION,
    BridgeProtocolError,
    make_bridge_key,
    validate_protocol_payload,
    validate_slot_data_payload,
)
from ..Data import (
    BAD_STATUS_TRAP_RUNTIME_TOKEN_BY_ITEM_NAME,
    CONTROLLER_UNLOCK_ITEM_NAMES,
    DEFAULT_BASE_ABILITY_CHRINIT_FIELDS,
    DURABLE_SORA_ABILITY_GRANT_METHOD,
    DURABLE_SORA_VBONUS_GRANT_METHOD,
    FILLER_ITEM_NAMES,
    FILLER_ITEM_RUNTIME_TOKEN_BY_NAME,
    FIXED_SORA_CHRINIT_ABILITY_FIELDS,
    GOLDEN_HERC_REWARD_SOURCE_IDS,
    HEART_PIECE_GOAL_COUNT,
    HEART_PIECE_ITEM_NAME,
    KEYBLADE_DISPLAY_NAMES,
    KEYBLADE_ENHANCEMENT_GROUPS,
    LARGE_MUNNY_POUCH_ITEM_NAME,
    LARGE_MUNNY_POUCH_RUNTIME_TOKEN,
    MUNNY_POUCH_GRANT_METHOD,
    MUNNY_POUCH_ITEM_NAME,
    MUNNY_POUCH_RUNTIME_TOKEN,
    PROGRESSIVE_KEYBLADE_LEVEL_INDIVIDUAL_ITEM_NAMES,
    PROGRESSIVE_KEYBLADE_LEVEL_ITEM_COUNT,
    PROGRESSIVE_KEYBLADE_LEVEL_ITEM_NAME,
    PROGRESSIVE_KEYBLADE_LEVEL_ITEM_NAME_BY_KEYBLADE,
    PROGRESSIVE_KEYBLADE_LEVEL_ITEM_NAMES,
    PROGRESSIVE_KEYBLADE_LEVEL_RUNTIME_TOKEN,
    PROOF_ITEMS,
    SPELLWEAVER_UNLOCK_ITEM,
    STARTING_PORTAL_KEYBLADE_ITEM_NAMES,
    STATIC_KEYBLADE_UPGRADE_SANITY_KEYBLADE_NAMES,
    STYLE_CHANGE_UNLOCK_ITEM,
    ULTIMA_WEAPON_ITEM_NAME,
    ULTIMA_WEAPON_SHARD_ITEM_NAME,
    ULTIMA_WEAPON_SHARD_RUNTIME_TOKEN,
    build_item_counts,
    build_item_name_to_id,
    classify_runtime_grant_method,
    goal_required_item_counts,
    is_keyblade_item_name,
    load_extracted_data,
    local_seeded_reward_requires_bridge_delivery,
    normalize_locations,
    resolve_item_display_name,
    resolve_item_runtime_flag_name,
    resolve_item_runtime_token,
    resolve_vbonus_source_id_for_tokens,
)
from ..export_ap_kh3_randomized_options import (
    ALWAYS_ON_EVENT_IDS,
    apply_vbonus_override,
    bridge_only_item_runtime_token,
    build_item_name_to_token,
    build_remote_hint_placements_from_multidata,
    enforce_fixed_sora_chrinit_abilities,
    export_randomized_options_from_bridge_data,
    suppress_default_base_chrinit_abilities,
)
from ..export_ap_kh3_randomized_options import RAW_ITEM_DISPLAY_NAMES as EXPORT_RAW_ITEM_DISPLAY_NAMES
from ..Items import ABILITY_ITEM_NAMES, classify_item
from ..Locations import ROCK_TITAN_NO_WALL_RUN_BONUS_LOCATION_NAME, location_name_to_id
from ..Options import (
    DEFAULT_HINT_CHECKS,
    GOAL_MANAGED_HINT_CHECKS,
    HINT_CHECK_KEYS,
    BattleLevelMode,
    BossesPool,
    DefaultDataBattles,
    DefaultEndGameBattles,
    DefaultYozoraBattle,
    EnemiesPool,
    ExpMultiplier,
    HeartPieceCount,
    ImportantHintChecks,
    KeybladeLevels,
    KH3LocationPoolMode,
    KH3Options,
    LuckyEmblemsPerCheck,
    LuckyEmblemsPool,
    Stylesanity,
    UltimaWeaponMode,
    UltimaWeaponShardCount,
    build_randomizer_generation_config,
    option_groups,
)
from ..PakGenerator import generate_kh3_pak_from_container, generate_kh3_paks_from_ap_zip, load_kh3_container_data
from ..SlotData import decode_slot_data
from . import KH3TestBase
from .test_helpers import KH3WorldIntegrityMixin


class TestKH3WorldGeneration(KH3TestBase):
    options = {
        "remind_pool": KH3LocationPoolMode.option_junk,
    }

    def test_repeatable_filler_uses_curated_consumable_pool(self) -> None:
        self.world_setup(seed=1)

        generated_names = {
            self.world.get_filler_item_name()
            for _ in range(len(FILLER_ITEM_NAMES) * 4)
        }

        self.assertGreater(len(FILLER_ITEM_NAMES), 1)
        self.assertGreater(len(generated_names), 1)
        self.assertLessEqual(generated_names, set(FILLER_ITEM_NAMES))
        self.assertIn(self.world.create_filler().name, FILLER_ITEM_NAMES)
        for item_name, runtime_token in FILLER_ITEM_RUNTIME_TOKEN_BY_NAME.items():
            with self.subTest(item_name=item_name):
                self.assertEqual(classify_item(item_name), ItemClassification.filler)
                self.assertEqual(resolve_item_runtime_token(item_name), runtime_token)

        self.assertNotIn("Strength Boost", FILLER_ITEM_NAMES)
        self.assertNotIn("Orichalcum+", FILLER_ITEM_NAMES)
        self.assertNotIn(LARGE_MUNNY_POUCH_ITEM_NAME, FILLER_ITEM_NAMES)
        self.assertIn(MUNNY_POUCH_ITEM_NAME, self.world.item_name_to_id)
        self.assertIn(LARGE_MUNNY_POUCH_ITEM_NAME, self.world.item_name_to_id)
        self.assertEqual(
            classify_runtime_grant_method(MUNNY_POUCH_RUNTIME_TOKEN),
            MUNNY_POUCH_GRANT_METHOD,
        )
        self.assertEqual(
            classify_runtime_grant_method(LARGE_MUNNY_POUCH_RUNTIME_TOKEN),
            MUNNY_POUCH_GRANT_METHOD,
        )
        self.assertEqual(
            bridge_only_item_runtime_token(LARGE_MUNNY_POUCH_ITEM_NAME),
            LARGE_MUNNY_POUCH_RUNTIME_TOKEN,
        )

    def test_yaml_options_use_junk_terminology(self) -> None:
        self.assertEqual((HeartPieceCount.range_start, HeartPieceCount.range_end), (3, 30))
        self.assertNotIn("goal", KH3Options.type_hints)
        self.assertEqual(KH3LocationPoolMode.name_lookup[KH3LocationPoolMode.option_junk], "junk")
        self.assertEqual(KH3LocationPoolMode.from_text("replace").value, KH3LocationPoolMode.option_junk)
        self.assertTrue({
            "junk_classic_kingdom_reward",
            "junk_golden_herc_rewards",
        }.issubset(KH3Options.type_hints))
        self.assertNotIn("junk_sora_collection_rewards", KH3Options.type_hints)
        self.assertIn("keyblade_levels", KH3Options.type_hints)
        self.assertIn("stylesanity", KH3Options.type_hints)
        self.assertNotIn("style_change", KH3Options.type_hints)
        self.assertIn(
            "On: each Keyblade requires its own style unlock item.",
            " ".join((Stylesanity.__doc__ or "").split()),
        )
        self.assertEqual(Stylesanity.display_name, "Stylesanity")
        self.assertEqual(Stylesanity.default, 1)
        self.assertEqual(Stylesanity.name_lookup, {0: "false", 1: "true"})
        self.assertEqual(Stylesanity.from_text("on").value, 1)
        self.assertEqual(Stylesanity.from_text("off").value, 0)
        self.assertEqual(KeybladeLevels.name_lookup, {
            KeybladeLevels.option_start_at_max: "start_at_max",
            KeybladeLevels.option_shuffled: "shuffled",
            KeybladeLevels.option_keyblade_upgrade_sanity: "keyblade_upgrade_sanity",
        })
        self.assertEqual(KeybladeLevels.default, KeybladeLevels.option_shuffled)
        self.assertEqual(KeybladeLevels.get_option_name(0), "Start at Max")
        self.assertEqual(KeybladeLevels.get_option_name(1), "Shuffled")
        self.assertEqual(KeybladeLevels.get_option_name(2), "Keyblade Upgrade Sanity")
        self.assertIn("ultima_weapon_mode", KH3Options.type_hints)
        self.assertIn("ultima_weapon_shard_count", KH3Options.type_hints)
        self.assertEqual(UltimaWeaponMode.name_lookup, {
            UltimaWeaponMode.option_shards: "shards",
            UltimaWeaponMode.option_shop: "shop",
            UltimaWeaponMode.option_yozora: "yozora",
            UltimaWeaponMode.option_randomized_pool: "randomized_pool",
            UltimaWeaponMode.option_banished: "banished",
        })
        self.assertEqual(UltimaWeaponMode.default, UltimaWeaponMode.option_shards)
        self.assertEqual(UltimaWeaponShardCount.range_start, 2)
        self.assertEqual(UltimaWeaponShardCount.default, 7)
        self.assertEqual(UltimaWeaponShardCount.range_end, 13)
        self.assertIn("battle_level_mode", KH3Options.type_hints)
        self.assertIn("skip_guardians_and_mickey_walk", KH3Options.type_hints)
        skip_guardians_and_mickey_walk = KH3Options.type_hints[
            "skip_guardians_and_mickey_walk"
        ]
        self.assertEqual(
            skip_guardians_and_mickey_walk.display_name,
            "Skip Guardians and Mickey Walk",
        )
        self.assertEqual(skip_guardians_and_mickey_walk.default, 1)

    def test_enemy_and_boss_yaml_options_only_control_initial_runtime_state(self) -> None:
        self.assertEqual(EnemiesPool.default, EnemiesPool.option_vanilla)
        self.assertIn("Start Enemy Randomization disabled", EnemiesPool.choice_descriptions["vanilla"])
        self.assertIn("Start Enemy Randomization enabled", EnemiesPool.choice_descriptions["randomize"])
        self.assertIn("Every AP seed includes the remap", " ".join((EnemiesPool.__doc__ or "").split()))
        self.assertEqual(BossesPool.default, BossesPool.option_vanilla)
        self.assertIn("Start Boss Randomization disabled", BossesPool.choice_descriptions["vanilla"])
        self.assertIn("Start Boss Randomization enabled", BossesPool.choice_descriptions["randomize"])
        self.assertIn("Every AP seed includes the remap", " ".join((BossesPool.__doc__ or "").split()))
        for option_name, display_name in (
            ("fast_union_cross", "Fast Union Cross"),
            ("fast_dance_minigame", "Fast Dance Minigame"),
            ("fast_beat_of_lead", "Fast Beat of Lead"),
        ):
            with self.subTest(option_name=option_name):
                self.assertIn(option_name, KH3Options.type_hints)
                option_type = KH3Options.type_hints[option_name]
                self.assertEqual(option_type.display_name, display_name)
                self.assertEqual(option_type.default, 1)
        self.assertNotIn("clear_condition", KH3Options.type_hints)
        self.assertEqual(LuckyEmblemsPerCheck.special_range_names, {"vanilla milestones": 0})
        self.assertEqual(LuckyEmblemsPerCheck.from_text("vanilla milestones").value, 0)
        self.assertTrue({
            "can_be_none",
            "big_hero_rescue_skip",
            "union_x_skip",
            "include_lucky_emblems",
            "include_battlegates",
            "include_data_battles",
            "include_remind",
            "include_unreality",
            "include_keyblade_graveyard",
        }.isdisjoint(KH3Options.type_hints))
        self.assertIn("keyblade_graveyard_pool", KH3Options.type_hints)
        self.assertEqual(BattleLevelMode.name_lookup[0], "off")
        self.assertEqual(BattleLevelMode.name_lookup[1], "sora_level")
        self.assertEqual(BattleLevelMode.name_lookup, {0: "off", 1: "sora_level"})
        self.assertEqual(BattleLevelMode.from_any("off").value, 0)
        self.assertEqual(BattleLevelMode.from_any("sora_level").value, 1)
        for removed_value in ("sphere_and_tools", 2):
            with self.assertRaises(KeyError):
                BattleLevelMode.from_any(removed_value)
        self.assertFalse(any(option_name.startswith("replace_") for option_name in KH3Options.type_hints))
        self.assertNotIn("all_maps_unlocked", KH3Options.type_hints)

        self.world_setup()
        self.assertEqual(
            self.world.build_runtime_slot_data()["battle_level_mode"],
            BattleLevelMode.option_sora_level,
        )


    def test_area_maps_are_baseline_and_excluded_from_the_ap_pool(self) -> None:
        source_item_counts = build_item_counts(
            self.world.active_locations + self.world.starting_inventory_locations
        )
        source_map_count = sum(
            count
            for item_name, count in source_item_counts.items()
            if item_name.startswith("Map: ")
        )
        generated_items = self.multiworld.itempool + self.multiworld.precollected_items[self.player]

        self.assertGreater(source_map_count, 0)
        self.assertFalse(any(item.name.startswith("Map: ") for item in generated_items))
        self.assertEqual(len(self.multiworld.itempool), len(self.multiworld.get_unfilled_locations(self.player)))
        self.assertTrue(self.world.build_runtime_slot_data()["kh3_randomizer"]["qol"]["Items"]["All Maps Unlocked"])

    def test_boss_safeguards_are_grouped_as_experimental(self) -> None:
        safeguards = {
            DefaultDataBattles: "Keep Data Battles Vanilla (Boss Randomizer)",
            DefaultEndGameBattles: "Keep Endgame Bosses Vanilla (Boss Randomizer)",
            DefaultYozoraBattle: "Keep Yozora Vanilla (Boss Randomizer)",
        }
        experimental_group = next(
            group for group in option_groups
            if group.name == "KH3 Enemy and Party Pools (Experimental)"
        )
        randomizer_rules_group = next(
            group for group in option_groups
            if group.name == "KH3 Randomizer Rules"
        )

        grouped_options = set(safeguards)
        self.assertTrue(grouped_options.issubset(experimental_group.options))
        self.assertTrue(grouped_options.isdisjoint(randomizer_rules_group.options))
        for option, expected_display_name in safeguards.items():
            self.assertEqual(option.display_name, expected_display_name)
            self.assertIn("enabled later in-game", " ".join((option.__doc__ or "").split()))

    def test_lucky_emblem_frequency_is_grouped_below_its_pool(self) -> None:
        generation_group = next(group for group in option_groups if group.name == "Generation")
        lucky_group = next(group for group in option_groups if group.name == "KH3 Lucky Emblems")
        lucky_pool_index = lucky_group.options.index(LuckyEmblemsPool)

        self.assertIn(KeybladeLevels, generation_group.options)
        self.assertIn(Stylesanity, generation_group.options)
        self.assertNotIn(LuckyEmblemsPerCheck, generation_group.options)
        self.assertIs(lucky_group.options[lucky_pool_index + 1], LuckyEmblemsPerCheck)


class TestKH3SoraBattleLevelMode(KH3TestBase):
    options = {
        "battle_level_mode": BattleLevelMode.option_sora_level,
    }

    def test_sora_level_mode_exports_choice_without_calculating_spheres(self) -> None:
        self.world_setup()
        slot_data = self.world.build_runtime_slot_data()
        self.assertEqual(slot_data["battle_level_mode"], BattleLevelMode.option_sora_level)
        self.assertNotIn("goal_sphere", slot_data["bridge"])
        self.assertTrue(all("logic_sphere" not in location for location in slot_data["bridge"]["locations"]))


class TestKH3OffBattleLevelMode(KH3TestBase):
    options = {
        "battle_level_mode": BattleLevelMode.option_off,
    }

    def test_off_mode_disables_seed_battle_level_table(self) -> None:
        self.world_setup()

        slot_data = self.world.build_runtime_slot_data()
        self.assertEqual(slot_data["battle_level_mode"], BattleLevelMode.option_off)
        self.assertFalse(slot_data["kh3_randomizer"]["battle_level_sync"])


class TestKH3SecretReportItemPool(KH3TestBase):
    auto_construct = False
    test_all_state_can_reach_everything = None
    test_empty_state_can_reach_something = None
    test_fill = None

    def test_all_thirteen_reports_survive_battlegate_pool_modes(self) -> None:
        expected_counts = Counter({f"Secret Report {number}": 1 for number in range(1, 14)})
        for mode in (KH3LocationPoolMode.option_randomize, KH3LocationPoolMode.option_junk):
            for seed in (1, 2, 3):
                with self.subTest(battlegates_pool=mode, seed=seed):
                    self.options = {"battlegates_pool": mode}
                    self.world_setup(seed=seed)
                    reports = [
                        item
                        for item in self.multiworld.itempool + self.multiworld.precollected_items[self.player]
                        if item.name.startswith("Secret Report ")
                    ]
                    self.assertEqual(Counter(item.name for item in reports), expected_counts)
                    self.assertTrue(all(item.classification == ItemClassification.useful for item in reports))
                    self.assertEqual(len(self.multiworld.itempool), len(self.multiworld.get_unfilled_locations()))

    def test_all_thirteen_reports_are_preserved_after_seed_fill(self) -> None:
        from Fill import distribute_items_restrictive

        expected_counts = Counter({f"Secret Report {number}": 1 for number in range(1, 14)})
        for mode in (KH3LocationPoolMode.option_randomize, KH3LocationPoolMode.option_junk):
            with self.subTest(battlegates_pool=mode):
                self.options = {"battlegates_pool": mode}
                self.world_setup(seed=1)
                distribute_items_restrictive(self.multiworld)
                placed_items = [location.item for location in self.multiworld.get_filled_locations()]
                reports = [
                    item
                    for item in placed_items + self.multiworld.precollected_items[self.player]
                    if item.name.startswith("Secret Report ")
                ]
                self.assertEqual(Counter(item.name for item in reports), expected_counts)
                self.assertTrue(all(item.classification == ItemClassification.useful for item in reports))
                self.assertFalse(self.multiworld.get_unfilled_locations())


class TestKH3PlandoItemPool(KH3TestBase):
    options = {
        "battlegates_pool": KH3LocationPoolMode.option_junk,
        "plando_items": [
            {
                "items": {
                    "Map: Mount Olympus": 1,
                    "Map: Thebes": 1,
                },
                "locations": [
                    "Olympus - EVENT_KEYBLADE_001 - RandomizedItem",
                    "Twilight Town - EVENT_KEYBLADE_002 - RandomizedItem",
                ],
                "from_pool": True,
                "force": True,
            },
        ],
    }

    def test_created_plando_items_use_standard_ap_surplus_handling(self) -> None:
        self.world_setup(seed=1)

        item_names = {"Map: Mount Olympus", "Map: Thebes"}
        target_names = self.options["plando_items"][0]["locations"]
        initial_pool_size = len(self.multiworld.itempool)
        self.assertTrue(item_names.isdisjoint(item.name for item in self.multiworld.itempool))
        self.assertEqual(initial_pool_size, len(self.multiworld.get_unfilled_locations()))

        self.multiworld.plando_item_blocks = parse_planned_blocks(self.multiworld)
        resolve_early_locations_for_planned(self.multiworld)
        distribute_planned_blocks(
            self.multiworld,
            self.multiworld.plando_item_blocks[self.player],
        )

        self.assertEqual(
            {self.multiworld.get_location(name, self.player).item.name for name in target_names},
            item_names,
        )
        self.assertEqual(len(self.multiworld.itempool), initial_pool_size)

        self.world.pre_fill()

        self.assertEqual(len(self.multiworld.itempool), initial_pool_size)

        self.world.pre_fill()
        self.assertEqual(len(self.multiworld.itempool), initial_pool_size)
        from Fill import distribute_items_restrictive
        with self.assertLogs(level='WARNING') as logs:
            distribute_items_restrictive(self.multiworld)
        self.assertTrue(any('Unplaced items' in message for message in logs.output))
        self.assertFalse(self.multiworld.get_unfilled_locations())
        self.assertEqual({self.multiworld.get_location(name, self.player).item.name
                          for name in target_names}, item_names)
        self.assertTrue(self.multiworld.can_beat_game())
        self.assertTrue(self.multiworld.fulfills_accessibility())

    def test_existing_pool_plando_item_does_not_remove_extra_filler(self) -> None:
        self.world_setup(seed=1)
        target_name = "Olympus - EVENT_KEYBLADE_001 - RandomizedItem"
        initial_pool_size = len(self.multiworld.itempool)
        self.assertTrue(any(item.name == "Potion" for item in self.multiworld.itempool))
        self.world.options.plando_items.value = PlandoItems.from_any([
            {
                "item": "Potion",
                "location": target_name,
                "from_pool": True,
                "force": True,
            },
        ]).value

        self.multiworld.plando_item_blocks = parse_planned_blocks(self.multiworld)
        resolve_early_locations_for_planned(self.multiworld)
        distribute_planned_blocks(
            self.multiworld,
            self.multiworld.plando_item_blocks[self.player],
        )
        pool_size_after_plando = len(self.multiworld.itempool)

        self.world.pre_fill()

        self.assertEqual(self.multiworld.get_location(target_name, self.player).item.name, "Potion")
        self.assertEqual(pool_size_after_plando, initial_pool_size - 1)
        self.assertEqual(len(self.multiworld.itempool), pool_size_after_plando)
        self.assertEqual(len(self.multiworld.itempool), len(self.multiworld.get_unfilled_locations()))


class TestKH3WorldIntegrity(KH3WorldIntegrityMixin, KH3TestBase):
    test_all_state_can_reach_everything = None
    test_empty_state_can_reach_something = None
    test_fill = None

    def test_location_name_to_id_uses_descriptions(self) -> None:
        display_name = "Chest 1 (Large Chest, The Neighborhood: Tram Common, Near Gummi Shop)"

        self.assertIn(display_name, location_name_to_id)
        self.assertNotIn("Twilight Town - Large Chest 1", location_name_to_id)
        self.assertIn("Olympus - Victory Bonus 004", location_name_to_id)
        self.assertEqual(location_name_to_id["Olympus - Victory Bonus 004"], 1263023176)
        disabled_vbonus = next(
            location
            for location in normalize_locations(load_extracted_data())
            if (location["source_table"], location["source_id"]) == ("VBonus", "Vbonus_004")
        )
        self.assertIs(disabled_vbonus["ap_enabled"], False)
        self.assertEqual(disabled_vbonus["vanilla_items"]["Sora_Ability1"], "ITEM_POTION")
        self.assertIn("10,000 Enemy Fight in The Keyblade Graveyard: The Badlands a", location_name_to_id)
        self.assertIn("10,000 Enemy Fight in The Keyblade Graveyard: The Badlands b", location_name_to_id)

    def test_olympus_event_locations_use_friendly_names(self) -> None:
        expected_names = {
            "EVENT_001": "Forge Knight's Shield+ for Goofy",
            "EVENT_002": "Find Golden Herc Figure in Thebes: Alleyway, In Building",
            "EVENT_003": "Find Golden Herc Figure in Thebes: Garden, Digged-Up Pit",
            "EVENT_004": "Find Golden Herc Figure in Thebes: Garden, In Building",
            "EVENT_005": "Find Golden Herc Figure in Thebes: Overlook, On Statue's Shield",
            "EVENT_006": "Find Golden Herc Figure in Thebes: Agora, Near Save Point",
            "EVENT_007": "Return All Golden Herc Figures",
        }
        locations_by_source_id = {
            location["source_id"]: location
            for location in normalize_locations(load_extracted_data())
            if location["source_table"] == "Event"
        }

        for source_id, friendly_name in expected_names.items():
            location = locations_by_source_id[source_id]
            self.assertEqual(location["location_description"], friendly_name)
            self.assertEqual(location["name"], friendly_name)
            self.assertIn(friendly_name, location_name_to_id)

    def test_little_chef_reward_is_not_an_archipelago_check(self) -> None:
        locations = normalize_locations(load_extracted_data())

        self.assertFalse(
            any(
                location["source_table"] == "Event"
                and location["source_id"] == "EVENT_KEYBLADE_010"
                for location in locations
            )
        )

    def test_slot_data_contains_bridge_metadata(self) -> None:
        self.world_setup()

        slot_data = self.world.build_runtime_slot_data()
        bridge = slot_data["bridge"]

        self.assertEqual(slot_data["protocol_name"], BRIDGE_PROTOCOL_NAME)
        self.assertEqual(slot_data["protocol_version"], BRIDGE_PROTOCOL_VERSION)
        self.assertEqual(bridge["protocol_name"], BRIDGE_PROTOCOL_NAME)
        self.assertEqual(bridge["protocol_version"], BRIDGE_PROTOCOL_VERSION)
        self.assertEqual(bridge["heart_piece_count"], 7)
        required_item_counts = goal_required_item_counts(7)
        self.assertEqual(bridge["goal_required_item_counts"], required_item_counts)
        self.assertNotIn("goal_required_items", bridge)
        self.assertNotIn("proof_items", bridge)
        self.assertEqual(bridge["completion_location_name"], "Defeat Master Xehanort")
        self.assertEqual(bridge["chest_persistence_version"], 1)
        self.assertTrue(all(
            entry.get("chest_persistence_version") == 1
            for entry in bridge["locations"] if entry["type"] == "treasure"
        ))

        bridge_locations = [row for row in bridge["locations"] if row["type"] != "sequence_break_bonus"]
        self.assertEqual(len(bridge_locations), len(self.world.active_locations))
        self.assertEqual(
            {entry["ap_location_name"] for entry in bridge_locations},
            {location["name"] for location in self.world.active_locations},
        )

        seen_bridge_keys = set()
        for entry in bridge_locations:
            self.assertIn(entry["ap_location_name"], location_name_to_id)
            self.assertEqual(entry["ap_location_id"], location_name_to_id[entry["ap_location_name"]])
            self.assertEqual(entry["bridge_key"], f"{entry['source_table']}:{entry['source_id']}")
            self.assertNotIn(entry["bridge_key"], seen_bridge_keys)
            seen_bridge_keys.add(entry["bridge_key"])
            if entry["bridge_key"] == "VBonus:Vbonus_016":
                self.assertEqual(
                    entry["ap_location_name"],
                    "Twilight Town - The Old Mansion Heartless & Nobodies",
                )
                self.assertNotIn("legacy_ap_location_name", entry)
                self.assertEqual(
                    entry["location_description"],
                    "Twilight Town - The Old Mansion Heartless & Nobodies",
                )
            if entry["bridge_key"] == "TreasureTT:TT_LBOX_001":
                self.assertEqual(
                    entry["ap_location_name"],
                    "Chest 1 (Large Chest, The Neighborhood: Tram Common, Near Gummi Shop)",
                )
                self.assertNotIn("legacy_ap_location_name", entry)
                self.assertEqual(
                    entry["location_description"],
                    "Chest 1 (Large Chest, The Neighborhood: Tram Common, Near Gummi Shop)",
                )

    def test_slot_data_contains_kh3_randomizer_config(self) -> None:
        self.world_setup()

        slot_data = self.world.build_runtime_slot_data()
        config = slot_data["kh3_randomizer"]

        self.assertEqual(config, build_randomizer_generation_config(self.world.options))
        self.assertEqual(config["pools"]["Olympus"], 2)
        self.assertEqual(config["pools"]["100 Acre Wood"], 3)
        self.assertEqual(config["pools"]["Fullcourse Abilities"], 3)
        self.assertEqual(config["pools"]["Lucky Emblems"], 3)
        self.assertEqual(config["pools"]["Unreality"], 3)
        self.assertEqual(config["pools"]["Keyblade Graveyard"], 2)
        self.assertEqual(config["pools"]["Base Sora Stats"], 1)
        self.assertEqual(config["pools"]["Keyblade Enhance Stats"], 1)
        self.assertEqual(config["pools"]["Enemies"], 1)
        self.assertEqual(config["pools"]["Always On"], 1)
        self.assertTrue(config["exceptions"]["Safe Magic Locations"])
        self.assertNotIn("Extra Movement", config["exceptions"])
        self.assertFalse(config["exceptions"]["Can Be None"])
        self.assertEqual(config["limiters"]["Level Up Limit"], 35)
        self.assertEqual(config["limiters"]["Lucky Emblem Limit"], 50)
        self.assertEqual(config["lucky_emblems_per_check"], 0)
        self.assertTrue(config["battle_level_sync"])
        self.assertEqual(config["limiters"]["Moogle Synthesis Limit"], 15)
        self.assertEqual(config["exp_multiplier"], 1.0)
        self.assertTrue(config["qol"]["Bosses"]["Easier Mini-UFO"])
        self.assertTrue(config["qol"]["Events"]["Frozen Chase Skip"])
        self.assertNotIn("Big Hero Rescue Skip", config["qol"]["Events"])
        self.assertNotIn("big_hero_rescue_skip", slot_data)
        self.assertTrue(config["qol"]["Items"]["All Maps Unlocked"])
        self.assertFalse(slot_data["auto_skip_cutscenes"])
        self.assertTrue(slot_data["skip_guardians_and_mickey_walk"])
        self.assertTrue(slot_data["fast_union_cross"])
        self.assertTrue(slot_data["fast_dance_minigame"])
        self.assertTrue(slot_data["fast_beat_of_lead"])
        self.assertEqual(slot_data["lucky_emblems_per_check"], 0)
        self.assertFalse(slot_data["start_with_wall_run"])
        self.assertEqual(slot_data["stylesanity"], 1)
        self.assertNotIn("style_change", slot_data)
        self.assertNotIn("keyblade_style_sanity", slot_data)
        self.assertNotIn("start_with_style_change", slot_data)
        self.assertFalse(slot_data["start_with_magic"])
        self.assertFalse(slot_data["start_with_attraction_flow"])
        self.assertTrue(slot_data["start_with_airstep"])
        self.assertEqual(slot_data["keyblade_levels"], KeybladeLevels.option_shuffled)
        self.assertNotIn("keyblade_upgrade_sanity", slot_data)
        self.assertFalse(config["qol"]["Items"]["Easily Upgraded Keyblades"])
        self.assertFalse(config["runtime"]["auto_skip_cutscenes"])
        self.assertTrue(config["runtime"]["skip_guardians_and_mickey_walk"])
        self.assertTrue(config["runtime"]["fast_union_cross"])
        self.assertTrue(config["runtime"]["fast_dance_minigame"])
        self.assertTrue(config["runtime"]["fast_beat_of_lead"])
        self.assertNotIn("controller_unlocks", config)
        self.assertEqual(config["hint"]["type"], "Verbose")
        self.assertIn("Heart Piece", config["hint"]["checks"])
        self.assertEqual(config["clear_condition"]["type"], "Heart Pieces")
        self.assertEqual(config["clear_condition"]["conditions"]["KEY_ITEM17\u0000"], 7)

    def test_guardians_and_mickey_walk_skip_can_be_disabled(self) -> None:
        self.world.options.skip_guardians_and_mickey_walk.value = 0

        slot_data = self.world.build_runtime_slot_data()

        self.assertFalse(slot_data["skip_guardians_and_mickey_walk"])
        self.assertFalse(
            slot_data["kh3_randomizer"]["runtime"]["skip_guardians_and_mickey_walk"]
        )

    def test_world_intro_skip_defaults_off_in_seed_config(self) -> None:
        self.world_setup()
        config = self.world.build_runtime_slot_data()["kh3_randomizer"]
        self.assertFalse(config["skip_world_intro_cutscenes"])

    def test_fun_10k_heartless_defaults_off_and_reaches_seed_config(self) -> None:
        self.world_setup()
        self.assertFalse(self.world.build_runtime_slot_data()["fun_10k_heartless"])
        self.assertFalse(self.world.build_runtime_slot_data()["kh3_randomizer"]["runtime"]["fun_10k_heartless"])
        self.world.options.fun_10k_heartless.value = 1
        self.assertTrue(self.world.build_runtime_slot_data()["fun_10k_heartless"])
        self.assertTrue(self.world.build_runtime_slot_data()["kh3_randomizer"]["runtime"]["fun_10k_heartless"])

    def test_world_intro_skip_is_independent_of_runtime_cutscene_skip(self) -> None:
        self.world_setup()
        self.world.options.skip_world_intro_cutscenes.value = 1
        config = self.world.build_runtime_slot_data()["kh3_randomizer"]
        self.assertTrue(config["skip_world_intro_cutscenes"])
        self.assertFalse(config["runtime"]["auto_skip_cutscenes"])

    def test_boss_blacklist_yaml_reaches_generation_config(self) -> None:
        from ..Options import BossBlacklist, build_randomizer_generation_config
        self.assertEqual(BossBlacklist.default, frozenset())
        self.world.options.boss_blacklist = BossBlacklist.from_any(["Yozora", "Data Ansem"])
        self.world.options.boss_blacklist.verify(KH3World, "Sora", PlandoOptions(0))
        config = build_randomizer_generation_config(self.world.options)
        self.assertEqual(config["boss_blacklist"], ["Data Ansem", "Yozora"])
        group = next(group for group in option_groups if group.name == "KH3 Enemy and Party Pools (Experimental)")
        self.assertIn(BossBlacklist, group.options)

    def test_ui_yaml_defaults_reach_runtime_settings(self) -> None:
        from ..RuntimeSeed import runtime_documents
        values = dict(map_overlay_enabled=0, song_display=0, hide_locked_chest_icons=1,
                      map_chest_icons=0, map_emblem_icons=0, map_ingredient_icons=0,
                      minimap_icons=0, large_map_icons=0, battle_level_offset=10,
                      melody_of_memory_party=3, melody_of_memory_play_style=2,
                      melody_of_memory_starting_item=3)
        for key, value in values.items():
            option = KH3Options.type_hints[key].from_any(value)
            setattr(self.world.options, key, option)
        slot = self.world.build_runtime_slot_data()
        runtime = runtime_documents({"slot_data": slot})["runtime_seed_settings.json"]["slot_data"]
        for key, value in values.items():
            with self.subTest(key=key):
                self.assertEqual(slot[key], value)
                self.assertEqual(runtime[key], value)
        offset = KH3Options.type_hints["battle_level_offset"]
        for value in (-3, 0, 5, 10):
            self.assertEqual(offset.from_any(value).value, value)
        for value in (-4, 6, 9, 11):
            with self.assertRaises(Exception):
                offset.from_any(value)

    def test_map_keybind_yaml_option_reaches_runtime_settings(self) -> None:
        from ..RuntimeSeed import runtime_documents
        option_type = KH3Options.type_hints["map_overlay_keybinds"]
        self.assertEqual(option_type.default, 1)
        group = next(group for group in option_groups if group.name == "Map Options")
        self.assertIn(option_type, group.options)
        for enabled in (0, 1):
            with self.subTest(enabled=enabled):
                self.world.options.map_overlay_keybinds.value = enabled
                slot_data = self.world.build_runtime_slot_data()
                self.assertEqual(slot_data["map_overlay_keybinds"], enabled)
                documents = runtime_documents({"slot_data": slot_data})
                self.assertEqual(documents["runtime_seed_settings.json"]["slot_data"]["map_overlay_keybinds"], enabled)

    def test_song_display_yaml_option_reaches_runtime_settings(self) -> None:
        from ..RuntimeSeed import runtime_documents
        option_type = KH3Options.type_hints["song_display"]
        self.assertEqual(option_type.default, 1)
        group = next(group for group in option_groups if group.name == "Map Options")
        self.assertIn(option_type, group.options)
        for enabled in (0, 1):
            with self.subTest(enabled=enabled):
                self.world.options.song_display = option_type.from_any(enabled)
                slot = self.world.build_runtime_slot_data()
                self.assertEqual(slot["song_display"], enabled)
                self.assertEqual(runtime_documents({"slot_data": slot})["runtime_seed_settings.json"]["slot_data"]["song_display"], enabled)

    def test_essential_auto_equip_yaml_reaches_runtime_settings(self) -> None:
        from ..RuntimeSeed import runtime_documents
        option_type = KH3Options.type_hints["auto_equip_essential_abilities"]
        self.assertEqual(option_type.default, 1)
        group = next(group for group in option_groups if group.name == "KH3 Randomizer Rules")
        self.assertIn(option_type, group.options)
        for enabled in (0, 1):
            with self.subTest(enabled=enabled):
                self.world.options.auto_equip_essential_abilities = option_type.from_any(enabled)
                slot = self.world.build_runtime_slot_data()
                self.assertEqual(slot["auto_equip_essential_abilities"], enabled)
                self.assertEqual(runtime_documents({"slot_data": slot})["runtime_seed_settings.json"]
                                 ["slot_data"]["auto_equip_essential_abilities"], enabled)

    def test_fast_minigame_options_can_be_disabled_independently(self) -> None:
        self.world.options.fast_union_cross.value = 0

        slot_data = self.world.build_runtime_slot_data()

        self.assertFalse(slot_data["fast_union_cross"])
        self.assertFalse(slot_data["kh3_randomizer"]["runtime"]["fast_union_cross"])
        self.assertTrue(slot_data["fast_dance_minigame"])
        self.assertTrue(slot_data["kh3_randomizer"]["runtime"]["fast_dance_minigame"])
        self.assertTrue(slot_data["fast_beat_of_lead"])
        self.assertTrue(slot_data["kh3_randomizer"]["runtime"]["fast_beat_of_lead"])

        self.world.options.fast_union_cross.value = 1
        self.world.options.fast_dance_minigame.value = 0
        slot_data = self.world.build_runtime_slot_data()

        self.assertTrue(slot_data["fast_union_cross"])
        self.assertTrue(slot_data["kh3_randomizer"]["runtime"]["fast_union_cross"])
        self.assertFalse(slot_data["fast_dance_minigame"])
        self.assertFalse(slot_data["kh3_randomizer"]["runtime"]["fast_dance_minigame"])
        self.assertTrue(slot_data["fast_beat_of_lead"])
        self.assertTrue(slot_data["kh3_randomizer"]["runtime"]["fast_beat_of_lead"])

        self.world.options.fast_dance_minigame.value = 1
        self.world.options.fast_beat_of_lead.value = 0
        slot_data = self.world.build_runtime_slot_data()

        self.assertTrue(slot_data["fast_union_cross"])
        self.assertTrue(slot_data["kh3_randomizer"]["runtime"]["fast_union_cross"])
        self.assertTrue(slot_data["fast_dance_minigame"])
        self.assertTrue(slot_data["kh3_randomizer"]["runtime"]["fast_dance_minigame"])
        self.assertFalse(slot_data["fast_beat_of_lead"])
        self.assertFalse(slot_data["kh3_randomizer"]["runtime"]["fast_beat_of_lead"])

    def test_default_junk_unreality_retires_proof_of_fantasy(self) -> None:
        self.world_setup()

        self.assertTrue(any(
            location["world"] == "Unreality"
            for location in self.world.active_locations
        ))
        self.assertEqual(
            Counter(
                item.name
                for item in self.multiworld.itempool + self.multiworld.precollected_items[self.player]
            )["Proof of Fantasy"],
            0,
        )
        self.assertEqual(
            self.world.build_runtime_slot_data()["kh3_randomizer"]["pools"]["Unreality"],
            KH3LocationPoolMode.option_junk,
        )

    def test_exp_multiplier_supports_only_selected_values(self) -> None:
        self.world_setup()

        multipliers = (0.5, 1, 2, 3, 4, 5, 10, 20)
        self.assertEqual(set(ExpMultiplier.options.values()), {int(value * 2) for value in multipliers})
        for multiplier in multipliers:
            value = int(multiplier * 2)
            self.assertEqual(ExpMultiplier.get_option_name(value), f"{multiplier:g}x")
            self.world.options.exp_multiplier.value = ExpMultiplier.from_any(value).value
            self.assertEqual(build_randomizer_generation_config(self.world.options)["exp_multiplier"], multiplier)
        for removed in ("1_5x", "2_5x", "3_5x", "4_5x", 3, 5, 7, 9):
            with self.assertRaises(KeyError):
                ExpMultiplier.from_any(removed)

    def test_yaml_settings_survive_slot_encoding_and_runtime_projection(self) -> None:
        import yaml

        from ..RuntimeSeed import SETTINGS_FILE, encode_document, runtime_documents
        from ..SlotData import decode_slot_data
        from ..seedgen.remaps import add_runtime_settings

        self.world_setup()
        work_dir = TemporaryDirectory()
        self.addCleanup(work_dir.cleanup)
        root = PakGenerator.prepare_randomizer_root(Path(work_dir.name))
        for exp, party, chaos, color, music, custom, themes, battle in (
            ("0_5x", "vanilla", 0, "off", "vanilla", 0, 0, "off"),
            ("5_0x", "randomize", 1, "wild", "chaos", 1, 1, "sora_level"),
            ("20_0x", "randomize", 0, "coordinated", "shuffle_battle_themes", 0, 1, "sora_level"),
            ("2_0x", "vanilla", 1, "off", "shuffle_worlds", 1, 0, "off"),
        ):
            with self.subTest(exp=exp, color=color, music=music):
                yaml_options = yaml.safe_load(f"""
exp_multiplier: {exp}
party_members_pool: {party}
party_chaos: {chaos}
color_randomization: "{color}"
music_shuffle: {music}
include_custom_music: {custom}
form_themes: {themes}
battle_level_mode: "{battle}"
""")
                for name, value in yaml_options.items():
                    option = type(getattr(self.world.options, name)).from_any(value)
                    setattr(self.world.options, name, option)
                slot = decode_slot_data(self.world.fill_slot_data())
                metadata = {"seed": "settings-test", "slot": 1, "slot_name": "Sora", "slot_data": slot}
                add_runtime_settings(root, "settings-test", slot["kh3_randomizer"], metadata)
                documents = runtime_documents(metadata)
                settings = json.loads(encode_document(SETTINGS_FILE, documents[SETTINGS_FILE]))
                options = settings["slot_data"]
                self.assertEqual(options["kh3_randomizer"]["exp_multiplier"],
                                 self.world.options.exp_multiplier.value / 2)
                self.assertEqual(options["kh3_randomizer"]["battle_level_sync"], battle != "off")
                self.assertEqual(settings["party_randomization_initially_enabled"], party == "randomize")
                self.assertEqual(settings["party_randomize_on_room_change"], bool(chaos))
                for name in ("color_randomization", "music_shuffle", "include_custom_music", "form_themes"):
                    self.assertEqual(options[name], getattr(self.world.options, name).value)
                    self.assertEqual(documents["slot_data.json"][name], options[name])


    def test_forced_goal_hints_are_not_editable(self) -> None:
        forced_hint_checks = {*PROOF_ITEMS, HEART_PIECE_ITEM_NAME}

        self.assertEqual(forced_hint_checks, set(GOAL_MANAGED_HINT_CHECKS))
        self.assertTrue(forced_hint_checks.isdisjoint(HINT_CHECK_KEYS))
        self.assertTrue(forced_hint_checks.isdisjoint(DEFAULT_HINT_CHECKS))

        legacy_option = ImportantHintChecks.from_any([
            *DEFAULT_HINT_CHECKS,
            *forced_hint_checks,
        ])
        with self.assertRaises(Exception):
            legacy_option.verify(KH3World, "KH3 Player", PlandoOptions.none)

        self.world_setup()
        config = build_randomizer_generation_config(self.world.options)
        self.assertTrue(PROOF_ITEMS.isdisjoint(config["hint"]["checks"]))
        self.assertNotIn(STYLE_CHANGE_UNLOCK_ITEM, config["hint"]["checks"])

    def test_slot_data_includes_placed_item_metadata_when_available(self) -> None:
        self.world_setup()

        target_location = self.world.active_locations[0]["name"]
        self.multiworld.get_location(target_location, self.player).item = self.world.create_item("Potion")

        slot_data = self.world.build_runtime_slot_data()
        bridge_entry = next(
            entry
            for entry in slot_data["bridge"]["locations"]
            if entry["ap_location_name"] == target_location
        )

        self.assertEqual(bridge_entry["assigned_item_name"], "Potion")
        self.assertEqual(bridge_entry["assigned_item_player"], self.player)
        self.assertEqual(bridge_entry["assigned_item_player_name"], self.multiworld.get_player_name(self.player))
        self.assertEqual(bridge_entry["assigned_item_game"], "Kingdom Hearts III")
        self.assertFalse(bridge_entry["is_foreign_item"])

    def test_slot_data_marks_local_munny_pouches_for_bridge_delivery(self) -> None:
        self.world_setup()

        target = next(
            location
            for location in self.world.active_locations
            if location.get("source_table") == "Event"
            and location.get("source_id") == "EVENT_KEYBLADE_006"
        )
        target_location = self.multiworld.get_location(target["name"], self.player)

        for item_name in (MUNNY_POUCH_ITEM_NAME, LARGE_MUNNY_POUCH_ITEM_NAME):
            with self.subTest(item_name=item_name):
                target_location.item = self.world.create_item(item_name)
                bridge_entry = next(
                    entry
                    for entry in self.world.build_runtime_slot_data()["bridge"]["locations"]
                    if entry["ap_location_name"] == target_location.name
                )

                self.assertEqual(bridge_entry["assigned_item_name"], item_name)
                self.assertTrue(bridge_entry["bridge_delivery_required"])

    def test_slot_data_marks_selected_completion_events_for_bridge_delivery(self) -> None:
        self.world_setup()

        source_ids = {
            "EVENT_008",
            "EVENT_009",
            "EVENT_CKGAME_001",
            "EVENT_HEARTBINDER_002",
            "EVENT_HEARTBINDER_003",
            "EVENT_HEARTBINDER_004",
            "EVENT_KEYBLADE_001",
            "EVENT_KEYBLADE_002",
            "EVENT_KEYBLADE_003",
            "EVENT_KEYBLADE_004",
            "EVENT_KEYBLADE_005",
            "EVENT_KEYBLADE_006",
            "EVENT_KEYBLADE_007",
            "EVENT_KEYBLADE_008",
            "EVENT_KEYBLADE_009",
            "EVENT_KEYBLADE_011",
            "EVENT_KEYITEM_002",
        }
        active_by_source_id = {
            location["source_id"]: location
            for location in self.world.active_locations
            if location.get("source_table") == "Event"
            and location.get("source_id") in source_ids
        }
        self.assertEqual(set(active_by_source_id), source_ids)

        for location in active_by_source_id.values():
            self.multiworld.get_location(location["name"], self.player).item = self.world.create_item("Potion")

        bridge_by_source_id = {
            entry["source_id"]: entry
            for entry in self.world.build_runtime_slot_data()["bridge"]["locations"]
            if entry.get("source_table") == "Event"
            and entry.get("source_id") in source_ids
        }
        self.assertEqual(set(bridge_by_source_id), source_ids)
        for source_id in sorted(source_ids):
            with self.subTest(source_id=source_id):
                self.assertEqual(bridge_by_source_id[source_id]["assigned_item_name"], "Potion")
                self.assertTrue(bridge_by_source_id[source_id]["bridge_delivery_required"])

    def test_generate_output_writes_room_visible_kh3_container(self) -> None:
        self.world_setup()

        active_location_names = {location["name"] for location in self.world.active_locations}
        for location in self.multiworld.get_locations(self.player):
            if location.name in active_location_names:
                location.item = self.world.create_item("Potion")

        with TemporaryDirectory() as temp_dir:
            self.world.generate_output(temp_dir)
            container_path = (
                Path(temp_dir)
                / f"{self.multiworld.get_out_file_name_base(self.player)}{KH3Container.patch_file_ending}"
            )

            self.assertTrue(container_path.is_file())
            with zipfile.ZipFile(container_path) as archive:
                manifest = json.loads(archive.read("archipelago.json"))
                payload = json.loads(archive.read(KH3_CONTAINER_PAYLOAD))

            self.assertEqual(manifest["game"], "Kingdom Hearts III")
            self.assertEqual(manifest["patch_file_ending"], KH3Container.patch_file_ending)
            self.assertEqual(payload["format"], "kh3_ap_seed")
            self.assertEqual(payload["slot"], self.player)
            self.assertEqual(payload["slot_name"], self.multiworld.get_player_name(self.player))
            self.assertEqual(payload["randomizer_config"], payload["slot_data"]["kh3_randomizer"])
            self.assertEqual(payload["slot_data"]["kh3_seed"]["format"], "kh3_server_seed")
            self.assertEqual(payload["slot_data"]["kh3_seed"]["format_version"], 1)
            self.assertEqual(payload["slot_data"]["kh3_seed"]["seed_name"], payload["seed_name"])
            self.assertEqual(payload["slot_data"]["kh3_seed"]["slot"], payload["slot"])
            self.assertEqual(payload["slot_data"]["kh3_seed"]["slot_name"], payload["slot_name"])
            self.assertEqual(payload["slot_data"]["kh3_seed"]["pak_seed"], payload["pak_seed"])
            self.assertTrue(decode_slot_data(payload["slot_data"])["bridge"]["locations"])
            self.assertTrue(
                all("assigned_item_id" in location for location in decode_slot_data(payload["slot_data"])["bridge"]["locations"])
            )
            self.assertEqual(load_kh3_container_data(container_path)["format"], "kh3_ap_seed")

    def test_installed_apworld_prefers_bundled_generation_resources(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            loose_randomizer_root = root / "loose-randomizer"
            loose_project = loose_randomizer_root / "wwwroot" / "DefaultKH3.json"
            loose_project.parent.mkdir(parents=True)
            loose_project.write_text("<Project />", encoding="utf-8")
            loose_extracted_data = root / "loose-extracted-locations.json"
            loose_extracted_data.write_text("[]", encoding="utf-8")
            work_dir = root / "work"

            with patch.object(
                PakGenerator,
                "get_apworld_archive_context",
                return_value=(root / "installed-kh3.apworld", "kh3"),
            ), patch.object(
                PakGenerator,
                "KH3_RANDOMIZER_ROOT",
                loose_randomizer_root,
            ), patch.object(
                PakGenerator,
                "KH3_EXTRACTED_DATA",
                loose_extracted_data,
            ), patch.object(
                PakGenerator,
                "copy_apworld_tree",
            ) as copy_tree, patch.object(
                PakGenerator,
                "copy_apworld_file",
                side_effect=lambda _source, destination: destination,
            ) as copy_file:
                randomizer_root = PakGenerator.prepare_randomizer_root(work_dir)
                extracted_data = PakGenerator.prepare_extracted_data(work_dir)

            self.assertEqual(randomizer_root, work_dir / "bundled-randomizer")
            self.assertEqual(extracted_data, work_dir / "bundled-data" / "extracted_locations.json")
            copy_tree.assert_called_once_with("randomizer", work_dir / "bundled-randomizer")
            copy_file.assert_called_once_with(
                "data/extracted_locations.json",
                work_dir / "bundled-data" / "extracted_locations.json",
            )

    def test_source_checkout_uses_writable_generation_copy(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            loose_randomizer_root = root / "loose-randomizer"
            loose_project = loose_randomizer_root / "wwwroot" / "DefaultKH3.json"
            loose_project.parent.mkdir(parents=True)
            loose_project.write_text("<Project />", encoding="utf-8")
            template_asset = loose_randomizer_root / "Content" / "template.uexp"
            template_asset.parent.mkdir()
            template_asset.write_bytes(b"template")
            template_asset.chmod(0o444)
            generated_build_file = loose_randomizer_root / "obj" / "stale.bin"
            generated_build_file.parent.mkdir()
            generated_build_file.write_bytes(b"stale")
            loose_extracted_data = root / "loose-extracted-locations.json"
            loose_extracted_data.write_text("[]", encoding="utf-8")
            work_dir = root / "work"

            with patch.object(PakGenerator, "get_apworld_archive_context", return_value=None), patch.object(
                PakGenerator,
                "KH3_RANDOMIZER_ROOT",
                loose_randomizer_root,
            ), patch.object(
                PakGenerator,
                "KH3_EXTRACTED_DATA",
                loose_extracted_data,
            ), patch.object(PakGenerator, "copy_apworld_tree") as copy_tree, patch.object(
                PakGenerator,
                "copy_apworld_file",
            ) as copy_file:
                randomizer_root = PakGenerator.prepare_randomizer_root(work_dir)
                extracted_data = PakGenerator.prepare_extracted_data(work_dir)

            self.assertEqual(randomizer_root, work_dir / "bundled-randomizer")
            copied_asset = randomizer_root / "Content" / "template.uexp"
            self.assertEqual(copied_asset.read_bytes(), b"template")
            self.assertTrue(os.access(copied_asset, os.W_OK))
            self.assertFalse((randomizer_root / "obj").exists())
            self.assertEqual(extracted_data, loose_extracted_data)
            copy_tree.assert_not_called()
            copy_file.assert_not_called()

    def test_source_template_copy_honors_packaging_exclusions_and_exceptions(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "randomizer"
            project = source / "wwwroot/DefaultKH3.json"
            project.parent.mkdir(parents=True)
            project.write_text("<Project />")
            (root / ".apignore").write_text(
                "/randomizer/Content/retired/*\n!randomizer/Content/retired/needed.uasset\n")
            for name in ("needed.uasset", "unused.uasset"):
                file = source / "Content/retired" / name
                file.parent.mkdir(parents=True, exist_ok=True)
                file.write_bytes(b"table")
            with patch.object(PakGenerator, "get_apworld_archive_context", return_value=None), \
                    patch.object(PakGenerator, "KH3_RANDOMIZER_ROOT", source):
                copied = PakGenerator.prepare_randomizer_root(root / "work")
            self.assertTrue((copied / "Content/retired/needed.uasset").is_file())
            self.assertFalse((copied / "Content/retired/unused.uasset").exists())

    def test_generated_seed_staging_requires_seed_tables_and_matching_backend_fingerprint(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            seed = "AP_123456789-1-Sora"
            staging_name = PakGenerator.kh3_pak_staging_directory_name(seed)
            staging = root / "wwwroot" / "pak" / "Seeds" / staging_name
            fingerprint = {
                "format": "kh3_randomizer_backend",
                "format_version": 2,
                "implementation": "python",
                "source_sha256": "a" * 64,
            }

            self.assertEqual(staging_name, "a4a3faeb992b")
            self.assertNotIn("s-", staging_name)

            for relative_path in PakGenerator.KH3_REQUIRED_SEED_STAGING_FILES:
                target = staging / relative_path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b"asset")
            fingerprint_path = staging / PakGenerator.KH3_RANDOMIZER_BACKEND_FINGERPRINT_RELATIVE_PATH
            metadata_path = staging / PakGenerator.KH3_AP_SEED_METADATA_RELATIVE_PATH
            fingerprint_path.write_text(json.dumps(fingerprint), encoding="utf-8")
            metadata_path.write_text(json.dumps({"randomizer_backend": fingerprint}), encoding="utf-8")

            PakGenerator.validate_generated_seed_staging(root, seed, "Sora")

            flags_uexp = staging / "KINGDOM HEARTS III/Content/Blueprints/GoA/Data/InitialWorldFlags_DT.uexp"
            flags_uexp.unlink()
            with self.assertRaisesRegex(PakGenerator.KH3PakGenerationError, "InitialWorldFlags_DT.uexp"):
                PakGenerator.validate_generated_seed_staging(root, seed, "Sora")

            flags_uexp.write_bytes(b"asset")
            metadata_path.write_text(
                json.dumps({"randomizer_backend": {**fingerprint, "source_sha256": "b" * 64}}),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(PakGenerator.KH3PakGenerationError, "does not match"):
                PakGenerator.validate_generated_seed_staging(root, seed, "Sora")

    def test_remote_kh3_item_placement_is_exported_for_secret_reports(self) -> None:
        remote_location_id = 91630179862862
        multidata = {
            "slot_info": {
                1: SimpleNamespace(name="RemotePlayer", game="Saving Princess"),
                2: SimpleNamespace(name="KH3-EZ5t", game="Kingdom Hearts III"),
            },
            "datapackage": {
                "Saving Princess": {
                    "location_name_to_id": {
                        "Cave: After Wallboss": remote_location_id,
                    },
                },
            },
            "locations": {
                1: {
                    remote_location_id: (1263020001, 2, 1),
                },
                2: {},
            },
        }

        placements = build_remote_hint_placements_from_multidata(
            multidata,
            2,
            {1263020001: "Proof of Fantasy"},
        )

        self.assertEqual(len(placements), 1)
        self.assertEqual(placements[0]["ap_location_id"], remote_location_id)
        self.assertEqual(placements[0]["item_name"], "Proof of Fantasy")
        self.assertEqual(placements[0]["location_game"], "Saving Princess")
        self.assertEqual(placements[0]["location_player_name"], "RemotePlayer")
        self.assertEqual(
            placements[0]["ap_location_name"],
            "Cave: After Wallboss",
        )
        self.assertTrue(placements[0]["is_remote_location"])

    def test_generate_kh3_pak_from_container_uses_player_prefixed_pakchunk9_name(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            output_dir = root / "out"
            container_path = root / "seed.kh3"
            payload = {
                "format": "kh3_ap_seed",
                "slot": 2,
                "slot_name": "KH3-EZ5",
                "pak_seed": "AP_123456789_P2_KH3-EZ5",
                "slot_data": self.world.fill_slot_data(),
                "randomizer_config": {},
            }
            with zipfile.ZipFile(container_path, "w") as archive:
                archive.writestr("archipelago.json", json.dumps({"game": "Kingdom Hearts III"}))
                archive.writestr(KH3_CONTAINER_PAYLOAD, json.dumps(payload))

            def fake_generate(_root, work_dir, seed, _config, _options, _metadata):
                match = re.fullmatch(r"(AP_\d+)_P(\d+)_(.+)", seed)
                self.assertIsNotNone(match)
                ap_seed_id, player_num, player_name = match.groups()
                (work_dir / f"pakchunk9-{player_name}-randomizer-{ap_seed_id}-{player_num}_99_P.pak").write_bytes(
                    b"x" * PakGenerator.KH3_MINIMUM_GENERATED_PAK_SIZE_BYTES
                )
                (work_dir / f"SpoilerLog-{seed}.txt").write_text("spoiler", encoding="utf-8")

            with patch("worlds.kh3.PakGenerator.prepare_randomizer_root", side_effect=lambda work_dir: work_dir), \
                    patch("worlds.kh3.PakGenerator.prepare_extracted_data", side_effect=lambda work_dir: work_dir), \
                    patch("worlds.kh3.PakGenerator.export_exact_randomized_options_from_container",
                          side_effect=lambda *_args: root / "options.json"), \
                    patch("worlds.kh3.PakGenerator.write_container_seed_metadata",
                          side_effect=lambda *_args: root / "metadata.json"), \
                    patch("worlds.kh3.PakGenerator.generate_python_seed", side_effect=fake_generate), \
                    patch("worlds.kh3.PakGenerator.validate_generated_seed_staging") as validate_staging:
                result = generate_kh3_pak_from_container(container_path, output_dir=output_dir)

            expected_pak = output_dir / "pakchunk9-KH3-EZ5-randomizer-AP_123456789-2_99_P.pak"
            expected_spoiler = output_dir / "SpoilerLog-AP_123456789_P2_KH3-EZ5.txt"
            self.assertEqual(result.pak_path, expected_pak.resolve())
            self.assertEqual(result.spoiler_log_path, expected_spoiler.resolve())
            self.assertTrue(expected_pak.is_file())
            self.assertTrue(expected_spoiler.is_file())
            self.assertFalse((output_dir / f"KH3-EZ5_{expected_pak.name}").exists())
            validate_staging.assert_called_once()

    def test_kh3_pak_glob_accepts_archipelago_player_file_seed_name(self) -> None:
        glob = PakGenerator.kh3_ap_pak_glob_for_seed("AP_123456789_P2_KH3-EZ5")

        self.assertEqual(glob, "pakchunk9-*-randomizer-AP_123456789-2_99_P.pak")

    def test_generate_kh3_paks_from_ap_zip_uses_player_prefixed_pakchunk9_name(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            output_dir = root / "out"
            ap_zip_path = root / "AP_987654321.zip"
            ap_zip_path.write_bytes(b"zip payload mocked")
            slot = {
                "slot": 2,
                "name": "PlayerKH",
                "slot_data": self.world.fill_slot_data(),
                "randomizer_config": {},
            }

            def fake_generate(_root, work_dir, seed, _config, _options, _metadata):
                ap_seed_id, player_num, player_name = seed.split("-", 2)
                (work_dir / f"pakchunk9-{player_name}-randomizer-{ap_seed_id}-{player_num}_99_P.pak").write_bytes(
                    b"x" * PakGenerator.KH3_MINIMUM_GENERATED_PAK_SIZE_BYTES
                )
                (work_dir / f"SpoilerLog-{seed}.txt").write_text("spoiler", encoding="utf-8")

            with patch("worlds.kh3.PakGenerator.prepare_randomizer_root", side_effect=lambda work_dir: work_dir), \
                    patch("worlds.kh3.PakGenerator.prepare_extracted_data", side_effect=lambda work_dir: work_dir), \
                    patch("worlds.kh3.PakGenerator.load_multidata_from_ap_zip", return_value={}), \
                    patch("worlds.kh3.PakGenerator.iter_kh3_slots", return_value=[slot]), \
                    patch("worlds.kh3.PakGenerator.export_exact_randomized_options",
                          side_effect=lambda *_args: root / "options.json"), \
                    patch("worlds.kh3.PakGenerator.write_archipelago_seed_metadata",
                          side_effect=lambda *_args: root / "metadata.json"), \
                    patch("worlds.kh3.PakGenerator.generate_python_seed", side_effect=fake_generate), \
                    patch("worlds.kh3.PakGenerator.validate_generated_seed_staging") as validate_staging:
                results = generate_kh3_paks_from_ap_zip(ap_zip_path, output_dir=output_dir)

            expected_pak = output_dir / "pakchunk9-PlayerKH-randomizer-AP_987654321-2_99_P.pak"
            expected_spoiler = output_dir / "SpoilerLog-AP_987654321-2-PlayerKH.txt"
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0].pak_path, expected_pak.resolve())
            self.assertEqual(results[0].spoiler_log_path, expected_spoiler.resolve())
            self.assertTrue(expected_pak.is_file())
            self.assertTrue(expected_spoiler.is_file())
            self.assertFalse((output_dir / f"PlayerKH_{expected_pak.name}").exists())
            validate_staging.assert_called_once()

    def test_junk_world_pool_excludes_progression_items(self) -> None:
        self.world_setup()

        location_data = next(
            location
            for location in self.world.active_locations
            if location["world"] == "100 Acre Wood"
        )
        location = self.multiworld.get_location(location_data["name"], self.player)

        self.assertTrue(location.filler_only)
        self.assertFalse(location.item_rule(self.world.create_item("Heart Piece")))
        self.assertFalse(location.item_rule(self.world.create_item("Ability: Thunder Boost")))
        self.assertTrue(location.item_rule(self.world.create_item("Potion")))

    def test_junk_reward_pool_excludes_progression_items(self) -> None:
        self.world_setup()

        location_data = next(
            location
            for location in self.world.active_locations
            if location["world"] == "Battlegate Rewards"
        )
        location = self.multiworld.get_location(location_data["name"], self.player)

        self.assertTrue(location.filler_only)
        self.assertFalse(location.item_rule(self.world.create_item("Magic: Fire")))
        self.assertFalse(location.item_rule(self.world.create_item("Ability: Thunder Boost")))
        self.assertTrue(location.item_rule(self.world.create_item("Potion")))

    def test_junk_golden_herc_rewards_rejects_progression_at_all_six_events(self) -> None:
        self.world_setup()

        golden_herc_locations = {
            location["source_id"]: self.multiworld.get_location(location["name"], self.player)
            for location in self.world.active_locations
            if location["source_table"] == "Event"
            and location["source_id"] in GOLDEN_HERC_REWARD_SOURCE_IDS
        }

        self.assertEqual(set(golden_herc_locations), GOLDEN_HERC_REWARD_SOURCE_IDS)
        for location in golden_herc_locations.values():
            self.assertFalse(location.item_rule(self.world.create_item("Heart Piece")))
            self.assertFalse(location.item_rule(self.world.create_item("Secret Report 1")))
            self.assertFalse(location.item_rule(self.world.create_item("Ability: Thunder Boost")))
            self.assertTrue(location.item_rule(self.world.create_item("Potion")))

    def test_disabled_junk_golden_herc_rewards_allows_progression_items(self) -> None:
        self.options = {"junk_golden_herc_rewards": 0}
        self.world_setup()

        for location_data in self.world.active_locations:
            if (
                location_data["source_table"] == "Event"
                and location_data["source_id"] in GOLDEN_HERC_REWARD_SOURCE_IDS
            ):
                location = self.multiworld.get_location(location_data["name"], self.player)
                self.assertTrue(location.item_rule(self.world.create_item("Potion")))

    def test_always_on_rewards_are_starting_inventory_not_locations(self) -> None:
        self.world_setup()

        always_on_source_locations = [
            location
            for location in normalize_locations(load_extracted_data())
            if location["world"] == "Always On"
        ]
        self.assertEqual(len(always_on_source_locations), 6)
        self.assertFalse(any(location["world"] == "Always On" for location in self.world.active_locations))
        self.assertFalse(any(location.name.startswith("Always On - ") for location in self.multiworld.get_locations(self.player)))
        precollected_item_names = [
            item.name for item in self.multiworld.precollected_items[self.player]
        ]
        self.assertEqual(len(precollected_item_names), len(always_on_source_locations) + 3)
        self.assertIn("Unlock: Airstep", precollected_item_names)
        self.assertIn(SPELLWEAVER_UNLOCK_ITEM, precollected_item_names)
        self.assertNotIn("Party Member: Donald", precollected_item_names)
        self.assertNotIn("Party Member: Goofy", precollected_item_names)
        self.assertEqual(
            len(self.multiworld.itempool) + len(self.multiworld.precollected_items[self.player]),
            len(self.multiworld.get_unfilled_locations(self.player)) + len(always_on_source_locations) + 3,
        )
        self.assertEqual(
            sorted(self.world.kh3_starting_inventory_items),
            sorted(item.name for item in self.multiworld.precollected_items[self.player]),
        )
        self.assertTrue(PROOF_ITEMS.isdisjoint(self.world.kh3_starting_inventory_items))
        self.assertTrue(PROOF_ITEMS.isdisjoint(
            item.name
            for item in self.multiworld.precollected_items[self.player]
        ))

    def test_all_native_keyblades_are_available_with_default_ultima_shards(self) -> None:
        self.options = {"keyblade_graveyard_pool": KH3LocationPoolMode.option_junk}
        self.world_setup()

        item_pool_names = [item.name for item in self.multiworld.itempool]
        precollected_item_names = [
            item.name
            for item in self.multiworld.precollected_items[self.player]
        ]
        available_item_names = set(item_pool_names) | set(precollected_item_names)
        randomized_keyblade_names = KEYBLADE_DISPLAY_NAMES.difference({"Ultima Weapon"})

        self.assertTrue(
            any(
                location["primary_item_name"] == "Starlight"
                for location in self.world.active_locations
            )
        )
        self.assertIn("Kingdom Key", item_pool_names)
        self.assertEqual(randomized_keyblade_names.difference(available_item_names), set())
        self.assertIn("Grand Chef", available_item_names)
        self.assertNotIn(ULTIMA_WEAPON_ITEM_NAME, available_item_names)
        self.assertEqual(item_pool_names.count(ULTIMA_WEAPON_SHARD_ITEM_NAME), 7)
        self.assertIn(ULTIMA_WEAPON_ITEM_NAME, self.world.item_name_to_id)
        self.assertIn(ULTIMA_WEAPON_SHARD_ITEM_NAME, self.world.item_name_to_id)
        self.assertGreater(
            self.world.item_name_to_id["Grand Chef"],
            self.world.item_name_to_id[ULTIMA_WEAPON_ITEM_NAME],
        )
        for item_name in CONTROLLER_UNLOCK_ITEM_NAMES:
            if item_name in {"Unlock: Airstep", "Unlock: Style Change", "Style Change: Spellweaver"}:
                self.assertNotIn(item_name, item_pool_names)
            else:
                self.assertIn(item_name, item_pool_names)
        self.assertNotIn("Kingdom Key", self.world.kh3_starting_inventory_items)
        starting_portal_keyblades = STARTING_PORTAL_KEYBLADE_ITEM_NAMES.intersection(
            self.world.kh3_starting_inventory_items
        )
        self.assertEqual(len(starting_portal_keyblades), 1)
        self.assertIn("Unlock: Airstep", self.world.kh3_starting_inventory_items)
        self.assertIn(SPELLWEAVER_UNLOCK_ITEM, self.world.kh3_starting_inventory_items)
        self.assertTrue(
            (set(CONTROLLER_UNLOCK_ITEM_NAMES) - {"Unlock: Airstep", SPELLWEAVER_UNLOCK_ITEM}).isdisjoint(
                self.world.kh3_starting_inventory_items
            )
        )
        self.assertEqual(
            len(self.multiworld.itempool),
            len(self.multiworld.get_unfilled_locations(self.player)),
        )

    def test_progressive_keyblade_level_has_ten_useful_copies_without_resizing_pool(self) -> None:
        self.world_setup()

        all_items = self.multiworld.itempool + self.multiworld.precollected_items[self.player]
        self.assertEqual(
            sum(item.name == PROGRESSIVE_KEYBLADE_LEVEL_ITEM_NAME for item in all_items),
            PROGRESSIVE_KEYBLADE_LEVEL_ITEM_COUNT,
        )
        self.assertTrue(
            set(PROGRESSIVE_KEYBLADE_LEVEL_INDIVIDUAL_ITEM_NAMES).isdisjoint(
                item.name for item in all_items
            )
        )
        self.assertEqual(
            classify_item(PROGRESSIVE_KEYBLADE_LEVEL_ITEM_NAME),
            ItemClassification.useful,
        )
        self.assertGreater(
            self.world.item_name_to_id[PROGRESSIVE_KEYBLADE_LEVEL_ITEM_NAME],
            self.world.item_name_to_id["Grand Chef"],
        )
        self.assertEqual(len(self.multiworld.itempool), len(self.multiworld.get_unfilled_locations(self.player)))

    def test_secondary_vbonus_rewards_are_readded_as_primary_pool_items(self) -> None:
        self.world_setup()

        available_item_counts = Counter(
            item.name
            for item in self.multiworld.itempool + self.multiworld.precollected_items[self.player]
        )
        expected_counts = {
            "Ability: Aerial Dive": 1,
            "Ability: Aerial Sweep": 1,
            "Ability: Falling Slash": 1,
            "Ability: Final Blow": 1,
            "Ability: Flash Step": 1,
            "Ability: Last Charge": 1,
            "Ability: MP Safety": 1,
            "Ability: Payback Strike": 1,
            "Ability: Quick Slash": 1,
            "Ability: Superjump": 1,
            "Ability: Superslide": 3,
            "Bonus: Accessory Slot +1": 2,
            "Bonus: HP +10": 15,
            "Bonus: MP +5": 7,
            "Magic: Water": 2,
        }

        self.assertEqual(
            set(expected_counts) | {HEART_PIECE_ITEM_NAME},
            SECONDARY_VBONUS_ITEM_POOL_REWARDS,
        )
        for item_name, expected_count in expected_counts.items():
            self.assertEqual(available_item_counts[item_name], expected_count, item_name)
        self.assertEqual(available_item_counts[HEART_PIECE_ITEM_NAME], 7)
        self.assertEqual(len(self.multiworld.itempool), len(self.multiworld.get_unfilled_locations(self.player)))

    def test_retired_level_path_abilities_are_standalone_ap_pool_items(self) -> None:
        self.world_setup()

        available_item_names = {
            item.name
            for item in self.multiworld.itempool + self.multiworld.precollected_items[self.player]
        }

        self.assertTrue(set(RETIRED_GROWTH_PATH_ABILITY_POOL_ITEMS).issubset(available_item_names))
        self.assertIn("Ability: Unison Fire", RETIRED_GROWTH_PATH_ABILITY_POOL_ITEMS)
        self.assertEqual(len(self.multiworld.itempool), len(self.multiworld.get_unfilled_locations(self.player)))

    def test_large_stat_bonuses_are_standalone_ap_pool_items(self) -> None:
        self.world_setup()

        pool_counts = Counter(item.name for item in self.multiworld.itempool)
        expected_tokens = {
            "Bonus: HP +15": "ETresVictoryBonusKind::HP_UP15",
            "Bonus: HP +30": "ETresVictoryBonusKind::HP_UP30",
            "Bonus: MP +10": "ETresVictoryBonusKind::MP_UP10",
            "Bonus: MP +20": "ETresVictoryBonusKind::MP_UP20",
        }
        for item_name, runtime_token in expected_tokens.items():
            with self.subTest(item_name=item_name):
                self.assertEqual(pool_counts[item_name], 1)
                self.assertEqual(classify_item(item_name), ItemClassification.useful)
                self.assertEqual(resolve_item_runtime_token(item_name), runtime_token)
        self.assertEqual(len(self.multiworld.itempool), len(self.multiworld.get_unfilled_locations(self.player)))

    def test_bridge_key_source_matches_active_location(self) -> None:
        self.world_setup()

        sample = next(location for location in self.world.active_locations if location["type"] == "treasure")
        self.assertEqual(make_bridge_key(sample), f"{sample['source_table']}:{sample['source_id']}")

    def test_raw_keyblade_ids_classify_as_useful(self) -> None:
        self.assertEqual(classify_item("WEP_KEYBLADE_SO_05"), ItemClassification.useful)
        self.assertEqual(classify_item("Grand Chef"), ItemClassification.useful)
        self.assertEqual(classify_item("Ultima Weapon"), ItemClassification.useful)

    def test_ability_and_controller_unlock_items_are_grouped_without_forcing_local(self) -> None:
        self.world_setup()

        self.assertIn("Ability: Thunder Boost", self.world.item_name_groups["Abilities"])
        self.assertIn("Unlock: Wall Run", self.world.item_name_groups["Controller Unlocks"])
        self.assertEqual(
            set(self.world.item_name_groups["Keyblade Upgrades"]),
            set(PROGRESSIVE_KEYBLADE_LEVEL_ITEM_NAMES),
        )
        self.assertTrue(ABILITY_ITEM_NAMES.isdisjoint(self.world.options.local_items.value))
        self.assertTrue(set(CONTROLLER_UNLOCK_ITEM_NAMES).isdisjoint(self.world.options.local_items.value))

    def test_abilities_and_controller_unlocks_respect_non_local_option(self) -> None:
        self.world_setup()

        options = self.multiworld.worlds[self.player].options
        options.local_items.value.clear()
        options.non_local_items.value.update({"Ability: Thunder Boost", "Unlock: Wall Run", "Potion"})

        self.world.generate_early()

        self.assertNotIn("Ability: Thunder Boost", options.local_items.value)
        self.assertNotIn("Unlock: Wall Run", options.local_items.value)
        self.assertIn("Ability: Thunder Boost", options.non_local_items.value)
        self.assertIn("Unlock: Wall Run", options.non_local_items.value)
        self.assertIn("Potion", options.non_local_items.value)

    def test_runtime_token_resolution_handles_friendly_and_enum_items(self) -> None:
        self.assertEqual(resolve_item_runtime_token("Happy Gear"), "WEP_KEYBLADE_SO_05")
        self.assertEqual(resolve_item_runtime_token("Grand Chef"), "WEP_KEYBLADE_SO_011")
        self.assertEqual(resolve_item_runtime_token("Classic Tone"), "WEP_KEYBLADE_SO_012")
        self.assertEqual(resolve_item_runtime_token("Ultima Weapon"), "WEP_KEYBLADE_SO_015")
        self.assertEqual(EXPORT_RAW_ITEM_DISPLAY_NAMES["WEP_KEYBLADE_SO_011"], "Grand Chef")
        self.assertEqual(EXPORT_RAW_ITEM_DISPLAY_NAMES["WEP_KEYBLADE_SO_012"], "Classic Tone")
        self.assertEqual(resolve_item_runtime_token("Magic: Cure"), "ETresVictoryBonusKind::MELEM_CURE")
        self.assertEqual(resolve_item_runtime_token("'Ohana Heartbinder"), "KEY_ITEM08")
        self.assertEqual(resolve_item_display_name("\\'Ohana Heartbinder"), "'Ohana Heartbinder")
        self.assertEqual(resolve_item_runtime_token("\\'Ohana Heartbinder"), "KEY_ITEM08")
        self.assertEqual(resolve_item_display_name("ETresVictoryBonusKind::MP_UP20"), "Bonus: MP +20")
        self.assertEqual(resolve_item_runtime_token("Bonus: MP +20"), "ETresVictoryBonusKind::MP_UP20")
        raw_magic_aliases = {
            "MAGIC_AERO": ("Magic: Aero", "ETresVictoryBonusKind::MELEM_AERO"),
            "MAGIC_BLIZZARD": ("Magic: Blizzard", "ETresVictoryBonusKind::MELEM_BLIZZARD"),
            "MAGIC_CURE": ("Magic: Cure", "ETresVictoryBonusKind::MELEM_CURE"),
            "MAGIC_FIRE": ("Magic: Fire", "ETresVictoryBonusKind::MELEM_FIRE"),
            "MAGIC_THUNDER": ("Magic: Thunder", "ETresVictoryBonusKind::MELEM_THUNDER"),
            "MAGIC_WATER": ("Magic: Water", "ETresVictoryBonusKind::MELEM_WATER"),
        }
        for raw_name, (display_name, runtime_token) in raw_magic_aliases.items():
            with self.subTest(raw_name=raw_name):
                self.assertEqual(resolve_item_display_name(raw_name), display_name)
                self.assertEqual(resolve_item_runtime_token(raw_name), runtime_token)
        self.assertEqual(resolve_item_runtime_token("Proof of Promises"), "KEY_ITEM15")
        self.assertEqual(resolve_item_runtime_flag_name("Magic: Cure"), "GIVESORA_MELEM_CURE_01")
        self.assertEqual(
            resolve_vbonus_source_id_for_tokens([
                "ETresVictoryBonusKind::MELEM_CURE",
                "ETresVictoryBonusKind::HP_UP10",
            ]),
            "Vbonus_040",
        )






    def test_progressive_keyblade_groups_match_native_weapon_rows(self) -> None:
        first_row_by_keyblade = dict(KEYBLADE_ENHANCEMENT_GROUPS)

        self.assertEqual(first_row_by_keyblade["Shooting Star"], 20)
        self.assertEqual(first_row_by_keyblade["Hero's Origin"], 10)
        self.assertEqual(first_row_by_keyblade["Happy Gear"], 50)
        self.assertEqual(first_row_by_keyblade["Wheel of Fate"], 90)

    def test_extracted_display_names_unescape_csharp_apostrophes(self) -> None:
        extracted_data = load_extracted_data()
        item_names = {item["name"] for item in extracted_data["items"]}
        location_vanilla_names = {
            location.get("vanilla_item_name")
            for location in extracted_data["locations"]
            if location.get("vanilla_item_name")
        }

        self.assertIn("'Ohana Heartbinder", item_names)
        self.assertIn("CK: Fishin' Frenzy", item_names)
        self.assertIn("'Ohana Heartbinder", location_vanilla_names)
        self.assertIn("CK: Fishin' Frenzy", location_vanilla_names)
        self.assertNotIn("\\'Ohana Heartbinder", item_names)
        self.assertNotIn("CK: Fishin\\' Frenzy", item_names)

    def test_runtime_grant_method_resolution_matches_token_family(self) -> None:
        self.assertEqual(classify_runtime_grant_method("ITEM_POTION"), "bp_tres_get_item")
        self.assertFalse(is_keyblade_item_name("Magic: Cure"))
        self.assertFalse(is_keyblade_item_name("Bonus: MP +20"))
        self.assertTrue(is_keyblade_item_name("Crystal Snow"))
        self.assertEqual(
            classify_runtime_grant_method("ETresVictoryBonusKind::MELEM_FIRE"),
            DURABLE_SORA_VBONUS_GRANT_METHOD,
        )
        self.assertEqual(
            classify_runtime_grant_method("ETresAbilityKind::REVENGEIMPACT"),
            DURABLE_SORA_ABILITY_GRANT_METHOD,
        )
        self.assertEqual(
            classify_runtime_grant_method("ETresVictoryBonusKind::ITEM_SLOT_UP1"),
            DURABLE_SORA_VBONUS_GRANT_METHOD,
        )
        self.assertEqual(
            classify_runtime_grant_method("ETresVictoryBonusKind::HP_UP10"),
            DURABLE_SORA_VBONUS_GRANT_METHOD,
        )
        self.assertEqual(
            classify_runtime_grant_method("ETresVictoryBonusKind::MP_UP5"),
            DURABLE_SORA_VBONUS_GRANT_METHOD,
        )
        self.assertEqual(
            classify_runtime_grant_method("ETresVictoryBonusKind::MP_UP20"),
            DURABLE_SORA_VBONUS_GRANT_METHOD,
        )
        self.assertIsNone(classify_runtime_grant_method("ETresVictoryBonusKind::NONE"))

    def test_protocol_payload_validation_rejects_wrong_version(self) -> None:
        with self.assertRaises(BridgeProtocolError):
            validate_protocol_payload(
                {
                    "protocol_name": BRIDGE_PROTOCOL_NAME,
                    "protocol_version": BRIDGE_PROTOCOL_VERSION + 1,
                },
                "test payload",
            )

    def test_bridge_world_version_matches_manifest(self) -> None:
        manifest = json.loads((Path(__file__).resolve().parents[1] / "archipelago.json").read_text(encoding="utf-8"))
        self.assertEqual(KH3_WORLD_VERSION, manifest["world_version"])

    def test_slot_data_payload_validation_accepts_generated_slot_data(self) -> None:
        self.world_setup()
        slot_data = self.world.build_runtime_slot_data()
        validated = validate_slot_data_payload(slot_data, "generated slot_data")
        self.assertIs(validated, slot_data)

    def test_generated_world_excludes_disabled_vbonus_004_but_reserves_its_id(self) -> None:
        self.world_setup()

        active_sources = {
            (location["source_table"], location["source_id"])
            for location in self.world.active_locations
        }
        self.assertNotIn(("VBonus", "Vbonus_004"), active_sources)

        slot_data = self.world.build_runtime_slot_data()
        bridge_sources = {
            (location["source_table"], location["source_id"])
            for location in slot_data["bridge"]["locations"]
        }
        self.assertNotIn(("VBonus", "Vbonus_004"), bridge_sources)
        self.assertEqual(location_name_to_id["Olympus - Victory Bonus 004"], 1263023176)

    def test_vbonus_explicit_suppress_reward_leaves_row_empty(self) -> None:
        default_options = {
            "VBonus": {
                "Vbonus_064": {
                    "Sora_Bonus1": "ETresVictoryBonusKind::NONE\0",
                    "Sora_Ability1": "ETresAbilityKind::NONE\0",
                    "Sora_Bonus2": "ETresVictoryBonusKind::NONE\0",
                    "Sora_Ability2": "ETresAbilityKind::NONE\0",
                }
            }
        }
        bridge_location = {"source_id": "Vbonus_064"}
        extracted_location = {
            "source_id": "Vbonus_064",
            "vanilla_items": {
                "Sora_Bonus1": "ETresVictoryBonusKind::MELEM_WATER",
                "Sora_Ability1": "ETresAbilityKind::NONE",
                "Sora_Bonus2": "ETresVictoryBonusKind::NONE",
                "Sora_Ability2": "ETresAbilityKind::NONE",
            },
            "vanilla_item_names": {
                "Sora_Bonus1": "Magic: Water",
                "Sora_Ability1": "ETresAbilityKind::NONE",
                "Sora_Bonus2": "ETresVictoryBonusKind::NONE",
                "Sora_Ability2": "ETresAbilityKind::NONE",
            },
        }

        apply_vbonus_override(
            default_options,
            bridge_location,
            extracted_location,
            "ITEM_MITHRILGEM",
            suppress_reward=True,
        )

        self.assertEqual(default_options["VBonus"]["Vbonus_064"], {
            "Sora_Bonus1": "ETresVictoryBonusKind::NONE\0",
            "Sora_Ability1": "ETresAbilityKind::NONE\0",
            "Sora_Bonus2": "ETresVictoryBonusKind::NONE\0",
            "Sora_Ability2": "ETresAbilityKind::NONE\0",
        })

    def test_vbonus_override_suppresses_non_primary_vanilla_rewards(self) -> None:
        default_options = {"VBonus": {}}
        bridge_location = {"source_id": "Vbonus_082"}
        extracted_location = {
            "source_id": "Vbonus_082",
            "vanilla_items": {
                "Sora_Ability1": "ETresAbilityKind::NONE",
                "Sora_Ability2": "ETresAbilityKind::NONE",
                "Sora_Bonus1": "ETresVictoryBonusKind::MELEM_FIRE",
                "Sora_Bonus2": "ETresVictoryBonusKind::MELEM_WATER",
            },
            "vanilla_item_names": {
                "Sora_Ability1": "ETresAbilityKind::NONE",
                "Sora_Ability2": "ETresAbilityKind::NONE",
                "Sora_Bonus1": "Magic: Fire",
                "Sora_Bonus2": "Magic: Water",
            },
        }

        apply_vbonus_override(default_options, bridge_location, extracted_location, "ITEM_ELIXIR")

        self.assertEqual(default_options["VBonus"]["Vbonus_082"], {
            "Sora_Ability1": "ETresAbilityKind::NONE\0",
            "Sora_Ability2": "ETresAbilityKind::NONE\0",
            "Sora_Bonus1": "ITEM_ELIXIR\0",
            "Sora_Bonus2": "ETresVictoryBonusKind::NONE\0",
        })

    def test_vbonus_natural_all_none_row_still_accepts_randomized_reward(self) -> None:
        default_options = {
            "VBonus": {
                "Vbonus_999": {
                    "Sora_Bonus1": "ETresVictoryBonusKind::NONE\0",
                    "Sora_Ability1": "ETresAbilityKind::NONE\0",
                    "Sora_Bonus2": "ETresVictoryBonusKind::NONE\0",
                    "Sora_Ability2": "ETresAbilityKind::NONE\0",
                }
            }
        }
        bridge_location = {"source_id": "Vbonus_999"}
        extracted_location = {
            "source_id": "Vbonus_999",
            "vanilla_items": {
                "Sora_Bonus1": "ETresVictoryBonusKind::NONE",
                "Sora_Ability1": "ETresAbilityKind::NONE",
                "Sora_Bonus2": "ETresVictoryBonusKind::NONE",
                "Sora_Ability2": "ETresAbilityKind::NONE",
            },
            "vanilla_item_names": {
                "Sora_Bonus1": "ETresVictoryBonusKind::NONE",
                "Sora_Ability1": "ETresAbilityKind::NONE",
                "Sora_Bonus2": "ETresVictoryBonusKind::NONE",
                "Sora_Ability2": "ETresAbilityKind::NONE",
            },
        }

        apply_vbonus_override(default_options, bridge_location, extracted_location, "ETresVictoryBonusKind::HP_UP5")

        self.assertEqual(
            default_options["VBonus"]["Vbonus_999"]["Sora_Bonus1"],
            "ETresVictoryBonusKind::HP_UP5\0",
        )

    def test_default_base_abilities_false_suppresses_chrinit_base_abilities(self) -> None:
        default_options = {
            "ChrInit": {
                "m_PlayerSora": {
                    field_name: f"{runtime_token}\0"
                    for field_name, runtime_token, _item_name in DEFAULT_BASE_ABILITY_CHRINIT_FIELDS
                } | {"HaveAbility": "ETresAbilityKind::EXPZERO\0"}
            }
        }

        suppressed = suppress_default_base_chrinit_abilities(
            default_options,
            {"exceptions": {"Default Base Abilities": False}},
        )

        self.assertEqual(len(suppressed), len(DEFAULT_BASE_ABILITY_CHRINIT_FIELDS))
        for field_name, runtime_token, item_name in DEFAULT_BASE_ABILITY_CHRINIT_FIELDS:
            with self.subTest(field_name=field_name):
                self.assertEqual(
                    default_options["ChrInit"]["m_PlayerSora"][field_name],
                    "ETresAbilityKind::NONE\0",
                )
                self.assertIn({
                    "target": f"ChrInit.m_PlayerSora.{field_name}",
                    "field_name": field_name,
                    "item_name": item_name,
                    "previous_token": f"{runtime_token}\0",
                    "previous_runtime_token": runtime_token,
                    "runtime_token": "ETresAbilityKind::NONE",
                }, suppressed)
        self.assertEqual(
            default_options["ChrInit"]["m_PlayerSora"]["HaveAbility"],
            "ETresAbilityKind::EXPZERO\0",
        )

    def test_zero_exp_is_enforced_in_sora_chrinit(self) -> None:
        default_options = {
            "ChrInit": {
                "m_PlayerSora": {
                    "HaveAbility": "ETresAbilityKind::THUNDER_UP\0",
                }
            }
        }

        enforced = enforce_fixed_sora_chrinit_abilities(default_options)

        self.assertEqual(
            default_options["ChrInit"]["m_PlayerSora"]["HaveAbility"],
            "ETresAbilityKind::EXPZERO\0",
        )
        self.assertEqual(enforced, [{
            "target": "ChrInit.m_PlayerSora.HaveAbility",
            "field_name": "HaveAbility",
            "item_name": "Ability: Zero EXP",
            "previous_token": "ETresAbilityKind::THUNDER_UP\0",
            "runtime_token": "ETresAbilityKind::EXPZERO",
        }])
        self.assertEqual(
            FIXED_SORA_CHRINIT_ABILITY_FIELDS,
            (("HaveAbility", "ETresAbilityKind::EXPZERO", "Ability: Zero EXP"),),
        )

    def test_exact_export_keeps_zero_exp_in_chrinit_when_default_base_abilities_are_off(self) -> None:
        result = export_randomized_options_from_bridge_data(
            default_kh3=PakGenerator.KH3_RANDOMIZER_ROOT / "wwwroot" / "DefaultKH3.json",
            default_kh3_overrides=(
                PakGenerator.KH3_RANDOMIZER_ROOT / "wwwroot" / "DefaultKH3.ArchipelagoOverrides.json"
            ),
            extracted_data_path=PakGenerator.KH3_EXTRACTED_DATA,
            bridge_locations=[],
            slot=1,
            slot_name="Sora",
            archive_name="AP_test.zip",
            randomizer_config={"exceptions": {"Default Base Abilities": False}},
        )

        self.assertEqual(
            result.randomized_options["ChrInit"]["m_PlayerSora"]["HaveAbility"],
            "ETresAbilityKind::EXPZERO\0",
        )
        suppressed_item_names = {
            entry["item_name"]
            for entry in result.placement_summary["suppressed_default_base_chrinit_abilities"]
        }
        self.assertNotIn("Ability: Zero EXP", suppressed_item_names)
        self.assertEqual(
            result.placement_summary["fixed_sora_chrinit_abilities"][0]["item_name"],
            "Ability: Zero EXP",
        )

    def test_numeric_stat_rows_are_not_items_or_runtime_tokens(self) -> None:
        item_ids = build_item_name_to_id(load_extracted_data())
        self.assertNotIn("114", item_ids)
        self.assertEqual(item_ids["Hi-Potion"], 1263022430)
        self.assertIsNone(resolve_item_runtime_token("114"))

        with self.assertRaisesRegex(RuntimeError, "114"):
            export_randomized_options_from_bridge_data(
                default_kh3=PakGenerator.KH3_RANDOMIZER_ROOT / "wwwroot" / "DefaultKH3.json",
                default_kh3_overrides=(
                    PakGenerator.KH3_RANDOMIZER_ROOT / "wwwroot" / "DefaultKH3.ArchipelagoOverrides.json"
                ),
                extracted_data_path=PakGenerator.KH3_EXTRACTED_DATA,
                bridge_locations=[{
                    "ap_location_id": 1263022902,
                    "ap_location_name": "Toy Box - Invalid Numeric Stat Chest",
                    "assigned_item_game": "Kingdom Hearts III",
                    "assigned_item_id": 1263022115,
                    "assigned_item_name": "114",
                    "assigned_item_player": 1,
                    "assigned_item_player_name": "Sora",
                    "bridge_key": "TreasureTS:TS_SBOX_007",
                    "source_id": "TS_SBOX_007",
                    "source_table": "TreasureTS",
                    "type": "treasure",
                    "world": "Toy Box",
                }],
                slot=1,
                slot_name="Sora",
                archive_name="AP_invalid_numeric_item.zip",
            )

    def test_default_base_abilities_true_preserves_chrinit_base_abilities(self) -> None:
        default_options = {
            "ChrInit": {
                "m_PlayerSora": {
                    "EquipAbility11": "ETresAbilityKind::POLE_SPIN\0",
                }
            }
        }

        suppressed = suppress_default_base_chrinit_abilities(
            default_options,
            {"exceptions": {"Default Base Abilities": True}},
        )

        self.assertEqual(suppressed, [])
        self.assertEqual(
            default_options["ChrInit"]["m_PlayerSora"]["EquipAbility11"],
            "ETresAbilityKind::POLE_SPIN\0",
        )

    def test_export_routes_local_vbonus_reward_through_bridge_with_typed_none_fields(self) -> None:
        with TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            default_path = temp_path / "DefaultKH3.json"
            override_path = temp_path / "DefaultKH3.ArchipelagoOverrides.json"
            extracted_path = temp_path / "extracted_locations.json"
            event_table = {
                event_id: {"RandomizedItem": "ITEM_POTION\0"}
                for event_id in ALWAYS_ON_EVENT_IDS
            }
            default_path.write_text(json.dumps({
                "Event": event_table,
                "VBonus": {
                    "Vbonus_TEST": {
                        "Sora_Bonus1": "ETresVictoryBonusKind::HP_UP10\0",
                        "Sora_Ability1": "ETresAbilityKind::QUICK_SHAFT\0",
                        "Sora_Bonus2": "ETresVictoryBonusKind::NONE\0",
                        "Sora_Ability2": "ETresAbilityKind::FLASH_STEP\0",
                    },
                },
            }), encoding="utf-8")
            override_path.write_text(json.dumps({
                "VBonus": {
                    "Vbonus_TEST": {
                        "Sora_Bonus1": "ETresVictoryBonusKind::NONE\0",
                        "Sora_Ability1": "ETresAbilityKind::NONE\0",
                        "Sora_Bonus2": "ETresVictoryBonusKind::NONE\0",
                        "Sora_Ability2": "ETresAbilityKind::NONE\0",
                    },
                },
            }), encoding="utf-8")
            extracted_path.write_text(json.dumps({
                "items": [
                    {"name": "Ability: Glide", "id": "ETresAbilityKind::GLIDE"},
                ],
                "locations": [
                    {
                        "source_table": "VBonus",
                        "source_id": "Vbonus_TEST",
                        "vanilla_item_names": {
                            "Sora_Ability1": "Ability: Groundbreaker",
                            "Sora_Ability2": "ETresAbilityKind::NONE",
                            "Sora_Bonus1": "Heart Piece",
                            "Sora_Bonus2": "ETresVictoryBonusKind::NONE",
                        },
                        "vanilla_items": {
                            "Sora_Ability1": "ETresAbilityKind::AIR_ROLL_BEAT",
                            "Sora_Ability2": "ETresAbilityKind::NONE",
                            "Sora_Bonus1": "KEY_ITEM17",
                            "Sora_Bonus2": "ETresVictoryBonusKind::NONE",
                        },
                    },
                ],
            }), encoding="utf-8")
            bridge_locations = [
                {
                    "ap_location_id": 1263023177,
                    "ap_location_name": "Synthetic Victory Bonus",
                    "assigned_item_game": "Kingdom Hearts III",
                    "assigned_item_name": "Ability: Glide",
                    "assigned_item_player": 1,
                    "assigned_item_player_name": "KH3",
                    "bridge_key": "VBonus:Vbonus_TEST",
                    "location_description": "Defeat Synthetic Boss in Test World - Test Arena",
                    "source_id": "Vbonus_TEST",
                    "source_table": "VBonus",
                    "type": "vbonus",
                    "world": "Test World",
                },
            ]

            result = export_randomized_options_from_bridge_data(
                default_kh3=default_path,
                default_kh3_overrides=override_path,
                extracted_data_path=extracted_path,
                bridge_locations=bridge_locations,
                slot=1,
                slot_name="KH3",
                archive_name="AP_test.zip",
            )

            self.assertEqual(result.randomized_options["VBonus"]["Vbonus_TEST"], {
                "Sora_Bonus1": "ETresVictoryBonusKind::NONE\0",
                "Sora_Ability1": "ETresAbilityKind::NONE\0",
                "Sora_Bonus2": "ETresVictoryBonusKind::NONE\0",
                "Sora_Ability2": "ETresAbilityKind::NONE\0",
            })
            placement = result.placement_summary["locations"][0]
            self.assertEqual(placement["export_token"], "ETresVictoryBonusKind::NONE")
            self.assertEqual(placement["native_reward_token"], "ETresVictoryBonusKind::NONE")
            self.assertEqual(placement["runtime_token"], "ETresAbilityKind::GLIDE")
            self.assertEqual(placement["world"], "Test World")
            self.assertEqual(
                placement["location_description"],
                "Defeat Synthetic Boss in Test World - Test Arena",
            )
            self.assertTrue(placement["bridge_delivery_required"])
            self.assertTrue(bridge_locations[0]["bridge_delivery_required"])
            self.assertFalse(placement["is_foreign_item"])

    def test_export_maps_local_controller_unlock_to_bridge_delivered_reward(self) -> None:
        with TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            default_path = temp_path / "DefaultKH3.json"
            extracted_path = temp_path / "extracted_locations.json"
            event_table = {
                event_id: {"RandomizedItem": "ITEM_POTION\0"}
                for event_id in ALWAYS_ON_EVENT_IDS
            }
            default_path.write_text(json.dumps({
                "Event": event_table,
                "TreasureTT": {
                    "TT_TEST_001": {"Treasure": "ITEM_POTION\0"},
                },
            }), encoding="utf-8")
            extracted_path.write_text(json.dumps({
                "items": [],
                "locations": [],
            }), encoding="utf-8")
            bridge_locations = [
                {
                    "ap_location_id": 1263999001,
                    "ap_location_name": "Twilight Town - Test Chest",
                    "assigned_item_game": "Kingdom Hearts III",
                    "assigned_item_name": "Unlock: Magic",
                    "assigned_item_player": 1,
                    "assigned_item_player_name": "KH3",
                    "bridge_key": "TreasureTT:TT_TEST_001",
                    "source_id": "TT_TEST_001",
                    "source_table": "TreasureTT",
                    "type": "treasure",
                },
            ]

            result = export_randomized_options_from_bridge_data(
                default_kh3=default_path,
                default_kh3_overrides=None,
                extracted_data_path=extracted_path,
                bridge_locations=bridge_locations,
                slot=1,
                slot_name="KH3",
                archive_name="AP_test.zip",
            )

            self.assertEqual(
                result.randomized_options["TreasureTT"]["TT_TEST_001"]["Treasure"],
                "AP_ITEM_1263999001\0",
            )
            placement = result.placement_summary["locations"][0]
            self.assertEqual(placement["item_name"], "Unlock: Magic")
            self.assertEqual(placement["item_display_name"], "Unlock: Magic")
            self.assertEqual(placement["export_token"], "AP_ITEM_1263999001")
            self.assertEqual(placement["native_reward_token"], "AP_ITEM_1263999001")
            self.assertEqual(placement["runtime_token"], "KH3AP_CONTROLLER_UNLOCK::magic")
            self.assertTrue(placement["bridge_delivery_required"])

    def test_export_maps_local_munny_pouch_to_bridge_delivered_reward(self) -> None:
        with TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            default_path = temp_path / "DefaultKH3.json"
            extracted_path = temp_path / "extracted_locations.json"
            default_path.write_text(json.dumps({
                "Event": {
                    event_id: {"RandomizedItem": "ITEM_POTION\0"}
                    for event_id in ALWAYS_ON_EVENT_IDS
                },
                "TreasureTT": {
                    "TT_TEST_001": {"Treasure": "ITEM_POTION\0"},
                },
            }), encoding="utf-8")
            extracted_data = {"items": [], "locations": []}
            extracted_path.write_text(json.dumps(extracted_data), encoding="utf-8")
            bridge_locations = [{
                "ap_location_id": 1263999004,
                "ap_location_name": "Twilight Town - Munny Pouch Test Chest",
                "assigned_item_game": "Kingdom Hearts III",
                "assigned_item_name": MUNNY_POUCH_ITEM_NAME,
                "assigned_item_player": 1,
                "assigned_item_player_name": "KH3",
                "bridge_key": "TreasureTT:TT_TEST_001",
                "source_id": "TT_TEST_001",
                "source_table": "TreasureTT",
                "type": "treasure",
            }]

            self.assertEqual(
                build_item_name_to_token(extracted_data)[MUNNY_POUCH_ITEM_NAME],
                MUNNY_POUCH_RUNTIME_TOKEN,
            )
            self.assertEqual(
                build_item_name_to_token(extracted_data)[LARGE_MUNNY_POUCH_ITEM_NAME],
                LARGE_MUNNY_POUCH_RUNTIME_TOKEN,
            )
            result = export_randomized_options_from_bridge_data(
                default_kh3=default_path,
                default_kh3_overrides=None,
                extracted_data_path=extracted_path,
                bridge_locations=bridge_locations,
                slot=1,
                slot_name="KH3",
                archive_name="AP_test.zip",
            )

            self.assertEqual(
                result.randomized_options["TreasureTT"]["TT_TEST_001"]["Treasure"],
                "AP_ITEM_1263999004\0",
            )
            placement = result.placement_summary["locations"][0]
            self.assertEqual(placement["item_name"], MUNNY_POUCH_ITEM_NAME)
            self.assertEqual(placement["runtime_token"], MUNNY_POUCH_RUNTIME_TOKEN)
            self.assertTrue(placement["bridge_delivery_required"])

    def test_export_maps_local_ultima_weapon_shard_to_bridge_delivered_reward(self) -> None:
        with TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            default_path = temp_path / "DefaultKH3.json"
            extracted_path = temp_path / "extracted_locations.json"
            default_path.write_text(json.dumps({
                "Event": {
                    event_id: {"RandomizedItem": "ITEM_POTION\0"}
                    for event_id in ALWAYS_ON_EVENT_IDS
                },
                "TreasureTT": {
                    "TT_TEST_001": {"Treasure": "ITEM_POTION\0"},
                },
            }), encoding="utf-8")
            extracted_path.write_text(json.dumps({"items": [], "locations": []}), encoding="utf-8")
            bridge_locations = [{
                "ap_location_id": 1263999005,
                "ap_location_name": "Twilight Town - Ultima Weapon Shard Test Chest",
                "assigned_item_game": "Kingdom Hearts III",
                "assigned_item_name": ULTIMA_WEAPON_SHARD_ITEM_NAME,
                "assigned_item_player": 1,
                "assigned_item_player_name": "KH3",
                "bridge_key": "TreasureTT:TT_TEST_001",
                "source_id": "TT_TEST_001",
                "source_table": "TreasureTT",
                "type": "treasure",
            }]

            result = export_randomized_options_from_bridge_data(
                default_kh3=default_path,
                default_kh3_overrides=None,
                extracted_data_path=extracted_path,
                bridge_locations=bridge_locations,
                slot=1,
                slot_name="KH3",
                archive_name="AP_test.zip",
            )

            self.assertEqual(
                result.randomized_options["TreasureTT"]["TT_TEST_001"]["Treasure"],
                "AP_ITEM_1263999005\0",
            )
            placement = result.placement_summary["locations"][0]
            self.assertEqual(placement["item_name"], ULTIMA_WEAPON_SHARD_ITEM_NAME)
            self.assertEqual(placement["runtime_token"], ULTIMA_WEAPON_SHARD_RUNTIME_TOKEN)
            self.assertTrue(placement["bridge_delivery_required"])

    def test_export_maps_local_progressive_keyblade_level_to_bridge_delivered_reward(self) -> None:
        with TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            default_path = temp_path / "DefaultKH3.json"
            extracted_path = temp_path / "extracted_locations.json"
            default_path.write_text(json.dumps({
                "Event": {
                    event_id: {"RandomizedItem": "ITEM_POTION\0"}
                    for event_id in ALWAYS_ON_EVENT_IDS
                },
                "TreasureTT": {
                    "TT_TEST_001": {"Treasure": "ITEM_POTION\0"},
                },
            }), encoding="utf-8")
            extracted_path.write_text(json.dumps({"items": [], "locations": []}), encoding="utf-8")
            cases = (
                (
                    1263999002,
                    PROGRESSIVE_KEYBLADE_LEVEL_ITEM_NAME,
                    PROGRESSIVE_KEYBLADE_LEVEL_RUNTIME_TOKEN,
                ),
                (
                    1263999003,
                    "Progressive Shooting Star Level",
                    f"{PROGRESSIVE_KEYBLADE_LEVEL_RUNTIME_TOKEN}::KEYBLADE::20",
                ),
            )
            for ap_location_id, item_name, expected_runtime_token in cases:
                with self.subTest(item_name=item_name):
                    bridge_locations = [
                        {
                            "ap_location_id": ap_location_id,
                            "ap_location_name": "Twilight Town - Test Chest",
                            "assigned_item_game": "Kingdom Hearts III",
                            "assigned_item_name": item_name,
                            "assigned_item_player": 1,
                            "assigned_item_player_name": "KH3",
                            "bridge_key": "TreasureTT:TT_TEST_001",
                            "source_id": "TT_TEST_001",
                            "source_table": "TreasureTT",
                            "type": "treasure",
                        },
                    ]

                    result = export_randomized_options_from_bridge_data(
                        default_kh3=default_path,
                        default_kh3_overrides=None,
                        extracted_data_path=extracted_path,
                        bridge_locations=bridge_locations,
                        slot=1,
                        slot_name="KH3",
                        archive_name="AP_test.zip",
                    )

                    self.assertEqual(
                        result.randomized_options["TreasureTT"]["TT_TEST_001"]["Treasure"],
                        f"AP_ITEM_{ap_location_id}\0",
                    )
                    placement = result.placement_summary["locations"][0]
                    self.assertEqual(placement["item_name"], item_name)
                    self.assertEqual(placement["runtime_token"], expected_runtime_token)
                    self.assertTrue(placement["bridge_delivery_required"])

    def test_export_maps_local_bad_status_traps_to_bridge_delivered_rewards(self) -> None:
        with TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            default_path = temp_path / "DefaultKH3.json"
            extracted_path = temp_path / "extracted_locations.json"
            default_path.write_text(json.dumps({
                "Event": {
                    event_id: {"RandomizedItem": "ITEM_POTION\0"}
                    for event_id in ALWAYS_ON_EVENT_IDS
                },
                "TreasureTT": {
                    "TT_TEST_001": {"Treasure": "ITEM_POTION\0"},
                },
            }), encoding="utf-8")
            extracted_path.write_text(json.dumps({"items": [], "locations": []}), encoding="utf-8")

            for offset, (item_name, expected_runtime_token) in enumerate(
                BAD_STATUS_TRAP_RUNTIME_TOKEN_BY_ITEM_NAME.items()
            ):
                with self.subTest(item_name=item_name):
                    ap_location_id = 1263999010 + offset
                    bridge_locations = [{
                        "ap_location_id": ap_location_id,
                        "ap_location_name": "Twilight Town - Test Chest",
                        "assigned_item_game": "Kingdom Hearts III",
                        "assigned_item_name": item_name,
                        "assigned_item_player": 1,
                        "assigned_item_player_name": "KH3",
                        "bridge_key": "TreasureTT:TT_TEST_001",
                        "source_id": "TT_TEST_001",
                        "source_table": "TreasureTT",
                        "type": "treasure",
                    }]

                    result = export_randomized_options_from_bridge_data(
                        default_kh3=default_path,
                        default_kh3_overrides=None,
                        extracted_data_path=extracted_path,
                        bridge_locations=bridge_locations,
                        slot=1,
                        slot_name="KH3",
                        archive_name="AP_test.zip",
                    )

                    self.assertEqual(
                        result.randomized_options["TreasureTT"]["TT_TEST_001"]["Treasure"],
                        f"AP_ITEM_{ap_location_id}\0",
                    )
                    placement = result.placement_summary["locations"][0]
                    self.assertEqual(placement["item_name"], item_name)
                    self.assertEqual(placement["runtime_token"], expected_runtime_token)
                    self.assertTrue(placement["bridge_delivery_required"])

    def test_export_maps_golden_herc_turn_in_to_bridge_delivered_reward(self) -> None:
        with TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            default_path = temp_path / "DefaultKH3.json"
            extracted_path = temp_path / "extracted_locations.json"
            event_table = {
                event_id: {"RandomizedItem": "ITEM_POTION\0"}
                for event_id in ALWAYS_ON_EVENT_IDS
            }
            event_table["EVENT_007"] = {"RandomizedItem": "ITEM_POTION\0"}
            default_path.write_text(json.dumps({"Event": event_table}), encoding="utf-8")
            extracted_path.write_text(json.dumps({
                "items": [
                    {"name": "Kingdom Key", "id": "WEP_KEYBLADE_SO_00"},
                ],
                "locations": [
                    {
                        "source_table": "Event",
                        "source_id": "EVENT_007",
                        "vanilla_item": "PRT_ITEM01",
                    },
                ],
            }), encoding="utf-8")
            bridge_locations = [
                {
                    "ap_location_id": 1263023108,
                    "ap_location_name": "Olympus - EVENT_007 - RandomizedItem",
                    "assigned_item_game": "Kingdom Hearts III",
                    "assigned_item_name": "Kingdom Key",
                    "assigned_item_player": 2,
                    "assigned_item_player_name": "KH3-EZ5t",
                    "bridge_key": "Event:EVENT_007",
                    "source_id": "EVENT_007",
                    "source_table": "Event",
                    "type": "event",
                },
            ]

            result = export_randomized_options_from_bridge_data(
                default_kh3=default_path,
                default_kh3_overrides=None,
                extracted_data_path=extracted_path,
                bridge_locations=bridge_locations,
                slot=2,
                slot_name="KH3-EZ5t",
                archive_name="AP_test.zip",
            )

            self.assertEqual(
                result.randomized_options["Event"]["EVENT_007"]["RandomizedItem"],
                "ETresVictoryBonusKind::NONE\0",
            )
            placement = result.placement_summary["locations"][0]
            self.assertEqual(placement["export_token"], "ETresVictoryBonusKind::NONE")
            self.assertEqual(placement["native_reward_token"], "ETresVictoryBonusKind::NONE")
            self.assertEqual(placement["runtime_token"], "WEP_KEYBLADE_SO_00")
            self.assertTrue(placement["bridge_delivery_required"])
            self.assertTrue(bridge_locations[0]["bridge_delivery_required"])

    def test_export_maps_monstropolis_event_008_to_bridge_delivered_reward(self) -> None:
        with TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            default_path = temp_path / "DefaultKH3.json"
            extracted_path = temp_path / "extracted_locations.json"
            event_table = {
                event_id: {"RandomizedItem": "ITEM_POTION\0"}
                for event_id in ALWAYS_ON_EVENT_IDS
            }
            event_table["EVENT_008"] = {"RandomizedItem": "ACC_ITEM01\0"}
            default_path.write_text(json.dumps({"Event": event_table}), encoding="utf-8")
            extracted_path.write_text(json.dumps({
                "items": [
                    {"name": "Mega-Potion", "id": "ITEM_MEGAPOTION"},
                ],
                "locations": [
                    {
                        "source_table": "Event",
                        "source_id": "EVENT_008",
                        "vanilla_item": "ACC_ITEM01",
                    },
                ],
            }), encoding="utf-8")
            bridge_locations = [
                {
                    "ap_location_id": 1263023115,
                    "ap_location_name": "Monstropolis - EVENT_008 - RandomizedItem",
                    "assigned_item_game": "Kingdom Hearts III",
                    "assigned_item_name": "Mega-Potion",
                    "assigned_item_player": 1,
                    "assigned_item_player_name": "KH3",
                    "bridge_key": "Event:EVENT_008",
                    "source_id": "EVENT_008",
                    "source_table": "Event",
                    "subtype": "RandomizedItem",
                    "type": "event",
                },
            ]

            result = export_randomized_options_from_bridge_data(
                default_kh3=default_path,
                default_kh3_overrides=None,
                extracted_data_path=extracted_path,
                bridge_locations=bridge_locations,
                slot=1,
                slot_name="KH3",
                archive_name="AP_test.zip",
            )

            self.assertEqual(
                result.randomized_options["Event"]["EVENT_008"]["RandomizedItem"],
                "ETresVictoryBonusKind::NONE\0",
            )
            placement = result.placement_summary["locations"][0]
            self.assertEqual(placement["export_token"], "ETresVictoryBonusKind::NONE")
            self.assertEqual(placement["native_reward_token"], "ETresVictoryBonusKind::NONE")
            self.assertEqual(placement["runtime_token"], "ITEM_MEGAPOTION")
            self.assertTrue(placement["bridge_delivery_required"])
            self.assertTrue(bridge_locations[0]["bridge_delivery_required"])

    def test_selected_completion_event_rewards_require_bridge_delivery(self) -> None:
        bridge_keys = {
            "Event:EVENT_008",
            "Event:EVENT_009",
            "Event:EVENT_CKGAME_001",
            "Event:EVENT_HEARTBINDER_002",
            "Event:EVENT_HEARTBINDER_003",
            "Event:EVENT_HEARTBINDER_004",
            "Event:EVENT_KEYBLADE_001",
            "Event:EVENT_KEYBLADE_002",
            "Event:EVENT_KEYBLADE_003",
            "Event:EVENT_KEYBLADE_004",
            "Event:EVENT_KEYBLADE_005",
            "Event:EVENT_KEYBLADE_006",
            "Event:EVENT_KEYBLADE_007",
            "Event:EVENT_KEYBLADE_008",
            "Event:EVENT_KEYBLADE_009",
            "Event:EVENT_KEYBLADE_011",
            "Event:EVENT_KEYITEM_002",
        }

        for bridge_key in sorted(bridge_keys):
            with self.subTest(bridge_key=bridge_key):
                self.assertTrue(local_seeded_reward_requires_bridge_delivery(
                    bridge_key,
                    "event",
                    "ITEM_MEGAPOTION",
                ))

        self.assertTrue(local_seeded_reward_requires_bridge_delivery(
            "Event:EVENT_DATAB_001",
            "event",
            "ITEM_MEGAPOTION",
        ))

    def test_all_event_reward_types_use_ap_after_swap_retirement(self) -> None:
        for event in ("EVENT_002", "EVENT_003", "EVENT_DATAB_001", "EVENT_KEYBLADE_011"):
            for token in ("ETresAbilityKind::AERO_UP", "ITEM_MEGAPOTION", "WEP_KEYBLADE_SO_018"):
                with self.subTest(event=event, token=token):
                    self.assertTrue(local_seeded_reward_requires_bridge_delivery(
                        f"Event:{event}", "event", token))

    def test_level_up_items_always_require_ap_runtime_delivery(self) -> None:
        for runtime_token in (
            "ETresAbilityKind::UNISON_FIRE",
            "ITEM_MEGAPOTION",
            "ETresVictoryBonusKind::MELEM_FIRE",
        ):
            with self.subTest(runtime_token=runtime_token):
                self.assertTrue(local_seeded_reward_requires_bridge_delivery(
                    "LevelUp:8",
                    "level_up",
                    runtime_token,
                ))

    def test_raw_keyblade_locations_normalize_to_friendly_item_names(self) -> None:
        extracted_data = load_extracted_data()
        normalized_locations = normalize_locations(extracted_data)
        target_location = next(
            location
            for location in normalized_locations
            if location.get("vanilla_item_name") == "WEP_KEYBLADE_SO_02"
        )

        self.assertEqual(target_location["primary_item_name"], "Shooting Star")

        item_counts = build_item_counts([target_location])
        self.assertIn("Shooting Star", item_counts)

        item_name_to_id = build_item_name_to_id(extracted_data)
        self.assertIn("Shooting Star", item_name_to_id)


class TestKH3SevenHeartPiecesGoal(KH3TestBase):
    options = {
        "heart_piece_count": 7,
        "remind_pool": KH3LocationPoolMode.option_junk,
    }

    def test_junk_world_pools_retain_required_heart_pieces(self) -> None:
        self.options = {**self.options, "olympus_pool": 3, "twilight_town_pool": 3,
                        "toy_box_pool": 3, "kingdom_of_corona_pool": 3,
                        "monstropolis_pool": 3, "arendelle_pool": 3}
        self.world_setup(seed=14009901)
        items = self.multiworld.itempool + self.multiworld.precollected_items[self.player]
        self.assertGreaterEqual(sum(item.name == HEART_PIECE_ITEM_NAME for item in items), 7)
        self.assertEqual(len(self.multiworld.itempool), len(self.multiworld.get_unfilled_locations()))

    def test_pool_contains_only_the_selected_final_door_key_items(self) -> None:
        self.world_setup()

        all_items = self.multiworld.itempool + self.multiworld.precollected_items[self.player]
        item_counts = Counter(item.name for item in all_items)

        self.assertEqual(item_counts[HEART_PIECE_ITEM_NAME], HEART_PIECE_GOAL_COUNT)
        self.assertTrue(PROOF_ITEMS.isdisjoint(item_counts))
        self.assertTrue(
            all(
                item.classification == ItemClassification.progression
                for item in all_items
                if item.name == HEART_PIECE_ITEM_NAME
            )
        )
        self.assertEqual(len(self.multiworld.itempool), len(self.multiworld.get_unfilled_locations(self.player)))

    def test_slot_data_and_randomizer_config_follow_the_selected_goal(self) -> None:
        self.world_setup()

        slot_data = self.world.build_runtime_slot_data()
        bridge = slot_data["bridge"]
        config = slot_data["kh3_randomizer"]

        self.assertEqual(slot_data["heart_piece_count"], 7)
        self.assertEqual(bridge["heart_piece_count"], 7)
        self.assertEqual(
            bridge["goal_required_item_counts"],
            {
                HEART_PIECE_ITEM_NAME: HEART_PIECE_GOAL_COUNT,
            },
        )
        self.assertEqual(bridge["completion_location_name"], "Defeat Master Xehanort")
        self.assertEqual(config["clear_condition"]["type"], "Heart Pieces")
        self.assertEqual(config["clear_condition"]["conditions"], {"KEY_ITEM17\0": 7})
        self.assertIn(HEART_PIECE_ITEM_NAME, config["hint"]["checks"])
        self.assertTrue(PROOF_ITEMS.isdisjoint(config["hint"]["checks"]))
        self.assertNotIn(STYLE_CHANGE_UNLOCK_ITEM, config["hint"]["checks"])

class TestKH3UltimaWeaponDefaultShards(KH3TestBase):
    def test_default_adds_seven_useful_shards_and_exports_both_settings(self) -> None:
        counts = Counter(item.name for item in self.multiworld.itempool)

        self.assertEqual(counts[ULTIMA_WEAPON_SHARD_ITEM_NAME], 7)
        self.assertEqual(counts[ULTIMA_WEAPON_ITEM_NAME], 0)
        self.assertEqual(classify_item(ULTIMA_WEAPON_SHARD_ITEM_NAME), ItemClassification.useful)
        self.assertEqual(
            resolve_item_runtime_token(ULTIMA_WEAPON_SHARD_ITEM_NAME),
            ULTIMA_WEAPON_SHARD_RUNTIME_TOKEN,
        )
        self.assertEqual(
            classify_runtime_grant_method(ULTIMA_WEAPON_SHARD_RUNTIME_TOKEN),
            "ultima_weapon_shard",
        )
        slot_data = self.world.build_runtime_slot_data()
        self.assertEqual(slot_data["ultima_weapon_mode"], UltimaWeaponMode.option_shards)
        self.assertEqual(slot_data["ultima_weapon_shard_count"], 7)


class TestKH3UltimaWeaponThirteenShards(KH3TestBase):
    options = {"ultima_weapon_shard_count": 13}

    def test_selected_shard_count_replaces_items_without_resizing_pool(self) -> None:
        counts = Counter(item.name for item in self.multiworld.itempool)

        self.assertEqual(counts[ULTIMA_WEAPON_SHARD_ITEM_NAME], 13)
        self.assertEqual(len(self.multiworld.itempool), len(self.multiworld.get_unfilled_locations(self.player)))


class TestKH3UltimaWeaponThirteenShardsWithUpgradeSanity(KH3TestBase):
    options = {
        "ultima_weapon_shard_count": 13,
        "keyblade_levels": KeybladeLevels.option_keyblade_upgrade_sanity,
    }

    def test_maximum_shards_and_individual_upgrades_are_both_preserved(self) -> None:
        all_items = self.multiworld.itempool + self.multiworld.precollected_items[self.player]
        counts = Counter(item.name for item in all_items)
        randomized_item_counts = counts.copy()
        randomized_item_counts.subtract(self.world.options.start_inventory_from_pool.value)
        randomized_keyblade_count = sum(
            1
            for keyblade_name, _ in KEYBLADE_ENHANCEMENT_GROUPS
            if randomized_item_counts[keyblade_name] > 0
        )
        expected_upgrade_count = sum(
            counts[item_name]
            for item_name in PROGRESSIVE_KEYBLADE_LEVEL_ITEM_NAMES
        )

        self.assertEqual(counts[ULTIMA_WEAPON_SHARD_ITEM_NAME], 13)
        self.assertEqual(
            counts[PROGRESSIVE_KEYBLADE_LEVEL_ITEM_NAME_BY_KEYBLADE[ULTIMA_WEAPON_ITEM_NAME]],
            PROGRESSIVE_KEYBLADE_LEVEL_ITEM_COUNT,
        )
        self.assertEqual(len(self.multiworld.itempool), len(self.multiworld.get_unfilled_locations(self.player)))
        self.assertEqual(
            expected_upgrade_count,
            (
                randomized_keyblade_count
                + 1  # Ultima Weapon is obtained through shards instead of a weapon item.
                + len(STATIC_KEYBLADE_UPGRADE_SANITY_KEYBLADE_NAMES)
            ) * PROGRESSIVE_KEYBLADE_LEVEL_ITEM_COUNT,
        )


class TestKH3UltimaWeaponShop(KH3TestBase):
    options = {"ultima_weapon_mode": UltimaWeaponMode.option_shop}

    def test_shop_mode_has_no_randomized_ultima_items(self) -> None:
        names = [item.name for item in self.multiworld.itempool]

        self.assertNotIn(ULTIMA_WEAPON_ITEM_NAME, names)
        self.assertNotIn(ULTIMA_WEAPON_SHARD_ITEM_NAME, names)
        slot_data = self.world.build_runtime_slot_data()
        self.assertEqual(slot_data["ultima_weapon_mode"], UltimaWeaponMode.option_shop)
        self.assertEqual(len(slot_data["ultima_weapon_shop_required_keyblades"]), 15)
        self.assertNotIn(
            ULTIMA_WEAPON_ITEM_NAME,
            slot_data["ultima_weapon_shop_required_keyblades"],
        )
        self.assertNotIn(
            "Pandora's Power",
            slot_data["ultima_weapon_shop_required_keyblades"],
        )


class TestKH3UltimaWeaponShopWithUpgradeSanity(KH3TestBase):
    options = {
        "ultima_weapon_mode": UltimaWeaponMode.option_shop,
        "keyblade_levels": KeybladeLevels.option_keyblade_upgrade_sanity,
        "start_inventory_from_pool": {"Hunny Spout": 1},
    }

    def test_shop_ultima_includes_its_ten_individual_levels(self) -> None:
        counts = Counter(item.name for item in (
            self.multiworld.itempool + self.multiworld.precollected_items[self.player]
        ))
        self.assertEqual(
            counts[PROGRESSIVE_KEYBLADE_LEVEL_ITEM_NAME_BY_KEYBLADE[ULTIMA_WEAPON_ITEM_NAME]],
            PROGRESSIVE_KEYBLADE_LEVEL_ITEM_COUNT,
        )
        self.assertEqual(len(self.multiworld.itempool), len(self.multiworld.get_unfilled_locations(self.player)))

    def test_shop_prerequisites_match_keyblades_with_individual_level_items(self) -> None:
        all_items = self.multiworld.itempool + self.multiworld.precollected_items[self.player]
        owned_item_names = {
            item.name
            for item in all_items
            if item.player == self.player
        }
        expected_keyblades = [
            keyblade_name
            for keyblade_name, _ in KEYBLADE_ENHANCEMENT_GROUPS
            if keyblade_name not in {ULTIMA_WEAPON_ITEM_NAME, "Pandora's Power"}
            and PROGRESSIVE_KEYBLADE_LEVEL_ITEM_NAME_BY_KEYBLADE[keyblade_name]
            in owned_item_names
        ]

        required_keyblades = self.world.build_runtime_slot_data()[
            "ultima_weapon_shop_required_keyblades"
        ]
        self.assertEqual(required_keyblades, expected_keyblades)
        self.assertNotIn("Hunny Spout", required_keyblades)


class TestKH3UltimaWeaponYozora(KH3TestBase):
    options = {"ultima_weapon_mode": UltimaWeaponMode.option_yozora, "unreality_pool": "randomize"}

    def test_yozora_mode_forces_and_locks_the_post_battle_reward(self) -> None:
        location = self.get_location_by_source("Event", "EVENT_YOZORA_001")

        self.assertEqual(location.item.name, ULTIMA_WEAPON_ITEM_NAME)
        self.assertTrue(location.locked)
        self.assertNotIn(
            ULTIMA_WEAPON_ITEM_NAME,
            (item.name for item in self.multiworld.itempool),
        )
        self.assertEqual(len(self.multiworld.itempool), len(self.multiworld.get_unfilled_locations(self.player)))


class TestKH3UltimaWeaponYozoraWithUpgradeSanity(TestKH3UltimaWeaponYozora):
    options = {
        "ultima_weapon_mode": UltimaWeaponMode.option_yozora,
        "unreality_pool": "randomize",
        "keyblade_levels": KeybladeLevels.option_keyblade_upgrade_sanity,
    }

    def test_yozora_ultima_includes_its_ten_individual_levels(self) -> None:
        counts = Counter(item.name for item in (
            self.multiworld.itempool + self.multiworld.precollected_items[self.player]
        ))
        self.assertEqual(
            counts[PROGRESSIVE_KEYBLADE_LEVEL_ITEM_NAME_BY_KEYBLADE[ULTIMA_WEAPON_ITEM_NAME]],
            PROGRESSIVE_KEYBLADE_LEVEL_ITEM_COUNT,
        )


class TestKH3UltimaWeaponRandomizedPool(KH3TestBase):
    options = {"ultima_weapon_mode": UltimaWeaponMode.option_randomized_pool}

    def test_randomized_pool_mode_adds_exactly_one_ultima_weapon(self) -> None:
        counts = Counter(item.name for item in self.multiworld.itempool)

        self.assertEqual(counts[ULTIMA_WEAPON_ITEM_NAME], 1)
        self.assertEqual(counts[ULTIMA_WEAPON_SHARD_ITEM_NAME], 0)
        self.assertEqual(len(self.multiworld.itempool), len(self.multiworld.get_unfilled_locations(self.player)))


class TestKH3UltimaWeaponRandomizedPoolWithUpgradeSanity(KH3TestBase):
    options = {
        "ultima_weapon_mode": UltimaWeaponMode.option_randomized_pool,
        "keyblade_levels": KeybladeLevels.option_keyblade_upgrade_sanity,
    }

    def test_randomized_ultima_includes_its_ten_individual_levels(self) -> None:
        counts = Counter(
            item.name
            for item in (
                self.multiworld.itempool
                + self.multiworld.precollected_items[self.player]
            )
        )

        self.assertEqual(counts[ULTIMA_WEAPON_ITEM_NAME], 1)
        self.assertEqual(
            counts[PROGRESSIVE_KEYBLADE_LEVEL_ITEM_NAME_BY_KEYBLADE[ULTIMA_WEAPON_ITEM_NAME]],
            PROGRESSIVE_KEYBLADE_LEVEL_ITEM_COUNT,
        )


class TestKH3UltimaWeaponBanished(KH3TestBase):
    options = {"ultima_weapon_mode": UltimaWeaponMode.option_banished}

    def test_banished_mode_keeps_ultima_out_of_the_normal_pool(self) -> None:
        names = [item.name for item in self.multiworld.itempool]

        self.assertNotIn(ULTIMA_WEAPON_ITEM_NAME, names)
        self.assertNotIn(ULTIMA_WEAPON_SHARD_ITEM_NAME, names)
        hidden_bonus = self.multiworld.get_location(
            ROCK_TITAN_NO_WALL_RUN_BONUS_LOCATION_NAME,
            self.player,
        )
        self.assertEqual(hidden_bonus.item.name, ULTIMA_WEAPON_ITEM_NAME)
        self.assertTrue(hidden_bonus.locked)


class TestKH3UltimaWeaponBanishedWithUpgradeSanity(TestKH3UltimaWeaponBanished):
    options = {
        "ultima_weapon_mode": UltimaWeaponMode.option_banished,
        "keyblade_levels": KeybladeLevels.option_keyblade_upgrade_sanity,
    }

    def test_banished_ultima_has_no_individual_levels(self) -> None:
        all_items = self.multiworld.itempool + self.multiworld.precollected_items[self.player]
        self.assertNotIn(
            PROGRESSIVE_KEYBLADE_LEVEL_ITEM_NAME_BY_KEYBLADE[ULTIMA_WEAPON_ITEM_NAME],
            [item.name for item in all_items],
        )


class TestKH3KeybladeLevelsStartAtMax(KH3TestBase):
    options = {"keyblade_levels": KeybladeLevels.option_start_at_max}

    def test_starts_maxed_without_randomized_level_items(self) -> None:
        all_items = self.multiworld.itempool + self.multiworld.precollected_items[self.player]

        self.assertTrue(
            PROGRESSIVE_KEYBLADE_LEVEL_ITEM_NAMES.isdisjoint(
                item.name for item in all_items
            )
        )
        slot_data = self.world.build_runtime_slot_data()
        self.assertEqual(
            slot_data["keyblade_levels"],
            KeybladeLevels.option_start_at_max,
        )
        self.assertNotIn("keyblade_upgrade_sanity", slot_data)
        self.assertEqual(len(self.multiworld.itempool), len(self.multiworld.get_unfilled_locations(self.player)))


class TestKH3KeybladeUpgradeSanity(KH3TestBase):
    options = {
        "keyblade_levels": KeybladeLevels.option_keyblade_upgrade_sanity,
        "lucky_emblems_pool": KH3LocationPoolMode.option_junk,
        "battlegates_pool": KH3LocationPoolMode.option_junk,
        "data_battle_rewards_pool": KH3LocationPoolMode.option_junk,
        "remind_pool": KH3LocationPoolMode.option_junk,
        "unreality_pool": KH3LocationPoolMode.option_junk,
        "keyblade_graveyard_pool": KH3LocationPoolMode.option_junk,
    }

    def test_each_keyblade_has_ten_independent_useful_progressive_items(self) -> None:
        all_items = self.multiworld.itempool + self.multiworld.precollected_items[self.player]
        counts = Counter(item.name for item in all_items)
        randomized_item_counts = counts.copy()
        randomized_item_counts.subtract(self.world.options.start_inventory_from_pool.value)
        randomized_keyblade_names = {
            keyblade_name
            for keyblade_name, _ in KEYBLADE_ENHANCEMENT_GROUPS
            if randomized_item_counts[keyblade_name] > 0
        }
        upgrade_sanity_keyblade_names = (
            randomized_keyblade_names
            | STATIC_KEYBLADE_UPGRADE_SANITY_KEYBLADE_NAMES
            | {ULTIMA_WEAPON_ITEM_NAME}
        )

        self.assertEqual(len(KEYBLADE_ENHANCEMENT_GROUPS), 17)
        self.assertEqual(
            STATIC_KEYBLADE_UPGRADE_SANITY_KEYBLADE_NAMES,
            {"Pandora's Power"},
        )
        self.assertEqual(counts[PROGRESSIVE_KEYBLADE_LEVEL_ITEM_NAME], 0)
        for keyblade_name, _ in KEYBLADE_ENHANCEMENT_GROUPS:
            item_name = f"Progressive {keyblade_name} Level"
            with self.subTest(keyblade_name=keyblade_name):
                expected_count = (
                    PROGRESSIVE_KEYBLADE_LEVEL_ITEM_COUNT
                    if keyblade_name in upgrade_sanity_keyblade_names
                    else 0
                )
                self.assertEqual(counts[item_name], expected_count)
                self.assertEqual(classify_item(item_name), ItemClassification.useful)
        self.assertNotIn("Ultima Weapon", randomized_keyblade_names)
        self.assertNotIn("Pandora's Power", randomized_keyblade_names)
        self.assertEqual(counts[ULTIMA_WEAPON_SHARD_ITEM_NAME], 7)
        self.assertEqual(
            counts["Progressive Ultima Weapon Level"],
            PROGRESSIVE_KEYBLADE_LEVEL_ITEM_COUNT,
        )
        self.assertEqual(
            counts["Progressive Pandora's Power Level"],
            PROGRESSIVE_KEYBLADE_LEVEL_ITEM_COUNT,
        )
        self.assertEqual(
            sum(counts[item_name] for item_name in PROGRESSIVE_KEYBLADE_LEVEL_ITEM_NAMES),
            len(upgrade_sanity_keyblade_names) * PROGRESSIVE_KEYBLADE_LEVEL_ITEM_COUNT,
        )
        self.assertEqual(len(self.multiworld.itempool), len(self.multiworld.get_unfilled_locations(self.player)))
        slot_data = self.world.build_runtime_slot_data()
        self.assertEqual(
            slot_data["keyblade_levels"],
            KeybladeLevels.option_keyblade_upgrade_sanity,
        )
        self.assertNotIn("keyblade_upgrade_sanity", slot_data)

    def test_individual_item_ids_append_after_the_existing_shared_item(self) -> None:
        shared_item_id = self.world.item_name_to_id[PROGRESSIVE_KEYBLADE_LEVEL_ITEM_NAME]
        individual_item_ids = [
            self.world.item_name_to_id[item_name]
            for item_name in PROGRESSIVE_KEYBLADE_LEVEL_INDIVIDUAL_ITEM_NAMES
        ]

        self.assertEqual(
            individual_item_ids,
            list(range(shared_item_id + 1, shared_item_id + 1 + len(individual_item_ids))),
        )


class TestKH3KeybladeUpgradeSanityStartingInventoryFromPool(TestKH3KeybladeUpgradeSanity):
    options = {
        **TestKH3KeybladeUpgradeSanity.options,
        "start_inventory_from_pool": {"Hunny Spout": 1},
    }

    def test_keyblade_removed_from_pool_by_yaml_has_no_randomized_upgrades(self) -> None:
        all_items = self.multiworld.itempool + self.multiworld.precollected_items[self.player]
        counts = Counter(item.name for item in all_items)

        self.assertEqual(counts["Progressive Hunny Spout Level"], 0)


del KH3TestBase
