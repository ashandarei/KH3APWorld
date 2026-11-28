from unittest import TestCase
from unittest.mock import patch

from test.general import setup_multiworld
from worlds.AutoWorld import call_all

from .. import YOZORA_REWARD_SOURCE_KEY, KH3World
from ..Data import ULTIMA_WEAPON_ITEM_NAME
from ..Locations import COMPLETION_LOCATION_NAME
from ..Options import UltimaWeaponMode, WallRunMode
from ..SlotData import decode_slot_data


class TestFixedRewardGenerationStages(TestCase):
    def test_locations_keep_their_regions_through_pre_fill(self):
        for options in ({}, {"include_space": 1, "classic_kingdom_checks": 1},
                        {"synthesis_pool": "randomize", "photo_missions_pool": "randomize"}):
            with self.subTest(options=options):
                multiworld = setup_multiworld(KH3World, seed=31, options=options,
                    steps=("generate_early", "create_regions", "create_items"))
                regions = set(multiworld.get_regions())
                locations = {location: location.parent_region for location in multiworld.get_locations()}
                for step in ("set_rules", "connect_entrances", "generate_basic", "pre_fill"):
                    call_all(multiworld, step)
                    self.assertEqual(regions, set(multiworld.get_regions()), step)
                    current_locations = {location: location.parent_region for location in multiworld.get_locations()}
                    self.assertEqual(locations, current_locations, step)

    def test_fixed_rewards_exist_before_rules_without_duplicating_pool_items(self):
        for mode in UltimaWeaponMode.name_lookup:
            with self.subTest(ultima_weapon_mode=mode):
                multiworld = setup_multiworld(
                    KH3World, steps=("generate_early", "create_regions", "create_items"),
                    seed=31, options={"ultima_weapon_mode": mode},
                )
                world = multiworld.worlds[1]
                victory = multiworld.get_location(COMPLETION_LOCATION_NAME, 1)
                self.assertEqual(victory.item.name, "Victory")
                self.assertIsNone(victory.item.code)
                self.assertTrue(victory.locked)
                self.assertTrue(all(item is not victory.item for item in multiworld.itempool))
                if mode == UltimaWeaponMode.option_yozora:
                    row = next(row for row in world.active_locations
                               if (row["source_table"], row["source_id"]) == YOZORA_REWARD_SOURCE_KEY)
                    yozora = multiworld.get_location(row["name"], 1)
                    self.assertEqual(yozora.item.name, ULTIMA_WEAPON_ITEM_NAME)
                    self.assertTrue(yozora.locked)
                    self.assertTrue(all(item is not yozora.item for item in multiworld.itempool))
                self.assertEqual(len(multiworld.itempool), len(multiworld.get_unfilled_locations()))

                items_before_rules = {id(item) for item in multiworld.get_items()}
                with patch.object(world, "create_item", side_effect=AssertionError("Late item creation")), \
                        patch.object(world, "create_event_item", side_effect=AssertionError("Late event creation")):
                    call_all(multiworld, "set_rules")
                self.assertEqual(items_before_rules, {id(item) for item in multiworld.get_items()})

    def test_rock_titan_keeps_ordinary_rewards_in_each_wall_run_mode(self):
        retired_bonus_id = 1263088383
        self.assertNotIn(retired_bonus_id, KH3World.location_name_to_id.values())
        for mode in WallRunMode.name_lookup:
            with self.subTest(wall_run_mode=mode):
                multiworld = setup_multiworld(KH3World, seed=31, options={
                    "wall_run_mode": mode,
                    "olympus_pool": "junk",
                    "ultima_weapon_mode": "banished",
                })
                self.assertEqual(len(multiworld.itempool), len(multiworld.get_unfilled_locations()))
                self.assertNotIn(retired_bonus_id, {loc.address for loc in multiworld.get_locations()})
                self.assertNotIn(ULTIMA_WEAPON_ITEM_NAME, {
                    item.name for item in multiworld.precollected_items[1]
                })
                bridge = decode_slot_data(multiworld.worlds[1].fill_slot_data())["bridge"]
                rewards = {row["bridge_key"]: row for row in bridge["locations"]}
                self.assertIn("VBonus:Vbonus_010", rewards)
                self.assertNotIn(retired_bonus_id, {row["ap_location_id"] for row in rewards.values()})
                self.assertEqual(sum(
                    rule["trigger_bridge_key"] == "VBonus:Vbonus_010"
                    for rule in bridge["conditional_checks"]
                ), 2)
