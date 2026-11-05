# KH3 Randomizer Templates

The active seed generator is Python in `../seedgen/`. It edits the cooked
`Content/` templates, reads the `wwwroot/Default*.json` catalogs, and packages
the seed using `../Pak.py`. The client calls it in process, including inside a
frozen Archipelago client and an installed APWorld. It needs no .NET, NuGet,
external Python interpreter, native pak utility, or generation-time download.

The retired C# backend and loopback bridge have been removed. Seed generation
and installed-pak metadata reads use the bundled Python implementation.

Only templates consumed by the Python generator remain in `Content/`. Retired
enemy maps, enemy stat tables, boss/party tables, and GoA battle-level templates
are removed. Native encounter randomization uses the `wwwroot/Default*.json`
catalogs; EXP uses the native award hook. The Avalanche and Raging Vulture QoL
templates remain because the generator still edits them.

The AP option names and modes remain the same. Rewards are exported from the
current AP placements, with chests and native grants controlled by the existing
UE4SS DLL. Newly generated seeds are the supported target.

## Catalog overrides

`wwwroot/DefaultKH3.ArchipelagoOverrides.json` carries AP-specific catalog fixes
without editing the upstream `wwwroot/DefaultKH3.json` directly. Both
`../extract_data.py` and `../export_ap_kh3_randomized_options.py` recursively
merge it into the base catalog before extracting locations or exporting rewards.
Nested fields replace matching values; missing categories, rows and fields are
added. This is generator input, not a runtime configuration file.

`Vbonus_004` retains a dummy Potion reward in this layer. The extracted catalog
reserves its AP location ID, while `DISABLED_AP_VBONUS_IDS` in `extract_data.py`
excludes it from generated worlds.

## Native encounter generation

Enemy and boss mappings are generated once inside `KH3APProbeCpp` from the seed
and options. The APWorld exports the encounter
catalogs and shared compatibility policy through `tools/kh3/export_dll_inputs.py`.
The native project generates embedded tables from its own exported inputs; DLL
builds do not depend on the APWorld checkout. The Python seed
builder validates options and blacklists but does not generate or export remaps.

The DLL reads the stored `Content/Archipelago/runtime_seed_settings.json` record
directly from the installed seed pak, using the running executable's install
root. Enemy, boss, and party initialization do not read its loose bridge copy.
The reader validates V4 archive bounds, index/header/payload integrity, and the
settings schema; it rejects missing or multiple installed seed paks explicitly.
The settings record is limited to 1 MiB and is read once per game process. Seed paks must
be generated with this version; compressed records from previous generators are
not imported or migrated.

Party uses the same pak seed/options and its existing received-item unlocks.
Player menu preferences still persist in `goa_runtime_settings.json`; that is
player-owned state, not generated encounter data. Existing event-driven spawn
hooks, compatibility restrictions, and toggles remain in use.

Color randomization also reads its initial Off/Coordinated/Wild mode from the
pak's `slot_data.color_randomization`, preserving the YAML option. Character,
weapon, and Gummi colors share that native setting and pak seed identity.
Saved in-game color preferences override the initial mode. No loose color
settings export is required. Music shuffle mode, custom-music inclusion and
Form Themes also initialize from the pak, with saved menu choices taking precedence.

All fixed native gameplay options use the same cached pak settings: cutscene
and story/minigame shortcuts, Leviathan level, fun Heartless, Stylesanity,
Keyblade level mode, map keybind display, Melody of Memory defaults, shop/Ultima
options, minigame check modes, and space/portal/KH0.2 access requirements. These
remain YAML-controlled and require a newly generated pak after generation
options change.

The pak also stores `slot_data.json` (up to 32 MiB) and optional
`runtime_seed_hints.json` (up to 1 MiB) without compression. The DLL loads their
catalog references, assignment metadata, seed identity and report targets in
the same archive pass. The bundled base catalog is expanded once and shared
with native chest, event, VBonus, level, emblem, shop, minigame, space and
reconciliation readers. The game binds this source before constructing its
services; missing or conflicting loose seed exports cannot override it, and an
invalid pak never falls back to a loose catalog. Invalid optional hint targets
do not disable gameplay.

Generated reward bundles preserve Event/VBonus secondary rewards in the pak;
native repair no longer depends on `kh3-runtime-debug-manifest.jsonl`. Foreign
assignment metadata is retained exactly, while AP notification markers are
excluded from native reward bundles. Receipt/check transport, connection state,
progress journals and saved player preferences continue through the bridge.

The client keeps `slot_data.json` and `kh3_ap_seed_metadata.json` as its run
inventory/offline recovery caches. It no longer exports duplicate loose
`runtime_seed_settings.json` or `runtime_seed_hints.json`; their runtime copies
belong in the pak. Native VBonus presentation parses structured reward bundles
directly, with no JSONL debug-manifest reader or filename setting.

See [music development](../../../tools/kh3/MUSIC_DEVELOPMENT.md) and
[Now Playing](../../../tools/kh3/now_playing/README.md) for live music tests and
startup-only custom fixtures. Report skipped playback checks explicitly.

