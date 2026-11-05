# Field ingredient checks

Ingredientsanity tracks unique ingredients; Ingredientsanity+ tracks individual
field pickups. Both are optional. Use a newly generated seed for either mode.

The **Ingredients** choice uses this YAML setting:

```yaml
ingredients: off                 # no ingredient checks (default)
# ingredients: ingredientsanity  # 51 ingredient-type checks
# ingredients: ingredientsanity_plus  # 298 pickup-location checks
```

The display choices are **Off**, **Ingredientsanity**, and **Ingredientsanity+**.
`ingredients: ingredientsanity+` is also accepted.

Normal mode adds 51 locations named `Ingredients - <name>`; Honey and the seven
Flan fruits are excluded. Plus replaces those checks with 298 catalogued field
pickup spots. Tutorial pickups are excluded and spawn chances remain vanilla,
as requested. Existing IDs remain unchanged: Plus IDs follow the 59 reserved
ingredient-type IDs. Neither mode alters the native ingredient reward.

The client displays Ingredientsanity+ spots with world, friendly map name,
pickup number, and possible vanilla ingredient drops. These descriptions appear
in AP location messages, bridge item metadata, and Debug's Last Ingredient Spot.
Drop names come from the reviewed ingredient journal's exact actor sources;
multiple names describe possible drops, not guaranteed rewards. The two spots
without journal drop records display `Drop unknown`. Map labels describe the
broad map, rather than an exact subarea or directions to the pickup. Public AP
location names and pickup IDs retain their existing names and numbering.

A check records the first successful field acquisition during this seed.
Purchases and AP grants do not count. Previously purchased stock does not
prevent a later field acquisition from counting. The ingredient remains in
inventory for cooking, alongside the separately delivered AP reward.

## Runtime design

`RuntimeIngredientService` wraps the native thunks for
`BP_FoodstuffGetTeamPrize`, `BP_GetFoodstuffPrizeToTeam`, and
`TresPrizeGenComponent:BP_SpawnFoodstuffPrize`. These field-prize
entrypoints exclude shop purchases. The original function receives its
unchanged context, Blueprint stack, and result storage. Narrow native bindings
are necessary because UE4SS RegisterHook missed the Blueprint VM plant call
in the tested EGS build. Reflection layout checks guard installation.

Physical container food is awarded later by native `TresPrize` collection,
not inside the food-release thunk or `BP_TresGetPrize`. The service also wraps
that collection routine (EGS RVA `0x17aad90`, Steam RVA `0x1479360`), preserving
its arguments and result. The supported PE timestamp, image size and function
prologue must match. Only the native item-box branch (`m_Kind == 15`) snapshots
ingredient stock; ordinary munny/HP/MP prizes require no inventory reads.
Positive stock changes count only after the original collection completes.
The caller still handles destruction and all vanilla effects. Steam's matching
routine was inspected statically; the live sweep uses EGS.

The handler reads the 51 ingredient counts immediately before and after the
field-prize event using Sora and `BP_TresGetBackyardItemNum`. Positive deltas
are evidence of acquisition; failed reads, consumption, unchanged stock, and
AP-internal grant context do not qualify. There is no inventory timer or
gameplay polling loop. At maximum stock, a pickup that does not increase the
count cannot count: use an ingredient first.

For Plus, the same event captures the originating actor's exact cooked map
path before the vanilla grant. The native gimmick context or its Blueprint
call frame supplies that identity; no periodic actor scan is used. Only
catalogued paths qualify. Separate plants giving the same ingredient are
different checks; repeated collections and nested prize callbacks deduplicate
by the stable spot key. Olympus map variants share 24 explicit key aliases.
Grouped oyster actors count as one pickup spot per actor, not per shell.
Breakable food points count when the game's native food-loot release event
runs for their catalogued actor (harvesting/breaking the source); the scattered
ingredients can then be collected normally. This does not infer completion
from arbitrary attack input or generic prize spawning. Ingredient-type mode
still requires a positive inventory increase at its direct field-prize event.

Unique acquisitions are persisted atomically in the randomizer-owned
`runtime_ingredient_state.json` (`acquired_ingredients` and `acquired_spots`),
scoped by seed and player slot. Reloading or
consuming an ingredient does not clear this ledger. The client's existing
bridge transport reads that evidence; it never infers acquisition from an
inventory snapshot. Disabled options and locations absent from the seed are
ignored. Diagnostics can collect evidence on an old seed, but cannot add
locations to that seed's AP server.

Checkpoint writes run on a dedicated, event-driven worker. The game thread
submits a value-only snapshot; the worker writes and reads back a temporary
file, replaces the checkpoint atomically, then verifies the final bytes.
Queued snapshots for the same destination coalesce to the latest full history.
Different seed destinations remain separate. Idle workers wait for a signal;
only an unfinished failed write uses a bounded retry delay.

The game thread publishes committed snapshots through the bridge after checking
their seed scope and destination. Failed commits do not publish progress.
Normal shutdown drains accepted writes before stopping the bridge transport,
with a synchronous fallback for the current uncommitted state. Abrupt process
termination can interrupt a queued write; this is not a crash-proof transaction
with KH3's own save system. The ledger format, acquisition rules and stock
snapshots are unchanged.

### Shared check dependencies

Access now uses the existing `LOCATION_LOGIC_DEPENDENCIES` mechanism in
`LogicData.py`, replacing the initial blanket world-completion tool gates.
`data/ingredient_logic_dependencies.json` holds editable source references:

* Each of the 298 Plus spots uses `require_all_locations` for its single
  reference chest, or `require_any_location` for alternative actor aliases.
* Each of the 51 ingredient types uses `require_any_location` over its
  catalogued field pickup sources. It does not require collecting all sources.
* For example, `IngredientSpot:MI_01_PF_001` relies on
  `TreasureMI:MI_SBOX_002` (Laugh Floor entrance chest), currently reachable
  with Happy Gear and no additional tools.

These dependencies mean logically reachable, not already collected. The
referenced check's own region, item requirements and nested dependencies are
used directly. Disabled prerequisite checks remain private logic references
without AP addresses, reward slots or bridge entries; they still contribute
their required items to generation. This also supports Ingredientsanity when
the Plus mode itself is disabled.

The initial anchor choices are approximate nearest-chest references from
authored EGS coordinates, accepted as a starting point. They are not verified
physical routes. Review and change the reference source keys as routes are
confirmed; future edits to a referenced chest's logic propagate automatically.

## Validation

Use a new seed with the selected ingredient mode and an installed diagnostic
runtime. Confirm a field pickup changes native inventory or the qualifying
container-harvest record, produces its stable ledger entry, and sends the
corresponding AP check only once. Repeat the pickup, reload, and reconnect to
verify deduplication. Purchases and incoming AP grants must not create checks.

For Ingredientsanity+, verify the exact pickup ID as well as the friendly drop
name. Test two separate plants with the same ingredient and a container source.
Unreliable spawn locations must retain their configured junk restrictions.

Alternate characters use their owned read-only acquisition context. Keep Sora's
reward-grant context separate. Check Aqua's nearby plant interaction, container
drops, and repeated depleted-plant input. A seed with ingredients disabled can
validate native collection, but cannot prove server reporting.
