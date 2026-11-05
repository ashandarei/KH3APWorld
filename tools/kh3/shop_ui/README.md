# Original Archipelago shop extension

For editable movie source and a plain-language variable reference, start with
[Readable KH3 movie sources](../MOVIE_SOURCES.md).

The shipped shop implementation contains our standalone `kh3ap.APShopRuntime`
helper and two small constructor hooks. It contains no copied KH3 class or
method implementation. The game supplies all original shop code and graphics
at runtime. The old replacement `CoTradeListItem` and Forge menu sources have
been removed.

The DLL asynchronously preloads the vanilla `/Game/UI/Scaleform/41_shop/Shop`
movie once Sora is available. Separate size/fingerprint guards verify the exact
EGS and Steam stock movies. It then inserts our independently compiled DoABC helper, appends our
identifiers to the existing constant pools, and inserts one eight-byte call
before the final return in each of these constructors:

- `CoTradeListItem`: calls `APShopRuntime.attachRow(this)` after the game's
  constructor has configured its timeline callbacks.
- `Shop_fla.McMenuMixTopObj_122`: calls `APShopRuntime.attachForge(this)` after
  the game's original command setup has completed.

No existing pool entries or instruction operands are reindexed. All 937
original method bodies remain byte for byte, apart from the appended calls in
the two constructors. Container sizes and the Forge constructor's maximum stack
are updated. Constructors with branches or exception handlers are rejected by
the authoring tool. The runtime decoder builds into separate storage and checks
the resulting fingerprint before replacing `RawData`, then roots the asset for
the lifetime of the process. Unknown movies, including obsolete cooked shop
overrides, are left untouched and logged.

The generated `.bin` resource contains sequential copy ranges into the player's
movie and explicit literals. Every literal is authored helper bytecode, our hook
instructions, our new identifiers, or encoded counts/sizes. It is not generated
by comparing recompiled vanilla classes. `audit.json` in the ignored build
directory records every edit and its provenance. The compiler receives only our
original blank movie and `APShopRuntime.as`, never a KH3 movie or decompiled class.

`KH3APProbeCpp/resources/shop_movie_patch_egs.bin` and
`shop_movie_patch_steam.bin` are embedded as uncompressed Windows RCDATA, using
the same resource loader as the map movie and journal. The generated C++ headers
contain metadata and cached `delta_bytes()` accessors. There are no payload hex
arrays in C++. The original/input and patched/output fingerprint checks are
unchanged; missing, incorrectly sized or corrupt resources leave the original
movie intact. The resource view is cached after its first use, with no external
file reads or per-frame resource lookups.

GoA must not contain the cooked Shop asset; the manifest removes it during patch
builds. Our custom icon textures remain in GoA. No GFX fixture or full modified
movie is committed or shipped, and normal DLL iteration does not rebuild the
APWorld.

## UI behavior

The helper listens to each Buy row's frame event and keeps its own state on that
row. It reads the classification marker from the original name wrapper and
strips it only from the nested rendered text field. Keeping the wrapper's
marker makes recycled rows retain their AP identity. Useful names are light
blue `#8FD8FF`, progression names light yellow `#FFF0A0`, and filler/vanilla names
restore the original text color.

| Marker | Meaning / icon |
| --- | --- |
| U+2063 | Filler/other: `ap_archipelago` |
| U+2062 | Useful: `ap_archipelago_useful` |
| U+2064 | Progression: `ap_archipelago_progression` |
| U+2065 (second) | KH1: `ap_kh1` |
| U+2066 (second) | KH2: `ap_kh2` |
| U+2067 (second) | Birth by Sleep: `ap_kh_bbs` |
| U+2068 (second) | Chain of Memories: `ap_kh_com` |
| U+2069 (second) | Dream Drop Distance: `ap_kh_ddd` |

`Ultima Weapon Shard` uses the dedicated `ap_ultima_weapon_shop_row` texture.
The helper resolves the game's `tres.controls.Icon` class by name at runtime,
creates a 32x32 icon at `(-40, 0)`, and uses its normal texture loader. A 2.0
alpha multiplier compensates for the original list's 50% unfocused-row shading.
Each row caches one icon per texture path, with one load request per icon, so
asynchronous completions cannot overwrite a recycled row's current icon.
Vanilla category glyphs return when a row no longer represents an AP item.
The large right-side preview continues to use the item's seed-owned table row.

The Forge hook sets only `cmd2.enabled=false`, `cmd2.visible=false`,
`cmd1.toDown=33`, and `cmd3.toUp=31`. Original setup, other commands and frame
scripts continue to execute from the player's movie.

## Rebuild and verify

Extract the unmodified base game's Shop `RawData` into an ignored local build
directory. Do not resave it through JPEXS; resaving can alter framing bytes and
invalidate the exact-version guard. Provide local Java and JPEXS CLI paths:

```powershell
python ../KH3Runtime/authoring/kh3/shop_ui/build_runtime_patch.py build/shop-runtime/original.gfx --java tmp/kh3-workshop-ui/jre21/jdk-21.0.12+8-jre/bin/java.exe --ffdec tmp/kh3-workshop-ui/ffdec/ffdec-cli.jar
python ../KH3Runtime/authoring/kh3/movie_resources.py --check
.\scripts\test-kh3.ps1 -NativeIntegration -PytestArgs @('tools/kh3/tests/test_shop_runtime_extension.py', '-q')
..\KH3Runtime\scripts\test.ps1 -Suite policies
..\KH3Runtime\build-tests\policies\test_kh3_shop_movie_patch.exe build/shop-runtime/original.gfx build/shop-runtime-extension/extended.gfx
```

Steam uses a distinct stock movie (161539 bytes, FNV-1a
`a194a7511d837f33`); EGS uses 161537 bytes (`72d4457958eb7b8b`). The original
EGS-only guard left Steam's movie untouched, so its AP row helper never ran.
`shop_movie_patch_variants.h` now selects the verified patch by movie contents,
and still rejects unknown/modified movies. Generate the Steam header with the
same compiler command, adding `--namespace kh3ap::shop_movie_patch_steam`,
`--work-dir build/shop-runtime-steam/extension` and
`--output ../KH3Runtime/KH3APProbeCpp/kh3ap/features/moogle_shop/shop_movie_patch_steam_data.h`.
The namespace also selects the matching binary resource output. Check in both
the metadata header and `.bin` resource after generation. `--resource-output`
can redirect a local fixture build. Normal DLL configuration validates source
and resource hashes without running the ActionScript compiler.

The generator verifies every original method and pool entry, plus all non-ABC
movie tags. Tests exercise constructor control-flow rejection, preservation,
corruption detection, and the compiled payload's single-class provenance. The
production C++ decoder checks truncation/corruption, unknown input, overflow and
exact output against the locally generated fixture.

For live verification, use newly generated AP shop rows and confirm icons,
colors, Forge behavior and the right-side preview on each supported storefront.
Compare loaded movie bytes with the locally generated fixture when investigating
a patch mismatch. Unit decoder success does not establish visible rendering or
menu interaction; record those observations separately.
