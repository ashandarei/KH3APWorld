import json
from collections import Counter
from types import SimpleNamespace
from typing import ClassVar
from unittest import TestCase
from unittest.mock import patch

from BaseClasses import DEFAULT_COLLECTION_RULE, CollectionState
from rule_builder.rules import And, CanReachLocation, CanReachRegion, Has, Rule, True_

from .. import KH3World
from ..Data import PROOF_ITEMS, VICTORY_BONUS_LOGIC_REQUIREMENTS, filter_locations_for_options
from ..Regions import HUB_REGION_NAME
from ..rule_helpers import CountReachableRules, CountRules, cache_reachable_rule_construction
from . import KH3TestBase


class TestCountRules(TestCase):
    def test_duplicate_requirements_are_evaluated_once_with_their_full_weight(self):
        world = SimpleNamespace(player=1)
        state = SimpleNamespace(prog_items={1: Counter(A=1)})
        resolved = CountRules(*(Has("A") for _ in range(90)), count=90, label="spots").resolve(world)
        original = Has.Resolved._evaluate
        with patch.object(Has.Resolved, "_evaluate", autospec=True, side_effect=original) as evaluate:
            self.assertTrue(resolved(state))
            self.assertEqual(evaluate.call_count, 1)
            state.prog_items[1].clear()
            self.assertFalse(resolved(state))
            self.assertEqual(evaluate.call_count, 2)

    def test_weighted_counts_match_naive_evaluation_for_every_inventory_and_threshold(self):
        world = SimpleNamespace(player=1)
        children = (Has("A"), Has("B"), Has("A"), Has("C"), Has("B"), Has("A"))
        for mask in range(8):
            state = SimpleNamespace(prog_items={1: Counter({
                name: (mask >> index) & 1 for index, name in enumerate("ABC")
            })})
            found = sum(child.resolve(world)(state) for child in children)
            for count in range(-1, 8):
                with self.subTest(mask=mask, count=count):
                    resolved = CountRules(*children, count=count, label="spots").resolve(world)
                    self.assertEqual(resolved(state), found >= count)

    def test_identical_spots_count_separately_and_do_not_cache_stale_inventory(self):
        world = SimpleNamespace(player=1)
        state = SimpleNamespace(prog_items={1: Counter()})
        # Distinct emblem spots can have exactly the same access requirement.
        rule = And(True_(), CountRules(Has("A"), Has("A"), Has("B"), count=2, label="spots"))
        resolved = rule.resolve(world)
        self.assertFalse(resolved(state))
        state.prog_items[1]["A"] = 1
        self.assertTrue(resolved(state))
        self.assertIn("2/2", resolved.explain_str(state))
        state.prog_items[1]["A"] = 0
        state.prog_items[1]["B"] = 99
        self.assertFalse(resolved(state))

    def test_counts_are_scoped_to_the_player(self):
        state = SimpleNamespace(prog_items={1: Counter(A=1), 2: Counter(B=1)})
        rule = CountRules(Has("A"), Has("A"), Has("B"), count=2, label="spots")
        self.assertTrue(rule.resolve(SimpleNamespace(player=1))(state))
        self.assertFalse(rule.resolve(SimpleNamespace(player=2))(state))

    def test_empty_zero_and_unattainable_counts(self):
        world = SimpleNamespace(player=1)
        state = SimpleNamespace(prog_items={1: Counter(A=10)})
        for count, children, expected in (
            (0, (), True), (1, (), False), (2, (Has("A"),), False),
        ):
            with self.subTest(count=count, children=children):
                self.assertEqual(CountRules(*children, count=count, label="spots").resolve(world)(state), expected)

    def test_serialization_preserves_duplicates_and_all_dependency_types(self):
        rule = CountRules(
            Has("A"), Has("A"), CanReachRegion("World"),
            CanReachLocation("Boss", parent_region_name="Boss World"),
            count=3, label="reachable objectives",
        )
        serialized = json.loads(json.dumps(rule.to_dict()))
        restored = KH3World.rule_from_dict(serialized)
        self.assertEqual(restored.to_dict(), serialized)
        self.assertEqual(len(restored.children), 4)
        world = SimpleNamespace(player=1)
        resolved = restored.resolve(world)
        self.assertEqual(resolved, rule.resolve(world))
        self.assertEqual(set(resolved.item_dependencies()), {"A"})
        self.assertEqual(set(resolved.region_dependencies()), {"World", "Boss World"})
        self.assertEqual(set(resolved.location_dependencies()), {"Boss"})
        self.assertEqual(resolved.explain_str(), "At least 3 reachable objectives")


