"""Order KH3 connection preparation, slot prompting, and authentication recovery."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from .seed_import_service import KH3SeedPreparationResult

logger = logging.getLogger("Client")
SlotPrompt = Callable[..., Awaitable[str | None]]


@dataclass(frozen=True)
class ConnectionState:
    auth: str | None
    username: str | None
    password: str | None
    managed_slot_name: str
    bootstrap_pending: bool
    pre_auth_blocked: bool


@dataclass(frozen=True)
class ConnectionFlowCallbacks:
    state: Callable[[], ConnectionState]
    confirm_cloud: Callable[[], Awaitable[bool]]
    disconnect: Callable[[], Awaitable[None]]
    reset_attempt: Callable[[], None]
    prepare_seed: Callable[[], Awaitable[KH3SeedPreparationResult | None]]
    has_installed_pak: Callable[[], bool]
    game_running: Callable[[], bool]
    warn: Callable[[str], None]
    prompt_slot: Callable[[], Awaitable[bool]]
    set_bootstrap: Callable[[bool], None]
    guard_pak: Callable[[], bool]
    prepare_connection: Callable[[], None]
    start_server: Callable[[str | None], None]
    clear_prompt_profile: Callable[[], None]
    text_prompt: Callable[[], SlotPrompt | None]
    set_auth: Callable[[str | None, bool], None]
    clear_username: Callable[[], None]
    get_username: Callable[[], Awaitable[None]]
    infer_slot: Callable[[], str | None]
    send_connect: Callable[[], Awaitable[None]]
    suspend_run: Callable[[str], Awaitable[bool]]
    close_socket: Callable[[], Awaitable[None]]
    block_pre_auth: Callable[[], None]
    allow_reconnect: Callable[[], None]
    clear_recovery_pending: Callable[[], None]


class ConnectionFlowService:
    def __init__(self, callbacks: ConnectionFlowCallbacks) -> None:
        self._client = callbacks

    async def connect(self, address: str | None) -> None:
        if not await self._client.confirm_cloud():
            return
        await self._client.disconnect()
        self._client.reset_attempt()
        preparation = await self._client.prepare_seed()
        if preparation is None:
            return
        if preparation.status == "no_source" and not self._client.has_installed_pak():
            if self._client.game_running():
                self._client.warn(
                    "Close KINGDOM HEARTS III before connecting without a seed pak. "
                    "The client must generate and install the room's pak after authentication."
                )
                return
            if not await self._client.prompt_slot():
                return
            self._client.set_bootstrap(True)
        elif not self._client.guard_pak():
            return
        self._client.prepare_connection()
        self._client.start_server(address)

    async def prompt_slot(self) -> bool:
        state = self._client.state()
        previous_name = str(state.auth or state.username or "").strip()
        self._client.clear_prompt_profile()
        prompt = self._client.text_prompt()
        if callable(prompt):
            slot_name = await prompt(
                title="KH3 Slot Name",
                text=(
                    "No KH3 seed pak is installed. Enter the exact slot name from the room. "
                    "After authentication, the client will download or generate and install this slot's pak."
                ),
                hint_text="Slot name",
                initial_text=previous_name,
                submit_text="Connect",
            )
            slot_name = str(slot_name or "").strip()
            if not slot_name:
                logger.info("KH3 connection cancelled before requesting a server seed pak.")
                return False
            self._client.set_auth(slot_name, False)
            return True
        self._client.clear_username()
        await self._client.get_username()
        auth = str(self._client.state().auth or "").strip() or None
        self._client.set_auth(auth, False)
        return auth is not None

    async def authenticate(self, password_requested: bool, *, parent_auth: Callable[[bool], Awaitable[None]]) -> None:
        if not await self._prepare_authentication():
            return
        if password_requested and not self._client.state().password:
            await parent_auth(password_requested)
        state = self._client.state()
        if state.bootstrap_pending and not state.auth:
            if not await self._client.prompt_slot():
                await self._client.disconnect()
                return
        elif not state.auth:
            self._select_auth_slot(state)
        await self._client.get_username()
        await self._client.send_connect()

    async def _prepare_authentication(self) -> bool:
        if self._client.state().pre_auth_blocked:
            return False
        if not self._client.state().bootstrap_pending:
            preparation = await self._client.prepare_seed()
            if preparation is None or not self._client.guard_pak():
                await self._client.disconnect()
                return False
        return True

    def _select_auth_slot(self, state: ConnectionState) -> None:
        slot_name = state.managed_slot_name
        inferred = False
        if not slot_name:
            slot_name = self._client.infer_slot()
            inferred = bool(slot_name)
        if slot_name:
            self._client.set_auth(slot_name, inferred)
            if inferred:
                logger.info("Using KH3 slot name from installed AP pak: %s", slot_name)

    async def guard_room(self, *, seed_mismatch: bool, affinity_mismatch: bool, seed_name: Any) -> None:
        if not seed_mismatch and not affinity_mismatch:
            return
        if seed_mismatch:
            logger.info(
                "The installed KH3 seed pak does not match room seed %s; preserving the old run before authentication.",
                seed_name,
            )
            reason = "it does not match the selected room"
        else:
            logger.info(
                "The installed KH3 seed is being opened through a different server hostname; preserving its "
                "existing server profile before authentication."
            )
            reason = "the selected server hostname uses a separate save profile"
        if not await self._client.suspend_run(reason):
            self._client.block_pre_auth()
            await self._client.close_socket()
            return
        self._client.set_bootstrap(True)
        logger.info("Previous KH3 seed pak preserved. Enter this room's exact KH3 slot name to continue.")

    async def recover_rejected_slot(self) -> None:
        try:
            if not await self._client.suspend_run("the server rejected its inferred slot name"):
                await self._client.close_socket()
                return
            self._client.set_bootstrap(True)
            if not await self._client.prompt_slot():
                await self._client.close_socket()
                return
            self._client.allow_reconnect()
            logger.info("Retrying KH3 authentication with the slot name entered in the dialog.")
            await self._client.send_connect()
        finally:
            self._client.clear_recovery_pending()
