import random
from collections import Counter
from unittest import TestCase
from unittest.mock import patch

from BaseClasses import DEFAULT_COLLECTION_RULE, CollectionState, Item, ItemClassification
from rule_builder.rules import And, CanReachLocation, Has, Or
from test.general import setup_multiworld

from .. import KH3World
from ..LogicRegions import required_location_names


class TestLogicRegions(TestCase):
    def test_story_regions_follow_mandatory_prerequisites_without_cycles(self):
        mw = setup_multiworld(KH3World, seed=713)
        world = mw.worlds[1]
        checks = {(row["source_table"], row["source_id"]): mw.get_location(row["name"], 1)
                  for row in world.active_locations}
        previous = checks["VBonus", "Vbonus_002"]
        later = checks["VBonus", "Vbonus_005"]
        entrance = later.parent_region.entrances[0]
        self.assertIs(entrance.parent_region, previous.parent_region)
        # The direct edge still observes post-build edits to the location rule.
        self.assertIn(previous.name, entrance.access_rule.location_dependencies())
        self.assertGreater(world.logic_chain_region_count, 0)
        for region in mw.get_regions(1):
            path = set()
            while region.name.startswith("Access: "):
                self.assertNotIn(region, path)
                path.add(region)
                self.assertEqual(len(region.entrances), 1)
                region = region.entrances[0].parent_region

    def test_parent_selection_keeps_or_routes_as_predicates(self):
        rule = And(Has("Magic: Fire"), CanReachLocation("Required"),
                   Or(CanReachLocation("Alternative A"), CanReachLocation("Alternative B")))
        self.assertEqual(list(required_location_names(rule)), ["Required"])

    def test_region_graph_matches_original_rules_for_inventory_changes(self):
        profiles = (
            {},
            {"portal_unlocks": "party_members", "include_space": 1, "olympus_coliseum_pool": "randomize"},
            {"synthesis_pool": "randomize", "moogle_synthesis_limit": 67,
             "photo_missions_pool": "randomize", "keyblade_upgrade_rewards_pool": "randomize",
             "keyblade_levels": "keyblade_upgrade_sanity", "music_chest_count": 8},
            {"classic_kingdom_checks": 1, "junk_classic_kingdom_reward": 1,
             "default_base_abilities": 0, "lucky_emblems_per_check": 1, "include_space": 1},
        )
        for index, options in enumerate(profiles):
            with self.subTest(options=options):
                with patch("worlds.kh3.LogicRegions.create_logic_regions"):
                    original = setup_multiworld(KH3World, seed=713, options=options)
                regions = setup_multiworld(KH3World, seed=713, options=options)
                self.assertGreater(regions.worlds[1].logic_region_count, 0)
                self.assertEqual(original.random.getstate(), regions.random.getstate())
                self.assertEqual(original.worlds[1].build_runtime_slot_data(),
                                 regions.worlds[1].build_runtime_slot_data())
                old_checks = {loc.name: loc for loc in original.get_locations()}
                new_checks = {loc.name: loc for loc in regions.get_locations()}
                self.assertEqual(old_checks.keys(), new_checks.keys())
                self.assertEqual(Counter((i.name, i.player, i.classification) for i in original.itempool),
                                 Counter((i.name, i.player, i.classification) for i in regions.itempool))
                for name, old in old_checks.items():
                    new = new_checks[name]
                    self.assertEqual((old.address, old.locked, old.progress_type, old.filler_only),
                                     (new.address, new.locked, new.progress_type, new.filler_only))
                    for owner in (1, 2):
                        for classification in ItemClassification:
                            item = Item("Test reward", classification, 1, owner)
                            self.assertEqual(old.item_rule(item), new.item_rule(item), name)

                old_state, new_state = CollectionState(original), CollectionState(regions)
                names = sorted(original.get_all_state(False).prog_items[1].elements())
                random.Random(700 + index).shuffle(names)

                def compare():
                    self.assertEqual({name for name, loc in old_checks.items() if loc.can_reach(old_state)},
                                     {name for name, loc in new_checks.items() if loc.can_reach(new_state)})

                compare()
                for step, name in enumerate(names):
                    old_state.collect(original.worlds[1].create_item(name), True)
                    new_state.collect(regions.worlds[1].create_item(name), True)
                    if step % 13 == 0:
                        compare()
                compare()
                old_state, new_state = old_state.copy(), new_state.copy()
                for step, name in enumerate(reversed(names)):
                    old_state.remove(original.worlds[1].create_item(name))
                    new_state.remove(regions.worlds[1].create_item(name))
                    if step % 17 == 0:
                        compare()
                compare()

    def test_shared_milestones_movement_and_encounter_rewards_use_region_gates(self):
        mw = setup_multiworld(KH3World, seed=713, options={
            "synthesis_pool": "randomize", "moogle_synthesis_limit": 67,
            "photo_missions_pool": "randomize", "olympus_coliseum_pool": "randomize", "include_space": 1,
        })
        world = mw.worlds[1]
        by_source = {(row["source_table"], row["source_id"]): mw.get_location(row["name"], 1)
                     for row in world.active_locations}
        figures = [by_source["Event", f"EVENT_00{number}"] for number in range(2, 7)]
        self.assertEqual(len({loc.parent_region for loc in figures}), 1)
        self.assertTrue(all(loc.access_rule is DEFAULT_COLLECTION_RULE for loc in figures))
        recipes = [mw.get_location(row["name"], 1) for row in world.active_locations if row["type"] == "synthesis"]
        self.assertEqual(len({loc.parent_region for loc in recipes}), 2)
        self.assertTrue(all(loc.access_rule is DEFAULT_COLLECTION_RULE for loc in recipes))
        for row in world.extra_vbonus_locations:
            extra = mw.get_location(row["name"], 1)
            parent = by_source[tuple(row["trigger_bridge_key"].split(":", 1))]
            self.assertIs(extra.parent_region, parent.parent_region)

    def test_photo_checks_share_subject_world_gates(self):
        mw = setup_multiworld(KH3World, seed=915, options={
            'photo_missions_pool': 'randomize', 'hundred_acre_wood_pool': 'randomize'})
        photos = {row['source_id']: mw.get_location(row['name'], 1)
                  for row in mw.worlds[1].active_locations if row['type'] == 'photo_mission'}
        self.assertEqual(len(photos), 20)
        self.assertEqual(len({loc.parent_region for loc in photos.values()}), 11)
        self.assertTrue(all(loc.access_rule is DEFAULT_COLLECTION_RULE for loc in photos.values()))
        for group in (('IM_0', 'IM_2', 'IM_7', 'IM_12'), ('IM_8', 'IM_9', 'IM_15', 'IM_17'),
                      ('IM_13', 'IM_14'), ('IM_1', 'IM_10'), ('IM_5', 'IM_18')):
            self.assertEqual(len({photos[mission].parent_region for mission in group}), 1)
        # Demon Tower must retain its extra Battlegate gate; teammates retain
        # their multiworld requirement rather than sharing one subject world.
        self.assertIsNot(photos['IM_3'].parent_region, photos['IM_11'].parent_region)
        self.assertIsNot(photos['IM_6'].parent_region, photos['IM_0'].parent_region)

    def test_entrance_indirects_follow_moved_prerequisites(self):
        mw = setup_multiworld(KH3World, seed=713)
        for region in mw.get_regions(1):
            if not region.name.startswith("Access: "):
                continue
            entrance = region.entrances[0]
            for name in entrance.access_rule.location_dependencies():
                prerequisite = mw.get_location(name, 1)
                self.assertIn(entrance, mw.indirect_connections[prerequisite.parent_region])
