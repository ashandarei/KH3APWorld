"""Archipelago adapter, compatibility facade, and entrypoint for the KH3 client."""

from __future__ import annotations

import asyncio
import sys
import threading
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import Utils
from CommonClient import (
    ClientCommandProcessor,
    CommonContext,
    gui_enabled,
    logger,
)
from NetUtils import ClientStatus
from .client_support.network import server_loop

from .Bridge import (
    BRIDGE_PROTOCOL_NAME,
    BRIDGE_PROTOCOL_VERSION,
    KH3_WORLD_VERSION,
    BridgeProtocolError,
    validate_protocol_payload,
    validate_slot_data_payload,
)
from .bridge_support.files import (
    atomic_write_json,
    file_signature,
    load_optional_json_file,
    load_optional_json_file_once,
    load_optional_json_view,
    safe_unlink,
)
from .bridge_support.paths import RUNTIME_CURRENT_SAVE_SLOT_FILE
from .bridge_support.runtime_snapshots import NATIVE_RUNTIME_SNAPSHOT_SCHEMAS, bound_runtime_snapshot
from .BridgeSession import KH3BridgeSession
from .CharacterUnlocks import CHARACTER_UNLOCK_ITEM_ALIASES
from .client_support import bridge_sync_service as bridge_sync_support
from .client_support import client_command_service as client_command_support
from .client_support import client_transport_service as client_transport_support
from .client_support import deathlink_service as deathlink_support
from .client_support import deathlink_settings
from .client_support import item_sync_service as item_sync_support
from .client_support import launch_preparation as launch_preparation_support
from .client_support import save_service as save_service_support
from .client_support import seed_guard as seed_guard_support
from .client_support import seed_policy_service as seed_policy_support
from .client_support import steam_cloud_client as steam_cloud_support
from .client_support.bridge_publication import BridgePublicationService, BridgeRefreshCallbacks, BridgeRefreshInput
from .client_support.bridge_publisher import OrderedBridgePublisher
from .client_support.bridge_runtime import (
    BridgePollCallbacks,
    BridgeWorkerOptions,
    run_integrated_bridge_worker,
    watch_bridge,
)
from .client_support.bridge_status_payload import BridgeStatusInput, build_bridge_status_payload
from .client_support.cli import parse_launch_arguments
from .client_support.client_packets import (
    ClientPacketCallbacks,
    ClientPacketService,
    ConnectedSetupCallbacks,
    ConnectedSetupService,
)
from .client_support.client_paths import ClientPaths, resolve_client_paths, resolve_game_root_paths
from .client_support.command_executor import (
    ClientCommandExecutor,
    CommandExecutionCallbacks,
)
from .client_support.connected_run import (
    AutoActivationCallbacks,
    AutoActivationRequest,
    ConnectedRunCallbacks,
    ConnectedRunService,
    SuspensionCallbacks,
)
from .client_support.connected_seed import ConnectedSeedRequest, ConnectedSeedService
from .client_support.connection_flow import ConnectionFlowCallbacks, ConnectionFlowService, ConnectionState
from .client_support.debug_bundle import export_kh3_debug_bundle
from .client_support.goa_download import (
    KH3_GOA_MOD_PATTERN,
    goa_mod_install_status,
    goa_mod_status_cache_path,
)
from .client_support.json_snapshot import freeze_slot_location_catalog
from .client_support.lifecycle_service import BridgeLifecycleService, LifecycleState, SaveDirectoryRestartCallbacks
from .client_support.mom_client import MoMClientRoute, MoMClientService
from .client_support.notification_service import ClientNotificationService, NotificationState
from .client_support.offline_bridge_service import (
    InstalledPakDiscoveryInput,
    OfflineBridgeInitializationInput,
    OfflineBridgeInitializationResult,
    OfflineBridgeService,
)
from .client_support.offline_startup import OfflineStartupCallbacks, OfflineStartupService
from .client_support.olympus_coliseum_compat import OlympusColiseumCompatResult
from .client_support.platform_seed_download import (
    KH3PlatformSeedDownloadService,
    kh3_platform_room_fallback_url,
    kh3_room_page_url_from_slot_data,
    kh3_slot_data_with_connected_room_url,
    normalize_kh3_room_page_url,
    open_kh3_room_page_url,
)
from .client_support.poptracker import PopTrackerSyncService, TrackerCallbacks, TrackerRoute
from .client_support.presentation import ClientStatusPresenter
from .client_support.recovery_commands import RecoveryCommandService
from .client_support.run_actions import ActiveRunSaves, RunActionCallbacks, RunActionService, RunSelection
from .client_support.run_connection import RunConnectionCallbacks, RunConnectionService, normalize_server_address
from .client_support.run_manager import (
    KH3RunActivationResult,
    KH3RunConnectionProfile,
    KH3RunDeleteResult,
    KH3RunInfo,
    KH3RunManagerError,
    KH3RunManagerService,
    KH3RunSuspensionResult,
    kh3_run_connection_affinity,
)
from .client_support.run_pruning import RunPruneCallbacks, RunPruningService
from .client_support.runtime_artifact_executor import RuntimeArtifactCallbacks, RuntimeArtifactExecutor
from .client_support.runtime_artifact_service import (
    ArtifactStatusInput,
    RuntimeArtifactPlan,
    RuntimeArtifactService,
)
from .client_support.secret_report_hint_service import SecretReportHintCallbacks, SecretReportHintService
from .client_support.seed_import_service import (
    KH3SeedImportResult,
    KH3SeedImportService,
    KH3SeedPreparationResult,
)
from .client_support.seed_paks import (
    KH3_AP_SEED_PAK_PATTERN,
    deactivate_incompatible_seed_paks,
    infer_kh3_ap_slot_name_from_pak_name,
    load_installed_kh3_ap_seed_metadata,
    seed_pak_metadata_cache_path,
)
from .client_support.seed_preparation import SeedPreparationCallbacks, SeedPreparationService
from .client_support.status_snapshot_service import ClientStatusSnapshotService
from .Data import (
    resolve_item_display_name,
)
from .ExternalBridge import (
    KH3_PROCESS_NAME,
    is_process_running,
)
from .platform_support.game_discovery import (
    is_likely_steam_kh3_root_dir,
)
from .platform_support.path_opening import open_path
from .platform_support.save_discovery import (
    is_kh3_auto_save_dir,
    is_kh3_save_dir_dynamic_root,
    resolve_kh3_save_dir_for_root,
    resolve_kh3_save_dir_input,
)
from .platform_support.steam_cloud import (
    detect_kh3_steam_cloud_state,
    disable_kh3_steam_cloud,
)
from .RuntimeProfiler import PythonProfileScope, configure_python_profiler
from .transport.pipe_transport import transport_for
from .client_support.context.paths import ClientPathsMixin
from .client_support.context.bridge_sync import ClientBridgeSyncMixin
from .client_support.context.presentation import ClientPresentationMixin
from .client_support.context.bridge_lifecycle import ClientBridgeLifecycleMixin
from .client_support.context.notifications import ClientNotificationsMixin
from .client_support.context.run_library import ClientRunLibraryMixin
from .client_support.context.run_actions import ClientRunActionsMixin
from .client_support.context.seed_policy import ClientSeedPolicyMixin
from .client_support.context.connection import ClientConnectionMixin
from .client_support.context.runtime import ClientRuntimeMixin

