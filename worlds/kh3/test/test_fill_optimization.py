import pickle
from collections import Counter
from copy import deepcopy
from itertools import combinations
from types import SimpleNamespace
from unittest import TestCase

from BaseClasses import DEFAULT_COLLECTION_RULE, CollectionState, Item, ItemClassification, Region
from rule_builder.rules import Has, Rule, True_
from test.general import setup_multiworld

from .. import KH3World
from ..Data import (
    CLASSIC_KINGDOM_UNLOCK_ITEM_NAMES,
    KEYBLADE_STYLE_ITEM_NAMES,
    SECRET_REPORT_ITEM_NAMES,
    STYLE_CHANGE_UNLOCK_ITEM,
)
from ..Items import KH3Item
from ..Locations import KH3Location
from ..Logic import CountedRequirement
from ..rule_helpers import requirement_rule


class TestRequirementEvaluation(TestCase):
    def test_rule_builder_matches_requirement_truth_table(self):
        names = ("A", "B", "C")
        subsets = [frozenset(c) for size in range(4) for c in combinations(names, size)]
        examples = (
            (),
            ((),),
            ((frozenset(),),),
            ((frozenset(names),),),
            ((CountedRequirement(frozenset(names), 2),),),
            ((CountedRequirement(frozenset(names), 3), frozenset({"A"})),),
            ((frozenset({"A"}), frozenset({"B"})), (frozenset({"C"}),)),
        )
        for owned in subsets:
            for starting in subsets:
                state = SimpleNamespace(prog_items={2: Counter(owned)})
                world = SimpleNamespace(player=2)
                for routes in examples:
                    expected = any(all(
                        len((owned | starting)
                            & (clause.item_names if isinstance(clause, CountedRequirement) else clause))
                        >= (clause.count if isinstance(clause, CountedRequirement) else 1)
                        for clause in route) for route in routes)
                    self.assertEqual(requirement_rule(routes, starting).resolve(world)(state), expected,
                                     (owned, starting, routes))


class TestKH3CollectionAndConstantRules(TestCase):
    def test_styles_and_reports_are_useful_and_do_not_enter_progression_inventory(self):
        mw = setup_multiworld(KH3World, steps=(), seed=731)
        world = mw.worlds[1]
        state = CollectionState(mw)
        for name in (*KEYBLADE_STYLE_ITEM_NAMES, STYLE_CHANGE_UNLOCK_ITEM, *SECRET_REPORT_ITEM_NAMES):
            with self.subTest(item=name):
                item = world.create_item(name)
                self.assertEqual(item.classification, ItemClassification.useful)
                self.assertFalse(state.collect(item, True))
                self.assertEqual(state.count(name, 1), 0)

    def test_extra_pool_rewards_preserve_styles_and_reports_without_filler(self):
        mw = setup_multiworld(KH3World, steps=(), seed=731)
        world = mw.worlds[1]
        protected = (*KEYBLADE_STYLE_ITEM_NAMES, *SECRET_REPORT_ITEM_NAMES)
        pool = [world.create_item(name) for name in (*protected, 'Ability: HP Prize Plus')]
        world._replace_removable_item_pool_items(pool, ['Ability: Fire Boost'], 'extra test reward')
        self.assertEqual(Counter(item.name for item in pool), Counter((*protected, 'Ability: Fire Boost')))

    def test_collection_matches_progression_flags_and_tracks_useful_games(self):
        mw = setup_multiworld(KH3World, steps=(), seed=731)
        world = mw.worlds[1]
        state = CollectionState(mw)
        for item_type in (Item, KH3Item):
            for name in ("Ordinary reward", *sorted(CLASSIC_KINGDOM_UNLOCK_ITEM_NAMES)):
                for flags in range(16):
                    item = item_type(name, ItemClassification(flags), 1, 1)
                    expected = name if flags & int(ItemClassification.progression) else None
                    optional_game = (not expected and bool(flags & int(ItemClassification.useful))
                                     and name in CLASSIC_KINGDOM_UNLOCK_ITEM_NAMES)
                    with self.subTest(item_type=item_type, name=name, flags=flags):
                        self.assertEqual(world.collect_item(state, item), expected)
                        self.assertEqual(world.collect_item(state, item, True), expected)
                        self.assertEqual(world.collect(state, item), bool(expected))
                        self.assertEqual(state.count(name, 1), int(bool(expected)))
                        self.assertEqual(state._kh3_useful_classic_games.get(1, {}).get(name, 0), int(optional_game))
                        self.assertEqual(world.remove(state, item), bool(expected or optional_game))
                        self.assertEqual(state.count(name, 1), 0)
                        self.assertEqual(state._kh3_useful_classic_games.get(1, {}).get(name, 0), 0)

    def test_collection_metadata_follows_name_and_classification_changes(self):
        mw = setup_multiworld(KH3World, steps=(), seed=731)
        world = mw.worlds[1]
        state = CollectionState(mw)
        item = KH3Item("Ordinary reward", ItemClassification.useful, 1, 1)
        self.assertIsNone(world.collect_item(state, item))
        item.classification |= ItemClassification.progression
        self.assertEqual(world.collect_item(state, item), item.name)
        item.classification = ItemClassification.filler
        self.assertIsNone(world.collect_item(state, item))
        item.name = next(iter(CLASSIC_KINGDOM_UNLOCK_ITEM_NAMES))
        self.assertIsNone(world.collect_item(state, item))
        item.name = "Ordinary reward"
        self.assertIsNone(world.collect_item(state, item))
        item.classification = ItemClassification.progression
        for copy in (deepcopy(item), pickle.loads(pickle.dumps(item))):
            self.assertEqual(world.collect_item(state, copy), item.name)
            copy.classification = ItemClassification.useful
            self.assertIsNone(world.collect_item(state, copy))
        self.assertEqual(world.collect_item(state, item), item.name)

    def test_only_proven_unconditional_rules_use_the_default_predicate(self):
        mw = setup_multiworld(KH3World, steps=(), seed=731)
        world = mw.worlds[1]
        menu = Region('Menu', 1, mw)
        mw.regions.append(menu)
        location = KH3Location(1, 'Test rule', 1, menu)
        menu.locations.append(location)
        world.set_rule(location, Has('Magic: Fire') | True_())
        self.assertIs(location.access_rule, DEFAULT_COLLECTION_RULE)
        self.assertTrue(location.can_reach(CollectionState(mw)))
        world.set_rule(location, Has('Magic: Fire'))
        self.assertIsInstance(location.access_rule, Rule.Resolved)
        state = CollectionState(mw)
        self.assertFalse(location.can_reach(state))
        item = world.create_item('Magic: Fire')
        state.collect(item, True)
        self.assertTrue(location.can_reach(state.copy()))
        state.remove(item)
        self.assertFalse(location.can_reach(state))
