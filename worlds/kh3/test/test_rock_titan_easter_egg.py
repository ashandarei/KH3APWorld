from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from typing import ClassVar
from unittest.mock import patch

from ..client_support.bridge_sync_service import (
    BridgeSyncService,
)
from ..export_ap_kh3_randomized_options import (
    ALWAYS_ON_EVENT_IDS,
    export_randomized_options,
    export_randomized_options_from_bridge_data,
)
from ..Locations import (
    ROCK_TITAN_BONUS_BRIDGE_LOCATION,
    ROCK_TITAN_NO_WALL_RUN_BONUS_LOCATION_ID,
    ROCK_TITAN_NO_WALL_RUN_BONUS_LOCATION_NAME,
    ROCK_TITAN_VBONUS_BRIDGE_KEY,
    ROCK_TITAN_VBONUS_SOURCE_KEY,
    location_name_to_id,
)
from . import KH3TestBase


class TestRockTitanSequenceBreakExport(unittest.TestCase):
    def test_conditional_reward_exports_without_a_native_datatable_row(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            defaults, extracted = root / "defaults.json", root / "extracted.json"
            defaults.write_text(json.dumps({"Event": {
                key: {"RandomizedItem": "ITEM_POTION\0"} for key in ALWAYS_ON_EVENT_IDS}}))
            extracted.write_text(json.dumps({"items": [{"name": "Potion", "id": "ITEM_POTION"}], "locations": []}))
            row = dict(ROCK_TITAN_BONUS_BRIDGE_LOCATION,
                       assigned_item_game="Kingdom Hearts III", assigned_item_name="Potion",
                       assigned_item_player=1, assigned_item_player_name="KH3")
            result = export_randomized_options_from_bridge_data(
                default_kh3=defaults, default_kh3_overrides=None, extracted_data_path=extracted,
                slot=1, slot_name="KH3", archive_name="AP_test.zip", bridge_locations=[row])
            placement = result.placement_summary["locations"][0]
            self.assertEqual(placement["location_type"], "sequence_break_bonus")
            self.assertEqual(placement["ap_location_id"], ROCK_TITAN_NO_WALL_RUN_BONUS_LOCATION_ID)
            self.assertTrue(placement["bridge_delivery_required"])

    def test_archive_export_supports_conditional_reward_without_a_native_row(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            defaults, extracted = root / "defaults.json", root / "extracted.json"
            defaults.write_text(json.dumps({"Event": {
                key: {"RandomizedItem": "ITEM_POTION\0"} for key in ALWAYS_ON_EVENT_IDS}}))
            extracted.write_text(json.dumps({"items": [{"name": "Potion", "id": "ITEM_POTION"}], "locations": []}))
            row = dict(ROCK_TITAN_BONUS_BRIDGE_LOCATION)
            multidata = {
                "slot_info": {1: SimpleNamespace(name="KH3", game="Kingdom Hearts III")},
                "slot_data": {1: {}},
                "locations": {1: {row["ap_location_id"]: (100, 1, 0)}},
                "datapackage": {"Kingdom Hearts III": {"item_name_to_id": {"Potion": 100}}},
            }
            args = SimpleNamespace(
                default_kh3=defaults, default_kh3_overrides=None, extracted_data=extracted,
                ap_zip=root / "AP_test.zip", runtime_root=root, slot=1,
                chrinit_sora_have_ability=[], event_reward_override=[],
            )
            with patch("worlds.kh3.export_ap_kh3_randomized_options.load_multidata", return_value=multidata), \
                    patch("worlds.kh3.export_ap_kh3_randomized_options.require_bridge_locations", return_value=[row]):
                result = export_randomized_options(args)
            placement = result.placement_summary["locations"][0]
            self.assertEqual(placement["location_type"], "sequence_break_bonus")
            self.assertTrue(placement["bridge_delivery_required"])
            self.assertNotIn("Conditional", result.randomized_options)


class TestRockTitanSequenceBreakWorld(KH3TestBase):
    def test_bonus_location_is_locked_to_local_ultima_weapon(self) -> None:
        self.world_setup()

        bonus_location = self.multiworld.get_location(
            ROCK_TITAN_NO_WALL_RUN_BONUS_LOCATION_NAME,
            self.player,
        )
        rock_titan_location = next(
            self.multiworld.get_location(location["name"], self.player)
            for location in self.world.active_locations
            if (location["source_table"], location["source_id"]) == ROCK_TITAN_VBONUS_SOURCE_KEY
        )

        self.assertEqual(
            location_name_to_id[ROCK_TITAN_NO_WALL_RUN_BONUS_LOCATION_NAME],
            ROCK_TITAN_NO_WALL_RUN_BONUS_LOCATION_ID,
        )
        self.assertEqual(bonus_location.address, ROCK_TITAN_NO_WALL_RUN_BONUS_LOCATION_ID)
        if bonus_location.filler_only:
            self.assertFalse(bonus_location.item.advancement or bonus_location.item.useful)
            self.assertIn("Ultima Weapon", [item.name for item in self.multiworld.precollected_items[self.player]])
        else:
            self.assertEqual(bonus_location.item.name, "Ultima Weapon")
        self.assertEqual(bonus_location.item.player, self.player)
        self.assertIs(bonus_location.access_rule, rock_titan_location.access_rule)
        self.assertNotIn("Ultima Weapon", (item.name for item in self.multiworld.itempool))

    def test_slot_data_describes_the_no_wall_run_condition(self) -> None:
        self.world_setup()

        rules = self.world.build_runtime_slot_data()["bridge"]["conditional_checks"]

        self.assertEqual(
            [rule for rule in rules if rule["ap_location_id"] == ROCK_TITAN_NO_WALL_RUN_BONUS_LOCATION_ID],
            [
                {
                    "trigger_bridge_key": ROCK_TITAN_VBONUS_BRIDGE_KEY,
                    "ap_location_id": ROCK_TITAN_NO_WALL_RUN_BONUS_LOCATION_ID,
                    "ap_location_name": ROCK_TITAN_NO_WALL_RUN_BONUS_LOCATION_NAME,
                    "unless_item_id": self.world.item_name_to_id["Unlock: Wall Run"],
                    "unless_item_name": "Unlock: Wall Run",
                    "unless_start_option": "start_with_wall_run",
                }
            ],
        )


class TestRockTitanSequenceBreakWorldWithJunkOlympus(TestRockTitanSequenceBreakWorld):
    # Junk keeps the AP checks, including the locked sequence-break reward.
    options: ClassVar[dict[str, int]] = {"olympus_pool": 3}


class TestRockTitanSequenceBreakClient(unittest.TestCase):
    WALL_RUN_ITEM_ID = 9001
    BONUS_LOCATION_ID = 8001

    @classmethod
    def slot_data(cls, *, start_with_wall_run: bool = False) -> dict:
        return {
            "start_with_wall_run": start_with_wall_run,
            "bridge": {
                "conditional_checks": [
                    {
                        "trigger_bridge_key": ROCK_TITAN_VBONUS_BRIDGE_KEY,
                        "ap_location_id": cls.BONUS_LOCATION_ID,
                        "unless_item_id": cls.WALL_RUN_ITEM_ID,
                        "unless_start_option": "start_with_wall_run",
                    }
                ],
            },
        }


    def test_rock_titan_observation_expands_to_normal_and_bonus_checks(self) -> None:
        service = BridgeSyncService.__new__(BridgeSyncService)
        service.log_warning = lambda *_args: None

        location_ids = service._location_ids(
            {"checked_locations": [ROCK_TITAN_VBONUS_BRIDGE_KEY, "Conditional:RockTitanSequenceBreak"]},
            {ROCK_TITAN_VBONUS_BRIDGE_KEY: 7001, "Conditional:RockTitanSequenceBreak": self.BONUS_LOCATION_ID},
        )

        self.assertEqual(location_ids, {7001, self.BONUS_LOCATION_ID})
