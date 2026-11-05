"""Real Windows/Wine probe ↔ native Python IPC and Linux directory notifications."""
import importlib
import json
import os
from pathlib import Path
import queue
import socket
import struct
import subprocess
import sys
import threading
import time
import types

import pytest

ROOT = Path(__file__).resolve().parents[3]
PACKAGE = "kh3_socket_tests"
package = types.ModuleType(PACKAGE)
package.__path__ = [str(ROOT / "worlds/kh3")]
sys.modules.setdefault(PACKAGE, package)
pipe = importlib.import_module(f"{PACKAGE}.transport.pipe_transport")
files = importlib.import_module(f"{PACKAGE}.bridge_support.files")
sockets = importlib.import_module(f"{PACKAGE}.transport.socket_transport")


def wait_until(predicate, timeout=10):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return
        time.sleep(.01)
    raise AssertionError("Timed out waiting for local IPC")


def host_command(root):
    executable = os.environ.get("KH3_PIPE_TEST_HOST")
    if not executable:
        pytest.skip("requires compiled production transport host")
    if os.name == "nt":
        return [executable, str(root), "--tcp"]
    # No --tcp override: exercise the production Wine/Proton auto-detection.
    return [os.environ.get("KH3_WINE", "/usr/lib/wine/wine64"), executable,
            "Z:" + str(root).replace("/", "\\")]


@pytest.fixture
def start_host():
    processes = []

    def start(root):
        native = subprocess.Popen(host_command(root), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        processes.append(native)
        lines = queue.Queue()
        threading.Thread(target=lambda: [lines.put(line.strip()) for line in native.stdout], daemon=True).start()
        def ready():
            assert native.poll() is None, "Native host exited before publishing its endpoint"
            try:
                sockets.endpoint_from_payload(json.loads((root / sockets.ENDPOINT_FILE).read_text()))
                return True
            except (OSError, ValueError):
                return False
        wait_until(ready, timeout=30)
        return native, lines

    yield start
    for native in processes:
        if native.poll() is None:
            native.terminate()
        native.wait(timeout=5)


class RawPeer:
    def __init__(self, root, token=None, receive_buffer=None):
        self.connection = sockets.SocketConnection()
        if receive_buffer is not None:
            self.connection.socket.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, receive_buffer)
        address, actual_token = sockets.endpoint_from_payload(json.loads((root / sockets.ENDPOINT_FILE).read_text()))
        self.connection.connect(address)
        self.connection.io(pipe.MAGIC + (actual_token if token is None else token), timeout=5000)
        self.hello = self.connection.io(timeout=5000)
        assert self.hello[:4] == pipe.MAGIC and len(self.hello) == 12

    def send(self, name, value):
        self.connection.io(pipe.encode(0, [(pipe.SET, name, 1,
            json.dumps(value, separators=(",", ":")).encode())]), timeout=5000)

    def receive(self):
        return pipe.decode(self.connection.io(timeout=5000))[1]

    def close(self):
        self.connection.close()


def test_recovery_commands_are_not_replayed_after_socket_reconnect(tmp_path, start_host):
    start_host(tmp_path)
    transport = pipe.PipeTransport(tmp_path, transport="local_tcp").start()
    try:
        wait_until(lambda: transport.connected)
        assert transport.server_process_id == json.loads((tmp_path / sockets.ENDPOINT_FILE).read_text())["pid"]
        for channel, result in (("runtime_gameflow_command.json", "runtime_gameflow_result.json"),
                                ("runtime_warp_command.json", "runtime_warp_result.json")):
            transport.write_encoded(channel, b'{"hold":true}')
            wait_until(lambda: transport.read(result) == {"hold": True})
        generation = transport.connection_generation
        with transport.lock:
            transport._cancel.set()
        wait_until(lambda: transport.connected and transport.connection_generation > generation)
        transport.write_encoded("runtime_ap_message.json", b'{"inspect_recovery":true}')
        wait_until(lambda: transport.read("runtime_ap_message_result.json") == {"pending": False})
        assert not (pipe.RECOVERY_COMMANDS & transport.pending.keys())
        assert not list(tmp_path.glob("runtime_*command.json"))
    finally:
        transport.close()


