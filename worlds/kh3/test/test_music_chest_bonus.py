from __future__ import annotations

import copy
import json
import random
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase

from BaseClasses import CollectionState
from NetUtils import convert_to_base_types

from ..Bridge import build_bridge_slot_data
from ..client_support.mom_challenges import MoMChallengeService
from ..Data import load_extracted_data, local_seeded_reward_requires_bridge_delivery, normalize_locations
from ..export_ap_kh3_randomized_options import ALWAYS_ON_EVENT_IDS, export_randomized_options_from_bridge_data
from ..Locations import ROCK_TITAN_VBONUS_BRIDGE_KEY, location_name_to_id
from ..MelodyOfMemory import (
    MUSIC_CHEST_BONUS_ID_OFFSET,
    MUSIC_CHEST_PLACEMENTS,
    music_chest_bonus_locations,
    music_chest_bonus_name,
    placed_music_chest,
    select_music_chests,
)
from . import KH3TestBase
from .test_universal_tracker import TestKH3UniversalTracker as TrackerTests


def paired_slot_data() -> dict:
    native = next(row for row in normalize_locations(load_extracted_data()) if row["type"] == "treasure")
    chests = [copy.deepcopy(native)]
    chests.extend(select_music_chests(random.Random(42), chests, 1, 1, 1))
    bridge = build_bridge_slot_data(chests + music_chest_bonus_locations(chests), 7)
    bridge["chest_persistence_version"] = 1
    for row in bridge["locations"]:
        row.update(assigned_item_name="Potion", assigned_item_player=1,
                   assigned_item_player_name="Sora", assigned_item_game="Kingdom Hearts III")
    return {"bridge": bridge, "kh3_seed": {"seed_name": "bonus-test", "slot": 1, "slot_name": "Sora"}}


class TestMusicChestBonusDelivery(TestCase):
    def test_ids_are_unique_stable_and_bound_to_chests(self):
        self.assertEqual(len(location_name_to_id), len(set(location_name_to_id.values())))
        for row in (*normalize_locations(load_extracted_data()), *MUSIC_CHEST_PLACEMENTS):
            if row.get("type") == "treasure" or "placement_id" in row:
                self.assertEqual(location_name_to_id[music_chest_bonus_name(row["name"])],
                                 location_name_to_id[row["name"]] + MUSIC_CHEST_BONUS_ID_OFFSET)
        placement = MUSIC_CHEST_PLACEMENTS[0]["placement_id"]
        first = music_chest_bonus_locations([placed_music_chest(placement, 1, 0)])
        second = music_chest_bonus_locations([placed_music_chest(placement, 2, 2)])
        self.assertEqual(first, second)


    def test_bonus_is_never_a_second_spawn_or_challenge(self):
        slot = paired_slot_data()
        with TemporaryDirectory() as directory:
            root = Path(directory)
            service = MoMChallengeService(root, slot)
            bonuses = {row["ap_location_id"] for row in slot["bridge"]["locations"]
                       if row["type"] == "music_chest_bonus"}
            self.assertTrue(bonuses.isdisjoint(service.challenges))
            service.publish_clears(root)
            payload = json.loads((root / "runtime_mom_clear_state.json").read_text())
            self.assertEqual([], payload["cleared_locations"])
            self.assertNotIn("chests", payload)

    def test_bonus_export_and_receipt_use_bridge_for_local_and_foreign_items(self):
        slot = paired_slot_data()
        bonuses = [row for row in slot["bridge"]["locations"] if row["type"] == "music_chest_bonus"]
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
                slot=1, slot_name="Sora", archive_name="bonus-test.zip", bridge_locations=bonuses,
            )
            self.assertNotIn("MusicChestBonus", result.randomized_options)
            entries = result.placement_summary["locations"]
            self.assertTrue(entries[0]["bridge_delivery_required"])
            self.assertEqual(entries[0]["runtime_token"], "ITEM_POTION")
            self.assertTrue(entries[1]["is_foreign_item"])
            self.assertEqual(entries[1]["recipient_slot"], 2)
            self.assertEqual(entries[1]["item_name"], "Hookshot")
            self.assertTrue(local_seeded_reward_requires_bridge_delivery(
                bonuses[0]["bridge_key"], "music_chest_bonus", "ITEM_POTION"))

    def test_duplicate_receipts_keep_two_distinct_reward_deliveries(self):
        from .. import KH3World
        from ..Client import KH3Context

        slot = paired_slot_data()
        parent = slot["bridge"]["locations"][0]
        bonus = next(row for row in slot["bridge"]["locations"]
                     if row.get("music_chest_parent_location_id") == parent["ap_location_id"])
        with TemporaryDirectory() as directory:
            ctx = KH3Context.__new__(KH3Context)
            ctx.bridge_dir = Path(directory)
            ctx.game = "Kingdom Hearts III"
            ctx.slot_data = slot
            ctx.items_received = [SimpleNamespace(
                item=KH3World.item_name_to_id["Potion"], location=row["ap_location_id"], player=1, flags=0,
            ) for row in (parent, bonus, bonus)]
            ctx.item_names = SimpleNamespace(lookup_in_game=lambda *_: "Potion")
            ctx.location_names = SimpleNamespace(lookup_in_slot=lambda *_: "Chest")
            ctx.player_names = {1: "Sora"}
            ctx.location_id_to_bridge_key = {
                row["ap_location_id"]: row["bridge_key"] for row in slot["bridge"]["locations"]}
            items = ctx.build_items_payload()["items"]
            self.assertEqual(len(items), 2)
            delivered_bonus = next(item for item in items if item["location_id"] == bonus["ap_location_id"])
            self.assertTrue(bonus["bridge_delivery_required"])
            self.assertFalse(any(key.startswith("kh3_") for key in delivered_bonus))
            self.assertEqual(delivered_bonus["location_name"], bonus["ap_location_name"])


