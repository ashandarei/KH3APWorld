import copy
import json
import random
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from NetUtils import convert_to_base_types

from ..Bridge import build_bridge_slot_data
from ..client_support.mom_challenges import MoMChallengeService
from ..MelodyOfMemory import MOM_SONGS, select_music_chests
from . import KH3TestBase
from .test_universal_tracker import TestKH3UniversalTracker


class TestMusicChestSelection(TestCase):
    def test_serialized_tracker_data_still_rejects_invalid_chest_identity(self):
        rows = select_music_chests(random.Random(5), [], 1, 0, 1)
        slot = {"bridge": build_bridge_slot_data(rows, 7)}
        changes = {
            "ap_location_id": -1,
            "mom_difficulty": 2,
            "source_id": "WRONG_CHEST",
            "source_table": "WrongTable",
            "world": "Wrong World",
            "music_chest_map": "/Wrong/Map",
        }
        for field in ("music_chest_position", "music_chest_rotation"):
            original = slot["bridge"]["locations"][0][field]
            for invalid in (None, "invalid", {"x": 0}, original[:2], [original[0] + 100, *original[1:]]):
                for encode in (lambda value: value, convert_to_base_types):
                    with self.subTest(field=field, invalid=invalid, encoded=encode is convert_to_base_types):
                        bad_slot = copy.deepcopy(slot)
                        bad_slot["bridge"]["locations"][0][field] = invalid
                        with self.assertRaisesRegex(ValueError, "Invalid additional chest identity"):
                            select_music_chests(random.Random(1), [], 1, 0, 1, encode(bad_slot))
        for field, invalid in changes.items():
            with self.subTest(field=field):
                bad_slot = copy.deepcopy(slot)
                bad_slot["bridge"]["locations"][0][field] = invalid
                with self.assertRaisesRegex(ValueError, "Invalid additional chest identity"):
                    select_music_chests(random.Random(1), [], 1, 0, 1, convert_to_base_types(bad_slot))

    def test_fixed_placements_keep_ids_when_songs_change(self):
        from ..Locations import location_name_to_id
        from ..MelodyOfMemory import MUSIC_CHEST_PLACEMENTS
        from ..Options import MusicChestCount

        with self.assertRaises(Exception):
            MusicChestCount.from_any(9)
        with self.assertRaises(ValueError):
            select_music_chests(random.Random(1), [], 9, 0, 0)
        first = select_music_chests(random.Random(1), [], 8, 0, 0)
        second = select_music_chests(random.Random(2), [], 8, 0, 0)
        self.assertEqual(8, len({r["world"] for r in first}))
        self.assertEqual([r["name"] for r in first], [r["name"] for r in second])
        self.assertNotEqual([r["mom_song_id"] for r in first], [r["mom_song_id"] for r in second])
        for placement in MUSIC_CHEST_PLACEMENTS:
            identity = placement["ap_location_id"]
            self.assertEqual(identity, location_name_to_id[placement["name"]])
            self.assertEqual(1, list(location_name_to_id.values()).count(identity))


    def test_poptracker_export_matches_runtime_catalog(self):
        from ..MelodyOfMemory import MUSIC_CHEST_PLACEMENTS

        export = json.loads(
            (Path(__file__).resolve().parents[3] / "docs/kh3-melody-chests-poptracker.json").read_text()
        )
        self.assertEqual(8, export["options"]["music_chest_count"]["max"])
        for expected, row in zip(MUSIC_CHEST_PLACEMENTS, export["locations"], strict=True):
            self.assertEqual(expected, {k: row[k] for k in expected})
            self.assertEqual("MusicChest:" + expected["treasure_id"], row["bridge_key"])

    def test_requested_conversion_count_and_option_bounds(self):
        from ..Options import MusicChestVanillaConversion

        for count in (0, 1, 32, 128):
            with self.subTest(count=count):
                option = MusicChestVanillaConversion.from_any(count)
                rows = self.candidates()
                select_music_chests(random.Random(5), rows, 0, option.value, 0)
                self.assertEqual(count, sum("mom_song_id" in row for row in rows))
        for value in (True, False):
            with self.assertRaises(TypeError):
                MusicChestVanillaConversion.from_any(value)
        for count in (-1, 129):
            with self.assertRaises(Exception):
                MusicChestVanillaConversion.from_any(count)

    def candidates(self, count=150):
        return [
            {"type": "treasure", "source_table": "TreasureRA", "source_id": f"RA_SBOX_{i:03}"} for i in range(count)
        ]

    def test_unique_song_budget_is_shared_and_selection_is_seeded(self):
        a, b = self.candidates(), self.candidates()
        extras = select_music_chests(random.Random(5), a, 8, True, 2)
        self.assertEqual(extras, select_music_chests(random.Random(5), b, 8, True, 2))
        self.assertEqual(a, b)
        selected = [r for r in a if "mom_song_id" in r]
        self.assertEqual(128, len(selected))
        self.assertEqual(len(MOM_SONGS), len({r["mom_song_id"] for r in extras + selected}))
        self.assertEqual({2}, {r["mom_difficulty"] for r in extras + selected})
        other = self.candidates()
        select_music_chests(random.Random(6), other, 8, True, 2)
        self.assertNotEqual(a, other)

    def test_disabled_does_not_mutate_chests_or_consume_rng(self):
        rows = self.candidates()
        before = copy.deepcopy(rows)
        rng = random.Random(5)
        state = rng.getstate()
        self.assertEqual([], select_music_chests(rng, rows, 0, False, 0))
        self.assertEqual(rows, before)
        self.assertEqual(state, rng.getstate())

    def test_small_chest_pool_and_full_extra_reservation(self):
        rows = self.candidates(3)
        select_music_chests(random.Random(5), rows, 0, True, 1)
        self.assertEqual(3, len([r for r in rows if "mom_song_id" in r]))
        rows = self.candidates()
        select_music_chests(random.Random(5), rows, 8, True, 0)
        self.assertEqual(128, sum("mom_song_id" in r for r in rows))