def test_local_delivery_client_first_unicode_reconnect_and_durable_ack(tmp_path, start_host):
    root = tmp_path / "Sóra 日本"
    root.mkdir()
    transport = pipe.register(root, transport="local_tcp").start()
    try:
        # Cache a missing descriptor before the probe creates it; OS events must invalidate it.
        wait_until(lambda: sockets.ENDPOINT_FILE in transport.state.entries)
        native, _ = start_host(root)
        wait_until(lambda: transport.read("runtime_context.json") == {"context": "native-test"})
        assert transport.transport == "local_tcp" and not transport.state.failed
        payload = {"request_id": "local-burst", "indices": list(range(1000))}
        files.atomic_write_json(root / "runtime_item_grant.json", payload)
        wait_until(lambda: transport.read("runtime_receipt_ack_event.json") == payload)
        old_epoch = transport.epoch
        pipe.unregister(transport)
        # No ACK was sent: the same receipt must replay, with no new grant.
        transport = pipe.register(root, transport="local_tcp").start()
        wait_until(lambda: transport.read("runtime_receipt_ack_event.json") == payload)
        assert transport.epoch == old_epoch
        files.acknowledge_pipe_event(root / "runtime_receipt_ack_event.json")
        seen = []
        for _ in range(20):
            wait_until(lambda: transport.read("runtime_loot_event.json") is not None)
            seen.append(transport.read("runtime_loot_event.json")["check"])
            files.acknowledge_pipe_event(root / "runtime_loot_event.json")
        assert seen == list(range(20))
        wait_until(lambda: len(transport.lines("current_state_write_polyhook_events.jsonl")) == 20)
        assert not (root / "runtime_item_grant.json").exists()
        assert not (root / "runtime_receipt_ack_event.json").exists()
        pipe.unregister(transport)
        transport = pipe.register(root, transport="local_tcp").start()
        wait_until(lambda: transport.read("runtime_context.json") == {"context": "native-test"})
        assert transport.epoch == old_epoch
        assert transport.read("runtime_receipt_ack_event.json") is None
        files.atomic_write_json(root / "runtime_ap_message.json", {"stop": True})
        native.wait(timeout=3)
        wait_until(lambda: not transport.connected)
        assert transport.read("runtime_context.json") is None
        diagnostic = transport.diagnostic_snapshot()
        assert diagnostic["transport"] == "local_tcp"
        assert diagnostic["last_disconnected_runtime_snapshots"]["status"] == "historical_not_live"
    finally:
        pipe.unregister(transport)


def test_probe_restart_replaces_endpoint_and_recovers_queued_command(tmp_path, start_host):
    native, _ = start_host(tmp_path)
    transport = pipe.register(tmp_path, transport="local_tcp").start()
    try:
        wait_until(lambda: transport.read("runtime_context.json") is not None)
        epoch = transport.epoch
        descriptor = (tmp_path / sockets.ENDPOINT_FILE).read_bytes()
        native.terminate()
        native.wait(timeout=5)
        wait_until(lambda: not transport.connected)
        transport.write("runtime_item_grant.json", {"request_id": "queued-offline"})
        restarted, _ = start_host(tmp_path)
        wait_until(lambda: transport.read("runtime_receipt_ack_event.json") == {"request_id": "queued-offline"})
        assert transport.epoch != epoch
        assert (tmp_path / sockets.ENDPOINT_FILE).read_bytes() != descriptor
        assert not transport.state.failed
    finally:
        pipe.unregister(transport)


def test_local_deltas_coalesce_and_reconnect_with_full_state(tmp_path, start_host):
    start_host(tmp_path)
    transport = pipe.PipeTransport(tmp_path, transport="local_tcp")
    sent, original = [], transport._io

    def spy(handle, cancel, data=None, **kwargs):
        result = original(handle, cancel, data, **kwargs)
        if data and data.startswith(pipe.MAGIC) and len(data) > 68:
            sent.append(data)
        return result

    transport._io = spy
    payload = {"padding": "x" * 100000, "revision": 1}
    transport.write("runtime_mom_clear_state.json", payload)
    transport.start()
    try:
        wait_until(lambda: transport.read("runtime_context.json") == payload)
        for revision in range(2, 20):
            payload = {**payload, "revision": revision}
            transport.write("runtime_mom_clear_state.json", payload)
        wait_until(lambda: transport.read("runtime_context.json") == payload)
        assert any(packet[16] == pipe.PATCH and len(packet) < 200 for packet in sent)
        transport.close()
        transport = pipe.PipeTransport(tmp_path, transport="local_tcp")
        transport.write("runtime_mom_clear_state.json", payload)
        transport.start()
        wait_until(lambda: transport.read("runtime_context.json") == payload)
    finally:
        transport.close()


