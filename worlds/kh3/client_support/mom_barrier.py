"""Connect an explicitly armed KH3 prototype barrier to this seed's MoM checks."""
from __future__ import annotations

import time
from pathlib import Path
from uuid import UUID

from ..bridge_support.files import atomic_write_json, file_signature, load_optional_json_file

SCHEMA = "kh3ap.mom_barrier.v1"
IDENTITY = ("barrier_id", "process_id", "seed_name", "slot", "ap_location_id")


def read_record(path: Path) -> dict:
    try:
        _, size = file_signature(path)
        if size is None or size > 32768:
            return {}
        data = load_optional_json_file(path)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def matching_barrier(service, bridge_dir: Path) -> dict:
    state = read_record(bridge_dir / "runtime_mom_barrier_state.json")
    if (state.get("schema") != SCHEMA or state.get("active") is not True
            or state.get("seed_name") != service.scope["seed_name"]
            or type(state.get("slot")) is not int or state["slot"] != service.scope["slot"]
            or type(state.get("ap_location_id")) is not int or state["ap_location_id"] not in service.challenges
            or type(state.get("process_id")) is not int or state["process_id"] <= 0):
        return {}
    try:
        UUID(state["barrier_id"])
    except (KeyError, TypeError, ValueError, AttributeError):
        return {}
    return state


def consume_interaction(service, bridge_dir: Path) -> int | None:
    state = matching_barrier(service, bridge_dir)
    if not state:
        return None
    event = read_record(bridge_dir / "runtime_mom_barrier_interaction.json")
    expiry = event.get("expires_unix_ms")
    sequence = event.get("sequence")
    now = time.time() * 1000
    if (event.get("schema") != SCHEMA or any(event.get(k) != state[k] for k in IDENTITY)
            or type(expiry) is not int or not now < expiry <= now + 30000
            or type(sequence) is not int or sequence <= 0):
        return None
    key = f"{state['process_id']}:{state['barrier_id']}"
    ledger = service.state.setdefault("barrier_interactions", {})
    if sequence <= ledger.get(key, 0):
        return None
    # A single Examine must never restart MoM repeatedly as the client polls.
    ledger[key] = sequence
    service.save()
    return state["ap_location_id"]


def publish_clear(service, bridge_dir: Path, checked: set[int]) -> None:
    state = matching_barrier(service, bridge_dir)
    if not state or state["ap_location_id"] not in checked | set(service.state["cleared"]):
        return
    payload = {"schema": SCHEMA, **{key: state[key] for key in IDENTITY}, "cleared": True}
    path = bridge_dir / "runtime_mom_barrier_unlock.json"
    if read_record(path) != payload:
        atomic_write_json(path, payload)
