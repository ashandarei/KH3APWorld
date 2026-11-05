# KH3 bridge contract

The KH3 client exchanges live AP and gameplay state with the installed
`KH3APProbeCpp` UE4SS runtime. Python owns AP networking and publishes received
item/check facts. The DLL owns native checks, reward decoding, grant scheduling,
save-linked receipt membership, retry reconciliation and DeathLink execution.
The client normally starts its integrated bridge; a separate stub or manual
item-application loop is not required.

## Protocol and transport

The payload contract in `Bridge.py` is `kh3_ap_file_bridge`, version `1`.
The historical name remains the schema identifier; it does not select file IPC.
The live transport is independently versioned as `K3P3`:

- Windows uses a local named pipe scoped to the bridge directory.
- Linux/Proton uses authenticated loopback TCP with endpoint discovery in the
  same bridge directory.
- A missing, disconnected or incompatible runtime does not fall back to file
  mailboxes. Reconnection restores current snapshots through the transport.
- Names such as `runtime_item_grant.json` identify IPC channels. Their suffix
  does not mean a file should be written or polled.

See [local IPC](docs/named-pipe-bridge.md) for framing, channel sets, handshake,
acknowledgements, reconnect behavior and transport tests. The authoritative
channel definitions are in `transport/pipe_transport.py` and the native
`bridge/transport/bridge_channels.h`; native transport execution is in `bridge/transport/pipe_transport.cpp`.

## Bridge identity

The default bridge root is `Utils.user_path("kh3", "bridge")`; the client accepts
`--bridge-root` for an explicit root. Each initialized run uses a seed/player
subdirectory. Local development commonly uses the repository's `kh3/bridge`.
Verify the active directory against `ap_status.json` fields `installed_pak`,
`seed_name` and `player_name`, and the installed seed pak. Directory recency is
not evidence that it belongs to the running seed.

The client and runtime must share the seed, player, bridge root and normal
SaveGames directory. [Environment and paths](docs/development.md)
documents discovery and override precedence.

## Seed data

`slot_data.json` and generated metadata use the compact `location_catalog`
contract in `RuntimeSeed.py`. Version 1 references the bundled location catalog
by SHA-256 and stores deduplicated item assignments plus per-location overrides.
Readers expand that document at their boundary; `bridge.locations` describes
its expanded in-memory form, not the raw stored file. Invalid versions,
references and catalog hashes are rejected. Newly generated seeds are the
supported target.

Canonical check keys are `<source_table>:<source_id>`, such as
`TreasureHE:HE_LBOX_001`, `Event:EVENT_KEYBLADE_001` and `VBonus:Vbonus_067`.
The native check pipeline maps those identities to the seed's AP location IDs.

## State and ownership

| State | Owner and behavior |
| --- | --- |
| AP connection, received items and server-confirmed checks | Client publishes normalized facts through `ap_status.json` and `ap_items.json`. |
| Native checks and goal | DLL resolves runtime evidence; Python sends the corresponding AP network updates. |
| Item delivery | DLL decodes the reward catalog and applies receipt groups only after live safety gates pass. Historical acknowledgements alone do not suppress save replay. |
| Saved receipt membership | Native durable journal associates sparse membership with runtime save-completion evidence. |
| Delivery/consumer/ledger views | Read-only IPC projections of native state; these are not independent Python ledgers or manual apply controls. |
| Runtime notifications and loot | Ordered IPC events; persist the relevant client effect before acknowledging consumption. |
| Diagnostics | Bounded client histories exported on request; export before closing the client if crash investigation needs them. |

For exact filenames, schemas and persistence boundaries, use the
[sidecar and channel catalog](docs/development.md). For sequencing,
use [runtime state machines](docs/development.md) and
[saved receipts](docs/development.md).

## Durable files

Seed documents, installation configuration, native journals, randomizer-owned
save sidecars and client checkpoints remain on disk. Live snapshots and
diagnostic histories do not become durable just because their channel has a
JSON filename. Recovery-aware readers must preserve a checkpoint together with
its `.json.wal` where present; directly reading the base can return old state.
Use `bridge_support.files` and `bridge_support.checkpoint` rather than adding
independent readers. See [checkpoint storage](docs/bridge-checkpoints.md) for
cache invalidation and journal recovery, and [debug bundles](docs/debug-bundles.md)
for on-demand evidence capture and redaction.

Ordinary bridge work must not inspect, hash, parse or copy vanilla KH3 save-slot
files. Save/retry decisions use runtime evidence and randomizer-owned state.

## Inspection and verification

Run commands from the repository root:

```powershell
python -m worlds.kh3.BridgeStub '<bridge_dir>' status
python -m worlds.kh3.BridgeStub '<bridge_dir>' items
python -m worlds.kh3.external_bridge_cli --help
.\scripts\test-kh3.ps1 -PytestArgs @('tools/kh3/tests/test_pipe_transport.py', 'worlds/kh3/test/test_pipe_bridge.py', '-m', 'world or not world', '-q')
python -m pytest worlds/kh3/test/test_pipe_bridge.py -q -p no:cacheprovider
```

Manual and offline diagnostic backends remain available through the external
CLI. They do not replace the integrated live runtime. Do not synthesize applied
receipts or goal completion as a substitute for native evidence. The
[client recovery commands](docs/development.md) document
supported map and gameflow recovery operations.
