from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from ..Bridge import BRIDGE_PROTOCOL_NAME, BRIDGE_PROTOCOL_VERSION
from ..Data import (
    LIVE_BONUS_STAT_RUNTIME_TOKENS,
    load_extracted_data,
    resolve_runtime_token_display_name,
)
from ..export_ap_kh3_randomized_options import (
    ALWAYS_ON_EVENT_IDS,
    build_item_name_to_token,
    export_randomized_options_from_bridge_data,
)


def publish_chests(root, data, ids):
    payload = {"schema": "kh3ap.chest_progress.v1", **data["kh3_seed"], "completed": sorted(ids)}
    (root / "chest_progress.json").write_text(json.dumps(payload))
    return payload


def slot_data() -> dict:
    return {
        "protocol_name": BRIDGE_PROTOCOL_NAME, "protocol_version": BRIDGE_PROTOCOL_VERSION,
        "kh3_seed": {"seed_name": "chest-test", "slot": 1},
        "bridge": {
            "protocol_name": BRIDGE_PROTOCOL_NAME, "protocol_version": BRIDGE_PROTOCOL_VERSION,
            "chest_persistence_version": 1,
            "locations": [
                {"bridge_key": "TreasureTT:TT_LBOX_001", "type": "treasure", "source_table": "TreasureTT",
                 "source_id": "TT_LBOX_001", "ap_location_id": 1001, "ap_location_name": "Chest 1"},
                {"bridge_key": "TreasureHE:HE_SBOX_001", "type": "treasure", "source_table": "TreasureHE",
                 "source_id": "HE_SBOX_001", "ap_location_id": 1002, "ap_location_name": "Chest 2"},
            ],
        },
    }


class TestChestPersistence(unittest.TestCase):
    def test_standalone_stat_rewards_export_without_extracted_item_rows(self):
        mapping = build_item_name_to_token({"items": []})
        for token in LIVE_BONUS_STAT_RUNTIME_TOKENS:
            with self.subTest(token=token):
                self.assertEqual(mapping[resolve_runtime_token_display_name(token)], token)

    def test_catalog_has_unique_chest_ids(self):
        ids = {location["source_id"] for location in load_extracted_data()["locations"]
               if location.get("type") == "treasure"}
        self.assertGreater(len(ids), 200)
        self.assertEqual(len(ids), len(set(ids)))

    def test_exports_suppress_local_and_remote_native_chest_rewards(self):
        items = {"Potion": "ITEM_POTION", "Ability: Glide": "ETresAbilityKind::GLIDE",
                 "Magic: Fire": "ETresVictoryBonusKind::FIRE"}
        for recipient in (1, 2):
            for name, token in items.items():
                with self.subTest(recipient=recipient, item=name), TemporaryDirectory() as temp:
                    root = Path(temp)
                    defaults = root / "DefaultKH3.json"
                    extracted = root / "extracted.json"
                    defaults.write_text(json.dumps({
                        "Event": {key: {"RandomizedItem": "ITEM_POTION\0"} for key in ALWAYS_ON_EVENT_IDS},
                        "TreasureTT": {"TT_LBOX_001": {"Treasure": "ITEM_POTION\0"}},
                    }), encoding="utf-8")
                    extracted.write_text(json.dumps({
                        "items": [{"name": key, "id": value} for key, value in items.items()], "locations": [],
                    }), encoding="utf-8")
                    location = {
                        **slot_data()["bridge"]["locations"][0], "assigned_item_game": "Kingdom Hearts III",
                        "assigned_item_name": name, "assigned_item_player": recipient,
                        "assigned_item_player_name": "Sora" if recipient == 1 else "Riku",
                    }
                    location["chest_persistence_version"] = 1
                    result = export_randomized_options_from_bridge_data(
                        default_kh3=defaults, default_kh3_overrides=None, extracted_data_path=extracted,
                        bridge_locations=[location], slot=1, slot_name="Sora", archive_name="AP_test.zip",
                    )
                    placement = result.placement_summary["locations"][0]
                    native = result.randomized_options["TreasureTT"]["TT_LBOX_001"]["Treasure"].rstrip("\0")
                    self.assertEqual(native, "ETresVictoryBonusKind::NONE")
                    if recipient == 1:
                        self.assertTrue(placement["bridge_delivery_required"])
                        self.assertEqual(placement["runtime_token"], token)
