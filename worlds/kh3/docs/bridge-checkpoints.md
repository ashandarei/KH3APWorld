# KH3 bridge checkpoint storage

[Local IPC](named-pipe-bridge.md) carries live state. This page describes the
files that support startup and recovery; [bridge ownership](../BRIDGE_PROTOCOL.md)
defines which side owns gameplay decisions.

## Persistence boundaries

Seed configuration (`slot_data.json`, lookup manifests and installation config),
client checkpoints, native journals and randomizer-owned save sidecars remain on
disk. Client snapshots in `transport.pipe_transport.PERSISTENT` are checkpointed
before publication and resent on connection. Deleting a persistent checkpoint
also removes its disk copy.

Five native progression snapshot channels retain verified disk checkpoints:
ingredients, minigame rewards, Classic Kingdom, Workshop and Olympus Coliseum.
They load at startup and publish changes over IPC. Other native durable journals,
including saved receipt membership, belong to their native services. Delivery
ledgers, retry windows and DeathLink runtime state exposed over IPC are native
projections, not independent Python recovery ledgers. Client DeathLink settings
and network bookkeeping remain client-owned files.

Diagnostic histories are bounded in memory and written only during
[debug-bundle export](debug-bundles.md). Diagnostic builds also support agent
request files. Ordinary bridge work does not inspect vanilla KH3 slot/system
save files; save reconciliation uses runtime evidence and randomizer-owned state.

## Revision cache and directory notifications

[BridgeState](../bridge_support/state.py) caches client-owned state by revision.
Cancellable `ReadDirectoryChangesW` on Windows or `inotify` on Linux invalidates
external replacements, writes and deletions. Unchanged cached reads do not stat
or reopen files. Notification overflow invalidates loaded entries; watcher
failure falls back to metadata validation. Explicit client commands use the same
notifications instead of idle polling.

Mutable readers receive copies; internal read-only consumers can share immutable
snapshots. Persistence returns encoded bytes to transport publication so the same
snapshot does not need encoding twice.

## AP item checkpoint journal

[checkpoint.py](../bridge_support/checkpoint.py) currently enables incremental
journaling for `ap_items.json`. Large updates use a bounded `.json.wal` byte-delta
journal; other client files retain atomic checkpoint writes. Deltas use common
prefix/suffix bounds found in 64 KiB chunks. Each append is flushed before
publication. Recovery checks the base digest, delta bounds and reconstructed
payload digests. Incomplete final appends are discarded; corrupt committed
records fail closed.

Compaction writes and flushes a new checkpoint before removing its old journal.
A fresh `_kh3_checkpoint_id` binds the base generation so an old journal cannot
reactivate when content returns to an earlier value; bridge readers strip it.
The journal is bounded to 64 updates and 4 MiB. Inefficient deltas, small snapshots
and clean client shutdown also trigger compaction.

Use `bridge_support.files.load_json_file`, or `bridge_support.checkpoint.read_bytes`
for standalone tools, to read current state. Reading the base JSON alone while a
journal exists can return old state. Preserve the base and WAL together for
recovery; do not delete journals to reduce disk use. Debug exports recover these
pairs but distribute redacted evidence, not byte-for-byte recovery archives.

## Validation

`tools/kh3/tests/test_pipe_transport.py` covers cached zero-I/O reads, external
replacement/deletion/creation, torn tails, failed flushes, corrupt records,
compaction crashes and shutdown. Linux watcher checks also live in
`tools/kh3/tests/test_local_socket_transport.py`. Use the
[development test runner](development.md#tests).
