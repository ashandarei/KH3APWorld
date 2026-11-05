from unittest import TestCase

from BaseClasses import PlandoOptions
from Options import LocalItems, StartHints, StartLocationHints, PriorityLocations

from .. import KH3World
from ..Data import load_extracted_data, normalize_locations
from ..Locations import extra_vbonus_location_name


class TestNameGroups(TestCase):
    def test_integrated_kh02_checks_have_their_own_groups(self):
        groups = KH3World.location_name_groups
        kh02 = groups["Kingdom Hearts 0.2"]
        self.assertEqual(len(kh02), 99)
        self.assertEqual(kh02, groups["0.2 Chests"] | groups["0.2 Bosses"] | groups["0.2 Challenges"])
        for group, names in groups.items():
            if group != "Kingdom Hearts 0.2" and not group.startswith("0.2 "):
                self.assertTrue(kh02.isdisjoint(names), group)
        exported = KH3World.get_data_package_data()["location_name_groups"]
        for catalog in (groups, exported):
            self.assertNotIn("Everywhere", catalog)
            self.assertNotIn("Synth/Shop Items", catalog)

    def test_group_cleanup_preserves_individual_catalog_selection(self):
        data = KH3World.get_data_package_data()
        rows = normalize_locations(load_extracted_data())
        excluded_locations = {
            row["name"] for row in rows
            if row["type"] == "synthesis"
            or (row["type"] == "ingredient" and row.get("ap_enabled") is False)
        }
        self.assertEqual(len(excluded_locations), 75)
        for names in data["location_name_groups"].values():
            self.assertTrue(excluded_locations.isdisjoint(names))
        self.assertTrue(excluded_locations.issubset(data["location_name_to_id"]))
        workshop = StartLocationHints.from_any(["Moogle Workshop"])
        workshop.verify(KH3World, "Group cleanup", PlandoOptions.none)
        self.assertEqual(len(workshop.value), 39)
        self.assertIn("Moogle Workshop - Ever After Level 4", workshop.value)
        self.assertTrue(any("Photo Mission" in name for name in workshop.value))

        excluded_abilities = {
            f"Ability: {name}" for name in
            ("Aeroza", "Blizzaza", "Curaza", "Firaza", "Thundaza", "Waterza")
        }
        for names in data["item_name_groups"].values():
            self.assertTrue(excluded_abilities.isdisjoint(names))
        hints = StartHints.from_any(excluded_abilities)
        hints.verify(KH3World, "Group cleanup", PlandoOptions.none)
        self.assertEqual(hints.value, excluded_abilities)

        individual = StartLocationHints.from_any(excluded_locations)
        individual.verify(KH3World, "Group cleanup", PlandoOptions.none)
        self.assertEqual(individual.value, excluded_locations)

    def test_every_group_member_is_registered(self):
        for groups, catalog in (
            (KH3World.item_name_groups, KH3World.item_name_to_id),
            (KH3World.location_name_groups, KH3World.location_name_to_id),
        ):
            for group, names in groups.items():
                with self.subTest(group=group):
                    self.assertTrue(names)
                    self.assertFalse(set(names) - catalog.keys())

    def test_cups_partition_and_postgame_boundary(self):
        groups = KH3World.location_name_groups
        normal, postgame = groups["Normal Cups"], groups["Postgame Cups"]
        self.assertEqual((len(normal), len(postgame)), (15, 9))
        self.assertFalse(normal & postgame)
        self.assertEqual(normal | postgame, groups["Cups"])
        self.assertIn("Olympus Coliseum - KH1 - Gold Match Clear", postgame)
        self.assertIn("Olympus Coliseum - KH2 - Titan Cup Clear", normal)

    def test_world_and_story_reward_membership(self):
        groups = KH3World.location_name_groups
        boss = "Defeat King of Toys in Toy Box: Galaxy Toys"
        for name in (boss, extra_vbonus_location_name(boss, 2), extra_vbonus_location_name(boss, 3)):
            self.assertIn(name, groups["Toy Box"])
            self.assertIn(name, groups["Story Bosses"])
        self.assertIn("Toy Box - Melody Chest", groups["Toy Box"])
        self.assertIn(
            "Ingredients+ - San Fransokyo - Pickup 001",
            groups["San Fransokyo"],
        )
        self.assertNotIn("Toy Box - Verum Rex - A Rank", groups["Story Bosses"])
        self.assertNotIn("Defeat Armored Xehanort in Re:Mind", groups["Story Bosses"])

    def test_designed_run_options_expand_groups(self):
        for option_type, group, expected in (
            (PriorityLocations, "Normal Cups", KH3World.location_name_groups["Normal Cups"]),
            (StartLocationHints, "Postgame Cups", KH3World.location_name_groups["Postgame Cups"]),
            (LocalItems, "Consumables", KH3World.item_name_groups["Consumables"]),
            (StartHints, "Reports", {f"Secret Report {n}" for n in range(1, 14)}),
            (StartHints, "Magic", {f"Magic: {n}" for n in ("Fire", "Blizzard", "Thunder", "Water", "Aero", "Cure")}),
            (StartHints, "Heartbinders", {
                "Dream Heartbinder", "Pixel Heartbinder", "Pride Heartbinder",
                "'Ohana Heartbinder", "Ocean Heartbinder",
            }),
            (PriorityLocations, "Heartbinders", {
                "Toy Box - EVENT_HEARTBINDER_002 - RandomizedItem",
                "Monstropolis - EVENT_HEARTBINDER_003 - RandomizedItem",
                "San Fransokyo - EVENT_HEARTBINDER_004 - RandomizedItem",
                "Chest 15 (Large Chest, Huddled Isles: Undersea Cavern, Lightning Angler's Lair)",
            }),
        ):
            with self.subTest(option=option_type.__name__, group=group):
                option = option_type.from_any([group])
                option.verify(KH3World, "Designed Run", PlandoOptions.none)
                self.assertEqual(option.value, set(expected))
        self.assertNotIn("Munny Pouch", KH3World.item_name_groups["Consumables"])
        self.assertNotIn("AP Boost", KH3World.item_name_groups["Consumables"])
