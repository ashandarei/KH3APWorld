"""AP metadata publication and read-only consumption of DLL checkpoints.

No save/retry selection, timestamp inference, or native journal writes occur in
the client. Receipt accounting and its durable journal belong to the DLL.
"""
from __future__ import annotations

from .files import atomic_write_json, load_optional_json_view
from .saved_receipts import SavedReceipts

STATE = "runtime_reconciliation_state.json"
CATALOG = "runtime_receipt_catalog.json"


def state(session):
    payload = load_optional_json_view(session.bridge_dir / STATE)
    if not isinstance(payload, dict) or payload.get("schema") != "kh3ap.native_reconciliation_state.v1":
        return None
    runtime = session.load_bridge_runtime_status() or {}
    identity = session.slot_data.get("kh3_seed") or {}
    if (payload.get("owner") != "dll" or type(payload.get("process_id")) is not int
            or payload["process_id"] <= 0 or payload["process_id"] != runtime.get("kh3_native_process_pid", runtime.get("kh3_process_pid"))
            or payload.get("seed_name") != identity.get("seed_name")
            or type(payload.get("slot")) is not int or payload.get("slot") != identity.get("slot")
            or type(payload.get("delivery_epoch")) is not int):
        return None
    # Invalid membership must fail visibly; never substitute a scalar frontier.
    SavedReceipts.read({"saved_receipts": payload.get("active_receipts")})
    return payload


def publish_catalog(session):
    native = state(session)
    if native is None:
        return
    items = session._load_ap_items_view()
    status = session.load_ap_status_payload()
    local_slot = native["slot"]
    server_checked = tuple(sorted(status.get("checked_locations", []))) if status.get("connected") else ()
    cache = getattr(session, "_native_receipt_catalog_cache", None)
    if cache is None or cache[0] is not items:
        # Network facts only. The DLL resolves IDs, seed locations, bundles,
        # grant methods and ordered flag rows from its shared catalogs.
        fields = ("index", "item_id", "location_id", "from_player", "flags",
                  "item_name", "location_name", "from_player_name")
        cache = (items, [{key: item[key] for key in fields if key in item}
                         for item in items.get("items", [])])
        session._native_receipt_catalog_cache = cache
    signature = (id(items), native["process_id"], local_slot, server_checked)
    if getattr(session, "_native_receipt_catalog_signature", None) == signature:
        return
    payload = {
        "schema": "kh3ap.receipt_catalog.v4", "process_id": native["process_id"],
        "seed_name": native["seed_name"], "slot": native["slot"], "items": cache[1],
        "server_checked_locations": list(server_checked),
    }
    # Bridge transport suppresses identical publications, including reconnect.
    atomic_write_json(session.bridge_dir / CATALOG, payload)
    session._native_receipt_catalog_signature = signature
