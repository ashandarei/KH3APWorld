# KH3 development

This checkout contains Archipelago 0.6.8, the KH3 world, the client, and seed
asset tools. The standalone KH3Runtime checkout owns the native DLL, C++ tests,
movie sources, generated headers, and native authoring tools. Keep one editable
copy of each source. Newly generated seeds are the supported target.

## Local configuration

Copy `.env.example` to `.env` at the checkout root. Environment variables take
precedence over that file; explicit command arguments take precedence over both.
`AP_ENV_FILE` selects another settings file. Values are literal, not executed.
Relative paths resolve against the checkout, not the current directory.

- `KH3_DIR`: the KH3 game runtime directory, containing `Binaries` and `Content`.
- `KH3_RUNTIME_DIR`: the native checkout; the default is the sibling `KH3Runtime`.
- `KH3_ENUMS_FILE`: optional local enum dump for passive inspection tools.
- `KH3_NATIVE_RANDOMIZATION_TEST_EXE`: optional override for the native parity
  harness executable; normally the native checkout's `build-tests/policies/randomization.exe`.

The game, engine headers, local credentials and `.env` are not project inputs
that should be committed. Use the client's detected EGS installation by default.
Launch through the client/storefront, never the game executable directly.

## Tests

From this checkout in PowerShell:

```powershell
python -m pip install -r tools/kh3/requirements-dev.txt
.\scripts\test-kh3.ps1

# With the native checkout and its documented C++ toolchain configured:
..\KH3Runtime\scripts\build-runtime-harness.ps1
.\scripts\test-kh3.ps1 -NativeIntegration
```

The default runs the KH3 world and tooling suites. Native source/authoring
contracts are opt-in and fail if the configured runtime checkout is missing.
To select tests, pass `-PytestArgs @('path/to/test.py', '-q')`. In a configured
Python environment, first set `AP_TEST_WORLDS=kh3` and
`SKIP_REQUIREMENTS_UPDATE=1`, then use `python -m pytest -p tools.kh3.pytest_plugin worlds/kh3/test
tools/kh3/tests -m 'world or not world'`; add
`--native-integration` for cross-project contracts. No game is launched by these
suites. Local game/compiler fixtures are not distributed and are skipped when
absent. Live gameplay validation remains a separate release task.

## Shared data and native builds

APWorld generators write JSON catalogs. Export these deliberately with
`python tools/kh3/export_dll_inputs.py`, or verify parity with `--check`.
KH3Runtime then generates its own headers using `scripts/generate_catalogs.py`.
A native build never imports this checkout.

From the native checkout, use `scripts/build.ps1 -EnableDiagnostics -Install`
for an authorized local diagnostic session with the game closed. Omit
`-EnableDiagnostics` for player builds. `scripts/test.ps1` runs native tests.
Use the native checkout's README for toolchain setup and packaging.
Rebuilding a DLL does not require rebuilding an APWorld.

## Portable client and optional host extensions

The APWorld uses the supported CommonContext authentication hook on stock
Archipelago 0.6.8. Run preservation happens before KH3 sends authentication;
connection errors do not require changes to CommonClient. Shared Archipelago
files remain at their pre-cleanup revisions; the KH3 test option lives in
`tools/kh3/pytest_plugin.py`.

This development checkout also provides optional server item-name aliases and
scoped world loading. Ordinary APWorld networking and generation do not require
those extensions. On other hosts use canonical item names, such as `Magic: Fire`,
in server commands. `tools/kh3/tests/test_host_extensions.py` covers the optional
host behavior separately from the world suite.

## Runtime ownership and recovery

[The bridge contract](../BRIDGE_PROTOCOL.md),
[transport reference](named-pipe-bridge.md) and
[checkpoint storage](bridge-checkpoints.md) define ownership, live channels and
persistence. [Debug bundles](debug-bundles.md) document support evidence and redaction.
The client transports AP facts; native services own grant safety, receipt
membership, retry reconciliation and goal observation. Import caches seed
metadata; activation derives native settings and hints in memory.

Use the client's recovery commands through
`client_support/recovery_commands.py`; inspect supported commands before use.
Ordinary work does not inspect vanilla save files or replay reward-bearing
actions as a repair.

## CI and release review

The KH3 workflow runs tests and focused lint checks. Upstream release/container
workflows are restricted to the upstream repository, so enabling Actions here
does not publish Archipelago artifacts. GitHub Actions must still be enabled in
repository settings before the workflow can run remotely.

Review local diffs before committing. APWorld builds, installation, publishing,
and release replacement are explicit steps; test runs do not perform them.