Build `KH3Runtime/scripts/build-runtime-harness.ps1`, then run
`.\scripts\test-kh3.ps1 -NativeIntegration -PytestArgs @('worlds/kh3/test/test_native_randomization.py', '-q')` for native/Python parity,
determinism, blacklist, missing-loose-file, and archive corruption tests.

## Custom item icons

KH3 item rows reference cooked Unreal texture objects; the game does not load a
source PNG or TGA directly from a pak. The seed-independent textures are supplied
by the version-matched static GoA pak. The seed generator only sets each row's
`m_IconAsset` object path; it does not duplicate static assets in generated
seed paks.

`Munny Pouch` and `Large Munny Pouch` are wholly custom filler items.
`KEY_ITEM18` and `KEY_ITEM19` both use the cooked
`/Game/UI/Icon/Item/ap_munny_pouch.ap_munny_pouch` texture. Receiving them
through the runtime bridge shows the matching key-item presentation and then
grants 100 or 1,000 munny, respectively.

Moogle Shop AP rows use three cooked textures based on their AP classification:
`/Game/UI/Icon/Item/ap_archipelago.ap_archipelago` for filler/other items,
`/Game/UI/Icon/Item/ap_archipelago_useful.ap_archipelago_useful` for useful
items, and
`/Game/UI/Icon/Item/ap_archipelago_progression.ap_archipelago_progression` for
progression items. Progression takes precedence for compound classifications.
When a shop placement's `item_game` identifies Kingdom Hearts, Kingdom Hearts
2, Birth by Sleep, Chain of Memories/Re:Chain of Memories, or Dream Drop
Distance, the generated row uses that game's dedicated texture instead of the
generic classification texture. Classification still controls the row-name
color. CoM and Re:CoM intentionally share one icon family.
The three classification textures are cooked as uncompressed UI RGBA with sRGB
disabled and no mipmaps
so Scaleform preserves their authored color and avoids block-compression
artifacts.

In `shards` mode, each Ultima Weapon Shard is granted through KH3's normal
material-item path so the acquired count is visible in the inventory. KH3's
compiled item enum prevents a generated DataTable row from creating new
persistent storage, so the seed repurposes the otherwise excluded Orichalcum+
slot as an unsellable shard material. Only that shard material uses the custom
high-resolution icon; Ultima Weapon keeps its vanilla row and icon unchanged.

The shop's right-side preview reads the row texture directly, while the Buy
list normally renders a separate category-font glyph. The DLL extends the
vanilla `UI/Scaleform/41_shop/Shop` movie in memory with our original helper and
two constructor calls. It supplies AP row textures/colors and hides Forge,
while original game code and graphics come from the player's installation.
GoA removes the former cooked Shop override; custom icon textures remain in
GoA. Authoring sources and rebuild notes live under `../../../tools/kh3/shop_ui/`.

## Developer CLI

From the repository root, run the same Python implementation as the client:

```powershell
python tools/kh3/randomizer.py exact --config config.json --randomized-options rewards.json --archipelago-metadata metadata.json --randomizer-root worlds/kh3/randomizer --seed AP_12345-1-Sora --output build/kh3-python
```

The inputs are the config, exact reward mapping and AP metadata emitted by
`PakGenerator.py`. The generator requires current AP placements and matching
seed/player metadata. It does not perform a separate standalone item shuffle.

For a repeatable Python-only stress test with fresh Archipelago placements:

```powershell
python tools/kh3/stress_python_seedgen.py --count 1000 --workers 4 --output build/kh3-python-stress-1000
```

Use an empty output directory. The test varies supported settings, exercises both
AP zip and player-container generation, checks every pak entry and all 19 edited
assets, and repeats a sample for byte-identical output. It blocks subprocesses
and downloads inside generation workers. Paks, input YAMLs, logs, hash manifests
and reports are retained. These are offline checks; they do not establish
in-game compatibility or install generated paks.

The Coliseum companion uses the same Python asset editor and pak implementation:

```powershell
python tools/kh3/randomizer.py olympus-coliseum-compat --source-pak zzzy_ColiseumMod_998_P.pak --output-pak zzzz_APOlympusColiseumCompat_999_P.pak
```

Use `--preserve-cup-rewards` to suppress only the chests. The supported source
hashes and asset layouts are validated before writing an overlay. The client manages compatibility installation and backups during Patch Game.

## Packaging

`seedgen/generator.py` edits the UE4.17 cooked templates in process. It preserves
unmodified properties, opaque exports and asset tails, then updates name maps,
export sizes and file offsets. Seeded randomization, hints, localization and
native runtime metadata use the same AP option formats as before.

`Pak.py` writes unencrypted V4 Zlib seed paks using Python's standard library.
Every packed entry is read back and compared with its staged bytes before the
output is replaced. The reader also handles the supported Coliseum mod's stored
and Zlib entries; unsupported pak versions and codecs fail explicitly.

No loopback bridge, SDK bootstrap, NuGet restore or native packer is used by the
active generation flow. Third-party format references and licenses are documented
in `../seedgen/THIRD_PARTY_NOTICES.txt`.
