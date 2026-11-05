from dataclasses import replace
from unittest import TestCase

from BaseClasses import CollectionState, ItemClassification, Location, LocationProgressType, Region
from rule_builder.rules import (
    And,
    CanReachEntrance,
    CanReachLocation,
    CanReachRegion,
    Has,
    HasAllCounts,
    HasAnyCount,
    HasFromListUnique,
    Or,
    WrapperRule,
)
from test.general import setup_multiworld

from .. import KH3World
from ..Balancing import mark_optional_balancing_items, protected_check_item_counts, protected_check_item_names
from ..Options import KH3LocationPoolMode, KH3Options
from ..rule_helpers import CountRules


class TestBalancingRelevance(TestCase):
    def setUp(self):
        self.mw = setup_multiworld(KH3World, steps=(), seed=610065002)
        self.world = self.mw.worlds[1]
        self.world.options.keyblade_upgrade_rewards_pool.value = 3
        menu = Region("Menu", 1, self.mw)
        self.normal = Region("Normal", 1, self.mw)
        self.junk = Region("Junk", 1, self.mw)
        self.optional = Region("Optional", 1, self.mw)
        self.goal = Region("Goal", 1, self.mw)
        self.mw.regions.extend((menu, self.normal, self.junk, self.optional, self.goal))
        for region in (self.normal, self.junk, self.optional, self.goal):
            menu.connect(region, name="To " + region.name)
        self.check = Location(1, "Normal check", 1, self.normal)
        self.dependency = Location(1, "Junk dependency", 2, self.junk)
        self.unrelated = Location(1, "Unrelated junk", 3, self.optional)
        self.victory = Location(1, "Victory check", None, self.goal)
        for location in (self.check, self.dependency, self.unrelated, self.victory):
            location.parent_region.locations.append(location)
        self.dependency.progress_type = self.unrelated.progress_type = LocationProgressType.EXCLUDED
        self.victory.place_locked_item(self.world.create_event_item("Victory"))
        self.world.set_completion_rule(Has("Victory"))
        self.world.set_rule(self.check, CanReachLocation(self.dependency.name))
        self.world.set_rule(self.dependency, Or(Has("Magic: Fire"), Has("Magic: Blizzard")))
        self.world.set_rule(self.junk.entrances[0], Has("Hunny Spout"))
        self.world.set_rule(self.unrelated, Has("Classic Tone"))
        self.world.set_rule(self.goal.entrances[0], Has("Proof of Promises"))
        self.mw.itempool.extend(self.world.create_item(name) for name in
            ("Magic: Fire", "Magic: Blizzard", "Hunny Spout", "Classic Tone", "Proof of Promises", "Unlock: Links"))

    def test_protected_dependencies_include_junk_prerequisites_and_alternative_paths(self):
        self.assertEqual(protected_check_item_names(self.world), frozenset(
            ("Victory", "Magic: Fire", "Magic: Blizzard", "Hunny Spout", "Proof of Promises")))
        original_flags = {item.name: item.flags for item in self.mw.itempool}
        mark_optional_balancing_items(self.mw)
        for item in self.mw.itempool:
            self.assertTrue(item.advancement)
            self.assertEqual(item.flags, original_flags[item.name])
            if item.name not in {"Magic: Fire", "Magic: Blizzard"}:
                self.assertEqual(item.skip_in_prog_balancing, item.name in {"Classic Tone", "Unlock: Links"})
            # All items still participate in the normal logical inventory.
            state = CollectionState(self.mw)
            state.collect(item, True)
            self.assertEqual(state.count(item.name, 1), 1)
        choices = [i for i in self.mw.itempool if i.name in {"Magic: Fire", "Magic: Blizzard"}]
        self.assertEqual(sum(not i.skip_in_prog_balancing for i in choices), 1)
        balanced = CollectionState(self.mw)
        for item in self.mw.itempool:
            if not item.skip_in_prog_balancing:
                balanced.collect(item, True)
        self.assertTrue(self.check.can_reach(balanced))

    def test_completion_location_region_entrance_and_count_dependencies_remain_balanced(self):
        for completion in (And(Has("Victory"), HasAllCounts({"Unlock: Links": 1})),
                           And(Has("Victory"), CanReachLocation(self.unrelated.name)),
                           And(Has("Victory"), CanReachRegion(self.optional.name)),
                           And(Has("Victory"), CanReachEntrance("To Optional"))):
            with self.subTest(completion=completion):
                self.world.set_rule(self.optional.entrances[0], Has("Unlock: Links"))
                self.world.set_completion_rule(completion)
                mark_optional_balancing_items(self.mw)
                item = next(item for item in self.mw.itempool if item.name == "Unlock: Links")
                self.assertFalse(item.skip_in_prog_balancing)

    def test_opaque_rule_keeps_existing_balancing_and_restores_only_added_flags(self):
        original = self.mw.itempool[-1]
        original.classification |= ItemClassification.skip_balancing
        mark_optional_balancing_items(self.mw)
        self.check.access_rule = lambda state: state.has("Classic Tone", 1)
        self.assertIsNone(protected_check_item_names(self.world))
        mark_optional_balancing_items(self.mw)
        self.assertTrue(original.skip_in_prog_balancing)
        self.assertFalse(next(i for i in self.mw.itempool if i.name == "Classic Tone").skip_in_prog_balancing)

    def test_opaque_completion_keeps_normal_balancing(self):
        self.mw.completion_condition[1] = lambda state: state.has("Victory", 1)
        self.assertIsNone(protected_check_item_names(self.world))
        mark_optional_balancing_items(self.mw)
        self.assertFalse(any(i.skip_in_prog_balancing for i in self.mw.itempool))

    def test_linked_items_keep_normal_policy(self):
        self.world.options.item_links.value = [{"name": "Linked", "item_pool": ["Classic Tone"]}]
        mark_optional_balancing_items(self.mw)
        self.assertFalse(any(i.skip_in_prog_balancing for i in self.mw.itempool))

    def test_nested_foreign_player_rule_keeps_normal_policy(self):
        foreign = replace(Has("Unlock: Links").resolve(self.world), player=2)
        self.check.access_rule = And.Resolved(
            (Has("Classic Tone").resolve(self.world), foreign), player=1)
        self.assertIsNone(protected_check_item_names(self.world))
        mark_optional_balancing_items(self.mw)
        self.assertFalse(any(i.skip_in_prog_balancing for i in self.mw.itempool))

    def test_changed_rules_restore_balancing_on_a_second_hook_call(self):
        mark_optional_balancing_items(self.mw)
        classic = next(i for i in self.mw.itempool if i.name == "Classic Tone")
        self.assertTrue(classic.skip_in_prog_balancing)
        self.world.set_rule(self.check, Has("Classic Tone"))
        mark_optional_balancing_items(self.mw)
        self.assertFalse(classic.skip_in_prog_balancing)

    def test_only_copies_above_the_largest_referenced_count_skip_balancing(self):
        self.world.set_rule(self.check, And(
            HasFromListUnique("Magic: Fire", "Magic: Blizzard", count=2),
            HasAnyCount({"Magic: Fire": 2, "Magic: Blizzard": 1}),
            CountRules(Has("Magic: Fire", 3), count=1, label="test counted route")))
        self.mw.itempool.extend(self.world.create_item("Magic: Fire") for _ in range(3))
        self.assertEqual(protected_check_item_counts(self.world)["Magic: Fire"], 3)
        before = self.mw.get_all_state(False).prog_items
        mark_optional_balancing_items(self.mw)
        copies = [i for i in self.mw.itempool if i.name == "Magic: Fire"]
        self.assertEqual(sum(not i.skip_in_prog_balancing for i in copies), 3)
        self.assertEqual(before, self.mw.get_all_state(False).prog_items)
        self.assertTrue(all(i.advancement and i.flags == 1 for i in copies))

    def test_precollected_copies_reduce_only_the_balancing_quota(self):
        self.world.set_rule(self.check, Has("Magic: Fire", 2))
        self.mw.itempool.extend(self.world.create_item("Magic: Fire") for _ in range(2))
        self.mw.push_precollected(self.world.create_item("Magic: Fire"))
        mark_optional_balancing_items(self.mw)
        copies = [i for i in self.mw.itempool if i.name == "Magic: Fire"]
        self.assertEqual(sum(not i.skip_in_prog_balancing for i in copies), 1)
        self.assertEqual(self.mw.get_all_state(False).count("Magic: Fire", 1), 4)

    def test_unrecognized_count_semantics_keep_all_referenced_copies(self):
        self.world.set_rule(self.check, WrapperRule(Has("Unlock: Links", 3)))
        self.mw.itempool.extend(self.world.create_item("Unlock: Links") for _ in range(4))
        self.assertIsNone(protected_check_item_counts(self.world)["Unlock: Links"])
        mark_optional_balancing_items(self.mw)
        self.assertFalse(any(i.skip_in_prog_balancing for i in self.mw.itempool if i.name == "Unlock: Links"))

    def test_alternative_groups_share_a_sufficient_balancing_set(self):
        self.world.set_rule(self.check, And(
            HasFromListUnique("Magic: Fire", "Magic: Blizzard", "Magic: Thunder", count=2),
            HasFromListUnique("Magic: Blizzard", "Magic: Thunder", "Magic: Aero", count=2)))
        self.mw.itempool.extend(self.world.create_item(name) for name in ("Magic: Thunder", "Magic: Aero"))
        mark_optional_balancing_items(self.mw)
        balanced = CollectionState(self.mw)
        all_items = CollectionState(self.mw)
        for item in self.mw.itempool:
            all_items.collect(item, True)
            if not item.skip_in_prog_balancing:
                balanced.collect(item, True)
        self.assertTrue(self.check.can_reach(balanced))
        self.assertTrue(self.check.can_reach(all_items))
        self.assertEqual({i.name for i in self.mw.itempool if not i.skip_in_prog_balancing},
                         {"Magic: Blizzard", "Magic: Thunder", "Proof of Promises"})

    def test_starting_and_mandatory_items_cover_unique_alternatives_first(self):
        self.world.set_rule(self.check, And(Has("Magic: Fire"),
            HasFromListUnique("Magic: Fire", "Magic: Blizzard", "Magic: Thunder", count=2)))
        self.mw.push_precollected(self.world.create_item("Magic: Thunder"))
        mark_optional_balancing_items(self.mw)
        self.assertTrue(next(i for i in self.mw.itempool if i.name == "Magic: Blizzard").skip_in_prog_balancing)
        balanced = CollectionState(self.mw)
        for item in self.mw.itempool:
            if not item.skip_in_prog_balancing:
                balanced.collect(item, True)
        self.assertTrue(self.check.can_reach(balanced))

    def test_readonly_dependency_analysis_does_not_change_access_or_inventory(self):
        state = self.mw.get_all_state(False)
        before = {location.name: location.can_reach(state) for location in self.mw.get_locations()}
        flags = [item.classification for item in self.mw.itempool]
        for _ in range(3):
            protected_check_item_names(self.world)
        self.assertEqual(flags, [item.classification for item in self.mw.itempool])
        mark_optional_balancing_items(self.mw)
        after = self.mw.get_all_state(False)
        self.assertEqual(before, {location.name: location.can_reach(after) for location in self.mw.get_locations()})
        self.assertEqual(state.prog_items, after.prog_items)

    def test_inventory_or_reuses_a_sufficient_mandatory_route(self):
        self.mw.itempool.extend(self.world.create_item(name) for name in ("Magic: Thunder", "Magic: Aero"))
        self.world.set_rule(self.check, And(Has("Magic: Blizzard"), Or(
            And(Has("Magic: Fire"), Has("Magic: Aero")),
            And(Has("Magic: Blizzard"), Has("Magic: Thunder")))))
        mark_optional_balancing_items(self.mw)
        self.assertEqual({i.name for i in self.mw.itempool if not i.skip_in_prog_balancing},
                         {"Magic: Blizzard", "Magic: Thunder", "Proof of Promises"})
        state = CollectionState(self.mw)
        for item in self.mw.itempool:
            if not item.skip_in_prog_balancing:
                state.collect(item, True)
        self.assertTrue(self.check.can_reach(state))

    def test_inventory_or_never_selects_an_unavailable_cheaper_route(self):
        self.mw.itempool.pop()  # Unlock: Links is not available in this fixture.
        self.world.set_rule(self.check, Or(Has("Unlock: Links"),
            And(Has("Magic: Fire"), Has("Magic: Blizzard"))))
        mark_optional_balancing_items(self.mw)
        for name in ("Magic: Fire", "Magic: Blizzard"):
            self.assertFalse(next(i for i in self.mw.itempool if i.name == name).skip_in_prog_balancing)

    def test_reachability_or_keeps_both_dependency_routes_protected(self):
        self.world.set_rule(self.check, Or(CanReachLocation(self.dependency.name),
            And(Has("Classic Tone"), Has("Unlock: Links"))))
        counts = protected_check_item_counts(self.world)
        self.assertGreater(counts["Classic Tone"], 0)
        self.assertGreater(counts["Unlock: Links"], 0)
        self.assertGreater(counts["Hunny Spout"], 0)

    def test_unique_group_selects_an_available_alternative(self):
        self.mw.itempool[:] = [i for i in self.mw.itempool if i.name != "Magic: Blizzard"]
        mark_optional_balancing_items(self.mw)
        self.assertFalse(next(i for i in self.mw.itempool if i.name == "Magic: Fire").skip_in_prog_balancing)


class TestRealWorldBalancingBasis(TestCase):
    def test_selected_items_preserve_every_protected_check_and_goal(self):
        junk = {name: "junk" for name, kind in KH3Options.type_hints.items()
                if issubclass(kind, KH3LocationPoolMode)}
        examples = ({}, {"heart_piece_count": 7}, {"portal_unlocks": "party_members"},
                    {"keyblade_levels": "keyblade_upgrade_sanity"},
                    {"junk_classic_kingdom_reward": True}, junk)
        for index, options in enumerate(examples):
            with self.subTest(options=options):
                mw = setup_multiworld(KH3World, seed=801700 + index, options=options)
                all_items = mw.get_all_state(False)
                basis = CollectionState(mw)
                for item in mw.itempool:
                    if not item.skip_in_prog_balancing:
                        basis.collect(item, True)
                basis.sweep_for_advancements()
                for location in mw.get_locations():
                    if location.address is not None and location.progress_type != LocationProgressType.EXCLUDED:
                        self.assertEqual(location.can_reach(basis), location.can_reach(all_items), location.name)
                self.assertEqual(mw.has_beaten_game(basis), mw.has_beaten_game(all_items))
