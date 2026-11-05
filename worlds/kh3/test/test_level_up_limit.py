
from ..Options import LevelUpLimit, build_randomizer_generation_config
from . import KH3TestBase


class TestExtendedLevelUpLimit(KH3TestBase):
    options = {"level_up_limit": 99}

    def test_extended_limits_generate_balanced_checks_and_slot_data(self):
        for limit in (1, 2, 3, 35, 51, 55, 60, 65, 70, 75, 80, 85, 90, 95, 98, 99):
            with self.subTest(limit=limit):
                self.assertEqual(LevelUpLimit.from_any(str(limit)).value, limit)
                self.options = {"level_up_limit": limit}
                self.world_setup(seed=99)
                levels = {int(row["source_id"]) for row in self.world.active_locations
                          if row["type"] == "level_up"}
                self.assertEqual(levels, set(range(2, 100)))
                self.assertEqual(len(self.multiworld.itempool), len(self.multiworld.get_unfilled_locations()))
                self.assertEqual(build_randomizer_generation_config(self.world.options)
                                 ["limiters"]["Level Up Limit"], limit)
                rows = [row for row in self.world.build_runtime_slot_data()["bridge"]["locations"]
                        if row["type"] == "level_up"]
                self.assertEqual({int(row["source_id"]) for row in rows}, levels)
                self.assertTrue(all(row["bridge_delivery_required"] for row in rows))

    def test_range_accepts_every_level_and_rejects_outside_bounds(self):
        with self.assertRaises(Exception):
            LevelUpLimit.from_any("level_35")
        for level in range(1, 100):
            self.assertEqual(LevelUpLimit.from_any(level).value, level)
            self.assertEqual(LevelUpLimit.from_any(str(level)).value, level)
        for level in (0, 100):
            with self.assertRaises(Exception):
                LevelUpLimit.from_any(level)

    def test_junk_worlds_remain_available_for_level_up_xp(self):
        from ..Data import LEVEL_UP_XP_SOURCE_REGION_NAMES
        self.options = {"level_up_limit": 99, "olympus_pool": "junk",
                        "twilight_town_pool": "junk", "toy_box_pool": "junk",
                        "kingdom_of_corona_pool": "junk", "monstropolis_pool": "junk",
                        "arendelle_pool": "junk", "san_fransokyo_pool": "junk",
                        "the_caribbean_pool": "junk", "olympus_coliseum_pool": 1}
        self.world_setup(seed=14009904)
        regions = {region.name for region in self.multiworld.get_regions(self.player)}
        self.assertTrue(set(LEVEL_UP_XP_SOURCE_REGION_NAMES).issubset(regions))
        state = self.multiworld.get_all_state(False)
        self.assertTrue(self.multiworld.get_location("Level Ups - Sora Level 99", self.player).can_reach(state))


class TestJunkLevelUpChecks(KH3TestBase):
    options = {"level_up_limit": 1}


    def test_limit_is_a_strict_junk_boundary_for_local_and_remote_rewards(self):
        from BaseClasses import Item, ItemClassification
        from Fill import distribute_items_restrictive

        for limit in (1, 10, 35, 99):
            with self.subTest(limit=limit):
                self.options = {"level_up_limit": limit}
                self.world_setup(seed=7710 + limit)
                levels = [row for row in self.world.active_locations if row["type"] == "level_up"]
                self.assertEqual(len(levels), 98)
                for row in levels:
                    location = self.multiworld.get_location(row["name"], self.player)
                    above_limit = int(row["source_id"]) > limit
                    self.assertEqual(location.filler_only, above_limit)
                    for player in (1, 2):
                        for classification in (ItemClassification.progression, ItemClassification.useful):
                            self.assertEqual(location.item_rule(Item("Test", classification, 1, player)), not above_limit)
                        self.assertTrue(location.item_rule(Item("Filler", ItemClassification.filler, 1, player)))
                        self.assertTrue(location.item_rule(Item("Trap", ItemClassification.trap, 1, player)))
                distribute_items_restrictive(self.multiworld)
                self.assertTrue(self.multiworld.can_beat_game())
                for row in levels:
                    if int(row["source_id"]) > limit:
                        item = self.multiworld.get_location(row["name"], self.player).item
                        self.assertFalse(item.classification & (ItemClassification.progression | ItemClassification.useful))
