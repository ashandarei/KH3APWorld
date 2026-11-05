"""Protocol, real Windows IPC and file-boundary tests, independent of AP imports."""
import importlib
import json
import os
import re
import struct
import subprocess
import sys
import threading
import time
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
PACKAGE = "kh3_pipe_tests"
package = types.ModuleType(PACKAGE)
package.__path__ = [str(ROOT / "worlds/kh3")]
sys.modules.setdefault(PACKAGE, package)
pipe = importlib.import_module(f"{PACKAGE}.transport.pipe_transport")
files = importlib.import_module(f"{PACKAGE}.bridge_support.files")
checkpoint = importlib.import_module(f"{PACKAGE}.bridge_support.checkpoint")


def wait_until(predicate, timeout=5):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("Timed out waiting for named pipe condition")


@pytest.mark.parametrize("old,new", [(b"", b""), (b"abc", b""), (b"", b"abc"),
    (b"abc", b"abc"), (b"abc", b"axc"), (b"abc", b"abcdef"), (b"abc", b"zabc"),
    (b"abc", b"xyz"), (b"a" * 100000, b"a" * 50000 + b"b" + b"a" * 49999)], ids=[str(i) for i in range(9)])
def test_byte_delta_boundaries(old, new):
    assert checkpoint.apply_delta(old, checkpoint.byte_delta(old, new)) == new


def test_delta_chunk_edges_and_arbitrary_edits():
    import random
    rng = random.Random(149)
    old = rng.randbytes(3 * 65536 + 17)
    for position in (0, 1, 65535, 65536, 65537, len(old) - 1, len(old)):
        for removed in (0, 1, 100):
            for inserted in (b"", b"x", rng.randbytes(100)):
                new = old[:position] + inserted + old[position + removed:]
                assert checkpoint.apply_delta(old, checkpoint.byte_delta(old, new)) == new


def test_checkpoint_crash_recovery_torn_tail_and_compaction(tmp_path):
    path = tmp_path / "ap_items.json"
    first = json.dumps({"items": "x" * 100000, "revision": 1}).encode()
    second = first.replace(b'1}', b'2}')
    third = first.replace(b'1}', b'3}')
    path.write_bytes(first)
    writer = checkpoint.Checkpoint(path)
    writer.write(second)
    assert path.read_bytes() == first
    assert writer.wal.stat().st_size < 200
    # Simulated process crash: a new reader must recover the committed receipt.
    assert checkpoint.read_bytes(path) == second
    with writer.wal.open("ab") as output:
        output.write(struct.pack("<I", 100) + b"partial")
    recovered = checkpoint.Checkpoint(path)
    assert recovered.value == second
    recovered.write(third)
    assert checkpoint.read_bytes(path) == third
    recovered.compact()
    assert checkpoint.read_bytes(path) == third
    assert not writer.wal.exists()


def test_compaction_to_original_value_cannot_reactivate_old_journal(tmp_path, monkeypatch):
    path = tmp_path / "ap_items.json"
    first = b'{"padding":"' + b"a" * 100000 + b'","revision":1}'
    second = first.replace(b':1}', b':2}')
    path.write_bytes(first)
    writer = checkpoint.Checkpoint(path)
    writer.write(second)
    # Crash at the final unlink, after replacing the base with its original state.
    with monkeypatch.context() as scoped:
        original = Path.unlink
        def fail_wal(target, *args, **kwargs):
            if target == writer.wal:
                raise OSError("crash before removing old WAL")
            return original(target, *args, **kwargs)
        scoped.setattr(Path, "unlink", fail_wal)
        with pytest.raises(OSError):
            writer.compact(first)
    assert checkpoint.read_bytes(path) == first


def test_checkpoint_external_reset_and_corrupt_committed_record(tmp_path):
    path = tmp_path / "ap_items.json"
    first = b'{"items":"' + b"a" * 100000 + b'","index":1}'
    second = first.replace(b':1}', b':2}')
    path.write_bytes(first)
    writer = checkpoint.Checkpoint(path)
    writer.write(second)
    wal = writer.wal.read_bytes()
    writer.wal.write_bytes(wal[:-1] + b"X")
    with pytest.raises(checkpoint.JournalError):
        checkpoint.read_bytes(path)
    path.write_bytes(b'{"items":[]}')
    assert checkpoint.read_bytes(path) == b'{"items":[]}'
    # Crash between replacing the checkpoint and removing the old journal.
    path.write_bytes(second)
    writer.wal.write_bytes(wal)
    assert checkpoint.read_bytes(path) == second


@pytest.mark.skipif(os.name != "nt", reason="Windows directory notifications")
def test_shared_state_idle_hits_external_replace_delete_and_missing_create(tmp_path, monkeypatch):
    transport = pipe.register(tmp_path).start()
    path = tmp_path / "bridge_notifications.json"
    missing = tmp_path / "deathlink_network_state.json"
    try:
        files.atomic_write_json(path, {"applied": [1]})
        wait_until(lambda: path.name in transport.state.dirty)
        original = files.file_signature(path)
        assert files.load_optional_json_file(missing) is None
        with monkeypatch.context() as scoped:
            scoped.setattr(Path, "stat", lambda *a, **k: pytest.fail("idle cache stat"))
            scoped.setattr(Path, "open", lambda *a, **k: pytest.fail("idle cache open"))
            for _ in range(100):
                assert files.file_signature(path) == original
                loaded = files.load_json_file(path)
                loaded["applied"].append(99)
                assert files.load_json_file(path) == {"applied": [1]}
                assert files.load_optional_json_file(missing) is None
        replacement = tmp_path / "replace.tmp"
        replacement.write_text('{"applied":[2]}')
        os.replace(replacement, path)
        wait_until(lambda: files.load_json_file(path) == {"applied": [2]})
        assert files.file_signature(path) != original
        path.unlink()
        wait_until(lambda: not files.bridge_exists(path))
        missing.write_text('{"received":true}')
        wait_until(lambda: files.load_optional_json_file(missing) == {"received": True})
        # A non-atomic external writer may temporarily publish incomplete JSON.
        # Signatures still work and optional readers retry after its next event.
        missing.write_text('{partial')
        wait_until(lambda: files.load_optional_json_file(missing) is None)
        assert files.file_signature(missing)[0] is not None
        missing.write_text('{"received":false}')
        wait_until(lambda: files.load_optional_json_file(missing) == {"received": False})
    finally:
        pipe.unregister(transport)


def test_live_checkpoint_is_recovered_offline_and_compacted_at_shutdown(tmp_path):
    transport = pipe.register(tmp_path)
    path = tmp_path / "ap_items.json"
    try:
        files.atomic_write_json(path, {"items": "a" * 100000, "revision": 1})
        files.atomic_write_json(path, {"items": "a" * 100000, "revision": 2})
        assert json.loads(checkpoint.read_bytes(path))["revision"] == 2
        assert json.loads(path.read_bytes())["revision"] == 1
        assert files.load_json_file(path)["revision"] == 2
    finally:
        pipe.unregister(transport)
    assert json.loads(path.read_bytes())["revision"] == 2
    assert not path.with_suffix(".json.wal").exists()


def test_shutdown_compacts_bootstrap_journal_without_any_new_publication(tmp_path):
    path = tmp_path / "ap_items.json"
    first = {"padding": "x" * 100000, "revision": 1}
    path.write_text(json.dumps(first))
    cp = checkpoint.Checkpoint(path)
    second = {**first, "revision": 2}
    cp.write(json.dumps(second).encode())
    transport = pipe.register(tmp_path)
    assert transport.read(path.name) == second
    pipe.unregister(transport)
    assert not cp.wal.exists()
    assert json.loads(checkpoint.read_bytes(path)) == second


def test_checkpoint_failed_fsync_does_not_publish_or_advance_memory(tmp_path, monkeypatch):
    path = tmp_path / "ap_items.json"
    transport = pipe.register(tmp_path)
    first = {"items": "a" * 100000, "revision": 1}
    second = {**first, "revision": 2}
    try:
        files.atomic_write_json(path, first)
        with monkeypatch.context() as scoped:
            def fail(*args):
                raise OSError("injected disk failure")
            scoped.setattr(os, "fsync", fail)
            with pytest.raises(OSError):
                files.atomic_write_json(path, second)
        assert transport.read(path.name) == first
        assert transport.state.read(path.name) == first
        assert json.loads(checkpoint.read_bytes(path)) == first
        files.atomic_write_json(path, second)
        assert json.loads(checkpoint.read_bytes(path)) == second
        assert transport.read(path.name) == second
    finally:
        pipe.unregister(transport)


