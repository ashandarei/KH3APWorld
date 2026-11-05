# KH3 native area map overlay

The DLL provides a large area map and collectible icons on the native minimap.
Use the [native implementation guide](../../../../KH3Runtime/KH3APProbeCpp/kh3ap/features/ui/map_overlay/README.md)
for discovery, projection and cache contracts, and [UI movie authoring](../MOVIE_SOURCES.md)
for editable sources and resource generation. The feature works in player builds.

## Controls and options

| Control | Action |
| --- | --- |
| Hold L3 for 0.25 seconds | Open the large map; tap L3 to close. |
| F8 | Toggle immediately during gameplay. |
| L2 / R2 release | Zoom out/in by 25%, within 50-200%; remembered this session. |
| L2+R2 together | Toggle Hide Locked Chest Icons on both maps, once until both release; does not zoom. |

Gameplay continues while the map is open. Menus and cutscenes dismiss the map,
legend, battle-level display and custom minimap icons. Closing the map cancels
pending trigger zoom. Outside it, triggers retain normal gameplay behavior.

**AP Options > Map options** saves these settings per seed; no YAML option is needed:

- **Map Overlay Shortcut** enables L3/F8 and defaults ON. Disabling immediately
  closes the map; disabled presses pass through after any owned key cycle ends.
- **Chests**, **Lucky Emblems**, **Ingredients**, **Minimap Icons** and
  **Large Map Icons** default ON. Both maps have independent layer visibility.
- **Hide Locked Chest Icons** defaults OFF: unowned-Keyblade icons are 70% opaque.
  ON hides them; owned-Keyblade icons remain fully opaque.

The map fits up to 90% of viewport height. Zoom follows Sora, clamps at texture
edges and clips map/markers together. Chest icons scale from 75% to 150% across
the map zoom range; minimap icons and legend retain their size. Height chevrons
are large-map only. The battle level appears at the frame's top-left and is
independent of collectible filters. The large map never hides enabled minimap icons.

## Markers

Markers are observations, not collection actions. They do not open chests,
photograph emblems, grant rewards or establish that a location is reachable.
Only the current loaded area is shown; overlapping floors can share XY bounds.
The map does not stitch a world atlas or reveal unloaded areas.

| Marker | Visibility and limits |
| --- | --- |
| Chests | Up to 128 loaded, unopened boxes. Opened/opening, fake, unknown-state and out-of-bounds boxes are omitted. |
| Lucky Emblems | Up to 90 valid loaded detectors, deduplicated by ID. Recorded/unknown marks are omitted; a detector is not a recommended camera vantage point. |
| Ingredients | Up to 128 visible loaded actors from the 298-spot catalog, including food containers. The 52 spots with authored spawn rate below 100 are omitted even if spawned; all 51 field types retain a catalogued guaranteed source. |
| Save points | Up to 64 visible native minimap symbols mirrored on the large map, following vanilla availability. |

Chests, emblems and ingredients use native texture scale/origin and bounds.
Projection must agree with Sora's native marker. Chevrons indicate a height
difference above 600 game units. Hidden/invalid/unloaded actors are omitted.
Ingredient aliases share a spot identity; collected spots remain hidden after
respawn and restart. First-pickup history is recorded even with Ingredientsanity
off, without enabling AP checks. The spawn-rate filter affects markers only.

### Chestsanity

Both maps tint chest icons by assigned Keyblade. Melody and unassigned chests
retain the original gold icon. Uncertain Keyblade ownership stays dim/hidden
according to the setting until a successful refresh.

The large-map legend includes eligible owned and unowned door Keyblades. Keyblade
doors use Classic Tone, Grand Chef and Hunny Spout; character doors use the twelve
normal door Keyblades. Pandora's Power and Ultima Weapon are excluded. Unowned
legend entries are 35% opaque. It follows Chests/Large Map Icons settings and
never appears on the minimap. Ownership changes do not resize the map or legend.

**Hide Physical Locked Chests** is separate and defaults OFF. It hides physical
boxes and suspends their reactors; revealing restores prior state without
reactivating opened/inactive boxes. Visible boxes still require their Keyblade.
Map tints and physical body/trim palettes are independent.

## Build and local testing

Run from the APWorld checkout:

```powershell
.\scripts\test-kh3.ps1 -NativeIntegration -PytestArgs @('tools/kh3/tests/test_map_overlay_icons.py', '-q')
```

The native `map_overlay_test` covers projection, rotation, clipping, invalid
transforms, visibility defaults, complete input cycles, zoom/focus bounds and
legend placement. Movie tests cover unique names/depths, initial transparency
and vector artwork. Authoring commands and dependencies live in the
[runtime movie guide](../../../../KH3Runtime/docs/MOVIE_SOURCES.md#generate-and-verify).

For DLL iteration, check whether KH3 is
running before choosing `-Install`; use diagnostics for automation and restart
after installation. No APWorld rebuild or separate overlay pak is required.

For live regression:

- Check L3 and F8 separately, trigger zoom/chords, centering, stock keyblade
  orientation, camp/cutscene dismissal and viewport/ultrawide layouts.
- Check both maps on mainland and island/sea textures, including marker/native
  alignment, height chevrons, Chestsanity colors and locked-icon settings.
- Open a reachable chest and photograph an emblem through normal gameplay;
  markers must disappear without duplicate rewards. Check ingredient completion
  with Ingredientsanity off as well as on.
- Close/reopen, travel and restart to verify cached rendering and durable
  completion. Distinguish a closed large map from both collectible layers hidden:
  enabled minimap icons still update. Measure cold and cached openings separately.

Service timing alone does not measure GPU frame time or input latency. Rebuild
with diagnostics off before separately authorized player packaging.
