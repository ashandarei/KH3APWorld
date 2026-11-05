"""Explicit Linux setup/discovery. No background discovery or launcher edits."""
from __future__ import annotations

import json
import os
import secrets
import shlex
import shutil
import socket
import subprocess
from pathlib import Path

MOM_EXE = "KINGDOM HEARTS Melody of Memory.exe"
HEROIC_APP = "com.heroicgameslauncher.hgl"


def read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def settings_path() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / "kh3ap/mom-linux.json"


def discover(home: Path | None = None) -> list[dict]:
    home = home or Path.home()
    config = Path(os.environ.get("XDG_CONFIG_HOME", str(home / ".config")))
    roots = [(config / "heroic", config / "legendary", False),
             (home / ".var/app/com.heroicgameslauncher.hgl/config/heroic",
              home / ".var/app/com.heroicgameslauncher.hgl/config/legendary", True)]
    found = []
    seen = set()
    for heroic, legendary, flatpak in roots:
        # Heroic commonly keeps its own Legendary database inside heroic/.
        for database in (heroic / "legendaryConfig/legendary", legendary):
            for app, item in read_json(database / "installed.json").items():
                if not isinstance(item, dict) or not isinstance(item.get("install_path"), str):
                    continue
                if not isinstance(app, str) or not app or any(c in app for c in "/\\"):
                    continue
                root = Path(item["install_path"]).expanduser().resolve()
                if not (root / MOM_EXE).is_file() or not (root / "GameAssembly.dll").is_file():
                    continue
                identity = (str(root), str(database), app)
                if identity in seen:
                    continue
                seen.add(identity)
                found.append(dict(game_dir=str(root), app=app, database=str(database),
                                  heroic=str(heroic), flatpak=flatpak))
    return found


