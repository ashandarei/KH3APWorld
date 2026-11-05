from unittest import TestCase

from BaseClasses import Item, ItemClassification, Location, LocationProgressType, Region
from Fill import FillError, balance_multiworld_progression, distribute_items_restrictive
from test.general import TestWorld as ForeignWorld
from test.general import setup_multiworld
from worlds.AutoWorld import call_all
from worlds.generic.Rules import locality_rules

from .. import KH3World
from ..Locations import KH3Location
from ..StartingInventory import reserve_all_local_locations


class TestAllLocalFill(TestCase):
    def make_world(self, local_names=('Magic: Fire', 'Potion'), checks=2):
        mw = setup_multiworld([KH3World, ForeignWorld], steps=(), seed=927019)
        mw.worlds[1].options.local_items.value = set(KH3World.item_names)
        for player in mw.player_ids:
            region = Region('Menu', player, mw)
            mw.regions.append(region)
            for index in range(checks if player == 1 else 2):
                cls = KH3Location if player == 1 else Location
                region.locations.append(cls(player, f'Check {index}', index + 1, region))
            mw.completion_condition[player] = lambda state: True
        mw.itempool.extend(mw.worlds[1].create_item(name) for name in local_names)
        mw.itempool.extend(Item(name, ItemClassification.filler, index + 1, 2)
                           for index, name in enumerate(('Foreign early item', 'Foreign filler')))
        locality_rules(mw)
        return mw

    def test_full_generation_reserves_before_foreign_early_items_and_balancing(self):
        for junk in (False, True):
            with self.subTest(junk=junk):
                mw = self.make_world()
                for location in mw.get_locations(1):
                    location.filler_only = junk
                    if junk:
                        location.progress_type = LocationProgressType.EXCLUDED
                mw.early_items[2]['Foreign early item'] = 1
                items = {id(item) for item in mw.itempool}
                call_all(mw, 'pre_fill')
                distribute_items_restrictive(mw)
                call_all(mw, 'post_fill')
                balance_multiworld_progression(mw)
                call_all(mw, 'finalize_multiworld')
                self.assertFalse(mw.get_unfilled_locations())
                self.assertEqual(len(items), len(mw.get_locations()))
                self.assertEqual(sum(map(len, mw.precollected_items.values())), int(junk))
                for location in mw.get_locations():
                    self.assertEqual(location.player, location.item.player)
                    self.assertIs(location.item.location, location)
                self.assertTrue(mw.can_beat_game())
                self.assertTrue(mw.fulfills_accessibility())
                self.assertFalse(any(loc.item.advancement for loc in mw.get_locations(1) if loc.filler_only))

    def test_preserves_strict_rules_exclusions_and_foreign_locations(self):
        mw = self.make_world()
        normal, strict = mw.get_locations(1)
        strict.item_rule = lambda item: not item.advancement and not item.useful
        strict.progress_type = LocationProgressType.EXCLUDED
        foreign = {loc: loc.item_rule for loc in mw.get_locations(2)}
        self.assertEqual(reserve_all_local_locations(mw), {1: 2})
        fire, potion = mw.itempool[:2]
        self.assertTrue(normal.item_rule(fire))
        self.assertFalse(strict.item_rule(fire))
        self.assertTrue(strict.item_rule(potion))
        self.assertFalse(normal.item_rule(mw.itempool[-1]))
        self.assertEqual(strict.progress_type, LocationProgressType.EXCLUDED)
        self.assertTrue(all(loc.item_rule is rule for loc, rule in foreign.items()))

    def test_partial_local_settings_do_not_block_foreign_items(self):
        mw = self.make_world()
        mw.worlds[1].options.local_items.value = {'Magic: Fire'}
        before = [loc.item_rule for loc in mw.get_locations()]
        self.assertEqual(reserve_all_local_locations(mw), {})
        self.assertEqual(before, [loc.item_rule for loc in mw.get_locations()])
        self.assertTrue(next(iter(mw.get_locations(1))).item_rule(mw.itempool[-1]))

    def test_spare_capacity_stays_open_for_foreign_or_linked_items(self):
        mw = self.make_world(local_names=('Magic: Fire',))
        self.assertEqual(reserve_all_local_locations(mw), {})
        self.assertTrue(all(loc.item_rule(mw.itempool[-1]) for loc in mw.get_locations(1)))

    def test_already_placed_local_items_are_not_counted_twice(self):
        mw = self.make_world()
        placed = next(iter(mw.get_locations(1)))
        placed.place_locked_item(mw.itempool[0])
        old_rule = placed.item_rule
        # Some callers retain preplaced objects in the pool; others remove them.
        for retained in (True, False):
            with self.subTest(retained=retained):
                if not retained:
                    mw.itempool.remove(placed.item)
                self.assertEqual(reserve_all_local_locations(mw), {1: 1})
                self.assertIs(placed.item_rule, old_rule)
                self.assertIs(placed.item.location, placed)

    def test_conflicting_import_reports_insufficient_capacity_without_rule_changes(self):
        mw = self.make_world()
        next(iter(mw.get_locations(1))).place_locked_item(mw.itempool[-1])
        before = [loc.item_rule for loc in mw.get_locations()]
        with self.assertRaisesRegex(FillError, 'needs 2 locations.*only 1 unfilled'):
            reserve_all_local_locations(mw)
        self.assertEqual(before, [loc.item_rule for loc in mw.get_locations()])

    def test_shared_rule_identity_is_preserved_for_capacity_matching(self):
        mw = self.make_world()
        normal, other = mw.get_locations(1)
        self.assertIs(normal.item_rule, other.item_rule)
        reserve_all_local_locations(mw)
        self.assertIs(normal.item_rule, other.item_rule)
