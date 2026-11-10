# KH3 APWorld

Kingdom Hearts III's Archipelago world, client, and seed generator.
This repository includes the complete Archipelago 0.6.8 development framework.
The official Archipelago repository is retained as the `upstream` Git remote;
KH3 development uses `main` in this repository.

Players can start with the [setup guide](worlds/kh3/docs/setup_en.md) and
[game guide](worlds/kh3/docs/en_Kingdom%20Hearts%20III.md).

## Development

The world and client live in `worlds/kh3/`; `KH3Client.py` launches the client.
World and catalog development utilities live in `tools/kh3/`.
The native DLL source lives in the separate `KH3Runtime` checkout, normally
beside this repository. Its source is needed for native contract tests and
local DLL development.

Prepare Python dependencies using `requirements.txt`. Copy `.env.example` to
`.env` and set local game and source paths as needed. Local settings, bridge
state, logs, and build outputs are ignored by Git.

Run KH3 tests from this repository:

```powershell
.\scripts\test-kh3.ps1
```

Build the APWorld using its pinned, published KH3Runtime release:

```powershell
.\scripts\build-workspace-apworld.ps1 -RunSmokeTests
```

This command verifies the release download and creates APWorld artifacts without
installing them or changing a seed. It does not build or bundle the native DLL.
Catalog changes are exported explicitly with `tools/kh3/export_dll_inputs.py`;
ordinary native builds use the runtime repository's own checked-in inputs.

## Licenses

Archipelago's framework retains its [MIT license](LICENSE). First-party code
under `worlds/kh3/` is [GPL-3.0-only](worlds/kh3/LICENSE); its
[copyright notice](worlds/kh3/COPYRIGHT.txt) explains component boundaries.
The separate native runtimes are MIT licensed. Upstream code, artwork, fonts,
and game/engine material retain their applicable notices and terms.

## Upstream Archipelago documentation

# [Archipelago](https://archipelago.gg) ![Discord Shield](https://discordapp.com/api/guilds/731205301247803413/widget.png?style=shield) | [Install](https://github.com/ArchipelagoMW/Archipelago/releases)

Archipelago provides a generic framework for developing multiworld capability for game randomizers. In all cases,
presently, Archipelago is also the randomizer itself.

Currently, the following games are supported:

* The Legend of Zelda: A Link to the Past
* Factorio
* Subnautica
* Risk of Rain 2
* The Legend of Zelda: Ocarina of Time
* Timespinner
* Super Metroid
* Secret of Evermore
* Final Fantasy
* VVVVVV
* Raft
* Super Mario 64
* Meritous
* Super Metroid/Link to the Past combo randomizer (SMZ3)
* ChecksFinder
* Hollow Knight
* The Witness
* Sonic Adventure 2: Battle
* Starcraft 2
* Dark Souls 3
* Super Mario World
* Pokémon Red and Blue
* Hylics 2
* Overcooked! 2
* Zillion
* Lufia II Ancient Cave
* Blasphemous
* Wargroove
* Stardew Valley
* The Legend of Zelda
* The Messenger
* Kingdom Hearts 2
* The Legend of Zelda: Link's Awakening DX
* Adventure
* DLC Quest
* Noita
* Undertale
* Bumper Stickers
* Mega Man Battle Network 3: Blue Version
* Muse Dash
* DOOM 1993
* Terraria
* Lingo
* Pokémon Emerald
* DOOM II
* Shivers
* Heretic
* Landstalker: The Treasures of King Nole
* Final Fantasy Mystic Quest
* TUNIC
* Kirby's Dream Land 3
* Celeste 64
* Castlevania 64
* A Short Hike
* Yoshi's Island
* Mario & Luigi: Superstar Saga
* Bomb Rush Cyberfunk
* Aquaria
* Yu-Gi-Oh! Ultimate Masters: World Championship Tournament 2006
* A Hat in Time
* Old School Runescape
* Kingdom Hearts 1
* Mega Man 2
* Yacht Dice
* Faxanadu
* Saving Princess
* Castlevania: Circle of the Moon
* Inscryption
* Civilization VI
* The Legend of Zelda: The Wind Waker
* Jak and Daxter: The Precursor Legacy
* Super Mario Land 2: 6 Golden Coins
* shapez
* Paint
* Celeste (Open World)
* Choo-Choo Charles
* APQuest
* Satisfactory
* EarthBound
* Mega Man 3
* Gauntlet Legends

For setup and instructions check out our [tutorials page](https://archipelago.gg/tutorial/).
Downloads can be found at [Releases](https://github.com/ArchipelagoMW/Archipelago/releases), including compiled
windows binaries.

## History

Archipelago is built upon a strong legacy of brilliant hobbyists. We want to honor that legacy by showing it here.
The repositories which Archipelago is built upon, inspired by, or otherwise owes its gratitude to are:

* [bonta0's MultiWorld](https://github.com/Bonta0/ALttPEntranceRandomizer/tree/multiworld_31)
* [AmazingAmpharos' Entrance Randomizer](https://github.com/AmazingAmpharos/ALttPEntranceRandomizer)
* [VT Web Randomizer](https://github.com/sporchia/alttp_vt_randomizer)
* [Dessyreqt's alttprandomizer](https://github.com/Dessyreqt/alttprandomizer)
* [Zarby89's](https://github.com/Ijwu/Enemizer/commits?author=Zarby89)
  and [sosuke3's](https://github.com/Ijwu/Enemizer/commits?author=sosuke3) contributions to Enemizer, which make up the
  vast majority of Enemizer contributions.

We recognize that there is a strong community of incredibly smart people that have come before us and helped pave the
path. Just because one person's name may be in a repository title does not mean that only one person made that project
happen. We can't hope to perfectly cover every single contribution that lead up to Archipelago, but we hope to honor
them fairly.

### Path to the Archipelago

Archipelago was directly forked from bonta0's `multiworld_31` branch of ALttPEntranceRandomizer (this project has a
long legacy of its own, please check it out linked above) on January 12, 2020. The repository was then named to
_MultiWorld-Utilities_ to better encompass its intended function. As Archipelago matured, then known as
"Berserker's MultiWorld" by some, we found it necessary to transform our repository into a root level repository
(as opposed to a 'forked repo') and change the name (which came later) to better reflect our project.

## Running Archipelago

For most people, all you need to do is head over to
the [releases page](https://github.com/ArchipelagoMW/Archipelago/releases), then download and run the appropriate
installer, or AppImage for Linux-based systems.

If you are a developer or are running on a platform with no compiled releases available, please see our doc on
[running Archipelago from source](docs/running%20from%20source.md).

## Related Repositories

This project makes use of multiple other projects. We wouldn't be here without these other repositories and the
contributions of their developers, past and present.

* [z3randomizer](https://github.com/ArchipelagoMW/z3randomizer)
* [Enemizer](https://github.com/Ijwu/Enemizer)
* [Ocarina of Time Randomizer](https://github.com/TestRunnerSRL/OoT-Randomizer)

## Contributing

To contribute to Archipelago, including the WebHost, core program, or by adding a new game, see our
[Contributing guidelines](/docs/contributing.md).

## FAQ

For Frequently asked questions, please see the website's [FAQ Page](https://archipelago.gg/faq/en/).

## Code of Conduct

Please refer to our [code of conduct](/docs/code_of_conduct.md).
