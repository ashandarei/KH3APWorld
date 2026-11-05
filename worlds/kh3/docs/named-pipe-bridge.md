# KH3 local IPC bridge

The client and UE4SS runtime use named pipes on Windows and authenticated loopback
TCP under Linux/Proton. Both endpoints must support protocol v3. The integrated
bridge and external CLI's `save-files` and `runtime-loot-events` backends register
a live session; missing, disconnected or incompatible runtimes never select file
IPC. Registered sessions also suppress stale runtime files during shutdown.

This page covers transport. Related contracts:

- [Bridge ownership](../BRIDGE_PROTOCOL.md): AP facts, native delivery and save replay.
- [Checkpoint storage](bridge-checkpoints.md): durable files, caching and WAL recovery.
- [Debug bundles](debug-bundles.md): on-demand evidence, redaction and crash artifacts.

## Channels

The transport carries AP status/items and checked locations; grants, receipts,
traps and messages; pawn/map/UI state; save/retry and progression state; and
loot, notification and diagnostic events. Existing JSON schemas and request IDs
remain independent of the transport version. Names ending in `.json` or `.jsonl`
identify channels, not files to poll.

[Python channel definitions](../transport/pipe_transport.py) match the native
`bridge/transport/bridge_channels.h` registry; a contract test checks parity.
Channel categories distinguish client checkpoints/commands, native snapshots,
queued events and diagnostic histories. Histories are bounded in-memory evidence,
not recovery checkpoints; see [debug bundles](debug-bundles.md).

## Transport and recovery

### Windows

The endpoint is `\\.\pipe\KH3AP-v3-<hash>`, scoped to the configured bridge root.
The hash is FNV-1a over the absolute Windows path encoded as UTF-16LE, with ASCII
case folding and normalized separators. A version and runtime-instance handshake
precedes every connection. The server accepts one local client, rejects remote
pipe clients and uses cancellable overlapped I/O.

### Linux / Proton

The runtime detects Wine through its `ntdll` export and listens on `127.0.0.1`
at an OS-assigned port. Native Linux Python and Windows Python under Wine select
this transport automatically; native Windows retains named pipes. No player port
setting or Wine-side Python helper is required.

The listener atomically publishes `ipc_endpoint.json` once per listener lifetime:
protocol, loopback address, port, random 256-bit token and PID. An exclusive
`ipc_endpoint.lock` prevents another runtime taking over the bridge directory.
Both endpoints use the existing Linux-to-Wine `Z:` path mapping. The client caches
discovery metadata and watches directory changes, so disconnected retries do not
reread an unchanged descriptor. Clean shutdown removes the descriptor; stale
crash descriptors cannot authenticate to an unrelated listener.

Stream messages have a 32-bit little-endian length, bounded to 8 MiB. The first
message is `K3P3` plus the 64 ASCII hex token characters. After authentication,
the server replies with `K3P3` plus its 64-bit epoch and sends runtime snapshots.
Subsequent messages contain unmodified v3 packets. Partial reads and coalesced
writes are supported; TCP_NODELAY avoids small-message batching delays.

Client and Proton must share the bridge directory and loopback network namespace,
as in the existing Steam/Flatpak setup. macOS is unsupported. Standalone transport
tests do not establish full live-game Linux/Proton compatibility.

### Packet format

Packets are bounded to 8 MiB and begin with `K3P3`, a little-endian 64-bit snapshot
cursor and 32-bit record count. Each record contains an operation byte
(set/delete/ack/patch), 16-bit ASCII channel-name length, 64-bit revision, 32-bit
payload length, then channel and payload. SET carries UTF-8 JSON. PATCH carries
a 64-bit base revision, 32-bit common-prefix length, 32-bit common-suffix length
and replacement bytes. Only client-owned persistent snapshots accept patches.
Invalid bases, bounds, incomplete packets and unknown records close the connection.

Snapshot batches split at packet boundaries; individual native publications are
limited to 4 MiB. Every connection starts with full client snapshots. Later large
publications use deltas only when they save at least 25%. Patch bases advance
after a successful write, so coalescing cannot patch an unsent version.

### Scheduling, acknowledgements and reconnects

Each endpoint has independent receive/send lanes. Publication wakes the sender;
reads wait for data or cancellation. Idle connections send no packets and have
no periodic transport wakeup. Native workers handle bytes and mutex-protected
mailboxes; pending input wakes the existing game-thread driver, where UObject
access and grant safety checks remain. Receipt activity wakes the Python bridge
and asyncio watcher.

The native sender owns its snapshot cursor and drains split packets without a
client request. Five-second handshake/write deadlines detect stalled peers;
TCP also bounds partial-frame reads to five seconds. Cancellation wakes both I/O
lanes and drains outstanding operations before releasing buffers or handles.
Disconnected clients retry with a 500 ms backoff.

Snapshots resend after reconnect. Queued events remain in the running runtime
until explicitly acknowledged; consumers persist required effects before ACK.
Diagnostic histories are retained in client memory on receipt. Conditional
command deletion includes the old value so an old response cannot delete a newer
request. Gameplay request IDs and native ledgers own deduplication: transport
receipt does not prove a gameplay operation succeeded. Undelivered memory-backed
events are lost when the game exits; game saves and AP reconciliation remain the
recovery sources.

`bridge_runtime_status.json` reports `transport`, `pipe_connected` and `pipe_error`.
An old runtime leaves the client waiting for an updated endpoint. Disconnected
snapshots retained for diagnostics are unavailable to gameplay readers.

## Validation and installation

Run tests from the APWorld checkout using the [development test runner](development.md#tests):

- `tools/kh3/tests/test_pipe_transport.py`: framing, channel parity, unsolicited
  pushes, reconnects, split/delta snapshots, malformed peers, idle traffic,
  bidirectional backpressure and cancellation.
- `worlds/kh3/test/test_pipe_bridge.py`: bridge integration, native snapshot
  authority, pipe-only AP messages and immediate client wakeup.
- `tools/kh3/tests/test_local_socket_transport.py`: authenticated socket transport
  and Linux directory notifications. For native Linux Python against the Windows
  test host under Wine, set `KH3_PIPE_TEST_HOST` to the EXE's Linux path and
  `KH3_WINE` if the loader is not `/usr/lib/wine/wine64`; use an initialized private
  `WINEPREFIX`. The harness supplies the Wine-visible bridge path and exercises
  automatic Wine detection. Only Python's standard library is needed at runtime;
  pytest/Wine are test tools.

The standalone runtime's `scripts/test.ps1` owns native transport tests. For a
local DLL update, run `../KH3Runtime/scripts/build.ps1 -Install` with KH3 closed,
following repository build rules, then start the updated `KH3Client.py`. Restart
KH3 if it was using an older DLL. Local DLL iteration needs no `.apworld` rebuild;
full gameplay validation is separate from standalone IPC tests.
