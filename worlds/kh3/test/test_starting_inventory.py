from unittest import TestCase
from unittest.mock import patch

import Fill as CoreFill
from BaseClasses import Item, ItemClassification, LocationProgressType, Region
from test.general import TestWorld as ForeignWorld
from test.general import setup_multiworld
from worlds.AutoWorld import call_all

from .. import KH3World
from ..Locations import KH3Location
from ..Options import KH3LocationPoolMode, KH3Options
from ..Rules import is_junk_item
from ..Space import SHIPS_BY_ZONE, ZONE_UNLOCK_BLUEPRINTS
from ..StartingInventory import precollect_overflow, precollect_restrictive_starts, register_post_fill_classifications


class TestStartingInventoryOverflow(TestCase):
    def build(self, normal_count, foreign=False):
        mw = setup_multiworld([KH3World, ForeignWorld] if foreign else [KH3World], steps=(), seed=731)
        menu = Region('Menu', 1, mw)
        mw.regions.append(menu)
        for index in range(2):
            location = KH3Location(1, f'Check {index}', index + 1, menu)
            if index >= normal_count:
                location.filler_only = True
                location.progress_type = LocationProgressType.EXCLUDED
                location.item_rule = is_junk_item
            menu.locations.append(location)
        mw.completion_condition[1] = lambda state: True
        mw.itempool = [mw.worlds[1].create_item("Hero's Origin"), mw.worlds[1].create_item('Bonus: HP +5')]
        return mw

    def test_all_junk_grants_both_classes_preserving_identity_and_pool_size(self):
        mw = self.build(0)
        originals = mw.itempool.copy()
        report = precollect_overflow(mw)[1]
        self.assertEqual((report['progression_granted'], report['useful_granted']), (1, 1))
        self.assertEqual(mw.precollected_items[1], originals[::-1])
        self.assertEqual(len(mw.itempool), 2)
        self.assertTrue(all(is_junk_item(item) for item in mw.itempool))
        CoreFill.distribute_items_restrictive(mw)
        self.assertFalse(mw.get_unfilled_locations())

    def test_partial_capacity_preserves_progression_in_pool(self):
        mw = self.build(1)
        progression, useful = mw.itempool
        report = precollect_overflow(mw)[1]
        self.assertEqual(report['pool_overflow_granted'], 1)
        self.assertIs(mw.itempool[0], progression)
        self.assertIn(useful, mw.precollected_items[1])

    def test_optional_useful_check_does_not_inflate_progression_capacity(self):
        mw = self.build(2)
        world = mw.worlds[1]
        optional = mw.get_location('Check 1', 1)
        optional.progression_allowed = False
        optional.item_rule = lambda item: not item.advancement
        mw.itempool = [world.create_item(name) for name in ("Hero's Origin", 'Magic: Fire')]
        original = mw.itempool.copy()
        report = precollect_overflow(mw)[1]
        self.assertEqual((report['normal_slots'], report['progression_slots']), (2, 1))
        self.assertEqual(report['progression_granted'], 1)
        self.assertIn(original[0], mw.precollected_items[1])
        CoreFill.distribute_items_restrictive(mw)
        self.assertFalse(mw.get_unfilled_locations())
        self.assertFalse(optional.item.advancement)

    def test_level_and_optional_restrictions_share_the_progression_budget(self):
        mw = self.build(2)
        world = mw.worlds[1]
        optional = mw.get_location('Check 0', 1)
        optional.progression_allowed = False
        optional.item_rule = lambda item: not item.advancement
        world.active_locations = [{'name': 'Check 1', 'source_table': 'LevelUp'}]
        world.level_up_forbidden_item_names = frozenset({"Hero's Origin"})
        mw.get_location('Check 1', 1).item_rule = lambda item: item.name != "Hero's Origin"
        mw.itempool = [world.create_item(name) for name in ("Hero's Origin", 'Magic: Fire')]
        report = precollect_overflow(mw)[1]
        self.assertEqual(report['progression_granted'], 1)
        self.assertEqual([item.name for item in mw.precollected_items[1]], ["Hero's Origin"])
        CoreFill.distribute_items_restrictive(mw)
        self.assertFalse(mw.get_unfilled_locations())

    def test_no_shortage_makes_no_grants_and_consumes_no_rng(self):
        mw = self.build(2)
        before = mw.worlds[1].random.getstate()
        with patch.object(mw.worlds[1], 'create_filler', side_effect=AssertionError('Unneeded filler')):
            report = precollect_overflow(mw)[1]
        self.assertEqual(report['pool_overflow_granted'], 0)
        self.assertEqual(before, mw.worlds[1].random.getstate())

    def test_foreign_post_fill_promotion_cannot_enter_junk_or_optional_progression(self):
        mw = self.build(1, foreign=True)
        future_useful = Item('Future useful', ItemClassification.filler, 10, 2)
        future_progression = Item('Future progression', ItemClassification.useful, 11, 2)
        mw.worlds[2].classifications_to_override_post_fill = [
            (future_useful, ItemClassification.useful),
            (future_progression, ItemClassification.progression_skip_balancing)]
        mw.get_location('Check 0', 1).item_rule = lambda item: not item.advancement
        register_post_fill_classifications(mw)
        self.assertEqual(future_useful.classification, ItemClassification.filler)
        self.assertEqual(future_progression.classification, ItemClassification.useful)
        self.assertFalse(mw.get_location('Check 1', 1).item_rule(future_useful))
        self.assertFalse(mw.get_location('Check 1', 1).item_rule(future_progression))
        self.assertTrue(mw.get_location('Check 0', 1).item_rule(future_useful))
        self.assertFalse(mw.get_location('Check 0', 1).item_rule(future_progression))

    def test_declared_promotion_preserves_foreign_excluded_item_rules(self):
        from worlds.generic.Rules import exclusion_rules
        mw = self.build(2, foreign=True)
        menu = Region('Menu', 2, mw)
        mw.regions.append(menu)
        optional = KH3Location(2, 'Foreign excluded', 5, menu)
        menu.locations.append(optional)
        exclusion_rules(mw, 2, {'Foreign excluded'})
        item = Item('Future useful', ItemClassification.filler, 10, 2)
        mw.worlds[2].classifications_to_override_post_fill = [(item, ItemClassification.useful)]
        original_rule = optional.item_rule
        self.assertTrue(optional.item_rule(item))
        register_post_fill_classifications(mw)
        self.assertIs(optional.item_rule, original_rule)
        self.assertTrue(optional.item_rule(item))
        self.assertEqual(item.classification, ItemClassification.filler)

    def test_second_call_does_not_repeat_grants(self):
        mw = self.build(0)
        first = precollect_overflow(mw)
        items = mw.itempool.copy()
        starts = mw.precollected_items[1].copy()
        self.assertIs(precollect_overflow(mw), first)
        self.assertEqual(mw.itempool, items)
        self.assertEqual(mw.precollected_items[1], starts)

    def test_fixed_junk_reward_moves_to_start_but_victory_event_stays(self):
        mw = self.build(0)
        location = mw.get_location('Check 0', 1)
        fixed = mw.worlds[1].create_item('Bonus: MP +10')
        location.place_locked_item(fixed)
        event = KH3Location(1, 'Victory event', None, location.parent_region)
        event.progress_type = LocationProgressType.EXCLUDED
        victory = mw.worlds[1].create_event_item('Victory')
        event.place_locked_item(victory)
        location.parent_region.locations.append(event)
        report = precollect_overflow(mw)[1]
        self.assertEqual(report['fixed_junk_rewards_granted'], ['Bonus: MP +10'])
        self.assertIn(fixed, mw.precollected_items[1])
        self.assertIsNone(fixed.location)
        self.assertIs(event.item, victory)
        self.assertTrue(location.locked)
        self.assertTrue(is_junk_item(location.item))
        self.assertIs(location.item.location, location)

    def test_foreign_items_and_worlds_are_not_granted_or_replaced(self):
        mw = self.build(0, foreign=True)
        item = Item('Foreign progression', ItemClassification.progression, 1, 2)
        mw.itempool.append(item)
        precollect_overflow(mw)
        self.assertIn(item, mw.itempool)
        self.assertFalse(mw.precollected_items[2])

    def test_fixed_foreign_progression_in_junk_is_rejected_without_granting_it(self):
        mw = self.build(0, foreign=True)
        foreign = Item('Foreign progression', ItemClassification.progression, 1, 2)
        location = mw.get_location('Check 0', 1)
        location.place_locked_item(foreign)
        with self.assertRaisesRegex(CoreFill.FillError, 'fixed foreign non-filler'):
            precollect_overflow(mw)
        self.assertIs(location.item, foreign)
        self.assertIs(foreign.location, location)
        self.assertFalse(mw.precollected_items[2])

    def test_preplaced_normal_reward_reduces_remaining_capacity(self):
        mw = self.build(2)
        placed = mw.worlds[1].create_filler()
        mw.get_location('Check 0', 1).place_locked_item(placed)
        self.assertEqual(precollect_overflow(mw)[1]['normal_slots'], 1)
        self.assertIs(mw.get_location('Check 0', 1).item, placed)

    def test_core_fill_and_balancer_are_not_replaced(self):
        mw = self.build(0)
        before = (CoreFill.fill_restrictive, CoreFill.remaining_fill, CoreFill.balance_multiworld_progression)
        precollect_overflow(mw)
        self.assertEqual(before, (CoreFill.fill_restrictive, CoreFill.remaining_fill,
                                  CoreFill.balance_multiworld_progression))

    def test_early_filler_leaves_room_for_every_excluded_check(self):
        mw = self.build(1)
        world = mw.worlds[1]
        progression = mw.itempool[0]
        mw.itempool[1] = world.create_item('Potion')
        mw.early_items[1]['Potion'] = 1
        report = precollect_overflow(mw)[1]
        self.assertEqual(report['early_filler_reserved'], 1)
        self.assertIn(progression, mw.precollected_items[1])
        CoreFill.distribute_items_restrictive(mw)
        self.assertFalse(mw.get_unfilled_locations())
        self.assertTrue(all(is_junk_item(loc.item) for loc in mw.get_locations()))

    def test_absent_early_filler_does_not_reserve_a_slot(self):
        mw = self.build(2)
        mw.early_items[1]['Potion'] = 1
        self.assertNotIn('early_filler_reserved', precollect_overflow(mw)[1])
        self.assertFalse(mw.precollected_items[1])

    def test_level_rule_capacity_grants_only_excess_restricted_items(self):
        mw = self.build(2)
        world = mw.worlds[1]
        world.active_locations = [{'name': 'Check 1', 'source_table': 'LevelUp'}]
        world.level_up_forbidden_item_names = frozenset({"Hero's Origin", 'Proof of Times Past'})
        mw.get_location('Check 1', 1).item_rule = lambda item: item.name not in world.level_up_forbidden_item_names
        mw.itempool = [world.create_item(name) for name in sorted(world.level_up_forbidden_item_names)]
        report = precollect_overflow(mw)[1]
        self.assertEqual(report['level_rule_overflow_granted'], 1)
        self.assertEqual(report['progression_granted'], 1)
        CoreFill.distribute_items_restrictive(mw)
        self.assertFalse(mw.get_unfilled_locations())
        self.assertTrue(is_junk_item(mw.get_location('Check 1', 1).item))

    def test_empty_opening_grants_route_prerequisites_but_keeps_other_progression(self):
        mw = self.build(2)
        world = mw.worlds[1]
        menu = mw.get_region('Menu', 1)
        menu.locations.append(KH3Location(1, 'Check 2', 3, menu))
        world.active_locations = [{'name': f'Check {i}', 'world': 'Olympus', 'type': 'treasure'} for i in range(3)]
        for loc in mw.get_locations():
            loc.access_rule = lambda state: state.has_all(("Hero's Origin", 'Unlock: Magic'), 1)
        mw.itempool = [world.create_item(name) for name in ("Hero's Origin", 'Unlock: Magic', 'Magic: Thunder')]
        precollect_overflow(mw)
        precollect_restrictive_starts(mw)
        self.assertEqual({item.name for item in mw.precollected_items[1]}, {"Hero's Origin", 'Unlock: Magic'})
        self.assertTrue(any(item.name == 'Magic: Thunder' for item in mw.itempool))
        CoreFill.distribute_items_restrictive(mw)
        self.assertFalse(mw.get_unfilled_locations())
        self.assertTrue(mw.fulfills_accessibility())

    def test_open_levels_cannot_hide_a_portal_only_start_deadlock(self):
        mw = self.build(2)
        world = mw.worlds[1]
        world.active_locations = [{'name': 'Check 0', 'world': 'Olympus', 'type': 'treasure'},
                                  {'name': 'Check 1', 'source_table': 'LevelUp', 'world': 'Level Ups'}]
        world.level_up_forbidden_item_names = frozenset({"Hero's Origin"})
        mw.get_location('Check 0', 1).access_rule = lambda state: state.has("Hero's Origin", 1)
        mw.get_location('Check 1', 1).item_rule = lambda item: item.name != "Hero's Origin"
        mw.itempool = [world.create_item(name) for name in ("Hero's Origin", 'Magic: Fire')]
        precollect_overflow(mw)
        precollect_restrictive_starts(mw)
        self.assertEqual([item.name for item in mw.precollected_items[1]], ["Hero's Origin"])
        CoreFill.distribute_items_restrictive(mw)
        self.assertFalse(mw.get_unfilled_locations())

    def test_early_filler_cannot_take_the_only_progression_opening(self):
        mw = self.build(2)
        world = mw.worlds[1]
        menu = mw.get_region('Menu', 1)
        menu.locations.append(KH3Location(1, 'Check 2', 3, menu))
        world.active_locations = [{'name': f'Check {i}', 'world': 'Olympus', 'type': 'treasure'} for i in range(3)]
        for index in (1, 2):
            mw.get_location(f'Check {index}', 1).access_rule = lambda state: state.has("Hero's Origin", 1)
        mw.itempool = [world.create_item(name) for name in ("Hero's Origin", 'Magic: Fire', 'Potion')]
        mw.early_items[1]['Potion'] = 1
        precollect_overflow(mw)
        precollect_restrictive_starts(mw)
        self.assertEqual([item.name for item in mw.precollected_items[1]], ["Hero's Origin"])
        CoreFill.distribute_items_restrictive(mw)
        self.assertFalse(mw.get_unfilled_locations())
        self.assertTrue(mw.fulfills_accessibility())

    def test_ship_unlock_can_start_from_a_level_check_without_a_free_ship(self):
        mw = self.build(2)
        world = mw.worlds[1]
        world.options.include_space.value = 1
        world.active_locations = [{'name': 'Check 0', 'type': 'space'},
                                  {'name': 'Check 1', 'type': 'level_up'}]
        mw.get_location('Check 0', 1).access_rule = lambda state: state.has_any(ZONE_UNLOCK_BLUEPRINTS, 1)
        ship = world.create_item(sorted(SHIPS_BY_ZONE['Starlight Way'])[0])
        mw.itempool = [ship, world.create_item('Potion')]
        precollect_overflow(mw)
        precollect_restrictive_starts(mw)
        self.assertFalse(mw.precollected_items[1])
        CoreFill.distribute_items_restrictive(mw)
        self.assertIs(mw.get_location('Check 1', 1).item, ship)
        self.assertFalse(mw.get_unfilled_locations())
        self.assertTrue(mw.fulfills_accessibility())

    def test_open_minigames_cannot_hide_a_closed_portal_route(self):
        mw = self.build(2)
        world = mw.worlds[1]
        menu = mw.get_region('Menu', 1)
        menu.locations.append(KH3Location(1, 'Check 2', 3, menu))
        world.active_locations = [{'name': f'Check {i}', 'world': 'Olympus',
                                   'type': 'treasure' if i == 2 else 'minigame'} for i in range(3)]
        world.level_up_forbidden_item_names = frozenset({"Hero's Origin"})
        mw.get_location('Check 2', 1).access_rule = lambda state: state.has("Hero's Origin", 1)
        mw.itempool = [world.create_item(name) for name in ("Hero's Origin", 'Magic: Fire', 'Potion')]
        precollect_overflow(mw)
        precollect_restrictive_starts(mw)
        self.assertEqual([item.name for item in mw.precollected_items[1]], ["Hero's Origin"])
        CoreFill.distribute_items_restrictive(mw)
        self.assertFalse(mw.get_unfilled_locations())

    def test_shop_funding_stays_in_the_pool_for_standard_fill(self):
        mw = self.build(1)
        world = mw.worlds[1]
        world.options.moogle_shop_checks.value = 1
        menu = mw.get_region('Menu', 1)
        shop = KH3Location(1, 'Shop', 3, menu)
        menu.locations.append(shop)
        pouch = world.create_item('Munny Pouch')
        early = world.create_item('Large Munny Pouch')
        mw.itempool = [pouch, early, world.create_item('Magic: Fire')]
        mw.early_items[1][early.name] = 1
        self.assertEqual(precollect_overflow(mw)[1]['early_filler_reserved'], 1)
        self.assertTrue(all(item.location is None for item in mw.itempool))
        CoreFill.distribute_items_restrictive(mw)
        self.assertFalse(mw.get_unfilled_locations())
        self.assertEqual({id(loc.item) for loc in mw.get_locations()},
                         {id(pouch), id(early), id(mw.itempool[-1])})


