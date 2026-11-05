import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from ..export_ap_kh3_randomized_options import ALWAYS_ON_EVENT_IDS, export_randomized_options_from_bridge_data


class TestApEventTracking(unittest.TestCase):
    def test_local_event_rewards_are_suppressed_and_delivered_by_ap(self):
        items = {"Potion": "ITEM_POTION", "Ability: Aero Boost": "ETresAbilityKind::AERO_UP",
                 "Pride Heartbinder": "KEY_ITEM09", "'Ohana Heartbinder": "KEY_ITEM08"}
        for name, token in items.items():
            with self.subTest(item=name), TemporaryDirectory() as temp:
                root = Path(temp)
                defaults, extracted = root / "defaults.json", root / "extracted.json"
                defaults.write_text(json.dumps({"Event": {
                    key: {"RandomizedItem": "ITEM_POTION\0"}
                    for key in (*ALWAYS_ON_EVENT_IDS, "EVENT_003")
                }}))
                extracted.write_text(json.dumps({"items": [
                    {"name": key, "id": value} for key, value in items.items()
                ], "locations": []}))
                result = export_randomized_options_from_bridge_data(
                    default_kh3=defaults, default_kh3_overrides=None,
                    extracted_data_path=extracted, slot=1, slot_name="Sora", archive_name="AP_test.zip",
                    bridge_locations=[{
                        "bridge_key": "Event:EVENT_003", "type": "event", "source_table": "Event",
                        "source_id": "EVENT_003", "field_name": "RandomizedItem",
                        "ap_location_id": 1001, "ap_location_name": "Golden Herc",
                        "assigned_item_game": "Kingdom Hearts III", "assigned_item_name": name,
                        "assigned_item_player": 1, "assigned_item_player_name": "Sora",
                    }])
                row = result.placement_summary["locations"][0]
                self.assertTrue(row["bridge_delivery_required"])
                self.assertEqual(row["runtime_token"], token)
                self.assertEqual(result.randomized_options["Event"]["EVENT_003"]["RandomizedItem"].rstrip("\0"),
                                 "ETresVictoryBonusKind::NONE")