RUNTIME_DISPLAY_SAVE_SLOT_FILE = "runtime_display_save_slot.json"

KH3_SAVE_SEED_ASSOCIATIONS_KEY = seed_guard_support.KH3_SAVE_SEED_ASSOCIATIONS_KEY
KH3_SAVE_SEED_GUARD_VERSION = seed_guard_support.KH3_SAVE_SEED_GUARD_VERSION
ItemSyncInput = item_sync_support.ItemSyncInput
ItemSyncService = item_sync_support.ItemSyncService
DeathLinkService = deathlink_support.DeathLinkService
SaveManagementService = save_service_support.SaveManagementService
SeedGuardService = seed_guard_support.SeedGuardService
SeedPolicyService = seed_policy_support.SeedPolicyService
BridgeInitializationInput = bridge_sync_support.BridgeInitializationInput
BridgeSyncService = bridge_sync_support.BridgeSyncService
BridgeObservationState = bridge_sync_support.BridgeObservationState
GameStateSyncInput = bridge_sync_support.GameStateSyncInput
ObservationResetInput = bridge_sync_support.ObservationResetInput
BridgeCommandInput = client_command_support.BridgeCommandInput
ClientCommandAction = client_command_support.ClientCommandAction
ClientCommandActionKind = client_command_support.ClientCommandActionKind
ClientCommandKind = client_command_support.ClientCommandKind
ClientCommandPlan = client_command_support.ClientCommandPlan
ClientCommandService = client_command_support.ClientCommandService
FacadeCommandInput = client_command_support.FacadeCommandInput
ClientTransportService = client_transport_support.ClientTransportService
ConnectionPreparationInput = client_transport_support.ConnectionPreparationInput
LocationTransportDiagnosticInput = client_transport_support.LocationTransportDiagnosticInput
OutgoingMessageInput = client_transport_support.OutgoingMessageInput

KH3_LOCATION_CHECK_TRANSPORT_LOG_FILE = client_transport_support.KH3_LOCATION_CHECK_TRANSPORT_LOG_FILE
KH3_LOCATION_CHECK_TRANSPORT_STATE_FILE = client_transport_support.KH3_LOCATION_CHECK_TRANSPORT_STATE_FILE
KH3_STEAM_CLOUD_WARNING = steam_cloud_support.KH3_STEAM_CLOUD_WARNING
KH3_STEAM_CLOUD_UNKNOWN_WARNING = steam_cloud_support.KH3_STEAM_CLOUD_UNKNOWN_WARNING
KH3_STEAM_CLOUD_ACKNOWLEDGED_KEY = steam_cloud_support.KH3_STEAM_CLOUD_ACKNOWLEDGED_KEY
KH3_STEAM_CLOUD_CONFIRMATION_TEXT = steam_cloud_support.KH3_STEAM_CLOUD_CONFIRMATION_TEXT



