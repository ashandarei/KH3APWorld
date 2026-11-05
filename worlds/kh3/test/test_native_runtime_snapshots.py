"""Native snapshot identity and the client's read-only boundary."""

from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from ..bridge_support.runtime_snapshots import NATIVE_RUNTIME_SNAPSHOT_SCHEMAS, bound_runtime_snapshot
from ..Client import KH3Context
from ..transport.pipe_transport import SET, decode, encode


def fixture(schema):
    return ({"schema": schema, "owner": "dll", "process_id": 42, "seed_name": "seed", "slot": 1,
             "error": "", "presentation": {}, "reports": {}, "missing_report_numbers": []},
            {"status": "running", "kh3_process_pid": 42, "kh3_process_running": True},
            {"kh3_seed": {"seed_name": "seed", "slot": 1}})


@pytest.mark.parametrize("filename,schema", NATIVE_RUNTIME_SNAPSHOT_SCHEMAS.items())
def test_snapshot_is_process_and_seed_bound_and_readonly(filename, schema):
    snapshot, runtime, slot_data = fixture(schema)
    original = deepcopy(snapshot)
    assert bound_runtime_snapshot(snapshot, schema, runtime, slot_data) is snapshot
    assert snapshot == original
    ctx = KH3Context.__new__(KH3Context)
    ctx.bridge_dir, ctx.slot_data = Path("synthetic"), slot_data
    read = Mock(return_value=snapshot)
    ctx._get_client_transport_service = lambda: SimpleNamespace(load_sidecar_payload=read)
    ctx._load_bridge_payload = Mock(return_value=runtime)
    assert ctx._load_bridge_sidecar_payload(filename) is snapshot
    read.assert_called_once_with(ctx.bridge_dir, filename, read_only=True)
    ctx._load_bridge_payload.assert_called_once_with("bridge_runtime_status.json")
    packet = [(SET, filename, 1, b'{}')]
    assert decode(encode(0, packet)) == (0, packet)


@pytest.mark.parametrize("key,value", [
    ("schema", "legacy"), ("owner", "python"), ("process_id", 41), ("process_id", True),
    ("process_id", 0), ("slot", True), ("slot", 2), ("seed_name", "other"), ("error", "journal_commit_failed"),
])
def test_stale_foreign_or_failed_snapshot_is_unavailable(key, value):
    schema = NATIVE_RUNTIME_SNAPSHOT_SCHEMAS["runtime_status.json"]
    snapshot, runtime, slot_data = fixture(schema)
    snapshot[key] = value
    assert bound_runtime_snapshot(snapshot, schema, runtime, slot_data) is None


@pytest.mark.parametrize("runtime,slot_data", [
    ({}, {"kh3_seed": {"seed_name": "seed", "slot": 1}}),
    ({"status": "stopped", "kh3_process_pid": 42}, {"kh3_seed": {"seed_name": "seed", "slot": 1}}),
    ({"status": "running", "kh3_process_pid": 41}, {"kh3_seed": {"seed_name": "seed", "slot": 1}}),
    ({"status": "running", "kh3_process_pid": 42}, {}),
    ({"status": "running", "kh3_process_pid": 42, "kh3_process_running": False},
     {"kh3_seed": {"seed_name": "seed", "slot": 1}}),
    ({"status": "running", "kh3_process_pid": 42, "kh3_process_running": True},
     {"kh3_seed": {"seed_name": "seed", "slot": True}}),
])
def test_no_binding_does_not_recover_from_raw_runtime_files(runtime, slot_data):
    schema = NATIVE_RUNTIME_SNAPSHOT_SCHEMAS["runtime_status.json"]
    snapshot, _, _ = fixture(schema)
    assert bound_runtime_snapshot(snapshot, schema, runtime, slot_data) is None