def test_local_idle_connection_has_no_traffic_or_disk_reads(tmp_path, start_host, monkeypatch):
    start_host(tmp_path)
    transport = pipe.PipeTransport(tmp_path, transport="local_tcp").start()
    try:
        wait_until(lambda: transport.read("runtime_context.json") is not None)
        counters = transport.packets_sent, transport.packets_received
        with monkeypatch.context() as guards:
            guards.setattr(Path, "open", lambda *a, **k: pytest.fail("idle IPC opened a file"))
            guards.setattr(Path, "stat", lambda *a, **k: pytest.fail("idle IPC statted a file"))
            time.sleep(5.2)
            assert transport.connected
            assert (transport.packets_sent, transport.packets_received) == counters
        started = time.monotonic()
        transport.close()
        assert time.monotonic() - started < 1
    finally:
        transport.close()


@pytest.mark.parametrize("bad", [b"bad-token" * 8, b"K3P1" + bytes(64)])
def test_unauthenticated_peer_cannot_read_snapshots(tmp_path, start_host, bad):
    start_host(tmp_path)
    address, _ = sockets.endpoint_from_payload(json.loads((tmp_path / sockets.ENDPOINT_FILE).read_text()))
    peer = sockets.SocketConnection()
    try:
        peer.connect(address)
        peer.io(bad, timeout=5000)
        with pytest.raises(OSError):
            peer.io(timeout=5000)
    finally:
        peer.close()
    good = RawPeer(tmp_path)
    try:
        # Startup workers may publish level/deathlink state before context.
        # Authentication permits the context snapshot regardless of order.
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            records = good.receive()
            context = next((value for _, name, _, value in records
                            if name == "runtime_context.json"), None)
            if context is not None:
                assert json.loads(context) == {"context": "native-test"}
                break
        else:
            pytest.fail("Authenticated peer did not receive the context snapshot")
    finally:
        good.close()


@pytest.mark.parametrize("bad", [struct.pack("<I", 0), struct.pack("<I", pipe.MAX_PACKET + 1),
                                  struct.pack("<I", 16) + b"K3P1" + bytes(12)])
def test_malformed_frames_disconnect_and_preserve_native_state(tmp_path, start_host, bad):
    start_host(tmp_path)
    peer = RawPeer(tmp_path)
    try:
        peer.receive()
        assert peer.connection.socket.send(bad) == len(bad)
        with pytest.raises((OSError, ValueError)):
            peer.receive()
    finally:
        peer.close()
    good = RawPeer(tmp_path)
    try:
        assert good.receive()[0][1] == "runtime_context.json"
    finally:
        good.close()


@pytest.mark.parametrize("channel", ["runtime_music_chests.json", "runtime_keyblade_chests.json"])
@pytest.mark.parametrize("operation", [pipe.SET, pipe.DELETE, pipe.PATCH])
def test_native_rejects_client_chest_manifest_writes(tmp_path, start_host, channel, operation):
    start_host(tmp_path)
    peer = RawPeer(tmp_path)
    try:
        peer.receive()
        peer.connection.io(pipe.encode(0, [(operation, channel, 1, b'{"owner":"client","chests":[]}')]), timeout=5000)
        with pytest.raises(OSError):
            peer.receive()
    finally:
        peer.close()
    transport = pipe.PipeTransport(tmp_path, transport="local_tcp").start()
    try:
        wait_until(lambda: transport.read("runtime_context.json") == {"context": "native-test"})
        assert transport.read(channel) is None
    finally:
        transport.close()


def test_fragmented_and_coalesced_frames(tmp_path, start_host):
    start_host(tmp_path)
    peer = RawPeer(tmp_path)
    try:
        peer.receive()
        frames = []
        for name, value in (("runtime_ap_message.json", b'{"message":"split"}'),
                            ("runtime_item_grant.json", b'{"request_id":"joined"}')):
            packet = pipe.encode(0, [(pipe.SET, name, 1, value)])
            frames.append(struct.pack("<I", len(packet)) + packet)
        data = b"".join(frames)
        # Split even the length prefix, then coalesce two messages in one write.
        for part in (data[:1], data[1:3], data[3:9], data[9:]):
            assert peer.connection.socket.send(part) == len(part)
        seen = {}
        while not {"runtime_ap_message_result.json", "runtime_receipt_ack_event.json"} <= seen.keys():
            seen.update({name: json.loads(value) for op, name, rev, value in peer.receive() if op == pipe.SET})
        assert seen["runtime_receipt_ack_event.json"]["request_id"] == "joined"
        assert seen["runtime_ap_message_result.json"]["message"] == "split"
    finally:
        peer.close()


