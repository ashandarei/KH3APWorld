from __future__ import annotations

import copy
import json
from collections import Counter
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from BaseClasses import CollectionState, Item, ItemClassification, LocationProgressType
from Fill import distribute_items_restrictive
from NetUtils import convert_to_base_types
from test.general import setup_multiworld
from worlds import network_data_package
from worlds.AutoWorld import data_package_checksum
from worlds.generic.Rules import exclusion_rules

from .. import KH3World
from .. import export_ap_kh3_randomized_options as exporter
from ..Bridge import BridgeProtocolError
from ..Data import local_seeded_reward_requires_bridge_delivery
from ..export_ap_kh3_randomized_options import ALWAYS_ON_EVENT_IDS, export_randomized_options_from_bridge_data
from ..ExtraVictoryBonuses import extra_vbonus_locations
from ..Locations import (
    EXTRA_VBONUS_LOCATION_ID_BASE,
    ROCK_TITAN_VBONUS_BRIDGE_KEY,
    location_name_to_id,
)
from ..SlotData import encode_slot_data
from . import test_universal_tracker as tracker_tests


def make_world(**options):
    return setup_multiworld(KH3World, seed=5, options=options)


def tracker_world(slot):
    return tracker_tests.TestKH3UniversalTracker.build_yamlless_tracker_multiworld(None, convert_to_base_types(slot))


def parent_pairs(world):
    parents = {f"{row['source_table']}:{row['source_id']}": row
               for row in world.active_locations if row["type"] == "vbonus"}
    return [(world.multiworld.get_location(row["name"], world.player),
             world.multiworld.get_location(parents[row["trigger_bridge_key"]]["name"], world.player))
            for row in world.extra_vbonus_locations]


