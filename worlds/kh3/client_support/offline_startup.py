"""Coordinate deferred offline startup without applying stale worker results."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from ..Bridge import BridgeProtocolError

from .offline_bridge_service import OfflineBridgeInitializationInput, OfflineBridgeInitializationResult
from .run_manager import KH3RunConnectionProfile

logger = logging.getLogger("Client")


@dataclass(frozen=True)
class OfflineStartupCallbacks:
    metadata_reader: Callable[
        [], Callable[[OfflineBridgeInitializationInput], OfflineBridgeInitializationResult | None]
    ]
    profile_reader: Callable[[], Callable[[], KH3RunConnectionProfile | None]]
    still_current: Callable[[OfflineBridgeInitializationInput], bool]
    installed_paths: Callable[[], frozenset[Path]]
    enroll: Callable[[OfflineBridgeInitializationResult], Awaitable[None]]
    apply_bridge: Callable[[OfflineBridgeInitializationResult], bool]
    apply_profile: Callable[[KH3RunConnectionProfile], None]


class OfflineStartupService:
    def __init__(self, callbacks: OfflineStartupCallbacks) -> None:
        self._client = callbacks

    async def initialize(self, source: OfflineBridgeInitializationInput) -> bool:
        try:
            return await self._initialize(source)
        except (BridgeProtocolError, ValueError) as exc:
            logger.warning("KH3 offline seed metadata could not be loaded: %s. "
                           "The client remains open; select a compatible run or connect to the room.", exc)
            return False

    async def _initialize(self, source: OfflineBridgeInitializationInput) -> bool:
        result, active_profile = await asyncio.gather(
            asyncio.to_thread(self._client.metadata_reader(), source),
            asyncio.to_thread(self._client.profile_reader()),
        )
        if not self._client.still_current(source):
            logger.info("Skipped deferred KH3 offline bridge setup because client state changed during startup.")
            return False
        initialized = False
        if result is not None:
            if result.pak_path.expanduser().resolve() not in self._client.installed_paths():
                logger.info("Skipped deferred KH3 offline bridge setup because the installed seed pak changed.")
                return False
            await self._client.enroll(result)
            if not self._client.still_current(source):
                logger.info("Skipped offline setup because client state changed while storing the seed.")
                return False
            if active_profile is None:
                active_profile = await asyncio.to_thread(self._client.profile_reader())
            initialized = self._client.apply_bridge(result)
        if active_profile is not None:
            self._client.apply_profile(active_profile)
        return initialized
