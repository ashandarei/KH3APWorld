# Native Linux MoM lifecycle (experimental)

## Automatic setup

In the native Linux KH3 client, select the correct KH3 installation in
**KH3 Config**, use the detect button beside **MoM Dir**, then click the
**Install MoM Mod** button. The client detects Linux and configures launching
as part of the installation. If more than one MoM folder is registered, use
the `...` button to select one. Setup reads Heroic/Legendary metadata,
reuses the configured runner/prefix, validates Legendary's JSON launch recipe
without starting a game, and installs/repairs the existing MoM integration.

Setup saves its launch recipe in `$XDG_CONFIG_HOME/kh3ap/mom-linux.json`
(default `~/.config/kh3ap/mom-linux.json`) and a matching connection configuration
in KH3's `Binaries/Win64/kh3ap_mom_activity.json`. No environment-variable editing,
port selection, launcher-file edits, or wrapper script is needed. Restart KH3
once after first setup so its probe reads the new configuration. The connection
checks a per-setup greeting before launching MoM to detect a mismatched listener.
The automatically selected port can still be occupied by another application
later; listener failure is logged and connection failure prevents launching.

Supported automatic recipes: native or Flatpak Heroic with a configured Wine runner,
Proton through an installed UMU launcher, or traditional Proton with UMU
explicitly disabled and an existing Steam runtime. UMU discovery follows Heroic:
`umu-run` on PATH, then `heroic/tools/runtimes/umu/umu_run.py`. Setup keeps the
selected Proton directory and prefix, uses Heroic's cached game-specific UMU ID
when present, and requests `waitforexitandrun` so game exit remains observable.
If UMU is missing, launch MoM once through Heroic to prepare its runtime, close
MoM, then retry **Install MoM Mod**. Setup itself does not download UMU.
Flatpak launches use its bundled Legendary and a foreground runner inside the
Heroic sandbox, with per-launch access to the game and prefix. GameMode is
preserved when enabled in Flatpak Heroic. No persistent Flatpak overrides are
added. Run the KH3 client outside Flatpak. Launch MoM once through Heroic first
if its selected prefix has not been initialized.
Custom wrappers/pre/post scripts and unsupported settings retain
the manual fallback below. Discovery reports these cases instead of silently
discarding their launch requirements. For native Heroic, Legendary must be available on PATH or
in a recognized native Heroic installation. No tools are installed automatically.

Run setup again after changing runner/prefix settings. Discovery runs only on
Auto-detect/setup; fresh authenticated launch arguments are obtained from
Legendary on each chest/dome launch. Authentication output is neither saved nor
included in errors. The launch command itself is never a detached Heroic URI.
Setup does not launch either game. The manual Test Launch UI was removed.

Sources: [Heroic per-game configuration](https://github.com/Heroic-Games-Launcher/HeroicGamesLauncher/blob/main/src/backend/game_config.ts),
[Heroic Legendary paths](https://github.com/Heroic-Games-Launcher/HeroicGamesLauncher/blob/main/src/backend/storeManagers/legendary/constants.ts),
[Heroic runner selection](https://github.com/Heroic-Games-Launcher/HeroicGamesLauncher/blob/main/src/backend/utils/compatibility_layers.ts),
[UMU launch environment](https://github.com/Open-Wine-Components/umu-launcher/blob/main/docs/umu.1.scd),
[Legendary launch JSON](https://github.com/legendary-gl/legendary/blob/master/legendary/cli.py),
[Legendary configuration selection](https://github.com/legendary-gl/legendary/blob/master/legendary/lfs/lgndry.py),
[Flatpak foreground commands](https://docs.flatpak.org/en/latest/flatpak-command-reference.html#flatpak-run).

## Manual fallback

Chest/dome launching can use a native Linux KH3 client and a Wine MoM runner.
The game/probe and client must share the host loopback network. Separate Wine
prefixes are fine: Linux PIDs are kept in Python, never passed to OpenProcess.
The live matrix below covers Flatpak Heroic. Manual wrappers require their own
foreground-process lifetime validation.

Set `KH3AP_MOM_ACTIVITY_PORT=38419` in both the KH3 game's launch environment
and the native Linux client's environment. Use a different unused port for
each concurrent KH3 instance. The probe listens only on IPv4 loopback and only
when configured; a connection temporarily suppresses background controller
input. It does not grant items or execute commands. Other local programs can
connect to this opt-in port, so do not expose it through network forwarding.

For the native client, also set:

```sh
export KH3AP_MOM_DIR='/games/MoM'
export KH3AP_MOM_COMMAND='["/absolute/path/to/mom-foreground-wrapper"]'
export KH3AP_MOM_ACTIVITY_PORT=38419
```

The command is a JSON argv array, executed without a shell. Configure the
correct Wine/Proton runner and prefix in your wrapper. It **must remain alive
until the actual game exits**, preferably by `exec`ing the foreground runner.
Do not use `xdg-open`, a Heroic URI, or a launcher that detaches: their PID is
the launcher, not the game. This is explicit runner configuration, not automatic
Heroic discovery. An example for a known working Wine setup is:

```sh
#!/bin/sh
export WINEPREFIX='/games/prefixes/mom'
exec /absolute/path/to/wine '/games/MoM/KINGDOM HEARTS Melody of Memory.exe'
```

Install the existing MoM controller in that game's `Mods` directory first.
Start KH3 before requesting a song. Close any manually launched MoM before
using this integration. Close MoM before requesting another song; Linux does
not scan for or terminate unknown game processes. Client restart does not
reattach to an already-running game: close it before launching a new challenge.

The client waits for its owned launch process on a background thread and signals
exit through a local socket pair, without timers or process scans. This also
works with bundled Python builds that do not expose `os.pidfd_open`.
Normal exit/crash closes
the activity connection; client death also releases it. The Windows probe
waits for socket events on a background thread and ORs native activity with
its existing Windows window observer. Losing KH3 ends the connection; there
is no automatic reconnect or game restart. Cross-prefix foreground focus is
left to the Linux desktop/window manager.

Requirements: a native Linux KH3 client and a foreground Wine/Proton runner.
See [Python subprocess documentation](https://docs.python.org/3/library/subprocess.html#subprocess.Popen.wait)
and [WSAEventSelect](https://learn.microsoft.com/en-us/windows/win32/api/winsock2/nf-winsock2-wsaeventselect).

## Linux validation

Test both selected storefronts through a real chest Examine callback. Require
matching request, song, difficulty and actual MoM Playing state, and verify the
foreground process and activity lease remain owned until exit. A launch/lifetime
check does not establish a song clear or AP chest reward; test those separately.

For Flatpak Heroic, run Legendary and its foreground runner inside Heroic's
sandbox, pass configuration through `LEGENDARY_CONFIG_PATH`, and keep AppImage
library paths out of external-tool environments. Record the actual launcher and
runner versions with the result instead of assuming a previous setup still works.
