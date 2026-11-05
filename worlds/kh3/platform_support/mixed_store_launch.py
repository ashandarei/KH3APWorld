"""Launch authenticated Epic KH3 with Steam's Linux client available to KH0.2.

Use a new instance of the installed Steam Flatpak, with per-launch access to
the selected game, its existing Heroic prefix and configured Proton runner.
No persistent Flatpak overrides or replacement game/Steam libraries are used.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import logging
import os
from pathlib import Path
import re
import shutil
import subprocess
import threading

LOG = logging.getLogger("Client")
STEAM_APP = "com.valvesoftware.Steam"
HEROIC_APP = "com.heroicgameslauncher.hgl"
KH02_STEAM_APP_ID = "2552440"
_launch_lock = threading.Lock()
_launching: set[Path] = set()


@dataclass(frozen=True)
class LaunchEnvironment:
    flatpak: str
    steam: Path
    runtime: Path
    proton: Path
    prefix: Path
    collection: Path
    executable: Path
    source_collection: Path
    documents: Path
    config: Path
    app_id: str


def _absolute_path(value, description: str) -> Path:
    if not isinstance(value, str) or not value or any(ch in value for ch in "\0\r\n"):
        raise RuntimeError(f"Invalid {description} in Heroic settings.")
    path = Path(value)
    if not path.is_absolute():
        raise RuntimeError(f"The {description} in Heroic must be an absolute path.")
    return path.resolve()


def _runtime_for_proton(steam: Path, proton: Path) -> Path:
    from .game_discovery import _steam_library_paths_from_vdf
    manifest = (proton.parent / "toolmanifest.vdf").read_text(encoding="utf-8")
    match = re.search(r'"require_tool_appid"\s+"([0-9]+)"', manifest)
    if match is None:
        raise RuntimeError("The selected Proton runner does not declare a Steam runtime.")
    libraries = [steam]
    for path in [steam / "steamapps/libraryfolders.vdf", steam / "config/libraryfolders.vdf"]:
        if path.is_file():
            libraries.extend(_steam_library_paths_from_vdf(path.read_text(encoding="utf-8")))
    for library in dict.fromkeys(libraries):
        app_manifest = library / "steamapps" / f"appmanifest_{match[1]}.acf"
        if not app_manifest.is_file():
            continue
        text = app_manifest.read_text(encoding="utf-8")
        folder = re.search(r'"installdir"\s+"([^"/\\]+)"', text)
        if folder is None or folder[1] in {".", ".."}:
            continue
        runtime = library / "steamapps/common" / folder[1] / "_v2-entry-point"
        if runtime.is_file():
            return runtime.resolve()
    raise RuntimeError("Install the Steam Linux Runtime required by the selected Proton runner in Steam.")


def prepare_environment(executable: Path, source) -> LaunchEnvironment:
    home = Path.home()
    flatpak = shutil.which("flatpak")
    steam = home / ".var/app" / STEAM_APP / ".local/share/Steam"
    if flatpak is None or not (steam / "steam.sh").is_file():
        raise RuntimeError("Steam Flatpak is required for this Epic/Steam launch path.")
    config = home / ".var/app" / HEROIC_APP / "config/heroic"
    installed = json.loads((config / "legendaryConfig/legendary/installed.json").read_text(encoding="utf-8"))
    for app_id, entry in installed.items():
        if not isinstance(app_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", app_id) or not isinstance(entry, dict):
            continue
        try:
            collection = _absolute_path(entry.get("install_path"), "game installation")
        except RuntimeError:
            continue
        relative = entry.get("executable")
        if not isinstance(relative, str):
            continue
        if (collection / relative.replace("\\", "/")).resolve() != executable.resolve():
            continue
        settings = json.loads((config / "GamesConfig" / f"{app_id}.json").read_text(encoding="utf-8"))[app_id]
        version = settings.get("wineVersion", {})
        if version.get("type") != "proton":
            raise RuntimeError("Select a Proton runner for KH3 in Heroic before using a Steam KH0.2 source.")
        proton = _absolute_path(version.get("bin"), "Proton runner")
        prefix = _absolute_path(settings.get("winePrefix"), "Wine prefix")
        if not proton.is_file() or not (prefix / "pfx/drive_c").is_dir():
            raise RuntimeError("Launch KH3 once in Heroic to initialize its selected Proton prefix.")
        if settings.get("wrapperOptions") or settings.get("enviromentOptions"):
            raise RuntimeError("Custom Heroic wrappers or environment options need explicit support for mixed-store launch.")
        return LaunchEnvironment(flatpak, steam.resolve(), _runtime_for_proton(steam, proton), proton,
                                 prefix, collection, executable.resolve(), source.root.parent.resolve(),
                                 home / "Documents/KINGDOM HEARTS III", config, app_id)
    raise RuntimeError("The selected Epic KH3 executable is not registered in Heroic.")


def launch_command(environment: LaunchEnvironment, arguments: list[str]) -> list[str]:
    grants = [environment.collection, environment.source_collection, environment.prefix,
              environment.documents]
    command = [environment.flatpak, "run", "--branch=stable", "--arch=x86_64"]
    command.extend("--filesystem=" + str(path) for path in dict.fromkeys(grants))
    command.extend(["--filesystem=" + str(environment.proton.parent),
                    "--filesystem=" + str(environment.runtime.parent),
                    "--env=STEAM_COMPAT_CLIENT_INSTALL_PATH=" + str(environment.steam),
                    "--env=STEAM_COMPAT_DATA_PATH=" + str(environment.prefix),
                    "--env=STEAM_COMPAT_INSTALL_PATH=" + str(environment.collection),
                    "--env=STEAM_COMPAT_APP_ID=" + KH02_STEAM_APP_ID,
                    "--env=SteamAppId=" + KH02_STEAM_APP_ID, "--env=SteamGameId=" + KH02_STEAM_APP_ID,
                    "--unset-env=GAMEID", "--unset-env=UMU_ID", "--unset-env=STORE",
                    "--command=" + str(environment.runtime), STEAM_APP,
                    "--verb=waitforexitandrun", "--", str(environment.proton), "waitforexitandrun",
                    str(environment.executable), *arguments])
    return command


def _start_authenticated(executable: Path, source, selection_revision: int):
    from ..kh02 import installation
    from .game_launch import _linux_clean_launch_env
    from ..kh02.store_launch import _legendary_command, _validated_arguments
    from .process_status import get_process_status
    if get_process_status("KINGDOM HEARTS III.exe").get("running"):
        raise RuntimeError("Close the running KH3 session before changing its launch environment.")
    installation.inspect_install(source.root, verify=True)
    installation.sync_runtime(executable.parent.parent.parent, source)
    environment = prepare_environment(executable, source)
    clean = _linux_clean_launch_env()
    backend = _legendary_command(environment.config / "legendaryConfig/legendary", True)
    try:
        result = subprocess.run(backend + ["launch", environment.app_id, "--json"],
                                env=clean, capture_output=True, text=True, timeout=40, check=False)
        if result.returncode:
            raise ValueError("Authentication unavailable")
        plan = json.loads(result.stdout)
        arguments = _validated_arguments(plan, environment.app_id)
        game_arguments = plan.get("game_parameters", [])
        if (not isinstance(game_arguments, list) or len(game_arguments) > 64 or
                any(not isinstance(arg, str) or len(arg) > 2048 or any(ch in arg for ch in "\0\r\n")
                    or arg.partition("=")[0].casefold().startswith("-auth_") for arg in game_arguments)):
            raise ValueError("Invalid game arguments")
    except Exception:
        raise RuntimeError("Epic KH3 authentication failed. Sign in to Epic in Heroic and retry.") from None
    current_source = installation.selected_install()
    if (installation.selection_revision != selection_revision or current_source is None or
            current_source.root != source.root):
        raise RuntimeError("The KH0.2 source changed during authentication. Launch again with the selected source.")
    if get_process_status("KINGDOM HEARTS III.exe").get("running"):
        raise RuntimeError("KH3 started during authentication; no second session was launched.")
    # Credentials remain in memory and process arguments, as with ordinary Epic
    # launches. Never log the command, backend output, or subprocess exceptions.
    try:
        subprocess.Popen([environment.flatpak, "run", "--command=/app/bin/steam", STEAM_APP, "-silent"],
                         env=clean, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, close_fds=True, start_new_session=True)
        process = subprocess.Popen(launch_command(environment, game_arguments + arguments), env=clean,
                                   stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   close_fds=True, start_new_session=True)
    except Exception:
        raise RuntimeError("Could not start Epic KH3 with Steam access. Keep Steam running and signed in.") from None
    LOG.info("Launched authenticated Epic KH3 with Steam access for the selected KH0.2 source.")
    return process


def _watch_launch(process, executable: Path) -> None:
    try:
        status = process.wait()
        if status:
            LOG.error("Epic KH3's mixed-store launcher exited with code %s. Check Steam sign-in and the selected Proton runner.", status)
    finally:
        with _launch_lock:
            _launching.discard(executable)


def _launch_worker(executable: Path, source, revision: int) -> None:
    try:
        process = _start_authenticated(executable, source, revision)
    except RuntimeError as error:
        LOG.error("Could not prepare the Epic/Steam launch: %s", error)
    except Exception:
        LOG.error("Could not prepare the Epic/Steam launch. Check the selected game paths and Heroic configuration.")
    else:
        threading.Thread(target=_watch_launch, args=(process, executable), daemon=True).start()
        return
    with _launch_lock:
        _launching.discard(executable)


def launch_if_needed(executable: Path, epic_uri: str) -> bool:
    if os.name == "nt" or not epic_uri.startswith("heroic://"):
        return False
    from ..kh02 import installation
    source = installation.selected_install()
    steam = Path.home() / ".var/app" / STEAM_APP / ".local/share/Steam"
    if source is None or source.platform != "steam" or not (steam / "steam.sh").is_file():
        return False
    executable = executable.resolve()
    with _launch_lock:
        if executable in _launching:
            LOG.info("Epic KH3's mixed-store launch is already active.")
            return True
        _launching.add(executable)
    # Authentication and hashing must not block the GUI. The short-lived worker
    # also lets CLI callers finish dispatching before their interpreter exits.
    worker = threading.Thread(target=_launch_worker,
                              args=(executable, source, installation.selection_revision), daemon=False)
    try:
        worker.start()
    except Exception:
        with _launch_lock:
            _launching.discard(executable)
        raise RuntimeError("Could not start the mixed-store launch worker.") from None
    return True
