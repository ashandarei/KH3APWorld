import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from ..Bridge import BRIDGE_PROTOCOL_NAME, BRIDGE_PROTOCOL_VERSION
from ..bridge_support.deathlink import NETWORK_FILE
from ..bridge_support.state import OWNED
from ..BridgeSession import KH3BridgeSession
from ..client_support.deathlink_service import DeathLinkService
from ..client_support.deathlink_settings import read_settings, write_settings
from ..transport.pipe_transport import SET, register, unregister


@pytest.fixture
def harness(tmp_path):
    metadata = {"protocol_name": BRIDGE_PROTOCOL_NAME, "protocol_version": BRIDGE_PROTOCOL_VERSION,
                "kh3_seed": {"seed_name": "test-seed", "slot": 1},
                "bridge": {"protocol_name": BRIDGE_PROTOCOL_NAME, "protocol_version": BRIDGE_PROTOCOL_VERSION,
                           "locations": []}}
    (tmp_path / "slot_data.json").write_text(json.dumps(metadata))
    (tmp_path / "bridge_runtime_status.json").write_text(json.dumps({"kh3_process_pid": 123}))
    write_settings(tmp_path, True, 3)
    transport = register(tmp_path)
    state = SimpleNamespace(enabled=True, connected=True)
    sent = AsyncMock()
    async def update(enabled):
        state.enabled = enabled
    def make():
        return DeathLinkService(send_death=sent, deathlink_enabled=lambda: state.enabled,
                                has_player=lambda: True, server_connected=lambda: state.connected,
                                read_settings=lambda root: read_settings(root, {}), update_enabled=update)
    fixture = SimpleNamespace(root=tmp_path, transport=transport, state=state, sent=sent, make=make,
                              service=make(), seq=0)
    def publish(**fields):
        fixture.seq += 1
        payload = {"schema": "kh3ap.deathlink_state.v1", "owner": "dll", "process_id": 123,
                   "seed_name": "test-seed", "slot": 1, "ready": True, "accepted_ids": [], "outgoing": [],
                   "active": False, "awaiting_apply": False, "recovery_pending": False, **fields}
        transport.accept([(SET, "runtime_deathlink_state.json", fixture.seq, json.dumps(payload).encode())])
    fixture.publish = publish
    fixture.poll = lambda: asyncio.run(fixture.service.handle_runtime_game_over(tmp_path))
    try:
        yield fixture
    finally:
        unregister(transport)


def test_native_outgoing_is_sent_once_and_ack_survives_client_restart(harness):
    h = harness
    h.publish(outgoing=[{"id": "deathlink-local-1", "cause": "Sora hit Game Over."}])
    h.poll()
    h.poll()
    h.sent.assert_awaited_once_with("Sora hit Game Over.")
    assert h.transport.read("runtime_deathlink_control.json")["sent_ids"] == ["deathlink-local-1"]
    h.service = h.make()
    h.poll()
    assert h.sent.await_count == 1
    assert json.loads((h.root / NETWORK_FILE).read_text())["sent_ids"] == ["deathlink-local-1"]


def test_send_failure_retries_native_intent_without_gameplay_state_changes(harness):
    h = harness
    h.sent.side_effect = [ConnectionError("offline"), None]
    h.publish(outgoing=[{"id": "deathlink-local-1", "cause": "Death"}])
    with pytest.raises(ConnectionError):
        h.poll()
    assert h.service.custody(h.root).network()["sent_ids"] == []
    h.poll()
    assert h.sent.await_count == 2
    assert h.service.custody(h.root).network()["sent_ids"] == ["deathlink-local-1"]


def test_offline_intent_is_sent_after_reconnect_even_with_same_snapshot(harness):
    h = harness
    h.state.connected = False
    h.publish(outgoing=[{"id": "deathlink-local-1", "cause": "Death"}])
    h.poll()
    h.sent.assert_not_awaited()
    h.state.connected = True
    h.poll()
    h.sent.assert_awaited_once()


def test_incoming_network_custody_waits_for_native_durable_acceptance(harness):
    h = harness
    h.service.handle_incoming(h.root, {"source": "Riku", "time": 1234.5, "cause": "Defeated"})
    h.service.handle_incoming(h.root, {"source": "Riku", "time": 1234.5})
    expected = [{"request_id": "deathlink-gameover-1234500", "source": "Riku", "cause": "Defeated"}]
    assert h.service.custody(h.root).network()["incoming"] == expected
    h.service = h.make()
    h.publish()
    h.poll()
    assert h.transport.read("runtime_deathlink_control.json")["incoming"] == expected
    h.publish(accepted_ids=[expected[0]["request_id"]], active=True, awaiting_apply=True)
    h.poll()
    assert h.service.custody(h.root).network()["incoming"] == []
    h.sent.assert_not_awaited()


