"""Accept native presentation snapshots only from the current process and seed."""

from collections.abc import Mapping

NATIVE_RUNTIME_SNAPSHOT_SCHEMAS = {
    "runtime_status.json": "kh3ap.runtime_status.v1",
    "runtime_secret_report_hints.json": "kh3ap.secret_report_hints.v1",
    "runtime_save_receipts.json": "kh3ap.save_receipts.v1",
    "runtime_current_save_slot.json": "kh3ap.save_slot.v1",
    "runtime_display_save_slot.json": "kh3ap.save_slot.v1",
}


def bound_runtime_snapshot(payload, schema, runtime, slot_data):
    if not all(isinstance(value, Mapping) for value in (payload, runtime, slot_data)):
        return None
    seed = slot_data.get("kh3_seed")
    if (not isinstance(seed, Mapping) or not seed.get("seed_name")
            or payload.get("schema") != schema or payload.get("owner") != "dll"
            or type(seed.get("slot")) is not int
            or runtime.get("status") != "running" or runtime.get("kh3_process_running") is not True
            or type(payload.get("process_id")) is not int or payload["process_id"] <= 0
            or payload["process_id"] != runtime.get("kh3_native_process_pid", runtime.get("kh3_process_pid"))
            or type(payload.get("slot")) is not int or payload["slot"] != seed.get("slot")
            or payload.get("seed_name") != seed["seed_name"] or payload.get("error")):
        return None
    return payload