class TestMusicChestBonusWorld(KH3TestBase):
    options = {"music_chest_count": 8, "music_chest_vanilla_conversion": 120}

    def test_bonus_count_pool_and_tracker_reconstruction(self):
        self.assertNotIn("music_chest_bonus_checks", type(self.world.options).type_hints)
        bonuses = [row for row in self.world.active_locations if row["type"] == "music_chest_bonus"]
        self.assertEqual(len(bonuses), 128)
        self.assertEqual(len(self.multiworld.get_unfilled_locations()), len(self.multiworld.itempool))
        slot = json.loads(json.dumps(self.world.build_runtime_slot_data()))
        self.assertEqual(slot["music_chest_bonus_checks"], 1)
        self.assertEqual(len(slot["bridge"]["conditional_checks"]), 129 + len(self.world.extra_vbonus_locations) + sum(
            row["type"] == "character_unlock" and "trigger_bridge_key" in row
            for row in self.world.active_locations))
        self.assertTrue(any(row["trigger_bridge_key"] == ROCK_TITAN_VBONUS_BRIDGE_KEY
                            for row in slot["bridge"]["conditional_checks"]))
        tracker = TrackerTests.build_yamlless_tracker_multiworld(self, convert_to_base_types(slot))
        self.assertEqual(bonuses, [row for row in tracker.worlds[1].active_locations
                                  if row["type"] == "music_chest_bonus"])
        slot["bridge"]["locations"] = [row for row in slot["bridge"]["locations"]
                                        if row["ap_location_name"] != bonuses[0]["name"]]
        with self.assertRaisesRegex(ValueError, "bonus selection"):
            TrackerTests.build_yamlless_tracker_multiworld(self, slot)

    def test_bonus_access_matches_parent_through_item_progression(self):
        pairs = [(self.multiworld.get_location(row["name"], self.player),
                  self.multiworld.get_location(row["music_chest_parent_location_name"], self.player))
                 for row in self.world.active_locations if row["type"] == "music_chest_bonus"]
        state = CollectionState(self.multiworld)
        for bonus, parent in pairs:
            self.assertIs(bonus.parent_region, parent.parent_region)
        progression = [item for item in self.multiworld.itempool if item.advancement]
        for item in [None, *progression]:
            if item is not None:
                state.collect(item, True)
            for bonus, parent in pairs:
                self.assertEqual(bonus.can_reach(state), parent.can_reach(state), bonus.name)

    def test_no_music_chests_have_no_bonus_checks(self):
        self.options = {"music_chest_count": 0, "music_chest_vanilla_conversion": 0}
        self.world_setup(seed=42)
        self.assertFalse(any(row["type"] == "music_chest_bonus" for row in self.world.active_locations))
        slot = json.loads(json.dumps(self.world.build_runtime_slot_data()))
        tracker = TrackerTests.build_yamlless_tracker_multiworld(self, slot)
        self.assertEqual(slot["music_chest_bonus_checks"], 1)
        self.assertFalse(any(row["type"] == "music_chest_bonus" for row in tracker.worlds[1].active_locations))
