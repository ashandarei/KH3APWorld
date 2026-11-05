from . import KH3TestBase


class TestKH3EmblemRegions(KH3TestBase):
    auto_construct = False
    options = {
        "level_up_limit": 1,
        "olympus_coliseum_pool": "off",
        "toy_box_pool": "junk",
        "kingdom_of_corona_pool": "junk",
        "san_fransokyo_pool": "junk",
        "hundred_acre_wood_pool": "junk",
        "lucky_emblems_pool": "randomize",
        "lucky_emblems_per_check": 30,
        "lucky_emblem_limit": 90,
        "music_chest_count": 0,
        "music_chest_vanilla_conversion": 0,
    }

    def test_junk_worlds_supply_emblems_and_keep_checks(self):
        self.world_setup(seed=286779190)
        state = self.multiworld.get_all_state(False)
        for milestone in (30, 60, 90):
            with self.subTest(milestone=milestone):
                self.assertTrue(self.multiworld.get_location(
                    f"Lucky Emblems - Milestone {milestone}", self.player).can_reach(state))
        for world_name in ("Toy Box", "Kingdom of Corona", "San Fransokyo", "100 Acre Wood"):
            with self.subTest(world=world_name):
                region = self.multiworld.get_region(world_name, self.player)
                self.assertTrue(region.can_reach(state))
                self.assertTrue(any(self.get_location_base_region(location) is region
                                    for location in self.multiworld.get_locations(self.player)))
                self.assertTrue(any(row["world"] == world_name for row in self.world.active_locations))

    def test_junk_world_emblems_still_require_portal_access(self):
        self.world_setup(seed=286779190)
        state = self.multiworld.get_all_state(False)
        while state.has("Favorite Deputy", self.player):
            state.remove(self.world.create_item("Favorite Deputy"))
        self.assertFalse(self.multiworld.get_region("Toy Box", self.player).can_reach(state))
        self.assertFalse(self.multiworld.get_location("Lucky Emblems - Milestone 90", self.player).can_reach(state))
        state.collect(self.world.create_item("Favorite Deputy"))
        self.assertTrue(self.multiworld.get_location("Lucky Emblems - Milestone 90", self.player).can_reach(state))

    def test_frozen_and_pooh_cannot_supply_five_emblems_before_pole_spin(self):
        self.options = {**self.options, "default_base_abilities": 0, "lucky_emblems_per_check": 1}
        self.world_setup(seed=286779190)
        state = self.multiworld.get_all_state(False)
        # Isolate the reported route while retaining the portal prerequisite items.
        for entrance in self.multiworld.get_region("Garden of Assemblage", self.player).exits:
            if entrance.name not in ("To Arendelle", "To 100 Acre Wood", "To Lucky Emblems"):
                entrance.access_rule = lambda state: False
        while state.has("Ability: Pole Spin", self.player):
            state.remove(self.world.create_item("Ability: Pole Spin"))
        self.reset_reachability(state)

        self.assertTrue(self.multiworld.get_region("Arendelle", self.player).can_reach(state))
        self.assertTrue(self.multiworld.get_region("100 Acre Wood", self.player).can_reach(state))
        self.assertTrue(self.multiworld.get_location("Lucky Emblems - Milestone 3", self.player).can_reach(state))
        # Three Pooh emblems plus the Middle Tier Bridge emblem inside the labyrinth.
        self.assertTrue(self.multiworld.get_location("Lucky Emblems - Milestone 4", self.player).can_reach(state))
        self.assertFalse(self.multiworld.get_location("Lucky Emblems - Milestone 5", self.player).can_reach(state))

        state.collect(self.world.create_item("Ability: Pole Spin"))
        self.assertTrue(self.multiworld.get_location("Lucky Emblems - Milestone 14", self.player).can_reach(state))
        self.assertFalse(self.multiworld.get_location("Lucky Emblems - Milestone 15", self.player).can_reach(state))