def test_native_channel_contract():
    from tools.kh3.native_integration import native_project_root
    source = (native_project_root() / "KH3APProbeCpp/kh3ap/bridge/transport/bridge_channels.h").read_text()
    definitions = re.findall(r'X\(\w+, "([^"]+)", (\w+)\)', source)
    native = {name: kind for name, kind in definitions}
    assert len(definitions) == len(native)
    assert native.keys() == pipe.CHANNELS
    assert {name for name, kind in definitions if kind == "ClientCheckpoint"} == pipe.PERSISTENT
    assert {name for name, kind in definitions if kind == "ClientCommand"} == pipe.COMMANDS
    assert {name for name, kind in definitions if kind == "NativeEvent"} == pipe.EVENTS
    assert {name for name, kind in definitions if kind == "NativeJournal"} == pipe.JOURNALS
    assert {name for name, kind in definitions if kind in {"NativeSnapshot", "NativeCheckpoint", "ReadOnlySnapshot"}} == pipe.SNAPSHOTS


def test_framing_and_validation():
    records = [(pipe.SET, "runtime_item_grant.json", 9, '{"name":"Sóra"}'.encode())]
    packet = pipe.encode(123, records)
    assert pipe.decode(packet) == (123, records)
    for malformed in (b"old!", packet[:-1], packet + b"x", b"BAD!" + packet[4:]):
        with pytest.raises(ValueError):
            pipe.decode(malformed)
    bad_name = packet.replace(b"runtime_item_grant.json", b"../../evil_command.txt")
    with pytest.raises(ValueError):
        pipe.decode(bad_name)


def test_no_live_file_fallback_and_persistent_checkpoint(tmp_path):
    stale = tmp_path / "runtime_context.json"
    stale.write_text('{"stale":true}')
    transport = pipe.register(tmp_path)
    try:
        assert files.load_optional_json_file(stale) is None
        assert not files.bridge_exists(stale)
        command = tmp_path / "runtime_item_grant.json"
        files.atomic_write_json(command, {"request_id": "test"})
        assert not command.exists()
        assert files.bridge_exists(command)
        assert files.load_json_file(command) == {"request_id": "test"}
        files.safe_unlink(command)
        assert not files.bridge_exists(command)
        durable = tmp_path / "ap_status.json"
        files.atomic_write_json(durable, {"connected": True})
        assert json.loads(durable.read_text()) == {"connected": True}
        durable.write_text('{"stale":true}')
        assert files.load_json_file(durable) == {"connected": True}
    finally:
        pipe.unregister(transport)


@pytest.mark.parametrize("name", sorted(pipe.PERSISTENT))
def test_recreated_run_republishes_persistent_state_after_transport_shutdown(tmp_path, name):
    path = tmp_path / name
    payload = {"items": [{"index": 0, "item_id": 123}]}
    old = pipe.register(tmp_path)
    files.atomic_write_json(path, payload)
    pipe.unregister(old)
    path.unlink()
    # Deleting a run removes its checkpoints. The old closed transport must
    # not make a same-seed reconnect believe the deleted snapshot still exists.
    assert files.load_optional_json_file(path) is None
    assert not files.bridge_exists(path)
    files.atomic_write_json(path, payload)
    assert json.loads(checkpoint.read_bytes(path)) == payload
    fresh = pipe.register(tmp_path)
    try:
        assert fresh.read(name) == payload
        assert name in fresh.pending
        # Native snapshots still cannot fall back to stale disk mailboxes.
        native = tmp_path / "runtime_context.json"
        native.write_text('{"stale":true}')
        assert files.load_optional_json_file(native) is None
    finally:
        pipe.unregister(fresh)
    assert files.load_optional_json_file(native) is None


def test_event_ack_and_conditional_delete(tmp_path):
    transport = pipe.PipeTransport(tmp_path)
    transport.write("runtime_item_grant.json", {"request_id": "new"})
    transport.accept([(pipe.DELETE, "runtime_item_grant.json", 4, b'{"request_id":"old"}')])
    assert transport.read("runtime_item_grant.json") == {"request_id": "new"}
    event = (pipe.SET, "runtime_loot_event.json", 6, b'{"check":1}')
    transport.accept([event, event])
    assert len(transport.events["runtime_loot_event.json"]) == 1
    assert not transport.acks
    transport.delete("runtime_loot_event.json")
    assert transport.acks == [(pipe.ACK, "runtime_loot_event.json", 6, b"")]


def test_client_only_replay_and_deathlink_state_survives_transport_restart(tmp_path):
    transport = pipe.register(tmp_path)
    try:
        for name in ("deathlink_network_state.json",):
            files.atomic_write_json(tmp_path / name, {"request_id": "offline-death"})
            assert json.loads((tmp_path / name).read_text())["request_id"] == "offline-death"
            assert name not in transport.pending
        path = tmp_path / "runtime_mom_clear_state.json"
        files.atomic_write_json(path, {"old_seed": True})
        assert files.safe_unlink(path)
        assert not path.exists()
    finally:
        pipe.unregister(transport)
    transport = pipe.register(tmp_path)
    try:
        assert files.load_json_file(tmp_path / "deathlink_network_state.json")["request_id"] == "offline-death"
        assert transport.read("runtime_mom_clear_state.json") is None
    finally:
        pipe.unregister(transport)


@pytest.mark.skipif(os.name != "nt" or not os.environ.get("KH3_PIPE_TEST_HOST"), reason="requires compiled native pipe host")
def test_native_roundtrip_burst_reconnect_and_shutdown(tmp_path):
    # Non-ASCII root tests the independently implemented endpoint hash too.
    root = tmp_path / "Sóra 日本"
    root.mkdir()
    (root / "runtime_ingredient_state.json").write_text('{"persisted":true}')
    native = subprocess.Popen([os.environ["KH3_PIPE_TEST_HOST"], str(root)])
    transport = pipe.register(root, threading.Event()).start()
    try:
        wait_until(lambda: transport.connected)
        wait_until(lambda: transport.read("runtime_context.json") == {"context": "native-test"})
        wait_until(lambda: transport.read("runtime_ingredient_state.json") == {"persisted": True})
        files.atomic_write_json(root / "runtime_item_grant.json", {"request_id": "native-roundtrip"})
        wait_until(lambda: transport.read("runtime_receipt_ack_event.json") is not None)
        assert transport.read("runtime_receipt_ack_event.json") == {"request_id": "native-roundtrip"}
        wait_until(lambda: not files.bridge_exists(root / "runtime_item_grant.json"))
        seen = []
        for i in range(20):
            wait_until(lambda: transport.read("runtime_loot_event.json") is not None)
            seen.append(transport.read("runtime_loot_event.json")["check"])
            files.acknowledge_pipe_event(root / "runtime_loot_event.json")
        assert seen == list(range(20))
        assert [p.name for p in root.glob("runtime_*.json")] == ["runtime_ingredient_state.json"]
        wait_until(lambda: len(transport.lines("current_state_write_polyhook_events.jsonl")) == 20)
        assert [json.loads(line)["level"] for line in transport.lines("current_state_write_polyhook_events.jsonl")] == list(range(2, 22))
        # Reconnecting a new client replays the unacknowledged grant event.
        pipe.unregister(transport)
        transport = pipe.register(root).start()
        wait_until(lambda: transport.read("runtime_receipt_ack_event.json") is not None)
        assert transport.read("runtime_receipt_ack_event.json")["request_id"] == "native-roundtrip"
        assert len(transport.lines("current_state_write_polyhook_events.jsonl")) == 20
        files.acknowledge_pipe_event(root / "runtime_receipt_ack_event.json")
        files.atomic_write_json(root / "runtime_ap_message.json", {"stop": True})
        native.wait(timeout=5)
        wait_until(lambda: not transport.connected)
        assert transport.read("runtime_context.json") is None
        diagnostic = transport.diagnostic_snapshot()
        assert diagnostic["status"] == "disconnected"
        assert "runtime_context.json" not in diagnostic["channels"]
        retained = diagnostic["last_disconnected_runtime_snapshots"]
        assert retained["status"] == "historical_not_live"
        assert retained["channels"]["runtime_context.json"]["payload"] == {"context": "native-test"}
    finally:
        pipe.unregister(transport)
        if native.poll() is None:
            native.terminate()
        native.wait(timeout=5)

NATIVE = pytest.mark.skipif(os.name != 'nt' or not os.environ.get('KH3_PIPE_TEST_HOST'),
                            reason='requires compiled native pipe host')