class TestExtraVictoryBonusGeneration(TestCase):
    def test_randomized_minigame_extras_remain_normal_inside_junk_worlds(self):
        world = make_world(toy_box_pool='junk', verum_rex_pool='randomize', verum_rex_max_rank='a').worlds[1]
        pairs = [(extra, parent) for extra, parent in parent_pairs(world) if 'Verum Rex - A Rank' in parent.name]
        self.assertTrue(pairs)
        for extra, parent in pairs:
            self.assertFalse(parent.filler_only)
            self.assertFalse(extra.filler_only)
            self.assertEqual(parent.progress_type, LocationProgressType.DEFAULT)
            self.assertEqual(extra.progress_type, LocationProgressType.DEFAULT)
            self.assertTrue(extra.item_rule(world.create_item('Magic: Fire')))

    def test_standard_checks_accept_every_item_classification_and_progressive_items(self):
        world = make_world(flantastic_flans_pool='randomize', verum_rex_pool='randomize',
                           festival_dance_pool='randomize', frozen_slider_pool='randomize',
                           flash_tracer_course_a_pool='randomize', flash_tracer_course_b_pool='randomize',
                           verum_rex_max_rank='a', festival_dance_max_rank='a', frozen_slider_max_rank='a',
                           flash_tracer_course_a_max_rank='a', flash_tracer_course_b_max_rank='a').worlds[1]
        pairs = parent_pairs(world)
        self.assertTrue(pairs)
        sora_pairs = [(extra, parent) for extra, parent in pairs
                      if "222 Soras" in parent.name or "333 Soras" in parent.name]
        self.assertEqual(len(sora_pairs), 4)
        candidates = [world.create_item(name) for name in (
            "Magic: Fire", "Progressive Keyblade Level", "Ability: Thunder Boost", "Potion", "Burn Trap",
        )]
        candidates.extend(Item(f"Remote {classification.name}", classification, 1, 2)
                          for classification in (ItemClassification.progression, ItemClassification.useful,
                                                 ItemClassification.filler, ItemClassification.trap))
        for extra, parent in pairs:
            self.assertIsNone(extra.item)
            self.assertFalse(extra.locked)
            self.assertFalse(parent.filler_only)
            self.assertFalse(extra.filler_only)
            self.assertEqual(parent.progress_type, LocationProgressType.DEFAULT)
            self.assertEqual(extra.progress_type, LocationProgressType.DEFAULT)
            for item in candidates:
                with self.subTest(location=extra.name, item=item.name):
                    self.assertTrue(parent.item_rule(item))
                    self.assertTrue(extra.item_rule(item))

    def test_every_victory_bonus_has_two_fixed_extras_regardless_of_pool(self):
        identities = []
        for traps in (0, 50, 100):
            mw = make_world(trap_percentage=traps)
            world = mw.worlds[1]
            parents = {f"{row['source_table']}:{row['source_id']}"
                       for row in world.active_locations if row["type"] == "vbonus"}
            counts = Counter(row["trigger_bridge_key"] for row in world.extra_vbonus_locations)
            self.assertTrue(parents)
            self.assertEqual(counts, {key: 2 for key in parents})
            self.assertEqual(len(mw.itempool), len(mw.get_unfilled_locations()))
            identities.append(world.extra_vbonus_locations)
            if traps == 100:
                self.assertFalse(any(item.classification == ItemClassification.filler for item in mw.itempool))
        self.assertEqual(identities[0], identities[1])
        self.assertEqual(identities[0], identities[2])

    def test_three_rewards_fill_and_inherit_access_and_pool_junk(self):
        mw = make_world(keyblade_graveyard_pool="junk", olympus_pool="junk")
        world = mw.worlds[1]
        self.assertTrue(world.extra_vbonus_locations)
        self.assertEqual(len(mw.itempool), len(mw.get_unfilled_locations()))
        pairs = parent_pairs(world)
        sora_pairs = [(extra, parent) for extra, parent in pairs
                      if "222 Soras" in parent.name or "333 Soras" in parent.name]
        self.assertEqual(len(sora_pairs), 4)
        for extra, parent in pairs:
            self.assertIs(extra.parent_region, parent.parent_region)
            self.assertEqual(extra.filler_only, parent.filler_only)
            for name in ("Magic: Water", "Ability: Thunder Boost", "Potion"):
                item = world.create_item(name)
                self.assertEqual(extra.item_rule(item), parent.item_rule(item))
        for extra, _ in sora_pairs:
            self.assertFalse(extra.item_rule(world.create_item("Magic: Water")))
        state = CollectionState(mw)
        for item in [None, *[item for item in mw.itempool if item.advancement]]:
            if item is not None:
                state.collect(item, True)
            for extra, parent in pairs:
                self.assertEqual(extra.can_reach(state), parent.can_reach(state))
        distribute_items_restrictive(mw)
        self.assertTrue(mw.can_beat_game())
        self.assertFalse(mw.get_unfilled_locations())
        self.assertTrue(any(extra.item.advancement for extra, _ in pairs))
        for extra, _ in sora_pairs:
            self.assertFalse(extra.item.advancement)

    def test_parent_exclusions_and_descriptive_names(self):
        initial = make_world().worlds[1]
        parent_name = parent_pairs(initial)[0][1].name
        mw = make_world(exclude_locations=[parent_name])
        world = mw.worlds[1]
        exclusion_rules(mw, 1, world.options.exclude_locations.value)
        excluded = [extra for extra, parent in parent_pairs(world) if parent.name == parent_name]
        self.assertEqual(len(excluded), 2)
        self.assertTrue(all(extra.progress_type == LocationProgressType.EXCLUDED for extra in excluded))
        self.assertEqual({extra.name for extra in excluded},
                         {f"{parent_name} - Reward 2", f"{parent_name} - Reward 3"})
        restored = tracker_world(world.build_runtime_slot_data())
        for extra in excluded:
            self.assertEqual(restored.get_location(extra.name, 1).progress_type, LocationProgressType.EXCLUDED)

    def test_catalog_is_immutable_across_players_seeds_and_tracker(self):
        package = network_data_package["games"][KH3World.game]
        before = copy.deepcopy(package)
        reverse = KH3World.location_id_to_name.copy()
        mw = setup_multiworld([KH3World, KH3World], seed=5, options=[
            {"trap_percentage": 100}, {"trap_percentage": 0}])
        self.assertTrue(mw.worlds[1].extra_vbonus_locations)
        self.assertEqual(mw.worlds[1].extra_vbonus_locations, mw.worlds[2].extra_vbonus_locations)
        tracker_world(mw.worlds[1].build_runtime_slot_data())
        make_world(level_up_limit=10)
        self.assertEqual(package, before)
        self.assertEqual(KH3World.location_id_to_name, reverse)
        self.assertEqual(len(location_name_to_id), len(set(location_name_to_id.values())))
        self.assertEqual(package["checksum"], data_package_checksum(
            {key: value for key, value in package.items() if key != "checksum"}))
        distribute_items_restrictive(mw)
        self.assertTrue(mw.can_beat_game())

    def test_parent_identities_do_not_depend_on_order_or_other_active_parents(self):
        locations = make_world().worlds[1].active_locations
        def by_id(rows):
            return {row['ap_location_id']: row for row in rows}
        catalog = by_id(extra_vbonus_locations(locations))
        self.assertEqual(catalog, by_id(extra_vbonus_locations(list(reversed(locations)))))
        parent = next(row for row in locations if row["type"] == "vbonus")
        subset = extra_vbonus_locations([parent])
        self.assertEqual(len(subset), 2)
        for row in subset:
            self.assertEqual(row, catalog[row["ap_location_id"]])
        self.assertEqual(extra_vbonus_locations([]), [])


