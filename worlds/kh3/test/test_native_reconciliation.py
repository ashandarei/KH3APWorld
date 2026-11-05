import json
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from ..bridge_support.files import atomic_write_json
from ..bridge_support.native_reconciliation import state
from ..ExternalBridge import SaveFilesBackend
from ..ExternalBridge import BridgeRuntimeStatusWriter
from .test_saved_receipts import applied, session_at


@pytest.mark.parametrize("connected,expected", [(True, 312), (False, None)])
def test_proton_status_binds_native_receipts_to_authenticated_windows_pid(tmp_path, monkeypatch, connected, expected):
    session, payload = native_session(tmp_path)
    payload["process_id"] = 312
    session.store.write_json("runtime_reconciliation_state.json", payload)
    transport = SimpleNamespace(transport="local_tcp", lock=threading.RLock(), connected=connected,
                                closed=False, server_process_id=312)
    monkeypatch.setattr("worlds.kh3.ExternalBridge.transport_for", lambda path: transport)
    writer = BridgeRuntimeStatusWriter(session, "test")
    monkeypatch.setattr(writer, "_get_cached_kh3_process_status",
                        lambda: {"running": True, "pid": 63613})
    writer.mark_running()
    runtime = session.load_bridge_runtime_status()
    assert runtime["kh3_process_pid"] == 63613
    assert runtime["kh3_native_process_pid"] == expected
    assert (state(session) is not None) == connected
    payload["process_id"] = 63613
    session.store.write_json("runtime_reconciliation_state.json", payload)
    assert state(session) is None


def native_session(root, *, through=0, extra=None, pending=None):
    session = session_at(root)
    session.slot_data["kh3_seed"] = {"seed_name": "test-seed", "slot": 1}
    atomic_write_json(root / "bridge_runtime_status.json", {
        "protocol_name": "kh3_ap_file_bridge", "protocol_version": 1,
        "kh3_process_pid": 777, "kh3_process_running": True})
    payload = dict(schema="kh3ap.native_reconciliation_state.v1", owner="dll", process_id=777,
        seed_name="test-seed", slot=1, initialized=True, ready=True, delivery_epoch=4,
        active_receipts=dict(version=1, through=through, extra=extra or []),
        pending_replay_indices=pending or [], pending_delivery_indices=pending or [])
    active = sorted(set(range(through + 1)) | set(extra or []))
    last = -1
    while last + 1 in active:
        last += 1
    payload["delivery_summary"] = dict(received_count=4, applied_count=len(active),
        pending_count=len(pending or []), applied_item_indices=active, last_applied_index=last, highest_received_index=3)
    atomic_write_json(root / "runtime_reconciliation_state.json", payload)
    from .test_native_delivery_snapshots import write_native
    write_native(root, "item_delivery_state.json", payload, applied_item_indices=active, last_applied_actions=[])
    write_native(root, "item_delivery_ledger_summary.json", payload, applied_count=len(active), total_count=4, pending_count=len(pending or []))
    write_native(root, "item_delivery.json", payload, native_delivery_summary=payload["delivery_summary"],
        applied_item_indices=active, pending_items=[dict(index=i) for i in pending or []], last_applied_index=last,
        total_received_item_count=4, native_delivery_ready=True)
    write_native(root, "item_consumer_plan.json", payload, pending_actions=[dict(index=i) for i in pending or []])
    return session, payload


def test_catalog_publishes_only_ap_facts_and_leaves_decoding_to_native(tmp_path):
    session, _ = native_session(tmp_path, pending=[0, 1, 2])
    session.store.write_json("ap_items.json", dict(protocol_name="kh3_ap_file_bridge", protocol_version=1, items=[
        dict(index=0, item_id=1263022577, location_id=-1, from_player=2, item_name="Progressive Kingdom Key Level"),
        dict(index=1, item_name="Ultima Weapon Shard", kh3_ultima_shard_required_count=3),
        dict(index=2, item_name="Potion", kh3_secondary_rewards=["Ability: Aerial Recovery", "Ability: Aerial Recovery"]),
        dict(index=3, item_name="Burn Trap"),
    ]))
    session.sync_native_receipt_catalog()
    catalog = session.store.read_optional_json("runtime_receipt_catalog.json")
    rows = catalog["items"]
    assert catalog["schema"] == "kh3ap.receipt_catalog.v4"
    assert rows[0] == dict(index=0, item_id=1263022577, location_id=-1, from_player=2,
                           item_name="Progressive Kingdom Key Level")
    for row in rows:
        assert not (set(row) & {"primary", "rewards", "supported", "bridge_required", "inventory", "level_up", "always_on", "local_seeded"})
        assert all(not key.startswith("kh3_") for key in row)
    assert session.store.read_optional_json("runtime_item_grant.json") is None
    assert session.store.read_optional_json("runtime_trap_queue_request.json") is None


