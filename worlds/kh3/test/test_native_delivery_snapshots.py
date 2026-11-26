import json
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from ..bridge_support.files import atomic_write_json, load_optional_json_view
from ..bridge_support.native_delivery import SCHEMAS, read, require
from ..ExternalBridge import SaveFilesBackend
from ..transport.pipe_transport import SET, register, unregister
from .test_native_reconciliation import native_session


def write_native(root, name, native, **fields):
    payload = {key: native[key] for key in ('owner', 'process_id', 'seed_name', 'slot', 'delivery_epoch')}
    payload.update(schema=SCHEMAS[name], protocol_name='kh3_ap_file_bridge', protocol_version=1, **fields)
    (root / name).write_text(json.dumps(payload), encoding='utf-8')
    return payload


@pytest.mark.parametrize('name', SCHEMAS)
def test_reads_only_bound_native_snapshots_without_python_writes(tmp_path, name):
    session, native = native_session(tmp_path)
    payload = write_native(tmp_path, name, native, sentinel='native')
    with patch('worlds.kh3.bridge_support.files.atomic_write_json', side_effect=AssertionError('Python writer')):
        assert require(session, name) == payload
    (tmp_path / name).write_text(json.dumps(dict(payload, process_id=888)), encoding='utf-8')
    assert read(session, name) is None
    (tmp_path / name).write_text(json.dumps(dict(payload, schema='legacy')), encoding='utf-8')
    with pytest.raises(ValueError, match='Invalid DLL-owned'):
        require(session, name)




def test_live_native_projection_is_read_only_and_immutable(tmp_path):
    pipe = register(tmp_path)
    try:
        name = 'item_delivery.json'
        payload = b'{"schema":"kh3ap.delivery_snapshot.v1","pending_items":[]}'
        pipe.accept([(SET, name, 1, payload)])
        view = load_optional_json_view(tmp_path / name)
        assert load_optional_json_view(tmp_path / name) is view
        with pytest.raises(TypeError):
            view['pending_items'].append({})
        with pytest.raises(ValueError, match='owned by the DLL'):
            atomic_write_json(tmp_path / name, {})
        assert not (tmp_path / name).exists()
    finally:
        unregister(pipe)


def test_completed_ack_is_consumed_even_when_native_last_event_already_matches(tmp_path):
    session, native = native_session(tmp_path)
    ack = {key: native[key] for key in ('owner', 'process_id', 'seed_name', 'slot', 'delivery_epoch')}
    ack.update(schema='kh3ap.receipt_ack.v1', receipt_bookkeeping_owner='dll', event_key='native-key',
        indices=[1], newly_applied_indices=[1], applied_actions=[], client_notifications=[],
        pending_item_count=0, request_id='native-delivery', status='applied', action_kind='runtime_item_grant_batch')
    write_native(tmp_path, 'runtime_item_apply_state.json', native, last_processed_event_key='native-key', last_processed_event=ack)
    path = tmp_path / 'runtime_receipt_ack_event.json'
    path.write_text(json.dumps(ack), encoding='utf-8')
    backend = SaveFilesBackend(tmp_path / 'synthetic')
    backend.reporter = Mock()
    before = {name: (tmp_path / name).read_bytes() for name in ('runtime_item_apply_state.json', 'runtime_reconciliation_state.json')}
    result = backend._apply_runtime_item_apply_event_if_present(session)
    assert result['status'] == 'applied' and result['indices'] == [1]
    assert backend._apply_runtime_item_apply_event_if_present(session)['status'] == 'duplicate_event'
    assert all((tmp_path / name).read_bytes() == value for name, value in before.items())
    with pytest.raises(ValueError, match='completed native receipt bookkeeping'):
        session.apply_runtime_item_apply_event(dict(ack, schema='raw'))


