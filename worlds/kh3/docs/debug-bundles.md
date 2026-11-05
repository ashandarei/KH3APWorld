# KH3 debug bundles

Debug bundles capture evidence on demand. See [local IPC](named-pipe-bridge.md)
for transport behavior and [checkpoint storage](bridge-checkpoints.md) for recovery
files.

## Live and disconnected transport evidence

Debug bundle format 3 captures the registered client transport on demand in
`pipe/transport_snapshot.json`: connection/epoch/counters, available channel
snapshots, pending commands and acknowledgments, every queued unacknowledged
event, and the last 64 retained entries per transport journal. Export neither
connects to the game nor drains or acknowledges traffic. On disconnect, the
client retains its last received runtime snapshots in memory for diagnostics
only, marked `historical_not_live` with capture time and epoch. Gameplay readers
still lose those snapshots immediately. This adds no periodic disk writes.

Export works with the game closed, the pipe disconnected, or no registered
session. The bundle explicitly reports `disconnected`, `closed`, or
`unavailable`/`no_registered_session` as appropriate; it never treats old disk
mailboxes as current pipe data. A client restart loses the previous process's
in-memory snapshots. Durable AP state, delivery/replay ledgers, traces, UE4SS
logs, and current/recent KH3 client logs remain collectable without a pipe
connection. Crash artifact discovery is disabled unless explicitly requested
in the export modal, and includes only files modified on the current local date.
Report-directory timestamps cannot bring older dump files into the export.

## Checkpoint capture and redaction

`bridge/` contains redacted text copies of captured checkpoint bases and other
bridge files. Binary WALs and opaque attachments are omitted because text
redaction cannot safely scrub them. `bridge-state/` contains redacted JSON
recovered from the captured pairs, including committed changes still in the
WAL. Source files remain untouched. The manifest records omitted files, missing,
changing or corrupt files and ignored incomplete tails without aborting the
rest of the export. Capture uses the registered writer's state lock per pair,
with bounded identity-checked retries for external/offline writers; compression
does not reread those pairs. Pipe and file captures have separate timestamps,
so a bundle is not a globally paused game snapshot or a complete packet history.
Events lost before delivery and never persisted/logged cannot be recovered from
a later bundle. Vanilla game slot/system saves remain excluded.

The same export boundary redacts logs, JSON/JSONL snapshots, runtime settings,
XML crash reports, and summary metadata. Recovery diagnostics remain available,
but exported bases are not byte-for-byte recovery evidence. Dump opt-in and
explicit passive capture preserve dump bytes and mark the bundle for private
support only; binary memory dumps cannot be redacted by the text filter.

Diagnostic histories are bounded in memory and are not recovery checkpoints.
Bundles identify `local_tcp` or `named_pipe`; socket discovery tokens and lock
files are omitted.

## Validation

[Debug-bundle tests](../test/test_debug_bundle.py) cover connected, disconnected
and offline capture, WAL recovery, racing writers, redaction, credential omission
and crash opt-in. [Redaction tests](../test/test_debug_redaction.py) cover the text
filter. Use the [development test runner](development.md#tests).