@pytest.fixture
def native_peer(tmp_path):
    import queue
    native = subprocess.Popen([os.environ['KH3_PIPE_TEST_HOST'], str(tmp_path)], stdout=subprocess.PIPE, text=True)
    lines = queue.Queue()
    reader = threading.Thread(target=lambda: [lines.put(line.strip()) for line in native.stdout], daemon=True)
    reader.start()
    try:
        yield native, lines
    finally:
        if native.poll() is None:
            native.terminate()
        native.wait(timeout=5)
        reader.join(2)
        native.stdout.close()


@NATIVE
def test_native_notification_fifo_reconnect_and_ack(tmp_path, native_peer):
    transport = pipe.register(tmp_path).start()
    first = dict(request_id="native-1", message="AP: Found Samus's Missile Tank (p)")
    second = dict(request_id="native-2", message="AP: Already obtained Potion")
    try:
        wait_until(lambda: transport.connected)
        transport.write("runtime_ap_message.json", {"notification_test": 1, "records": [first, second]})
        wait_until(lambda: transport.read("runtime_notification_event.json") == first)
        pipe.unregister(transport)
        transport = pipe.register(tmp_path).start()
        wait_until(lambda: transport.read("runtime_notification_event.json") == first)
        files.acknowledge_pipe_event(tmp_path / "runtime_notification_event.json", first)
        wait_until(lambda: transport.read("runtime_notification_event.json") == second)
        files.acknowledge_pipe_event(tmp_path / "runtime_notification_event.json", second)
        wait_until(lambda: transport.read("runtime_notification_event.json") is None)
        assert not (tmp_path / "runtime_notification_event.json").exists()
    finally:
        pipe.unregister(transport)


class RawPeer:
    def __init__(self, root):
        import _winapi as win
        self.cancel = pipe._WindowsEvent()
        self.transport = pipe.PipeTransport(root)
        self.handle = None
        def connect():
            try:
                self.handle = win.CreateFile(pipe.pipe_name(root), win.GENERIC_READ | win.GENERIC_WRITE,
                    0, 0, win.OPEN_EXISTING, win.FILE_FLAG_OVERLAPPED, 0)
                return True
            except OSError:
                return False
        wait_until(connect)
        win.SetNamedPipeHandleState(self.handle, win.PIPE_READMODE_MESSAGE, None, None)
        self.send_raw(pipe.MAGIC)
        assert self.receive_raw()[:4] == pipe.MAGIC

    def send_raw(self, packet):
        self.transport._io(self.handle, self.cancel, packet, timeout=2000)

    def receive_raw(self):
        return self.transport._io(self.handle, self.cancel, timeout=2000)

    def send(self, name, payload):
        self.send_raw(pipe.encode(0, [(pipe.SET, name, 0, json.dumps(payload, separators=(',', ':')).encode())]))

    def close(self):
        import _winapi as win
        win.CloseHandle(self.handle)
        self.cancel.close()

    def result(self, name):
        while True:
            _, records = pipe.decode(self.receive_raw())
            for _, key, revision, value in records:
                if key in pipe.EVENTS:
                    self.send_raw(pipe.encode(0, [(pipe.ACK, key, revision, b'')]))
                if key == name:
                    return json.loads(value)


@NATIVE
@pytest.mark.parametrize("padding", [0, 9 * 1024 * 1024])
@pytest.mark.parametrize("trap_type", ["BURN", "KING_MICKEY"])
def test_native_trap_journal_acceptance_recovery_dedupe_and_failure(tmp_path, padding, trap_type):
    (tmp_path / 'slot_data.json').write_text(json.dumps({
        'kh3_seed': {'seed_name': 'test-seed', 'slot': 1}, 'padding': 'x' * padding}))
    def launch():
        process = subprocess.Popen([os.environ['KH3_PIPE_TEST_HOST'], str(tmp_path)], stdout=subprocess.DEVNULL)
        return process, RawPeer(tmp_path)
    process, peer = launch()
    serial = 0
    def submit(indices, token=trap_type, **overrides):
        nonlocal serial
        serial += 1
        payload = dict(request_schema='kh3ap.item_delivery.v1', process_id=process.pid, seed_name='test-seed', slot=1,
            request_id=f'traps-{serial}', action_kind='deferred_bad_status_trap', grants=[dict(index=i,
                kh3_runtime_token='KH3AP_TRAP::KING_MICKEY' if token == 'KING_MICKEY' else f'ETresBadStatusType::TRES_BADSTAT_{token}',
                kh3_grant_method='king_mickey_trap' if token == 'KING_MICKEY' else 'direct_player_bad_status') for i in indices])
        payload.update(overrides)
        peer.send('runtime_trap_queue_request.json', payload)
        peer.send('runtime_ap_message.json', {'trap_submit': serial})
        return peer.result('runtime_trap_queue_ack.json')
    def inspect():
        nonlocal serial
        serial += 1
        peer.send('runtime_ap_message.json', {'trap_inspect': serial})
        return peer.result('runtime_ap_message_result.json')
    journal = tmp_path / 'runtime_native_trap_queue.json'
    try:
        assert submit([4, 5])['status'] == 'accepted'
        assert json.loads(journal.read_text())['record_count'] == 2  # committed before ACK
        assert submit([4, 5])['status'] == 'accepted'
        assert inspect()['pending'] == 2
        assert submit([4], token='SNEEZE')['reason'] == 'receipt_identity_conflict'
        assert submit([6], process_id=process.pid + 1)['status'] == 'rejected'
        assert submit([6], seed_name='other-seed')['status'] == 'rejected'
        peer.send('runtime_ap_message.json', {'trap_apply': 4})
        def committed():
            try: return json.loads(journal.read_text())['traps'][0]['applied']
            except OSError: return False  # Windows atomic replacement may briefly deny an external reader.
        wait_until(committed)
        assert inspect()['head'] == 5
        peer.close(); process.terminate(); process.wait(timeout=5)
        process, peer = launch()
        # The pipe can connect before the seed workers finish cold recovery.
        wait_until(lambda: inspect()['head'] == 5)  # completed 4 stays complete; pending 5 recovers
        assert submit([4, 5])['status'] == 'accepted'
        assert inspect()['pending'] == 1
        # An atomic replacement failure must not emit accepted or change receipt state.
        journal.unlink(); journal.mkdir()
        assert submit([6])['status'] == 'rejected'
        assert inspect()['accepted'] == 2
    finally:
        peer.close()
        if process.poll() is None: process.terminate()
        process.wait(timeout=5)


@NATIVE
@pytest.mark.parametrize('saved', [dict(schema='wrong'), dict(schema='kh3ap.native_trap_queue.v1',
    seed_name='other-seed', slot=1, record_count=0, traps=[], complete=True),
    dict(schema='kh3ap.native_trap_queue.v1', seed_name='test-seed', slot=1, record_count=1, traps=[], complete=True)])
def test_native_trap_recovery_fails_closed(tmp_path, saved):
    (tmp_path / 'slot_data.json').write_text(json.dumps(dict(kh3_seed=dict(seed_name='test-seed', slot=1))))
    (tmp_path / 'runtime_native_trap_queue.json').write_text(json.dumps(saved))
    process = subprocess.Popen([os.environ['KH3_PIPE_TEST_HOST'], str(tmp_path)], stdout=subprocess.DEVNULL)
    peer = RawPeer(tmp_path)
    try:
        state = {}
        serial = 0
        def recovered():
            nonlocal state, serial
            # Repeated identical commands are coalesced by the native harness.
            # A fresh serial lets us observe recovery that finishes after hello.
            serial += 1
            peer.send('runtime_ap_message.json', {'trap_inspect': serial})
            state = peer.result('runtime_ap_message_result.json')
            # The listener can accept a peer before the recovery worker has
            # published its failure; the initial unavailable view has no error.
            return bool(state.get('error'))
        wait_until(recovered)
        assert state['ready'] is False and state['head'] == -1 and state['error']
    finally:
        peer.close(); process.terminate(); process.wait(timeout=5)


@NATIVE
def test_native_trap_pending_capacity_preserves_committed_receipts(tmp_path):
    (tmp_path / 'slot_data.json').write_text(json.dumps(dict(kh3_seed=dict(seed_name='test-seed', slot=1))))
    process = subprocess.Popen([os.environ['KH3_PIPE_TEST_HOST'], str(tmp_path)], stdout=subprocess.DEVNULL)
    peer = RawPeer(tmp_path)
    try:
        def submit(indices, serial):
            peer.send('runtime_trap_queue_request.json', dict(request_schema='kh3ap.item_delivery.v1',
                process_id=process.pid, seed_name='test-seed', slot=1, request_id=f'capacity-{serial}',
                action_kind='deferred_bad_status_trap', grants=[dict(index=index,
                    kh3_grant_method='direct_player_bad_status',
                    kh3_runtime_token='ETresBadStatusType::TRES_BADSTAT_BURN') for index in indices]))
            peer.send('runtime_ap_message.json', {'trap_submit': serial})
            return peer.result('runtime_trap_queue_ack.json')
        assert submit(range(4096), 1)['status'] == 'accepted'
        assert submit([4096], 2)['reason'] == 'queue_capacity'
        assert submit([-1], 3)['reason'] == 'unsupported_trap'
        saved = json.loads((tmp_path / 'runtime_native_trap_queue.json').read_text())
        assert saved['record_count'] == 4096
        assert all(not trap['applied'] for trap in saved['traps'])
    finally:
        peer.close(); process.terminate(); process.wait(timeout=5)