class TestExtraVictoryBonusTracker(TestCase):
    def test_roundtrip_preserves_all_three_rewards_and_normal_traps(self):
        mw = make_world(trap_percentage=100, music_chest_count=8, music_chest_vanilla_conversion=120)
        world = mw.worlds[1]
        slot = json.loads(json.dumps(world.build_runtime_slot_data()))
        restored = tracker_world(slot)
        self.assertTrue(world.extra_vbonus_locations)
        self.assertEqual(world.extra_vbonus_locations, restored.worlds[1].extra_vbonus_locations)
        self.assertEqual({(loc.name, loc.address) for loc in mw.get_locations()},
                         {(loc.name, loc.address) for loc in restored.get_locations()})
        self.assertEqual(len(restored.itempool), len(restored.get_unfilled_locations()))
        self.assertEqual(sum(i.classification == ItemClassification.filler for i in restored.itempool),
                         0)
        rules = slot["bridge"]["conditional_checks"]
        self.assertTrue(any(row["trigger_bridge_key"] == ROCK_TITAN_VBONUS_BRIDGE_KEY for row in rules))
        music_rewards = sum(row["type"] == "music_chest_bonus" for row in slot["bridge"]["locations"])
        # Aqua observes a 0.2 victory flag directly rather than a KH3 reward trigger.
        character_rewards = sum(row["type"] == "character_unlock" and "trigger_bridge_key" in row
                                for row in slot["bridge"]["locations"])
        self.assertEqual(len(rules), music_rewards + len(world.extra_vbonus_locations) + character_rewards + 1)
        self.assertEqual(len({row["ap_location_id"] for row in rules}), len(rules))
        # Check identities are independent of pool composition.
        slot["universal_tracker"]["options"]["trap_percentage"] = 0
        self.assertEqual(world.extra_vbonus_locations, tracker_world(slot).worlds[1].extra_vbonus_locations)

    def test_incomplete_or_changed_static_rewards_are_rejected(self):
        slot = json.loads(json.dumps(make_world().worlds[1].build_runtime_slot_data()))
        for field, value in (("ap_location_name", "Wrong name"), ("trigger_bridge_key", "VBonus:wrong"),
                             ("ap_location_id", EXTRA_VBONUS_LOCATION_ID_BASE - 1)):
            damaged = copy.deepcopy(slot)
            next(row for row in damaged["bridge"]["locations"] if row["type"] == "vbonus_extra")[field] = value
            with self.assertRaises((ValueError, BridgeProtocolError)):
                tracker_world(damaged)
        damaged = copy.deepcopy(slot)
        damaged["bridge"]["locations"] = [row for row in damaged["bridge"]["locations"]
                                          if row["type"] != "vbonus_extra"]
        with self.assertRaisesRegex(ValueError, "reward selection"):
            tracker_world(damaged)


