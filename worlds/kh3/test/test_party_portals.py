from BaseClasses import CollectionState, ItemClassification

from ..Data import (
    LEVEL_UP_XP_SOURCE_REGION_NAMES, PORTAL_KEYBLADE_ITEM_NAMES, PORTAL_KEYBLADE_REGION_LOCKS, PORTAL_PARTY_ITEM_NAMES,
    PORTAL_PARTY_REGION_LOCKS, STARTING_PORTAL_KEYBLADE_ITEM_NAMES, WORLD_DIFFICULTY_TIERS,
)
from . import KH3TestBase


class TestPartyPortals(KH3TestBase):
    options = {"portal_unlocks": "party_members"}

    def empty_state(self):
        state = CollectionState(self.multiworld)
        for item in self.multiworld.precollected_items[self.player]:
            state.remove(item)
        return state

    def test_designated_character_required_and_keyblade_ignored(self):
        for region, required, companion, keyblade in (
            ("Toy Box", "Woody", "Buzz Lightyear", "Favorite Deputy"),
            ("Kingdom of Corona", "Rapunzel", "Flynn Rider", "Ever After"),
            ("Monstropolis", "Sulley", "Mike", "Happy Gear"),
        ):
            with self.subTest(region=region):
                state = self.empty_state()
                entrance = self.multiworld.get_entrance("To " + region, self.player)
                self.assertFalse(entrance.access_rule(state))
                state.collect(self.world.create_item(keyblade))
                state.collect(self.world.create_item("Party Member: " + companion))
                self.assertFalse(entrance.access_rule(state))
                state.collect(self.world.create_item("Party Member: " + required))
                self.assertTrue(entrance.access_rule(state))
                self.assertEqual(PORTAL_PARTY_REGION_LOCKS[region], ("Party Member: " + required,))

    def test_tier_count_counts_worlds_instead_of_characters(self):
        state = self.empty_state()
        for name in ("Woody", "Buzz Lightyear", "Hercules", "Riku"):
            state.collect(self.world.create_item("Party Member: " + name))
        entrance = self.multiworld.get_entrance("To Data Battles", self.player)
        self.assertFalse(entrance.access_rule(state))
        state.collect(self.world.create_item("Party Member: Mike"))
        self.assertFalse(entrance.access_rule(state))
        state.collect(self.world.create_item("Party Member: Sulley"))
        self.assertTrue(entrance.access_rule(state))

    def test_tier_two_story_worlds_require_designated_party_member_and_entrance_unlocks(self):
        for region_name, member in (("Keyblade Graveyard", "Ventus"),
                                    ("Dark World", "Aqua"), ("Re Mind", "Kairi")):
            with self.subTest(region=region_name):
                state = self.empty_state()
                entrance = self.multiworld.get_entrance("To " + region_name, self.player)
                self.assertFalse(entrance.access_rule(state))
                state.collect(self.world.create_item("Party Member: " + member))
                if region_name == "Keyblade Graveyard":
                    self.assertFalse(entrance.access_rule(state))
                    state.collect(self.world.create_item("Unlock: Attraction Flow"))
                self.assertTrue(entrance.access_rule(state))

    def test_starter_and_progression_and_export(self):
        starters = [name for name in self.world.kh3_starting_inventory_items if name in PORTAL_PARTY_ITEM_NAMES]
        self.assertEqual(len(starters), 1)
        self.assertIn(starters[0], PORTAL_PARTY_ITEM_NAMES)
        self.assertNotEqual(starters[0], "Party Member: Captain Jack Sparrow")
        self.assertTrue(any(self.multiworld.get_entrance("To " + region, self.player).can_reach(
            self.multiworld.state) for region in ("Olympus", "Toy Box", "Kingdom of Corona",
                                                 "Monstropolis", "Arendelle", "San Fransokyo")))
        for name in PORTAL_PARTY_ITEM_NAMES:
            self.assertEqual(self.world.create_item(name).classification, ItemClassification.progression)
        for name in ("Buzz Lightyear", "Flynn Rider", "Mike"):
            item_name = "Party Member: " + name
            self.assertNotIn(item_name, PORTAL_PARTY_ITEM_NAMES)
            self.assertEqual(self.world.create_item(item_name).classification, ItemClassification.useful)
        slot = self.world.build_runtime_slot_data()
        self.assertEqual(slot["portal_unlocks"], 1)
        self.assertEqual({name for name in slot if name.startswith("portal_")}, {"portal_unlocks"})
        self.assertEqual({name for name in self.world.options_dataclass.type_hints
                          if name.startswith("portal_")}, {"portal_unlocks"})

    def test_level_locations_cannot_hold_party_gates(self):
        level = next(location for location in self.multiworld.get_locations(self.player)
                     if self.get_location_base_region(location).name == "Level Ups")
        for name in PORTAL_PARTY_ITEM_NAMES:
            self.assertFalse(level.item_rule(self.world.create_item(name)))

    def test_level_entrance_uses_party_unlocks(self):
        entrance = self.multiworld.get_entrance("To Level Ups", self.player)
        state = self.empty_state()
        state.collect(self.world.create_item("Favorite Deputy"))
        self.assertFalse(entrance.access_rule(state))
        for name in ("Buzz Lightyear", "Flynn Rider", "Mike"):
            state.collect(self.world.create_item("Party Member: " + name))
        self.assertFalse(entrance.access_rule(state))
        state.collect(self.world.create_item("Party Member: Woody"))
        self.assertTrue(entrance.access_rule(state))

    def test_arendelle_uses_marshmallow(self):
        entrance = self.multiworld.get_entrance("To Arendelle", self.player)
        state = self.empty_state()
        state.collect(self.world.create_item("Party Member: Xion"))
        self.assertFalse(entrance.access_rule(state))
        state.collect(self.world.create_item("Party Member: Marshmallow"))
        self.assertTrue(entrance.access_rule(state))

    def test_twilight_town_requires_roxas_instead_of_lea(self):
        entrance = self.multiworld.get_entrance("To Twilight Town", self.player)
        state = self.empty_state()
        state.collect(self.world.create_item("Ability: Block"))
        state.add_item("Party Member: Lea", self.player)
        self.assertFalse(entrance.access_rule(state))
        state.collect(self.world.create_item("Party Member: Roxas"))
        self.assertTrue(entrance.access_rule(state))

    def test_data_battles_requires_riku_instead_of_mickey(self):
        entrance = self.multiworld.get_entrance("To Data Battles", self.player)
        state = self.empty_state()
        state.collect(self.world.create_item("Party Member: Riku"))
        self.assertFalse(entrance.access_rule(state))
        state = self.empty_state()
        for name in ("Hercules", "Woody", "Sulley"):
            state.collect(self.world.create_item("Party Member: " + name))
        state.add_item("Party Member: Mickey", self.player)
        self.assertFalse(entrance.access_rule(state))
        state.collect(self.world.create_item("Party Member: Riku"))
        self.assertTrue(entrance.access_rule(state))

    def test_unreality_is_open_without_party_or_tier_unlocks(self):
        entrance = self.multiworld.get_entrance("To Unreality", self.player)
        self.assertTrue(entrance.access_rule(self.empty_state()))


