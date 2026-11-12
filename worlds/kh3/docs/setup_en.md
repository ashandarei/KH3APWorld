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

## 2. Prepare the game

To create a player YAML, open **KH3 YAML Creator** from the launcher or the
client's **YAML Creator** button. Use **Save as…** to name a settings preset,
**Save** (Ctrl+S) to update it, and the **Saved settings** dropdown to load one
again. Presets stay on this computer and keep your current player name when
loaded. **Delete** removes the selected preset and leaves your current settings
in the editor. **Export YAML** writes the player file to share with your host;
an empty player name defaults to **Player**. The **− Zoom +** controls at the
bottom left adjust the text size one point at a time.

1. Close Kingdom Hearts III.
2. Open **KH3 Client** from the Archipelago Launcher.
3. Open **KH3 Config**.
4. On **KH3 Dir**, choose your KH3 installation folder if it was not found automatically.
5. Check that **Save Dir** points to the folder where your KH3 saves are kept.
6. Leave save cleanup set to **Backup** to keep a copy of saves when starting another run.
7. Click **Patch Game** and wait for it to finish.

The client installs the required mods, including Garden of Assemblage. Follow
any messages it shows. Run **Patch Game** again after updating the KH3 APWorld.

On Steam, the client also tries to turn off Steam Cloud for KH3. Follow its
message if you need to turn it off yourself.

On Linux, if patching asks you to start KH3 once, open the game through Steam
or Heroic, close it, and try **Patch Game** again.

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

## Help with common problems

| Problem | What to do |
| --- | --- |
| KH3 Client is missing from the launcher. | Install `kh3.apworld` again and restart the launcher. |
| Completed chests or challenges are not showing up. | Close KH3, run **Patch Game**, connect the client, and launch KH3 again. |
| The client says the room and your saves do not match. | Close KH3, activate the matching run in **Save Manager**, and reconnect. |
| Old saves are blocking a new run. | Close KH3 and use automatic save cleanup or the trash button for saves in **KH3 Config**. Keep **Backup** selected if you want to return to those saves. |
| The game still seems to be using the previous run. | Close KH3 completely. Wait for the client to finish preparing the new run, then launch again. |

If you still need help, use **Export Debug Bundle** in **KH3 Config** and share
it with your support request, along with the error message you saw.
