# KH3 save and replay rules

The DLL owns save-linked item reconciliation. Python publishes AP receipt facts
and consumes native results; it does not reconstruct replay from a single
highest received-item index. The detailed persistence contract is in
[saved receipts](docs/development.md), and the transition model is in
[runtime state machines](docs/development.md).

## Saved membership

Each accepted save boundary records the sparse set of complete receipt groups
known to be represented by that save. A later receipt may be saved while an
earlier Sora-only receipt remains deferred. Replaying every index above a
single watermark would mishandle that case. Normal delivery, save loading,
Retry/Continue, zone-entry baselines and DeathLink preserve sparse membership.

Historical application ledgers are diagnostic views. An item applied in an
older session is not proof that a newly loaded save contains it. Group
membership becomes durable only after all actions in the group succeed.

## Runtime save evidence

Manual-save completion proves the numbered slot was written; it does not prove
slot 0 was serialized. Native automatic-save requests and completed slot-0
writes provide AUTO evidence. Map entry, an updated UI, or a recent-looking
file timestamp does not establish that evidence.

Loading, retrying and continuing select the appropriate native save/checkpoint
membership. Pending items remain pending while the owning context is unsafe.
The reconciliation journal provides cold recovery without requiring the client
to reconstruct it from trace files.

## Grant context

Ordinary Sora grants use the current playable Sora pawn as the world context.
Do not reuse stale pawns or substitute a GameInstance, UWorld, viewport or
arbitrary UObject. Shared inventory, Munny and Ultima shards may use the verified
current space controller only through the native space safety gate. Abilities,
magic, party unlocks, command unlocks, traps and DeathLink wait for playable Sora.

Use existing readiness and possession events. Any pending retry work must stop
when finished or cancelled; do not add an idle discovery timer.

## Investigation

Inspect the native receipt and retry projections through the registered live
bridge. Export the client's diagnostic bundle before closing it when event
history is needed. For authorized test saves, use native save operations and
verify their completion through runtime events.

Do not poll, stat, hash, diff, parse or copy vanilla KH3 slot/system files for
ordinary replay debugging. Use runtime save-completion events and
randomizer-owned journals and sidecars. Dated replay investigations are evidence
for their recorded builds, not an alternative implementation contract.
