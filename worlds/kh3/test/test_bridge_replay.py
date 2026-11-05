from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

from .test_helpers import KH3BridgeTestCase
from .test_native_reconciliation import native_session


class TestKH3BridgeSessionAutoAck(KH3BridgeTestCase):
    def test_controller_unlock_notice_is_consumed_from_native_event(self) -> None:
        with TemporaryDirectory() as temp_dir:
            bridge_dir = Path(temp_dir)
            session, native = native_session(bridge_dir)
            payload = {"schema": "kh3ap.notification.v1", "owner": "dll", "process_id": native["process_id"],
                "seed_name": native["seed_name"], "slot": native["slot"], "request_id": "native-unlock-0",
                "event": "controller_unlock", "status": "applied", "message": "Magic unlocked",
                "item_name": "Unlock: Magic"}
            self.assertEqual(session.consume_native_notification_event(payload)["status"], "applied")
            self.assertEqual(session.consume_native_notification_event(payload)["status"], "duplicate_event")
            notifications = session.load_bridge_notification_state()["notifications"]
            self.assertEqual(len(notifications), 1)
            self.assertEqual(notifications[0]["message"], "Magic unlocked")
            self.assertFalse((bridge_dir / "runtime_ap_message.json").exists())