@pytest.mark.parametrize("late_input", ["pid", "reconciliation"])
def test_backend_retries_queued_proton_ack_when_binding_is_ready(tmp_path, monkeypatch, late_input):
    session, native = native_session(tmp_path)
    native["process_id"] = 360
    status = {"kh3_process_pid": 35186, "kh3_native_process_pid": 360}
    ack = {key: native[key] for key in ("owner", "process_id", "seed_name", "slot", "delivery_epoch")}
    ack.update(schema="kh3ap.receipt_ack.v1", receipt_bookkeeping_owner="dll", event_key="proton-ack",
               indices=[0], newly_applied_indices=[0], pending_item_count=0, status="applied")
    channel = "runtime_receipt_ack_event.json"
    pipe = register(tmp_path)
    try:
        def publish_snapshot():
            pipe.accept([(SET, "runtime_reconciliation_state.json", 1, json.dumps(native).encode())])

        if late_input == "pid":
            publish_snapshot()
        initial = {**status, "kh3_native_process_pid": None} if late_input == "pid" else status
        session.store.write_json("bridge_runtime_status.json", initial)
        pipe.accept([(SET, channel, 1, json.dumps(ack).encode())])
        backend = SaveFilesBackend(tmp_path, reporter=Mock())
        cycles = iter(range(2))
        monkeypatch.setattr(backend, "_should_stop", lambda: next(cycles, None) is None)

        def wait(_):
            if pipe.read(channel) is None:
                return
            if late_input == "pid":
                session.store.write_json("bridge_runtime_status.json", status)
            else:
                publish_snapshot()

        monkeypatch.setattr(backend, "_wait_for_next_poll", wait)
        assert backend.run(session) == 0
        backend.reporter.error.assert_not_called()
        assert session._last_receipt_ack_key == "proton-ack"
        assert pipe.read(channel) is None
    finally:
        unregister(pipe)


def test_native_ack_queue_retains_each_group(tmp_path):
    pipe = register(tmp_path)
    try:
        name = 'runtime_receipt_ack_event.json'
        pipe.accept([(SET, name, 1, b'{"indices":[0]}'), (SET, name, 2, b'{"indices":[1]}')])
        assert pipe.read(name)['indices'] == [0]
        pipe.acknowledge(name, pipe.read(name))
        assert pipe.read(name)['indices'] == [1]
        pipe.acknowledge(name, pipe.read(name))
        assert pipe.read(name) is None
    finally:
        unregister(pipe)


def test_debug_summary_uses_native_counts_without_loading_acknowledgement_history():
    from ..client_support.presentation import ClientStatusPresenter
    from ..client_support.status_snapshot_service import ClientStatusSnapshotService

    counts = dict(received_count=4, pending_count=2, applied_count=2, highest_received_index=3)

    def read_protocol(name):
        if name == "item_delivery_ledger.json":
            raise AssertionError("Debug loaded historical receipts")
        return (
            {"native_delivery_summary": counts, "native_delivery_ready": True} if name == "item_delivery.json" else {}
        )

    service = ClientStatusSnapshotService(
        presenter=None,
        protocol_payload_reader=read_protocol,
        sidecar_payload_reader=lambda _: {},
        latest_save_load_reader=lambda: {},
        current_save_slot_file="runtime_current_save_slot.json",
        display_save_slot_file="runtime_display_save_slot.json",
    )
    payloads = service.load_inputs().as_presenter_payloads()
    source = SimpleNamespace(
        checked_locations=set(),
        locations_checked=set(),
        total_locations=0,
        location_key_to_id={},
        player_names={},
        slot=None,
        auth=None,
        seed_name=None,
        offline_seed_name=None,
        bridge_dir=None,
        server=None,
        items_received=[],
    )
    snapshot = ClientStatusPresenter(source).build_status_snapshot_from_payloads(payloads)
    assert snapshot.received_items == "4" and snapshot.pending_items == "2" and snapshot.applied_items == "2"
    assert snapshot.highest_ap_index == "3"
