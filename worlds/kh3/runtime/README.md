# KH3 Runtime Bundle

This directory is packaged into `kh3.apworld` and is used by the KH3 client
`Patch Game` button.

Patch Game downloads `UE4SS.dll` and `dwmapi.dll` from the
pinned GitHub release of `ashandarei/ue4ss-goa`. The APWorld excludes
these DLLs and Microsoft's `xinput1_3.dll`. The initial
release is UE4SS 3.0.4 from ue4ss-goa revision
`06dd053d3e537ec2b477ee76337661b15642d012`. Steam uses its built-in
GUObjectArray detection; the installer adds a signature override only for EGS.

Patch Game queries `/releases/tags/v3.0.4` and requires both named DLL assets
with GitHub SHA-256 digests matching the APWorld's pinned hashes, valid sizes,
and exact download URLs for that tag and filename. Matching installed hashes skip downloads.
All needed downloads are staged and verified before runtime installation begins;
download or verification failures leave the runtime untouched. Replacements use
the existing atomic-write and timestamped-backup mechanism. Status polling never
queries GitHub or hashes DLLs. Like GoA, the user-scoped
`kh3/cache/ue4ss-status.json` stores release metadata and verified hashes tied to
path, size, modification time, and change time. Patch Game refreshes the release
once per explicit action and reuses cached hashes while those signatures match.
If the release request fails, it uses the last cached release only if it still
matches this APWorld's tag and DLL pins. Changing a pin invalidates the cache. An
uncached first install needs internet; a matching cached installation works offline.

`Win64/ue4ss-release.json` pins the release tag and both `UE4SS.dll` and
`dwmapi.dll` hashes, matching `Win64/KH3AP-Licenses/manifest.json`.
Upgrade these pins deliberately alongside the license manifest after runtime testing.
The probe remains bundled and versioned with the APWorld.

XInput 1.3 is a separate platform prerequisite, not a UE4SS proxy:

- Windows uses the 64-bit system runtime installed by Microsoft's DirectX setup.
  Patch Game verifies an existing runtime's architecture and Microsoft signature.
  If missing, it downloads the official June 2010 redistributable from Microsoft,
  verifies its pinned SHA-256 and Microsoft signature, caches it under
  `kh3/cache/directx_Jun2010_redist.exe`, extracts the complete package into a
  temporary folder, and runs normal `DXSETUP.exe` with license and UAC prompts.
  It waits for success and verifies the system DLL before installing UE4SS/probe
  files. It never accepts Microsoft's terms silently or copies a loose system DLL.
  A first prerequisite download is 95.6 MB; a verified cached package works offline.
- Linux uses Wine/Proton's built-in XInput. Patch Game downloads or executes no
  Microsoft redistributable. It writes KH3-specific `xinput1_3=builtin` and
  `dwmapi=native,builtin` overrides in the selected prefix's `user.reg`, preserving
  other programs' settings and backing up the registry before changing it.
  Prefix discovery supports the selected Steam library (including primary-library
  compatdata) and matching native/Flatpak Heroic game configurations. Missing or
  ambiguous prefixes stop installation. Start KH3 once through its launcher, close
  it, then patch. Remove conflicting `WINEDLLOVERRIDES` from launcher settings.
  Changes to installer configuration have tests; Linux gameplay and controller
  compatibility still require verification on a real KH3 Wine/Proton installation.

After the prerequisite succeeds, a local XInput DLL matching the former AP
Microsoft runtime hash is moved to `xinput1_3.dll.apbak-<timestamp>`. Unknown
local DLLs are preserved: Windows stops because they would shadow the system
runtime; Linux's built-in override bypasses them. Older bundle files are never
copied back. Status inspection does no downloads, DLL hashing, or signature checks.

The runtime DLLs on `v3.0.4` are frozen. Required binary redistribution notices
are consolidated in `Win64/KH3AP-Licenses/THIRD_PARTY_NOTICES.txt`: one MIT grant
with the applicable copyright notices, Apache 2.0 for Roboto, SIL OFL 1.1 for
Font Awesome, and Unicode 3.0 for the Rust standard library's embedded data.
MIT is selected where upstream offers MIT OR Apache 2.0. Host-only parsers,
test/example licenses, other-platform standard-library dependencies and unused
license alternatives are omitted. Generated runtime code still retains its
applicable attribution. The checksum/provenance manifest is the only other file.

