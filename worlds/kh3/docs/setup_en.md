# Kingdom Hearts III setup

The KH3 client installs the mods you need and prepares your randomized run for
you. These instructions cover the Steam and Epic Games Store versions of KH3
on PC.

Start a **New Game** for each new randomized run, also called a seed. Keep the
KH3 client open and connected while you play.

## What you need

- Kingdom Hearts III on PC.
- Archipelago 0.6.8.
- The `kh3.apworld` file supplied by the project maintainer or your host.
  This file adds KH3 support to the Archipelago Launcher.
- Your room's server address and exact player name. Ask the host for these.

## 1. Add KH3 to the launcher

1. Save the `kh3.apworld` file supplied by the maintainer or your host.
2. Open the Archipelago Launcher.
3. Choose **Install APWorld** and select the downloaded file.
4. Restart the launcher. **KH3 Client** should now appear.

If your host asks for a settings file, open **KH3 YAML Creator** in the
launcher, choose your settings, and send them the file from **Export YAML**.

## 2. Prepare the game

1. Close Kingdom Hearts III and open **KH3 Client** from the Archipelago Launcher.
2. In **KH3 Config**, check **KH3 Dir** and **Save Dir**. Leave save cleanup set to **Backup**.
3. Click **Patch Game** to install the required mods. Follow any prompts and wait for it to finish.

Run **Patch Game** again after updating `kh3.apworld`.

## 3. Join your room and play

1. Keep KH3 closed while the client prepares the run.
2. Enter your room's server address and click **Connect**.
3. Enter your exact player name and the room password if asked.
4. Wait for the client to finish preparing your run.
5. Click **Launch KH3**, then start a **New Game** for this seed.

The client gets your player file from the room automatically. You normally
only need the address and player name from your host.

Keep the client connected before launching KH3 or loading a save. Whenever
you change runs or update the game mods, close KH3 completely and launch it
again after the client finishes.

## Continue a previous run

1. Close KH3 and disconnect the client.
2. Open **Save Manager**.
3. Click **Activate** beside the run you want to continue.
4. Reconnect, launch KH3, and load that run's save.

Save Manager remembers each run's connection details, game saves, and progress.
Activating a run restores the saves that belong to it.

## Start a different run

Close KH3 and connect to the new room. If the client asks you to clear saves
from the previous run, enable **Auto remove saves on new seed**, or use the
trash button for saves in **KH3 Config** while KH3 is closed.

With cleanup set to **Backup**, the client keeps the old saves before preparing
the new run. Choosing deletion instead removes that protection.

## If your host gives you a player file

Use **Import Seed Pak** in Save Manager to select the file your host gave you.
It may end in `.pak`, `.apkh3`, or `.zip`. Then click **Activate** beside the
matching run and connect to your room.

The first time the installed client generates a seed pak locally, it downloads
the 223 KiB seed-template ZIP from the pinned GoA release. The client checks
its SHA-256 and extracts it automatically on Windows and Linux. Later seeds
can use the cached ZIP offline. Importing a ready-made `.pak` skips this download.

## Help with common problems

| Problem | What to do |
| --- | --- |
| KH3 Client is missing from the launcher. | Install `kh3.apworld` again and restart the launcher. |
| The client asks you to disable Steam Cloud. | Turn off Steam Cloud for KH3 in the game's Steam properties. |
| On Steam, KH3 crashes with “expecting 187, received 181.” when entering the data room | Close KH3 and delete the `*_0_P.*` files in `Content/Paks`, one level above `~mods`. |
| On Linux, patching asks you to start KH3 once. | Launch KH3 through Steam or Heroic, close it, and run **Patch Game** again. |
| Completed chests or challenges are not showing up. | Close KH3, run **Patch Game**, connect the client, and launch KH3 again. |
| The client says the room and your saves do not match. | Close KH3, activate the matching run in **Save Manager**, and reconnect. |
| Old saves are blocking a new run. | Close KH3 and use automatic save cleanup or the trash button for saves in **KH3 Config**. Keep **Backup** selected if you want to return to those saves. |

If you still need help, use **Export Debug Bundle** in **KH3 Config** and share
it with your support request, along with the error message you saw.
