"""The client submits identity-bound work; native state owns live admission."""
from unittest.mock import patch

import pytest

from ..bridge_support.store import BridgeStore
from ..BridgeSession import KH3BridgeSession
from .test_helpers import build_slot_data


@pytest.mark.parametrize("context", [
    None,
    {},
    {"hook": "RuntimePlayablePawnContext", "object": "stale real=false"},
    {"hook": "RuntimePlayablePawnContext", "opened_at": "2000-01-01T00:00:00Z"},
    {"hook": "TresSetPauseMenuType", "pawn_source": "PauseMenu"},
    {"hook": "BP_TresGetItem", "object": "GoA actor real=true"},
    {"hook": "Camera", "object": "transient camera real=true"},
    {"hook": "RuntimePlayablePawnContext", "pawn_source": "RuntimeFilePoll:cached_pawn"},
    {"hook": "RuntimePlayablePawnContext", "pawn_source": "HandleStartingNewPlayer:NewPlayer:Pawn"},
])
@pytest.mark.parametrize("name", ["Potion", "Magic: Blizzard", "Ability: Aerial Recovery", "Ultima Weapon Shard"])
def test_snapshot_never_selects_or_blocks_grant_context(tmp_path, context, name):
    store = BridgeStore(tmp_path)
    slot = build_slot_data()
    slot["kh3_seed"] = {"seed_name": "native-readiness", "slot": 1}
    store.write_json("slot_data.json", slot)
    store.write_json("bridge_runtime_status.json", {"kh3_process_running": True, "kh3_process_pid": 42})
    if context is not None:
        store.write_json("runtime_context.json", context)
    store.write_json("runtime_blocking_ui_state.json", {"blocking_ui_active": True})
    item = {"index": 0, "item_name": name, "kh3_bridge_delivery_required": True}
    session = KH3BridgeSession(tmp_path)
    with patch.object(session, "runtime_item_grant_context_status", side_effect=AssertionError("snapshot admission"), create=True):
        store.write_json("ap_items.json", dict(protocol_name="kh3_ap_file_bridge", protocol_version=1, items=[item]))
        store.write_json("runtime_reconciliation_state.json", dict(schema="kh3ap.native_reconciliation_state.v1",
            owner="dll", process_id=42, seed_name="native-readiness", slot=1, delivery_epoch=4,
            initialized=True, ready=False, active_receipts=dict(version=1, through=-1, extra=[])))
        session.sync_native_receipt_catalog()
    catalog = store.read_optional_json("runtime_receipt_catalog.json")
    assert catalog["schema"] == "kh3ap.receipt_catalog.v4"
    assert (catalog["process_id"], catalog["seed_name"], catalog["slot"]) == (42, "native-readiness", 1)
    assert catalog["items"][0]["index"] == 0
    assert "primary" not in catalog["items"][0]
    assert store.read_optional_json("runtime_item_grant.json") is None
    assert store.read_optional_json("item_delivery_state.json") is None


def test_restart_rebinds_pending_request_without_marking_receipt_applied(tmp_path):
    store = BridgeStore(tmp_path)
    slot = build_slot_data()
    slot["kh3_seed"] = {"seed_name": "restart", "slot": 1}
    store.write_json("slot_data.json", slot)
    item = {"index": 0, "item_name": "Potion", "kh3_bridge_delivery_required": True}
    session = KH3BridgeSession(tmp_path)
    store.write_json("ap_items.json", dict(protocol_name="kh3_ap_file_bridge", protocol_version=1, items=[item]))
    def bind(pid):
        store.write_json("bridge_runtime_status.json", {"kh3_process_running": True, "kh3_process_pid": pid})
        store.write_json("runtime_reconciliation_state.json", dict(schema="kh3ap.native_reconciliation_state.v1",
            owner="dll", process_id=pid, seed_name="restart", slot=1, delivery_epoch=1,
            initialized=True, active_receipts=dict(version=1, through=-1, extra=[])))
    bind(42)
    session.sync_native_receipt_catalog()
    first = store.read_optional_json("runtime_receipt_catalog.json")
    session.sync_native_receipt_catalog()
    assert store.read_optional_json("runtime_receipt_catalog.json") == first
    bind(43)
    session.sync_native_receipt_catalog()
    second = store.read_optional_json("runtime_receipt_catalog.json")
    assert second["process_id"] == 43
    assert first["items"] == second["items"]
    assert store.read_optional_json("runtime_item_grant.json") is None
    assert store.read_optional_json("item_delivery_state.json") is None
