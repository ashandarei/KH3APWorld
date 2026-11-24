from unittest import TestCase

from BaseClasses import LocationProgressType
from Fill import FillError, distribute_items_restrictive
from test.general import gen_steps, setup_multiworld
from worlds.AutoWorld import call_all

from .. import KH3World
from ..Options import KH3LocationPoolMode
from ..Rules import is_junk_item


class TestJunkFill(TestCase):
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
        with self.assertRaises(FillError):
            distribute_items_restrictive(multiworld, panic_method="swap")

        multiworld = setup_multiworld([KH3World, KH3World], seed=20261124, options=[options, {}])
        distribute_items_restrictive(multiworld, panic_method="swap")
        self.assertTrue(multiworld.fulfills_accessibility())
        self.assertTrue(any(
            location.item.player == 1 and not is_junk_item(location.item)
            for location in multiworld.get_filled_locations(2)
        ))

    def test_fixed_useful_reward_cannot_be_granted_from_a_junk_check(self):
        multiworld = setup_multiworld(
            KH3World, steps=gen_steps[:-1], seed=20261124,
            options={"ultima_weapon_mode": "yozora", "data_battle_rewards_pool": "junk"},
        )
        with self.assertRaisesRegex(FillError, "fixed reward.*Ultima Weapon"):
            call_all(multiworld, "pre_fill")
