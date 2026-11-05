"""Exercise existing gameplay policy through the live in-memory IPC boundary."""
import asyncio
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from CommonClient import CommonContext

from ..Bridge import BRIDGE_PROTOCOL_NAME, BRIDGE_PROTOCOL_VERSION
from ..bridge_support.files import load_optional_json_file
from ..BridgeSession import KH3BridgeSession
from ..Client import KH3Context, bridge_watcher
from ..ExternalBridge import SaveFilesBackend
from ..transport.pipe_transport import SET, register, unregister
from .test_helpers import build_slot_data


def test_chest_manifests_are_native_snapshots_and_only_external_clears_republish(tmp_path):
    names = ("runtime_music_chests.json", "runtime_keyblade_chests.json")
    for name in names:
        (tmp_path / name).write_text(json.dumps({"owner": "old client", "chests": ["stale"]}))
    clears = {"schema": "kh3ap.mom_clear_state.v1", "seed_name": "test", "slot": 1,
              "cleared_locations": [123]}
    (tmp_path / "runtime_mom_clear_state.json").write_text(json.dumps(clears))
    transport = register(tmp_path)
    try:
        assert "runtime_mom_clear_state.json" in transport.pending
        for name in names:
            assert name not in transport.pending
            assert load_optional_json_file(tmp_path / name) is None
            with pytest.raises(ValueError, match="owned by the DLL"):
                transport.write(name, {"chests": []})
            with pytest.raises(ValueError, match="owned by the DLL"):
                transport.delete(name)
        assert transport.read("runtime_mom_clear_state.json") == clears
    finally:
        unregister(transport)


@pytest.mark.parametrize("case", ["displayed", "before_reset", "different_request", "stale_disk_result"])
def test_ap_message_cleanup_uses_live_channels_and_preserves_stale_disk_files(tmp_path, case):
    (tmp_path / "slot_data.json").write_text(json.dumps(build_slot_data([])))
    result_path = tmp_path / "runtime_ap_message_result.json"
    stale_result = {"request_id": "message-1", "status": "information"}
    result_path.write_text(json.dumps(stale_result))
    stale_bytes = result_path.read_bytes()
    request = {
        "request_id": "message-1", "message": "AP: Found another player's item",
        "created_at_utc": "2026-10-23T12:00:00Z",
    }
    transport = register(tmp_path)
    try:
        transport.write("runtime_ap_message.json", request)
        if case == "before_reset":
            transport.write("game_state.json", {
                "protocol_name": BRIDGE_PROTOCOL_NAME, "protocol_version": BRIDGE_PROTOCOL_VERSION,
                "seq": 0, "checked_locations": [], "goal_complete": False,
                "reset_at_utc": "2026-10-23T13:00:00Z",
            })
        elif case != "stale_disk_result":
            result = {**stale_result, "request_id": "older-message" if case == "different_request" else "message-1"}
            transport.accept([(SET, result_path.name, 1, json.dumps(result).encode())])

        SaveFilesBackend(tmp_path / "saves")._consume_runtime_ap_message_result_if_present(KH3BridgeSession(tmp_path))

        assert transport.read("runtime_ap_message.json") == (
            request if case in {"different_request", "stale_disk_result"} else None
        )
        assert transport.read(result_path.name) is None
        assert result_path.read_bytes() == stale_bytes
        assert not (tmp_path / "runtime_ap_message.json").exists()
    finally:
        unregister(transport)


def test_native_checkpoint_replaces_stale_disk_projection_after_client_restart():
    with TemporaryDirectory() as directory:
        root = Path(directory)
        path = root / "runtime_current_save_slot.json"
        old = {"source": "new_game_staging", "replay_cutoff_index": -1,
               "observed_at_utc": "2026-09-22T02:52:03Z"}
        checkpoint = {"source": "save_file_open_polyhook_autosave", "replay_cutoff_index": 28,
                      "observed_at_utc": "2026-09-22T03:31:27Z"}
        path.write_text(json.dumps(old))
        for revision in (1, 500):
            transport = register(root)
            try:
                # The DLL supplies the checkpoint on each connection. A client
                # restart must neither publish the stale disk projection nor
                # require Python timestamp arbitration to replace it.
                assert path.name not in transport.pending
                transport.accept([(SET, path.name, revision, json.dumps(checkpoint).encode())])
                assert load_optional_json_file(path) == checkpoint
                assert json.loads(path.read_text()) == old
            finally:
                unregister(transport)


def test_client_watcher_wakes_from_pipe_without_waiting_for_timer():
    async def run():
        context = SimpleNamespace(exit_event=asyncio.Event(), bridge_dir=None, bridge_poll_interval=30.0)
        task = asyncio.create_task(bridge_watcher(context))
        await asyncio.sleep(0)
        context.exit_event.set()
        context.pipe_activity_notify()
        await asyncio.wait_for(task, timeout=1)
    asyncio.run(run())


