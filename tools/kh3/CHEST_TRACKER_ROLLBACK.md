# Treasure tracker reconciliation

A persisted randomizer check can remain complete after the vanilla treasure
record rolls back. Replaying the reward-bearing chest handler would risk
regranting items. The native persistence service instead restores presentation
and, when needed, the vanilla completion bit through verified native functions.

For a recorded chest with a valid initialized treasure index, read the native
completion getter, call the matching setter only when false, and verify readback.
Use fast UObject validation and weak handles. Unsupported binary signatures fail
closed; never replace this with raw bitset writes.

Reconciliation is independent of the actor's presentation tag. The service
uses the cached randomizer `OpenedChests` data, skips loading/opening actors,
and repairs eligible chests as they load. It does not prepopulate unloaded maps.
The diagnostic operation and normal service share the same native implementation.

Native source lives in KH3Runtime's `KH3APProbeCpp/kh3ap/features/chests`.
Use its current storefront profiles rather than copying old executable offsets.
For regression testing, verify a recorded loaded chest returns to obtained state,
neighboring unopened chests remain unchanged, and AP checks/rewards are not sent
again. Save/retry proof must come from native runtime observations, not direct
inspection or modification of vanilla save files.