def recipe_for(root: Path) -> dict:
    choices = [row for row in discover() if Path(row["game_dir"]) == root.resolve()]
    if len(choices) != 1:
        raise RuntimeError("Could not select one Heroic/Legendary registration for this MoM folder. "
                           "Use manual Linux configuration (tools/kh3/MOM_LINUX.md).")
    row = choices[0]
    if os.environ.get("FLATPAK_ID"):
        raise RuntimeError("Run the native KH3 client outside Flatpak to configure MoM launching.")
    flatpak = shutil.which("flatpak") if row["flatpak"] else None
    if row["flatpak"] and not flatpak:
        raise RuntimeError("Flatpak Heroic was detected, but the flatpak launcher is unavailable.")
    heroic = Path(row["heroic"])
    global_config = read_json(heroic / "config.json")
    defaults = global_config.get("defaultSettings", global_config)
    game_config = read_json(heroic / "GamesConfig" / (row["app"] + ".json"))
    if game_config.get("version", "v0") not in ("v0", "v0.1"):
        raise RuntimeError("This Heroic settings version needs manual Linux configuration.")
    overrides = game_config.get(row["app"], {})
    if not isinstance(defaults, dict) or not isinstance(overrides, dict):
        raise RuntimeError("Heroic MoM settings are malformed.")
    settings = {**defaults, **overrides}
    settings["language"] = overrides.get("language", "")
    # Do not silently bypass arbitrary wrappers or pre/post scripts.
    if any(settings.get(key) for key in ("wrapperOptions", "beforeLaunchScriptPath", "afterLaunchScriptPath",
                                         "targetExe", "showMangohud")) or (settings.get("useGameMode") and not flatpak):
        raise RuntimeError("This Heroic game uses custom launch wrappers or scripts. Use manual Linux configuration.")
    if any((settings.get("gamescope") or {}).get(key) for key in ("enableLimiter", "enableUpscaling", "enableForceFullscreen")):
        raise RuntimeError("Gamescope setup requires manual Linux configuration.")
    wine = settings.get("wineVersion", {})
    if not isinstance(wine, dict):
        raise RuntimeError("Heroic runner settings are malformed.")
    runner = Path(wine.get("bin", "")).expanduser()
    prefix = Path(settings.get("winePrefix", "")).expanduser()
    if not runner.is_absolute() or not runner.is_file() or not os.access(runner, os.X_OK) or not prefix.is_absolute() or not prefix.is_dir():
        raise RuntimeError("Select a working Wine/Proton runner and prefix for MoM in Heroic, then retry setup.")
    candidates = [shutil.which("legendary"),
                  "/opt/Heroic/resources/app.asar.unpacked/build/bin/x64/linux/legendary",
                  "/opt/Heroic/resources/app.asar.unpacked/build/bin/linux/legendary",
                  "/usr/lib/heroic/resources/app.asar.unpacked/build/bin/x64/linux/legendary"]
    executable = next((p for p in candidates if p and Path(p).is_file() and os.access(p, os.X_OK)), None)
    if flatpak:
        executable = "/app/bin/heroic/resources/app.asar.unpacked/build/bin/x64/linux/legendary"
    if not executable:
        raise RuntimeError("Could not find native Legendary. Use manual Linux configuration or install Legendary.")
    command = ([flatpak, "run", "--env=LEGENDARY_CONFIG_PATH=" + row["database"],
                "--command=" + executable, HEROIC_APP] if flatpak else [executable])
    command += ["launch", row["app"], "--json"]
    environment = {"LEGENDARY_CONFIG_PATH": row["database"]}
    for option in settings.get("enviromentOptions", []):
        if not isinstance(option, dict) or not isinstance(option.get("key"), str) or not isinstance(option.get("value"), str):
            raise RuntimeError("Invalid Heroic environment setting.")
        environment[option["key"]] = option["value"]
    launch_runner = runner
    if wine.get("type") == "wine":
        command += ["--wine", str(runner), "--wine-prefix", str(prefix)]
    elif wine.get("type") == "proton":
        if settings.get("disableUMU") is True:
            steam = next((p for p in (Path.home() / ".steam/steam", Path.home() / ".local/share/Steam") if p.is_dir()), None)
            if steam is None:
                raise RuntimeError("Could not find Steam's runtime for this Proton configuration; use manual setup.")
            command += ["--no-wine", "--wrapper", shlex.join([str(runner), "waitforexitandrun"])]
            environment.update(STEAM_COMPAT_DATA_PATH=str(prefix), STEAM_COMPAT_CLIENT_INSTALL_PATH=str(steam))
        else:
            # Match Heroic: prefer host umu-run, then its downloaded runtime.
            # Keep the selected Proton and prefix; UMU stays in the foreground
            # so the existing child-lifetime tracker can observe game exit.
            candidates = ([heroic / "tools/runtimes/umu/umu_run.py"] if flatpak else
                          [shutil.which("umu-run"), heroic / "tools/runtimes/umu/umu_run.py"])
            umu = next((Path(p) for p in candidates if p and Path(p).is_absolute()
                        and Path(p).is_file() and os.access(p, os.X_OK)), None)
            if umu is None:
                raise RuntimeError("Heroic's UMU launcher was not found. Launch MoM once through Heroic to prepare "
                                   "its runtime, close MoM, then click Install MoM Mod again.")
            launch_runner = umu
            command += ["--no-wine", "--wrapper", shlex.join([str(umu)])]
            environment.update(WINEPREFIX=str(prefix), STEAM_COMPAT_DATA_PATH=str(prefix),
                               PROTONPATH=str(runner.parent), PROTON_VERB="waitforexitandrun", STORE="egs")
            # Heroic caches its store-specific protonfixes identity here.
            game_id = read_json(heroic / "store_cache/umu.json").get("legendary_" + row["app"])
            environment.setdefault("GAMEID", game_id if isinstance(game_id, str) and game_id else "umu-0")
    else:
        raise RuntimeError("The selected Heroic runner is not Wine or Proton. Select a supported runner for MoM.")
    for setting, suffix in (("enableEsync", "ESYNC"), ("enableFsync", "FSYNC")):
        if setting in settings:
            if wine.get("type") == "proton":
                environment["PROTON_NO_" + suffix] = "0" if settings[setting] else "1"
            else:
                environment["WINE" + suffix] = "1" if settings[setting] else "0"
    # MelonLoader's installed version.dll must be loaded as a native proxy.
    overrides = environment.get("WINEDLLOVERRIDES", os.environ.get("WINEDLLOVERRIDES", ""))
    environment["WINEDLLOVERRIDES"] = overrides + (";" if overrides else "") + "version=n,b"
    if settings.get("offlineMode"):
        command.append("--offline")
    if settings.get("language"):
        command += ["--language", settings["language"]]
    if settings.get("launcherArgs"):
        command += ["--", *shlex.split(settings["launcherArgs"])]
    recipe = dict(game_dir=str(root.resolve()), query=command, environment=environment, runner=str(launch_runner))
    if flatpak:
        recipe["flatpak"] = dict(executable=flatpak, prefix=str(prefix),
                                 gamemode=bool(settings.get("useGameMode")))
    return recipe


