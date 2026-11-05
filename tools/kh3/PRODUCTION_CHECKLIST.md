# KH3 release validation checklist

Use this checklist when preparing an explicitly requested release. It records
validation to perform; it does not assert that a particular build passed. The
current implementation is described by the [client architecture](../../worlds/kh3/docs/development.md),
[bridge contract](../../worlds/kh3/BRIDGE_PROTOCOL.md) and
[runtime bundle](../../worlds/kh3/runtime/README.md).

## Generation and compatibility

- Validate newly generated single-player KH3, mixed-game, and multiple-KH3 seeds.
  Check relevant option combinations, reachability, starting inventory, junk
  restrictions, race generation and item classifications.
- Keep `archipelago.json` versioned. Update the compact `location_catalog` and
  bundled native catalog together when their contract changes; reject mismatched
  catalogs instead of importing legacy seeds.
- Build seed paks with the bundled Python generator. Check the selected seed and
  slot, source identities, native reward tokens and runtime settings. Generation
  needs no .NET, NuGet, external pak tool or remote generator.
- Race outputs must omit protected placement and remote-hint data. Do not require
  full placement/debug manifests in every player artifact.

## Client and native runtime

- Python owns AP networking and normalized receipt/check facts. The DLL owns
  native checks, reward decoding, grant groups, saved receipt membership, retry
  reconciliation and DeathLink. Validate those boundaries through the live IPC
  contract; do not restore Python-owned grant ledgers or file IPC fallbacks.
- Verify chest, event/VBonus, emblem, ingredient, minigame, Workshop and enabled
  optional check sources. Confirm real gameplay evidence, AP acknowledgements,
  duplicate suppression and actual goal activation.
- Verify incoming rewards, deferred Sora-only grants, shared space inventory
  grants, reconnect, offline checks, Retry/Continue, save/load replay and cold
  recovery. Historical apply counts alone do not prove membership in a save.
- Test EGS by default. Test Steam only when that platform is in the requested
  validation scope, using its nested runtime root. Report untested storefront
  behavior explicitly.

## Installation and packaging

- Exercise the packaged client from the Archipelago Launcher and follow the
  [player setup guide](../../worlds/kh3/docs/setup_en.md). Validate detected game,
  SaveGames, mod and bridge directories, patching, normal storefront launch,
  connection and seed selection.
- Verify the pinned GoA asset locally. Already matching paks must not cause a
  metadata request or download. Missing/mismatched paks download only the bundled
  release URL and must pass size/SHA-256 checks before replacement. Preserve
  existing paks on failure.
- UE4SS and its proxy are downloaded from the configured pinned GitHub release;
  they are not bundled in the player APWorld. Validate the configured hashes and
  include the required notices. The bundled probe DLL must match the reviewed
  source/build metadata.
- Build an APWorld only when explicitly requested. Use
  `scripts/build-workspace-apworld.ps1` to build the standalone player DLL with
  diagnostics off, verify/export its release inputs, and package the APWorld.
  This command does not install a game DLL or APWorld. Use the client/runtime
  installation instructions for a separately requested local install.
- A diagnostics-on DLL must never be packaged; `-SkipBuild` cannot change its
  recorded mode. Inspect the archive's version, notices, and runtime hashes.

## Evidence

Run the relevant Python and native suites and retain their results with the
source revision, executable profile, seed/slot, build hashes and test scope.
Keep diagnostic automation in diagnostics-enabled local builds. Export bounded
client histories before closing the client when investigating a crash or replay
failure. Distinguish unit/profile checks from live gameplay results and measured
hitches from unmeasured performance claims.

Use runtime evidence and randomizer-owned sidecars; ordinary release testing
must not inspect vanilla KH3 slot/system files. Package hashes and dated test
reports describe their recorded builds and do not certify a later release.
