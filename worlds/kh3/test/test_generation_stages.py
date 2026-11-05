from unittest import TestCase
from unittest.mock import patch

from test.general import setup_multiworld
from worlds.AutoWorld import call_all

from .. import YOZORA_REWARD_SOURCE_KEY, KH3World
from ..Data import ULTIMA_WEAPON_ITEM_NAME
from ..Locations import COMPLETION_LOCATION_NAME, ROCK_TITAN_NO_WALL_RUN_BONUS_LOCATION_NAME
from ..Options import UltimaWeaponMode


class TestFixedRewardGenerationStages(TestCase):
    def test_shared_regions_exist_before_rules_and_later_stages_keep_graph_size(self):
        for options in ({}, {"include_space": 1, "classic_kingdom_checks": 1},
                        {"synthesis_pool": "randomize", "photo_missions_pool": "randomize"}):
            with self.subTest(options=options):
                multiworld = setup_multiworld(KH3World, seed=31, options=options,
                    steps=("generate_early", "create_regions", "create_items"))
                self.assertGreater(multiworld.worlds[1].logic_region_count, 0)
                regions = set(multiworld.get_regions())
                locations = set(multiworld.get_locations())
                for step in ("set_rules", "connect_entrances", "generate_basic", "pre_fill"):
                    call_all(multiworld, step)
                    self.assertEqual(regions, set(multiworld.get_regions()), step)
                    self.assertEqual(locations, set(multiworld.get_locations()), step)

    def test_fixed_rewards_exist_before_rules_without_duplicating_pool_items(self):
        for mode in UltimaWeaponMode.name_lookup:
            with self.subTest(ultima_weapon_mode=mode):
                multiworld = setup_multiworld(
                    KH3World, steps=("generate_early", "create_regions", "create_items"),
                    seed=31, options={"ultima_weapon_mode": mode},
                )
                world = multiworld.worlds[1]
                victory = multiworld.get_location(COMPLETION_LOCATION_NAME, 1)
                bonus = multiworld.get_location(ROCK_TITAN_NO_WALL_RUN_BONUS_LOCATION_NAME, 1)
                self.assertEqual(victory.item.name, "Victory")
                self.assertIsNone(victory.item.code)
                self.assertTrue(victory.locked)
                self.assertEqual(bonus.item.name, ULTIMA_WEAPON_ITEM_NAME)
                self.assertTrue(bonus.locked)
                self.assertEqual(bonus.item.player, 1)
                self.assertTrue(all(item is not victory.item for item in multiworld.itempool))
                self.assertTrue(all(item is not bonus.item for item in multiworld.itempool))
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

    def test_junk_olympus_preserves_its_fixed_bonus_and_pool_capacity(self):
        multiworld = setup_multiworld(
            KH3World, steps=("generate_early", "create_regions", "create_items"),
            seed=31, options={"olympus_pool": "junk"},
        )
        bonus = multiworld.get_location(ROCK_TITAN_NO_WALL_RUN_BONUS_LOCATION_NAME, 1)
        self.assertEqual(bonus.item.name, ULTIMA_WEAPON_ITEM_NAME)
        self.assertTrue(bonus.locked)
        self.assertEqual(multiworld.get_location(COMPLETION_LOCATION_NAME, 1).item.name, "Victory")
        self.assertEqual(len(multiworld.itempool), len(multiworld.get_unfilled_locations()))
        call_all(multiworld, "set_rules")
        call_all(multiworld, "pre_fill")
        self.assertTrue(bonus.filler_only)
        self.assertFalse(bonus.item.advancement or bonus.item.useful)
        self.assertIn(ULTIMA_WEAPON_ITEM_NAME, [item.name for item in multiworld.precollected_items[1]])
        self.assertEqual(multiworld.get_location(COMPLETION_LOCATION_NAME, 1).item.name, "Victory")
