"""Client warning, acknowledgement, and prompt lifetime for Steam Cloud."""

from __future__ import annotations

import logging
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..platform_support.game_discovery import is_likely_steam_kh3_root_dir, normalize_kh3_root_dir
from ..platform_support.steam_cloud import SteamCloudDisableResult, SteamCloudState

logger = logging.getLogger("Client")

KH3_STEAM_CLOUD_WARNING = (
    "KINGDOM HEARTS III's per-game Steam Cloud setting is enabled. Run Patch Game to disable it automatically, "
    "or disable Steam Cloud in the game's Steam properties before playing Archipelago, to prevent cloud sync from "
    "restoring or overwriting randomizer save data."
)
KH3_STEAM_CLOUD_UNKNOWN_WARNING = (
    "Steam installation detected, but the KH3 client could not verify that Steam Cloud is disabled for "
    "KINGDOM HEARTS III. Run Patch Game to disable it automatically, or check the game's Steam properties and "
    "disable Steam Cloud before playing Archipelago."
)
KH3_STEAM_CLOUD_ACKNOWLEDGED_KEY = "steam_cloud_disabled_acknowledged"
KH3_STEAM_CLOUD_CONFIRMATION_TEXT = (
    "Before connecting, confirm that Steam Cloud is disabled for KINGDOM HEARTS III. Patch Game can disable the "
    "per-game setting automatically, or you can disable it in the game's Steam Properties. Steam Cloud can restore "
    "or overwrite save data while switching between Archipelago runs. Click OK only after it has been disabled."
)


@dataclass(frozen=True)
class SteamCloudCallbacks:
    detect: Callable[[Path], SteamCloudState]
    disable: Callable[[Path], SteamCloudDisableResult]
    load_settings: Callable[[], object]
    store_acknowledgement: Callable[[bool], None]
    print_json: Callable[[dict[str, Any]], None]
    warn: Callable[[], bool]


class SteamCloudClientService:
    def __init__(self, callbacks: SteamCloudCallbacks) -> None:
        self._client = callbacks
        self.warning_key: tuple[str, SteamCloudState] | None = None
        self.acknowledged = False
        self.confirmation_pending = False

    def warn(self, kh3_dir: Path) -> bool:
        root = normalize_kh3_root_dir(kh3_dir)
        if not is_likely_steam_kh3_root_dir(root):
            self.warning_key = None
            return False
        state = self._client.detect(root)
        if state is SteamCloudState.DISABLED:
            self.warning_key = None
            return False
        key = (os.path.normcase(str(root)), state)
        if self.warning_key == key:
            return False
        warning = KH3_STEAM_CLOUD_WARNING if state is SteamCloudState.ENABLED else KH3_STEAM_CLOUD_UNKNOWN_WARNING
        self._client.print_json({"cmd": "PrintJSON", "data": [{"text": warning, "type": "color", "color": "yellow"}]})
        self.warning_key = key
        return True

    def disable_for_patch(self, kh3_dir: Path) -> bool:
        root = normalize_kh3_root_dir(kh3_dir)
        if not is_likely_steam_kh3_root_dir(root):
            return True
        try:
            result = self._client.disable(root)
        except Exception as exc:
            detail = f"unexpected Steam API error: {exc}"
        else:
            if result.succeeded:
                state_text = "disabled" if result.changed else "already disabled"
                logger.info("Steam Cloud for KINGDOM HEARTS III is %s for the current Steam account.", state_text)
                self.warning_key = None
                self.acknowledged = True
                try:
                    self._client.store_acknowledgement(True)
                except Exception as exc:
                    logger.warning(
                        "Steam Cloud was disabled, but the KH3 client could not remember that result: %s", exc
                    )
                return True
            detail = result.detail or result.status.value.replace("_", " ")
        logger.warning(
            "Patch Game could not automatically disable Steam Cloud for KINGDOM HEARTS III: %s. "
            "Disable it manually in the game's Steam Properties before playing Archipelago.",
            detail,
        )
        self.warning_key = None
        self.acknowledged = False
        try:
            self._client.store_acknowledgement(False)
        except Exception as exc:
            logger.warning("Could not reset the Steam Cloud confirmation state: %s", exc)
        self._client.warn()
        return False

    def has_acknowledged(self) -> bool:
        if self.acknowledged:
            return True
        settings = self._client.load_settings()
        self.acknowledged = isinstance(settings, dict) and settings.get(KH3_STEAM_CLOUD_ACKNOWLEDGED_KEY) is True
        return self.acknowledged

    async def confirm(self, kh3_dir: Path | None, prompt: Callable[[], Awaitable[bool]] | None) -> bool:
        if kh3_dir is None or not is_likely_steam_kh3_root_dir(Path(kh3_dir)):
            return True
        if self.has_acknowledged():
            return True
        if self.confirmation_pending:
            logger.info("A Steam Cloud confirmation is already open; ignored the duplicate connection request.")
            return False
        if not callable(prompt):
            self._client.warn()
            logger.warning(
                "The Steam Cloud confirmation modal is unavailable in this client interface; continuing with the "
                "existing Steam Cloud warning."
            )
            return True
        self.confirmation_pending = True
        try:
            acknowledged = bool(await prompt())
        except Exception as exc:
            logger.warning("Could not show the Steam Cloud confirmation; the connection was cancelled: %s", exc)
            return False
        finally:
            self.confirmation_pending = False
        if not acknowledged:
            logger.info("KH3 connection cancelled because Steam Cloud was not acknowledged as disabled.")
            return False
        try:
            self._client.store_acknowledgement(True)
        except Exception as exc:
            logger.warning("Could not persist the Steam Cloud acknowledgement; the connection was cancelled: %s", exc)
            return False
        self.acknowledged = True
        return True