@NATIVE
def test_worker_prepares_whole_grant_batches_and_invalidates_deleted_or_malformed_payloads(tmp_path, native_peer):
    peer = RawPeer(tmp_path)
    try:
        def inspect(request):
            peer.send('runtime_ap_message.json', {'inspect_prepared_grant': request})
            while True:
                _, records = pipe.decode(peer.receive_raw())
                for _, name, _, value in records:
                    if name == 'runtime_ap_message_result.json':
                        return json.loads(value)

        request = dict(request_schema='kh3ap.item_delivery.v1', process_id=42, seed_name='test', slot=1,
            request_id='batch', action_kind='runtime_item_grant_batch', grants=[
                dict(index=i, kh3_runtime_token='ITEM_POTION', kh3_grant_method='bp_tres_get_item') for i in range(400)])
        peer.send('runtime_item_grant.priority.json', request)
        valid = inspect(1)
        assert valid['count'] == 400 and valid['process_id'] == 42 and valid['revision'] > 0
        peer.send('runtime_item_grant.priority.json', {**request, 'grants': [request['grants'][0], {}]})
        invalid = inspect(2)
        assert invalid['count'] == 0 and invalid['revision'] > valid['revision']
        peer.send('runtime_item_grant.priority.json', request)
        assert inspect(3)['count'] == 400
        peer.send_raw(pipe.encode(0, [(pipe.DELETE, 'runtime_item_grant.priority.json', 0, b'')]))
        assert inspect(4)['count'] == 0
    finally:
        peer.close()


@NATIVE
def test_grant_wake_ignores_unrelated_and_duplicate_inputs(tmp_path, native_peer):
    peer = RawPeer(tmp_path)
    try:
        def inspect(request):
            peer.send('runtime_ap_message.json', {'inspect_grant_wake': request})
            while True:
                _, records = pipe.decode(peer.receive_raw())
                for _, name, _, value in records:
                    if name == 'runtime_ap_message_result.json':
                        result = json.loads(value)
                        assert result['repeated'] is False
                        return result['requested']

        assert inspect(1) is False
        peer.send('ap_items.json', {'items': ['Potion']})
        assert inspect(2) is False
        peer.send('runtime_item_grant.priority.json', {'request_id': 'priority'})
        assert inspect(3) is True
        peer.send('runtime_item_grant.priority.json', {'request_id': 'priority'})
        assert inspect(4) is False
        peer.send_raw(pipe.encode(0, [(pipe.DELETE, 'runtime_item_grant.priority.json', 0, b'')]))
        assert inspect(5) is True
        peer.send('runtime_item_grant.json', {'request_id': 'normal'})
        assert inspect(6) is True
        assert inspect(7) is False  # Native ack/publication does not wake itself.
    finally:
        peer.close()


@NATIVE
def test_input_events_are_independent_of_consumers_and_duplicate_snapshots(tmp_path, native_peer):
    peer = RawPeer(tmp_path)
    try:
        def generation(request):
            peer.send('runtime_ap_message.json', {'inspect_generation': request})
            while True:
                _, records = pipe.decode(peer.receive_raw())
                for _, name, _, value in records:
                    if name == 'runtime_ap_message_result.json':
                        result = json.loads(value)
                        assert result['after_take'] == result['generation']
                        return result['generation']

        initial = generation(1)
        peer.send('ap_items.json', {'items': ['Nano Gear']})
        received = generation(2)
        assert received == initial + 2  # receipt and inspection command
        peer.send('ap_items.json', {'items': ['Nano Gear']})
        duplicate = generation(3)
        assert duplicate == received + 1  # only the new inspection command
        peer.send('ap_items.json', {'items': ['Nano Gear', 'Starlight']})
        assert generation(4) == duplicate + 2
        # A receipt removal is also an event; native response publications are not.
        peer.send_raw(pipe.encode(0, [(pipe.DELETE, 'ap_items.json', 0, b'')]))
        assert generation(5) == duplicate + 4
    finally:
        peer.close()


@NATIVE
def test_deleted_snapshot_keeps_live_authority_without_claiming_existence(tmp_path, native_peer):
    # Old durable receipts must never become authoritative after a live deletion.
    (tmp_path / 'ap_items.json').write_text('{"items":["obsolete keyblade"]}')
    peer = RawPeer(tmp_path)
    try:
        def inspect(request):
            peer.send('runtime_ap_message.json', {'inspect_generation': request})
            while True:
                _, records = pipe.decode(peer.receive_raw())
                for _, name, _, value in records:
                    if name == 'runtime_ap_message_result.json':
                        return json.loads(value)

        assert inspect(1)['items_input_revision'] == 0  # offline bootstrap allowed
        # Explicit absence is authoritative even before the first SET.
        peer.send_raw(pipe.encode(0, [(pipe.DELETE, 'ap_items.json', 0, b'')]))
        absent = inspect(2)
        assert absent['items_input_revision'] > 0
        assert absent['items_revision'] == 0
        # Coalesced SET+DELETE cannot be missed by a delayed space consumer.
        peer.send_raw(pipe.encode(0, [
            (pipe.SET, 'ap_items.json', 0, b'{"items":["Nano Gear"]}'),
            (pipe.DELETE, 'ap_items.json', 0, b''),
        ]))
        deleted = inspect(3)
        assert deleted['items_input_revision'] > absent['items_input_revision']
        assert deleted['items_revision'] == 0
        peer.send('ap_items.json', {'items': ['Starlight']})
        restored = inspect(4)
        assert restored['items_input_revision'] > deleted['items_input_revision']
        assert restored['items_revision'] > 0
    finally:
        peer.close()


@NATIVE
def test_recovery_channels_deliver_results_and_drop_unfinished_actions_on_disconnect(tmp_path, native_peer):
    transport = pipe.PipeTransport(tmp_path).start()
    try:
        wait_until(lambda: transport.connected)
        for channel, result in (("runtime_gameflow_command.json", "runtime_gameflow_result.json"),
                                ("runtime_warp_command.json", "runtime_warp_result.json")):
            transport.write(channel, {"request_id": channel})
            wait_until(lambda: transport.read(result) == {"request_id": channel})
            wait_until(lambda: transport.read(channel) is None)
            transport.write_encoded(channel, b'{"hold":true}')
            wait_until(lambda: transport.read(result) == {"hold": True})
        generation = transport.connection_generation
        with transport.lock:
            transport._cancel.set()
        wait_until(lambda: transport.connected and transport.connection_generation > generation)
        assert not (pipe.RECOVERY_COMMANDS & transport.pending.keys())
        transport.write_encoded("runtime_ap_message.json", b'{"inspect_recovery":true}')
        wait_until(lambda: transport.read("runtime_ap_message_result.json") == {"pending": False})
        assert not list(tmp_path.glob("runtime_*command.json"))
    finally:
        transport.close()
    assert not (pipe.RECOVERY_COMMANDS & transport.pending.keys())


@NATIVE
@pytest.mark.parametrize("channel", ["runtime_music_chests.json", "runtime_keyblade_chests.json"])
@pytest.mark.parametrize("operation", [pipe.SET, pipe.DELETE, pipe.PATCH])
def test_native_rejects_client_chest_manifest_writes(tmp_path, native_peer, channel, operation):
    peer = RawPeer(tmp_path)
    try:
        peer.receive_raw()
        peer.send_raw(pipe.encode(0, [(operation, channel, 1, b'{"owner":"client","chests":[]}')]))
        with pytest.raises(OSError):
            peer.receive_raw()
    finally:
        peer.close()
    transport = pipe.PipeTransport(tmp_path).start()
    try:
        wait_until(lambda: transport.read("runtime_context.json") == {"context": "native-test"})
        assert transport.read(channel) is None
    finally:
        transport.close()