class TestReachableRuleConstruction(TestCase):
    def test_shared_sources_resolve_once_and_keep_threshold_semantics(self):
        world = SimpleNamespace(player=1)
        children = (Has("A"), Has("A"), Has("B"))
        rules = [CountReachableRules(*children, count=count, label="spots") for count in range(5)]
        expected = [rule.resolve(world) for rule in rules]
        serialized = [rule.to_dict() for rule in rules]
        with cache_reachable_rule_construction(world), patch.object(
            Has, "_instantiate", autospec=True, side_effect=Has._instantiate,
        ) as instantiate:
            actual = [rule.resolve(world) for rule in rules]
            self.assertEqual(instantiate.call_count, len(children))
            self.assertEqual(actual, expected)
            self.assertEqual([rule.to_dict() for rule in rules], serialized)
        self.assertFalse(hasattr(world, "_kh3_reachable_rule_build_cache"))
        for mask in range(4):
            state = SimpleNamespace(prog_items={1: Counter(A=mask & 1, B=(mask >> 1) & 1)},
                                    reachable_regions={1: set()})
            for count, rule in enumerate(actual):
                with self.subTest(mask=mask, count=count):
                    self.assertEqual(rule(state), 2 * (mask & 1) + ((mask >> 1) & 1) >= count)

    def test_new_graph_pass_refreshes_location_dependencies(self):
        location = SimpleNamespace(parent_region=SimpleNamespace(name="Original region"))
        world = SimpleNamespace(player=1, get_location=lambda name: location)
        rule = CountReachableRules(CanReachLocation("Boss"), count=1, label="bosses")
        with cache_reachable_rule_construction(world):
            original = rule.resolve(world)
        location.parent_region = SimpleNamespace(name="Final access region")
        with cache_reachable_rule_construction(world):
            moved = rule.resolve(world)
        self.assertEqual(set(original.region_dependencies()), {"Original region"})
        self.assertEqual(set(moved.region_dependencies()), {"Final access region"})
        self.assertEqual(moved.source_region_names, frozenset({"Final access region"}))
        self.assertEqual(set(moved.location_dependencies()), {"Boss"})

    def test_players_and_world_instances_do_not_share_construction_caches(self):
        rule = CountReachableRules(CanReachLocation("Boss"), Has("A"), count=2, label="spots")
        for player, parent in ((1, "First world"), (2, "Second player"), (1, "Next seed")):
            world = SimpleNamespace(player=player, get_location=lambda name: SimpleNamespace(
                parent_region=SimpleNamespace(name=parent)))
            with self.subTest(player=player, parent=parent), cache_reachable_rule_construction(world):
                resolved = rule.resolve(world)
                self.assertEqual(resolved.player, player)
                self.assertTrue(all(child.player == player for child in resolved.children))
                self.assertEqual(set(resolved.region_dependencies()), {parent})

    def test_construction_cache_is_discarded_after_failure(self):
        world = SimpleNamespace(player=1)
        with self.assertRaisesRegex(ValueError, "failed build"):
            with cache_reachable_rule_construction(world):
                CountReachableRules(Has("A"), count=1, label="spots").resolve(world)
                raise ValueError("failed build")
        self.assertFalse(hasattr(world, "_kh3_reachable_rule_build_cache"))


class TestKH3RuleBuilder(KH3TestBase):
    auto_construct = False
    options: ClassVar[dict] = {"include_space": 1, "olympus_coliseum_pool": "randomize", "music_chest_count": 8}

    def test_disabled_prerequisite_chain_keeps_rules_without_adding_checks(self):
        hidden_keys = (("VBonus", "Vbonus_002"), ("VBonus", "Vbonus_005"))
        proof_names = sorted(PROOF_ITEMS)[:2]

        def omit_prerequisites(rows, options):
            return [row for row in filter_locations_for_options(rows, options)
                    if (row["source_table"], row["source_id"]) not in hidden_keys]

        # Give each hidden predecessor a distinct additional requirement.
        requirements = {key: ((frozenset({proof}),),) for key, proof in zip(hidden_keys, proof_names, strict=True)}
        with patch("worlds.kh3.filter_locations_for_options", side_effect=omit_prerequisites), \
                patch.dict(VICTORY_BONUS_LOGIC_REQUIREMENTS, requirements):
            self.world_setup(seed=730142)
        hidden = {(row["source_table"], row["source_id"]): row for row in self.world.logic_reference_locations}
        self.assertEqual(set(hidden), set(hidden_keys))
        location_names = {location.name for location in self.multiworld.get_locations(self.player)}
        exported = {row["ap_location_name"] for row in self.world.build_runtime_slot_data()["bridge"]["locations"]}
        for row in hidden.values():
            self.assertNotIn(row["name"], location_names)
            self.assertNotIn(row["name"], exported)
        target = self.get_location_by_source("VBonus", "Vbonus_006")
        state = self.multiworld.get_all_state(False)
        self.assertTrue(target.can_reach(state))
        for proof in proof_names:
            with self.subTest(missing=proof):
                missing = state.copy()
                while missing.has(proof, self.player):
                    missing.remove(self.world.create_item(proof))
                self.assertFalse(target.can_reach(missing))
                missing.collect(self.world.create_item(proof), True)
                self.assertTrue(target.can_reach(missing))
        parent = self.get_location_by_source("VBonus", "Vbonus_001")
        with patch.object(parent, "access_rule", return_value=False):
            self.reset_reachability(state)
            self.assertFalse(target.can_reach(state))

    def test_access_and_completion_rules_use_rule_builder(self):
        self.world_setup(seed=730142)
        for row in self.world.active_locations:
            location = self.multiworld.get_location(row["name"], self.player)
            with self.subTest(location=location.name):
                if location.access_rule is DEFAULT_COLLECTION_RULE:
                    self.assertTrue(location.access_rule(CollectionState(self.multiworld)))
                else:
                    self.assertIsInstance(location.access_rule, Rule.Resolved)
        for entrance in self.multiworld.get_region(HUB_REGION_NAME, self.player).exits:
            with self.subTest(entrance=entrance.name):
                self.assertTrue(entrance.access_rule is DEFAULT_COLLECTION_RULE
                                or isinstance(entrance.access_rule, Rule.Resolved))
        completion = self.multiworld.completion_condition[self.player]
        self.assertIsInstance(completion, Has.Resolved)
        state = CollectionState(self.multiworld)
        self.assertFalse(completion(state))
        victory = self.world.create_event_item("Victory")
        state.collect(victory, True)
        self.assertTrue(completion(state))
        state.remove(victory)
        self.assertFalse(completion(state))
