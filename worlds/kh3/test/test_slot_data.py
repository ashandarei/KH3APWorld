from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from BaseClasses import Item, ItemClassification
from NetUtils import convert_to_base_types
from test.general import setup_multiworld

from .. import KH3Container, KH3World
from ..Bridge import BridgeProtocolError, KH3_WORLD_VERSION
from ..PakGenerator import load_kh3_container_data
from ..SlotData import decode_slot_data, encode_slot_data


class TestCompactSlotData(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.world = setup_multiworld(KH3World, seed=9302026, options={
            "include_space": 1, "music_chest_count": 8,
            "music_chest_vanilla_conversion": 120,
            "melody_of_memory_difficulty": 2, "moogle_shop_checks": 20,
        }).worlds[1]
        # Exercise repeated local assignments and a foreign game's metadata.
        for location in cls.world.multiworld.get_unfilled_locations():
            location.item = cls.world.create_item("Potion")
        mw = cls.world.multiworld
        mw.player_name[2] = "Remote Player"
        mw.game[2] = "A Different Game"
        mw.get_location(cls.world.active_locations[0]["name"], 1).item = Item(
            "Foreign Progression", ItemClassification.progression, 987654321, 2)
        cls.runtime = cls.world.build_runtime_slot_data()

    def test_full_round_trip_preserves_all_runtime_fields_and_shrinks_payload(self):
        wire = self.world.fill_slot_data()
        self.assertEqual(wire["kh3_seed"]["seed_creation_version"], KH3_WORLD_VERSION)
        self.assertNotIn("locations", wire["bridge"])
        self.assertEqual(decode_slot_data(wire), self.runtime)
        self.assertLess(len(json.dumps(wire)), len(json.dumps(self.runtime)) * 0.4)
        self.assertEqual(json.loads(json.dumps(decode_slot_data(convert_to_base_types(wire)))), self.runtime)

    def test_reconstruction_does_not_mutate_wire_or_cached_catalog(self):
        wire = encode_slot_data(self.runtime)
        snapshot = copy.deepcopy(wire)
        decoded = decode_slot_data(wire)
        decoded["bridge"]["locations"][0]["ap_location_name"] = "changed"
        self.assertEqual(wire, snapshot)
        self.assertEqual(decode_slot_data(wire), self.runtime)

    def test_local_seed_storage_and_registered_reads_round_trip(self):
        from ..bridge_support.files import atomic_write_json, load_json_file, load_optional_json_view
        from ..RuntimeSeed import slot_payload
        from ..transport.pipe_transport import register, unregister
        expected = slot_payload(self.runtime)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "slot_data.json"
            atomic_write_json(path, self.runtime)
            stored = json.loads(path.read_text())
            self.assertIn("location_catalog", stored)
            self.assertNotIn("locations", stored["bridge"])
            self.assertLess(path.stat().st_size, len(json.dumps(expected).encode()) * 0.4)
            self.assertEqual(load_json_file(path), expected)
            transport = register(path.parent)
            try:
                first = load_optional_json_view(path)
                self.assertEqual(first, expected)
                self.assertIs(load_optional_json_view(path), first)
                atomic_write_json(path, self.runtime)
                self.assertEqual(load_json_file(path), expected)
                self.assertIn("location_catalog", json.loads(path.read_text()))
            finally:
                unregister(transport)

    def test_embedded_pak_metadata_expands_at_import(self):
        from ..client_support.seed_paks import _decode_kh3_ap_seed_metadata
        from ..RuntimeSeed import encode_document, metadata_payload
        metadata = {"format": "kh3_ap_seed_metadata", "format_version": 1,
                    "seed": "test", "slot": 1, "slot_data": self.runtime}
        encoded = encode_document("kh3_ap_seed_metadata.json", metadata_payload(metadata))
        self.assertIn("location_catalog", json.loads(encoded)["slot_data"])
        self.assertEqual(_decode_kh3_ap_seed_metadata(encoded, source="test"), metadata_payload(metadata))

    def test_local_catalog_mismatch_fails_read_without_partial_rows(self):
        from ..bridge_support.files import load_json_file
        from ..RuntimeSeed import encode_document
        wire = json.loads(encode_document("slot_data.json", self.runtime))
        wire["location_catalog"]["sha256"] = "wrong"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "slot_data.json"
            path.write_text(json.dumps(wire))
            with self.assertRaisesRegex(BridgeProtocolError, "catalog mismatch"):
                load_json_file(path)

    def test_rejects_old_expanded_payload_and_catalog_mismatch(self):
        with self.assertRaisesRegex(BridgeProtocolError, "Generate a new seed"):
            decode_slot_data(self.runtime)
        wire = encode_slot_data(self.runtime)
        wire["location_catalog"]["sha256"] = "wrong"
        with self.assertRaisesRegex(BridgeProtocolError, "catalog mismatch"):
            decode_slot_data(wire)

    def test_rejects_malformed_references(self):
        for change in (lambda m: m.update(version=999),
                       lambda m: m["entries"].append(m["entries"][0]),
                       lambda m: m["entries"][0].__setitem__(0, -1),
                       lambda m: m["entries"][0].__setitem__(1, -1),
                       lambda m: m["entries"][0][2].update(ap_location_id=1)):
            wire = encode_slot_data(self.runtime)
            change(wire["location_catalog"])
            with self.assertRaises(BridgeProtocolError):
                decode_slot_data(wire)

    def test_player_container_uses_wire_format_and_loads_runtime_format(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "test.apkh3"
            data = self.world.build_seed_container_data()
            self.assertNotIn("locations", data["slot_data"]["bridge"])
            KH3Container(path=str(path), player=1, player_name="Player1", data=data).write()
            loaded = load_kh3_container_data(path)
            self.assertEqual(loaded["slot_data"], self.runtime)
