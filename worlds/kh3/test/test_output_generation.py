"""Final output preserves placements while sharing work across AP's threads."""

import json
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier
from unittest import TestCase
from unittest.mock import patch

from BaseClasses import Item, ItemClassification, Location, Region
from test.general import TestWorld as ForeignWorld
from test.general import setup_multiworld

from .. import KH3_CONTAINER_PAYLOAD, KH3World
from ..PackageResources import safe_file_fragment
from ..RuntimeSeed import hint_reports
from ..SlotData import decode_slot_data
from ..seedgen.localization import make_hints


class TestOutputGeneration(TestCase):
    def setUp(self):
        self.mw = setup_multiworld([KH3World, KH3World, ForeignWorld], seed=610065002,
                                  options={"level_up_limit": 1})
        self.mw.seed_name = "output-test"
        for player in (1, 2):
            world = self.mw.worlds[player]
            for location in self.mw.get_unfilled_locations(player):
                location.item = world.create_item("Potion")
        self.world = self.mw.worlds[1]
        self.peer = self.mw.worlds[2]
        self.exported = next(loc for loc in self.mw.get_locations(2) if loc.address is not None)
        self.exported.item = self.world.create_item("Magic: Cure")
        next(iter(self.mw.get_locations(1))).item = self.peer.create_item("Potion")
        region = Region("Foreign", 3, self.mw)
        self.mw.regions.append(region)
        region.locations.extend([
            Location(3, "Foreign remote", 900001, region),
            Location(3, "Foreign event", None, region),
            Location(3, "Foreign no-code", 900002, region),
            Location(3, "Foreign item", 900003, region),
        ])
        region.locations[0].item = self.world.create_item("Magic: Fire")
        region.locations[1].item = self.world.create_item("Potion")
        region.locations[2].item = Item("Event", ItemClassification.progression, None, 1)
        region.locations[3].item = Item("Foreign reward", ItemClassification.useful, 900004, 3)

    def test_one_room_scan_preserves_both_players_and_foreign_hints(self):
        expected = {player: self.mw.worlds[player].fill_slot_data() for player in (1, 2)}
        with patch.object(self.mw, "get_locations", wraps=self.mw.get_locations) as scan:
            KH3World.stage_pre_output(self.mw)
            for player in (1, 2):
                world = self.mw.worlds[player]
                self.assertEqual(world.fill_slot_data(), expected[player])
                self.assertEqual(world.build_seed_container_data()["slot_data"], expected[player])
            self.assertEqual(scan.call_count, 1)
        hints = expected[1]["kh3_seed"]["secret_reports"]
        self.assertEqual({(row["player"], row["location"]) for rows in hints["reports"].values() for row in rows},
                         {(2, self.exported.address), (3, 900001)})
        self.assertTrue(any(f"Foreign remote in {self.mw.get_player_name(3)}'s {ForeignWorld.game} world" in line
                            for lines in hints["text"].values() for line in lines))

    def test_early_requests_do_not_freeze_partial_placements(self):
        before = self.world.fill_slot_data()
        self.assertIsNot(before, self.world.fill_slot_data())
        self.exported.item = self.peer.create_item("Potion")
        expected = self.world.fill_slot_data()
        self.assertNotEqual(before, expected)
        KH3World.stage_pre_output(self.mw)
        self.assertEqual(self.world.fill_slot_data(), expected)

    def test_concurrent_patch_and_server_requests_build_once(self):
        expected_runtime = self.world.build_runtime_slot_data()
        KH3World.stage_pre_output(self.mw)
        barrier = Barrier(8)

        def read_output():
            barrier.wait(timeout=10)
            return self.world.build_seed_container_data()["slot_data"]

        with patch.object(self.world, "build_runtime_slot_data",
                          wraps=self.world.build_runtime_slot_data) as build:
            with ThreadPoolExecutor(8) as pool:
                results = list(pool.map(lambda _: read_output(), range(8)))
            self.assertEqual(build.call_count, 1)
        self.assertTrue(all(result is results[0] for result in results))
        self.assertEqual(decode_slot_data(results[0]), expected_runtime)

    def test_actual_patch_matches_server_payload(self):
        expected = self.world.fill_slot_data()
        KH3World.stage_pre_output(self.mw)
        with TemporaryDirectory() as directory:
            with ThreadPoolExecutor(2) as pool:
                patch_output = pool.submit(self.world.generate_output, directory)
                server_output = pool.submit(self.world.fill_slot_data)
                patch_output.result(timeout=10)
                self.assertEqual(server_output.result(timeout=10), expected)
            with zipfile.ZipFile(next(Path(directory).glob("*.apkh3"))) as archive:
                self.assertIsNone(archive.testzip())
                payload = json.loads(archive.read(KH3_CONTAINER_PAYLOAD))
            self.assertEqual(payload["slot_data"], expected)
            self.assertNotIn("remote_hint_placements", payload)
            self.assertNotIn("remote_hint_placements", payload["slot_data"]["kh3_seed"])

    def test_index_is_room_local_and_repreparing_refreshes_it(self):
        KH3World.stage_pre_output(self.mw)
        old = self.world.fill_slot_data()
        other = setup_multiworld(KH3World, seed=610065003, options={"level_up_limit": 1})
        KH3World.stage_pre_output(other)
        self.assertFalse(any(other.worlds[1].fill_slot_data()["kh3_seed"]["secret_reports"]["reports"].values()))
        self.exported.item = self.peer.create_item("Potion")
        KH3World.stage_pre_output(self.mw)
        refreshed = self.world.fill_slot_data()
        self.assertNotEqual(old, refreshed)
        self.assertEqual([row for rows in refreshed["kh3_seed"]["secret_reports"]["reports"].values() for row in rows],
                         [{"player": 3, "location": 900001}])

    def test_finished_reports_match_pak_time_selection(self):
        for style in range(4):
            with self.subTest(style=style):
                self.world.options.hint_selection_type.value = style
                runtime = self.world.build_runtime_slot_data()
                config = runtime["kh3_randomizer"]
                seed = safe_file_fragment(runtime["kh3_seed"]["pak_seed"])
                candidates = [
                    dict(row, item_name=row["assigned_item_name"], location_player_slot=1)
                    for row in runtime["bridge"]["locations"] if row["assigned_item_player"] == 1
                ] + [
                    {"ap_location_id": location, "location_description": name, "location_game": game,
                     "location_player_slot": player, "location_player_name": self.mw.get_player_name(player),
                     "item_name": item, "is_remote_location": True}
                    for location, name, game, player, item in [
                        (self.exported.address, self.exported.name, self.world.game, 2, "Magic: Cure"),
                        (900001, "Foreign remote", ForeignWorld.game, 3, "Magic: Fire"),
                    ]
                ]
                reference = {"slot": 1, "placement_summary": {"locations": candidates}}
                finished = {"slot": 1, "slot_data": runtime}
                self.assertEqual(make_hints(seed, config, finished), make_hints(seed, config, reference))
                self.assertEqual(hint_reports(finished), hint_reports(reference))