class TestStrictJunkGeneration(TestCase):
    def test_all_junk_with_party_portals_keybladesanity_and_classic_checks(self):
        junk = {name: 'junk' for name, kind in KH3Options.type_hints.items()
                if issubclass(kind, KH3LocationPoolMode)}
        for keybladesanity in (False, True):
            with self.subTest(keybladesanity=keybladesanity):
                options = {**junk, 'portal_unlocks': 'party_members', 'classic_kingdom_checks': 1,
                           'level_up_limit': 1, 'keyblade_levels': 2 if keybladesanity else 1}
                mw = setup_multiworld(KH3World, seed=610065002, options=options)
                CoreFill.distribute_items_restrictive(mw)
                call_all(mw, 'post_fill')
                call_all(mw, 'finalize_multiworld')
                self.assertFalse(mw.get_unfilled_locations())
                self.assertTrue(mw.can_beat_game())
                self.assertTrue(mw.fulfills_accessibility())
                for location in mw.get_locations():
                    if location.filler_only and location.address is not None:
                        self.assertEqual(location.progress_type, LocationProgressType.EXCLUDED)
                        self.assertTrue(is_junk_item(location.item), location.name)
                        for owner in (1, 2):
                            for flags in (ItemClassification.progression, ItemClassification.useful,
                                          ItemClassification.progression_skip_balancing):
                                self.assertFalse(location.item_rule(Item('Remote important item', flags, 1, owner)))