# Public facade used by the context mixins and supported test adapters.
__all__ = [
    "ActiveRunSaves",
    "ArtifactStatusInput",
    "AutoActivationCallbacks",
    "AutoActivationRequest",
    "BRIDGE_PROTOCOL_NAME",
    "BRIDGE_PROTOCOL_VERSION",
    "BridgeLifecycleService",
    "BridgeProtocolError",
    "BridgePublicationService",
    "BridgeRefreshCallbacks",
    "BridgeRefreshInput",
    "BridgeWorkerOptions",
    "Callable",
    "ClientCommandExecutor",
    "ClientNotificationService",
    "ClientPacketCallbacks",
    "ClientPacketService",
    "ClientPaths",
    "ClientStatusSnapshotService",
    "CommandExecutionCallbacks",
    "ConnectedRunCallbacks",
    "ConnectedRunService",
    "ConnectedSeedRequest",
    "ConnectedSeedService",
    "ConnectedSetupCallbacks",
    "ConnectedSetupService",
    "ConnectionFlowCallbacks",
    "ConnectionFlowService",
    "ConnectionState",
    "InstalledPakDiscoveryInput",
    "KH3PlatformSeedDownloadService",
    "KH3RunActivationResult",
    "KH3RunConnectionProfile",
    "KH3RunDeleteResult",
    "KH3RunManagerError",
    "KH3RunManagerService",
    "KH3RunSuspensionResult",
    "KH3SeedImportResult",
    "KH3SeedImportService",
    "KH3SeedPreparationResult",
    "KH3_AP_SEED_PAK_PATTERN",
    "KH3_GOA_MOD_PATTERN",
    "KH3_WORLD_VERSION",
    "LifecycleState",
    "MoMClientRoute",
    "MoMClientService",
    "NATIVE_RUNTIME_SNAPSHOT_SCHEMAS",
    "NotificationState",
    "OfflineBridgeInitializationInput",
    "OfflineBridgeInitializationResult",
    "OfflineBridgeService",
    "OfflineStartupCallbacks",
    "OfflineStartupService",
    "OlympusColiseumCompatResult",
    "OrderedBridgePublisher",
    "RUNTIME_CURRENT_SAVE_SLOT_FILE",
    "RunActionCallbacks",
    "RunActionService",
    "RunConnectionCallbacks",
    "RunConnectionService",
    "RunPruneCallbacks",
    "RunPruningService",
    "RunSelection",
    "RuntimeArtifactCallbacks",
    "RuntimeArtifactExecutor",
    "RuntimeArtifactPlan",
    "SaveDirectoryRestartCallbacks",
    "SecretReportHintCallbacks",
    "SeedPreparationCallbacks",
    "SeedPreparationService",
    "SuspensionCallbacks",
    "TrackerCallbacks",
    "TrackerRoute",
    "atomic_write_json",
    "bound_runtime_snapshot",
    "configure_python_profiler",
    "datetime",
    "detect_kh3_steam_cloud_state",
    "disable_kh3_steam_cloud",
    "export_kh3_debug_bundle",
    "freeze_slot_location_catalog",
    "goa_mod_install_status",
    "goa_mod_status_cache_path",
    "infer_kh3_ap_slot_name_from_pak_name",
    "is_kh3_auto_save_dir",
    "is_kh3_save_dir_dynamic_root",
    "is_likely_steam_kh3_root_dir",
    "kh3_platform_room_fallback_url",
    "kh3_room_page_url_from_slot_data",
    "kh3_run_connection_affinity",
    "kh3_slot_data_with_connected_room_url",
    "load_installed_kh3_ap_seed_metadata",
    "load_optional_json_file",
    "load_optional_json_file_once",
    "normalize_kh3_room_page_url",
    "open_kh3_room_page_url",
    "open_path",
    "resolve_game_root_paths",
    "resolve_item_display_name",
    "resolve_kh3_save_dir_for_root",
    "resolve_kh3_save_dir_input",
    "run_integrated_bridge_worker",
    "safe_unlink",
    "seed_pak_metadata_cache_path",
    "server_loop",
    "timezone",
    "validate_protocol_payload",
    "validate_slot_data_payload",
]

