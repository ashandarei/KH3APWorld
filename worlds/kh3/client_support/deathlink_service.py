"""AP DeathLink networking around the DLL-owned gameplay state machine."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path

from ..bridge_support.deathlink import DeathLinkService as NetworkCustody
from ..bridge_support.files import atomic_write_json
from ..bridge_support.store import BridgeStore
from ..transport.pipe_transport import transport_for


class DeathLinkService:
    """Send native outgoing intents and submit AP deaths/settings over live IPC."""
    def __init__(self, *, send_death: Callable[[str], Awaitable[None]], deathlink_enabled: Callable[[], bool],
                 has_player: Callable[[], bool], server_connected: Callable[[], bool],
                 read_settings, update_enabled=None, log_info=lambda *a: None,
                 log_warning=lambda *a: None, log_exception=lambda *a: None):
        self.send_death = send_death
        self.deathlink_enabled = deathlink_enabled
        self.has_player = has_player
        self.server_connected = server_connected
        self.read_settings = read_settings
        self.update_enabled = update_enabled
        self.log_info, self.log_warning, self.log_exception = log_info, log_warning, log_exception
        self._stores = {}
        self._last_control = None
        self._last_payload = None

    def custody(self, root):
        root = Path(root)
        if root not in self._stores:
            self._stores[root] = NetworkCustody(BridgeStore(root))
        return self._stores[root]

    async def refresh_settings(self, bridge_dir):
        enabled, _ = self.read_settings(bridge_dir)
        if enabled != self.deathlink_enabled() and self.update_enabled is not None:
            await self.update_enabled(enabled)

    def handle_incoming(self, bridge_dir, data):
        if bridge_dir is None:
            self.log_warning("KH3 DeathLink received before bridge directory was initialized.")
            return
        if not self.read_settings(bridge_dir)[0]:
            return
        try:
            result = self.custody(bridge_dir).queue(data)
            self.log_info("Submitted KH3 DeathLink network intent: %s", result["request_id"])
        except Exception as exc:
            self.log_exception("Failed to submit KH3 DeathLink: %s", exc)

    def mark_existing_game_over_processed(self, bridge_dir):
        # Native observations already own the current episode. A reconnect
        # republishes the network binding without inspecting a death screen.
        self._last_control = None

    async def handle_runtime_game_over(self, bridge_dir):
        if bridge_dir is None:
            return
        await self.refresh_settings(bridge_dir)
        transport = transport_for(Path(bridge_dir) / "runtime_deathlink_state.json")
        if not transport:
            return
        payload = transport.read_view("runtime_deathlink_state.json")
        custody = self.custody(bridge_dir)
        identity = custody.identity()
        runtime = custody.store.read_optional_json_view("bridge_runtime_status.json") or {}
        if (not isinstance(payload, dict) or payload.get("schema") != "kh3ap.deathlink_state.v1"
                or payload.get("owner") != "dll" or type(payload.get("process_id")) is not int
                or payload["process_id"] <= 0 or payload["process_id"] != runtime.get("kh3_process_pid")
                or any(payload.get(k) != v for k, v in identity.items())):
            return
        if payload.get("ready") is not True:
            raise ValueError(f"Native DeathLink unavailable: {payload.get('reason')}")
        if payload is not self._last_payload:
            ids = payload.get("accepted_ids")
            outbox = payload.get("outgoing")
            if (not isinstance(ids, list) or len(ids) > 4096 or any(not isinstance(i, str) for i in ids)
                    or not isinstance(outbox, list) or len(outbox) > 256
                    or any(not isinstance(i, dict) or not isinstance(i.get("id"), str)
                           or not i["id"] or not isinstance(i.get("cause"), str) for i in outbox)):
                raise ValueError("Invalid native DeathLink network intents")
            custody.accepted(ids)
            self._last_payload = payload
        network = custody.network()
        if self.deathlink_enabled() and self.has_player() and self.server_connected():
            for event in payload["outgoing"]:
                if event["id"] not in network["sent_ids"]:
                    # Commit networking success before asking the DLL to
                    # retire this intent, so reconnects can resend its ACK.
                    await self.send_death(event["cause"])
                    network = custody.sent(event["id"])
        enabled, amnesty = self.read_settings(bridge_dir)
        control = {"schema": "kh3ap.deathlink_control.v1", **identity,
                   "process_id": payload["process_id"], "enabled": enabled, "amnesty": amnesty,
                   "online": bool(self.has_player() and self.server_connected()),
                   "incoming": network["incoming"], "sent_ids": network["sent_ids"]}
        signature = (Path(bridge_dir), transport.connection_generation, control)
        if signature != self._last_control:
            atomic_write_json(Path(bridge_dir) / "runtime_deathlink_control.json", control)
            self._last_control = signature
