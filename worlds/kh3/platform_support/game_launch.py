"""Process-launching operations for Kingdom Hearts III."""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

from ..installer_support.platform_config import (
    ensure_proton_dwmapi_override,
)
from .game_discovery import (
    _steam_root_for_executable,
    resolve_epic_launch_uri_for_kh3_executable,
    resolve_kh3_executable_path,
    resolve_steam_launch_uri_for_kh3_executable,
)

logger = logging.getLogger("Client")


def _ensure_kh3_proton_dwmapi_override(kh3_dir: Path) -> None:
    """Keep UE4SS injection configured while leaving process ownership to Steam."""
    if os.name == "nt":
        return
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    result = ensure_proton_dwmapi_override(kh3_dir, timestamp)
    errors = result.get("errors") or []
    if errors:
        logger.warning("Could not configure the KH3 Proton dwmapi override: %s", "; ".join(errors))
    elif result.get("changed"):
        logger.info("Configured the KH3 Proton dwmapi override before Steam launch.")


def _linux_clean_launch_env() -> dict[str, str]:
    keep_keys = (
        "HOME",
        "USER",
        "LOGNAME",
        "DISPLAY",
        "WAYLAND_DISPLAY",
        "XAUTHORITY",
        "XDG_RUNTIME_DIR",
        "DBUS_SESSION_BUS_ADDRESS",
        "XDG_CURRENT_DESKTOP",
        "DESKTOP_SESSION",
        "XDG_SESSION_TYPE",
        "XDG_DATA_DIRS",
        "LANG",
        "LC_ALL",
    )
    env = {key: value for key in keep_keys if (value := os.environ.get(key))}
    env["PATH"] = os.environ.get("PATH") or "/usr/local/bin:/usr/bin:/bin"
    return env


def _open_launch_uri(uri: str, *, description: str) -> bool:
    if os.name == "nt" and hasattr(os, "startfile"):
        try:
            os.startfile(uri)  # type: ignore[attr-defined]
            logger.info("Launched KH3 through %s: %s", description, uri)
            return True
        except Exception as exc:
            logger.warning("Could not launch KH3 through %s: %s", description, exc)
            return False

    is_steam = uri.casefold().startswith("steam://")
    is_heroic = uri.casefold().startswith("heroic://")
    if is_steam or is_heroic:
        app_id = "com.valvesoftware.Steam" if is_steam else "com.heroicgameslauncher.hgl"
        flatpak = shutil.which("flatpak")
        if flatpak is not None:
            try:
                flatpak_info = subprocess.run(
                    [flatpak, "info", app_id],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    env=_linux_clean_launch_env(),
                    timeout=2,
                    check=False,
                )
            except Exception:
                flatpak_info = None
            if flatpak_info is not None and flatpak_info.returncode == 0:
                try:
                    subprocess.Popen(
                        [
                            flatpak,
                            "run",
                            "--branch=stable",
                            "--arch=x86_64",
                            *(["--command=/app/bin/steam"] if is_steam else []),
                            app_id,
                            uri,
                        ],
                        close_fds=True,
                        env=_linux_clean_launch_env(),
                        start_new_session=True,
                    )
                    logger.info("Launched KH3 through Flatpak %s: %s", app_id, uri)
                    return True
                except Exception as exc:
                    logger.warning("Could not launch KH3 through Flatpak %s: %s", app_id, exc)

    opener = shutil.which("xdg-open")
    if opener is None:
        logger.warning("Could not launch KH3 through %s. xdg-open was not found.", description)
        return False
    try:
        subprocess.Popen([opener, uri], close_fds=True)
        logger.info("Launched KH3 through %s: %s", description, uri)
        return True
    except Exception as exc:
        logger.warning("Could not launch KH3 through %s: %s", description, exc)
        return False


def _steam_app_id_from_launch_uri(uri: str) -> str | None:
    match = re.fullmatch(r"steam://run/(\d+)", uri.strip(), flags=re.IGNORECASE)
    return match.group(1) if match is not None else None


def _launch_kh3_through_steam_exe(executable_path: Path, steam_launch_uri: str) -> bool:
    if os.name != "nt":
        return False
    steam_root = _steam_root_for_executable(executable_path)
    if steam_root is None:
        return False
    steam_exe = steam_root / "steam.exe"
    app_id = _steam_app_id_from_launch_uri(steam_launch_uri)
    if app_id is None or not steam_exe.is_file():
        return False
    launch_args = [str(steam_exe), "-applaunch", app_id]
    creationflags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    try:
        subprocess.Popen(
            launch_args,
            cwd=str(steam_root),
            close_fds=True,
            creationflags=creationflags,
        )
        logger.info("Launched KH3 through Steam executable: %s -applaunch %s", steam_exe, app_id)
        return True
    except Exception as exc:
        logger.warning("Could not launch KH3 through Steam executable %s: %s", steam_exe, exc)
        return False


def launch_epic_game(executable_path: Path, epic_launch_uri: str) -> bool:
    """Shared client/automation entry point; never falls back to the game EXE."""
    from .mixed_store_launch import launch_if_needed
    try:
        if launch_if_needed(executable_path, epic_launch_uri):
            return True
    except (OSError, RuntimeError, ValueError) as error:
        logger.error("Could not prepare the Epic/Steam launch: %s", error)
        return False
    return _open_launch_uri(epic_launch_uri, description="Epic Games Launcher")


def launch_kh3_game(kh3_dir: Path) -> None:
    """Launch KH3 through its selected store and compatibility environment."""

    executable_path = resolve_kh3_executable_path(kh3_dir)
    if not executable_path.is_file():
        logger.warning(
            "Could not launch KH3. Expected executable at %s. Set KH3 Dir in the KH3 Config tab.",
            executable_path,
        )
        return

    epic_launch_uri = resolve_epic_launch_uri_for_kh3_executable(executable_path)
    if epic_launch_uri:
        launch_epic_game(executable_path, epic_launch_uri)
        return

    steam_launch_uri = resolve_steam_launch_uri_for_kh3_executable(executable_path)
    if steam_launch_uri:
        _ensure_kh3_proton_dwmapi_override(kh3_dir)
    if steam_launch_uri and _launch_kh3_through_steam_exe(executable_path, steam_launch_uri):
        return
    if steam_launch_uri and _open_launch_uri(steam_launch_uri, description="Steam"):
        return

    creationflags = 0
    if os.name == "nt":
        creationflags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    try:
        subprocess.Popen(
            [str(executable_path)],
            cwd=str(executable_path.parent),
            close_fds=True,
            creationflags=creationflags,
        )
        logger.info("Launched KH3 from: %s", executable_path)
    except Exception as exc:
        logger.warning("Could not launch KH3 from %s: %s", executable_path, exc)


__all__ = ["launch_kh3_game"]