def test_bound_native_ack_is_valid_across_map_changes_and_old_epochs(tmp_path):
    session, native = native_session(tmp_path, pending=[1])
    session.store.write_json("runtime_context.json", {"object": "new pawn real=false"})
    ack = dict(owner="dll", process_id=777, seed_name="test-seed", slot=1, delivery_epoch=3,
        request_id="native-delivery-777-1", action_kind="runtime_item_grant_batch", status="applied", applied_indices=[1])
    assert session.runtime_item_apply_event_context_status(ack)["ready"]
    for field, value in (("process_id", 999), ("seed_name", "another-seed"), ("slot", 2), ("delivery_epoch", -1)):
        assert not session.runtime_item_apply_event_context_status(dict(ack, **{field: value}))["ready"]
    # Delayed client bookkeeping never alters a newer native restore generation.
    assert session.native_reconciliation_state() == native


@pytest.mark.parametrize("override", [dict(process_id=1), dict(seed_name="other"), dict(slot=2), dict(owner="client")])
def test_stale_native_owner_cannot_bind_delivery(tmp_path, override):
    session, payload = native_session(tmp_path)
    atomic_write_json(tmp_path / "runtime_reconciliation_state.json", dict(payload, **override))
    assert state(session) is None
    assert "native_delivery_summary" not in session.build_item_delivery_payload()


def test_invalid_native_sparse_snapshot_fails_visibly(tmp_path):
    session, payload = native_session(tmp_path)
    payload["active_receipts"]["version"] = 2
    atomic_write_json(tmp_path / "runtime_reconciliation_state.json", payload)
    with pytest.raises(ValueError):
        session.build_item_delivery_payload()


def test_native_checkpoint_is_excluded_from_client_reset_and_ownership():
    from ..bridge_support.paths import SHARED_SIDECAR_FILENAMES
    from ..bridge_support.state import OWNED
    for collection in (OWNED, SHARED_SIDECAR_FILENAMES):
        assert "runtime_native_reconciliation.json" not in collection
    assert "runtime_death_retry_replay_window.json" not in OWNED


@pytest.mark.parametrize("ready", [True, False])
def test_backend_poll_and_client_restart_preserve_native_checkpoint_membership(tmp_path, ready):
    session, native = native_session(tmp_path, extra=[2], pending=[1, 3])
    atomic_write_json(tmp_path / "slot_data.json", session.slot_data)
    native["ready"] = ready
    atomic_write_json(tmp_path / "runtime_reconciliation_state.json", native)
    # Late AP acknowledgements belong to the old timeline; they cannot enlarge
    # the DLL's sparse restore membership, even while native recovery is fenced.
    applied(session, [0, 1, 2, 3], at="2026-10-04T01:00:00Z")
    current = dict(event="runtime_current_save_slot", status="known", owner="dll",
        source="native_reconciliation", save_slot=98, slot_file="kh3sv2/data/KHIII_slot98.bin",
        saved_receipts=native["active_receipts"], last_applied_index_at_save=2, ap_index_at_save=3)
    projections = {
        "runtime_current_save_slot.json": current,
        "runtime_display_save_slot.json": current,
        "runtime_save_receipts.json": {"slots": {current["slot_file"]: current}},
        "runtime_death_retry_baseline.json": {"saved_receipts": native["active_receipts"]},
        "runtime_death_retry_replay_window.json": {"pending_indices": [1, 3], "status": "pending"},
        "runtime_native_reconciliation.json": {"schema": "kh3ap.native_reconciliation.v2", "slots": [current]},
    }
    for name, payload in projections.items():
        atomic_write_json(tmp_path / name, payload)
    watched = [*projections, "runtime_reconciliation_state.json"]
    before = {name: (tmp_path / name).read_bytes() for name in watched}
    stop = threading.Event()
    backend = SaveFilesBackend(tmp_path / "synthetic-saves", stop_event=stop)
    backend.reporter = Mock()
    backend._wait_for_next_poll = lambda interval: stop.set()
    assert backend.run(session) == 0
    backend.reporter.error.assert_not_called()
    assert {name: (tmp_path / name).read_bytes() for name in watched} == before
    restarted = type(session)(tmp_path)
    assert restarted.native_reconciliation_state() == native
    result = restarted.build_item_delivery_payload()
    assert result["applied_item_indices"] == [0, 2]
    assert [item["index"] for item in result["pending_items"]] == [1, 3]
    catalog = json.loads((tmp_path / "runtime_receipt_catalog.json").read_text())
    assert "bootstrap_slots" not in catalog
    assert [row["index"] for row in catalog["items"]] == [0, 1, 2, 3]