def resolve_launch(recipe: dict) -> tuple[list[str], dict, Path]:
    from ..platform_support.game_launch import _linux_clean_launch_env
    environment = {**_linux_clean_launch_env(), **recipe["environment"]}
    result = subprocess.run(recipe["query"], env=environment, capture_output=True, text=True, timeout=60)
    # Output can contain exchange tokens. Never persist it or include it in errors.
    if result.returncode:
        raise RuntimeError("Legendary could not prepare MoM. Check your Heroic login and game installation.")
    try:
        data = json.loads(result.stdout)
        root = Path(recipe["game_dir"]).resolve()
        game = (Path(data["game_directory"]) / data["game_executable"]).resolve()
        if game != root / MOM_EXE or data.get("pre_launch_command"):
            raise ValueError("unexpected game or pre-launch script")
        command = data["launch_command"]
        if not command or Path(command[0]).resolve() != Path(recipe["runner"]).resolve():
            raise ValueError("unexpected runner")
        command = [*command, str(game), *data["game_parameters"], *data["user_parameters"], *data["egl_parameters"]]
        if any(not isinstance(v, str) or "\0" in v for v in command):
            raise ValueError("invalid argv")
        overrides = data["environment"]
        if not isinstance(overrides, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in overrides.items()):
            raise ValueError("invalid environment")
        environment.update(overrides)
        environment.update(recipe["environment"])
        if environment.get("LEGENDARY_WRAPPER_EXE"):
            raise ValueError("unexpected wrapper")
        cwd = Path(data["working_directory"]).resolve(strict=True)
        cwd.relative_to(root)
        if sandbox := recipe.get("flatpak"):
            # A foreground Flatpak invocation owns UMU/Wine until game exit.
            # Pass the launch environment inside the sandbox explicitly; a URI
            # would detach and lose both exit tracking and the activity signal.
            invocation = [sandbox["executable"], "run", "--share=network",
                          "--filesystem=" + str(root), "--filesystem=" + sandbox["prefix"],
                          "--filesystem=" + str(Path(recipe["runner"]).parent),
                          "--cwd=" + str(cwd)]
            invocation.extend("--env=" + key + "=" + value for key, value in
                              {**overrides, **recipe["environment"]}.items())
            entry = "/app/bin/gamemoderun" if sandbox["gamemode"] else command[0]
            command = [*invocation, "--command=" + entry, HEROIC_APP,
                       *(command if sandbox["gamemode"] else command[1:])]
        return command, environment, cwd
    except (KeyError, TypeError, ValueError, OSError):
        raise RuntimeError("Legendary returned an unsupported launch configuration; use manual Linux setup.") from None


def setup(root: Path, kh3_root: Path, progress=lambda message: None) -> str:
    from ..bridge_support.files import atomic_write_json
    from ..platform_support.game_discovery import kh3_win64_dir_from_root
    from . import mom_installation as installer

    recipe = recipe_for(root)
    win64 = kh3_win64_dir_from_root(kh3_root)
    if not (win64 / "KINGDOM HEARTS III.exe").is_file():
        raise RuntimeError("Select the KH3 installation in the client before setting up MoM.")
    progress("Verifying Linux launch settings…")
    resolve_launch(recipe)  # Validate once without starting either game.
    config_path = win64 / "kh3ap_mom_activity.json"
    existing = read_json(config_path)
    port = existing.get("port")
    if type(port) is not int or not 1024 <= port <= 65535:
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
    installer.install(root, progress)
    token = existing.get("token")
    if not isinstance(token, str) or len(token) != 32 or any(c not in "0123456789abcdef" for c in token):
        token = secrets.token_hex(16)
    atomic_write_json(config_path, {"schema": 1, "port": port, "token": token})
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    atomic_write_json(path, {"schema": 1, **recipe, "port": port, "token": token, "probe_config": str(config_path)})
    path.chmod(0o600)
    return "Linux MoM setup saved and integration installed. Restart KH3 once, then open a music chest or activate a dome."
