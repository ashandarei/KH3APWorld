"""Remembered server normalization and ordered connection-profile updates."""

from __future__ import annotations

import asyncio
import logging
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass

from .run_manager import KH3RunInfo, KH3RunManagerError, KH3RunManagerService

logger = logging.getLogger("Client")


def normalize_server_address(server_address: str | None) -> str | None:
    raw_address = str(server_address or "").strip()
    if not raw_address:
        return None
    parse_target = raw_address if "://" in raw_address else f"ws://{raw_address}"
    try:
        parsed = urllib.parse.urlparse(parse_target)
        hostname = parsed.hostname
        port = parsed.port or 38281
    except ValueError:
        return None
    if not hostname:
        return None
    display_host = f"[{hostname}]" if ":" in hostname and not hostname.startswith("[") else hostname
    scheme = "wss://" if parsed.scheme.casefold() == "wss" else ""
    return f"{scheme}{display_host}:{port}"


@dataclass(frozen=True)
class RunConnectionCallbacks:
    begin: Callable[[str], bool]
    finish: Callable[[], None]
    is_busy: Callable[[], bool]
    busy_label: Callable[[], str]
    apply_active: Callable[[KH3RunInfo], None]


class RunConnectionService:
    def __init__(self, manager: Callable[[], KH3RunManagerService], callbacks: RunConnectionCallbacks) -> None:
        self._manager = manager
        self._client = callbacks

    async def update(
        self,
        run_id: str,
        *,
        slot_name: str,
        server: str,
        port: str,
        room_url: str,
        show_activity: bool = True,
    ) -> None:
        activity_started = False
        if show_activity:
            if not self._client.begin("Updating KH3 run connection..."):
                return
            activity_started = True
        elif self._client.is_busy():
            logger.info("KH3 run action is already running: %s", self._client.busy_label())
            return
        try:
            updated = await asyncio.to_thread(
                self._manager().update_connection_profile,
                run_id,
                slot_name=slot_name,
                server=server,
                port=port,
                room_url=room_url,
            )
            if updated.active:
                self._client.apply_active(updated)
            logger.info(
                "Updated KH3 run connection profile for %s: slot %s, server %s.",
                updated.slot_name,
                updated.effective_slot_name,
                updated.server_address,
            )
        except Exception as exc:
            logger.warning(
                "Could not update KH3 run connection profile: %s", exc, exc_info=not isinstance(exc, KH3RunManagerError)
            )
        finally:
            if activity_started:
                self._client.finish()
