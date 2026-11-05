import json
import random
from unittest import TestCase
from unittest.mock import patch

from BaseClasses import CollectionState, Region
from rule_builder.rules import CanReachRegion, Has
from test.general import setup_multiworld

from .. import KH3World
from ..rule_helpers import CountReachableRules, CountRules


class TestReachableCounts(TestCase):
    def test_thresholds_share_totals_with_duplicates_copies_and_removals(self):
        mw = setup_multiworld([KH3World, KH3World], steps=(), seed=731)
        world = mw.worlds[1]
        children = (Has("Magic: Fire"), Has("Magic: Fire"), Has("Magic: Blizzard"))
        rules = [CountReachableRules(*children, count=n, label="test").resolve(world) for n in (1, 2, 3)]
        state = CollectionState(mw)
        original = Has.Resolved._evaluate
        with patch.object(Has.Resolved, "_evaluate", autospec=True, side_effect=original) as evaluate:
            self.assertEqual([rule(state) for rule in rules], [False, False, False])
            self.assertEqual(evaluate.call_count, 2)
        self.assertEqual(len(state._kh3_reachable_counts[1]), 1)
        fire = world.create_item("Magic: Fire")
        state.collect(fire, True)
        self.assertEqual([rule(state) for rule in rules], [True, True, False])
        state.collect(fire, True)
        self.assertEqual([rule(state) for rule in rules], [True, True, False])
        other = state.copy()
        other.collect(world.create_item("Magic: Blizzard"), True)
        self.assertTrue(rules[2](other))
        self.assertFalse(rules[2](state))
        self.assertIsNot(other._kh3_reachable_counts, state._kh3_reachable_counts)
        state.remove(fire)
        self.assertTrue(rules[1](state))
        state.remove(fire)
        self.assertFalse(rules[0](state))
        cached = state._kh3_reachable_counts[1]
        state.collect(mw.worlds[2].create_item("Magic: Fire"), True)
        self.assertIs(state._kh3_reachable_counts[1], cached)

    def test_totals_follow_region_growth_and_replaced_region_sets(self):
        mw = setup_multiworld(KH3World, steps=(), seed=731)
        world = mw.worlds[1]
        menu, first, second = (Region(name, 1, mw) for name in ("Menu", "First", "Second"))
        mw.regions.extend((menu, first, second))
        menu.connect(first)
        first.connect(second)
        rule = CountReachableRules(CanReachRegion("First"), CanReachRegion("First"),
                                   CanReachRegion("Second"), count=2, label="regions").resolve(world)
        state = CollectionState(mw)
        # Model the partial region set used inside AP's entrance search.
        state.stale[1] = False
        self.assertFalse(rule(state))
        state.reachable_regions[1].add(first)
        self.assertTrue(rule(state))
        state.reachable_regions[1] = {second}
        self.assertFalse(rule(state))
        state.stale[1] = True
        state.reachable_regions[1] = set()
        self.assertTrue(rule(state))
        # The first evaluation triggered BFS; its old partial count is not cached.
        self.assertTrue(all(entry[1] != entry[0].intersection(state.reachable_regions[1])
                            for entry in state._kh3_reachable_counts[1].values()))
        self.assertTrue(rule(state))
        self.assertTrue(state._kh3_reachable_counts[1])

    def test_serialization_keeps_thresholds_weights_and_dependencies(self):
        rule = CountReachableRules(Has("Magic: Fire"), Has("Magic: Fire"),
                                   CanReachRegion("Olympus"), count=2, label="sources")
        serialized = json.loads(json.dumps(rule.to_dict()))
        restored = KH3World.rule_from_dict(serialized)
        self.assertEqual(restored.to_dict(), serialized)
        mw = setup_multiworld(KH3World, seed=713)
        resolved = restored.resolve(mw.worlds[1])
        self.assertEqual(set(resolved.item_dependencies()), {"Magic: Fire"})
        self.assertEqual(set(resolved.region_dependencies()), {"Olympus"})
        self.assertEqual(len(resolved.children), 3)

    def test_full_logic_matches_uncached_totals_through_inventory_changes(self):
        for options in ({}, {"portal_unlocks": "party_members", "include_space": 1,
                             "default_base_abilities": 0, "lucky_emblems_per_check": 1}):
            with self.subTest(options=options):
                mw = setup_multiworld(KH3World, seed=713, options=options)
                world = mw.worlds[1]
                cached = CollectionState(mw)
                uncached = CollectionState(mw)
                names = sorted(mw.get_all_state(False).prog_items[1].elements())
                random.Random(719).shuffle(names)

                def compare():
                    actual = {loc.name for loc in mw.get_locations() if loc.can_reach(cached)}
                    with patch.object(CountReachableRules.Resolved, "_evaluate", CountRules.Resolved._evaluate):
                        expected = {loc.name for loc in mw.get_locations() if loc.can_reach(uncached)}
                    self.assertEqual(actual, expected)

                compare()
                for step, name in enumerate(names):
                    cached.collect(world.create_item(name), True)
                    uncached.collect(world.create_item(name), True)
                    if step % 11 == 0:
                        compare()
                compare()
                cached, uncached = cached.copy(), uncached.copy()
                for step, name in enumerate(reversed(names)):
                    cached.remove(world.create_item(name))
                    uncached.remove(world.create_item(name))
                    if step % 13 == 0:
                        compare()
                compare()