class TestMixedPortals(KH3TestBase):
    options = {"portal_unlocks": "mixed", "chestsanity": 1, "kh02_enabled": 1}

    def world_setup(self, seed=462):
        super().world_setup(seed)

    def test_only_the_rolled_item_opens_each_portal(self):
        unlocks = self.world.portal_unlock_items
        self.assertTrue(PORTAL_KEYBLADE_ITEM_NAMES.intersection(unlocks.values()))
        self.assertTrue(PORTAL_PARTY_ITEM_NAMES.intersection(unlocks.values()))
        self.assertEqual(unlocks["Data Battles"], unlocks["Radiant Garden"])
        tier_one = [unlocks[name] for name in sorted(WORLD_DIFFICULTY_TIERS[1]) if name in unlocks]
        for region, selected in {**unlocks, "Kingdom Hearts 0.2": unlocks["Dark World"]}.items():
            if region == "Radiant Garden":
                continue  # Data Battles is the AP entrance for this physical portal.
            with self.subTest(region=region):
                state = CollectionState(self.multiworld)
                for item in self.multiworld.precollected_items[self.player]:
                    state.remove(item)
                for item in ("Ability: Dodge Roll", "Unlock: Attraction Flow"):
                    state.add_item(item, self.player)
                world = "Dark World" if region == "Kingdom Hearts 0.2" else region
                blade = PORTAL_KEYBLADE_REGION_LOCKS[world]
                friend, = PORTAL_PARTY_REGION_LOCKS[world]
                self.assertIn(selected, (blade, friend))
                state.add_item(friend if selected == blade else blade, self.player)
                entrance = self.multiworld.get_entrance("To " + region, self.player)
                if region == "Data Battles":
                    for item in tier_one[:2]:
                        state.add_item(item, self.player)
                    state.add_item(selected, self.player)
                    self.assertFalse(entrance.access_rule(state))
                    state.remove_item(selected, self.player)
                    state.add_item(tier_one[2], self.player)
                self.assertFalse(entrance.access_rule(state))
                state.add_item(selected, self.player)
                self.assertTrue(entrance.access_rule(state))

        starters = {unlocks[region] for region, blade in PORTAL_KEYBLADE_REGION_LOCKS.items()
                    if blade in STARTING_PORTAL_KEYBLADE_ITEM_NAMES}
        self.assertEqual(len(starters.intersection(self.world.kh3_starting_inventory_items)), 1)
        level = next(loc for loc in self.multiworld.get_locations(self.player)
                     if self.get_location_base_region(loc).name == "Level Ups")
        for name in PORTAL_PARTY_ITEM_NAMES:
            item = self.world.create_item(name)
            self.assertEqual(item.advancement, name in unlocks.values())
            if item.advancement:
                self.assertFalse(level.item_rule(item))

    def test_level_ups_require_an_assigned_xp_world_unlock(self):
        entrance = self.multiworld.get_entrance("To Level Ups", self.player)
        state = CollectionState(self.multiworld)
        for item in self.multiworld.precollected_items[self.player]:
            state.remove(item)
        for region in LEVEL_UP_XP_SOURCE_REGION_NAMES:
            blade = PORTAL_KEYBLADE_REGION_LOCKS[region]
            friend, = PORTAL_PARTY_REGION_LOCKS[region]
            state.add_item(friend if self.world.portal_unlock_items[region] == blade else blade, self.player)
        self.assertFalse(entrance.access_rule(state))
        for region in LEVEL_UP_XP_SOURCE_REGION_NAMES:
            selected = self.world.portal_unlock_items[region]
            state.add_item(selected, self.player)
            self.assertTrue(entrance.access_rule(state), region)
            state.remove_item(selected, self.player)

    def test_server_tracker_hints_and_chests_share_the_roll(self):
        from NetUtils import convert_to_base_types
        from ..SlotData import decode_slot_data
        from .test_universal_tracker import TestKH3UniversalTracker

        slot = decode_slot_data(convert_to_base_types(self.world.fill_slot_data()))
        unlocks = slot["mixed_portal_unlocks"]
        self.assertEqual(unlocks, self.world.portal_unlock_items)
        hints = set(slot["kh3_randomizer"]["hint"]["checks"])
        self.assertEqual(hints & (PORTAL_KEYBLADE_ITEM_NAMES | PORTAL_PARTY_ITEM_NAMES), set(unlocks.values()))
        chest_keys = {r["keyblade_chest_keyblade"] for r in slot["bridge"]["locations"] if "keyblade_chest_keyblade" in r}
        self.assertEqual(chest_keys, {"Classic Tone", "Grand Chef", "Hunny Spout"}
                         | PORTAL_KEYBLADE_ITEM_NAMES.difference(unlocks.values()))
        tracker = TestKH3UniversalTracker.build_yamlless_tracker_multiworld(self, slot).worlds[1]
        self.assertEqual(tracker.portal_unlock_items, unlocks)
        self.assertEqual(tracker.active_locations, self.world.active_locations)
        del unlocks["Olympus"]
        with self.assertRaisesRegex(ValueError, "mixed portal unlock"):
            TestKH3UniversalTracker.build_yamlless_tracker_multiworld(self, slot)