class KH3CommandProcessor(ClientCommandProcessor):
    """Expose KH3-specific commands through the shared AP command processor."""

    ctx: "KH3Context"

    def _cmd_connect(self, address: str = "") -> bool:
        """Connect to a MultiWorld Server."""
        if address:
            self.ctx.server_address = None
            self.ctx.username = None
            self.ctx.password = None
        elif not self.ctx.server_address:
            self.output("Please specify an address.")
            return False
        if (
            (warning := self.ctx.build_installed_seed_pak_guard_message())
            and not getattr(self.ctx, "ui", None)
            and not self.ctx.has_kh3_seed_container_source()
        ):
            self.output(warning)
        Utils.async_start(self.ctx.connect(address if address else None), name="connecting")
        return True

    def _cmd_kh3_path(self) -> None:
        """Print the current KH3 bridge directory."""
        if self.ctx.bridge_dir is None:
            self.output("KH3 bridge directory is not initialized yet. Connect to a slot first.")
            return
        self.output(f"KH3 bridge directory: {self.ctx.bridge_dir}")

    def _cmd_launch(self) -> None:
        """Launch KH3 using the same action as the Launch KH3 button."""
        self.ctx.launch_kh3_game()
        self.output("KH3 launch requested.")

    def _cmd_gameflow(self, name: str = "", value: str = "") -> bool:
        """Read/set /gameflow <name> [integer]."""
        return self.ctx._get_recovery_command_service().gameflow(name, value)

    def _cmd_mapjump(self, mapname: str = "", playerstart: str = "") -> bool:
        """Warp with /mapjump <zone, mapname or /Game/... path> [playerstart]. Examples: /mapjump tt, /mapjump fz, /mapjump kh02."""
        return self.ctx._get_recovery_command_service().mapjump(mapname, playerstart)

    def _cmd_resync(self) -> None:
        """Rewrite all KH3 bridge files from the current Archipelago state."""
        if self.ctx.bridge_dir is None:
            self.output("KH3 bridge directory is not initialized yet. Connect to a slot first.")
            return
        self.ctx.invalidate_bridge_item_cache()
        self.ctx.write_bridge_files()
        self.output("KH3 bridge rewrite requested.")

    def _cmd_deathlink(self) -> bool:
        """Toggle DeathLink on or off, saving the choice for the current seed."""
        deathlink_enabled = "DeathLink" not in self.ctx.tags
        bridge_dir = getattr(self.ctx, "bridge_dir", None)
        if bridge_dir is not None:
            _, amnesty = deathlink_settings.read_settings(bridge_dir, self.ctx.slot_data)
            deathlink_settings.write_settings(bridge_dir, deathlink_enabled, amnesty)
        Utils.async_start(
            self.ctx.update_death_link(deathlink_enabled),
            name="KH3DeathLinkUpdate",
        )
        self.output(f"DeathLink {'enabled' if deathlink_enabled else 'disabled'}.")
        return True

    def _cmd_gameover(self) -> None:
        """Request a normal KH3 game-over through the UE4SS runtime bridge."""
        if self.ctx.bridge_dir is None:
            self.output("KH3 bridge directory is not initialized yet. Connect to a slot first.")
            return

        self.ctx.bridge_dir.mkdir(parents=True, exist_ok=True)
        request_id = f"manual-gameover-{int(time.time() * 1000)}"
        session = KH3BridgeSession(self.ctx.bridge_dir)
        session.initialize()
        result = session.queue_deathlink_game_over(
            {
                "time": time.time(),
                "source": self.ctx.player_names.get(self.ctx.slot, self.ctx.auth) or "manual-test",
                "cause": "Manual KH3 game-over test",
            },
            manual=True,
        )
        self.output(f"Queued KH3 normal game-over request: {result.get('request_id') or request_id}")

    def _cmd_deathlink_test(self) -> None:
        """Alias for /gameover while testing DeathLink."""
        self._cmd_gameover()


