"""Exercise client check/notification effects from committed DLL chest progress."""
import json
import threading
from unittest.mock import Mock

import pytest

from ..bridge_support.files import atomic_write_json
from ..BridgeSession import KH3BridgeSession
from ..ExternalBridge import SaveFilesBackend
from ..SlotData import _catalog
from ..transport.pipe_transport import SET, register, unregister
from .test_helpers import build_slot_data


@pytest.mark.parametrize("foreign", [True, False])
def test_committed_chest_progress_reports_once_across_polls_and_client_restart(tmp_path, foreign):
    key = "TreasureTT:TT_SBOX_001"
    location_id = next(row["ap_location_id"] for row in _catalog()[0].values() if row["bridge_key"] == key)
    location = dict(bridge_key=key, source_table="TreasureTT", source_id="TT_SBOX_001",
        type="treasure", ap_location_id=location_id, ap_location_name="Twilight Town - Small Chest 1",
        world="Twilight Town", is_foreign_item=foreign, assigned_item_name="Missile Tank" if foreign else "Potion",
        assigned_item_player=2 if foreign else 1, assigned_item_player_name="Aes" if foreign else "Sora",
        assigned_item_game="Metroid Fusion" if foreign else "Kingdom Hearts III",
        assigned_item_classification=1)
    slot_data = build_slot_data([location])
    slot_data["kh3_seed"] = dict(seed_name="chest-test", slot=1)
    atomic_write_json(tmp_path / "slot_data.json", slot_data)
    atomic_write_json(tmp_path / "bridge_runtime_status.json", {"kh3_process_pid": 777, "kh3_process_running": True})
    session = KH3BridgeSession(tmp_path)
    transport = register(tmp_path)

    def poll(current_session):
        stop = threading.Event()
        backend = SaveFilesBackend(tmp_path / "synthetic-saves", stop_event=stop)
        backend.reporter = Mock()
        backend._wait_for_next_poll = lambda interval: stop.set()
        assert backend.run(current_session) == 0
        backend.reporter.error.assert_not_called()

    def progress(revision, seed="chest-test"):
        transport.accept([(SET, "runtime_check_state.json", revision, json.dumps(dict(
            schema="kh3ap.check_state.v1", owner="dll", process_id=777, seed_name=seed, slot=1,
            ready=True, checked_locations=[key], goal_complete=False)).encode())])

    try:
        # An actor observation can arrive before a chest actually opens.
        transport.accept([(SET, "runtime_loot_event.json", 1, json.dumps(dict(
            type="treasure", bridge_key=key, opened_at="2026-10-04T01:00:00Z",
            actor_name="g_tt_TreasureBox_TT_SBOX_001")).encode())])
        poll(session)
        assert session.load_game_state()["checked_locations"] == []
        assert not (tmp_path / "bridge_notifications.json").exists()
        assert transport.acks

        progress(2, seed="other-seed")
        poll(session)
        assert session.load_game_state()["checked_locations"] == []

        progress(3)
        poll(session)
        checked = session.load_game_state()
        assert checked["checked_locations"] == [key]
        notices = session.load_bridge_notification_state()
        assert notices["notifications"] == []  # Reading membership does not decide in-game presentation.

        # Native progress is monotonic; repeated snapshots or a restarted
        # client cannot produce another check or repeat the foreign popup.
        progress(4)
        poll(session)
        restarted = KH3BridgeSession(tmp_path)
        poll(restarted)
        assert restarted.load_game_state()["seq"] == checked["seq"]
        assert restarted.load_game_state()["checked_locations"] == [key]
        assert restarted.load_bridge_notification_state() == notices
    finally:
        unregister(transport)