@NATIVE
def test_large_snapshot_deltas_coalesce_and_reconnect_with_full_snapshot(tmp_path, native_peer):
    transport = pipe.PipeTransport(tmp_path)
    sent = []
    original = transport._io
    def spy(handle, cancel, data=None, **kwargs):
        result = original(handle, cancel, data, **kwargs)
        if data and len(data) >= 16:
            sent.append(data)
        return result
    transport._io = spy
    payload = {"padding": "a" * 100000, "revision": 1}
    transport.write("runtime_mom_clear_state.json", payload)
    transport.start()
    try:
        wait_until(lambda: transport.read("runtime_context.json") == payload)
        for revision in range(2, 15):
            payload = {**payload, "revision": revision}
            transport.write("runtime_mom_clear_state.json", payload)
        wait_until(lambda: transport.read("runtime_context.json") == payload)
        assert any(packet[16] == pipe.PATCH and len(packet) < 200 for packet in sent)
        transport.close()
        # A new sender knows no peer bases, even when the native process lives.
        transport = pipe.PipeTransport(tmp_path)
        transport.write("runtime_mom_clear_state.json", payload)
        transport.start()
        wait_until(lambda: transport.read("runtime_context.json") == payload)
    finally:
        transport.close()


@NATIVE
@pytest.mark.parametrize("delta", [struct.pack("<QII", 999, 1, 0),
    struct.pack("<QII", 0, 100000, 0), b"short"], ids=["wrong_base", "bounds", "truncated"])
def test_bad_delta_disconnects_then_accepts_full_snapshot(tmp_path, native_peer, delta):
    peer = RawPeer(tmp_path)
    try:
        peer.receive_raw()
        peer.send("runtime_mom_clear_state.json", {"revision": 1})
        # Drain three echoed snapshots before sending the invalid patch.
        names = set()
        while "runtime_sora_state.json" not in names:
            names.update(name for _, name, _, _ in pipe.decode(peer.receive_raw())[1])
        peer.send_raw(pipe.encode(0, [(pipe.PATCH, "runtime_mom_clear_state.json", 2, delta)]))
        with pytest.raises(OSError):
            peer.receive_raw()
    finally:
        peer.close()
    transport = pipe.PipeTransport(tmp_path)
    transport.write("runtime_mom_clear_state.json", {"revision": 3})
    transport.start()
    try:
        wait_until(lambda: transport.read("runtime_context.json") == {"revision": 3})
    finally:
        transport.close()


@NATIVE
def test_native_pushes_without_request_and_reads_while_send_is_blocked(tmp_path, native_peer):
    native, lines = native_peer
    peer = RawPeer(tmp_path)
    try:
        # There is no post-handshake client request to solicit this snapshot.
        _, records = pipe.decode(peer.receive_raw())
        assert any(name == 'runtime_context.json' for _, name, _, _ in records)
        large = {'padding': 'x' * (3 * 1024 * 1024)}
        peer.send('runtime_mom_clear_state.json', large)
        assert lines.get(timeout=3) == 'large_published'
        # Leave >8 MiB of native snapshots unread, forcing its sender to block.
        # Incoming commands must still reach the native mailbox independently.
        peer.send('runtime_ap_message.json', {'ping': 1})
        assert lines.get(timeout=2) == 'control_received'
        names = set()
        while not {'runtime_context.json', 'runtime_map_ready.json', 'runtime_sora_state.json',
                   'runtime_ap_message_result.json'} <= names:
            _, records = pipe.decode(peer.receive_raw())
            names.update(name for _, name, _, _ in records)
        peer.send('runtime_ap_message.json', {'stop': True})
        native.wait(timeout=3)
    finally:
        peer.close()


@NATIVE
def test_idle_has_no_traffic_or_read_timeout_and_shutdown_cancels_read(tmp_path, native_peer):
    transport = pipe.PipeTransport(tmp_path).start()
    try:
        wait_until(lambda: transport.read('runtime_context.json') is not None)
        counters = transport.packets_sent, transport.packets_received
        # Beyond the previous five-second I/O timeout. A quiet healthy peer
        # needs no heartbeat or empty request/reply exchanges.
        time.sleep(5.2)
        assert transport.connected
        assert (transport.packets_sent, transport.packets_received) == counters == (0, 1)
        before = time.monotonic()
        transport.close()
        assert time.monotonic() - before < 1
    finally:
        transport.close()


@NATIVE
def test_native_stop_cancels_backpressured_write(tmp_path, native_peer):
    native, lines = native_peer
    peer = RawPeer(tmp_path)
    try:
        peer.receive_raw()
        peer.send('runtime_mom_clear_state.json', {'padding': 'x' * (3 * 1024 * 1024)})
        assert lines.get(timeout=3) == 'large_published'
        peer.send('runtime_ap_message.json', {'stop': True})
        native.wait(timeout=2)  # no reader draining the native write
        assert native.returncode == 0
    finally:
        peer.close()


@NATIVE
def test_malformed_peer_disconnects_without_losing_native_snapshots(tmp_path, native_peer):
    peer = RawPeer(tmp_path)
    try:
        peer.receive_raw()
        peer.send_raw(b'K3P1' + bytes(12))
        with pytest.raises(OSError):
            peer.receive_raw()
    finally:
        peer.close()
    transport = pipe.PipeTransport(tmp_path).start()
    try:
        wait_until(lambda: transport.read('runtime_context.json') == {'context': 'native-test'})
    finally:
        transport.close()


@pytest.mark.skipif(os.name != 'nt', reason='Windows overlapped pipes')
def test_python_receives_and_stops_while_its_write_is_backpressured(tmp_path):
    import _winapi as win
    handle = win.CreateNamedPipe(pipe.pipe_name(tmp_path), win.PIPE_ACCESS_DUPLEX | win.FILE_FLAG_OVERLAPPED,
        win.PIPE_TYPE_MESSAGE | win.PIPE_READMODE_MESSAGE | win.PIPE_WAIT, 1, 4096, 4096, 0, 0)
    connected = win.ConnectNamedPipe(handle, overlapped=True)
    cancel = pipe._WindowsEvent()
    server = pipe.PipeTransport(tmp_path)
    client = pipe.PipeTransport(tmp_path)
    client.write('ap_items.json', {'padding': 'x' * (3 * 1024 * 1024)})
    client.start()
    try:
        assert win.WaitForSingleObject(connected.event, 2000) == 0
        connected.GetOverlappedResult(True)
        assert server._io(handle, cancel, timeout=2000) == pipe.MAGIC
        server._io(handle, cancel, pipe.MAGIC + struct.pack('<Q', 42), timeout=2000)
        wait_until(lambda: client.connected)
        # Do not read the client's large snapshot. A native update still arrives
        # while that write is outstanding; neither lane waits for its opposite.
        server._io(handle, cancel, pipe.encode(0, [(pipe.SET, 'runtime_context.json', 1, b'{"live":true}')]), timeout=2000)
        wait_until(lambda: client.read('runtime_context.json') == {'live': True})
        assert client.packets_sent == 0  # large message remains blocked
        before = time.monotonic()
        client.close()
        assert time.monotonic() - before < 1
    finally:
        client.close()
        cancel.set()
        connected.cancel()
        connected.GetOverlappedResult(True)
        win.CloseHandle(handle)
        cancel.close()

@NATIVE
def test_repeated_client_wakeups_and_native_process_restart(tmp_path, native_peer):
    native, lines = native_peer
    transport = pipe.PipeTransport(tmp_path).start()
    restarted = None
    try:
        wait_until(lambda: transport.read('runtime_context.json') is not None)
        first_epoch = transport.epoch
        for index in range(50):
            transport.write('runtime_ap_message.json', {'ping': index})
            wait_until(lambda: transport.read('runtime_ap_message_result.json') == {'ping': index})
        native.terminate()
        native.wait(timeout=3)
        wait_until(lambda: not transport.connected)
        assert transport.read('runtime_context.json') is None
        transport.write('runtime_item_grant.json', {'request_id': 'queued-while-game-closed'})
        restarted = subprocess.Popen([os.environ['KH3_PIPE_TEST_HOST'], str(tmp_path)], stdout=subprocess.DEVNULL)
        wait_until(lambda: transport.read('runtime_receipt_ack_event.json') == {'request_id': 'queued-while-game-closed'})
        assert transport.epoch != first_epoch
        transport.acknowledge('runtime_receipt_ack_event.json')
        assert transport.read('runtime_receipt_ack_event.json') is None
    finally:
        transport.close()
        if restarted:
            restarted.terminate()
            restarted.wait(timeout=3)


