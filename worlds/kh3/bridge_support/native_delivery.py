"""Read DLL-owned receipt and delivery snapshots; never rebuild or persist them."""
from __future__ import annotations

from .files import load_optional_json_view
from .native_reconciliation import state

SCHEMAS = {
    "item_delivery.json": "kh3ap.delivery_snapshot.v1",
    "item_consumer_plan.json": "kh3ap.consumer_snapshot.v1",
    "item_delivery_state.json": "kh3ap.receipt_state.v1",
    "item_delivery_ledger_summary.json": "kh3ap.receipt_ledger_summary.v1",
    "runtime_item_apply_state.json": "kh3ap.receipt_apply_state.v1",
    "runtime_item_grant_state.json": "kh3ap.receipt_grant_state.v1",
}


def read(session, name):
    native = state(session)
    payload = load_optional_json_view(session.bridge_dir / name)
    if native is None or payload is None:
        return None
    if not isinstance(payload, dict) or payload.get("schema") != SCHEMAS[name] or payload.get("owner") != "dll":
        raise ValueError(f"Invalid DLL-owned KH3 snapshot: {name}")
    if any(payload.get(key) != native.get(key) for key in
           ("process_id", "seed_name", "slot", "delivery_epoch")):
        return None
    return payload


def require(session, name):
    payload = read(session, name)
    if payload is None:
        raise ValueError(f"DLL-owned KH3 snapshot unavailable: {name}")
    return payload