class KH3Context(
    ClientPathsMixin,
    ClientBridgeSyncMixin,
    ClientPresentationMixin,
    ClientBridgeLifecycleMixin,
    ClientNotificationsMixin,
    ClientRunLibraryMixin,
    ClientRunActionsMixin,
    ClientSeedPolicyMixin,
    ClientConnectionMixin,
    ClientRuntimeMixin,
    CommonContext,
):
    """Compose KH3 client services while preserving the CommonContext facade API."""

    # Resolve dependencies through this public module so existing integrations can
    # replace factories and platform helpers without knowing the private adapters.
    _client_api = sys.modules[__name__]

    KH3_STEAM_CLOUD_CONFIRMATION_TEXT = KH3_STEAM_CLOUD_CONFIRMATION_TEXT
    command_processor = KH3CommandProcessor
    game = "Kingdom Hearts III"
    items_handling = 0b111

    def __init__(
        self,
        server_address: str | None,
        password: str | None,
        bridge_root: str | None = None,
        *,
        integrated_bridge: bool = True,
        kh3_dir: str | None = None,
        save_dir: str | None = None,
        ue4ss_log: str | None = None,
        probe_config: str | None = None,
        kh3_mods_dir: str | None = None,
        bridge_poll_interval: float = 1.0,
    ):
        super().__init__(server_address, password)
        self.bridge_root_override = Path(bridge_root).expanduser().resolve() if bridge_root else None
        self.integrated_bridge_enabled = integrated_bridge
        self._apply_initial_paths(
            resolve_client_paths(
                kh3_dir=kh3_dir,
                save_dir=save_dir,
                ue4ss_log=ue4ss_log,
                probe_config=probe_config,
                mods_dir=kh3_mods_dir,
                persisted_save_dir=Utils.persistent_load().get("kh3_client", {}).get("save_dir"),
            )
        )
        self.bridge_poll_interval = bridge_poll_interval
        persistent_kh3 = Utils.persistent_load().get("kh3_client", {})
        self.auto_delete_old_kh3_runs = persistent_kh3.get("auto_delete_old_kh3_runs") is True
        self.steam_cloud_disabled_acknowledged = persistent_kh3.get(KH3_STEAM_CLOUD_ACKNOWLEDGED_KEY) is True
        self.ignore_unverified_save_seed_warnings = bool(
            persistent_kh3.get("ignore_unverified_save_seed_warnings", False)
        )
        self.integrated_bridge_activity_event = threading.Event()
        self.kh3_run_action_running = False
        self.kh3_run_action_label = ""
        self.kh3_seed_bootstrap_pending = False
        self.kh3_auth_inferred_from_installed_pak = False
        self.kh3_run_connection_slot_name = ""
        self.kh3_active_connection_profile_run_id: str | None = None
        self.kh3_room_pre_auth_blocked = False
        self.kh3_invalid_slot_recovery_pending = False
        self.kh3_connected_room_url: str | None = None
        self.kh3_received_items_snapshot_ready = False
        self.bridge_dir: Path | None = None
        self.offline_seed_name: str | None = None
        self.slot_data: dict[str, Any] = {}
        self.location_key_to_id: dict[str, int] = {}
        self.location_id_to_bridge_key: dict[int, str] = {}
        self.location_id_to_display_name: dict[int, str] = {}
        self.location_name_to_display_name: dict[str, str] = {}
        self.last_processed_game_state_seq = -1
        self.last_processed_game_state_file_signature: tuple[int, int] | None = None
        self.last_processed_bridge_notification_seq = 0
        self.last_processed_client_command_seq = 0
        self.poptracker_sync_service = PopTrackerSyncService()
        self.last_pak_status_warning = ""
        self.last_save_seed_status_warning = ""
        self.save_seed_mismatch_active = False
        self.kh3_restart_required_seed_id: str | None = None
        self.kh3_check_reporting_enabled = False
        self.goal_status_sent = False
        self.client_status_presenter = ClientStatusPresenter(self)
        self.status_snapshot_service = self._create_status_snapshot_service()
        self.runtime_artifact_service = RuntimeArtifactService()
        self.client_transport_service = self._create_client_transport_service()
        self.client_command_service = self._create_client_command_service()
        self.client_notification_service = self._create_client_notification_service()
        self.item_sync_service = self._create_item_sync_service()
        self.secret_report_hint_service = SecretReportHintService()
        self.deathlink_service = self._create_deathlink_service()
        self.seed_guard_service = SeedGuardService(self)
        self.save_management_service = SaveManagementService(
            self,
            process_running=lambda: is_process_running(KH3_PROCESS_NAME),
        )
        self.run_manager_service = self._create_run_manager_service()
        self.seed_import_service = self._create_seed_import_service()
        self.platform_seed_download_service = self._create_platform_seed_download_service()
        self.seed_policy_service = self._create_seed_policy_service()
        self.lifecycle_service = self._create_lifecycle_service()
        self.bridge_sync_service = self._create_bridge_sync_service()
        self.offline_bridge_service = self._create_offline_bridge_service()

    def _get_recovery_command_service(self) -> RecoveryCommandService:
        service = self.__dict__.get("recovery_command_service")
        if service is None:
            service = self.recovery_command_service = RecoveryCommandService(self, logger.info)
        return service

    # Keep CommonContext overrides in this facade: super() must bypass the
    # private adapters when notifications delegate to the base presentation.
    def on_user_say(self, text: str) -> str | None:
        parts = text.split(maxsplit=1)
        if len(parts) == 2 and parts[0].casefold() == "!getitem":
            requested_item = parts[1].strip().casefold()
            for alias, item_name in CHARACTER_UNLOCK_ITEM_ALIASES.items():
                if requested_item == alias.casefold():
                    text = f"!getitem {item_name}"
                    break
        return super().on_user_say(text)

    def on_print_json(self, args: dict) -> None:
        self._get_client_notification_service().deliver_print_json(args)

    def _present_notification_print_json(self, args: dict[str, Any]) -> None:
        super().on_print_json(args)

    def on_deathlink(self, data: dict[str, Any]) -> None:
        super().on_deathlink(data)
        self._get_deathlink_service().handle_incoming(self.bridge_dir, data)

    @staticmethod
    def auto_display_save_root_for_save_dir(save_dir: Path | str) -> Path:
        return SaveManagementService.auto_display_save_root_for_save_dir(save_dir)

    @staticmethod
    def _connection_slot_name_for_run(run: KH3RunInfo) -> str:
        return run.effective_slot_name

    normalized_kh3_run_server_address = staticmethod(normalize_server_address)

    def event_invalid_slot(self) -> None:
        if (
            bool(getattr(self, "kh3_auth_inferred_from_installed_pak", False))
            and self.has_installed_kh3_ap_seed_pak()
            and not bool(getattr(self, "kh3_invalid_slot_recovery_pending", False))
        ):
            self.kh3_invalid_slot_recovery_pending = True
            Utils.async_start(
                self._recover_rejected_inferred_kh3_slot(),
                name="KH3InvalidInferredSlotRecovery",
            )
            return
        super().event_invalid_slot()

    async def send_msgs(self, msgs: list[Any]) -> None:
        await self._get_client_transport_service().send_messages(
            OutgoingMessageInput(
                messages=tuple(msgs),
                check_reporting_enabled=bool(getattr(self, "kh3_check_reporting_enabled", False)),
                goal_status=ClientStatus.CLIENT_GOAL,
            ),
            sender=super().send_msgs,
            record_event=self.log_location_check_transport_event,
        )

    @staticmethod
    def extract_seed_id(seed_name: Any) -> str | None:
        return SeedGuardService.extract_seed_id(seed_name)

    @staticmethod
    def save_seed_scope_key(save_dir: Path | str) -> str:
        return SeedGuardService.save_seed_scope_key(save_dir)

    @staticmethod
    def save_seed_scope_label(save_dir: Path | str) -> str:
        return SeedGuardService.save_seed_scope_label(save_dir)

    @classmethod
    def load_save_seed_associations(cls, persistent_kh3: dict[str, Any]) -> dict[str, dict[str, Any]]:
        return SeedGuardService.load_save_seed_associations(persistent_kh3)

    @staticmethod
    def delete_save_files(
        save_dir: Path,
        *,
        preserve_root: bool = True,
        archive_label: str | None = None,
        backup_current_save: bool = True,
    ) -> dict[str, Any]:
        return SaveManagementService.delete_save_files(
            save_dir,
            preserve_root=preserve_root,
            archive_label=archive_label,
            backup_current_save=backup_current_save,
        )

    @staticmethod
    def find_save_backup_dirs(save_dir: Path) -> list[Path]:
        return SaveManagementService.find_save_backup_dirs(save_dir)

    @staticmethod
    def delete_save_files_and_backups(
        save_dir: Path,
        *,
        archive_label: str | None = None,
        backup_current_save: bool = True,
    ) -> dict[str, Any]:
        return SaveManagementService.delete_save_files_and_backups(
            save_dir,
            archive_label=archive_label,
            backup_current_save=backup_current_save,
        )

    @staticmethod
    def summarize_save_directory(save_dir: Path) -> dict[str, int]:
        return SaveManagementService.summarize_save_directory(save_dir)

    @staticmethod
    def steam_sibling_save_dirs(save_dir: Path) -> list[Path]:
        return SaveManagementService.steam_sibling_save_dirs(save_dir)

    def _launch_kh3_game_now(self) -> None:
        if not self._ensure_modloader_pak_priority():
            return
        if not launch_preparation_support.prepare_launch_files(self.kh3_dir):
            return
        self._reconcile_olympus_coliseum_reward_companion()
        self._apply_client_facade_plan(
            self._get_client_command_service().plan_facade_command(
                ClientCommandKind.LAUNCH_KH3,
            )
        )

    def _ensure_modloader_pak_priority(self) -> bool:
        return launch_preparation_support.ensure_modloader_priority(
            self.kh3_mods_dir,
            game_running=lambda: is_process_running(KH3_PROCESS_NAME),
        )

    def _install_kh3_runtime_files(self) -> None:
        if not self._ensure_modloader_pak_priority():
            return
        self.disable_steam_cloud_during_patch_game()
        self._apply_runtime_artifact_plan(self._get_runtime_artifact_service().plan_runtime_install())

    def run_gui(self) -> None:
        from .ClientGui import run_kh3_gui

        run_kh3_gui(self)

    def gui_error(self, title: str, text: Exception | str) -> Any | None:
        """Route CommonClient errors through the native frontend without loading Kivy."""
        if not self.ui:
            return None
        if self._messagebox:
            self._messagebox.dismiss()
        self._messagebox = self.ui.show_error(title, text)
        return self._messagebox

    def handle_connection_loss(self, msg: str) -> None:
        """Show expected network failures as the same readable modal and log message."""
        import socket
        import sys

        import websockets

        error = sys.exc_info()[1]
        if not isinstance(error, (OSError, websockets.InvalidURI, websockets.InvalidMessage)):
            super().handle_connection_loss(msg)
            return
        address = getattr(self, "connection_attempt_address", None) or self.server_address
        server = normalize_server_address(address)
        if server is None:
            # Keep malformed ports visible while omitting URI credentials and paths.
            server = str(address or "").split("://", 1)[-1].rsplit("@", 1)[-1].split("/", 1)[0]
        if isinstance(error, socket.gaierror):
            msg = "Could not resolve the server name."
        text = (f"Server: {server or 'No server address provided'}\n\n{msg}\n"
                "Check the server name or IP address and port, and make sure the server is running.")
        title = "Connection Error"
        logger.error("%s\n%s", title, text)
        self._messagebox_connection_loss = self.gui_error(title, text)

    async def connection_closed(self) -> None:
        await super().connection_closed()
        # Publish the outage even if no checks/items arrive to trigger another
        # refresh. Cached AP receipts remain available for offline gameplay.
        if self.bridge_dir is not None:
            publication = self.write_bridge_files()
            if publication is not None:
                await publication

    def reset_server_state(self) -> None:
        sync = self.__dict__.get("bridge_sync_service")
        if sync is not None:
            sync.invalidate_pending_observation()
        publisher = self.__dict__.get("bridge_publisher")
        if publisher is not None:
            publisher.invalidate_slot_data()
        super().reset_server_state()
        # Automatic reconnect bypasses connect()/prepare_for_new_server_connection.
        # Let the next RoomInfo reach KH3's run-preservation gate even if the
        # server was reseeded during the outage. Offline identity stays separate.
        self.seed_name = None
        self.kh3_check_reporting_enabled = False
        self.kh3_room_pre_auth_blocked = False
        self.kh3_connected_room_url = None
        self.kh3_received_items_snapshot_ready = False

    async def connect(self, address: str | None = None) -> None:
        self.connection_attempt_address = address or self.server_address
        await self._create_connection_flow_service().connect(address)

    async def _prepare_kh3_room_authentication(self, args: dict) -> None:
        self._observe_kh3_room_seed(args.get("seed_name"))
        await self._create_connection_flow_service().guard_room(
            seed_mismatch=self.installed_seed_pak_mismatches_room(self.seed_name),
            affinity_mismatch=self.installed_run_mismatches_connection_affinity(),
            seed_name=self.seed_name,
        )

    def on_package(self, cmd: str, args: dict[str, Any]) -> None:
        self._create_client_packet_service().handle(cmd, args)

    async def server_auth(self, password_requested: bool = False) -> None:
        # CommonClient 0.6.8 records RoomInfo before calling this supported hook.
        # Keep run preservation inside the APWorld, including automatic reconnect.
        room_seed = getattr(self, "server_seed_name", None)
        if room_seed:
            await self._prepare_kh3_room_authentication({"seed_name": room_seed})
        await self._create_connection_flow_service().authenticate(password_requested, parent_auth=super().server_auth)

    def build_status_payload(self, items_payload: dict[str, Any] | None = None) -> dict[str, Any]:
        reported_checks = tuple(sorted(self.checked_locations | self.locations_checked))
        total_locations = self.total_locations or len(self.location_key_to_id)
        received_item_count = self._get_item_sync_service().received_item_count(
            self.items_received,
            bridge_dir=self.bridge_dir,
            ap_connected=self._is_ap_connected(),
            received_items_snapshot_ready=bool(getattr(self, "kh3_received_items_snapshot_ready", False)),
            expected_seed=getattr(self, "slot_data", {}).get("kh3_seed"),
        )
        installed_pak = self._get_seed_policy_service().installed_pak_decision()
        seed_name = self.seed_name or getattr(self, "offline_seed_name", None)
        slot, team = self.slot, self.team
        player_name = self.player_names.get(self.slot, self.auth)
        previous = (
            self._load_bridge_payload("ap_status.json") or {}
            if slot is None and self.bridge_dir is not None and seed_name
            else {}
        )
        return build_bridge_status_payload(
            BridgeStatusInput(
                reported_checks=reported_checks,
                total_locations=total_locations,
                received_item_count=received_item_count,
                seed_name=seed_name,
                slot=slot,
                team=team,
                player_name=player_name,
                previous_status=previous,
                connected=self._is_ap_connected(),
                server_address=self.server_address,
                goal_complete=self.finished_game,
                last_processed_game_state_seq=self.last_processed_game_state_seq,
                installed_pak=installed_pak.status,
                save_seed=self.build_save_seed_status(),
                check_reporting_blocked=bool(
                    getattr(self, "save_seed_mismatch_active", False) or not installed_pak.compatible
                ),
            )
        )