@NATIVE
def test_native_completed_check_snapshot_and_goal_reconnect(tmp_path):
    metadata = dict(kh3_seed=dict(seed_name="test-seed", slot=1), bridge=dict(locations=[
        dict(bridge_key="Event:EVENT_KEYBLADE_001", source_table="Event", source_id="EVENT_KEYBLADE_001", type="event", ap_location_id=100),
        dict(bridge_key="VBonus:Vbonus_013", source_table="VBonus", source_id="Vbonus_013", type="vbonus")]))
    (tmp_path / "slot_data.json").write_text(json.dumps(metadata))
    native = subprocess.Popen([os.environ["KH3_PIPE_TEST_HOST"], str(tmp_path)], stdout=subprocess.DEVNULL)
    peer = RawPeer(tmp_path)
    serial = 0
    def command(kind, **fields):
        nonlocal serial
        serial += 1
        peer.send("runtime_ap_message.json", dict(reconciliation_test=serial, kind=kind, **fields))
        if kind != "inspect":
            assert peer.result("runtime_ap_message_result.json")["dispatched"] == serial
        else:
            return peer.result("runtime_ap_message_result.json")
    def synchronize():
        command("inspect", event=serial + 1)
    try:
        catalog = dict(schema="kh3ap.receipt_catalog.v4", process_id=native.pid, seed_name="test-seed", slot=1,
            server_checked_locations=[], items=[dict(index=0, item_id=1263022503,
                location_id=100, from_player=1)])
        peer.send("runtime_receipt_catalog.json", catalog)
        wait_until(lambda: command("inspect", event=serial + 1).get("initialized"))
        command("load", slot=0)
        wait_until(lambda: command("inspect", event=serial + 1).get("ready"))
        command("progress", channel="runtime_event_checks.json", records=[dict(schema="kh3ap.event_progress.v1", seed_name="test-seed", slot=1, completed=["EVENT_KEYBLADE_001"])])
        synchronize()
        state = command("inspect", event=serial + 1)["checks"]
        assert state["checked_locations"] == [] and state["goal_complete"] is False
        command("progress", channel="runtime_vbonus_checks.json", records=[dict(schema="kh3ap.vbonus_progress.v1", seed_name="test-seed", slot=1, completed=["Vbonus_013"])])
        wait_until(lambda: command("inspect", event=serial + 1)["active_receipts"]["through"] == 0)
        command("context", records=[dict(hook="RuntimeWorldContext", world="World /Game/Levels/ss/ss_50/ss_50")])
        wait_until(lambda: json.loads((tmp_path / "runtime_native_reconciliation.json").read_text())["goal_complete"])
        synchronize()
        peer.close(); peer = RawPeer(tmp_path)
        state = command("inspect", event=serial + 1)["checks"]
        assert state["owner"] == "dll" and state["process_id"] == native.pid
        assert state["checked_locations"] == ["Event:EVENT_KEYBLADE_001", "VBonus:Vbonus_013"]
        assert state["goal_complete"] is True
        # The public snapshot is replayed on connection, while its authority is
        # durable native metadata rather than the client's reporting ledger.
        assert "game_state.json" not in {path.name for path in tmp_path.iterdir()}
    finally:
        peer.close(); native.terminate(); native.wait(timeout=3)


@NATIVE
def test_native_reconciliation_save_load_sparse_boundaries_and_retry(tmp_path):
    (tmp_path / "slot_data.json").write_text(json.dumps({"kh3_seed": {"seed_name": "test-seed", "slot": 1}}))
    native = subprocess.Popen([os.environ["KH3_PIPE_TEST_HOST"], str(tmp_path)], stdout=subprocess.DEVNULL)
    peer = RawPeer(tmp_path)
    serial = 0
    def command(kind, **fields):
        nonlocal serial
        serial += 1
        peer.send("runtime_ap_message.json", dict(reconciliation_test=serial, kind=kind, **fields))
        if kind != "inspect":
            assert peer.result("runtime_ap_message_result.json")["dispatched"] == serial
    def inspect():
        nonlocal serial
        serial += 1
        command("inspect", event=serial)
        return peer.result("runtime_ap_message_result.json")
    def until(predicate):
        found = []
        def check():
            value = inspect()
            if predicate(value):
                found.append(value)
                return True
            return False
        wait_until(check)
        return found[-1]
    def has(value, index):
        r = value["active_receipts"]
        return index <= r["through"] or index in r["extra"]
    def context(pawn, source="VanillaLevelWritePolyhook:level_owner"):
        command("context", records=[dict(hook="RuntimePlayablePawnContext", pawn_source=source,
            object=f"p_ex001_Pawn_C /Game/Levels/tt/tt_01/tt_01.tt_01:PersistentLevel.{pawn}",
            opened_at="2026-10-03T23:00:00.000Z")])
    journal = tmp_path / "runtime_native_reconciliation.json"
    try:
        catalog = dict(schema="kh3ap.receipt_catalog.v4", process_id=native.pid, seed_name="test-seed", slot=1,
            server_checked_locations=[],
            items=[dict(index=i, item_id=1263022503, from_player=2, location_id=-1) for i in range(6)])
        until(lambda value: "initialized" in value)
        peer.send("runtime_receipt_catalog.json", dict(catalog, process_id=native.pid + 1))
        assert not inspect()["initialized"]
        peer.send("runtime_receipt_catalog.json", catalog)
        until(lambda value: value.get("initialized"))
        command("load", slot=98)
        initial = until(lambda value: value["ready"])
        epoch = initial["delivery_epoch"]
        context("Sora_1")
        command("completion", index=0)
        command("completion", index=2)
        until(lambda value: has(value, 2))
        # Only completed receipts before save-open belong to this manual slot.
        command("save_open", slot=98, event=10)
        command("completion", index=1)
        until(lambda value: value["active_receipts"]["through"] == 2)
        assert json.loads(journal.read_text())["slots"] == []
        command("save_closed", event=10, success=True)
        until(lambda value: value["ready"] and len(json.loads(journal.read_text())["slots"]) == 1)
        saved = json.loads(journal.read_text())["slots"][0]["saved_receipts"]
        assert saved == dict(version=1, through=0, extra=[2])
        command("save_open", slot=98, event=11)
        command("save_closed", event=11, success=False)
        # Load replaces membership; an old-epoch late ACK cannot reintroduce it.
        command("load", slot=98)
        restored = until(lambda value: value["delivery_epoch"] > epoch and not has(value, 1))
        command("completion", index=1, epoch=epoch)
        assert not has(inspect(), 1)
        assert 1 in restored["pending_replay_indices"]
        command("completion", index=1)
        until(lambda value: has(value, 1))
        # AUTO's native pre-serialization boundary excludes a later grant.
        command("auto_begin")
        command("completion", index=3)
        until(lambda value: has(value, 3))
        command("save_open", slot=0, event=12)
        command("save_closed", event=12, success=True)
        until(lambda value: len(json.loads(journal.read_text())["slots"]) == 2)
        auto = next(r for r in json.loads(journal.read_text())["slots"] if r["save_slot"] == 0)
        assert auto["saved_receipts"] == dict(version=1, through=2, extra=[])
        # Zone entry owns an in-memory Retry baseline newer than disk AUTO.
        context("Sora_2")
        command("completion", index=4)
        until(lambda value: has(value, 4))
        command("death", records=[dict(state_class="TresSoraPlayerStateGameOver", state_comp_matches_cached_pawn=True)])
        until(lambda value: not value["ready"])
        context("Sora_3", source="HandleStartingNewPlayer:NewPlayer:Pawn")
        retry = until(lambda value: value["ready"] and value["recovery_source"] == "death_continue_retry")
        assert has(retry, 3) and not has(retry, 4)
        assert 4 in retry["pending_replay_indices"]
        command("completion", index=4)
        until(lambda value: has(value, 4))
        # Overwriting slot 98 after loading AUTO must not union old slot 98.
        command("load", slot=0)
        until(lambda value: not has(value, 3))
        command("save_open", slot=98, event=13)
        command("save_closed", event=13, success=True)
        until(lambda value: next(r for r in json.loads(journal.read_text())["slots"] if r["save_slot"] == 98)
            ["saved_receipts"] == dict(version=1, through=2, extra=[]))
        peer.close(); native.terminate(); native.wait(5)
        # Cold recovery works without any catalog/client bootstrap.
        native = subprocess.Popen([os.environ["KH3_PIPE_TEST_HOST"], str(tmp_path)], stdout=subprocess.DEVNULL)
        peer = RawPeer(tmp_path)
        until(lambda value: value.get("initialized"))
        command("load", slot=98)
        cold = until(lambda value: value["ready"])
        assert cold["active_receipts"] == dict(version=1, through=2, extra=[])
        assert cold["pending_replay_indices"] == [3, 4, 5]
        # New grants require the current process's full item catalog after cold
        # checkpoint recovery; saved membership alone cannot authorize delivery.
        peer.send("runtime_receipt_catalog.json", dict(catalog, process_id=native.pid))
        peer.result("runtime_item_descriptions.json")
        # A write failure must retain the committed checkpoint, including its
        # readonly projection, and close delivery until the fault is repaired.
        committed = journal.read_bytes()
        (tmp_path / (journal.name + ".tmp")).mkdir()
        command("completion", index=3)
        failed = until(lambda value: value["reason"] == "journal_open_failed")
        assert not failed["ready"]
        assert journal.read_bytes() == committed
        ledger = failed["saved_slots"]
        assert ledger["slots"]["kh3sv2/data/KHIII_slot98.bin"]["saved_receipts"] == dict(version=1, through=2, extra=[])
    finally:
        peer.close()
        if native.poll() is None:
            native.terminate()
        native.wait(5)


