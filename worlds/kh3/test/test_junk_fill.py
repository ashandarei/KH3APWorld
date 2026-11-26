from unittest import TestCase

from BaseClasses import LocationProgressType
from Fill import distribute_items_restrictive
from Options import OptionError
from test.general import gen_steps, setup_multiworld
from worlds.AutoWorld import call_all

from .. import KH3World
from ..Options import KH3LocationPoolMode
from ..Rules import is_junk_item


class TestJunkFill(TestCase):
    def test_solo_start_needs_a_reachable_progression_check(self):
        options = dict.fromkeys((
            "olympus_pool", "arendelle_pool", "kingdom_of_corona_pool",
            "san_fransokyo_pool", "monstropolis_pool", "toy_box_pool",
        ), "junk")
        for seed in (2026112903, 2026112904):
            with self.subTest(seed=seed):
                multiworld = setup_multiworld(KH3World, seed=seed, options=options)
                with self.assertRaisesRegex(OptionError, "further progression from the starting checks.*olympus_pool"):
                    distribute_items_restrictive(multiworld)

        # With the Caribbean portal in inventory, a movement ability from a
        # level-up can open the Caribbean even though level-ups forbid portals.
        multiworld = setup_multiworld(KH3World, seed=2026112904, options=options)
        multiworld.push_precollected(multiworld.worlds[1].create_item("Wheel of Fate"))
        distribute_items_restrictive(multiworld)
        self.assertTrue(multiworld.fulfills_accessibility())

        options["olympus_pool"] = "randomize"
        multiworld = setup_multiworld(KH3World, seed=2026112903, options=options)
        distribute_items_restrictive(multiworld)
        self.assertTrue(multiworld.fulfills_accessibility())

    def test_starting_portal_opens_the_only_eligible_progression_check(self):
        check = "Chest 1 (Large Chest, Monsters, Inc.: Lobby & Offices, Before Save Point)"
        excluded = sorted(set(KH3World.location_name_to_id) - {check})
        for mode in ("keyblades", "party_members", "mixed"):
            with self.subTest(mode=mode):
                multiworld = setup_multiworld(
                    KH3World, steps=("generate_early", "create_regions", "create_items"), seed=103,
                    options={"portal_unlocks": mode, "exclude_locations": excluded},
                )
                world = multiworld.worlds[1]
                self.assertIn(world.portal_unlock_items["Monstropolis"], world.kh3_starting_inventory_items)
                self.assertTrue(multiworld.get_location(check, 1).can_reach(multiworld.state))

    def test_junk_checks_take_filler_without_granting_pool_items(self):
        multiworld = setup_multiworld(
            KH3World, steps=gen_steps[:-1], seed=20261124,
            options={"data_battle_rewards_pool": "junk", "battlegates_pool": "junk"},
        )
        pool = list(multiworld.itempool)
        starting = list(multiworld.precollected_items[1])
        call_all(multiworld, "pre_fill")
        self.assertEqual([id(item) for item in multiworld.itempool], [id(item) for item in pool])
        self.assertEqual(multiworld.precollected_items[1], starting)

        distribute_items_restrictive(multiworld)
        junk = [location for location in multiworld.get_locations(1) if location.filler_only]
        self.assertTrue(junk)
        for location in junk:
            self.assertEqual(location.progress_type, LocationProgressType.EXCLUDED)
            self.assertTrue(is_junk_item(location.item), location.name)
        self.assertTrue(multiworld.fulfills_accessibility())

    def test_all_junk_needs_enough_eligible_checks_in_the_room(self):
        options = {
            name: "junk" for name, kind in KH3World.options_dataclass.type_hints.items()
            if issubclass(kind, KH3LocationPoolMode) and "junk" in kind.options
        }
        multiworld = setup_multiworld(KH3World, seed=20261124, options=options)
        with self.assertRaisesRegex(OptionError, r"room has \d+ unfilled junk-only checks.*Free up at least \d+.*_pool"):
            distribute_items_restrictive(multiworld, panic_method="swap")

        multiworld = setup_multiworld([KH3World, KH3World], seed=20261124, options=[options, {}])
        distribute_items_restrictive(multiworld, panic_method="swap")
        self.assertTrue(multiworld.fulfills_accessibility())
        self.assertTrue(any(
            location.item.player == 1 and not is_junk_item(location.item)
            for location in multiworld.get_filled_locations(2)
        ))

    def test_only_fixed_ultima_weapon_is_exempt_from_unreality_junk(self):
        for mode in ("yozora", "shards"):
            with self.subTest(mode=mode):
                multiworld = setup_multiworld(
                    KH3World, seed=20261124,
                    options={"ultima_weapon_mode": mode, "unreality_pool": "junk"},
                )
                distribute_items_restrictive(multiworld)
                checks = [loc for loc in multiworld.get_locations(1) if loc.name.startswith("Unreality - ")]
                self.assertGreater(len(checks), 1)
                reward = multiworld.get_location("Unreality - EVENT_YOZORA_001 - RandomizedItem", 1)
                for check in checks:
                    if check is reward and mode == "yozora":
                        self.assertTrue(check.locked)
                        self.assertEqual(check.item.name, "Ultima Weapon")
                        self.assertFalse(check.filler_only)
                    else:
                        self.assertTrue(check.filler_only, check.name)
                        self.assertTrue(is_junk_item(check.item), check.name)

    def test_explicit_exclusion_of_fixed_yozora_reward_fails_early(self):
        with self.assertRaisesRegex(OptionError, "ultima_weapon_mode=yozora.*exclude_locations"):
            setup_multiworld(
                KH3World, steps=("generate_early",), seed=20261124,
                options={"ultima_weapon_mode": "yozora",
                         "exclude_locations": ["Unreality - EVENT_YOZORA_001 - RandomizedItem"]},
            )
