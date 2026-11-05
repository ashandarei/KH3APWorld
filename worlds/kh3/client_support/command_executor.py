"""Execute client commands and preserve acknowledgement order."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import Utils

from ..platform_support.game_launch import launch_kh3_game
from ..platform_support.path_opening import open_path
from .client_command_service import ClientCommandAction, ClientCommandPlan
from .client_command_service import ClientCommandActionKind as Kind
from .goa_download import download_goa_mod, goa_mod_status_cache_path

logger = logging.getLogger("Client")

@dataclass(frozen=True)
class CommandExecutionCallbacks:
    bridge: Callable[[], Path | None]
    root: Callable[[], Path]
    mods: Callable[[], Path]
    saves: Callable[[], Path]
    probe_directory: Callable[[], Path]
    write_bridge: Callable[[], object]
    delete_sidecar: Callable[[str], None]
    install_runtime: Callable[[], None]
    invalidate_goa: Callable[[], None]
    export_debug: Callable[[], None]
    send: Callable[[list[Any]], Awaitable[None]]
    acknowledge: Callable[[int], None]


class ClientCommandExecutor:
    def __init__(self, callbacks: CommandExecutionCallbacks) -> None:
        self._client = callbacks

    def apply_plan(self, plan: ClientCommandPlan) -> None:
        for action in plan.actions:
            self.apply(action)

    def apply(self, action: ClientCommandAction) -> None:
        kind = action.kind
        if kind in {Kind.LOG_INFO, Kind.LOG_WARNING}:
            log = logger.info if kind is Kind.LOG_INFO else logger.warning
            log(action.text, *action.log_args)
        elif kind is Kind.OPEN_BRIDGE_DIRECTORY:
            try:
                Utils.open_file(self._client.bridge())
            except Exception as exc:
                logger.info("Could not open KH3 bridge directory: %s", exc)
        elif kind is Kind.LAUNCH_KH3:
            launch_kh3_game(self._client.root())
        elif kind is Kind.DOWNLOAD_GOA_MOD:
            try:
                download_goa_mod(self._client.mods(), cache_path=goa_mod_status_cache_path())
            finally:
                self._client.invalidate_goa()
        else:
            self._apply_path_or_callback(kind)

    def _apply_path_or_callback(self, kind: Kind) -> None:
        directories = {
            Kind.OPEN_KH3_MODS_DIRECTORY: (self._client.mods, "KH3 mods directory"),
            Kind.OPEN_KH3_DIRECTORY: (self._client.root, "KH3 directory"),
            Kind.OPEN_KH3_SAVE_DIRECTORY: (self._client.saves, "KH3 save directory"),
            Kind.OPEN_PROBE_CONFIG_DIRECTORY: (self._client.probe_directory, "KH3 probe config directory"),
        }
        if kind in directories:
            path, description = directories[kind]
            open_path(path(), description=description, create_directory=True)
            return
        callback = {
            Kind.WRITE_BRIDGE_FILES: self._client.write_bridge,
            Kind.INSTALL_KH3_RUNTIME: self._client.install_runtime,
            Kind.EXPORT_DEBUG_BUNDLE: self._client.export_debug,
        }.get(kind)
        if callback is not None:
            callback()

    async def apply_bridge_plan(self, plan: ClientCommandPlan) -> None:
        for action in plan.actions:
            if action.kind is Kind.SEND_SAY:
                await self._client.send([{"cmd": "Say", "text": action.text}])
            elif action.kind is Kind.LOG_INFO:
                logger.info(action.text, *action.log_args)
            elif action.kind is Kind.ACKNOWLEDGE and action.sequence is not None:
                self._client.acknowledge(action.sequence)
            elif action.kind is Kind.DELETE_COMMAND_FILE:
                self._client.delete_sidecar(action.text)