async def bridge_watcher(ctx: KH3Context) -> None:
    from .kh02.store_launch import StoreLaunchService
    store_launch = StoreLaunchService(ctx)
    dependencies = {
        "game_state": ("game_state.json",),
        "deathlink_game_over": ("runtime_deathlink_state.json", "deathlink_settings.json", "deathlink_network_state.json", "slot_data.json"),
        "poptracker_bounce": ("runtime_status.json",),
        "poptracker_lucky_emblems": ("runtime_lucky_emblem_state.json",),
        "secret_report_hints": ("runtime_secret_report_hints.json",),
        "bridge_notifications": ("bridge_notifications.json",),
        "client_command": ("ap_client_command.json",),
        "mom_challenges": ("runtime_mom_barrier_interaction.json", "game_state.json"),
        "kh02_store_launch": ("runtime_kh02_launch_request.json",),
    }

    def step_signature(name: str):
        root = ctx.bridge_dir
        if root is None:
            return None
        route = (root, ctx.slot, ctx.server, ctx._is_ap_connected(),
                 getattr(ctx, "kh3_check_reporting_enabled", False),
                 getattr(ctx, "_bridge_observation_paused", False), id(ctx.slot_data))
        transport = transport_for(root / "runtime_context.json")
        route += (id(transport), getattr(transport, "connection_generation", None))
        channels = tuple(file_signature(root / channel) for channel in dependencies[name])
        extra = None
        if name == "deathlink_game_over":
            # Heartbeat timestamps do not change the DeathLink binding.
            status = load_optional_json_view(root / "bridge_runtime_status.json") or {}
            extra = status.get("kh3_process_pid")
        elif name == "mom_challenges":
            service = ctx.__dict__.get("_mom_client_service")
            challenge = getattr(service, "_service", None)
            if challenge is not None and any(record["location_id"] not in challenge.state["cleared"]
                                             for record in challenge.state["requests"].values()):
                extra = time.monotonic() // ctx.bridge_poll_interval
        elif name == "kh02_store_launch":
            extra = store_launch.selection_revision
        return route, channels, extra

    try:
        await watch_bridge(
            BridgePollCallbacks(
                exiting=ctx.exit_event.is_set,
                has_bridge=lambda: ctx.bridge_dir is not None,
                interval=lambda: ctx.bridge_poll_interval,
                publisher=lambda: ctx.__dict__.get("bridge_publisher"),
                recovery=lambda: ctx.__dict__.get("recovery_command_service"),
                set_activity_notify=lambda notify: setattr(ctx, "pipe_activity_notify", notify),
                step_signature=step_signature,
                steps=lambda: (
                    ("game_state", ctx.handle_game_state_file),
                    ("deathlink_game_over", ctx.handle_runtime_deathlink_game_over),
                    ("poptracker_bounce", ctx.handle_poptracker_runtime_context_bounce),
                    ("poptracker_lucky_emblems", ctx.handle_poptracker_lucky_emblem_state),
                    ("secret_report_hints", ctx.handle_secret_report_hints),
                    ("bridge_notifications", ctx.handle_bridge_notifications_file),
                    ("client_command", ctx.handle_bridge_client_command_file),
                    ("mom_challenges", ctx.handle_mom_challenge_results),
                    ("kh02_store_launch", store_launch.observe),
                ),
            ),
            profile_scope=PythonProfileScope,
        )
    finally:
        store_launch.close()


