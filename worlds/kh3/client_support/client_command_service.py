"""Pure planning for KH3 bridge and GUI client commands."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any

CLIENT_COMMAND_FILE = "ap_client_command.json"


class ClientCommandKind(str, Enum):
    """Commands accepted at the production bridge and GUI boundary."""

    SAY = "say"
    RESYNC_BRIDGE_FILES = "resync_bridge_files"
    OPEN_BRIDGE_DIRECTORY = "open_bridge_directory"
    OPEN_KH3_MODS_DIRECTORY = "open_kh3_mods_directory"
    OPEN_KH3_DIRECTORY = "open_kh3_directory"
    OPEN_KH3_SAVE_DIRECTORY = "open_kh3_save_directory"
    OPEN_PROBE_CONFIG_DIRECTORY = "open_probe_config_directory"
    LAUNCH_KH3 = "launch_kh3"
    INSTALL_KH3_RUNTIME = "install_kh3_runtime"
    DOWNLOAD_GOA_MOD = "download_goa_mod"
    EXPORT_DEBUG_BUNDLE = "export_debug_bundle"


class ClientCommandActionKind(str, Enum):
    """Ordered effects applied by the client command executor."""

    SEND_SAY = "send_say"
    ACKNOWLEDGE = "acknowledge"
    DELETE_COMMAND_FILE = "delete_command_file"
    WRITE_BRIDGE_FILES = "write_bridge_files"
    OPEN_BRIDGE_DIRECTORY = "open_bridge_directory"
    OPEN_KH3_MODS_DIRECTORY = "open_kh3_mods_directory"
    OPEN_KH3_DIRECTORY = "open_kh3_directory"
    OPEN_KH3_SAVE_DIRECTORY = "open_kh3_save_directory"
    OPEN_PROBE_CONFIG_DIRECTORY = "open_probe_config_directory"
    LAUNCH_KH3 = "launch_kh3"
    INSTALL_KH3_RUNTIME = "install_kh3_runtime"
    DOWNLOAD_GOA_MOD = "download_goa_mod"
    EXPORT_DEBUG_BUNDLE = "export_debug_bundle"
    LOG_INFO = "log_info"
    LOG_WARNING = "log_warning"


_FACADE_ACTION_KIND_BY_COMMAND = {
    ClientCommandKind.OPEN_KH3_MODS_DIRECTORY: ClientCommandActionKind.OPEN_KH3_MODS_DIRECTORY,
    ClientCommandKind.OPEN_KH3_DIRECTORY: ClientCommandActionKind.OPEN_KH3_DIRECTORY,
    ClientCommandKind.OPEN_KH3_SAVE_DIRECTORY: ClientCommandActionKind.OPEN_KH3_SAVE_DIRECTORY,
    ClientCommandKind.OPEN_PROBE_CONFIG_DIRECTORY: ClientCommandActionKind.OPEN_PROBE_CONFIG_DIRECTORY,
    ClientCommandKind.LAUNCH_KH3: ClientCommandActionKind.LAUNCH_KH3,
    ClientCommandKind.INSTALL_KH3_RUNTIME: ClientCommandActionKind.INSTALL_KH3_RUNTIME,
    ClientCommandKind.DOWNLOAD_GOA_MOD: ClientCommandActionKind.DOWNLOAD_GOA_MOD,
    ClientCommandKind.EXPORT_DEBUG_BUNDLE: ClientCommandActionKind.EXPORT_DEBUG_BUNDLE,
}


@dataclass(frozen=True)
class ClientCommandIdentity:
    """The stable sequence identity used by the existing one-shot protocol."""

    sequence: int


@dataclass(frozen=True)
class BridgeCommandInput:
    """State needed to validate and plan one bridge command payload."""

    last_processed_sequence: int
    ap_connected: bool


@dataclass(frozen=True)
class FacadeCommandInput:
    """State needed to plan one compatibility-facade command."""

    bridge_initialized: bool = False
    probe_configured: bool = True
    game_running: bool = False


@dataclass(frozen=True)
class ClientCommandAction:
    """One explicitly ordered effect in a command plan."""

    kind: ClientCommandActionKind
    text: str = ""
    sequence: int | None = None
    log_args: tuple[object, ...] = ()


@dataclass(frozen=True)
class ClientCommandPlan:
    """Immutable validation and dispatch result for a client command."""

    identity: ClientCommandIdentity | None = None
    command: ClientCommandKind | None = None
    actions: tuple[ClientCommandAction, ...] = ()

    @property
    def acknowledged(self) -> bool:
        return any(action.kind is ClientCommandActionKind.ACKNOWLEDGE for action in self.actions)

class ClientCommandService:
    """Load, validate, deduplicate, and plan KH3 client commands."""

    def __init__(self, payload_loader: Callable[[str], Mapping[str, Any] | None]) -> None:
        self._payload_loader = payload_loader

    def load_bridge_command(self, source: BridgeCommandInput) -> ClientCommandPlan:
        """Load the established sidecar and plan its ordered effects."""

        return self.plan_bridge_payload(self._payload_loader(CLIENT_COMMAND_FILE), source)

    @staticmethod
    def plan_bridge_payload(
        payload: Mapping[str, Any] | None,
        source: BridgeCommandInput,
    ) -> ClientCommandPlan:
        """Validate a bridge payload without performing AP or filesystem effects."""

        if not payload:
            return ClientCommandPlan()
        try:
            sequence = int(payload.get("seq", 0))
        except (TypeError, ValueError):
            return ClientCommandPlan()

        identity = ClientCommandIdentity(sequence)
        if sequence <= source.last_processed_sequence:
            return ClientCommandPlan(identity=identity)

        command_text = str(payload.get("cmd") or "").strip().lower()
        text = str(payload.get("text") or "").strip()
        if command_text != ClientCommandKind.SAY.value or not text:
            return ClientCommandPlan(
                identity=identity,
                actions=ClientCommandService._acknowledgement_actions(sequence),
            )
        if not source.ap_connected:
            return ClientCommandPlan(identity=identity, command=ClientCommandKind.SAY)

        return ClientCommandPlan(
            identity=identity,
            command=ClientCommandKind.SAY,
            actions=(
                ClientCommandAction(ClientCommandActionKind.SEND_SAY, text=text),
                ClientCommandAction(
                    ClientCommandActionKind.LOG_INFO,
                    text="Sent KH3 AP command: %s",
                    log_args=(text,),
                ),
                *ClientCommandService._acknowledgement_actions(sequence),
            ),
        )

    @staticmethod
    def _acknowledgement_actions(sequence: int) -> tuple[ClientCommandAction, ...]:
        return (
            ClientCommandAction(ClientCommandActionKind.ACKNOWLEDGE, sequence=sequence),
            ClientCommandAction(ClientCommandActionKind.DELETE_COMMAND_FILE, text=CLIENT_COMMAND_FILE),
        )

    @staticmethod
    def plan_facade_command(
        command: ClientCommandKind,
        source: FacadeCommandInput = FacadeCommandInput(),
    ) -> ClientCommandPlan:
        """Plan a command invoked through an existing KH3Context/GUI entrypoint."""

        if command is ClientCommandKind.RESYNC_BRIDGE_FILES:
            actions = ClientCommandService._plan_resync(source)
        elif command is ClientCommandKind.OPEN_BRIDGE_DIRECTORY:
            actions = ClientCommandService._plan_open_bridge(source)
        elif command is ClientCommandKind.OPEN_PROBE_CONFIG_DIRECTORY and not source.probe_configured:
            actions = (
                ClientCommandAction(
                    ClientCommandActionKind.LOG_INFO,
                    text="KH3APProbeCpp config path is not configured.",
                ),
            )
        elif command is ClientCommandKind.INSTALL_KH3_RUNTIME and source.game_running:
            actions = (
                ClientCommandAction(
                    ClientCommandActionKind.LOG_WARNING,
                    text=(
                        "Close KH3 before patching the game. Patch Game cannot safely update runtime files "
                        "while KH3 is running."
                    ),
                ),
            )
        else:
            actions = (ClientCommandAction(_FACADE_ACTION_KIND_BY_COMMAND[command]),)
        return ClientCommandPlan(command=command, actions=actions)

    @staticmethod
    def _plan_resync(source: FacadeCommandInput) -> tuple[ClientCommandAction, ...]:
        if not source.bridge_initialized:
            return (
                ClientCommandAction(
                    ClientCommandActionKind.LOG_INFO,
                    text="KH3 bridge directory is not initialized yet.",
                ),
            )
        return (
            ClientCommandAction(ClientCommandActionKind.WRITE_BRIDGE_FILES),
            ClientCommandAction(ClientCommandActionKind.LOG_INFO, text="Rewrote KH3 bridge files."),
        )

    @staticmethod
    def _plan_open_bridge(source: FacadeCommandInput) -> tuple[ClientCommandAction, ...]:
        if not source.bridge_initialized:
            return (
                ClientCommandAction(
                    ClientCommandActionKind.LOG_INFO,
                    text="KH3 bridge directory is not initialized yet.",
                ),
            )
        return (ClientCommandAction(ClientCommandActionKind.OPEN_BRIDGE_DIRECTORY),)


__all__ = [
    "CLIENT_COMMAND_FILE",
    "BridgeCommandInput",
    "ClientCommandAction",
    "ClientCommandActionKind",
    "ClientCommandIdentity",
    "ClientCommandKind",
    "ClientCommandPlan",
    "ClientCommandService",
    "FacadeCommandInput",
]
