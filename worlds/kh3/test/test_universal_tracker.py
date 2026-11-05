from __future__ import annotations

import inspect
import json
from argparse import Namespace
from typing import ClassVar

from BaseClasses import CollectionState, LocationProgressType, MultiWorld
from worlds.AutoWorld import call_all
from worlds.generic.Rules import exclusion_rules

from .. import KH3World
from ..Options import (
    ImportantHintChecks,
    KH3LocationPoolMode,
    LuckyEmblemsPerCheck,
)
from ..SlotData import encode_slot_data
from ..universal_tracker import (
    KH3_UNIVERSAL_TRACKER_OPTION_NAMES,
    KH3_UNIVERSAL_TRACKER_SLOT_DATA_KEY,
    KH3_UNIVERSAL_TRACKER_SLOT_DATA_VERSION,
)
from . import KH3TestBase


class TestKH3UniversalTracker(KH3TestBase):
    options: ClassVar[dict[str, int]] = {
        "remind_pool": KH3LocationPoolMode.option_junk,
    }

    def test_legacy_minigame_pool_expands_without_overriding_individual_modes(self):
        from ..Minigames import MINIGAME_POOL_OPTION_NAMES
        from ..universal_tracker import universal_tracker_options_from_slot_data

        slot_data = self.world.build_runtime_slot_data()
        saved = slot_data[KH3_UNIVERSAL_TRACKER_SLOT_DATA_KEY]["options"]
        for name in MINIGAME_POOL_OPTION_NAMES:
            saved.pop(name)
        saved["minigames_pool"] = 2
        saved["festival_dance_pool"] = 3
        restored = universal_tracker_options_from_slot_data(slot_data)
        self.assertNotIn("minigames_pool", restored)
        for name in MINIGAME_POOL_OPTION_NAMES:
            self.assertEqual(restored[name], 3 if name == "festival_dance_pool" else 2)

    def build_yamlless_tracker_multiworld(self, slot_data: dict) -> MultiWorld:
        multiworld = MultiWorld(1)
        multiworld.generation_is_fake = True
        multiworld.re_gen_passthrough = {KH3World.game: encode_slot_data(slot_data)}
        multiworld.game[1] = KH3World.game
        multiworld.player_name = {1: "Tracker"}
        multiworld.set_seed(1)

        args = Namespace()
        for option_name, option_type in KH3World.options_dataclass.type_hints.items():
            setattr(args, option_name, {1: option_type.from_any(option_type.default)})
        multiworld.set_options(args)
        multiworld.state = CollectionState(multiworld)

        for step in (
            "generate_early",
            "create_regions",
            "create_items",
            "set_rules",
            "connect_entrances",
            "generate_basic",
        ):
            call_all(multiworld, step)
            if step == "set_rules":
                exclusion_rules(
                    multiworld,
                    1,
                    multiworld.worlds[1].options.exclude_locations.value,
                )
        return multiworld

    def test_world_declares_yamlless_universal_tracker_contract(self) -> None:
        self.assertTrue(KH3World.ut_can_gen_without_yaml)
        self.assertIsInstance(
            inspect.getattr_static(KH3World, "interpret_slot_data"),
            staticmethod,
        )

        slot_data = {"heart_piece_count": 7}
        self.assertIs(KH3World.interpret_slot_data(slot_data), slot_data)

    def test_ingredient_display_aliases_preserve_tracker_pickup_names_and_ids(self) -> None:
        from ..Ingredients import INGREDIENT_SPOT_DISPLAY_NAMES, INGREDIENT_SPOTS_BY_ID

        slot_data = self.world.build_runtime_slot_data()
        slot_data[KH3_UNIVERSAL_TRACKER_SLOT_DATA_KEY]["options"]["ingredients"] = 2
        tracker_world = self.build_yamlless_tracker_multiworld(slot_data).worlds[1]
        tracker_data = tracker_world.build_runtime_slot_data()
        wire = encode_slot_data(tracker_data)
        locations = [row for row in tracker_data["bridge"]["locations"]
                     if row["source_table"] == "IngredientSpot"]
        self.assertEqual(len(locations), len(INGREDIENT_SPOT_DISPLAY_NAMES))
        exported = KH3World.get_data_package_data()["location_name_to_id"]
        for row in locations:
            name = INGREDIENT_SPOTS_BY_ID[row["source_id"]]["name"]
            with self.subTest(spot=row["source_id"]):
                self.assertEqual(row["ap_location_name"], name)
                self.assertEqual(row["bridge_key"], f"IngredientSpot:{row['source_id']}")
                self.assertEqual(row["ap_location_id"], exported[name])
                alias = INGREDIENT_SPOT_DISPLAY_NAMES[row["source_id"]]
                self.assertEqual(tracker_world.location_id_to_alias[row["ap_location_id"]], alias)
                self.assertEqual(wire["location_id_to_alias"][str(row["ap_location_id"])], alias)

    def test_slot_data_exports_every_tracker_relevant_resolved_option(self) -> None:
        slot_data = self.world.build_runtime_slot_data()
        tracker_data = slot_data[KH3_UNIVERSAL_TRACKER_SLOT_DATA_KEY]

        self.assertEqual(
            tracker_data["schema_version"],
            KH3_UNIVERSAL_TRACKER_SLOT_DATA_VERSION,
        )
        self.assertEqual(
            set(tracker_data["options"]),
            set(KH3_UNIVERSAL_TRACKER_OPTION_NAMES),
        )
        self.assertEqual(
            tracker_data["options"]["remind_pool"],
            KH3LocationPoolMode.option_junk,
        )
        self.assertIn("exclude_locations", tracker_data["options"])
        self.assertIsInstance(tracker_data["options"]["important_hint_checks"], list)
        json.dumps(tracker_data)

    def test_generate_early_restores_slot_options_before_location_filtering(self) -> None:
        slot_data = self.world.build_runtime_slot_data()
        excluded_location_name = next(
            location["name"]
            for location in self.world.active_locations
            if location["world"] == "Toy Box"
        )
        slot_data[KH3_UNIVERSAL_TRACKER_SLOT_DATA_KEY]["options"].update({
            "heart_piece_count": 7,
            "olympus_pool": KH3LocationPoolMode.option_junk,
            "lucky_emblems_per_check": 5,
            "lucky_emblem_limit": 20,
            "level_up_limit": 10,
            "exclude_locations": [excluded_location_name],
            "start_inventory_from_pool": {"Magic: Fire": 2},
            "important_hint_checks": ["Magic: Cure", "Magic: Fire"],
        })

        tracker_multiworld = self.build_yamlless_tracker_multiworld(slot_data)
        tracker_world = tracker_multiworld.worlds[1]

        self.assertEqual(tracker_world.options.heart_piece_count.value, 7)
        self.assertEqual(
            tracker_world.options.olympus_pool.value,
            KH3LocationPoolMode.option_junk,
        )
        self.assertIsInstance(tracker_world.options.lucky_emblems_per_check, LuckyEmblemsPerCheck)
        self.assertEqual(tracker_world.options.lucky_emblems_per_check.value, 5)
        self.assertEqual(
            tracker_world.options.start_inventory_from_pool.value,
            {"Magic: Fire": 2},
        )
        self.assertEqual(
            tracker_world.options.exclude_locations.value,
            {excluded_location_name},
        )
        self.assertIsInstance(tracker_world.options.important_hint_checks, ImportantHintChecks)
        self.assertEqual(
            tracker_world.options.important_hint_checks.value,
            {"Magic: Cure", "Magic: Fire"},
        )
        self.assertTrue(any(
            location["world"] == "Olympus"
            for location in tracker_world.active_locations
        ))
        level_rows = [row for row in tracker_world.active_locations if row.get("type") == "level_up"]
        self.assertEqual(len(level_rows), 98)
        for row in level_rows:
            self.assertEqual(tracker_multiworld.get_location(row["name"], 1).filler_only,
                             int(row["source_id"]) > 10)
        self.assertTrue(all(
            int(location["source_id"]) <= 20
            and int(location["source_id"]) % 5 == 0
            for location in tracker_world.active_locations
            if location["world"] == "Lucky Emblems"
        ))
        self.assertEqual(
            tracker_multiworld.get_location(excluded_location_name, 1).progress_type,
            LocationProgressType.EXCLUDED,
        )

    def test_retired_vanilla_reward_mode_is_rejected(self) -> None:
        slot_data = self.world.build_runtime_slot_data()
        slot_data = json.loads(json.dumps(slot_data))
        slot_data[KH3_UNIVERSAL_TRACKER_SLOT_DATA_KEY]["options"]["olympus_pool"] = 1
        with self.assertRaisesRegex(ValueError, "Invalid KH3 Universal Tracker value.*olympus_pool"):
            self.build_yamlless_tracker_multiworld(slot_data)

    def test_missing_tracker_payload_fails_instead_of_guessing_options(self) -> None:
        wire = self.world.fill_slot_data()
        del wire[KH3_UNIVERSAL_TRACKER_SLOT_DATA_KEY]
        self.multiworld.re_gen_passthrough = {self.world.game: wire}

        with self.assertRaisesRegex(ValueError, "missing the universal_tracker payload"):
            self.world._restore_universal_tracker_options()

    def test_unknown_tracker_schema_fails_instead_of_using_default_logic(self) -> None:
        wire = self.world.fill_slot_data()
        wire[KH3_UNIVERSAL_TRACKER_SLOT_DATA_KEY]["schema_version"] += 1
        self.multiworld.re_gen_passthrough = {self.world.game: wire}

        with self.assertRaisesRegex(ValueError, "Unsupported KH3 Universal Tracker"):
            self.world._restore_universal_tracker_options()