async def main(
    connect: str | None = None,
    password: str | None = None,
    name: str | None = None,
    bridge_root: str | None = None,
    *,
    integrated_bridge: bool = True,
    kh3_dir: str | None = None,
    save_dir: str | None = None,
    ue4ss_log: str | None = None,
    probe_config: str | None = None,
    kh3_mods_dir: str | None = None,
    bridge_poll_interval: float = 1.0,
) -> None:
    ctx = KH3Context(
        connect,
        password,
        bridge_root,
        integrated_bridge=integrated_bridge,
        kh3_dir=kh3_dir,
        save_dir=save_dir,
        ue4ss_log=ue4ss_log,
        probe_config=probe_config,
        kh3_mods_dir=kh3_mods_dir,
        bridge_poll_interval=bridge_poll_interval,
    )
    if name:
        ctx.auth = name

    offline_startup_safe = await asyncio.to_thread(deactivate_incompatible_seed_paks, ctx.kh3_mods_dir)

    if gui_enabled:
        ctx.lifecycle_service.start_gui()
    else:
        ctx.warn_about_steam_cloud_if_needed()
    ctx.lifecycle_service.start_cli()

    ctx.lifecycle_service.start_bridge_watcher()
    if offline_startup_safe:
        await ctx.initialize_offline_bridge_after_startup()
    if str(connect or "").strip():
        ctx.lifecycle_service.start_server(connect, task_name="ServerLoop")
    else:
        logger.info("KH3 client started disconnected. Press Connect when the room is ready.")

    await ctx.exit_event.wait()
    await ctx.lifecycle_service.shutdown()


def launch(*args: str) -> None:
    import colorama

    colorama.just_fix_windows_console()
    parsed = parse_launch_arguments(args)
    asyncio.run(
        main(
            parsed.connect,
            parsed.password,
            parsed.name,
            parsed.bridge_root,
            integrated_bridge=not parsed.no_integrated_bridge,
            kh3_dir=parsed.kh3_dir,
            save_dir=parsed.save_dir,
            ue4ss_log=parsed.ue4ss_log,
            probe_config=parsed.probe_config,
            kh3_mods_dir=parsed.kh3_mods_dir,
            bridge_poll_interval=parsed.bridge_poll_interval,
        )
    )