class TestExtraVictoryBonusBridge(TestCase):
    def setUp(self):
        world = make_world().worlds[1]
        self.slot = world.build_runtime_slot_data()
        self.bonuses = [row for row in self.slot["bridge"]["locations"] if row["type"] == "vbonus_extra"]


    def test_local_and_remote_rewards_export_without_native_bonus_rows(self):
        bonuses = copy.deepcopy(self.bonuses[:2])
        for row in bonuses:
            row.update(assigned_item_name="Potion", assigned_item_player=1,
                       assigned_item_player_name="Sora", assigned_item_game=KH3World.game)
        bonuses[1].update(assigned_item_player=2, assigned_item_player_name="Friend",
                          assigned_item_name="Hookshot", assigned_item_game="Ocarina of Time")
        with TemporaryDirectory() as directory:
            root = Path(directory)
            defaults, extracted = root / "defaults.json", root / "extracted.json"
            defaults.write_text(json.dumps({"Event": {
                key: {"RandomizedItem": "ITEM_POTION\0"} for key in ALWAYS_ON_EVENT_IDS}}))
            extracted.write_text(json.dumps({"items": [{"name": "Potion", "id": "ITEM_POTION"}], "locations": []}))
            result = export_randomized_options_from_bridge_data(
                default_kh3=defaults, default_kh3_overrides=None, extracted_data_path=extracted,
                slot=1, slot_name="Sora", archive_name="extra-test.zip", bridge_locations=bonuses,
            )
            multidata = {
                "slot_info": {1: SimpleNamespace(game=KH3World.game, name="Sora"),
                              2: SimpleNamespace(game="Ocarina of Time", name="Friend")},
                "slot_data": {1: encode_slot_data({**self.slot, "kh3_randomizer": {},
                    "bridge": {**self.slot["bridge"], "locations": copy.deepcopy(bonuses)}})},
                "locations": {1: {bonuses[0]["ap_location_id"]: (1, 1, 0),
                                   bonuses[1]["ap_location_id"]: (2, 2, 1)}},
                "datapackage": {KH3World.game: {"item_name_to_id": {"Potion": 1}},
                                "Ocarina of Time": {"item_name_to_id": {"Hookshot": 2}}},
            }
            args = SimpleNamespace(default_kh3=defaults, default_kh3_overrides=None,
                                   extracted_data=extracted, ap_zip=root / "extra-test.zip", runtime_root=root,
                                   slot=1, chrinit_sora_have_ability=(), event_reward_override=())
            with patch.object(exporter, "load_multidata", return_value=multidata):
                archive_result = exporter.export_randomized_options(args)
            self.assertEqual(result.randomized_options, archive_result.randomized_options)
            self.assertEqual([row["runtime_token"] for row in result.placement_summary["locations"]],
                             [row["runtime_token"] for row in archive_result.placement_summary["locations"]])
            self.assertNotIn("VBonusExtra", result.randomized_options)
            self.assertNotIn("VBonus", result.randomized_options)
            entries = result.placement_summary["locations"]
            self.assertTrue(entries[0]["bridge_delivery_required"])
            self.assertEqual(entries[0]["runtime_token"], "ITEM_POTION")
            self.assertTrue(entries[1]["is_foreign_item"])
            self.assertEqual(entries[1]["recipient_slot"], 2)
            self.assertTrue(local_seeded_reward_requires_bridge_delivery(
                bonuses[0]["bridge_key"], "vbonus_extra", "ITEM_POTION"))

    def test_distinct_checks_deliver_identical_items_once_each(self):
        from ..Client import KH3Context

        key = self.bonuses[0]["trigger_bridge_key"]
        rows = [row for row in self.slot["bridge"]["locations"]
                if row["bridge_key"] == key or row.get("trigger_bridge_key") == key]
        with TemporaryDirectory() as directory:
            ctx = KH3Context.__new__(KH3Context)
            ctx.bridge_dir = Path(directory)
            ctx.game = KH3World.game
            ctx.slot_data = self.slot
            ctx.items_received = [SimpleNamespace(
                item=KH3World.item_name_to_id["Potion"], location=row["ap_location_id"], player=1, flags=0,
            ) for row in (*rows, rows[-1])]
            ctx.item_names = SimpleNamespace(lookup_in_game=lambda *_: "Potion")
            ctx.location_names = SimpleNamespace(lookup_in_slot=lambda *_: "Bonus")
            ctx.player_names = {1: "Sora"}
            ctx.location_id_to_bridge_key = {row["ap_location_id"]: row["bridge_key"] for row in rows}
            items = ctx.build_items_payload()["items"]
            self.assertEqual(len(items), len(rows))
            self.assertEqual({item["location_id"] for item in items}, {row["ap_location_id"] for row in rows})
            extra_ids = {row["ap_location_id"] for row in rows if row["type"] == "vbonus_extra"}
            self.assertTrue(extra_ids.issubset({item["location_id"] for item in items}))
            self.assertTrue(all(not any(key.startswith("kh3_") for key in item) for item in items))
            self.assertTrue(all(row["bridge_delivery_required"] for row in rows
                                if row["ap_location_id"] in extra_ids))
