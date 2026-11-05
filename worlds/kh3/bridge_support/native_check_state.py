"""Consume the DLL's final check and goal decisions without inferring progress."""
from .files import load_optional_json_file


def read(session):
    payload = load_optional_json_file(session.bridge_dir / "runtime_check_state.json")
    runtime = session.load_bridge_runtime_status() or {}
    seed = session.slot_data.get("kh3_seed") or {}
    if (not isinstance(payload, dict) or payload.get("schema") != "kh3ap.check_state.v1"
            or payload.get("owner") != "dll" or type(payload.get("process_id")) is not int
            or payload["process_id"] <= 0 or payload["process_id"] != runtime.get("kh3_native_process_pid", runtime.get("kh3_process_pid"))
            or payload.get("seed_name") != seed.get("seed_name")
            or type(payload.get("slot")) is not int or payload["slot"] != seed.get("slot")):
        return None
    keys = {row["bridge_key"] for row in session.slot_data.get("bridge", {}).get("locations", [])}
    checked = payload.get("checked_locations")
    if (payload.get("ready") is not True or not isinstance(checked, list)
            or any(not isinstance(key, str) or key not in keys for key in checked)
            or len(set(checked)) != len(checked) or type(payload.get("goal_complete")) is not bool):
        raise ValueError(f"Native KH3 check state unavailable: {payload.get('reason') or 'invalid snapshot'}")
    return payload
