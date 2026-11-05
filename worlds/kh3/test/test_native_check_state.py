"""The native completed-check contract is the client's only gameplay authority."""
import json

import pytest

from ..bridge_support.files import atomic_write_json
from ..bridge_support.native_check_state import read
from ..BridgeSession import KH3BridgeSession
from ..ExternalBridge import SaveFilesBackend
from ..SlotData import _catalog
from ..transport.pipe_transport import SET, register, unregister
from .test_native_reconciliation import native_session as receipt_session


def native_session(root, **kwargs):
    session, native = receipt_session(root, **kwargs)
    metadata = dict(session.slot_data)
    row = next(row for row in _catalog()[0].values() if row["bridge_key"] == "Event:EVENT_KEYITEM_002")
    metadata["bridge"] = dict(metadata["bridge"], locations=[dict(row)])
    atomic_write_json(root / "slot_data.json", metadata)
    return KH3BridgeSession(root), native


def snapshot(session, **overrides):
    key = session.slot_data["bridge"]["locations"][0]["bridge_key"]
    result = dict(schema="kh3ap.check_state.v1", owner="dll", process_id=777,
                  seed_name="test-seed", slot=1, ready=True, reason="", delivery_epoch=4,
                  checked_locations=[key], goal_complete=True)
    result.update(overrides)
    return result


def test_checks_and_goal_are_persisted_from_native_snapshot(tmp_path):
    session, _ = native_session(tmp_path)
    payload = snapshot(session)
    transport = register(tmp_path)
    try:
        transport.accept([(SET, "runtime_check_state.json", 2, json.dumps(payload).encode())])
        SaveFilesBackend._sync_native_checks(session)
        state = session.load_game_state()
        assert state["checked_locations"] == payload["checked_locations"]
        assert state["goal_complete"] is True
        SaveFilesBackend._sync_native_checks(session)
        assert session.load_game_state() == state
        assert not transport.pending.get("runtime_item_grant.json")
        assert not transport.pending.get("runtime_ap_message.json")
    finally:
        unregister(transport)


@pytest.mark.parametrize("field,value", [("owner", "client"), ("process_id", 778),
    ("process_id", True), ("seed_name", "other"), ("slot", 2), ("slot", True)])
def test_check_snapshot_rejects_mismatched_identity(tmp_path, field, value):
    session, _ = native_session(tmp_path)
    atomic_write_json(tmp_path / "runtime_check_state.json", snapshot(session, **{field: value}))
    assert read(session) is None
    SaveFilesBackend._sync_native_checks(session)
    assert not session.load_game_state()["goal_complete"]


@pytest.mark.parametrize("override", [{"ready": False, "reason": "journal failure"},
    {"checked_locations": ["Event:NOT_IN_THIS_SEED"]}, {"goal_complete": "true"}])
def test_unavailable_or_invalid_native_state_fails_visibly(tmp_path, override):
    session, _ = native_session(tmp_path)
    atomic_write_json(tmp_path / "runtime_check_state.json", snapshot(session, **override))
    with pytest.raises(ValueError):
        SaveFilesBackend._sync_native_checks(session)
    assert not session.load_game_state()["goal_complete"]


def test_raw_loot_and_ending_context_cannot_report_checks_or_goal(tmp_path):
    session, _ = native_session(tmp_path)
    transport = register(tmp_path)
    try:
        payload = dict(type="event", bridge_key=session.slot_data["bridge"]["locations"][0]["bridge_key"])
        transport.accept([(SET, "runtime_loot_event.json", 3, json.dumps(payload).encode()),
            (SET, "runtime_context.json", 4, b'{"world":"/Game/Levels/ss/ss_50/ss_50"}')])
        SaveFilesBackend(tmp_path)._apply_runtime_loot_event_if_present(session)
        SaveFilesBackend._sync_native_checks(session)
        assert session.load_game_state()["checked_locations"] == []
        assert not session.load_game_state()["goal_complete"]
        assert transport.read("runtime_loot_event.json") is None
    finally:
        unregister(transport)


def test_receipt_bookkeeping_projects_native_membership_without_client_decisions(tmp_path):
    session, native = native_session(tmp_path, extra=[2])
    assert session.load_item_delivery_state()["applied_item_indices"] == [0, 2]
    assert session.native_reconciliation_state() == native


def test_catalog_has_only_facts_without_client_delivery_decisions(tmp_path):
    session, _ = native_session(tmp_path)
    session.sync_native_receipt_catalog()
    catalog = session.store.read_optional_json("runtime_receipt_catalog.json")
    assert catalog["schema"] == "kh3ap.receipt_catalog.v4"
    assert "server_checked_locations" in catalog
    assert not {"forced_indices", "checked_keys", "bootstrap_slots"} & catalog.keys()
    for row in catalog["items"]:
        assert not {"replay", "passthrough"} & row.keys()


@pytest.mark.parametrize("kind", ["kh02_chest", "kh02_boss", "kh02_objective"])
def test_integrated_kh02_checks_follow_native_state_without_companion_files(tmp_path, kind):
    session, _ = native_session(tmp_path)
    row = next(row for row in _catalog()[0].values() if row["type"] == kind)
    metadata = dict(session.slot_data)
    metadata["bridge"] = dict(metadata["bridge"], locations=[dict(row)])
    atomic_write_json(tmp_path / "slot_data.json", metadata)
    session = KH3BridgeSession(tmp_path)
    payload = snapshot(session, goal_complete=False)
    atomic_write_json(tmp_path / "runtime_check_state.json", payload)
    SaveFilesBackend._sync_native_checks(session)
    state = session.load_game_state()
    assert state["checked_locations"] == [row["bridge_key"]]
    assert state["goal_complete"] is False
    SaveFilesBackend._sync_native_checks(session)
    assert session.load_game_state() == state