def test_settings_refresh_changes_network_tag_and_rejects_disabled_incoming(harness):
    h = harness
    write_settings(h.root, False, 5)
    h.service.handle_incoming(h.root, {"time": 1})
    assert not (h.root / NETWORK_FILE).exists()
    h.publish()
    h.poll()
    assert h.state.enabled is False
    control = h.transport.read("runtime_deathlink_control.json")
    assert control["enabled"] is False and control["amnesty"] == 5


@pytest.mark.parametrize("fields", [{"seed_name": "wrong"}, {"slot": 2}, {"process_id": 124},
                                    {"process_id": True}, {"owner": "client"}, {"schema": "old"}])
def test_foreign_native_snapshot_cannot_send_or_change_custody(harness, fields):
    h = harness
    h.publish(outgoing=[{"id": "deathlink-local-1", "cause": "Death"}], **fields)
    h.poll()
    h.sent.assert_not_awaited()
    assert not (h.root / NETWORK_FILE).exists()


def test_native_failure_is_visible_and_legacy_death_logs_are_not_inputs(harness):
    h = harness
    (h.root / "runtime_sora_state.json").write_text('{"status":"known","current_state_id":118}')
    (h.root / "deathlink_amnesty_state.json").write_text('{"forgiven_deaths":3}')
    h.publish(ready=False, reason="checkpoint_commit_failed")
    for _ in range(2):
        with pytest.raises(ValueError, match="checkpoint_commit_failed"):
            h.poll()
    h.publish()
    h.poll()
    h.sent.assert_not_awaited()
    assert not (h.root / "runtime_item_grant.priority.json").exists()


def test_bridge_facade_only_submits_network_intents_and_reads_native_fence(harness):
    h = harness
    bridge = KH3BridgeSession(h.root)
    result = bridge.queue_deathlink_game_over({"time": 1234.5, "source": "Riku"})
    assert result == {"status": "submitted", "request_id": "deathlink-gameover-1234500"}
    assert not (h.root / "runtime_deathlink_state.json").exists()
    h.publish(active=True, awaiting_apply=True, reason="deathlink_game_over_apply_pending")
    transition = bridge.deathlink_game_over_transition_status()
    assert transition["active"] and transition["awaiting_apply"]
    assert not (h.root / "runtime_item_grant.priority.json").exists()


def test_manual_gameover_submits_with_deathlink_disabled_and_offline(harness):
    h = harness
    write_settings(h.root, False, 0)
    h.state.connected = False
    result = KH3BridgeSession(h.root).queue_deathlink_game_over(
        {"time": 1234.5, "source": "Sora", "cause": "Manual KH3 game-over test"}, manual=True)
    assert result["request_id"] == "manual-gameover-1234500"
    h.publish()
    h.poll()
    control = h.transport.read("runtime_deathlink_control.json")
    assert control["enabled"] is False and control["online"] is False
    assert control["incoming"] == [{"request_id": result["request_id"], "source": "Sora",
                                    "cause": "Manual KH3 game-over test", "manual": True}]
    h.sent.assert_not_awaited()


def test_network_death_cannot_set_manual_flag(harness):
    h = harness
    h.service.handle_incoming(h.root, {"time": 1234.5, "manual": True})
    intent = h.service.custody(h.root).network()["incoming"][0]
    assert "manual" not in intent
    assert intent["request_id"] == "deathlink-gameover-1234500"


def test_client_never_owns_resets_or_mirrors_native_deathlink_checkpoint():
    from ..bridge_support.paths import SHARED_SIDECAR_FILENAMES
    for collection in (OWNED, SHARED_SIDECAR_FILENAMES):
        assert "runtime_native_deathlink.json" not in collection
    assert "runtime_deathlink_state.json" not in OWNED
    assert "deathlink_amnesty_state.json" not in OWNED
    assert NETWORK_FILE in OWNED


def test_unchanged_native_snapshot_and_network_intents_do_not_poll_disk(harness):
    h = harness
    h.publish()
    h.poll()
    with patch("pathlib.Path.read_text", side_effect=AssertionError("disk polling")):
        h.poll()
        h.poll()


def test_generic_ack_preserves_native_priority_request(harness):
    h = harness
    native_request = {"request_id": "deathlink-gameover-1", "action_kind": "runtime_item_grant_batch"}
    h.transport.accept([(SET, "runtime_item_grant.priority.json", 1, json.dumps(native_request).encode())])
    session = KH3BridgeSession(h.root)
    assert h.transport.read("runtime_item_grant.priority.json") == native_request
    assert session.retire_acknowledged_runtime_item_grant_requests({**native_request, "status": "applied"}) == []
    assert h.transport.read("runtime_item_grant.priority.json") == native_request
