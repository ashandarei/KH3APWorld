from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from ..Bridge import BRIDGE_PROTOCOL_NAME, BRIDGE_PROTOCOL_VERSION
from ..BridgeSession import KH3BridgeSession
from .test_helpers import (
    KH3BridgeTestCase,
    apply_native_item_event,
)


class TestKH3BridgeSessionAutoAck(KH3BridgeTestCase):
    def test_apply_runtime_item_apply_event_does_not_advance_auto_current_slot_after_ack(self) -> None:
        with TemporaryDirectory() as temp_dir:
            bridge_dir = Path(temp_dir)
            slot_data = {
                "protocol_name": BRIDGE_PROTOCOL_NAME,
                "protocol_version": BRIDGE_PROTOCOL_VERSION,
                "bridge": {
                    "protocol_name": BRIDGE_PROTOCOL_NAME,
                    "protocol_version": BRIDGE_PROTOCOL_VERSION,
                    "locations": [],
                },
            }
            ap_items = {
                "protocol_name": BRIDGE_PROTOCOL_NAME,
                "protocol_version": BRIDGE_PROTOCOL_VERSION,
                "items": [
                    {
                        "index": index,
                        "item_id": index,
                        "item_name": f"Potion {index}",
                        "location_id": 2000 + index,
                        "location_name": f"Location {index}",
                        "from_player": 2,
                        "from_player_name": "zelda",
                        "flags": 0,
                        "kh3_runtime_token": "ITEM_POTION",
                        "kh3_grant_method": "bp_tres_get_item",
                    }
                    for index in range(3)
                ],
            }

            self._write_json(bridge_dir / "slot_data.json", slot_data)
            self._write_json(bridge_dir / "ap_items.json", ap_items)
            self._write_json(bridge_dir / "runtime_current_save_slot.json", {
                "protocol_name": BRIDGE_PROTOCOL_NAME,
                "protocol_version": BRIDGE_PROTOCOL_VERSION,
                "event": "runtime_current_save_slot",
                "status": "known",
                "source": "mapname_loaded_slot_cutover",
                "save_slot": "AUTO",
                "save_slot_label": "AUTO",
                "slot_file": "kh3sv2/data/KHIII_slot0.bin",
                "observed_at_utc": "2026-05-05T12:00:00+00:00",
                "ap_index_at_save": 0,
                "last_applied_index_at_save": 0,
            })
            context_time = datetime.now(timezone.utc)
            self._write_ready_runtime_context(bridge_dir, context_time.isoformat())

            session = KH3BridgeSession(bridge_dir)
            result = apply_native_item_event(session, {
                "protocol_name": BRIDGE_PROTOCOL_NAME,
                "protocol_version": BRIDGE_PROTOCOL_VERSION,
                "request_id": "ap-replay-2-20260505T120001.000000Z",
                "applied_indices": [1, 2],
                "status": "applied",
                "action_kind": "ap_replay_batch",
                "applied_at": (context_time + timedelta(seconds=1)).isoformat(),
            })

            self.assertEqual(result["status"], "applied")
            self.assertNotIn("runtime_auto_current_save_slot_update", result)
            current_slot = json.loads((bridge_dir / "runtime_current_save_slot.json").read_text(encoding="utf-8"))
            self.assertEqual(current_slot["save_slot"], "AUTO")
            self.assertEqual(current_slot["last_applied_index_at_save"], 0)
            self.assertNotIn("last_auto_advanced_request_id", current_slot)