class ReceiptSaveHost:
    """Exercise the production receipt worker with ordered native save events."""
    def __init__(self, root):
        (root / "slot_data.json").write_text(json.dumps({"kh3_seed": {"seed_name": "test-seed", "slot": 1}}))
        self.native = subprocess.Popen([os.environ["KH3_PIPE_TEST_HOST"], str(root)], stdout=subprocess.DEVNULL)
        self.peer = RawPeer(root)
        self.serial = 0
        try:
            self.until(lambda value: "initialized" in value)
            self.peer.send("runtime_receipt_catalog.json", dict(schema="kh3ap.receipt_catalog.v4",
                process_id=self.native.pid, seed_name="test-seed", slot=1, server_checked_locations=[],
                items=[dict(index=i, item_id=1263022503, from_player=2, location_id=-1) for i in range(6)]))
            self.until(lambda value: value.get("initialized"))
            self.load()
        except BaseException:
            self.close()
            raise

    def command(self, kind, **fields):
        self.serial += 1
        if kind == "inspect":
            fields["event"] = self.serial
        self.peer.send("runtime_ap_message.json", dict(reconciliation_test=self.serial,
            kind=kind, **fields))
        result = self.peer.result("runtime_ap_message_result.json")
        if kind != "inspect":
            assert result["dispatched"] == self.serial
        return result

    def until(self, predicate):
        found = []
        def check():
            value = self.command("inspect")
            if predicate(value):
                found.append(value)
                return True
            return False
        wait_until(check)
        return found[-1]

    def load(self, slot=0):
        epoch = self.command("inspect")["delivery_epoch"]
        self.command("load", slot=slot)
        return self.until(lambda value: value["ready"] and value["delivery_epoch"] > epoch)

    def grant(self, index):
        self.command("completion", index=index)
        self.until(lambda value: index <= value["active_receipts"]["through"] or
            index in value["active_receipts"]["extra"])

    def close(self):
        self.peer.close()
        self.native.terminate()
        self.native.wait(5)


@pytest.fixture
def receipt_save_host(tmp_path):
    host = ReceiptSaveHost(tmp_path)
    try:
        yield host
    finally:
        host.close()


@NATIVE
@pytest.mark.parametrize("write_offset", [101_110_450, 36_000_000_000, -50_000_000],
    ids=["reported-10.11-second-delay", "one-hour-delay", "wall-clock-backwards"])
def test_autosave_receipts_follow_events_not_elapsed_time(receipt_save_host, write_offset):
    host = receipt_save_host
    anchor = 134_360_833_209_916_658
    host.grant(0)
    host.grant(2)
    host.command("auto_begin", filetime=anchor)
    host.grant(1)  # Granted after serialization began: must still replay.
    host.command("save_open", slot=0, event=100, filetime=anchor + write_offset)
    host.grant(3)  # Completion must not capture the current inventory either.
    host.command("save_closed", event=100, success=True)
    loaded = host.load()
    assert loaded["active_receipts"] == dict(version=1, through=0, extra=[2])
    assert loaded["pending_replay_indices"] == [1, 3, 4, 5]


@NATIVE
@pytest.mark.parametrize("failure", ["open", "write"])
def test_failed_autosave_attempt_cannot_reuse_its_request(receipt_save_host, failure):
    host = receipt_save_host
    host.grant(0)
    host.command("auto_begin")
    host.command("save_open", slot=0, event=100, open_success=failure != "open")
    host.command("save_closed", event=100, success=False)
    # An unrelated successful write has no serialization boundary of its own.
    host.command("save_open", slot=0, event=101)
    host.command("save_closed", event=101, success=True)
    assert host.load()["active_receipts"] == dict(version=1, through=-1, extra=[])


@NATIVE
def test_autosave_open_binds_snapshot_and_completion_preserves_next_request(receipt_save_host):
    host = receipt_save_host
    host.grant(0)
    host.command("auto_begin")
    host.command("save_open", slot=0, event=100)
    host.grant(1)
    host.command("auto_begin")
    # Duplicate open/close notifications cannot steal the next request.
    host.command("save_open", slot=0, event=100)
    host.command("save_closed", event=100, success=True)
    host.command("save_closed", event=100, success=True)
    host.until(lambda value: value["saved_slots"]["slots"].get("kh3sv2/data/KHIII_slot0.bin", {})
        .get("saved_receipts") == dict(version=1, through=0, extra=[]))
    host.grant(2)
    host.command("save_open", slot=0, event=101)
    host.command("save_closed", event=101, success=True)
    assert host.load()["active_receipts"] == dict(version=1, through=1, extra=[])


@NATIVE
def test_completed_autosave_request_is_consumed_and_new_requests_coalesce(receipt_save_host):
    host = receipt_save_host
    host.grant(0)
    host.command("auto_begin")
    host.command("save_open", slot=0, event=100)
    host.command("save_closed", event=100, success=True)
    host.grant(1)
    host.command("auto_begin")
    host.grant(2)
    host.command("auto_begin")  # The native pending payload was replaced.
    host.command("save_open", slot=0, event=101)
    host.command("save_closed", event=101, success=True)
    # Repeated completion of the older write must not roll the slot backward.
    host.command("save_closed", event=100, success=True)
    host.grant(3)
    host.command("save_open", slot=0, event=102)
    host.command("save_closed", event=102, success=True)
    assert host.load()["active_receipts"] == dict(version=1, through=2, extra=[])


@NATIVE
@pytest.mark.parametrize("boundary", ["load", "new_game", "manual_save"])
def test_autosave_pending_request_does_not_cross_gameplay_boundaries(receipt_save_host, boundary):
    host = receipt_save_host
    host.grant(0)
    host.command("auto_begin")
    if boundary == "load":
        host.load()
    elif boundary == "new_game":
        host.command("new_game", records=[dict(source="native_new_game")])
        host.until(lambda value: value["recovery_source"] == "new_game_staging")
    else:
        host.command("save_open", slot=98, event=99)
        host.command("save_closed", event=99, success=True)
    host.command("save_open", slot=0, event=100)
    host.command("save_closed", event=100, success=True)
    assert host.load()["active_receipts"] == dict(version=1, through=-1, extra=[])


@NATIVE
@pytest.mark.parametrize("saved", ["{", json.dumps(dict(schema="kh3ap.native_reconciliation.v1",
    seed_name="wrong", slot=1, slots=[], catalog=[], complete=True)), json.dumps(dict(
    schema="kh3ap.native_reconciliation.v1", seed_name="test-seed", slot=1, slots=[dict(save_slot=98,
        saved_receipts=dict(version=2, through=9, extra=[]))], catalog=[], complete=True))])
def test_native_reconciliation_corrupt_checkpoint_fails_closed(tmp_path, saved):
    (tmp_path / "slot_data.json").write_text(json.dumps({"seed_name": "test-seed", "slot": 1}))
    (tmp_path / "runtime_native_reconciliation.json").write_text(saved)
    native = subprocess.Popen([os.environ["KH3_PIPE_TEST_HOST"], str(tmp_path)], stdout=subprocess.DEVNULL)
    peer = RawPeer(tmp_path)
    try:
        result = peer.result("runtime_reconciliation_state.json")
        assert not result["ready"] and result["reason"] not in {"ready", "awaiting_receipt_bootstrap"}
    finally:
        peer.close(); native.terminate(); native.wait(5)


class LuckyMarkHost:
    def __init__(self, root):
        self.root = root
        self.process = subprocess.Popen([os.environ["KH3_PIPE_TEST_HOST"], str(root)], stdout=subprocess.DEVNULL)
        self.peer = RawPeer(root)
        self.serial = 0
        self.closed = False

    def control(self, **fields):
        self.serial += 1
        self.peer.send("runtime_ap_message.json", {"lucky_mark_test": self.serial, **fields})
        return self.peer.result("runtime_ap_message_result.json")

    def until(self, condition):
        result = {}
        def inspect():
            nonlocal result
            result = self.control()
            return condition(result)
        wait_until(inspect)
        return result

    def close(self):
        if self.closed:
            return
        self.closed = True
        if self.process.poll() is None:
            self.peer.send("runtime_ap_message.json", {"stop": True})
            self.process.wait(timeout=5)
        self.peer.close()