def test_native_backpressured_sender_does_not_block_control_or_shutdown(tmp_path, start_host):
    native, lines = start_host(tmp_path)
    peer = RawPeer(tmp_path, receive_buffer=4096)
    try:
        peer.receive()
        peer.send("runtime_mom_clear_state.json", {"padding": "x" * (3 * 1024 * 1024)})
        assert lines.get(timeout=5) == "large_published"
        peer.send("runtime_ap_message.json", {"stop": True})
        native.wait(timeout=3)
        assert native.returncode == 0
    finally:
        peer.close()


def test_waiting_for_game_does_not_poll_endpoint_file(tmp_path, monkeypatch):
    transport = pipe.PipeTransport(tmp_path, transport="local_tcp").start()
    try:
        wait_until(lambda: sockets.ENDPOINT_FILE in transport.state.entries)
        with monkeypatch.context() as guards:
            guards.setattr(Path, "open", lambda *a, **k: pytest.fail("reconnect opened unchanged descriptor"))
            guards.setattr(Path, "stat", lambda *a, **k: pytest.fail("reconnect statted unchanged descriptor"))
            time.sleep(1.1)
            assert not transport.connected
            assert not transport.state.failed
    finally:
        transport.close()


@pytest.mark.skipif(os.name == "nt", reason="Linux backpressure; Windows loopback can buffer the whole test frame")
def test_linux_python_receives_and_cancels_while_socket_write_is_backpressured(tmp_path, monkeypatch):
    listener = socket.socket()
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    listener.settimeout(5)
    descriptor = {"protocol": "KH3AP-v3", "host": "127.0.0.1",
                  "port": listener.getsockname()[1], "token": "b" * 64, "pid": 312}
    (tmp_path / sockets.ENDPOINT_FILE).write_text(json.dumps(descriptor))
    connect = sockets.SocketConnection.connect

    def small_buffer(self, address):
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4096)
        return connect(self, address)

    monkeypatch.setattr(sockets.SocketConnection, "connect", small_buffer)
    transport = pipe.PipeTransport(tmp_path, transport="local_tcp")
    transport.write("ap_items.json", {"padding": "x" * (3 * 1024 * 1024)})
    transport.start()
    peer = None

    def exact(peer, size):
        result = b""
        while len(result) < size:
            result += peer.recv(size - len(result))
        return result

    try:
        peer, _ = listener.accept()
        peer.settimeout(5)
        size, = struct.unpack("<I", exact(peer, 4))
        assert exact(peer, size) == pipe.MAGIC + b"b" * 64
        hello = pipe.MAGIC + struct.pack("<Q", 123)
        peer.sendall(struct.pack("<I", len(hello)) + hello)
        wait_until(lambda: transport.connected)
        packet = pipe.encode(0, [(pipe.SET, "runtime_context.json", 1, b'{"live":true}')])
        peer.sendall(struct.pack("<I", len(packet)) + packet)
        wait_until(lambda: transport.read("runtime_context.json") == {"live": True})
        assert transport.packets_sent == 0  # no server reads drain the large pending frame
        started = time.monotonic()
        transport.close()
        assert time.monotonic() - started < 1
    finally:
        transport.close()
        if peer:
            peer.close()
        listener.close()


@pytest.mark.parametrize("delta", [struct.pack("<QII", 999, 1, 0),
                                    struct.pack("<QII", 1, 100000, 0), b"short"])