def test_received_item_view_is_immutable_and_tracks_pipe_publication():
    with TemporaryDirectory() as directory:
        root = Path(directory)
        (root / "slot_data.json").write_text(json.dumps(build_slot_data([])))
        transport = register(root)
        try:
            session = KH3BridgeSession(root)
            session.initialize()
            def publish(index):
                transport.write("ap_items.json", {"protocol_name": BRIDGE_PROTOCOL_NAME,
                    "protocol_version": BRIDGE_PROTOCOL_VERSION, "items": [{"index": index}]})
            publish(1)
            first = session._load_ap_items_view()
            assert session._load_ap_items_view() is first
            try:
                first["items"][0]["index"] = 99
                assert False, "cached receipt must reject mutation"
            except TypeError:
                pass
            editable = session.load_ap_items_payload()
            editable["items"][0]["index"] = 99
            assert first["items"][0]["index"] == 1
            publish(2)
            assert session._load_ap_items_view()["items"][0]["index"] == 2
            # Publication during a read must never cache the older version under
            # the new source revision.
            publish(3)
            reader = session.store.read_optional_json_view
            def racing_read(name):
                value = reader(name)
                publish(4)
                return value
            with patch.object(session.store, "read_optional_json_view", side_effect=racing_read):
                assert session._load_ap_items_view()["items"][0]["index"] == 3
            assert session._load_ap_items_view()["items"][0]["index"] == 4
        finally:
            unregister(transport)


def test_disconnected_status_keeps_identity_only_for_the_current_seed():
    for cached_seed, expected_name in (("fresh", "PipeBattery"), ("old", None)):
        context = SimpleNamespace(
            checked_locations=set(), locations_checked=set(), total_locations=1,
            location_key_to_id={}, items_received=[], bridge_dir=Path("bridge"),
            seed_name=None, offline_seed_name="fresh", slot=None, team=None,
            player_names={}, auth=None, server_address=None, finished_game=False,
            last_processed_game_state_seq=0, slot_data={},
            _is_ap_connected=lambda: False,
            _get_item_sync_service=lambda: SimpleNamespace(received_item_count=lambda *a, **kw: 2),
            _get_seed_policy_service=lambda: SimpleNamespace(
                installed_pak_decision=lambda: SimpleNamespace(status="OK fresh", compatible=True)),
            _load_bridge_payload=lambda name: dict(seed_name=cached_seed, player_name="PipeBattery", slot=1, team=0),
            build_save_seed_status=lambda: "OK",
        )
        payload = KH3Context.build_status_payload(context, {"items": []})
        assert payload["connected"] is False
        assert payload["seed_name"] == "fresh"
        assert payload["player_name"] == expected_name
        assert payload["slot"] == (1 if expected_name else None)


def test_connection_close_publishes_offline_status_without_another_event():
    async def run():
        context = KH3Context.__new__(KH3Context)
        context.bridge_dir = Path("bridge")
        published = asyncio.get_running_loop().create_future()
        published.set_result(None)
        context.write_bridge_files = Mock(return_value=published)
        with patch.object(CommonContext, "connection_closed", new_callable=AsyncMock) as close:
            await context.connection_closed()
            close.assert_awaited_once()
        context.write_bridge_files.assert_called_once_with()
    asyncio.run(run())


def test_deathlink_reads_native_recovery_after_client_consumes_pipe_ack():
    with TemporaryDirectory() as directory:
        root = Path(directory)
        (root / "slot_data.json").write_text(json.dumps({**build_slot_data([]),
            "kh3_seed": {"seed_name": "test-seed", "slot": 1}}))
        (root / "bridge_runtime_status.json").write_text(json.dumps({"kh3_process_pid": 123}))
        transport = register(root)
        try:
            session = KH3BridgeSession(root)
            transport.accept([(SET, "runtime_reconciliation_state.json", 1, json.dumps(dict(
                schema="kh3ap.native_reconciliation_state.v1", owner="dll", seed_name="test-seed", slot=1,
                process_id=123, delivery_epoch=4, active_receipts=dict(version=1, through=-1, extra=[]))).encode())])
            payload = {"schema": "kh3ap.receipt_ack.v1", "receipt_bookkeeping_owner": "dll", "owner": "dll",
                       "process_id": 123, "seed_name": "test-seed", "slot": 1, "delivery_epoch": 4,
                       "event_key": "deathlink-key", "indices": [], "newly_applied_indices": [], "pending_item_count": 0,
                       "request_id": "deathlink-test", "status": "applied",
                       "action_kind": "runtime_item_grant_batch", "applied_at": "2026-09-19T12:00:00Z",
                       "applied_grants": [{"index": -9001, "status": "applied",
                                           "kh3_grant_method": "bp_tres_start_game_over"}]}
            transport.accept([(SET, "runtime_receipt_ack_event.json", 1, json.dumps(payload).encode())])
            SaveFilesBackend(root / "saves", 1.0)._apply_runtime_item_apply_event_if_present(session)
            assert transport.read("runtime_receipt_ack_event.json") is None
            native = {"schema": "kh3ap.deathlink_state.v1", "owner": "dll", "seed_name": "test-seed",
                      "slot": 1, "process_id": 123, "ready": True, "active": True, "awaiting_apply": False,
                      "recovery_pending": True, "request_id": "deathlink-test", "status": "applied"}
            transport.accept([(SET, "runtime_deathlink_state.json", 2, json.dumps(native).encode())])
            state = session.deathlink_game_over_transition_status()
            assert state["active"] and state["recovery_pending"] and not state["awaiting_apply"]
            assert not (root / "runtime_deathlink_state.json").exists()
        finally:
            unregister(transport)
