import copy
import json
import random
from unittest import TestCase

from NetUtils import convert_to_base_types
from Options import OptionError
from ..MelodyOfMemory import select_music_chests
from ..MoMSongCatalog import (
    MOM_SONG_BY_ID, MOM_SONG_CATALOG, MOM_SONG_GROUPS, MOM_SONG_KEYS,
    MOM_SELECTOR_IDS, draw_song_ids, eligible_song_ids,
)
from ..Options import MelodyOfMemorySongWhitelist, MelodyOfMemorySongBlacklist
from . import KH3TestBase
from .test_universal_tracker import TestKH3UniversalTracker


class TestSongSelection(TestCase):
    def test_catalog_coverage_sources_and_no_holiday_categories(self):
        self.assertEqual(128, len(MOM_SONG_KEYS))
        self.assertEqual(128, len(set(MOM_SONG_KEYS.values())))
        for kind in ("theme", "source"):
            ids = {i for group in MOM_SONG_GROUPS if group["kind"] == kind for i in group["song_ids"]}
            self.assertEqual(set(MOM_SONG_BY_ID), ids)
        for group in MOM_SONG_GROUPS:
            self.assertTrue(group["song_ids"])
            self.assertEqual(len(group["song_ids"]), len(set(group["song_ids"])))
            self.assertLessEqual(set(group["song_ids"]), set(MOM_SONG_BY_ID))
            self.assertNotIn(group["label"], {"Halloween", "Christmas", "Holidays & Seasonal"})
        self.assertEqual({8, 9, 40, 41}, MOM_SELECTOR_IDS["World: The Nightmare Before Christmas"])
        self.assertEqual(10, sum(g["kind"] == "theme" for g in MOM_SONG_GROUPS))
        self.assertEqual(29, sum(g["kind"] == "world" for g in MOM_SONG_GROUPS))
        self.assertEqual(12, sum(g["kind"] == "source" for g in MOM_SONG_GROUPS))
        for song_id in MOM_SONG_BY_ID:
            self.assertEqual(1, sum(song_id in g["song_ids"] for g in MOM_SONG_GROUPS if g["kind"] == "source"))

    def test_whitelist_exclusive_and_wins_in_both_directions(self):
        world = "World: Traverse Town"
        song = MOM_SONG_KEYS[2]
        self.assertEqual(list(sorted(MOM_SONG_BY_ID)), eligible_song_ids())
        self.assertNotIn(2, eligible_song_ids(blacklist=[world]))
        self.assertEqual([2], eligible_song_ids([song], [world, song]))
        self.assertEqual(sorted(MOM_SELECTOR_IDS[world]), eligible_song_ids([world], [song, world]))
        self.assertEqual([2, 3], eligible_song_ids([song, MOM_SONG_KEYS[3]], [song]))

    def test_arrangements_remain_individually_selectable(self):
        self.assertNotEqual(MOM_SONG_KEYS[3], MOM_SONG_KEYS[144])
        self.assertEqual([144], eligible_song_ids([MOM_SONG_KEYS[144]], [MOM_SONG_KEYS[3]]))

    def test_seeded_shuffled_passes_and_empty_pool(self):
        pool = [0, 2, 3]
        songs = draw_song_ids(random.Random(3), 11, pool)
        self.assertEqual(songs, draw_song_ids(random.Random(3), 11, pool))
        for start in (0, 3, 6):
            self.assertEqual(set(pool), set(songs[start:start + 3]))
        self.assertEqual(2, len(set(songs[9:])))
        self.assertEqual([2] * 8, draw_song_ids(random.Random(3), 8, [2]))
        with self.assertRaisesRegex(ValueError, "No eligible"):
            draw_song_ids(random.Random(), 1, [])
        rng = random.Random(4)
        before = rng.getstate()
        self.assertEqual([], draw_song_ids(rng, 0, []))
        self.assertEqual(before, rng.getstate())
        with self.assertRaisesRegex(ValueError, "Unknown Melody"):
            eligible_song_ids(blacklist=["typo"])

    def test_disabled_empty_pool_and_requested_assignment_count(self):
        all_sources = [g["key"] for g in MOM_SONG_GROUPS if g["kind"] == "source"]
        self.assertEqual([], select_music_chests(random.Random(), [], 0, 0, 0, blacklist=all_sources))
        with self.assertRaisesRegex(OptionError, "No eligible"):
            select_music_chests(random.Random(), [], 1, 0, 0, blacklist=all_sources)
        converted = [{"type": "treasure", "source_table": "TreasureRA", "source_id": "1"}]
        with self.assertRaisesRegex(OptionError, "No eligible"):
            select_music_chests(random.Random(), converted, 0, 1, 0, blacklist=all_sources)
        self.assertNotIn("mom_song_id", converted[0])
        rows = [{"type": "treasure", "source_table": "TreasureRA", "source_id": str(i)} for i in range(150)]
        added = select_music_chests(random.Random(3), rows, 8, 128, 0, whitelist=[MOM_SONG_KEYS[2]])
        self.assertEqual(136, len(added) + sum("mom_song_id" in r for r in rows))
        self.assertEqual({2}, {r["mom_song_id"] for r in added + rows if "mom_song_id" in r})

    def test_schema_exposes_same_song_and_category_keys(self):
        from .. import KH3World
        for option in (MelodyOfMemorySongWhitelist, MelodyOfMemorySongBlacklist):
            self.assertEqual(set(MOM_SELECTOR_IDS), set(option.valid_keys))
            self.assertEqual(MOM_SONG_CATALOG, option.song_catalog)
            instance = option.from_any(["Theme: Disney Film Songs", MOM_SONG_KEYS[144]])
            instance.verify(KH3World, "Player", {})
            with self.assertRaises(Exception):
                option.from_any(["Theme: Typo"]).verify(KH3World, "Player", {})


class TestRepeatedSongWorld(KH3TestBase):
    options = {"music_chest_count": 8, "music_chest_vanilla_conversion": 128,
               "melody_of_memory_song_whitelist": [MOM_SONG_KEYS[2]],
               "melody_of_memory_song_blacklist": ["World: Traverse Town"]}

    def test_tracker_roundtrip_retains_repeats_and_filters(self):
        slot = convert_to_base_types(self.world.build_runtime_slot_data())
        json.dumps(slot)
        tracker = TestKH3UniversalTracker.build_yamlless_tracker_multiworld(self, slot)
        expected = [r for r in self.world.active_locations if "mom_song_id" in r]
        self.assertEqual(136, len(expected))
        self.assertEqual({2}, {r["mom_song_id"] for r in expected})
        self.assertEqual(expected, [r for r in tracker.worlds[1].active_locations if "mom_song_id" in r])
        self.assertEqual(len(self.multiworld.get_unfilled_locations()), len(self.multiworld.itempool))
        bad = copy.deepcopy(slot)
        row = next(r for r in bad["bridge"]["locations"] if "mom_song_id" in r)
        row["mom_song_id"] = 3
        with self.assertRaisesRegex(ValueError, "Invalid"):
            TestKH3UniversalTracker.build_yamlless_tracker_multiworld(self, bad)