Patch Game copies this folder from the APWorld under `Binaries/Win64/` without
license downloads. The same files are published as `KH3AP-Licenses-v3.0.4.zip`
on the release. The manifest binds the notices to the frozen DLL hashes, and the
APWorld build checks the folder contents and each notice's hash. Microsoft's
XInput terms remain separate.

The player APWorld includes these runtime notices. The host-only APWorld excludes
the runtime DLLs and this folder; it retains the licenses for the generation
code and data it distributes.

GitHub source repositories and binary release assets have different requirements.
The APWorld and GitHub release ZIP contain the same binary notices. Source
repositories retain their own root licenses and the original licenses/headers
for source they redistribute. In particular, GLFW, AsmJit and AsmTK's zlib
notices must remain with their source distributions, while zlib does not require
a notice with binary-only distributions. Their source files and notices are
unchanged. STB's public-domain alternative, adler's 0BSD alternative, and
byteorder/memchr's Unlicense alternative impose no binary notice requirement;
these choices are recorded in the runtime manifest.

The ue4ss-goa source tree also needs font notices alongside its embedded font
headers: `Roboto.hpp` contains both Regular and Black, while `FaSolid900.hpp`
contains Font Awesome. GitHub's automatic source archives use the tagged tree;
uploading a separate release notice ZIP does not add notices to those archives.
The source font notices therefore belong in that source tree as well.

`-SyncUe4ss` synchronizes local loose UE4SS copies only; it does not publish a
release or change what players download. To release a reviewed runtime, upload
UE4SS, its dwmapi proxy, and license notices to a stable release on the ue4ss-goa repository.
Do not replace runtime DLL assets on an existing release; publish a new tag for
changed runtime bytes. Notice-only corrections can update the license ZIP while
leaving the frozen DLL assets untouched.

Expected install target:

- `Win64/*` -> `KINGDOM HEARTS III/Binaries/Win64/`
- `Mods/KH3APProbeCpp/dlls/main.dll` ->
  `KINGDOM HEARTS III/Binaries/Win64/Mods/KH3APProbeCpp/dlls/main.dll`

The client also creates/updates:

- `KINGDOM HEARTS III/Binaries/Win64/Mods/mods.txt`
- `KINGDOM HEARTS III/Binaries/Win64/Mods/KH3APProbeCpp/config.lua`

If an older `Mods/KH3APProbe` Lua probe directory exists, the installer moves
it aside with an `.apbak-<timestamp>` suffix so it cannot compete with the C++
runtime probe.

The installer forces every bundled UE4SS settings layout to use the root
`Binaries/Win64/Mods` directory and root `Mods/mods.txt`. If an older nested
`Binaries/Win64/KINGDOM HEARTS III/Mods/mods.txt` exists, the installer disables
`KH3APProbeCpp` there so only one probe DLL can be active.

Do not place local UE4SS logs, object dumps, crash dumps, or backup DLLs here.

## Garden of Assemblage pak compatibility

The client explicitly selects GoA release `2.0.0` and
`pakchunk99-GardenOfAssemblageRandomizer_99_P.pak` (27,536,128 bytes, SHA256
`c0bdf84c5e9101022b2ebff2cdcafc9fb6577dbc51b99a6d4d5d4a2a9129f0c4`).
These compatibility pins live in `client_support/goa_download.py`; they are
independent of the APWorld's display version. Do not infer pak compatibility
by searching for lower tags. Update the pins deliberately after testing a new pak.

The client verifies installed paks directly against these bundled pins, even
without a metadata cache or network access. The status cache stores only local
file hashes, bound to the current pins and each file's metadata. Status checks
never contact GitHub. Patch Game skips downloading an already matching pak;
otherwise it downloads the fixed release asset and verifies its size and hash
before replacement or removal of older GoA paks. Failed downloads leave existing
paks in place and log a warning.

GoA has no remote manifest, release-discovery request, expiry timer, or URL
override.