def test_bad_delta_disconnects_without_corrupting_snapshot(tmp_path, start_host, delta):
    start_host(tmp_path)
    peer = RawPeer(tmp_path)
    try:
        peer.receive()
        peer.send("runtime_mom_clear_state.json", {"revision": 1})
        seen = set()
        while "runtime_sora_state.json" not in seen:
            seen.update(name for _, name, _, _ in peer.receive())
        peer.connection.io(pipe.encode(0, [(pipe.PATCH, "runtime_mom_clear_state.json", 2, delta)]), timeout=5000)
        with pytest.raises(OSError):
            peer.receive()
    finally:
        peer.close()
    transport = pipe.PipeTransport(tmp_path, transport="local_tcp")
    transport.write("runtime_mom_clear_state.json", {"revision": 3})
    transport.start()
    try:
        wait_until(lambda: transport.read("runtime_context.json") == {"revision": 3})
    finally:
        transport.close()


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux inotify")
def test_linux_checkpoint_notifications_replacement_wal_delete_and_watch_loss(tmp_path, monkeypatch):
    transport = pipe.register(tmp_path).start()
    path = tmp_path / "item_delivery_state.json"
    try:
        assert transport.transport == "local_tcp"
        assert files.load_optional_json_file(path) is None
        path.write_text('{"revision":1}')
        wait_until(lambda: files.load_optional_json_file(path) == {"revision": 1})
        temporary = tmp_path / "replace.tmp"
        temporary.write_text('{"revision":2}')
        temporary.replace(path)
        wait_until(lambda: files.load_optional_json_file(path) == {"revision": 2})
        wal_path = tmp_path / "item_delivery_ledger.json"
        wal_path.write_text('{"padding":"' + "a" * 100000 + '","revision":1}')
        wait_until(lambda: files.load_optional_json_file(wal_path) is not None)
        checkpoint = importlib.import_module(f"{PACKAGE}.bridge_support.checkpoint").Checkpoint(wal_path)
        checkpoint.write(checkpoint.value.replace(b':1}', b':2}'))
        wait_until(lambda: files.load_optional_json_file(wal_path)["revision"] == 2)
        # Drain pending own writes before asserting zero-I/O cached reads.
        with transport.state.lock:
            transport.state._load(path.name)
            transport.state._load(wal_path.name)
        time.sleep(.05)
        files.load_json_file(path)
        files.load_json_file(wal_path)
        with monkeypatch.context() as guards:
            guards.setattr(Path, "stat", lambda *a, **k: pytest.fail("cache hit stat"))
            guards.setattr(Path, "open", lambda *a, **k: pytest.fail("cache hit open"))
            for _ in range(100):
                assert files.load_json_file(path)["revision"] == 2
        path.unlink()
        wait_until(lambda: files.load_optional_json_file(path) is None)
        # A directory move loses this watch; foreground validation preserves correctness.
        moved = tmp_path.with_name(tmp_path.name + "-moved")
        tmp_path.rename(moved)
        wait_until(lambda: transport.state.failed)
        moved.rename(tmp_path)
        path.write_text('{"revision":3}')
        assert files.load_json_file(path) == {"revision": 3}
    finally:
        pipe.unregister(transport)


@pytest.mark.parametrize("change", [{"host": "0.0.0.0"}, {"host": "remote.example"},
    {"port": 0}, {"port": 65536}, {"port": True}, {"token": "bad"}, {"protocol": "KH3AP-v2"}])
def test_descriptor_rejects_remote_hosts_bad_ports_and_tokens(change):
    descriptor = {"host": "127.0.0.1", "port": 50000, "token": "a" * 64, "protocol": "KH3AP-v3", **change}
    with pytest.raises(ValueError):
        sockets.endpoint_from_payload(descriptor)


@pytest.mark.skipif(os.name != "nt", reason="Windows endpoint DACL")
def test_endpoint_token_file_has_private_noninherited_permissions(tmp_path, start_host):
    import ctypes
    from ctypes import wintypes

    start_host(tmp_path)
    security = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    security.GetFileSecurityW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD,
        wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    security.GetFileSecurityW.restype = wintypes.BOOL
    security.ConvertSecurityDescriptorToStringSecurityDescriptorW.argtypes = [
        wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD,
        ctypes.POINTER(wintypes.LPWSTR), ctypes.POINTER(wintypes.DWORD)]
    security.ConvertSecurityDescriptorToStringSecurityDescriptorW.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [wintypes.HLOCAL]
    kernel.LocalFree.restype = wintypes.HLOCAL
    path = str(tmp_path / sockets.ENDPOINT_FILE)
    dacl_information = 0x00000004
    sddl_revision = 1
    needed = wintypes.DWORD()
    security.GetFileSecurityW(path, dacl_information, None, 0, ctypes.byref(needed))
    assert needed.value
    descriptor = ctypes.create_string_buffer(needed.value)
    assert security.GetFileSecurityW(path, dacl_information, descriptor, len(descriptor), ctypes.byref(needed))
    text = wintypes.LPWSTR()
    assert security.ConvertSecurityDescriptorToStringSecurityDescriptorW(
        descriptor, sddl_revision, dacl_information, ctypes.byref(text), None)
    try:
        dacl = text.value
        assert dacl.startswith("D:P")
        assert dacl.count("(A;") == 2
        assert ";;;SY)" in dacl
        for public_sid in ("WD", "AN", "AU", "BU", "BA"):
            assert f";;;{public_sid})" not in dacl
    finally:
        kernel.LocalFree(ctypes.cast(text, wintypes.HLOCAL))