def lucky_metadata(root):
    (root / "slot_data.json").write_text(json.dumps({
        "kh3_seed": {"seed_name": "test-seed", "slot": 1},
        "bridge": {"locations": [dict(source_table="LuckyMark", source_id=str(i),
            bridge_key=f"LuckyMark:{i}", type="lucky_emblem", ap_location_id=1263023257+i) for i in (1, 33, 90)] + [
            dict(source_table="Event", source_id="EVENT_001", bridge_key="Event:EVENT_001", ap_location_id=99)]}}))


@NATIVE
def test_native_lucky_milestone_reaches_ap_check_snapshot_and_recovers_missed_handoff(tmp_path):
    lucky_metadata(tmp_path)
    host = LuckyMarkHost(tmp_path)

    def checks():
        host.serial += 1
        host.peer.send("runtime_ap_message.json", dict(
            reconciliation_test=host.serial, kind="inspect", event=host.serial))
        return host.peer.result("runtime_ap_message_result.json").get("checks", {})

    def bootstrap():
        host.peer.send("runtime_receipt_catalog.json", dict(
            schema="kh3ap.receipt_catalog.v4", process_id=host.process.pid,
            seed_name="test-seed", slot=1, server_checked_locations=[], items=[]))
        host.until(lambda state: state.get("ready"))

    try:
        bootstrap()
        host.control(token="ITEM_AP_I02200_1263023258")
        wait_until(lambda: checks().get("checked_locations") == ["LuckyMark:1"])
        state = checks()
        assert state["ready"] and state["process_id"] == host.process.pid
        assert state["seed_name"] == "test-seed" and state["slot"] == 1
        host.control(token="ITEM_AP_I02200", number=1263023259)
        assert checks()["checked_locations"] == ["LuckyMark:1"]
        host.close()

        # Reproduce the missed handoff: the milestone worker committed its
        # checkpoint, but the reconciliation journal never included the check.
        journal = tmp_path / "runtime_native_reconciliation.json"
        saved = json.loads(journal.read_text())
        saved["completed_checks"] = []
        saved["observed_checks"] = []
        journal.write_text(json.dumps(saved))
        lucky_before = (tmp_path / "lucky_mark_progress.json").read_bytes()
        host = LuckyMarkHost(tmp_path)
        bootstrap()
        wait_until(lambda: checks().get("checked_locations") == ["LuckyMark:1"])
        assert (tmp_path / "lucky_mark_progress.json").read_bytes() == lucky_before
    finally:
        host.close()


@NATIVE
@pytest.mark.parametrize("padding", [0, 9 * 1024 * 1024])
def test_native_lucky_marks_commit_reconnect_and_cold_recovery(tmp_path, padding):
    lucky_metadata(tmp_path)
    slot_path = tmp_path / "slot_data.json"
    slot_path.write_text(json.dumps({**json.loads(slot_path.read_text()), "padding": "x" * padding}))
    host = LuckyMarkHost(tmp_path)
    journal = tmp_path / "lucky_mark_progress.json"
    try:
        host.until(lambda state: state.get("ready"))
        # Explicit suffix and Unreal's numbered FName representation are equivalent.
        host.control(token="ITEM_AP_I02200", number=1263023259)
        completed = host.until(lambda state: state.get("completed") == ["1"])
        assert json.loads(journal.read_text())["completed"] == ["1"]
        assert completed["milestones"] == [1]
        before = journal.read_bytes()
        host.control(token="ITEM_AP_I02200_1263023258")
        host.control(token="ITEM_AP_I02006_1263023290")
        host.until(lambda state: state.get("completed") == ["1", "33"])
        assert before != journal.read_bytes()
        # Unsupported locations, malformed/failed tokens, and diagnostic copies cannot check anything.
        for token, result in [("ITEM_AP_I02200_99", 712), ("ITEM_AP_I02200_1263023347", 0),
                              ("ITEM_AP_I02200_1263023347x", 712), ("ITEM_AP_I02200_0", 712),
                              ("ITEM_AP_I00200_1263023347", 712), ("ITEM_AP_I02256_1263023347", 712),
                              ("ITEM_AP_I02200_900719925474099199", 712), ("ITEM_POTION", 712)]:
            host.control(token=token, result=result)
        stable = journal.read_bytes()
        host.peer.close()
        host.peer = RawPeer(tmp_path)
        restored = host.until(lambda state: state.get("completed") == ["1", "33"])
        assert restored["milestones"] == [1, 33]
        assert journal.read_bytes() == stable
        host.close()
        host = LuckyMarkHost(tmp_path)
        restored = host.until(lambda state: state.get("ready"))
        assert restored["completed"] == ["1", "33"]
        assert restored["milestones"] == [1, 33]
        host.control(token="ITEM_AP_I02200_1263023347")
        host.until(lambda state: state.get("completed") == ["1", "33", "90"])
        assert json.loads(journal.read_text())["record_count"] == 3
    finally:
        host.close()


@NATIVE
@pytest.mark.parametrize("saved", ["{", "null", json.dumps(dict(schema="wrong")),
    json.dumps(dict(schema="kh3ap.lucky_mark_progress.v1", owner="dll", seed_name="wrong", slot=1,
                    record_count=1, complete=True, completed=["33"])),
    json.dumps(dict(schema="kh3ap.lucky_mark_progress.v1", owner="dll", seed_name="test-seed", slot=1,
                    record_count=2, complete=True, completed=["33"])),
    json.dumps(dict(schema="kh3ap.lucky_mark_progress.v1", owner="dll", seed_name="test-seed", slot=1,
                    record_count=1, complete=True, completed=["missing"])),
    json.dumps(dict(schema="kh3ap.lucky_mark_progress.v1", owner="dll", seed_name="test-seed", slot=1,
                    record_count=2, complete=True, completed=["33", "33"]))])
def test_native_lucky_marks_corrupt_checkpoint_fails_closed(tmp_path, saved):
    lucky_metadata(tmp_path)
    journal = tmp_path / "lucky_mark_progress.json"
    journal.write_text(saved)
    host = LuckyMarkHost(tmp_path)
    try:
        failed = host.until(lambda state: state.get("reason"))
        assert failed["ready"] is False
        assert failed["completed"] == []
        host.control(token="ITEM_AP_I02200_1263023258")
        assert host.control()["ready"] is False
        assert journal.read_text() == saved
    finally:
        host.close()


@NATIVE
def test_native_lucky_marks_failed_commit_preserves_committed_membership(tmp_path):
    lucky_metadata(tmp_path)
    host = LuckyMarkHost(tmp_path)
    journal = tmp_path / "lucky_mark_progress.json"
    try:
        host.until(lambda state: state.get("ready"))
        host.control(token="ITEM_AP_I02200_1263023258")
        host.until(lambda state: state.get("completed") == ["1"])
        before = journal.read_bytes()
        (tmp_path / "lucky_mark_progress.json.tmp").mkdir()
        host.control(token="ITEM_AP_I02200_1263023290")
        failed = host.until(lambda state: state.get("reason"))
        assert failed["ready"] is False
        assert failed["completed"] == ["1"]
        assert failed["milestones"] == []
        assert journal.read_bytes() == before
    finally:
        host.close()


@NATIVE
@pytest.mark.parametrize("mode", ["reply", "timeout", "cancel"])
def test_transient_launch_wait_consumes_or_finishes_without_reply(tmp_path, native_peer, mode):
    transport = pipe.register(tmp_path).start()
    try:
        wait_until(lambda: transport.connected)
        transport.write("runtime_ap_message.json", {"launch_wait_test": 1, mode: True})
        if mode == "reply":
            wait_until(lambda: transport.read("runtime_kh02_launch_request.json") is not None)
            transport.write("runtime_kh02_launch_response.json", {"arguments": ["test-only-secret"]})
        wait_until(lambda: transport.read("runtime_ap_message_result.json") is not None)
        assert transport.read("runtime_ap_message_result.json") == {"received": mode == "reply"}
        assert "test-only-secret" not in json.dumps(transport.diagnostic_snapshot())
        assert transport.read("runtime_kh02_launch_response.json") is None
        assert not (tmp_path / "runtime_kh02_launch_response.json").exists()
    finally:
        pipe.unregister(transport)
