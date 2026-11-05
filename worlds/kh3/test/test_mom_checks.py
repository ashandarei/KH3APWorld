from __future__ import annotations

import json
import random
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from BaseClasses import CollectionState

from ..Bridge import build_bridge_slot_data
from ..Data import local_seeded_reward_requires_bridge_delivery
from ..export_ap_kh3_randomized_options import ALWAYS_ON_EVENT_IDS, export_randomized_options_from_bridge_data
from ..Locations import location_name_to_id
from ..MelodyOfMemory import MOM_LOCATION_ID_BASE, MOM_SONGS, select_song_locations, song_location_name
from . import KH3TestBase
from .test_universal_tracker import TestKH3UniversalTracker


class TestMoMCatalog(unittest.TestCase):
    def test_seed_export_delivers_local_reward_without_a_native_song_table(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            defaults, extracted = root / "defaults.json", root / "extracted.json"
            defaults.write_text(json.dumps({"Event": {
                key: {"RandomizedItem": "ITEM_POTION\0"} for key in ALWAYS_ON_EVENT_IDS
            }}))
            extracted.write_text(json.dumps({"items": [{"name": "Potion", "id": "ITEM_POTION"}], "locations": []}))
            rows = build_bridge_slot_data(select_song_locations(random.Random(1), 1, 0), 7)["locations"]
            rows[0].update(assigned_item_game="Kingdom Hearts III", assigned_item_name="Potion",
                           assigned_item_player=1, assigned_item_player_name="KH3")
            result = export_randomized_options_from_bridge_data(
                default_kh3=defaults, default_kh3_overrides=None, extracted_data_path=extracted,
                slot=1, slot_name="KH3", archive_name="AP_test.zip", bridge_locations=rows,
            )
            self.assertNotIn("MelodyOfMemory", result.randomized_options)
            placement = result.placement_summary["locations"][0]
            self.assertTrue(placement["bridge_delivery_required"])
            self.assertEqual("ITEM_POTION", placement["runtime_token"])

    def test_catalog_names_and_ids_are_unique_and_do_not_collide(self):
        self.assertEqual(128, len(MOM_SONGS))
        self.assertEqual(128, len({song_location_name(s) for s in MOM_SONGS}))
        self.assertEqual(len(location_name_to_id), len(set(location_name_to_id.values())))
        for song in MOM_SONGS:
            self.assertEqual(MOM_LOCATION_ID_BASE + song["song_id"], location_name_to_id[song_location_name(song)])

    def test_seeded_selection_and_disabled_rng_stability(self):
        a = select_song_locations(random.Random(10), 5, 0)
        self.assertEqual(a, select_song_locations(random.Random(10), 5, 0))
        self.assertNotEqual(a, select_song_locations(random.Random(11), 5, 0))
        self.assertEqual(5, len({r["mom_song_id"] for r in a}))
        rng = random.Random(10)
        state = rng.getstate()
        self.assertEqual([], select_song_locations(rng, 0, 0))
        self.assertEqual(state, rng.getstate())

    def test_tracker_rejects_missing_or_duplicate_selection(self):
        rows = select_song_locations(random.Random(1), 5, 0)
        slot = {"bridge": build_bridge_slot_data(rows, 7)}
        self.assertEqual(rows, select_song_locations(random.Random(99), 5, 0, slot))
        slot["bridge"]["locations"][1] = slot["bridge"]["locations"][0]
        with self.assertRaises(ValueError):
            select_song_locations(random.Random(), 5, 0, slot)


class TestMoMWorld(KH3TestBase):
    def test_disabled_by_default(self):
        self.assertFalse(any(r["type"] == "melody_of_memory" for r in self.world.active_locations))
        self.assertEqual(0, self.world.build_runtime_slot_data()["music_chest_count"])

    def test_checks_have_items_are_accessible_and_export_for_bridge_delivery(self):
        self.options = {"music_chest_count": 5, "melody_of_memory_difficulty": 0}
        self.world_setup(seed=311)
        rows = [r for r in self.world.active_locations if r["type"] == "melody_of_memory"]
        self.assertEqual(5, len(rows))
        self.assertEqual(len(self.multiworld.get_unfilled_locations()), len(self.multiworld.itempool))
        for row in rows:
            location = self.multiworld.get_location(row["name"], self.player)
            self.assertEqual(location.parent_region.can_reach(CollectionState(self.multiworld)),
                             location.can_reach(CollectionState(self.multiworld)))
            # Starting-area placements can forbid progression; all song checks
            # still accept filler. Placement restrictions are covered in test_world_logic.
            self.assertTrue(location.item_rule(self.world.create_item("Potion")))
        slot = self.world.build_runtime_slot_data()
        bridge_rows = [r for r in slot["bridge"]["locations"] if r["type"] == "melody_of_memory"]
        self.assertEqual(5, len(bridge_rows))
        for row in bridge_rows:
            self.assertEqual(0, row["mom_difficulty"])
            self.assertTrue(row["bridge_delivery_required"])
            self.assertTrue(local_seeded_reward_requires_bridge_delivery(row["bridge_key"], row["type"], "ITEM_POTION"))
        tracker = TestKH3UniversalTracker.build_yamlless_tracker_multiworld(self, json.loads(json.dumps(slot)))
        self.assertEqual(rows, [r for r in tracker.worlds[1].active_locations if r["type"] == "melody_of_memory"])

    def test_maximum_pool_and_each_difficulty(self):
        for difficulty in range(3):
            self.options = {"music_chest_count": 8, "melody_of_memory_difficulty": difficulty}
            self.world_setup(seed=312)
            rows = [r for r in self.world.active_locations if r["type"] == "melody_of_memory"]
            self.assertEqual(8, len(rows))
            self.assertEqual({difficulty}, {r["mom_difficulty"] for r in rows})
            self.assertEqual(len(self.multiworld.get_unfilled_locations()), len(self.multiworld.itempool))