class TestMusicChestWorld(KH3TestBase):
    options = {"music_chest_count": 5, "music_chest_vanilla_conversion": 32, "melody_of_memory_difficulty": 1}

    def test_tracker_restores_archipelago_serialized_slot_data(self):
        slot = convert_to_base_types(self.world.build_runtime_slot_data())
        placed = next(row for row in slot["bridge"]["locations"] if row.get("music_chest_placement_version") == 1)
        self.assertIsInstance(placed["music_chest_position"], tuple)
        self.assertIsInstance(placed["music_chest_rotation"], tuple)
        tracker = TestKH3UniversalTracker.build_yamlless_tracker_multiworld(self, slot)

        def music_rows(world):
            return [row for row in world.active_locations if "mom_song_id" in row]

        self.assertEqual(music_rows(self.world), music_rows(tracker.worlds[1]))

    def test_server_release_does_not_open_spawned_chests(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            service = MoMChallengeService(root, self.world.build_runtime_slot_data())
            service.publish_clears(root)
            clears = json.loads((root / "runtime_mom_clear_state.json").read_text())
            self.assertEqual([], clears["cleared_locations"])
            self.assertFalse((root / "runtime_music_chests.json").exists())

    def test_tracker_restores_placements_and_rejects_changed_transform(self):
        slot = json.loads(json.dumps(self.world.build_runtime_slot_data()))
        tracker = TestKH3UniversalTracker.build_yamlless_tracker_multiworld(self, copy.deepcopy(slot))

        def select(world):
            return [r for r in world.active_locations if r.get("music_chest_placement_version") == 1]

        self.assertEqual(select(self.world), select(tracker.worlds[1]))
        row = next(r for r in slot["bridge"]["locations"] if r.get("music_chest_placement_version") == 1)
        row["music_chest_position"][0] += 100
        with self.assertRaises(ValueError):
            TestKH3UniversalTracker.build_yamlless_tracker_multiworld(self, slot)

    def test_conversion_preserves_check_count_and_tracker_assignments(self):
        rows = self.world.active_locations
        selected = [r for r in rows if r.get("music_chest_kind") == "vanilla"]
        self.assertEqual(32, len(selected))
        self.assertTrue(all(r["type"] == "treasure" for r in selected))
        self.assertEqual(len(self.multiworld.get_unfilled_locations()), len(self.multiworld.itempool))
        slot = self.world.build_runtime_slot_data()
        tracker = TestKH3UniversalTracker.build_yamlless_tracker_multiworld(self, json.loads(json.dumps(slot)))
        self.assertEqual(
            selected, [r for r in tracker.worlds[1].active_locations if r.get("music_chest_kind") == "vanilla"]
        )

    def test_song_clear_unlocks_vanilla_chest_without_sending_its_check(self):
        slot = self.world.build_runtime_slot_data()
        with TemporaryDirectory() as directory:
            root = Path(directory)
            service = MoMChallengeService(root, slot)
            native = next(i for i, r in service.challenges.items() if r.get("music_chest_kind") == "vanilla")
            extra = next(i for i, r in service.challenges.items() if r.get("music_chest_kind") == "additional")
            service.state["cleared"] = [native, extra]
            self.assertEqual(set(), service.pending_checks(service.poll()))
            service.publish_clears(root)
            clears = json.loads((root / "runtime_mom_clear_state.json").read_text())
            self.assertEqual(sorted([native, extra]), clears["cleared_locations"])
            self.assertNotIn("chests", clears)
