"""Client-side MoM catalog lifetime, serialized I/O, and result routing.

The challenge service owns files and validates game results. This coordinator
owns its lifetime and rechecks the current client route after worker I/O so a
room switch cannot send old results to the new room. It never receives a client
context; the adapter supplies current snapshots and effects.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .json_snapshot import JsonArray, freeze_json

if TYPE_CHECKING:
    from .mom_challenges import MoMChallengeService

logger = logging.getLogger("Client")
CurrentChallenge = Callable[[], "MoMChallengeService | None"]
StartTask = Callable[[Coroutine[Any, Any, None]], None]
SendMessages = Callable[[list[dict[str, Any]]], Awaitable[None]]


@dataclass(frozen=True)
class MoMClientRoute:
    """Current AP identity and acknowledged checks, detached before worker I/O."""

    barrier_dir: Path
    slot: int | None
    seed_name: str | None
    checked_locations: frozenset[int]

    def matches(self, service: MoMChallengeService) -> bool:
        return self.slot == service.scope["slot"] and self.seed_name == service.scope["seed_name"]


class MoMClientService:
    def __init__(self) -> None:
        self.launch_running = False
        self._io_lock = asyncio.Lock()
        self._catalog_source: JsonArray | None = None
        self._catalog_rows: Any = None
        self._service: MoMChallengeService | None = None
        self._service_key: tuple[Any, ...] | None = None

    def challenge(self, bridge_dir: Path | None, slot_data: dict[str, Any]) -> MoMChallengeService | None:
        from .mom_challenges import MoMChallengeService, challenge_scope

        locations = slot_data.get("bridge", {}).get("locations", [])
        if isinstance(locations, JsonArray) and locations is self._catalog_source:
            rows = self._catalog_rows
        else:
            rows = [
                row
                for row in locations
                if row.get("type") == "melody_of_memory" or row.get("music_chest_kind") == "vanilla"
            ]
            if isinstance(locations, JsonArray):
                rows = freeze_json(rows)
                self._catalog_source = locations
                self._catalog_rows = rows
        if not bridge_dir or not rows:
            self._service = None
            self._service_key = None
            return None
        key = (str(bridge_dir), challenge_scope(slot_data), rows)
        if key != self._service_key:
            self._service = MoMChallengeService(Path(bridge_dir), slot_data)
            # Mutable catalogs need a detached comparison snapshot; immutable
            # catalogs can reuse their filtered rows without another traversal.
            self._service_key = (key[0], freeze_json(key[1]), freeze_json(rows))
        return self._service

    def launch(self, location_id: int, *, current: CurrentChallenge, start_task: StartTask) -> None:
        if self.launch_running:
            return
        service = current()
        if service is None or location_id not in service.challenges:
            return
        self.launch_running = True
        start_task(self._launch(service, location_id, current))

    async def _launch(self, service: MoMChallengeService, location_id: int, current: CurrentChallenge) -> None:
        try:
            async with self._io_lock:
                if service is not current():
                    return
                await asyncio.to_thread(service.launch, location_id)
        except Exception:
            logger.exception("Could not launch the MoM challenge.")
        finally:
            self.launch_running = False

    async def poll(
        self,
        *,
        current: CurrentChallenge,
        route: Callable[[], MoMClientRoute],
        is_connected: Callable[[], bool],
        launch: Callable[[int], None],
        send: SendMessages,
    ) -> None:
        if self.launch_running:
            return
        service = current()
        if service is None:
            return
        async with self._io_lock:
            completed = await asyncio.to_thread(service.poll)
            if service is current():
                requested = await asyncio.to_thread(self._sync_barrier, service, route())
                if (
                    requested is not None
                    and service is current()
                    and requested not in completed | route().checked_locations
                ):
                    launch(requested)
        # A room/seed switch while disk I/O was pending must not send to the new room.
        if service is not current() or not is_connected():
            return
        destination = route()
        if not destination.matches(service):
            return
        pending = service.pending_checks(completed) - destination.checked_locations
        if pending:
            await send([{"cmd": "LocationChecks", "locations": sorted(pending)}])

    @staticmethod
    def _sync_barrier(service: MoMChallengeService, route: MoMClientRoute) -> int | None:
        from .mom_barrier import consume_interaction, publish_clear

        checked = set(route.checked_locations) if route.matches(service) else set()
        service.publish_clears(route.barrier_dir)
        publish_clear(service, route.barrier_dir, checked)
        return consume_interaction(service, route.barrier_dir)
