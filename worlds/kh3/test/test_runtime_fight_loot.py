from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from ..Bridge import BRIDGE_PROTOCOL_NAME, BRIDGE_PROTOCOL_VERSION
from ..BridgeSession import KH3BridgeSession
from ..Client import KH3Context
from .test_helpers import KH3BridgeTestCase


class TestKH3BridgeSessionAutoAck(KH3BridgeTestCase):
    def test_snapshot_reconcile_new_foreign_lucky_mark_leaves_runtime_popup_to_native(self) -> None:
        with TemporaryDirectory() as temp_dir:
            bridge_dir = Path(temp_dir)
            slot_data = {
                "protocol_name": BRIDGE_PROTOCOL_NAME,
                "protocol_version": BRIDGE_PROTOCOL_VERSION,
                "bridge": {
                    "protocol_name": BRIDGE_PROTOCOL_NAME,
                    "protocol_version": BRIDGE_PROTOCOL_VERSION,
                    "locations": [
                        {
                            "bridge_key": "LuckyMark:10",
                            "source_table": "LuckyMark",
                            "source_id": "10",
                            "ap_location_name": "Lucky Emblems - Milestone 10",
                            "ap_location_id": 2010,
                            "world": "Lucky Emblems",
                            "assigned_item_name": "AP Boost",
                            "assigned_item_player": 2,
                            "assigned_item_player_name": "KH3-EZ",
                            "assigned_item_game": "Kingdom Hearts III",
                            "assigned_item_classification": 1,
                            "is_foreign_item": True,
                        }
                    ],
                },
            }

            self._write_json(bridge_dir / "slot_data.json", slot_data)

            session = KH3BridgeSession(bridge_dir)
            result = session.reconcile_snapshot(["LuckyMark:10"], False)

            self.assertEqual(result["newly_reported_keys"], ["LuckyMark:10"])
            self.assertFalse((bridge_dir / "runtime_ap_message.json").exists())
            self.assertFalse((bridge_dir / "bridge_notifications.json").exists())


class TestKH3ItemConsumerPlan(unittest.TestCase):
    def test_client_preserves_same_seed_offline_runtime_loot_on_restart(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bridge_root = root / "bridge"
            mods_dir = root / "~mods"
            pak_path = mods_dir / "pakchunk9-PlayerKH-randomizer-AP_85269139745991038903-1_99_P.pak"
            mods_dir.mkdir()
            pak_path.write_bytes(b"pak")
            preexisting_bridge_dir = bridge_root / "85269139745991038903_PlayerKH"
            preexisting_bridge_dir.mkdir(parents=True)
            slot_data = {
                "protocol_name": BRIDGE_PROTOCOL_NAME,
                "protocol_version": BRIDGE_PROTOCOL_VERSION,
                "bridge": {
                    "protocol_name": BRIDGE_PROTOCOL_NAME,
                    "protocol_version": BRIDGE_PROTOCOL_VERSION,
                    "locations": [
                        {
                            "ap_location_id": 1263022969,
                            "ap_location_name": "Arendelle - Large Chest 1",
                            "bridge_key": "TreasureFZ:FZ_LBOX_001",
                            "source_table": "TreasureFZ",
                            "source_id": "FZ_LBOX_001",
                            "world": "Arendelle",
                            "type": "treasure",
                        }
                    ],
                },
            }
            metadata = {
                "format": "kh3_ap_seed_metadata",
                "format_version": 1,
                "seed": "AP_85269139745991038903-1-PlayerKH",
                "slot": 1,
                "slot_name": "PlayerKH",
                "slot_data": slot_data,
                "foreign_item_manifest": {"archive_name": "AP_85269139745991038903.zip"},
            }
            (preexisting_bridge_dir / "kh3_ap_seed_metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
            (preexisting_bridge_dir / "game_state.json").write_text(json.dumps({
                "protocol_name": BRIDGE_PROTOCOL_NAME,
                "protocol_version": BRIDGE_PROTOCOL_VERSION,
                "seq": 4,
                "checked_locations": [],
                "goal_complete": False,
            }), encoding="utf-8")
            (preexisting_bridge_dir / "ap_status.json").write_text(json.dumps({
                "protocol_name": BRIDGE_PROTOCOL_NAME,
                "protocol_version": BRIDGE_PROTOCOL_VERSION,
                "connected": False,
                "checked_locations": ["TreasureTT:TT_SBOX_001"],
                "checked_location_count": 1,
                "goal_complete": False,
            }), encoding="utf-8")
            (preexisting_bridge_dir / "runtime_loot_event.json").write_text(json.dumps({
                "protocol_name": BRIDGE_PROTOCOL_NAME,
                "protocol_version": BRIDGE_PROTOCOL_VERSION,
                "type": "treasure",
                "bridge_key": "TreasureFZ:FZ_LBOX_001",
                "opened_at": "2026-04-29T12:00:00+00:00",
            }), encoding="utf-8")

            ctx = KH3Context.__new__(KH3Context)
            ctx.bridge_dir = None
            ctx.bridge_root_override = bridge_root
            ctx.kh3_mods_dir = mods_dir
            ctx.seed_name = None
            ctx.auth = None
            ctx.integrated_bridge_enabled = False
            ctx.server_address = None
            ctx.team = None
            ctx.bridge_save_dir = root / "synthetic-saves"
            ctx.kh3_dir = root / "KINGDOM HEARTS III"
            ctx.probe_config_path = ctx.kh3_dir / "Binaries" / "Win64" / "Mods" / "KH3APProbeCpp" / "config.lua"
            ctx.location_key_to_id = {}
            ctx.location_id_to_bridge_key = {}

            with patch("worlds.kh3.Client.load_installed_kh3_ap_seed_metadata", return_value=(metadata, pak_path)):
                self.assertTrue(ctx.try_initialize_offline_bridge_from_installed_pak())

            preserved_game_state = json.loads((ctx.bridge_dir / "game_state.json").read_text(encoding="utf-8"))
            self.assertEqual(preserved_game_state["seq"], 4)
            self.assertNotIn("reset_reason", preserved_game_state)
            self.assertTrue((ctx.bridge_dir / "runtime_loot_event.json").exists())
            written_status = json.loads((ctx.bridge_dir / "ap_status.json").read_text(encoding="utf-8"))
            self.assertEqual(written_status["checked_locations"], ["TreasureTT:TT_SBOX_001"])
            self.assertEqual(written_status["checked_location_count"], 1)
