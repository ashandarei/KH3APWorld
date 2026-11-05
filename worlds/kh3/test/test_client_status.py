from __future__ import annotations

import asyncio
import json
import os
import shutil
import threading
import unittest
from contextlib import nullcontext
from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from CommonClient import CommonContext

from .. import Client as KH3ClientModule
from ..Bridge import (
    BRIDGE_PROTOCOL_NAME,
    BRIDGE_PROTOCOL_VERSION,
    BridgeProtocolError,
    validate_protocol_payload,
)
from ..bridge_support.files import atomic_write_json, load_optional_json_file, read_text_tail_lines
from ..Client import (
    KH3_SAVE_SEED_ASSOCIATIONS_KEY,
    KH3_SAVE_SEED_GUARD_VERSION,
    KH3CommandProcessor,
    KH3Context,
)
from ..client_support import received_items_trace
from ..client_support.bridge_sync_service import (
    BridgeInitializationInput,
    BridgeSyncService,
    GameStateSyncInput,
)
from ..client_support.client_command_service import (
    CLIENT_COMMAND_FILE,
    BridgeCommandInput,
    ClientCommandAction,
    ClientCommandActionKind,
    ClientCommandIdentity,
    ClientCommandKind,
    ClientCommandPlan,
    ClientCommandService,
    FacadeCommandInput,
)
from ..client_support.client_transport_service import (
    ClientTransportService,
    ConnectionPreparationInput,
    LocationTransportDiagnosticInput,
    LocationTransportEntry,
    OutgoingMessageInput,
)
from ..client_support.item_sync_service import ItemSyncInput, ItemSyncService
from ..client_support.lifecycle_service import BridgeLifecycleService, LifecycleState
from ..client_support.notification_service import ClientNotificationService
from ..client_support.offline_bridge_service import (
    InstalledPakDiscoveryInput,
    OfflineBridgeInitializationInput,
    OfflineBridgeInitializationResult,
    OfflineBridgeService,
)
from ..client_support.platform_seed_download import KH3PlatformSeedDownloadError
from ..client_support.presentation import (
    ClientStatusPresenter,
    ClientStatusSnapshot,
    InstalledRuntimeSummary,
    RuntimeGrantSummary,
    build_installed_runtime_summary,
)
from ..client_support.replay_slot_presentation import (
    ReplaySlotSelectionInput,
    ReplaySlotSummary,
    build_replay_slot_summaries,
)
from ..client_support.run_manager import KH3RunConnectionProfile
from ..client_support.runtime_artifact_service import (
    ArtifactStatusInput,
    ProbeConfigPathInput,
    ProbeConfigurationInput,
    RuntimeArtifactActionKind,
    RuntimeArtifactService,
)
from ..client_support.save_service import SaveManagementService
from ..client_support.secret_report_hint_service import SecretReportHintService
from ..client_support.seed_guard import SeedGuardService
from ..client_support.seed_import_service import KH3SeedPreparationResult
from ..client_support.seed_policy_service import (
    KH3_MINIMUM_SEED_PAK_SIZE_BYTES,
    DisconnectPolicyResult,
    InstalledPakDecision,
    SaveGuardResult,
    SaveSeedDecision,
    SeedPolicyService,
)
from ..client_support.status_snapshot_service import (
    ClientStatusSnapshotService,
    StatusSnapshotInputs,
)
from ..ExternalBridge import _get_process_status_linux_proc, is_process_running
from ..SlotData import encode_slot_data
from . import empty_wire_slot_data


class TestClientTransportService(unittest.TestCase):
    @staticmethod
    def build_service(
        *,
        payload_reader=load_optional_json_file,
    ) -> ClientTransportService:
        return ClientTransportService(
            protocol_name=BRIDGE_PROTOCOL_NAME,
            protocol_version=BRIDGE_PROTOCOL_VERSION,
            payload_reader=payload_reader,
            validate_protocol=validate_protocol_payload,
            protocol_error=BridgeProtocolError,
        )

    def test_transport_plans_are_typed_immutable_and_preserve_ordering(self) -> None:
        service = self.build_service()
        preparation = service.plan_connection_preparation(ConnectionPreparationInput(
            auth="OldPlayer",
            inferred_slot_name="Sora",
        ))
        outgoing = service.plan_outgoing_messages(OutgoingMessageInput(
            messages=(
                {"cmd": "LocationChecks", "locations": [7, 8]},
                {"cmd": "Get", "keys": ["status"]},
                {"cmd": "StatusUpdate", "status": "goal"},
            ),
            check_reporting_enabled=False,
            goal_status="goal",
        ))

        self.assertEqual(preparation.auth, "Sora")
        self.assertTrue(preparation.log_inferred_slot_name)
        self.assertEqual(outgoing.messages, ({"cmd": "Get", "keys": ["status"]},))
        self.assertEqual(outgoing.suppressed_location_count, 2)
        self.assertTrue(outgoing.suppressed_goal)
        self.assertTrue(outgoing.record_send_suppressed)
        self.assertTrue(outgoing.log_suppression_warning)
        with self.assertRaises(FrozenInstanceError):
            outgoing.send_messages = False

    def test_transport_payload_loading_and_diagnostics_preserve_fallbacks(self) -> None:
        payloads = {
            "valid.json": {
                "protocol_name": BRIDGE_PROTOCOL_NAME,
                "protocol_version": BRIDGE_PROTOCOL_VERSION,
                "value": 1,
            },
            "incompatible.json": {
                "protocol_name": "other",
                "protocol_version": BRIDGE_PROTOCOL_VERSION,
            },
            "sidecar.json": {"value": 2},
            "not_object.json": [1, 2],
        }

        def payload_reader(path: Path):
            if path.name == "malformed.json":
                raise ValueError("malformed")
            return payloads.get(path.name)

        service = self.build_service(
            payload_reader=payload_reader,
        )
        bridge_dir = Path("synthetic-bridge")
        diagnostic = service.build_location_transport_diagnostic(LocationTransportDiagnosticInput(
            event="location_checks_send_attempt",
            recorded_at_utc=datetime(2026, 7, 11, tzinfo=timezone.utc),
            location_id_to_bridge_key={7: "Treasure:7"},
            last_processed_game_state_seq=4,
            check_reporting_enabled=True,
            connected=True,
            server_address="archipelago.gg",
            team=1,
            slot=2,
            locations=("7", "invalid", 8),
            source_cmd="LocationChecks",
        ))

        self.assertEqual(service.load_protocol_payload(bridge_dir, "valid.json")["value"], 1)
        self.assertIsNone(service.load_protocol_payload(bridge_dir, "incompatible.json"))
        self.assertIsNone(service.load_protocol_payload(bridge_dir, "malformed.json"))
        self.assertEqual(service.load_sidecar_payload(bridge_dir, "sidecar.json"), {"value": 2})
        self.assertIsNone(service.load_sidecar_payload(bridge_dir, "not_object.json"))
        self.assertEqual(diagnostic.payload["location_ids"], [7, 8])
        self.assertEqual(diagnostic.payload["bridge_keys"], ["Treasure:7"])
        self.assertEqual(diagnostic.payload["last_processed_game_state_seq"], 4)
        self.assertEqual(diagnostic.payload["recorded_at_utc"], "2026-07-11T00:00:00+00:00")
        self.assertEqual(
            diagnostic.locations,
            (
                LocationTransportEntry(7, "Treasure:7"),
                LocationTransportEntry(8, None),
            ),
        )
        with self.assertRaises(FrozenInstanceError):
            diagnostic.locations = ()

    def test_transport_payload_cache_reuses_unchanged_files_and_invalidates_on_change(self) -> None:
        with TemporaryDirectory() as temp_dir:
            bridge_dir = Path(temp_dir)
            protocol_path = bridge_dir / "status.json"
            sidecar_path = bridge_dir / "runtime_context.json"
            protocol_path.write_text(json.dumps({
                "protocol_name": BRIDGE_PROTOCOL_NAME,
                "protocol_version": BRIDGE_PROTOCOL_VERSION,
                "value": 1,
            }), encoding="utf-8")
            sidecar_path.write_text(json.dumps({"world": "first"}), encoding="utf-8")
            reads: list[str] = []

            def payload_reader(path: Path):
                reads.append(path.name)
                return load_optional_json_file(path)

            service = self.build_service(payload_reader=payload_reader)

            first_protocol = service.load_protocol_payload(bridge_dir, protocol_path.name)
            first_protocol["value"] = 99
            self.assertEqual(service.load_protocol_payload(bridge_dir, protocol_path.name)["value"], 1)
            self.assertEqual(service.load_sidecar_payload(bridge_dir, sidecar_path.name), {"world": "first"})
            self.assertEqual(service.load_sidecar_payload(bridge_dir, sidecar_path.name), {"world": "first"})
            self.assertEqual(reads, [protocol_path.name, sidecar_path.name])

            protocol_path.write_text(json.dumps({
                "protocol_name": BRIDGE_PROTOCOL_NAME,
                "protocol_version": BRIDGE_PROTOCOL_VERSION,
                "value": 200,
            }), encoding="utf-8")
            sidecar_path.unlink()
            self.assertEqual(service.load_protocol_payload(bridge_dir, protocol_path.name)["value"], 200)
            self.assertIsNone(service.load_sidecar_payload(bridge_dir, sidecar_path.name))
            self.assertEqual(reads, [
                protocol_path.name,
                sidecar_path.name,
                protocol_path.name,
                sidecar_path.name,
            ])

            service.clear_payload_cache()
            self.assertEqual(service.load_protocol_payload(bridge_dir, protocol_path.name)["value"], 200)
            self.assertEqual(reads[-1], protocol_path.name)

    def test_context_transport_facade_delegates_and_applies_connection_result(self) -> None:
        service = self.build_service(
            payload_reader=lambda _path: {
                "protocol_name": BRIDGE_PROTOCOL_NAME,
                "protocol_version": BRIDGE_PROTOCOL_VERSION,
            },
        )
        service.plan_connection_preparation = Mock(wraps=service.plan_connection_preparation)
        ctx = KH3Context.__new__(KH3Context)
        ctx.client_transport_service = service
        ctx.bridge_dir = Path("synthetic-bridge")
        ctx.auth = "OldPlayer"
        ctx.seed_name = "old-seed"
        ctx.kh3_check_reporting_enabled = True
        ctx.locations_checked = {1}
        ctx.locations_scouted = {2}
        ctx.goal_status_sent = True
        ctx.last_processed_game_state_seq = 9
        ctx.last_processed_game_state_file_signature = (1, 2)
        ctx.save_seed_mismatch_active = True
        ctx.last_save_seed_status_warning = "old warning"
        ctx.infer_slot_name_from_installed_pak = Mock(return_value="Sora")

        ctx.prepare_for_new_server_connection()

        service.plan_connection_preparation.assert_called_once_with(ConnectionPreparationInput(
            auth="OldPlayer",
            inferred_slot_name="Sora",
        ))
        self.assertEqual(ctx.auth, "Sora")
        self.assertIsNone(ctx.seed_name)
        self.assertEqual(ctx.locations_checked, set())
        self.assertEqual(ctx.locations_scouted, set())
        self.assertIsNone(ctx.last_processed_game_state_file_signature)
        self.assertEqual(ctx._load_bridge_payload("valid.json"), {
            "protocol_name": BRIDGE_PROTOCOL_NAME,
            "protocol_version": BRIDGE_PROTOCOL_VERSION,
        })
        self.assertEqual(ctx._load_bridge_sidecar_payload("sidecar.json"), {
            "protocol_name": BRIDGE_PROTOCOL_NAME,
            "protocol_version": BRIDGE_PROTOCOL_VERSION,
        })

    def test_context_transport_facade_preserves_send_diagnostic_order(self) -> None:
        async def run_case(*, fail: bool) -> list[tuple[str, object]]:
            observations: list[tuple[str, object]] = []

            async def common_send(_ctx, messages: list[dict]) -> None:
                observations.append(("send", messages))
                if fail:
                    raise RuntimeError("network down")

            def log_event(event: str, **fields) -> None:
                observations.append((event, fields))

            ctx = KH3Context.__new__(KH3Context)
            ctx.client_transport_service = self.build_service()
            ctx.kh3_check_reporting_enabled = True
            ctx.log_location_check_transport_event = log_event
            messages = [{"cmd": "LocationChecks", "locations": [7]}]
            with patch.object(CommonContext, "send_msgs", common_send):
                if fail:
                    with self.assertRaisesRegex(RuntimeError, "network down"):
                        await ctx.send_msgs(messages)
                else:
                    await ctx.send_msgs(messages)
            return observations

        complete = asyncio.run(run_case(fail=False))
        failed = asyncio.run(run_case(fail=True))

        self.assertEqual([entry[0] for entry in complete], [
            "location_checks_send_attempt",
            "send",
            "location_checks_send_complete",
        ])
        self.assertEqual([entry[0] for entry in failed], [
            "location_checks_send_attempt",
            "send",
            "location_checks_send_failed",
        ])
        self.assertEqual(failed[-1][1]["error"], "RuntimeError: network down")


class TestRuntimeArtifactService(unittest.TestCase):
    def test_text_tail_reader_avoids_the_start_of_large_logs(self) -> None:
        boundary_line = b"Runtime context refresh hook=Tail | world=World /Game/Levels/tt/tt_01 | outer=Pawn\n"
        payload = (b"old UE4SS line\n" * 4_000) + boundary_line + (b"newer UE4SS line\n" * 499)
        reads: list[tuple[int, int]] = []

        class TrackingReader(BytesIO):
            def read(self, size: int = -1) -> bytes:
                offset = self.tell()
                chunk = super().read(size)
                reads.append((offset, len(chunk)))
                return chunk

        with patch.object(Path, "open", return_value=TrackingReader(payload)):
            lines = read_text_tail_lines(Path("UE4SS.log"), max_lines=500, chunk_size=256)

        self.assertEqual(len(lines), 500)
        self.assertIn("Runtime context refresh hook=Tail", lines[0])
        self.assertGreater(min(offset for offset, _size in reads), 0)
        self.assertLess(sum(size for _offset, size in reads), len(payload) // 2)

    def test_artifact_and_probe_plans_are_typed_immutable_and_preserve_precedence(self) -> None:
        service = RuntimeArtifactService()
        summary = service.summarize_artifacts(ArtifactStatusInput(
            goa_status={"status": "current"},
            goa_present=True,
            seed_pak_present=False,
            runtime_status={"probe_config_installed": True},
        ))

        self.assertTrue(summary.goa_present)
        self.assertTrue(summary.goa_current)
        self.assertTrue(summary.runtime_artifacts_present)
        self.assertTrue(summary.install_artifacts_present)
        with self.assertRaises(FrozenInstanceError):
            summary.goa_present = False

        explicit = Path.cwd() / "explicit" / "config.lua"
        discovered = Path.cwd() / "discovered" / "config.lua"
        self.assertEqual(
            service.select_probe_config_path(ProbeConfigPathInput(str(explicit), discovered)),
            explicit.resolve(),
        )
        self.assertEqual(
            service.select_probe_config_path(ProbeConfigPathInput(
                explicit_path=None,
                discovered_path=discovered,
                override_active=True,
                current_path=explicit,
            )),
            explicit,
        )

        bridge_dir = Path("bridge")
        missing_plan = service.plan_probe_configuration(ProbeConfigurationInput(
            bridge_dir=bridge_dir,
            kh3_root=Path("KH3"),
            win64_exists=False,
            config_path=explicit,
        ))
        self.assertEqual(missing_plan.actions[0].kind, RuntimeArtifactActionKind.LOG_INFO)
        self.assertEqual(
            missing_plan.actions[0].text,
            "KH3 install directory was not found at %s. Set KH3 Dir in the KH3 Config tab "
            "if runtime chest events do not reach the client.",
        )
        write_plan = service.plan_probe_configuration(ProbeConfigurationInput(
            bridge_dir=bridge_dir,
            kh3_root=Path("KH3"),
            win64_exists=True,
            config_path=explicit,
        ))
        self.assertEqual(
            [(action.kind, action.path, action.bridge_dir) for action in write_plan.actions],
            [(RuntimeArtifactActionKind.WRITE_PROBE_CONFIG, explicit, bridge_dir)],
        )

    def test_install_and_uninstall_plans_preserve_order_and_wording(self) -> None:
        service = RuntimeArtifactService()
        self.assertEqual(
            [action.kind for action in service.plan_runtime_install().actions],
            [
                RuntimeArtifactActionKind.BACKUP_VANILLA_SAVES,
                RuntimeArtifactActionKind.ENSURE_GOA_MOD,
                RuntimeArtifactActionKind.INSTALL_RUNTIME_FILES,
            ],
        )
        self.assertEqual(
            [action.kind for action in service.plan_runtime_uninstall().actions],
            [
                RuntimeArtifactActionKind.UNINSTALL_RUNTIME_ARTIFACTS,
                RuntimeArtifactActionKind.RESTORE_VANILLA_SAVES,
                RuntimeArtifactActionKind.DISCONNECT_AFTER_RESTORE,
            ],
        )
        uninstall = service.normalize_uninstall_result({
            "removed_files": 3,
            "removed_paks": 2,
            "removed_dirs": 1,
            "updated_mods_txt": 2,
            "errors": ["first"],
        })
        report = service.plan_uninstall_report(uninstall)
        self.assertEqual(report.actions[0].kind, RuntimeArtifactActionKind.LOG_WARNING)
        self.assertEqual(
            report.actions[0].text,
            "Uninstalled KH3 AP files partially: removed %s file(s), %s pak(s), %s folder(s), "
            "updated %s mods.txt file(s), with %s error(s).",
        )
        self.assertEqual(report.actions[1].log_args, ("first",))

    def test_context_delegates_probe_status_install_and_uninstall_effects_in_order(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bridge_dir = root / "bridge"
            mods_dir = root / "mods"
            bridge_dir.mkdir()
            mods_dir.mkdir()
            goa_path = mods_dir / "pakchunk99-GardenOfAssemblageRandomizer_99_P.pak"
            seed_path = mods_dir / "pakchunk9-Player-randomizer-AP_12345678901234567890-1_99_P.pak"
            goa_path.write_bytes(b"goa")
            seed_path.write_bytes(b"seed")

            ctx = KH3Context.__new__(KH3Context)
            ctx.kh3_dir = root
            ctx.kh3_mods_dir = mods_dir
            ctx.bridge_dir = bridge_dir
            ctx.probe_config_path = root / "config.lua"
            service = RuntimeArtifactService()
            service.plan_probe_configuration = Mock(wraps=service.plan_probe_configuration)
            service.plan_runtime_uninstall = Mock(wraps=service.plan_runtime_uninstall)
            ctx.runtime_artifact_service = service

            with (
                patch("worlds.kh3.client_support.runtime_artifact_executor.kh3_win64_dir_from_root", return_value=root),
                patch("worlds.kh3.client_support.runtime_artifact_executor.write_probe_config") as write_config,
            ):
                ctx.configure_probe_for_current_bridge()
            write_config.assert_called_once_with(ctx.probe_config_path, bridge_dir)
            service.plan_probe_configuration.assert_called_once()

            self.assertTrue(ctx.has_goa_mod())

            effects: list[str] = []
            ctx.disable_steam_cloud_during_patch_game = lambda: effects.append("cloud") or True
            ctx.backup_vanilla_saves_before_ap_install = lambda: effects.append("backup")
            ctx.ensure_goa_mod_installed = lambda: effects.append("goa")
            with patch(
                "worlds.kh3.client_support.runtime_artifact_executor.install_kh3_runtime_files",
                side_effect=lambda *_args: effects.append("install") or {"status": "missing_kh3"},
            ):
                ctx._install_kh3_runtime_files()
            self.assertEqual(effects, ["cloud", "backup", "goa", "install"])

            effects.clear()
            ctx.restore_vanilla_save_files = lambda: effects.append("restore") or {
                "status": "missing",
                "backup_dir": root / "backup",
                "errors": [],
            }
            ctx.disconnect_from_ap_server_for_vanilla_restore = lambda: effects.append("disconnect")
            with patch(
                "worlds.kh3.client_support.runtime_artifact_executor.uninstall_kh3_ap_files",
                side_effect=lambda *_args: effects.append("uninstall") or {"errors": []},
            ):
                ctx.uninstall_kh3_ap_files()
            self.assertEqual(effects, ["uninstall", "restore", "disconnect"])
            service.plan_runtime_uninstall.assert_called_once_with()


class TestClientCommandService(unittest.TestCase):
    def test_bridge_command_plans_preserve_validation_deduplication_and_one_shot_policy(self) -> None:
        payloads = {
            CLIENT_COMMAND_FILE: {"seq": "7", "cmd": " SAY ", "text": "  hello world  "},
        }
        service = ClientCommandService(payloads.get)

        plan = service.load_bridge_command(BridgeCommandInput(
            last_processed_sequence=6,
            ap_connected=True,
        ))

        self.assertEqual(plan.identity, ClientCommandIdentity(7))
        self.assertEqual(plan.command, ClientCommandKind.SAY)
        self.assertEqual(
            [action.kind for action in plan.actions],
            [
                ClientCommandActionKind.SEND_SAY,
                ClientCommandActionKind.LOG_INFO,
                ClientCommandActionKind.ACKNOWLEDGE,
                ClientCommandActionKind.DELETE_COMMAND_FILE,
            ],
        )
        self.assertEqual(plan.actions[0].text, "hello world")
        self.assertEqual(plan.actions[1].text, "Sent KH3 AP command: %s")
        self.assertEqual(plan.actions[1].log_args, ("hello world",))
        self.assertTrue(plan.acknowledged)
        with self.assertRaises(FrozenInstanceError):
            plan.identity.sequence = 8  # type: ignore[misc]

        disconnected = service.load_bridge_command(BridgeCommandInput(6, ap_connected=False))
        self.assertEqual(disconnected.identity, ClientCommandIdentity(7))
        self.assertEqual(disconnected.command, ClientCommandKind.SAY)
        self.assertEqual(disconnected.actions, ())

        duplicate = service.load_bridge_command(BridgeCommandInput(7, ap_connected=True))
        self.assertEqual(duplicate.identity, ClientCommandIdentity(7))
        self.assertEqual(duplicate.actions, ())

        invalid = ClientCommandService.plan_bridge_payload(
            {"seq": "not-an-integer", "cmd": "say", "text": "hello"},
            BridgeCommandInput(0, ap_connected=True),
        )
        self.assertEqual(invalid, ClientCommandPlan())

        ignored = ClientCommandService.plan_bridge_payload(
            {"seq": 8, "cmd": "future-command", "text": "unused"},
            BridgeCommandInput(7, ap_connected=True),
        )
        self.assertIsNone(ignored.command)
        self.assertEqual(
            [action.kind for action in ignored.actions],
            [ClientCommandActionKind.ACKNOWLEDGE, ClientCommandActionKind.DELETE_COMMAND_FILE],
        )

    def test_facade_command_plans_preserve_dispatch_decisions_and_result_wording(self) -> None:
        unavailable = ClientCommandService.plan_facade_command(
            ClientCommandKind.RESYNC_BRIDGE_FILES,
            FacadeCommandInput(bridge_initialized=False),
        )
        self.assertEqual(
            unavailable.actions,
            (ClientCommandAction(
                ClientCommandActionKind.LOG_INFO,
                text="KH3 bridge directory is not initialized yet.",
            ),),
        )

        resync = ClientCommandService.plan_facade_command(
            ClientCommandKind.RESYNC_BRIDGE_FILES,
            FacadeCommandInput(bridge_initialized=True),
        )
        self.assertEqual(
            [action.kind for action in resync.actions],
            [ClientCommandActionKind.WRITE_BRIDGE_FILES, ClientCommandActionKind.LOG_INFO],
        )
        self.assertEqual(resync.actions[-1].text, "Rewrote KH3 bridge files.")

        blocked_install = ClientCommandService.plan_facade_command(
            ClientCommandKind.INSTALL_KH3_RUNTIME,
            FacadeCommandInput(game_running=True),
        )
        self.assertEqual(blocked_install.actions[0].kind, ClientCommandActionKind.LOG_WARNING)
        self.assertEqual(
            blocked_install.actions[0].text,
            "Close KH3 before patching the game. Patch Game cannot safely update runtime files while KH3 is running.",
        )

        for command, action_kind in (
            (ClientCommandKind.OPEN_KH3_MODS_DIRECTORY, ClientCommandActionKind.OPEN_KH3_MODS_DIRECTORY),
            (ClientCommandKind.OPEN_KH3_DIRECTORY, ClientCommandActionKind.OPEN_KH3_DIRECTORY),
            (ClientCommandKind.OPEN_KH3_SAVE_DIRECTORY, ClientCommandActionKind.OPEN_KH3_SAVE_DIRECTORY),
            (ClientCommandKind.OPEN_PROBE_CONFIG_DIRECTORY, ClientCommandActionKind.OPEN_PROBE_CONFIG_DIRECTORY),
            (ClientCommandKind.LAUNCH_KH3, ClientCommandActionKind.LAUNCH_KH3),
            (ClientCommandKind.INSTALL_KH3_RUNTIME, ClientCommandActionKind.INSTALL_KH3_RUNTIME),
            (ClientCommandKind.DOWNLOAD_GOA_MOD, ClientCommandActionKind.DOWNLOAD_GOA_MOD),
            (ClientCommandKind.EXPORT_DEBUG_BUNDLE, ClientCommandActionKind.EXPORT_DEBUG_BUNDLE),
        ):
            with self.subTest(command=command):
                self.assertEqual(
                    ClientCommandService.plan_facade_command(command).actions[0].kind,
                    action_kind,
                )

    def test_context_command_facade_delegates_planning_and_applies_actions_in_order(self) -> None:
        bridge_service = Mock(spec=ClientCommandService)
        bridge_service.load_bridge_command.return_value = ClientCommandPlan(
            identity=ClientCommandIdentity(12),
            command=ClientCommandKind.SAY,
            actions=(
                ClientCommandAction(ClientCommandActionKind.SEND_SAY, text="status"),
                ClientCommandAction(
                    ClientCommandActionKind.LOG_INFO,
                    text="Sent KH3 AP command: %s",
                    log_args=("status",),
                ),
                ClientCommandAction(ClientCommandActionKind.ACKNOWLEDGE, sequence=12),
                ClientCommandAction(ClientCommandActionKind.DELETE_COMMAND_FILE, text=CLIENT_COMMAND_FILE),
            ),
        )
        ctx = KH3Context.__new__(KH3Context)
        ctx.last_processed_client_command_seq = 4
        ctx.server = SimpleNamespace(socket=SimpleNamespace(closed=False))
        ctx.send_msgs = AsyncMock()
        ctx._delete_bridge_file = Mock()
        ctx._get_client_command_service = lambda: bridge_service

        with patch.object(KH3ClientModule.logger, "info") as log_info:
            asyncio.run(ctx.handle_bridge_client_command_file())

        bridge_service.load_bridge_command.assert_called_once_with(BridgeCommandInput(4, ap_connected=True))
        ctx.send_msgs.assert_awaited_once_with([{"cmd": "Say", "text": "status"}])
        log_info.assert_called_once_with("Sent KH3 AP command: %s", "status")
        self.assertEqual(ctx.last_processed_client_command_seq, 12)
        ctx._delete_bridge_file.assert_called_once_with(CLIENT_COMMAND_FILE)

class TestOfflineBridgeService(unittest.TestCase):
    @staticmethod
    def build_service(**overrides) -> OfflineBridgeService:
        payloads = overrides.get("payloads", {})
        return OfflineBridgeService(
            protocol_name=BRIDGE_PROTOCOL_NAME,
            protocol_version=BRIDGE_PROTOCOL_VERSION,
            world_version="0.0.1",
            seed_pak_pattern=KH3ClientModule.KH3_AP_SEED_PAK_PATTERN,
            list_pak_paths=overrides.get("list_pak_paths", lambda _path: []),
            modified_time=overrides.get("modified_time", lambda _path: 0.0),
            load_installed_metadata=overrides.get("load_installed_metadata", lambda *_args: None),
            infer_slot_name_from_pak=overrides.get("infer_slot_name_from_pak", lambda _path: None),
            validate_slot_data=lambda payload, _source: payload,
            validate_protocol=lambda payload, _source: payload,
            protocol_error=BridgeProtocolError,
            load_optional_json=overrides.get("load_optional_json", lambda path: payloads.get(path.name)),
            load_cached_items=overrides.get("load_cached_items", lambda _path: {"items": []}),
            atomic_write_json=overrides.get("atomic_write_json", lambda *_args: None),
            create_directory=overrides.get("create_directory", lambda _path: None),
            delete_file=overrides.get("delete_file", lambda _path: None),
            safe_name=lambda value: value,
            extract_seed_id=SeedGuardService.extract_seed_id,
            log_info=lambda *_args: None,
        )

    def test_candidate_discovery_and_slot_inference_preserve_precedence(self) -> None:
        mods_dir = Path("mods")
        older = mods_dir / "pakchunk9-Older-randomizer-AP_11111111111111111111-1_99_P.pak"
        newer = mods_dir / "pakchunk9-Newer-randomizer-AP_22222222222222222222-1_99_P.pak"
        unrelated = mods_dir / "pakchunk0-WindowsNoEditor.pak"
        metadata_loader = Mock()
        service = self.build_service(
            list_pak_paths=lambda _path: [older, unrelated, newer],
            modified_time=lambda path: {older: 1.0, unrelated: 3.0, newer: 2.0}[path],
            load_installed_metadata=metadata_loader,
            infer_slot_name_from_pak=lambda path: "Newer" if path == newer else None,
        )
        source = InstalledPakDiscoveryInput(mods_dir, expected_seed="22222222222222222222")

        self.assertEqual(service.infer_slot_name(source), "Newer")
        self.assertEqual(
            service.discover_installed_paks(source),
            [
                {"seed": "22222222222222222222", "name": newer.name, "path": str(newer)},
                {"seed": "11111111111111111111", "name": older.name, "path": str(older)},
            ],
        )
        metadata_loader.assert_not_called()

    def test_initialization_preserves_valid_status_and_cached_items(self) -> None:
        bridge_root = Path("bridge")
        managed_bridge_name = "12345678901234567890_Player--localprofile"
        pak_path = Path("mods") / "seed.pak"
        slot_data = {
            "bridge": {
                "locations": [{"bridge_key": "Treasure:001", "ap_location_id": 1001}],
            },
        }
        metadata = {
            "seed": "AP_12345678901234567890-1-Player",
            "slot": 1,
            "slot_name": "Player",
            "slot_data": slot_data,
            "foreign_item_manifest": {"archive_name": "AP_12345678901234567890.zip"},
        }
        payloads = {
            "ap_status.json": {
                "seed_name": "12345678901234567890",
                "slot": 1,
                "player_name": "Player",
                "checked_locations": [1001],
                "goal_complete": True,
            },
        }
        writes: list[tuple[str, dict]] = []
        service = self.build_service(
            payloads=payloads,
            load_installed_metadata=lambda *_args: (metadata, pak_path),
            load_cached_items=lambda _path: {"items": [{"index": 0}]},
            atomic_write_json=lambda path, payload: writes.append((path.name, payload)),
        )

        result = service.initialize(
            OfflineBridgeInitializationInput(
                current_bridge_dir=None,
                bridge_root=bridge_root,
                mods_dir=pak_path.parent,
                expected_seed="12345678901234567890",
                auth=None,
                server_address=None,
                team=None,
                bridge_dir_name=managed_bridge_name,
            )
        )

        self.assertIsNotNone(result)
        self.assertEqual(result.bridge_dir, bridge_root / managed_bridge_name)
        self.assertEqual(result.location_key_to_id, {"Treasure:001": 1001})
        preserve = service.offline_bridge_matches(result.metadata, result.bridge_dir)
        self.assertTrue(preserve)
        service.write_offline_files(
            source=OfflineBridgeInitializationInput(
                current_bridge_dir=None,
                bridge_root=bridge_root,
                mods_dir=pak_path.parent,
                expected_seed="12345678901234567890",
                auth=None,
                server_address=None,
                team=None,
                bridge_dir_name=managed_bridge_name,
            ),
            metadata=result.metadata,
            pak_path=result.pak_path,
            bridge_dir=result.bridge_dir,
            seed_name=result.offline_seed_name,
            slot_data=result.slot_data,
            location_count=len(result.location_key_to_id),
            preserve_previous_status=preserve,
        )
        self.assertEqual(
            [name for name, _payload in writes],
            [
                "slot_data.json",
                "ap_status.json",
                "kh3_ap_seed_metadata.json",
                "offline_bridge_publication.json",
            ],
        )
        status = writes[1][1]
        self.assertEqual(status["checked_locations"], [1001])
        self.assertEqual(status["received_item_count"], 1)
        self.assertTrue(status["goal_complete"])

    def test_initialization_reuses_matching_managed_bridge_metadata_before_pak_loader(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            mods_dir = root / "mods"
            bridge_root = root / "bridge"
            mods_dir.mkdir()
            pak_path = mods_dir / "pakchunk9-Player_Name-randomizer-AP_12345678901234567890-2_99_P.pak"
            pak_path.write_bytes(b"pak")
            bridge_dir = bridge_root / "12345678901234567890_Player_Name"
            bridge_dir.mkdir(parents=True)
            slot_data = {
                "bridge": {
                    "locations": [{"bridge_key": "Treasure:001", "ap_location_id": 1001}],
                },
            }
            metadata = {
                "format": "kh3_ap_seed_metadata",
                "seed": "AP_12345678901234567890-2-Player_Name",
                "slot": 2,
                "slot_name": "Player Name",
                "slot_data": slot_data,
                "foreign_item_manifest": {"archive_name": "AP_12345678901234567890.zip"},
            }
            atomic_write_json(bridge_dir / "kh3_ap_seed_metadata.json", metadata)
            pak_loader = Mock()
            service = self.build_service(
                list_pak_paths=lambda _path: [pak_path],
                modified_time=lambda path: path.stat().st_mtime,
                load_installed_metadata=pak_loader,
                infer_slot_name_from_pak=lambda _path: "Player_Name",
                load_optional_json=load_optional_json_file,
            )

            result = service.initialize(OfflineBridgeInitializationInput(
                current_bridge_dir=None,
                bridge_root=bridge_root,
                mods_dir=mods_dir,
                expected_seed="12345678901234567890",
                auth=None,
                server_address=None,
                team=None,
                bridge_dir_name=bridge_dir.name,
            ))

            self.assertIsNotNone(result)
            self.assertEqual(result.metadata, metadata)
            self.assertEqual(result.pak_path, pak_path)
            self.assertEqual(result.bridge_dir, bridge_dir)
            pak_loader.assert_not_called()

    def test_matching_status_identity_avoids_loading_large_cached_metadata(self) -> None:
        metadata = {
            "seed": "AP_12345678901234567890-1-Player",
            "slot": 1,
            "slot_name": "Player",
            "foreign_item_manifest": {"archive_name": "AP_12345678901234567890.zip"},
        }
        loaded_files: list[str] = []

        def load_optional_json(path: Path):
            loaded_files.append(path.name)
            if path.name == "ap_status.json":
                return {
                    "seed_name": "12345678901234567890",
                    "slot": 1,
                    "player_name": "Player",
                }
            if path.name == "kh3_ap_seed_metadata.json":
                raise AssertionError("large cached metadata should not be loaded")
            return None

        service = self.build_service(load_optional_json=load_optional_json)

        self.assertTrue(service.offline_bridge_matches(metadata, Path("bridge") / "seed_Player"))
        self.assertEqual(loaded_files, ["ap_status.json"])

    def test_publication_manifest_skips_unchanged_large_bridge_payload_reads(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bridge_dir = root / "bridge"
            bridge_dir.mkdir()
            pak_path = root / "seed.pak"
            managed_pak = root / "managed" / pak_path.name
            managed_pak.parent.mkdir()
            managed_pak.write_bytes(b"pak")
            shutil.copy2(managed_pak, pak_path)
            slot_data = {
                "bridge": {
                    "locations": [{"bridge_key": "Treasure:001", "ap_location_id": 1001}],
                },
            }
            metadata = {
                "seed": "AP_12345678901234567890-1-Player",
                "slot": 1,
                "slot_name": "Player",
                "slot_data": slot_data,
                "foreign_item_manifest": {"archive_name": "AP_12345678901234567890.zip"},
            }
            source = OfflineBridgeInitializationInput(
                current_bridge_dir=None,
                bridge_root=bridge_dir.parent,
                mods_dir=pak_path.parent,
                expected_seed="12345678901234567890",
                auth=None,
                server_address=None,
                team=None,
            )
            loaded_files: list[str] = []

            def load_optional_json(path: Path):
                loaded_files.append(path.name)
                return load_optional_json_file(path)

            service = self.build_service(
                load_optional_json=load_optional_json,
                load_cached_items=lambda path: (
                    load_optional_json_file(path / "ap_items.json") if path is not None else {"items": []}
                ) or {"items": []},
                atomic_write_json=atomic_write_json,
            )
            arguments = {
                "source": source,
                "metadata": metadata,
                "pak_path": pak_path,
                "bridge_dir": bridge_dir,
                "seed_name": "12345678901234567890",
                "slot_data": slot_data,
                "location_count": 1,
                "preserve_previous_status": False,
            }

            service.write_offline_files(**arguments)
            for filename, expected in (("slot_data.json", slot_data), ("kh3_ap_seed_metadata.json", metadata)):
                cached = (bridge_dir / filename).read_bytes()
                self.assertEqual(json.loads(cached), expected)
                self.assertNotIn(b"\n", cached)
            slot_mtime = (bridge_dir / "slot_data.json").stat().st_mtime_ns
            metadata_mtime = (bridge_dir / "kh3_ap_seed_metadata.json").stat().st_mtime_ns
            loaded_files.clear()
            pak_path.unlink()
            shutil.copy2(managed_pak, pak_path)

            service.write_offline_files(**arguments)

            self.assertNotIn("slot_data.json", loaded_files)
            self.assertNotIn("kh3_ap_seed_metadata.json", loaded_files)
            self.assertEqual((bridge_dir / "slot_data.json").stat().st_mtime_ns, slot_mtime)
            self.assertEqual((bridge_dir / "kh3_ap_seed_metadata.json").stat().st_mtime_ns, metadata_mtime)

    def test_context_offline_initialization_delegates_and_keeps_handoff_order(self) -> None:
        ctx = KH3Context.__new__(KH3Context)
        ctx.bridge_dir = None
        ctx.bridge_root_override = Path("bridge")
        ctx.kh3_mods_dir = Path("mods")
        ctx.auth = None
        ctx.server_address = None
        ctx.team = None
        ctx.location_key_to_id = {}
        ctx.location_id_to_bridge_key = {}
        result = OfflineBridgeInitializationResult(
            bridge_dir=Path("bridge") / "seed_Player",
            offline_seed_name="seed",
            auth="Player",
            slot_data={"bridge": {"locations": []}},
            location_key_to_id={"Treasure:001": 1001},
            location_id_to_bridge_key={1001: "Treasure:001"},
            metadata={"seed": "seed"},
            pak_path=Path("mods") / "seed.pak",
        )
        service = Mock(spec=OfflineBridgeService)
        service.initialize.return_value = result
        service.offline_bridge_matches.return_value = True
        ctx.offline_bridge_service = service
        ctx.expected_seed_id = Mock(return_value="seed")
        handoff: list[str] = []
        ctx.rebuild_location_display_indexes = lambda: handoff.append("indexes")
        ctx.ensure_bridge_observation_state = lambda **_kwargs: handoff.append("observations")
        ctx.mark_existing_bridge_notifications_processed = lambda: handoff.append("notifications")
        ctx.mark_existing_runtime_game_over_processed = lambda: handoff.append("game_over")
        ctx.configure_probe_for_current_bridge = lambda: handoff.append("probe")
        ctx.start_integrated_bridge = lambda: handoff.append("bridge")
        ctx.refresh_save_seed_guard = Mock(return_value=True)

        with patch("worlds.kh3.Client.configure_python_profiler"):
            self.assertTrue(ctx.try_initialize_offline_bridge_from_installed_pak())

        self.assertEqual(ctx.bridge_dir, result.bridge_dir)
        self.assertEqual(ctx.offline_seed_name, "seed")
        self.assertEqual(
            handoff,
            [
                "indexes",
                "observations",
                "notifications",
                "game_over",
                "probe",
                "bridge",
            ],
        )
        service.initialize.assert_called_once()

    def test_context_deferred_offline_initialization_applies_after_background_load(self) -> None:
        ctx = KH3Context.__new__(KH3Context)
        ctx.bridge_dir = None
        ctx.seed_name = None
        ctx.server = None
        initialization_input = OfflineBridgeInitializationInput(
            current_bridge_dir=None,
            bridge_root=Path("bridge"),
            mods_dir=Path("mods"),
            expected_seed=None,
            auth=None,
            server_address=None,
            team=None,
        )
        pak_path = (Path("mods") / "seed.pak").resolve()
        result = OfflineBridgeInitializationResult(
            bridge_dir=Path("bridge") / "seed_Player",
            offline_seed_name="seed",
            auth="Player",
            slot_data={"bridge": {"locations": []}},
            location_key_to_id={},
            location_id_to_bridge_key={},
            metadata={"seed": "seed"},
            pak_path=pak_path,
        )
        caller_thread_id = threading.get_ident()
        worker_thread_ids: list[int] = []

        def initialize_in_worker(_source: OfflineBridgeInitializationInput) -> OfflineBridgeInitializationResult:
            worker_thread_ids.append(threading.get_ident())
            return result

        service = Mock(spec=OfflineBridgeService)
        service.initialize.side_effect = initialize_in_worker
        profile = KH3RunConnectionProfile(
            run_id="seed-1-Player",
            slot_name="Player",
            connection_slot_name="Room Player",
            server_address="wss://archipelago.gg:45678",
        )
        run_manager = Mock()
        run_manager.active_connection_profile.return_value = profile
        ctx._get_offline_bridge_service = Mock(return_value=service)
        ctx._get_run_manager_service = Mock(return_value=run_manager)
        ctx._offline_bridge_initialization_input = Mock(return_value=initialization_input)
        ctx.installed_kh3_ap_seed_paks = Mock(return_value=[{"path": str(pak_path)}])
        ctx._apply_offline_bridge_initialization = Mock(return_value=True)
        ctx._apply_active_kh3_run_connection_profile = Mock()

        self.assertTrue(asyncio.run(ctx.initialize_offline_bridge_after_startup()))

        service.initialize.assert_called_once_with(initialization_input)
        self.assertEqual(len(worker_thread_ids), 1)
        self.assertNotEqual(worker_thread_ids[0], caller_thread_id)
        ctx._apply_offline_bridge_initialization.assert_called_once_with(result)
        ctx._apply_active_kh3_run_connection_profile.assert_called_once_with(profile)
        run_manager.ensure_pak_managed.assert_called_once_with(
            pak_path, expected_seed_id=result.offline_seed_name, server_address=None,
            connection_slot_name=None, room_url=None,
        )


class TestBridgeSyncService(unittest.TestCase):
    @staticmethod
    def build_service(**overrides) -> BridgeSyncService:
        recorded_locations = overrides.get("recorded_locations", set())

        async def send_msgs(_messages) -> None:
            return None

        return BridgeSyncService(
            protocol_name=BRIDGE_PROTOCOL_NAME,
            protocol_version=BRIDGE_PROTOCOL_VERSION,
            load_optional_json=load_optional_json_file,
            validate_protocol=validate_protocol_payload,
            validate_slot_data=lambda payload, _source: payload,
            protocol_error=BridgeProtocolError,
            atomic_write_json=overrides.get("atomic_write_json", atomic_write_json),
            safe_name=lambda value: value,
            configure_profiler=overrides.get("configure_profiler", lambda _path: None),
            mark_existing_notifications=overrides.get("mark_existing_notifications", lambda: None),
            installed_pak_compatible=overrides.get("installed_pak_compatible", lambda: True),
            build_installed_pak_status=lambda: "OK",
            refresh_save_seed_guard=overrides.get("refresh_save_seed_guard", lambda: True),
            build_save_seed_status=lambda: "OK",
            record_locations=overrides.get("record_locations", recorded_locations.update),
            send_msgs=overrides.get("send_msgs", send_msgs),
            publish_bridge_files=overrides.get("publish_bridge_files", lambda: None),
            profile_scope=lambda _name: nullcontext(),
            log_info=lambda *_args: None,
            log_warning=overrides.get("log_warning", lambda *_args: None),
            goal_status=KH3ClientModule.ClientStatus.CLIENT_GOAL,
        )

    def test_initialization_preserves_observations_and_resets_stale_transport_state(self) -> None:
        with TemporaryDirectory() as temp_dir:
            bridge_root = Path(temp_dir)
            bridge_dir = bridge_root / "seed_Player--localprofile"
            bridge_dir.mkdir()
            (bridge_dir / "game_state.json").write_text(json.dumps({
                "protocol_name": BRIDGE_PROTOCOL_NAME,
                "protocol_version": BRIDGE_PROTOCOL_VERSION,
                "seq": 8,
                "checked_locations": ["Treasure:001"],
                "goal_complete": True,
            }), encoding="utf-8")
            service = self.build_service()

            result = service.initialize(BridgeInitializationInput(
                slot_data={"bridge": {"locations": [{
                    "bridge_key": "Treasure:001",
                    "ap_location_id": 10,
                }]}},
                bridge_root=bridge_root,
                seed_name="seed",
                player_name="Player",
                previous_bridge_dir=bridge_root / "old",
                checked_locations=set(),
                locations_checked=set(),
                finished_game=False,
                bridge_dir_name=bridge_dir.name,
            ))

            reset_payload = json.loads((bridge_dir / "game_state.json").read_text(encoding="utf-8"))
            self.assertEqual(result.location_key_to_id, {"Treasure:001": 10})
            self.assertEqual(result.locations_checked, {10})
            self.assertTrue(result.has_unreported_observations)
            self.assertEqual(reset_payload["checked_locations"], ["Treasure:001"])
            self.assertTrue(reset_payload["goal_complete"])
            self.assertEqual(service.last_sequence, -1)

    def test_initialization_reuses_compatible_same_bridge_reconciliation_state(self) -> None:
        with TemporaryDirectory() as temp_dir:
            bridge_root = Path(temp_dir)
            bridge_dir = bridge_root / "seed_Player"
            bridge_dir.mkdir()
            slot_data = {"bridge": {"locations": [{
                "bridge_key": "Treasure:001",
                "ap_location_id": 10,
            }]}}
            (bridge_dir / "slot_data.json").write_text(json.dumps(slot_data), encoding="utf-8")
            original_game_state = {
                "protocol_name": BRIDGE_PROTOCOL_NAME,
                "protocol_version": BRIDGE_PROTOCOL_VERSION,
                "seq": 8,
                "checked_locations": ["Treasure:001"],
                "goal_complete": False,
            }
            (bridge_dir / "game_state.json").write_text(json.dumps(original_game_state), encoding="utf-8")

            service = self.build_service()
            result = service.initialize(BridgeInitializationInput(
                slot_data=slot_data,
                bridge_root=bridge_root,
                seed_name="seed",
                player_name="Player",
                previous_bridge_dir=bridge_dir,
                checked_locations={10},
                locations_checked={10},
                finished_game=False,
            ))

            self.assertTrue(result.reused_existing_state)
            self.assertEqual(
                json.loads((bridge_dir / "game_state.json").read_text(encoding="utf-8")),
                original_game_state,
            )

    def test_initialization_does_not_seed_server_only_checks_into_local_observations(self) -> None:
        with TemporaryDirectory() as temp_dir:
            bridge_root = Path(temp_dir)
            bridge_dir = bridge_root / "seed_Player"
            bridge_dir.mkdir()
            slot_data = {"bridge": {"locations": [{
                "bridge_key": "Treasure:001",
                "ap_location_id": 10,
            }]}}
            (bridge_dir / "slot_data.json").write_text(json.dumps(slot_data), encoding="utf-8")
            original_game_state = {
                "protocol_name": BRIDGE_PROTOCOL_NAME,
                "protocol_version": BRIDGE_PROTOCOL_VERSION,
                "seq": 8,
                "checked_locations": [],
                "goal_complete": False,
            }
            (bridge_dir / "game_state.json").write_text(json.dumps(original_game_state), encoding="utf-8")

            service = self.build_service()
            result = service.initialize(BridgeInitializationInput(
                slot_data=slot_data,
                bridge_root=bridge_root,
                seed_name="seed",
                player_name="Player",
                previous_bridge_dir=bridge_dir,
                checked_locations={10},
                locations_checked=set(),
                finished_game=False,
            ))

            self.assertTrue(result.reused_existing_state)
            self.assertEqual(
                json.loads((bridge_dir / "game_state.json").read_text(encoding="utf-8")),
                original_game_state,
            )

    def test_game_state_transport_is_monotonic_and_preserves_message_order(self) -> None:
        with TemporaryDirectory() as temp_dir:
            bridge_dir = Path(temp_dir)
            (bridge_dir / "game_state.json").write_text(json.dumps({
                "protocol_name": BRIDGE_PROTOCOL_NAME,
                "protocol_version": BRIDGE_PROTOCOL_VERSION,
                "seq": 3,
                "checked_locations": ["Treasure:001"],
                "goal_complete": True,
            }), encoding="utf-8")
            messages: list[list[dict]] = []
            publications: list[bool] = []
            recorded: set[int] = set()

            async def send_msgs(payload: list[dict]) -> None:
                messages.append(payload)

            service = self.build_service(
                recorded_locations=recorded,
                send_msgs=send_msgs,
                publish_bridge_files=lambda: publications.append(True),
            )
            source = GameStateSyncInput(bridge_dir, {"Treasure:001": 10}, True)
            first = asyncio.run(service.synchronize_game_state(source))
            second = asyncio.run(service.synchronize_game_state(source))

            self.assertTrue(first.processed)
            self.assertFalse(second.processed)
            self.assertEqual(recorded, {10})
            self.assertEqual(messages, [
                [{"cmd": "LocationChecks", "locations": [10]}],
                [{"cmd": "StatusUpdate", "status": KH3ClientModule.ClientStatus.CLIENT_GOAL}],
            ])
            self.assertEqual(publications, [True])
            self.assertEqual(service.last_sequence, 3)
            self.assertTrue(service.goal_reported)

    def test_publish_skips_unchanged_bridge_files(self) -> None:
        with TemporaryDirectory() as temp_dir:
            bridge_dir = Path(temp_dir)
            payloads = {
                "slot_data.json": {"slot": 1},
                "ap_status.json": {"connected": True},
                "ap_items.json": {"items": []},
            }
            for file_name, payload in payloads.items():
                (bridge_dir / file_name).write_text(json.dumps(payload), encoding="utf-8")
            writer = Mock()
            service = self.build_service(atomic_write_json=writer)

            service.publish(
                bridge_dir,
                slot_data=payloads["slot_data.json"],
                status_payload=payloads["ap_status.json"],
                items_payload=payloads["ap_items.json"],
            )

            writer.assert_not_called()

    def test_atomic_json_write_retries_a_transient_replace_lock(self) -> None:
        with TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "ap_status.json"
            real_replace = os.replace
            replace_attempts = 0

            def locked_then_replace(source: Path, destination: Path) -> None:
                nonlocal replace_attempts
                replace_attempts += 1
                if replace_attempts < 3:
                    raise PermissionError("simulated Windows replace lock")
                real_replace(source, destination)

            with (
                patch("worlds.kh3.bridge_support.files.os.replace", side_effect=locked_then_replace),
                patch("worlds.kh3.bridge_support.files.time.sleep") as sleep,
            ):
                atomic_write_json(path, {"connected": True}, attempts=3, delay_seconds=0.05)

            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"connected": True})
            self.assertEqual(replace_attempts, 3)
            self.assertEqual([call.args[0] for call in sleep.call_args_list], [0.05, 0.1])

    def test_failed_game_state_publication_retries_without_resending_messages(self) -> None:
        with TemporaryDirectory() as temp_dir:
            bridge_dir = Path(temp_dir)
            (bridge_dir / "game_state.json").write_text(json.dumps({
                "protocol_name": BRIDGE_PROTOCOL_NAME,
                "protocol_version": BRIDGE_PROTOCOL_VERSION,
                "seq": 4,
                "checked_locations": ["Treasure:001"],
                "goal_complete": False,
            }), encoding="utf-8")
            messages: list[list[dict]] = []
            publication_attempts = 0

            async def send_msgs(payload: list[dict]) -> None:
                messages.append(payload)

            def publish_bridge_files() -> None:
                nonlocal publication_attempts
                publication_attempts += 1
                if publication_attempts == 1:
                    raise PermissionError("simulated locked ap_status.json")

            service = self.build_service(
                send_msgs=send_msgs,
                publish_bridge_files=publish_bridge_files,
            )
            source = GameStateSyncInput(bridge_dir, {"Treasure:001": 10}, True)

            with self.assertRaises(PermissionError):
                asyncio.run(service.synchronize_game_state(source))
            retry = asyncio.run(service.synchronize_game_state(source))
            settled = asyncio.run(service.synchronize_game_state(source))

            self.assertTrue(retry.processed)
            self.assertFalse(settled.processed)
            self.assertEqual(publication_attempts, 2)
            self.assertEqual(messages, [[{"cmd": "LocationChecks", "locations": [10]}]])
            self.assertEqual(service.last_sequence, 4)
            self.assertIsNotNone(service.last_file_signature)


class TestBridgeLifecycleService(unittest.TestCase):
    @staticmethod
    def build_service(context: SimpleNamespace, **overrides) -> BridgeLifecycleService:
        async def server_loop(_context, _address) -> None:
            await asyncio.Event().wait()

        async def bridge_watcher(_context) -> None:
            await asyncio.Event().wait()

        return BridgeLifecycleService(
            context,
            server_loop=overrides.get("server_loop", server_loop),
            bridge_watcher=overrides.get("bridge_watcher", bridge_watcher),
            integrated_bridge_worker=overrides.get("integrated_bridge_worker", lambda *_args: 0),
            log_info=lambda *_args: None,
            log_warning=overrides.get("log_warning", lambda *_args: None),
            log_exception=lambda *_args: None,
            shutdown_timeout_seconds=overrides.get("shutdown_timeout_seconds", 1.0),
        )

    def test_start_is_idempotent_and_records_server_watcher_and_gui_ownership(self) -> None:
        async def run_case() -> tuple[list[str], list[asyncio.Task]]:
            gui_started: list[str] = []
            gui_release = asyncio.Event()

            async def gui_runner() -> None:
                await gui_release.wait()

            context = SimpleNamespace(server_address=None, server_task=None, ui_task=None)

            def run_gui() -> None:
                gui_started.append("gui")
                context.ui_task = asyncio.create_task(gui_runner(), name="UI")

            context.run_gui = run_gui
            context.run_cli = lambda: None
            context.shutdown = AsyncMock()
            service = self.build_service(context)

            server = service.start_server("localhost:50002", task_name="server loop")
            watcher = service.start_bridge_watcher()
            gui = service.start_gui()
            tasks = [server, watcher, gui]
            self.assertIs(server, service.start_server("ignored", task_name="different"))
            self.assertIs(watcher, service.start_bridge_watcher())
            self.assertIs(gui, service.start_gui())
            self.assertIs(context.server_task, service.state.server_task)
            self.assertIs(context.ui_task, service.state.gui_task)

            gui_release.set()
            server.cancel()
            watcher.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            return gui_started, tasks

        gui_started, tasks = asyncio.run(run_case())

        self.assertEqual(gui_started, ["gui"])
        self.assertEqual([task.get_name() for task in tasks], ["server loop", "KH3BridgeWatcher", "UI"])

    def test_integrated_bridge_owns_one_stop_event_and_awaits_worker(self) -> None:
        async def run_case() -> tuple[threading.Event, asyncio.Task | None]:
            worker_started = threading.Event()

            def worker(_bridge_dir: Path, _save_dir: Path, stop_event: threading.Event) -> int:
                worker_started.set()
                stop_event.wait(2)
                return 0

            context = SimpleNamespace(server_address=None, server_task=None, ui_task=None)
            context.run_gui = lambda: None
            context.run_cli = lambda: None
            context.shutdown = AsyncMock()
            service = self.build_service(context, integrated_bridge_worker=worker)
            bridge_dir = Path("synthetic-bridge")
            save_dir = Path("synthetic-save")

            service.start_integrated_bridge(enabled=True, bridge_dir=bridge_dir, save_dir=save_dir)
            task = service.state.integrated_bridge_task
            stop_event = service.state.integrated_bridge_stop_event
            self.assertIsNotNone(task)
            self.assertIsNotNone(stop_event)
            service.start_integrated_bridge(enabled=True, bridge_dir=bridge_dir, save_dir=save_dir)
            self.assertIs(task, service.state.integrated_bridge_task)
            await asyncio.to_thread(worker_started.wait, 2)
            service.stop_integrated_bridge()
            service.stop_integrated_bridge()
            await service.wait_for_integrated_bridge_shutdown()
            return stop_event, service.state.integrated_bridge_task

        stop_event, task = asyncio.run(run_case())

        self.assertTrue(stop_event.is_set())
        self.assertIsNone(task)

    def test_stalled_integrated_bridge_reports_stack_and_cannot_pin_exit(self) -> None:
        worker_started = threading.Event()
        worker_release = threading.Event()
        warnings: list[tuple] = []

        def worker(_bridge_dir: Path, _save_dir: Path, _stop_event: threading.Event) -> int:
            worker_started.set()
            worker_release.wait(2)
            return 0

        async def run_case() -> tuple[threading.Thread, bool]:
            context = SimpleNamespace(server_address=None, server_task=None, ui_task=None)
            context.run_gui = lambda: None
            context.run_cli = lambda: None
            context.shutdown = AsyncMock()
            service = self.build_service(
                context,
                integrated_bridge_worker=worker,
                log_warning=lambda *args: warnings.append(args),
                shutdown_timeout_seconds=0.1,
            )
            service.start_integrated_bridge(
                enabled=True,
                bridge_dir=Path("synthetic-bridge"),
                save_dir=Path("synthetic-save"),
            )
            while not worker_started.is_set():
                await asyncio.sleep(0.001)
            thread = service.state.integrated_bridge_thread
            self.assertIsNotNone(thread)

            await service.shutdown()
            return thread, service.state.integrated_bridge_thread is thread

        try:
            thread, worker_tracked = asyncio.run(run_case())
        finally:
            worker_release.set()

        thread.join(1)
        self.assertTrue(thread.daemon)
        self.assertFalse(thread.is_alive())
        self.assertTrue(worker_tracked)
        self.assertEqual(len(warnings), 1)
        self.assertIn("integrated bridge", warnings[0][1])
        self.assertIn("worker_release.wait", warnings[0][3])

    def test_shutdown_continues_when_base_client_teardown_stalls(self) -> None:
        warnings: list[tuple] = []

        async def run_case() -> None:
            context = SimpleNamespace(server_address="localhost", server_task=None, ui_task=None)
            context.run_gui = lambda: None
            context.run_cli = lambda: None

            async def shutdown() -> None:
                await asyncio.Event().wait()

            context.shutdown = shutdown
            service = self.build_service(
                context,
                log_warning=lambda *args: warnings.append(args),
                shutdown_timeout_seconds=0.1,
            )
            await service.shutdown()

        asyncio.run(run_case())

        self.assertEqual(len(warnings), 1)
        self.assertEqual(warnings[0][1], "base client")

    def test_shutdown_timeout_does_not_wait_for_cancellation_resistant_task(self) -> None:
        warnings: list[tuple] = []

        async def run_case() -> tuple[bool, bool]:
            cancellation_seen = asyncio.Event()
            release = asyncio.Event()

            async def cancellation_resistant_shutdown() -> None:
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    cancellation_seen.set()
                    await release.wait()

            context = SimpleNamespace(server_address="localhost", server_task=None, ui_task=None)
            context.run_gui = lambda: None
            context.run_cli = lambda: None
            context.shutdown = cancellation_resistant_shutdown
            service = self.build_service(
                context,
                log_warning=lambda *args: warnings.append(args),
                shutdown_timeout_seconds=0.1,
            )

            shutdown_task = asyncio.create_task(service.shutdown())
            await asyncio.wait_for(shutdown_task, timeout=0.5)
            pending_after_return = cancellation_seen.is_set()
            release.set()
            await asyncio.sleep(0)
            return pending_after_return, shutdown_task.done()

        cancellation_seen, shutdown_completed = asyncio.run(run_case())

        self.assertTrue(cancellation_seen)
        self.assertTrue(shutdown_completed)
        self.assertEqual(len(warnings), 1)
        self.assertEqual(warnings[0][1], "base client")

    def test_shutdown_preserves_context_watcher_integrated_await_order(self) -> None:
        order: list[str] = []

        class RecordingAwaitable:
            def __init__(self, label: str) -> None:
                self.label = label

            def __await__(self):
                order.append(self.label)
                if False:
                    yield None

        async def run_case() -> None:
            stop_event = threading.Event()
            context = SimpleNamespace(server_address="localhost", server_task=None, ui_task=None)
            context.run_gui = lambda: None
            context.run_cli = lambda: None

            async def shutdown() -> None:
                order.append(f"context:{stop_event.is_set()}")

            context.shutdown = shutdown
            service = self.build_service(context)
            service.state.integrated_bridge_stop_event = stop_event
            service.state.bridge_watcher_task = RecordingAwaitable("watcher")
            service.state.integrated_bridge_task = RecordingAwaitable("integrated")

            await service.shutdown()

        asyncio.run(run_case())

        self.assertEqual(order, ["context:True", "watcher", "integrated"])

    def test_wait_swallows_owned_task_cancellation_after_signaling_stop(self) -> None:
        async def run_case() -> tuple[bool, bool]:
            context = SimpleNamespace(server_address=None, server_task=None, ui_task=None)
            context.run_gui = lambda: None
            context.run_cli = lambda: None
            context.shutdown = AsyncMock()
            service = self.build_service(context)
            stop_event = threading.Event()
            task = asyncio.create_task(asyncio.Event().wait())
            service.state.integrated_bridge_stop_event = stop_event
            service.state.integrated_bridge_task = task
            task.cancel()

            await service.wait_for_integrated_bridge_shutdown()
            return stop_event.is_set(), task.cancelled()

        stop_signaled, task_cancelled = asyncio.run(run_case())

        self.assertTrue(stop_signaled)
        self.assertTrue(task_cancelled)

    def test_context_lifecycle_methods_and_state_attributes_remain_delegates(self) -> None:
        async def run_case() -> None:
            service = Mock(spec=BridgeLifecycleService)
            service.state = LifecycleState()
            service.run_integrated_bridge = AsyncMock()
            service.wait_for_integrated_bridge_shutdown = AsyncMock()
            ctx = KH3Context.__new__(KH3Context)
            ctx.lifecycle_service = service
            ctx.integrated_bridge_enabled = True
            ctx.bridge_dir = Path("synthetic-bridge")
            ctx.bridge_save_dir = Path("synthetic-save")
            stop_event = threading.Event()
            task = Mock(spec=asyncio.Task)

            ctx.integrated_bridge_task = task
            ctx.integrated_bridge_stop_event = stop_event
            ctx.integrated_bridge_dir = ctx.bridge_dir
            ctx.integrated_bridge_save_dir = ctx.bridge_save_dir
            ctx.start_integrated_bridge()
            ctx.stop_integrated_bridge()
            await ctx.wait_for_integrated_bridge_shutdown()
            await ctx.run_integrated_bridge(ctx.bridge_dir, ctx.bridge_save_dir, stop_event)

            self.assertIs(ctx.integrated_bridge_task, task)
            self.assertIs(ctx.integrated_bridge_stop_event, stop_event)
            self.assertEqual(ctx.integrated_bridge_dir, ctx.bridge_dir)
            self.assertEqual(ctx.integrated_bridge_save_dir, ctx.bridge_save_dir)
            service.start_integrated_bridge.assert_called_once_with(
                enabled=True,
                bridge_dir=ctx.bridge_dir,
                save_dir=ctx.bridge_save_dir,
            )
            service.stop_integrated_bridge.assert_called_once_with()
            service.wait_for_integrated_bridge_shutdown.assert_awaited_once_with()
            service.run_integrated_bridge.assert_awaited_once_with(ctx.bridge_dir, ctx.bridge_save_dir, stop_event)

        asyncio.run(run_case())

        self.assertIs(KH3ClientModule.BridgeLifecycleService, BridgeLifecycleService)

    def test_bridge_watcher_uses_configured_poll_interval(self) -> None:
        async def run_case() -> list[float]:
            sleeps: list[float] = []
            context = SimpleNamespace(
                exit_event=asyncio.Event(),
                bridge_dir=None,
                bridge_poll_interval=0.125,
            )

            async def fake_wait(waiter, timeout: float) -> None:
                waiter.close()
                delay = timeout
                sleeps.append(delay)
                context.exit_event.set()

            with patch("worlds.kh3.Client.asyncio.wait_for", side_effect=fake_wait):
                await KH3ClientModule.bridge_watcher(context)
            return sleeps

        self.assertEqual(asyncio.run(run_case()), [0.125])

    def test_bridge_watcher_retries_failed_game_state_without_stopping_other_steps(self) -> None:
        async def run_case() -> tuple[list[str], list[float]]:
            calls: list[str] = []
            sleeps: list[float] = []
            context = SimpleNamespace(
                exit_event=asyncio.Event(),
                bridge_dir=Path("synthetic-bridge"),
                bridge_poll_interval=0.125,
                slot=None, server=None, slot_data={}, location_key_to_id={}, _is_ap_connected=lambda: False,
            )

            async def game_state() -> None:
                calls.append("game_state")
                if calls.count("game_state") == 1:
                    raise ConnectionError("temporary AP send failure")

            async def successful_step(name: str) -> None:
                calls.append(name)

            context.handle_game_state_file = game_state
            context.handle_runtime_deathlink_game_over = lambda: successful_step("deathlink")
            context.handle_poptracker_runtime_context_bounce = lambda: successful_step("poptracker")
            context.handle_poptracker_lucky_emblem_state = lambda: successful_step("lucky_emblems")
            context.handle_secret_report_hints = lambda: successful_step("secret_report_hints")
            context.handle_bridge_notifications_file = lambda: successful_step("notifications")
            context.handle_bridge_client_command_file = lambda: successful_step("command")
            context.handle_mom_challenge_results = lambda: successful_step("mom_challenges")

            async def fake_wait(waiter, timeout: float) -> None:
                waiter.close()
                delay = timeout
                sleeps.append(delay)
                if len(sleeps) == 2:
                    context.exit_event.set()

            with (
                patch("worlds.kh3.Client.asyncio.wait_for", side_effect=fake_wait),
                patch("worlds.kh3.Client.logger.exception") as log_exception,
            ):
                await KH3ClientModule.bridge_watcher(context)

            log_exception.assert_called_once_with(
                "KH3 bridge watcher step %s failed; retrying on the next poll.",
                "game_state",
            )
            return calls, sleeps

        calls, sleeps = asyncio.run(run_case())

        self.assertEqual(calls.count("game_state"), 2)
        self.assertEqual(calls.count("deathlink"), 1)
        self.assertEqual(calls.count("lucky_emblems"), 1)
        self.assertEqual(calls.count("command"), 1)
        self.assertEqual(calls.count("mom_challenges"), 1)
        self.assertEqual(sleeps, [0.125, 0.125])

    def test_bridge_watcher_propagates_cancellation(self) -> None:
        async def run_case() -> None:
            context = SimpleNamespace(
                exit_event=asyncio.Event(),
                bridge_dir=Path("synthetic-bridge"),
                bridge_poll_interval=0.125,
                slot=None, server=None, slot_data={}, location_key_to_id={}, _is_ap_connected=lambda: False,
                handle_game_state_file=AsyncMock(side_effect=asyncio.CancelledError),
                handle_runtime_deathlink_game_over=AsyncMock(),
                handle_poptracker_runtime_context_bounce=AsyncMock(),
                handle_poptracker_lucky_emblem_state=AsyncMock(),
                handle_secret_report_hints=AsyncMock(),
                handle_bridge_notifications_file=AsyncMock(),
                handle_bridge_client_command_file=AsyncMock(),
                handle_mom_challenge_results=AsyncMock(),
            )

            with self.assertRaises(asyncio.CancelledError):
                await KH3ClientModule.bridge_watcher(context)

            context.handle_runtime_deathlink_game_over.assert_not_awaited()

        asyncio.run(run_case())

    def test_lucky_emblem_state_handler_sends_once_per_native_snapshot(self) -> None:
        ctx = KH3Context.__new__(KH3Context)
        ctx.bridge_dir = Path("synthetic-bridge")
        ctx.slot = 3
        ctx.team = 2
        ctx.server = SimpleNamespace(socket=SimpleNamespace(closed=False))
        ctx.player_names = {3: "PlayerKH"}
        ctx.auth = "PlayerKH"
        ctx.seed_name = "UnitSeed"
        ctx.offline_seed_name = None
        ctx._load_bridge_sidecar_payload = Mock(return_value={
            "sequence": 2,
            "captured_global_ids": [13, 14],
            "latest_capture_global_id": 14,
            "observed_at_utc": "2026-09-02T21:00:00Z",
        })
        ctx.send_msgs = AsyncMock()

        asyncio.run(ctx.handle_poptracker_lucky_emblem_state())
        asyncio.run(ctx.handle_poptracker_lucky_emblem_state())

        ctx.send_msgs.assert_awaited_once()
        messages = ctx.send_msgs.await_args.args[0]
        self.assertEqual(messages[0]["cmd"], "Set")
        self.assertEqual(messages[0]["key"], "kh3_lucky_emblems_2_3")
        self.assertEqual(
            messages[0]["operations"][0]["value"],
            {"13": True, "14": True},
        )
        self.assertEqual(messages[1]["cmd"], "Bounce")
        self.assertEqual(
            messages[1]["data"]["kh3_lucky_emblem_latest_global_id"],
            14,
        )


class TestItemSyncService(unittest.TestCase):
    @staticmethod
    def build_service(*, warnings: list[tuple] | None = None) -> ItemSyncService:
        warning_entries = warnings if warnings is not None else []
        return ItemSyncService(
            protocol_name=BRIDGE_PROTOCOL_NAME,
            protocol_version=BRIDGE_PROTOCOL_VERSION,
            load_optional_json=load_optional_json_file,
            validate_protocol=validate_protocol_payload,
            protocol_error=BridgeProtocolError,
            resolve_item_display_name=lambda name: {"Raw Potion": "Potion"}.get(name, name),
            log_warning=lambda *args: warning_entries.append(args),
        )

    def test_direct_service_builds_ordered_normalized_received_item_payload(self) -> None:
        warnings: list[tuple] = []
        service = self.build_service(warnings=warnings)
        source = ItemSyncInput(
            received_items=[
                SimpleNamespace(item=1, location=10, player=2, flags=1),
                SimpleNamespace(item=2, location=10, player=2, flags=2),
                SimpleNamespace(item=3, location=-1, player=2, flags=4),
            ],
            game="Kingdom Hearts III",
            player_names={2: "Riku"},
            location_id_to_bridge_key={10: "Treasure:001"},
            slot_data={
                "kh3_seed": {"seed_name": "lookup-test", "slot": 2},
                "bridge": {
                    "locations": [
                        {
                            "bridge_key": "Treasure:001",
                            "location_description": "Olympus - Chest 1",
                        }
                    ]
                }
            },
            bridge_save_dir=None,
            lookup_item_name=lambda item_id, game: {1: "Raw Potion", 2: "Cure", 3: "Fire"}[item_id],
            lookup_location_name=lambda location_id, game: f"Location {location_id}",
        )

        payload = service.build_items_payload(
            source,
        )

        self.assertEqual(payload["protocol_name"], BRIDGE_PROTOCOL_NAME)
        self.assertEqual(payload["protocol_version"], BRIDGE_PROTOCOL_VERSION)
        self.assertEqual([item["index"] for item in payload["items"]], [0, 2])
        self.assertEqual(payload["items"][0]["item_name"], "Potion")
        self.assertEqual(payload["items"][0]["kh3_raw_item_name"], "Raw Potion")
        self.assertNotIn("kh3_runtime_token", payload["items"][0])
        self.assertNotIn("kh3_grant_method", payload["items"][0])
        self.assertEqual(payload["items"][0]["location_name"], "Olympus - Chest 1")
        self.assertEqual(len(warnings), 1)

    def test_direct_service_normalizes_cache_and_refresh_callback(self) -> None:
        with TemporaryDirectory() as temp_dir:
            bridge_dir = Path(temp_dir)
            (bridge_dir / "ap_items.json").write_text(
                json.dumps({
                    "protocol_name": BRIDGE_PROTOCOL_NAME,
                    "protocol_version": BRIDGE_PROTOCOL_VERSION,
                    "items": "invalid",
                }),
                encoding="utf-8",
            )
            service = self.build_service()
            refreshes: list[bool] = []

            payload = service.load_cached_payload(bridge_dir)
            service.handle_received_items(
                bridge_dir,
                refresh_bridge_files=lambda: refreshes.append(True),
            )
            service.handle_received_items(None, refresh_bridge_files=lambda: refreshes.append(False))

        self.assertEqual(payload["items"], [])
        self.assertEqual(refreshes, [True])

    def test_refresh_preserves_cached_items_until_connected_snapshot_is_ready(self) -> None:
        with TemporaryDirectory() as temp_dir:
            bridge_dir = Path(temp_dir)
            cached_payload = {
                "protocol_name": BRIDGE_PROTOCOL_NAME,
                "protocol_version": BRIDGE_PROTOCOL_VERSION,
                "items": [{"index": 0, "item_name": "Potion"}],
            }
            (bridge_dir / "ap_items.json").write_text(
                json.dumps(cached_payload),
                encoding="utf-8",
            )
            service = self.build_service()
            empty_payload = service.empty_payload()

            handshake_payload = service.payload_for_refresh(
                empty_payload,
                bridge_dir=bridge_dir,
                ap_connected=True,
                received_items_snapshot_ready=False,
            )
            authoritative_empty_payload = service.payload_for_refresh(
                empty_payload,
                bridge_dir=bridge_dir,
                ap_connected=True,
                received_items_snapshot_ready=True,
            )
            disconnected_payload = service.payload_for_refresh(
                empty_payload,
                bridge_dir=bridge_dir,
                ap_connected=False,
                received_items_snapshot_ready=False,
            )
            populated_payload = {
                **empty_payload,
                "items": [{"index": 1, "item_name": "Ether"}],
            }
            populated_refresh_payload = service.payload_for_refresh(
                populated_payload,
                bridge_dir=bridge_dir,
                ap_connected=True,
                received_items_snapshot_ready=False,
            )

        self.assertEqual(handshake_payload, cached_payload)
        self.assertIs(authoritative_empty_payload, empty_payload)
        self.assertEqual(authoritative_empty_payload["items"], [])
        self.assertEqual(disconnected_payload, cached_payload)
        self.assertIs(populated_refresh_payload, populated_payload)


    def test_context_retains_item_sync_compatibility_delegates(self) -> None:
        service = self.build_service()
        service.load_cached_payload = Mock(return_value={"items": [{"index": 4}]})
        service.handle_received_items = Mock(return_value=None)
        ctx = KH3Context.__new__(KH3Context)
        ctx.item_sync_service = service
        ctx.bridge_dir = Path("synthetic-bridge")
        ctx.write_bridge_files = Mock()
        ctx.kh3_received_items_snapshot_ready = False
        ctx.integrated_bridge_activity_event = threading.Event()

        self.assertIs(KH3ClientModule.ItemSyncService, ItemSyncService)
        self.assertEqual(service.load_cached_payload(ctx.bridge_dir), {"items": [{"index": 4}]})
        self.assertEqual(
            ctx.build_slot_location_index(),
            {},
        )
        with patch.object(received_items_trace, "append_runtime_item_latency_trace_event") as append_trace:
            ctx.on_package("ReceivedItems", {"index": 0, "items": []})

        self.assertTrue(ctx.kh3_received_items_snapshot_ready)
        # Publication completion owns the wakeup; this callback is mocked.
        self.assertFalse(ctx.integrated_bridge_activity_event.is_set())
        service.load_cached_payload.assert_called_once_with(ctx.bridge_dir)
        service.handle_received_items.assert_called_once_with(
            ctx.bridge_dir,
            refresh_bridge_files=ctx.write_bridge_files,
        )
        self.assertEqual(
            [call.args[1] for call in append_trace.call_args_list],
            ["received_items_packet", "ap_items_snapshot_published"],
        )
        self.assertEqual(
            append_trace.call_args_list[0].kwargs["packet_trace_id"],
            append_trace.call_args_list[1].kwargs["packet_trace_id"],
        )

    def test_context_item_snapshot_authority_tracks_connection_lifecycle(self) -> None:
        service = self.build_service()
        service.handle_received_items = Mock()
        ctx = KH3Context.__new__(KH3Context)
        ctx.item_sync_service = service
        ctx.secret_report_hint_service = SecretReportHintService()
        ctx.bridge_dir = None
        ctx.write_bridge_files = Mock()
        ctx.kh3_received_items_snapshot_ready = True
        ctx.kh3_check_reporting_enabled = True
        ctx.locations_checked = {1}
        ctx.locations_scouted = {2}
        ctx.goal_status_sent = True
        ctx.last_deathlink_game_over_key = "old-game-over"
        ctx.received_deathlink_game_over_request_id = "old-request"

        ctx._reset_connected_kh3_state({"slot_data": empty_wire_slot_data()})

        self.assertFalse(ctx.kh3_received_items_snapshot_ready)
        ctx.on_package("ReceivedItems", {"index": 4, "items": []})
        self.assertFalse(ctx.kh3_received_items_snapshot_ready)
        ctx.on_package("ReceivedItems", {"index": 0, "items": []})
        self.assertTrue(ctx.kh3_received_items_snapshot_ready)


class TestKH3ItemConsumerPlan(unittest.TestCase):
    def test_client_delete_bridge_file_removes_processed_command_file(self) -> None:
        with TemporaryDirectory() as temp_dir:
            bridge_dir = Path(temp_dir)
            command_path = bridge_dir / "ap_client_command.json"
            command_path.write_text("{}", encoding="utf-8")

            ctx = KH3Context.__new__(KH3Context)
            ctx.bridge_dir = bridge_dir
            ctx._delete_bridge_file("ap_client_command.json")

            self.assertFalse(command_path.exists())

    def test_prepare_for_new_server_connection_clears_stale_room_seed(self) -> None:
        with TemporaryDirectory() as temp_dir:
            ctx = KH3Context.__new__(KH3Context)
            ctx.seed_name = "wrong_room_11111111"
            ctx.kh3_check_reporting_enabled = True
            ctx.locations_checked = {1}
            ctx.locations_scouted = {2}
            ctx.goal_status_sent = True
            ctx.last_processed_game_state_seq = 7
            ctx.save_seed_mismatch_active = True
            ctx.last_save_seed_status_warning = "old warning"
            ctx.kh3_mods_dir = Path(temp_dir)
            ctx.bridge_dir = None
            ctx.auth = "Sora"

            ctx.prepare_for_new_server_connection()

            self.assertIsNone(ctx.seed_name)
            self.assertFalse(ctx.kh3_check_reporting_enabled)
            self.assertEqual(ctx.locations_checked, set())
            self.assertEqual(ctx.locations_scouted, set())
            self.assertFalse(ctx.save_seed_mismatch_active)
            self.assertEqual(ctx.last_save_seed_status_warning, "")
            self.assertFalse(ctx.kh3_auth_inferred_from_installed_pak)

    def test_room_info_clears_restart_required_from_different_stale_seed(self) -> None:
        ctx = KH3Context.__new__(KH3Context)
        ctx.seed_name = None
        ctx.offline_seed_name = None
        ctx.kh3_restart_required_seed_id = "11111111"

        ctx.on_package("RoomInfo", {"seed_name": "correct_room_22222222"})

        self.assertEqual(ctx.seed_name, "correct_room_22222222")
        self.assertIsNone(ctx.kh3_restart_required_seed_id)

    def test_client_suppresses_location_checks_when_installed_pak_mismatches_seed(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bridge_dir = root / "bridge"
            mods_dir = root / "~mods"
            save_dir = root / "SaveGames"
            bridge_dir.mkdir()
            mods_dir.mkdir()
            save_dir.mkdir()
            (mods_dir / "pakchunk9-PlayerKH-randomizer-AP_98512966994186971735-2_99_P.pak").write_bytes(
                b"\0" * KH3_MINIMUM_SEED_PAK_SIZE_BYTES
            )
            (bridge_dir / "game_state.json").write_text(json.dumps({
                "protocol_name": BRIDGE_PROTOCOL_NAME,
                "protocol_version": BRIDGE_PROTOCOL_VERSION,
                "seq": 1,
                "checked_locations": ["TreasureFZ:FZ_LBOX_001"],
            }), encoding="utf-8")

            sent_messages: list[list[dict]] = []

            async def fake_send_msgs(messages: list[dict]) -> None:
                sent_messages.append(messages)

            ctx = KH3Context.__new__(KH3Context)
            ctx.bridge_dir = bridge_dir
            ctx.kh3_mods_dir = mods_dir
            ctx.bridge_save_dir = save_dir
            ctx.seed_name = "85269139745991038903"
            ctx.location_key_to_id = {"TreasureFZ:FZ_LBOX_001": 1263022969}
            ctx.last_processed_game_state_seq = -1
            ctx.kh3_check_reporting_enabled = True
            ctx.write_bridge_files = lambda: None
            ctx.send_msgs = fake_send_msgs

            with patch("worlds.kh3.Client.Utils.persistent_store"):
                asyncio.run(ctx.handle_game_state_file())

            self.assertEqual(sent_messages, [])
            self.assertEqual(ctx.last_processed_game_state_seq, 1)

    def test_client_sends_location_checks_when_installed_pak_matches_seed(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bridge_dir = root / "bridge"
            mods_dir = root / "~mods"
            save_dir = root / "SaveGames"
            bridge_dir.mkdir()
            mods_dir.mkdir()
            save_dir.mkdir()
            (mods_dir / "pakchunk9-PlayerKH-randomizer-AP_85269139745991038903-2_99_P.pak").write_bytes(
                b"\0" * KH3_MINIMUM_SEED_PAK_SIZE_BYTES
            )
            (bridge_dir / "game_state.json").write_text(json.dumps({
                "protocol_name": BRIDGE_PROTOCOL_NAME,
                "protocol_version": BRIDGE_PROTOCOL_VERSION,
                "seq": 1,
                "checked_locations": ["TreasureFZ:FZ_LBOX_001"],
            }), encoding="utf-8")

            sent_messages: list[list[dict]] = []

            async def fake_send_msgs(messages: list[dict]) -> None:
                sent_messages.append(messages)

            ctx = KH3Context.__new__(KH3Context)
            ctx.bridge_dir = bridge_dir
            ctx.kh3_mods_dir = mods_dir
            ctx.bridge_save_dir = save_dir
            ctx.seed_name = "85269139745991038903"
            ctx.location_key_to_id = {"TreasureFZ:FZ_LBOX_001": 1263022969}
            ctx.locations_checked = set()
            ctx.checked_locations = set()
            ctx.last_processed_game_state_seq = -1
            ctx.goal_status_sent = False
            ctx.kh3_check_reporting_enabled = True
            ctx.write_bridge_files = lambda: None
            ctx.send_msgs = fake_send_msgs
            ctx.refresh_save_seed_guard = lambda: True

            with patch("worlds.kh3.Client.Utils.persistent_store"):
                asyncio.run(ctx.handle_game_state_file())

            self.assertEqual(sent_messages, [[{"cmd": "LocationChecks", "locations": [1263022969]}]])
            self.assertEqual(ctx.locations_checked, {1263022969})

    def test_client_skips_unchanged_game_state_file_after_processing(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bridge_dir = root / "bridge"
            mods_dir = root / "~mods"
            save_dir = root / "SaveGames"
            bridge_dir.mkdir()
            mods_dir.mkdir()
            save_dir.mkdir()
            (mods_dir / "pakchunk9-PlayerKH-randomizer-AP_85269139745991038903-2_99_P.pak").write_bytes(
                b"\0" * KH3_MINIMUM_SEED_PAK_SIZE_BYTES
            )
            (bridge_dir / "game_state.json").write_text(json.dumps({
                "protocol_name": BRIDGE_PROTOCOL_NAME,
                "protocol_version": BRIDGE_PROTOCOL_VERSION,
                "seq": 1,
                "checked_locations": ["TreasureFZ:FZ_LBOX_001"],
            }), encoding="utf-8")

            sent_messages: list[list[dict]] = []
            write_count = 0

            async def fake_send_msgs(messages: list[dict]) -> None:
                sent_messages.append(messages)

            def fake_write_bridge_files() -> None:
                nonlocal write_count
                write_count += 1

            ctx = KH3Context.__new__(KH3Context)
            ctx.bridge_dir = bridge_dir
            ctx.kh3_mods_dir = mods_dir
            ctx.bridge_save_dir = save_dir
            ctx.seed_name = "85269139745991038903"
            ctx.location_key_to_id = {"TreasureFZ:FZ_LBOX_001": 1263022969}
            ctx.locations_checked = set()
            ctx.checked_locations = set()
            ctx.last_processed_game_state_seq = -1
            ctx.last_processed_game_state_file_signature = None
            ctx.kh3_check_reporting_enabled = True
            ctx.goal_status_sent = False
            ctx.write_bridge_files = fake_write_bridge_files
            ctx.send_msgs = fake_send_msgs
            ctx.refresh_save_seed_guard = lambda: True

            with patch("worlds.kh3.Client.Utils.persistent_store"):
                asyncio.run(ctx.handle_game_state_file())
                asyncio.run(ctx.handle_game_state_file())

            self.assertEqual(sent_messages, [[{"cmd": "LocationChecks", "locations": [1263022969]}]])
            self.assertEqual(write_count, 1)
            self.assertEqual(ctx.last_processed_game_state_seq, 1)

    def test_client_suppresses_location_checks_when_active_saves_have_no_seed_association(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bridge_dir = root / "bridge"
            mods_dir = root / "~mods"
            save_dir = root / "SaveGames"
            bridge_dir.mkdir()
            mods_dir.mkdir()
            save_dir.mkdir()
            (mods_dir / "pakchunk9-PlayerKH-randomizer-AP_85269139745991038903-2_99_P.pak").write_bytes(
                b"\0" * KH3_MINIMUM_SEED_PAK_SIZE_BYTES
            )
            (save_dir / "RandomizerEvents.sav").write_bytes(b"old-save")
            (bridge_dir / "game_state.json").write_text(json.dumps({
                "protocol_name": BRIDGE_PROTOCOL_NAME,
                "protocol_version": BRIDGE_PROTOCOL_VERSION,
                "seq": 1,
                "checked_locations": ["TreasureFZ:FZ_LBOX_001"],
            }), encoding="utf-8")

            sent_messages: list[list[dict]] = []

            async def fake_send_msgs(messages: list[dict]) -> None:
                sent_messages.append(messages)

            ctx = KH3Context.__new__(KH3Context)
            ctx.bridge_dir = bridge_dir
            ctx.kh3_mods_dir = mods_dir
            ctx.bridge_save_dir = save_dir
            ctx.seed_name = "AP_85269139745991038903"
            ctx.offline_seed_name = None
            ctx.location_key_to_id = {"TreasureFZ:FZ_LBOX_001": 1263022969}
            ctx.last_processed_game_state_seq = -1
            ctx.last_save_seed_status_warning = ""
            ctx.kh3_check_reporting_enabled = True
            ctx.write_bridge_files = lambda: None
            ctx.send_msgs = fake_send_msgs

            with patch("worlds.kh3.Client.Utils.persistent_load", return_value={}), \
                    patch("worlds.kh3.Client.Utils.persistent_store") as persistent_store:
                asyncio.run(ctx.handle_game_state_file())

            self.assertEqual(sent_messages, [])
            self.assertTrue(ctx.save_seed_mismatch_active)
            self.assertEqual(ctx.last_processed_game_state_seq, 1)
            persistent_store.assert_not_called()

    def test_client_suppresses_location_checks_when_active_saves_have_old_guard_association(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bridge_dir = root / "bridge"
            mods_dir = root / "~mods"
            save_dir = root / "SaveGames"
            bridge_dir.mkdir()
            mods_dir.mkdir()
            save_dir.mkdir()
            (mods_dir / "pakchunk9-PlayerKH-randomizer-AP_85269139745991038903-2_99_P.pak").write_bytes(
                b"\0" * KH3_MINIMUM_SEED_PAK_SIZE_BYTES
            )
            (save_dir / "RandomizerEvents.sav").write_bytes(b"old-save")
            (bridge_dir / "game_state.json").write_text(json.dumps({
                "protocol_name": BRIDGE_PROTOCOL_NAME,
                "protocol_version": BRIDGE_PROTOCOL_VERSION,
                "seq": 1,
                "checked_locations": ["TreasureFZ:FZ_LBOX_001"],
            }), encoding="utf-8")

            sent_messages: list[list[dict]] = []

            async def fake_send_msgs(messages: list[dict]) -> None:
                sent_messages.append(messages)

            ctx = KH3Context.__new__(KH3Context)
            ctx.bridge_dir = bridge_dir
            ctx.kh3_mods_dir = mods_dir
            ctx.bridge_save_dir = save_dir
            ctx.seed_name = "AP_85269139745991038903"
            ctx.offline_seed_name = None
            ctx.location_key_to_id = {"TreasureFZ:FZ_LBOX_001": 1263022969}
            ctx.last_processed_game_state_seq = -1
            ctx.last_save_seed_status_warning = ""
            ctx.kh3_check_reporting_enabled = True
            ctx.write_bridge_files = lambda: None
            ctx.send_msgs = fake_send_msgs
            save_key = KH3Context.save_seed_scope_key(save_dir)

            with patch("worlds.kh3.Client.Utils.persistent_load", return_value={
                "kh3_client": {
                    KH3_SAVE_SEED_ASSOCIATIONS_KEY: {
                        save_key: {
                            "seed_id": "11111111111111111111",
                            "guard_version": KH3_SAVE_SEED_GUARD_VERSION,
                        }
                    },
                    "last_save_seed_id": "85269139745991038903",
                    "last_save_seed_guard_version": KH3_SAVE_SEED_GUARD_VERSION,
                }
            }), patch("worlds.kh3.Client.Utils.persistent_store") as persistent_store:
                asyncio.run(ctx.handle_game_state_file())

            self.assertEqual(sent_messages, [])
            self.assertTrue(ctx.save_seed_mismatch_active)
            persistent_store.assert_not_called()

    def test_client_save_seed_guard_ignores_legacy_global_seed_when_scoped_association_matches(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            save_dir = root / "SaveGames"
            save_dir.mkdir()
            (save_dir / "RandomizerEvents.sav").write_bytes(b"current-save")

            ctx = KH3Context.__new__(KH3Context)
            ctx.bridge_save_dir = save_dir
            ctx.save_dir_override = True
            ctx.save_dir_dynamic_root_override = False
            ctx.seed_name = "AP_85269139745991038903"
            ctx.offline_seed_name = None
            ctx.kh3_restart_required_seed_id = None
            save_key = KH3Context.save_seed_scope_key(save_dir)

            with patch("worlds.kh3.Client.Utils.persistent_load", return_value={
                "kh3_client": {
                    KH3_SAVE_SEED_ASSOCIATIONS_KEY: {
                        save_key: {
                            "seed_id": "85269139745991038903",
                            "guard_version": KH3_SAVE_SEED_GUARD_VERSION,
                        }
                    },
                    "last_save_seed_id": "11111111111111111111",
                    "last_save_seed_guard_version": KH3_SAVE_SEED_GUARD_VERSION,
                }
            }):
                status = ctx.build_save_seed_status()

            self.assertEqual(status, "OK 85269139745991038903")

    def test_client_suppresses_location_checks_when_active_saves_match_old_offline_seed(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bridge_dir = root / "bridge"
            mods_dir = root / "~mods"
            save_dir = root / "SaveGames"
            bridge_dir.mkdir()
            mods_dir.mkdir()
            save_dir.mkdir()
            (mods_dir / "pakchunk9-PlayerKH-randomizer-AP_85269139745991038903-2_99_P.pak").write_bytes(
                b"\0" * KH3_MINIMUM_SEED_PAK_SIZE_BYTES
            )
            (save_dir / "RandomizerEvents.sav").write_bytes(b"old-save")
            (bridge_dir / "game_state.json").write_text(json.dumps({
                "protocol_name": BRIDGE_PROTOCOL_NAME,
                "protocol_version": BRIDGE_PROTOCOL_VERSION,
                "seq": 1,
                "checked_locations": ["TreasureFZ:FZ_LBOX_001"],
            }), encoding="utf-8")

            sent_messages: list[list[dict]] = []

            async def fake_send_msgs(messages: list[dict]) -> None:
                sent_messages.append(messages)

            ctx = KH3Context.__new__(KH3Context)
            ctx.bridge_dir = bridge_dir
            ctx.kh3_mods_dir = mods_dir
            ctx.bridge_save_dir = save_dir
            ctx.seed_name = "AP_85269139745991038903"
            ctx.offline_seed_name = "AP_98512966994186971735"
            ctx.location_key_to_id = {"TreasureFZ:FZ_LBOX_001": 1263022969}
            ctx.last_processed_game_state_seq = -1
            ctx.last_save_seed_status_warning = ""
            ctx.kh3_check_reporting_enabled = True
            ctx.write_bridge_files = lambda: None
            ctx.send_msgs = fake_send_msgs

            asyncio.run(ctx.handle_game_state_file())

            self.assertEqual(sent_messages, [])
            self.assertTrue(ctx.save_seed_mismatch_active)
            self.assertEqual(ctx.last_processed_game_state_seq, 1)

    def test_client_suppresses_offline_location_checks_when_active_saves_are_unverified(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bridge_dir = root / "bridge"
            mods_dir = root / "~mods"
            save_dir = root / "SaveGames"
            bridge_dir.mkdir()
            mods_dir.mkdir()
            save_dir.mkdir()
            (mods_dir / "pakchunk9-PlayerKH-randomizer-AP_81428333718587017233-2_99_P.pak").write_bytes(
                b"\0" * KH3_MINIMUM_SEED_PAK_SIZE_BYTES
            )
            (save_dir / "RandomizerEvents.sav").write_bytes(b"old-save")
            (bridge_dir / "game_state.json").write_text(json.dumps({
                "protocol_name": BRIDGE_PROTOCOL_NAME,
                "protocol_version": BRIDGE_PROTOCOL_VERSION,
                "seq": 1,
                "checked_locations": ["TreasureFZ:FZ_LBOX_001"],
            }), encoding="utf-8")

            sent_messages: list[list[dict]] = []

            async def fake_send_msgs(messages: list[dict]) -> None:
                sent_messages.append(messages)

            ctx = KH3Context.__new__(KH3Context)
            ctx.bridge_dir = bridge_dir
            ctx.kh3_mods_dir = mods_dir
            ctx.bridge_save_dir = save_dir
            ctx.seed_name = None
            ctx.offline_seed_name = "AP_81428333718587017233"
            ctx.location_key_to_id = {"TreasureFZ:FZ_LBOX_001": 1263022969}
            ctx.checked_locations = set()
            ctx.locations_checked = set()
            ctx.server = None
            ctx.server_address = None
            ctx.slot = None
            ctx.team = None
            ctx.player_names = {}
            ctx.auth = "PlayerKH"
            ctx.items_received = []
            ctx.finished_game = False
            ctx.last_processed_game_state_seq = -1
            ctx.last_save_seed_status_warning = ""
            ctx.kh3_check_reporting_enabled = True
            ctx.write_bridge_files = lambda: None
            ctx.send_msgs = fake_send_msgs

            with patch("worlds.kh3.Client.Utils.persistent_load", return_value={}), \
                    patch("worlds.kh3.Client.Utils.persistent_store") as persistent_store:
                asyncio.run(ctx.handle_game_state_file())

            self.assertEqual(sent_messages, [])
            self.assertTrue(ctx.save_seed_mismatch_active)
            self.assertEqual(ctx.last_processed_game_state_seq, 1)
            persistent_store.assert_not_called()

    def test_client_initializes_offline_bridge_from_installed_pak_metadata(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bridge_root = root / "bridge"
            mods_dir = root / "~mods"
            pak_path = mods_dir / "pakchunk9-PlayerKH-randomizer-AP_85269139745991038903-1_99_P.pak"
            mods_dir.mkdir()
            pak_path.write_bytes(b"\0" * KH3_MINIMUM_SEED_PAK_SIZE_BYTES)
            preexisting_bridge_dir = bridge_root / "85269139745991038903_PlayerKH"
            preexisting_bridge_dir.mkdir(parents=True)
            (preexisting_bridge_dir / "game_state.json").write_text(json.dumps({
                "protocol_name": BRIDGE_PROTOCOL_NAME,
                "protocol_version": BRIDGE_PROTOCOL_VERSION,
                "seq": 4,
                "checked_locations": ["TreasureFZ:FZ_LBOX_001"],
                "goal_complete": False,
            }), encoding="utf-8")
            (preexisting_bridge_dir / "ap_status.json").write_text(json.dumps({
                "protocol_name": BRIDGE_PROTOCOL_NAME,
                "protocol_version": BRIDGE_PROTOCOL_VERSION,
                "connected": False,
                "checked_locations": ["TreasureFZ:FZ_LBOX_001"],
                "checked_location_count": 1,
                "goal_complete": True,
            }), encoding="utf-8")
            (preexisting_bridge_dir / "runtime_loot_event.json").write_text(
                json.dumps({"bridge_key": "TreasureFZ:FZ_LBOX_001"}),
                encoding="utf-8",
            )
            slot_data = {
                "protocol_name": BRIDGE_PROTOCOL_NAME,
                "protocol_version": BRIDGE_PROTOCOL_VERSION,
                "bridge": {
                    "protocol_name": BRIDGE_PROTOCOL_NAME,
                    "protocol_version": BRIDGE_PROTOCOL_VERSION,
                    "locations": [
                        {
                            "ap_location_id": 1263022969,
                            "ap_location_name": "Arendelle - Large Chest 1",
                            "bridge_key": "TreasureFZ:FZ_LBOX_001",
                            "source_table": "TreasureFZ",
                            "source_id": "FZ_LBOX_001",
                            "world": "Arendelle",
                            "type": "treasure",
                            "assigned_item_name": "Charge Beam",
                            "assigned_item_player_name": "Aes",
                            "assigned_item_classification": 1,
                            "is_foreign_item": True,
                        }
                    ],
                },
            }
            metadata = {
                "format": "kh3_ap_seed_metadata",
                "format_version": 1,
                "seed": "AP_85269139745991038903-1-PlayerKH",
                "slot": 1,
                "slot_name": "PlayerKH",
                "slot_data": slot_data,
                "foreign_item_manifest": {"archive_name": "AP_85269139745991038903.zip"},
            }

            ctx = KH3Context.__new__(KH3Context)
            ctx.bridge_dir = None
            ctx.bridge_root_override = bridge_root
            ctx.kh3_mods_dir = mods_dir
            ctx.seed_name = None
            ctx.auth = None
            ctx.integrated_bridge_enabled = False
            ctx.server_address = None
            ctx.team = None
            ctx.kh3_dir = root / "KINGDOM HEARTS III"
            ctx.probe_config_path = ctx.kh3_dir / "Binaries" / "Win64" / "Mods" / "KH3APProbeCpp" / "config.lua"
            ctx.location_key_to_id = {}
            ctx.location_id_to_bridge_key = {}
            ctx.checked_locations = set()
            ctx.locations_checked = set()

            with patch("worlds.kh3.Client.load_installed_kh3_ap_seed_metadata", return_value=(metadata, pak_path)), patch.object(ctx, "refresh_save_seed_guard", return_value=True):
                self.assertTrue(ctx.try_initialize_offline_bridge_from_installed_pak())

            self.assertIsNotNone(ctx.bridge_dir)
            self.assertIsNone(ctx.seed_name)
            self.assertEqual(ctx.offline_seed_name, "85269139745991038903")
            self.assertEqual(ctx.location_key_to_id, {"TreasureFZ:FZ_LBOX_001": 1263022969})
            self.assertTrue((ctx.bridge_dir / "slot_data.json").is_file())
            self.assertTrue((ctx.bridge_dir / "ap_status.json").is_file())
            self.assertTrue((ctx.bridge_dir / "ap_items.json").is_file())
            reset_game_state = json.loads((ctx.bridge_dir / "game_state.json").read_text(encoding="utf-8"))
            self.assertEqual(reset_game_state["seq"], 0)
            self.assertEqual(reset_game_state["checked_locations"], [])
            self.assertEqual(reset_game_state["reset_reason"], "offline_bridge_initialized")
            self.assertFalse((ctx.bridge_dir / "runtime_loot_event.json").exists())
            written_status = json.loads((ctx.bridge_dir / "ap_status.json").read_text(encoding="utf-8"))
            self.assertFalse(written_status["connected"])
            self.assertEqual(written_status["seed_name"], "85269139745991038903")
            self.assertEqual(written_status["checked_locations"], [])
            self.assertEqual(written_status["checked_location_count"], 0)
            self.assertFalse(written_status["goal_complete"])
            self.assertEqual(written_status["total_location_count"], 1)

    def test_client_preserves_offline_bridge_from_matching_ap_status_without_cached_metadata(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bridge_root = root / "bridge"
            mods_dir = root / "~mods"
            pak_path = mods_dir / "pakchunk9-PlayerKH-randomizer-AP_85269139745991038903-1_99_P.pak"
            mods_dir.mkdir()
            pak_path.write_bytes(b"\0" * KH3_MINIMUM_SEED_PAK_SIZE_BYTES)
            preexisting_bridge_dir = bridge_root / "85269139745991038903_PlayerKH"
            preexisting_bridge_dir.mkdir(parents=True)
            (preexisting_bridge_dir / "game_state.json").write_text(json.dumps({
                "protocol_name": BRIDGE_PROTOCOL_NAME,
                "protocol_version": BRIDGE_PROTOCOL_VERSION,
                "seq": 4,
                "checked_locations": ["TreasureFZ:FZ_LBOX_001"],
                "goal_complete": False,
            }), encoding="utf-8")
            (preexisting_bridge_dir / "ap_status.json").write_text(json.dumps({
                "protocol_name": BRIDGE_PROTOCOL_NAME,
                "protocol_version": BRIDGE_PROTOCOL_VERSION,
                "connected": True,
                "seed_name": "85269139745991038903",
                "slot": 1,
                "player_name": "PlayerKH",
                "checked_locations": [1263022969],
                "checked_location_count": 1,
                "goal_complete": False,
            }), encoding="utf-8")
            (preexisting_bridge_dir / "runtime_loot_event.json").write_text(
                json.dumps({"bridge_key": "TreasureFZ:FZ_LBOX_001"}),
                encoding="utf-8",
            )
            slot_data = {
                "protocol_name": BRIDGE_PROTOCOL_NAME,
                "protocol_version": BRIDGE_PROTOCOL_VERSION,
                "bridge": {
                    "protocol_name": BRIDGE_PROTOCOL_NAME,
                    "protocol_version": BRIDGE_PROTOCOL_VERSION,
                    "locations": [
                        {
                            "ap_location_id": 1263022969,
                            "ap_location_name": "Arendelle - Large Chest 1",
                            "bridge_key": "TreasureFZ:FZ_LBOX_001",
                            "source_table": "TreasureFZ",
                            "source_id": "FZ_LBOX_001",
                            "world": "Arendelle",
                            "type": "treasure",
                        }
                    ],
                },
            }
            metadata = {
                "format": "kh3_ap_seed_metadata",
                "format_version": 1,
                "seed": "AP_85269139745991038903-1-PlayerKH",
                "slot": 1,
                "slot_name": "PlayerKH",
                "slot_data": slot_data,
                "foreign_item_manifest": {"archive_name": "AP_85269139745991038903.zip"},
            }

            ctx = KH3Context.__new__(KH3Context)
            ctx.bridge_dir = None
            ctx.bridge_root_override = bridge_root
            ctx.kh3_mods_dir = mods_dir
            ctx.seed_name = None
            ctx.auth = None
            ctx.integrated_bridge_enabled = False
            ctx.server_address = None
            ctx.team = None
            ctx.kh3_dir = root / "KINGDOM HEARTS III"
            ctx.probe_config_path = ctx.kh3_dir / "Binaries" / "Win64" / "Mods" / "KH3APProbeCpp" / "config.lua"
            ctx.location_key_to_id = {}
            ctx.location_id_to_bridge_key = {}

            with patch("worlds.kh3.Client.load_installed_kh3_ap_seed_metadata", return_value=(metadata, pak_path)), patch.object(ctx, "refresh_save_seed_guard", return_value=True):
                self.assertTrue(ctx.try_initialize_offline_bridge_from_installed_pak())

            preserved_game_state = json.loads((ctx.bridge_dir / "game_state.json").read_text(encoding="utf-8"))
            self.assertEqual(preserved_game_state["seq"], 4)
            self.assertEqual(preserved_game_state["checked_locations"], ["TreasureFZ:FZ_LBOX_001"])
            self.assertNotIn("reset_reason", preserved_game_state)
            self.assertTrue((ctx.bridge_dir / "runtime_loot_event.json").exists())
            written_status = json.loads((ctx.bridge_dir / "ap_status.json").read_text(encoding="utf-8"))
            self.assertEqual(written_status["checked_locations"], [1263022969])
            self.assertEqual(written_status["checked_location_count"], 1)

    def test_client_leaves_previous_runtime_context_behind_when_connect_bridge_dir_changes(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            old_bridge = root / "bridge" / "AP_85269139745991038903_PlayerKH"
            old_bridge.mkdir(parents=True)
            (old_bridge / "runtime_context.json").write_text('{"hook":"BP_TresGetItem"}', encoding="utf-8")
            (old_bridge / "runtime_loot_event.json").write_text('{"bridge_key":"TreasureFZ:FZ_LBOX_001"}', encoding="utf-8")
            (old_bridge / "runtime_loot_state.json").write_text("{}", encoding="utf-8")
            slot_data = {
                "protocol_name": BRIDGE_PROTOCOL_NAME,
                "protocol_version": BRIDGE_PROTOCOL_VERSION,
                "bridge": {
                    "protocol_name": BRIDGE_PROTOCOL_NAME,
                    "protocol_version": BRIDGE_PROTOCOL_VERSION,
                    "locations": [
                        {
                            "ap_location_id": 1263022969,
                            "ap_location_name": "Arendelle - Large Chest 1",
                            "bridge_key": "TreasureFZ:FZ_LBOX_001",
                            "source_table": "TreasureFZ",
                            "source_id": "FZ_LBOX_001",
                            "world": "Arendelle",
                            "type": "treasure",
                        }
                    ],
                },
            }

            ctx = KH3Context.__new__(KH3Context)
            ctx.bridge_dir = old_bridge
            ctx.bridge_root_override = root / "bridge"
            ctx.slot_data = slot_data
            ctx.seed_name = "85269139745991038903"
            ctx.auth = "PlayerKH"
            ctx.player_names = {}
            ctx.slot = 2
            ctx.checked_locations = set()
            ctx.locations_checked = set()
            ctx.finished_game = False

            ctx.initialize_bridge()

            self.assertEqual(ctx.bridge_dir, root / "bridge" / "85269139745991038903_PlayerKH")
            self.assertFalse((ctx.bridge_dir / "runtime_context.json").exists())
            self.assertEqual(
                json.loads((old_bridge / "runtime_context.json").read_text(encoding="utf-8")),
                {"hook": "BP_TresGetItem"},
            )
            self.assertFalse((ctx.bridge_dir / "runtime_loot_event.json").exists())
            self.assertFalse((ctx.bridge_dir / "runtime_loot_state.json").exists())

    def test_client_auto_disconnects_on_connected_pak_seed_mismatch_before_bridge_start(self) -> None:
        async def run_case() -> tuple[list[bool], list[bool]]:
            with TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                bridge_root = root / "bridge"
                mods_dir = root / "~mods"
                save_dir = root / "SaveGames"
                mods_dir.mkdir()
                save_dir.mkdir()
                (mods_dir / "pakchunk9-PlayerKH-randomizer-AP_98512966994186971735-1_99_P.pak").write_bytes(
                    b"\0" * KH3_MINIMUM_SEED_PAK_SIZE_BYTES
                )
                slot_data = {
                    "protocol_name": BRIDGE_PROTOCOL_NAME,
                    "protocol_version": BRIDGE_PROTOCOL_VERSION,
                    "bridge": {
                        "protocol_name": BRIDGE_PROTOCOL_NAME,
                        "protocol_version": BRIDGE_PROTOCOL_VERSION,
                        "locations": [
                            {
                                "ap_location_id": 1263022969,
                                "ap_location_name": "Arendelle - Large Chest 1",
                                "bridge_key": "TreasureFZ:FZ_LBOX_001",
                                "source_table": "TreasureFZ",
                                "source_id": "FZ_LBOX_001",
                                "world": "Arendelle",
                                "type": "treasure",
                            }
                        ],
                    },
                }

                disconnected: list[bool] = []
                started_bridge: list[bool] = []

                async def fake_disconnect() -> None:
                    disconnected.append(True)

                ctx = KH3Context.__new__(KH3Context)
                ctx.bridge_dir = None
                ctx.bridge_root_override = bridge_root
                ctx.slot_data = {}
                ctx.seed_name = "AP_85269139745991038903"
                ctx.auth = "PlayerKH"
                ctx.player_names = {}
                ctx.slot = 1
                ctx.team = 0
                ctx.kh3_mods_dir = mods_dir
                ctx.bridge_save_dir = save_dir
                ctx.kh3_dir = root / "KINGDOM HEARTS III"
                ctx.probe_config_path = ctx.kh3_dir / "Binaries" / "Win64" / "Mods" / "KH3APProbeCpp" / "config.lua"
                ctx.server = None
                ctx.server_address = "localhost:50002"
                ctx.checked_locations = set()
                ctx.locations_checked = set()
                ctx.locations_scouted = set()
                ctx.missing_locations = {1263022969}
                ctx.items_received = []
                ctx.finished_game = False
                ctx.last_processed_game_state_seq = -1
                ctx.last_processed_bridge_notification_seq = 0
                ctx.last_processed_client_command_seq = 0
                ctx.last_pak_status_warning = ""
                ctx.last_save_seed_status_warning = ""
                ctx.save_seed_mismatch_active = False
                ctx.goal_status_sent = False
                ctx.offline_seed_name = None
                ctx.integrated_bridge_stop_event = None
                ctx.disconnect = fake_disconnect
                ctx.start_integrated_bridge = lambda: started_bridge.append(True)

                ctx.on_package("Connected", {"slot_data": encode_slot_data(slot_data)})
                await asyncio.sleep(0)
                await ctx.flush_bridge_publications()
                return disconnected, started_bridge

        disconnected, started_bridge = asyncio.run(run_case())

        self.assertEqual(disconnected, [True])
        self.assertEqual(started_bridge, [])

    def test_client_connect_prompts_for_slot_and_starts_server_seed_bootstrap_without_pak(self) -> None:
        async def run_case() -> tuple[list[bool], list[str], list[str | None], bool]:
            with TemporaryDirectory() as temp_dir:
                mods_dir = Path(temp_dir) / "~mods"
                mods_dir.mkdir()
                (mods_dir / "pakchunk99-GardenOfAssemblageRandomizer_99_P.pak").write_bytes(b"goa")

                disconnected: list[bool] = []
                prompted: list[str] = []
                started: list[str | None] = []

                async def fake_disconnect() -> None:
                    disconnected.append(True)

                async def fake_prepare(*, trigger: str):
                    self.assertEqual(trigger, "connecting")
                    return SimpleNamespace(status="no_source")

                async def fake_prompt() -> bool:
                    prompted.append("slot")
                    ctx.auth = "SaveMgrTest"
                    return True

                ctx = KH3Context.__new__(KH3Context)
                ctx.kh3_mods_dir = mods_dir
                ctx.last_pak_status_warning = ""
                ctx.disconnect = fake_disconnect
                ctx.auth = None
                ctx.username = None
                ctx.locations_checked = set()
                ctx.locations_scouted = set()
                ctx._prepare_missing_seed_pak = fake_prepare
                ctx.has_installed_kh3_ap_seed_pak = lambda: False
                ctx._get_seed_import_service = lambda: SimpleNamespace(process_running=lambda: False)
                ctx._prompt_kh3_seed_bootstrap_slot_name = fake_prompt
                ctx.prepare_for_new_server_connection = lambda: None
                ctx._get_lifecycle_service = lambda: SimpleNamespace(
                    start_server=lambda address, task_name: started.append(address)
                )

                await ctx.connect("localhost:50002")
                return disconnected, prompted, started, ctx.kh3_seed_bootstrap_pending

        disconnected, prompted, started, pending = asyncio.run(run_case())

        self.assertEqual(disconnected, [True])
        self.assertEqual(prompted, ["slot"])
        self.assertEqual(started, ["localhost:50002"])
        self.assertTrue(pending)

    def test_room_info_preserves_mismatched_installed_run_before_authentication(self) -> None:
        async def run_case() -> KH3Context:
            ctx = KH3Context.__new__(KH3Context)
            ctx.seed_name = None
            ctx.kh3_restart_required_seed_id = None
            ctx.kh3_seed_bootstrap_pending = False
            policy = Mock()
            policy.observe_room_seed.return_value = SimpleNamespace(
                seed_name="AP_22222222222222222222",
                restart_required_seed=None,
            )
            ctx._get_seed_policy_service = Mock(return_value=policy)
            ctx.installed_seed_pak_mismatches_room = Mock(return_value=True)
            ctx._suspend_installed_kh3_run_for_server = AsyncMock(return_value=True)

            await ctx._prepare_kh3_room_authentication({"seed_name": "AP_22222222222222222222"})
            return ctx

        ctx = asyncio.run(run_case())

        ctx.installed_seed_pak_mismatches_room.assert_called_once_with("AP_22222222222222222222")
        ctx._suspend_installed_kh3_run_for_server.assert_awaited_once_with(
            reason="it does not match the selected room"
        )
        self.assertEqual(ctx.seed_name, "AP_22222222222222222222")
        self.assertTrue(ctx.kh3_seed_bootstrap_pending)

    def test_room_info_preserves_same_seed_when_server_hostname_changes(self) -> None:
        async def run_case() -> KH3Context:
            ctx = KH3Context.__new__(KH3Context)
            ctx.seed_name = None
            ctx.kh3_restart_required_seed_id = None
            ctx.kh3_seed_bootstrap_pending = False
            policy = Mock()
            policy.observe_room_seed.return_value = SimpleNamespace(
                seed_name="AP_78815585780120999120",
                restart_required_seed=None,
            )
            ctx._get_seed_policy_service = Mock(return_value=policy)
            ctx.installed_seed_pak_mismatches_room = Mock(return_value=False)
            ctx.installed_run_mismatches_connection_affinity = Mock(return_value=True)
            ctx._suspend_installed_kh3_run_for_server = AsyncMock(return_value=True)

            await ctx._prepare_kh3_room_authentication({"seed_name": "AP_78815585780120999120"})
            return ctx

        ctx = asyncio.run(run_case())

        ctx._suspend_installed_kh3_run_for_server.assert_awaited_once_with(
            reason="the selected server hostname uses a separate save profile"
        )
        self.assertTrue(ctx.kh3_seed_bootstrap_pending)

    def test_connection_affinity_check_ignores_port_but_detects_another_host(self) -> None:
        ctx = KH3Context.__new__(KH3Context)
        ctx.auth = "SaveMgrTest"
        profile = KH3RunConnectionProfile(
            run_id="78815585780120999120-1-SaveMgrTest",
            slot_name="SaveMgrTest",
            connection_slot_name="SaveMgrTest",
            server_address="wss://archipelago.gg:45678",
        )
        service = Mock()
        service.active_connection_profile.return_value = profile
        ctx._get_run_manager_service = Mock(return_value=service)

        ctx.server_address = "archipelago.gg:50005"
        self.assertFalse(ctx.installed_run_mismatches_connection_affinity())

        ctx.server_address = "127.0.0.1:38283"
        self.assertTrue(ctx.installed_run_mismatches_connection_affinity())

    def test_invalid_slot_from_inferred_pak_name_schedules_one_recovery(self) -> None:
        ctx = KH3Context.__new__(KH3Context)
        ctx.kh3_auth_inferred_from_installed_pak = True
        ctx.kh3_invalid_slot_recovery_pending = False
        ctx.has_installed_kh3_ap_seed_pak = Mock(return_value=True)

        with patch("worlds.kh3.Client.Utils.async_start") as async_start:
            ctx.event_invalid_slot()

        self.assertTrue(ctx.kh3_invalid_slot_recovery_pending)
        async_start.assert_called_once()
        self.assertEqual(async_start.call_args.kwargs["name"], "KH3InvalidInferredSlotRecovery")
        async_start.call_args.args[0].close()

    def test_client_connect_bails_before_server_loop_with_multiple_installed_seed_paks(self) -> None:
        async def run_case() -> tuple[list[bool], bool, str]:
            with TemporaryDirectory() as temp_dir:
                mods_dir = Path(temp_dir) / "~mods"
                mods_dir.mkdir()
                first_pak = mods_dir / "pakchunk9-Sora-randomizer-AP_11111111111111111111-1_99_P.pak"
                second_pak = mods_dir / "pakchunk9-Riku-randomizer-AP_22222222222222222222-2_99_P.pak"
                first_pak.write_bytes(b"first")
                second_pak.write_bytes(b"second")

                disconnected: list[bool] = []

                async def fake_disconnect() -> None:
                    disconnected.append(True)

                ctx = KH3Context.__new__(KH3Context)
                ctx.kh3_mods_dir = mods_dir
                ctx.last_pak_status_warning = ""
                ctx.disconnect = fake_disconnect

                with patch("worlds.kh3.Client.server_loop") as mocked_server_loop:
                    await ctx.connect("localhost:50002")
                    return disconnected, mocked_server_loop.called, ctx.last_pak_status_warning

        disconnected, server_loop_called, warning = asyncio.run(run_case())

        self.assertEqual(disconnected, [True])
        self.assertFalse(server_loop_called)
        self.assertIn("Multiple KH3 AP seed paks were found", warning)
        self.assertIn("pakchunk9-Sora-randomizer", warning)
        self.assertIn("pakchunk9-Riku-randomizer", warning)

    def test_client_connect_bails_before_server_loop_with_undersized_seed_pak(self) -> None:
        async def run_case() -> tuple[list[bool], bool, str, list[dict]]:
            with TemporaryDirectory() as temp_dir:
                mods_dir = Path(temp_dir) / "~mods"
                mods_dir.mkdir()
                pak_path = mods_dir / "pakchunk9-Sora-randomizer-AP_11111111111111111111-1_99_P.pak"
                pak_path.write_bytes(b"incomplete")

                disconnected: list[bool] = []

                async def fake_disconnect() -> None:
                    disconnected.append(True)

                ctx = KH3Context.__new__(KH3Context)
                ctx.kh3_mods_dir = mods_dir
                ctx.last_pak_status_warning = ""
                ctx.disconnect = fake_disconnect
                ctx.ui = object()
                yellow_messages: list[dict] = []
                ctx.on_print_json = yellow_messages.append

                with patch("worlds.kh3.Client.server_loop") as mocked_server_loop:
                    await ctx.connect("localhost:50002")
                    return (
                        disconnected,
                        mocked_server_loop.called,
                        ctx.last_pak_status_warning,
                        yellow_messages,
                    )

        disconnected, server_loop_called, warning, yellow_messages = asyncio.run(run_case())

        self.assertEqual(disconnected, [True])
        self.assertFalse(server_loop_called)
        self.assertEqual(
            warning,
            "The seed file had an error in generating. Please regenerate the seed file. "
            "The approximate size should be around 860kb.",
        )
        self.assertEqual(yellow_messages[0]["data"][0]["color"], "yellow")
        self.assertEqual(yellow_messages[0]["data"][0]["text"], warning)

    def test_client_connect_command_uses_guarded_connect_path(self) -> None:
        with TemporaryDirectory() as temp_dir:
            mods_dir = Path(temp_dir) / "~mods"
            mods_dir.mkdir()

            ctx = KH3Context.__new__(KH3Context)
            ctx.kh3_mods_dir = mods_dir
            ctx.server_address = "localhost:50002"
            ctx.username = "old-user"
            ctx.password = "old-password"

            command = KH3CommandProcessor(ctx)
            output: list[str] = []
            command.output = output.append

            with patch("worlds.kh3.Client.Utils.async_start") as async_start:
                self.assertTrue(command._cmd_connect())

            async_start.assert_called_once()
            self.assertIn("KH3 AP seed pak is missing", output[0])
            coroutine = async_start.call_args.args[0]
            coroutine.close()

    def test_client_launch_command_uses_launch_button_action(self) -> None:
        ctx = KH3Context.__new__(KH3Context)
        ctx.launch_kh3_game = Mock()

        command = KH3CommandProcessor(ctx)
        output: list[str] = []
        command.output = output.append

        command("/launch")

        ctx.launch_kh3_game.assert_called_once_with()
        self.assertEqual(output, ["KH3 launch requested."])

    def test_client_connect_command_reports_multiple_installed_seed_paks(self) -> None:
        with TemporaryDirectory() as temp_dir:
            mods_dir = Path(temp_dir) / "~mods"
            mods_dir.mkdir()
            (mods_dir / "pakchunk9-Sora-randomizer-AP_11111111111111111111-1_99_P.pak").write_bytes(b"first")
            (mods_dir / "pakchunk9-Riku-randomizer-AP_22222222222222222222-2_99_P.pak").write_bytes(b"second")

            ctx = KH3Context.__new__(KH3Context)
            ctx.kh3_mods_dir = mods_dir
            ctx.server_address = "localhost:50002"
            ctx.username = None
            ctx.password = None

            command = KH3CommandProcessor(ctx)
            output: list[str] = []
            command.output = output.append

            with patch("worlds.kh3.Client.Utils.async_start") as async_start:
                self.assertTrue(command._cmd_connect())

            async_start.assert_called_once()
            self.assertEqual(len(output), 1)
            self.assertIn("Multiple KH3 AP seed paks were found", output[0])
            coroutine = async_start.call_args.args[0]
            coroutine.close()

    def test_client_deathlink_command_toggles_live_connection_tag(self) -> None:
        ctx = KH3Context.__new__(KH3Context)
        ctx.tags = {"AP"}
        ctx.server = SimpleNamespace(socket=SimpleNamespace(closed=False))
        sent_messages: list[dict] = []

        async def send_msgs(messages: list[dict]) -> None:
            sent_messages.extend({**message, "tags": set(message["tags"])} for message in messages)

        ctx.send_msgs = send_msgs
        command = KH3CommandProcessor(ctx)
        output: list[str] = []
        command.output = output.append
        scheduled_names: list[str] = []

        def run_update(coroutine, *, name: str) -> None:
            scheduled_names.append(name)
            asyncio.run(coroutine)

        with patch("worlds.kh3.Client.Utils.async_start", side_effect=run_update):
            self.assertTrue(command("/deathlink"))
            self.assertIn("DeathLink", ctx.tags)
            self.assertTrue(command("/deathlink"))
            self.assertNotIn("DeathLink", ctx.tags)

        self.assertEqual(output, ["DeathLink enabled.", "DeathLink disabled."])
        self.assertEqual(scheduled_names, ["KH3DeathLinkUpdate", "KH3DeathLinkUpdate"])
        self.assertEqual(sent_messages, [
            {"cmd": "ConnectUpdate", "tags": {"AP", "DeathLink"}},
            {"cmd": "ConnectUpdate", "tags": {"AP"}},
        ])

    def test_client_server_auth_disconnects_without_installed_seed_pak(self) -> None:
        async def run_case() -> tuple[list[bool], list[bool], list[bool]]:
            with TemporaryDirectory() as temp_dir:
                mods_dir = Path(temp_dir) / "~mods"
                mods_dir.mkdir()

                disconnected: list[bool] = []
                prompted: list[bool] = []
                sent_connect: list[bool] = []

                async def fake_disconnect() -> None:
                    disconnected.append(True)

                async def fake_get_username() -> None:
                    prompted.append(True)

                async def fake_send_connect(*, game: str) -> None:
                    sent_connect.append(True)

                ctx = KH3Context.__new__(KH3Context)
                ctx.kh3_mods_dir = mods_dir
                ctx.last_pak_status_warning = ""
                ctx.disconnect = fake_disconnect
                ctx.get_username = fake_get_username
                ctx.send_connect = fake_send_connect

                await ctx.server_auth()
                return disconnected, prompted, sent_connect

        disconnected, prompted, sent_connect = asyncio.run(run_case())

        self.assertEqual(disconnected, [True])
        self.assertEqual(prompted, [])
        self.assertEqual(sent_connect, [])

    def test_client_server_auth_allows_pending_server_seed_bootstrap(self) -> None:
        async def run_case() -> tuple[list[bool], list[str]]:
            prompted: list[bool] = []
            sent_connect: list[str] = []

            async def fake_prompt() -> bool:
                prompted.append(True)
                return True

            async def fake_get_username() -> None:
                if not ctx.auth:
                    prompted.append(True)

            async def fake_send_connect(*, game: str) -> None:
                sent_connect.append(game)

            ctx = KH3Context.__new__(KH3Context)
            ctx.kh3_seed_bootstrap_pending = True
            ctx.auth = "SaveMgrTest"
            ctx.password = None
            ctx._prompt_kh3_seed_bootstrap_slot_name = fake_prompt
            ctx.get_username = fake_get_username
            ctx.send_connect = fake_send_connect

            await ctx.server_auth()
            return prompted, sent_connect

        prompted, sent_connect = asyncio.run(run_case())

        self.assertEqual(prompted, [])
        self.assertEqual(sent_connect, ["Kingdom Hearts III"])

    def test_connected_seed_bootstrap_prefers_hosted_player_artifact(self) -> None:
        ctx = KH3Context.__new__(KH3Context)
        ctx.slot_data = {
            "kh3_room": {
                "format": "kh3_platform_room",
                "format_version": 1,
                "uuid": "room-id",
                "url": "https://ap.aesais.net/room/room-id",
            },
            "kh3_seed": {"format": "kh3_server_seed"},
        }
        ctx.server_address = "ap.aesais.net:38281"
        ctx.slot = 4
        ctx.auth = "Sora"
        ctx.seed_name = "AP_11111111111111111111"
        ctx._set_kh3_run_action_status = Mock()
        downloader = Mock()
        downloader.has_platform_room.return_value = True
        downloader.download_player_artifact.return_value = SimpleNamespace(
            source_path=Path("Sora.pak"),
        )
        importer = Mock()
        prepared = SimpleNamespace(status="installed")
        importer.prepare_player_artifact.return_value = prepared
        ctx._get_platform_seed_download_service = Mock(return_value=downloader)
        ctx._get_seed_import_service = Mock(return_value=importer)

        result = ctx._prepare_connected_kh3_seed()

        self.assertIs(result, prepared)
        downloader.download_player_artifact.assert_called_once_with(
            ctx.slot_data,
            slot_name="Sora",
            fallback_room_url="https://ap.aesais.net/room/room-id",
            progress=ctx._set_kh3_run_action_status,
        )
        importer.prepare_player_artifact.assert_called_once_with(
            Path("Sora.pak"),
            server_address="ap.aesais.net:38281",
            expected_slot=4,
            expected_slot_name="Sora",
            expected_seed_name="AP_11111111111111111111",
            progress=ctx._set_kh3_run_action_status,
        )
        importer.prepare_server_slot_data.assert_not_called()

    def test_connected_seed_bootstrap_logs_inline_generation_for_local_room(self) -> None:
        ctx = KH3Context.__new__(KH3Context)
        ctx.slot_data = {"kh3_seed": {"format": "kh3_server_seed"}}
        ctx.server_address = "127.0.0.1:38281"
        ctx.slot = 4
        ctx.auth = "Sora"
        ctx.seed_name = "AP_11111111111111111111"
        ctx._set_kh3_run_action_status = Mock()
        downloader = Mock()
        downloader.has_platform_room.return_value = False
        importer = Mock()
        prepared = SimpleNamespace(status="installed")
        importer.prepare_server_slot_data.return_value = prepared
        ctx._get_platform_seed_download_service = Mock(return_value=downloader)
        ctx._get_seed_import_service = Mock(return_value=importer)

        with self.assertLogs("Client", level="INFO") as captured_logs:
            result = ctx._prepare_connected_kh3_seed()

        self.assertIs(result, prepared)
        downloader.download_player_artifact.assert_not_called()
        importer.prepare_player_artifact.assert_not_called()
        importer.prepare_server_slot_data.assert_called_once()
        logs = "\n".join(captured_logs.output)
        self.assertIn("no hosted player artifact was advertised", logs)
        self.assertIn("inline seed payload", logs)
        self.assertIn("127.0.0.1:38281", logs)

    def test_connected_seed_bootstrap_builds_coliseum_companion_after_seed_pak(self) -> None:
        async def run_case() -> list[str]:
            events: list[str] = []
            save_dir = Path("prepared-save-profile")
            run = SimpleNamespace(
                run_id="run-id",
                connection_slot_name="b",
                seed_id="11111111111111111111",
                slot_name="b",
                player=2,
            )
            prepared = KH3SeedPreparationResult(
                status="installed",
                installed_run=run,
                activated_save_dir=save_dir,
            )
            ctx = KH3Context.__new__(KH3Context)
            ctx.server = object()
            ctx.slot = 2
            ctx.auth = "b"
            ctx.kh3_seed_bootstrap_pending = True
            ctx.save_seed_mismatch_active = True
            ctx.last_save_seed_status_warning = "old mismatch"
            ctx.kh3_restart_required_seed_id = "11111111111111111111"
            ctx._begin_kh3_run_action = lambda _label: events.append("begin") or True
            ctx._prepare_connected_kh3_seed = lambda: events.append("seed") or prepared
            ctx._ensure_olympus_coliseum_reward_companion = lambda: events.append("compat")
            ctx._connection_slot_name_for_run = lambda _run: "b"
            ctx.store_save_seed_associations_for_targets = Mock(
                side_effect=lambda *_args, **_kwargs: events.append("associate")
            )
            ctx._finish_kh3_run_action = lambda: events.append("finish_action")
            ctx._finish_connected_kh3_setup = lambda _args: events.append("finish_setup")
            ctx._show_kh3_seed_bootstrap_warning = Mock()
            ctx.disconnect = AsyncMock()

            await ctx._bootstrap_connected_kh3_seed_action({"slot_data": empty_wire_slot_data()})
            ctx.store_save_seed_associations_for_targets.assert_called_once_with(
                [save_dir],
                run.seed_id,
                run_id=run.run_id,
            )
            self.assertFalse(ctx.save_seed_mismatch_active)
            self.assertEqual(ctx.last_save_seed_status_warning, "")
            self.assertIsNone(ctx.kh3_restart_required_seed_id)
            return events

        events = asyncio.run(run_case())

        self.assertEqual(events, ["begin", "seed", "compat", "associate", "finish_action", "finish_setup"])

    def test_prepared_seed_ownership_accepts_fresh_saves_created_after_bootstrap(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            save_dir = root / "SaveGames"
            save_dir.mkdir()
            new_seed = "38733435369538154540"
            old_seed = "28160837366242855567"
            run = SimpleNamespace(
                run_id=f"{new_seed}-1-d--localprofile",
                seed_id=new_seed,
                slot_name="d",
            )
            prepared = KH3SeedPreparationResult(
                status="installed",
                installed_run=run,
                activated_save_dir=save_dir,
            )
            save_key = KH3Context.save_seed_scope_key(save_dir)
            persistent = {
                "kh3_client": {
                    KH3_SAVE_SEED_ASSOCIATIONS_KEY: {
                        save_key: {
                            "seed_id": old_seed,
                            "guard_version": KH3_SAVE_SEED_GUARD_VERSION,
                        }
                    }
                }
            }

            def persistent_store(section: str, key: str, value: object) -> None:
                persistent.setdefault(section, {})[key] = value

            ctx = KH3Context.__new__(KH3Context)
            ctx.bridge_save_dir = save_dir
            ctx.kh3_dir = root
            ctx.kh3_mods_dir = root / "mods"
            ctx.save_dir_override = True
            ctx.save_dir_dynamic_root_override = False
            ctx.seed_name = f"AP_{new_seed}"
            ctx.offline_seed_name = f"AP_{new_seed}"
            ctx.kh3_restart_required_seed_id = None
            ctx.kh3_active_connection_profile_run_id = run.run_id
            ctx.auto_remove_saves_on_new_seed = True
            ctx.ignore_unverified_save_seed_warnings = False
            ctx.save_seed_mismatch_active = True
            ctx.last_save_seed_status_warning = "old mismatch"

            with patch("worlds.kh3.Client.Utils.persistent_load", side_effect=lambda: persistent), patch(
                "worlds.kh3.Client.Utils.persistent_store",
                side_effect=persistent_store,
            ), patch("worlds.kh3.Client.is_process_running", return_value=False):
                ctx._commit_prepared_seed_save_ownership(prepared)
                fresh_save = save_dir / "RandomizerEvents.sav"
                fresh_save.write_bytes(b"fresh-new-seed-save")

                self.assertTrue(ctx.refresh_save_seed_guard())

            self.assertTrue(fresh_save.is_file())
            self.assertEqual(
                persistent["kh3_client"][KH3_SAVE_SEED_ASSOCIATIONS_KEY][save_key],
                {
                    "seed_id": new_seed,
                    "guard_version": KH3_SAVE_SEED_GUARD_VERSION,
                    "run_id": run.run_id,
                },
            )
            self.assertFalse(ctx.save_seed_mismatch_active)

    def test_room_page_url_recovers_docker_internal_platform_metadata(self) -> None:
        ctx = KH3Context.__new__(KH3Context)
        ctx.slot_data = {
            "kh3_room": {
                "uuid": "fa5726c0-44e9-469b-b060-208c231a07ba",
                "url": "http://web:3000/room/fa5726c0-44e9-469b-b060-208c231a07ba",
            },
        }
        ctx.server_address = "ap.aesais.net:50005"

        self.assertEqual(
            ctx.kh3_room_page_url(),
            "https://ap.aesais.net/room/fa5726c0-44e9-469b-b060-208c231a07ba",
        )

    def test_room_page_url_is_unavailable_when_standard_server_publishes_no_url(self) -> None:
        ctx = KH3Context.__new__(KH3Context)
        ctx.slot_data = {"kh3_seed": {"format": "kh3_server_seed"}}
        ctx.server_address = "archipelago.gg:38281"

        self.assertIsNone(ctx.kh3_room_page_url())

    def test_connected_seed_bootstrap_falls_back_to_inline_seed_when_hosted_download_fails(self) -> None:
        ctx = KH3Context.__new__(KH3Context)
        ctx.slot_data = {
            "kh3_room": {
                "format": "kh3_platform_room",
                "format_version": 1,
                "uuid": "room-id",
                "url": "https://ap.aesais.net/room/room-id",
            },
            "kh3_seed": {"format": "kh3_server_seed"},
        }
        ctx.server_address = "ap.aesais.net:38281"
        ctx.slot = 4
        ctx.auth = "Sora"
        ctx.seed_name = "AP_11111111111111111111"
        statuses: list[str] = []
        ctx._set_kh3_run_action_status = statuses.append
        downloader = Mock()
        downloader.has_platform_room.return_value = True
        downloader.download_player_artifact.side_effect = KH3PlatformSeedDownloadError("temporary download failure")
        importer = Mock()
        prepared = SimpleNamespace(status="installed")
        importer.prepare_server_slot_data.return_value = prepared
        ctx._get_platform_seed_download_service = Mock(return_value=downloader)
        ctx._get_seed_import_service = Mock(return_value=importer)

        result = ctx._prepare_connected_kh3_seed()

        self.assertIs(result, prepared)
        importer.prepare_player_artifact.assert_not_called()
        importer.prepare_server_slot_data.assert_called_once_with(
            ctx.slot_data,
            server_address="ap.aesais.net:38281",
            expected_slot=4,
            expected_slot_name="Sora",
            expected_seed_name="AP_11111111111111111111",
            progress=ctx._set_kh3_run_action_status,
        )
        self.assertTrue(any("generating" in status.casefold() for status in statuses))

    def test_client_connect_preparation_clears_cached_location_checks_and_uses_newest_pak_slot(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            mods_dir = root / "~mods"
            bridge_dir = root / "bridge"
            mods_dir.mkdir()
            bridge_dir.mkdir()
            old_pak = mods_dir / "pakchunk9-PlayerOld-randomizer-AP_11111111111111111111-1_99_P.pak"
            new_pak = mods_dir / "pakchunk9-PlayerKH-randomizer-AP_22222222222222222222-1_99_P.pak"
            old_pak.write_bytes(b"old")
            new_pak.write_bytes(b"new")
            os.utime(old_pak, (1, 1))
            os.utime(new_pak, (2, 2))

            ctx = KH3Context.__new__(KH3Context)
            ctx.kh3_mods_dir = mods_dir
            ctx.bridge_dir = bridge_dir
            ctx.seed_name = None
            ctx.offline_seed_name = "AP_11111111111111111111"
            ctx.auth = "PlayerOld"
            ctx.locations_checked = {1263022969}
            ctx.locations_scouted = {1263022969}
            ctx.goal_status_sent = True
            ctx.last_processed_game_state_seq = 7
            ctx.save_seed_mismatch_active = True
            ctx.last_save_seed_status_warning = "old"

            ctx.prepare_for_new_server_connection()

            self.assertEqual(ctx.auth, "PlayerKH")
            self.assertEqual(ctx.locations_checked, set())
            self.assertEqual(ctx.locations_scouted, set())
            self.assertFalse(ctx.goal_status_sent)
            self.assertEqual(ctx.last_processed_game_state_seq, -1)
            self.assertFalse(ctx.save_seed_mismatch_active)
            self.assertTrue(ctx.kh3_auth_inferred_from_installed_pak)

    def test_client_send_msgs_suppresses_location_checks_until_seed_guard_passes(self) -> None:
        async def run_case() -> list[list[dict]]:
            sent_messages: list[list[dict]] = []

            async def fake_common_send(_ctx, messages: list[dict]) -> None:
                sent_messages.append(messages)

            ctx = KH3Context.__new__(KH3Context)
            ctx.bridge_dir = None
            ctx.kh3_check_reporting_enabled = False
            with patch.object(CommonContext, "send_msgs", fake_common_send):
                await ctx.send_msgs([
                    {"cmd": "LocationChecks", "locations": [1263022969]},
                    {"cmd": "Get", "keys": ["_read_hints_0_1"]},
                ])
                ctx.kh3_check_reporting_enabled = True
                await ctx.send_msgs([
                    {"cmd": "LocationChecks", "locations": [1263022969]},
                ])
            return sent_messages

        sent_messages = asyncio.run(run_case())

        self.assertEqual(sent_messages, [
            [{"cmd": "Get", "keys": ["_read_hints_0_1"]}],
            [{"cmd": "LocationChecks", "locations": [1263022969]}],
        ])

    def test_client_save_seed_guard_requires_kh3_restart_after_seed_mismatch(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            save_dir = root / "SaveGames"
            save_dir.mkdir()
            ctx = KH3Context.__new__(KH3Context)
            ctx.bridge_save_dir = save_dir
            ctx.seed_name = "AP_85269139745991038903"
            ctx.offline_seed_name = None
            ctx.kh3_restart_required_seed_id = "85269139745991038903"

            with patch("worlds.kh3.Client.is_process_running", return_value=True), \
                    patch("worlds.kh3.Client.Utils.persistent_load", return_value={
                        "kh3_client": {
                            "last_save_seed_id": "85269139745991038903",
                            "last_save_seed_guard_version": KH3_SAVE_SEED_GUARD_VERSION,
                        }
                    }):
                status = ctx.build_save_seed_status()

            self.assertTrue(status.startswith("Mismatch: KH3 is still running after a seed mismatch"))

    def test_client_preserves_mismatched_saves_despite_legacy_auto_remove_setting(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bridge_dir = root / "bridge"
            save_dir = root / "SaveGames"
            bridge_dir.mkdir()
            save_dir.mkdir()
            (save_dir / "RandomizerEvents.sav").write_bytes(b"old-save")

            ctx = KH3Context.__new__(KH3Context)
            ctx.bridge_dir = bridge_dir
            ctx.bridge_save_dir = save_dir
            ctx.seed_name = "AP_85269139745991038903"
            ctx.offline_seed_name = None
            ctx.kh3_restart_required_seed_id = None
            ctx.auto_remove_saves_on_new_seed = True
            ctx.last_processed_game_state_seq = -1
            ctx.last_processed_bridge_notification_seq = 0
            ctx.last_processed_client_command_seq = 0
            ctx.goal_status_sent = False
            ctx.write_bridge_files = lambda: None
            save_key = KH3Context.save_seed_scope_key(save_dir)

            with patch("worlds.kh3.Client.is_process_running", return_value=False), \
                    patch("worlds.kh3.Client.Utils.persistent_load", return_value={
                        "kh3_client": {
                            KH3_SAVE_SEED_ASSOCIATIONS_KEY: {
                                save_key: {
                                    "seed_id": "11111111111111111111",
                                    "guard_version": KH3_SAVE_SEED_GUARD_VERSION,
                                }
                            },
                            "last_save_seed_id": "11111111111111111111",
                            "last_save_seed_guard_version": KH3_SAVE_SEED_GUARD_VERSION,
                        }
                    }), patch("worlds.kh3.Client.Utils.persistent_store") as persistent_store:
                self.assertFalse(ctx.refresh_save_seed_guard())

            self.assertTrue((save_dir / "RandomizerEvents.sav").exists())
            self.assertTrue(ctx.save_seed_mismatch_active)
            persistent_store.assert_not_called()

    def test_client_has_no_automatic_save_cleanup_setting(self) -> None:
        async def run_test() -> None:
            with TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                save_dir = root / "SaveGames"
                save_dir.mkdir()

                with patch("worlds.kh3.Client.Utils.persistent_load", return_value={"kh3_client": {}}):
                    ctx = KH3Context(
                        None,
                        None,
                        integrated_bridge=False,
                        kh3_dir=str(root),
                        save_dir=str(save_dir),
                        ue4ss_log=str(root / "UE4SS.log"),
                        probe_config=str(root / "probe.json"),
                        kh3_mods_dir=str(root / "mods"),
                    )

                try:
                    self.assertFalse(ctx.auto_delete_old_kh3_runs)
                finally:
                    ctx.keep_alive_task.cancel()
                    await asyncio.gather(ctx.keep_alive_task, return_exceptions=True)

        asyncio.run(run_test())

    def test_client_ignores_legacy_persisted_automatic_save_cleanup(self) -> None:
        async def run_test() -> None:
            with TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                save_dir = root / "SaveGames"
                save_dir.mkdir()

                with patch(
                    "worlds.kh3.Client.Utils.persistent_load",
                    return_value={"kh3_client": {"auto_remove_saves_on_new_seed": True}},
                ):
                    ctx = KH3Context(
                        None,
                        None,
                        integrated_bridge=False,
                        kh3_dir=str(root),
                        save_dir=str(save_dir),
                        ue4ss_log=str(root / "UE4SS.log"),
                        probe_config=str(root / "probe.json"),
                        kh3_mods_dir=str(root / "mods"),
                    )

                try:
                    self.assertFalse(ctx.auto_delete_old_kh3_runs)
                finally:
                    ctx.keep_alive_task.cancel()
                    await asyncio.gather(ctx.keep_alive_task, return_exceptions=True)

        asyncio.run(run_test())


class TestClientNotificationService(unittest.TestCase):
    @staticmethod
    def _make_source(bridge_dir: Path):
        class Source:
            slot = 1

            def __init__(self) -> None:
                self.bridge_dir = bridge_dir
                self.slot_data: dict = {}
                self.location_id_to_display_name: dict[int, str] = {}
                self.location_name_to_display_name: dict[str, str] = {}

            @staticmethod
            def slot_concerns_self(player: int) -> bool:
                return player == 1

            def _load_bridge_payload(self, file_name: str) -> dict | None:
                path = self.bridge_dir / file_name
                payload = load_optional_json_file(path)
                if payload is None:
                    return None
                return validate_protocol_payload(payload, str(path))

        return Source()

    @staticmethod
    def _make_service(source, presented: list[dict], logged: list[tuple]) -> ClientNotificationService:
        return ClientNotificationService(
            source,
            present_print_json=presented.append,
            load_optional_json=load_optional_json_file,
            validate_protocol=validate_protocol_payload,
            protocol_error=BridgeProtocolError,
            log_info=lambda *args: logged.append(args),
            log_warning=lambda *args: logged.append(("warning", *args)),
        )

    def test_headless_service_translates_and_delivers_ap_print_json(self) -> None:
        with TemporaryDirectory() as temp_dir:
            source = self._make_source(Path(temp_dir))
            source.slot_data = {"bridge": {"locations": [
                {
                    "ap_location_id": 7,
                    "ap_location_name": "Chest Seven",
                    "location_description": "Chest 7 (Large Chest, Twilight Town)",
                },
                {
                    "ap_location_id": 8,
                    "ap_location_name": "Legacy Location",
                    "location_description": "Friendly Location",
                },
            ]}}
            presented: list[dict] = []
            service = self._make_service(source, presented, [])
            original = {
                "data": [
                    {"type": "location_id", "text": "7", "player": 1},
                    {"type": "location_name", "text": "Legacy Location", "player": 1},
                    {"type": "location_id", "text": "7", "player": 2},
                ]
            }

            service.rebuild_location_display_indexes()
            service.deliver_print_json(original)

            self.assertEqual(original["data"][0]["type"], "location_id")
            self.assertEqual(presented[0]["data"][0]["text"], "Chest 7 (Large Chest, Twilight Town)")
            self.assertEqual(presented[0]["data"][1]["text"], "Friendly Location")
            self.assertEqual(presented[0]["data"][2], original["data"][2])

    def test_headless_service_deduplicates_and_logs_bridge_and_item_notices(self) -> None:
        with TemporaryDirectory() as temp_dir:
            bridge_dir = Path(temp_dir)
            source = self._make_source(bridge_dir)
            logged: list[tuple] = []
            service = self._make_service(source, [], logged)
            service.last_bridge_notification_seq = 2
            (bridge_dir / "bridge_notifications.json").write_text(json.dumps({
                "protocol_name": BRIDGE_PROTOCOL_NAME,
                "protocol_version": BRIDGE_PROTOCOL_VERSION,
                "seq": 4,
                "notifications": [
                    {"seq": 4, "message": "Fourth"},
                    {"seq": 2, "message": "Old"},
                    {"seq": 3, "message": " Third "},
                ],
            }), encoding="utf-8")

            service.handle_bridge_notifications_file()
            service.handle_bridge_notifications_file()

            self.assertEqual(logged, [("Third",), ("Fourth",)])
            self.assertEqual(service.last_bridge_notification_seq, 4)

    def test_context_notification_methods_remain_compatibility_delegates(self) -> None:
        service = Mock(spec=ClientNotificationService)
        ctx = KH3Context.__new__(KH3Context)
        ctx._get_client_notification_service = lambda: service
        print_args = {"data": []}

        ctx.rebuild_location_display_indexes()
        ctx.on_print_json(print_args)
        ctx.mark_existing_bridge_notifications_processed()
        asyncio.run(ctx.handle_bridge_notifications_file())

        service.rebuild_location_display_indexes.assert_called_once_with()
        service.deliver_print_json.assert_called_once_with(print_args)
        service.mark_existing_bridge_notifications_processed.assert_called_once_with()
        service.handle_bridge_notifications_file.assert_called_once_with()


class TestClientStatusSnapshotService(unittest.TestCase):
    @staticmethod
    def presenter() -> ClientStatusPresenter:
        class StatusSource:
            checked_locations = set()
            locations_checked = set()
            total_locations = 0
            location_key_to_id = {}
            player_names = {}
            slot = None
            auth = "Player"
            seed_name = "Seed"
            offline_seed_name = None
            bridge_dir = Path("bridge")
            server = None
            items_received = []

        return ClientStatusPresenter(StatusSource())

    def test_load_inputs_preserves_sidecar_order_and_normalizes_invalid_payloads(self) -> None:
        calls: list[tuple[str, str]] = []

        def protocol_reader(file_name: str) -> object:
            calls.append(("protocol", file_name))
            if file_name == "item_delivery.json":
                return {"pending_items": []}
            if file_name == "ap_items.json":
                return {"items": []}
            return None

        def sidecar_reader(file_name: str) -> object:
            calls.append(("sidecar", file_name))
            return "invalid" if file_name == "runtime_context.json" else {}

        def latest_save_load_reader() -> object:
            calls.append(("latest", "load"))
            return None

        service = ClientStatusSnapshotService(
            presenter=self.presenter(),
            protocol_payload_reader=protocol_reader,
            sidecar_payload_reader=sidecar_reader,
            latest_save_load_reader=latest_save_load_reader,
            current_save_slot_file="runtime_current_save_slot.json",
            display_save_slot_file="runtime_display_save_slot.json",
        )

        inputs = service.load_inputs()

        self.assertIsInstance(inputs, StatusSnapshotInputs)
        self.assertEqual(inputs.item_delivery_payload, {"pending_items": []})
        self.assertEqual(inputs.runtime_status, {})
        self.assertEqual(
            calls,
            [
                ("protocol", "item_delivery.json"),
                ("sidecar", "runtime_save_receipts.json"),
                ("sidecar", "runtime_current_save_slot.json"),
                ("sidecar", "runtime_display_save_slot.json"),
                ("latest", "load"),
                ("sidecar", "runtime_status.json"),
                ("sidecar", "runtime_ingredient_state.json"),
            ],
        )
        with self.assertRaises(FrozenInstanceError):
            inputs.runtime_status = {}

        calls.clear()
        inputs_without_ap_items = service.load_inputs()
        self.assertNotIn("ap_items_payload", inputs_without_ap_items.as_presenter_payloads())
        self.assertNotIn(("protocol", "ap_items.json"), calls)

    def test_composition_reuses_presenter_and_replay_rules_end_to_end(self) -> None:
        payloads = {
            "runtime_save_receipts.json": {
                "slots": {"kh3sv2/data/KHIII_slot1.bin": {"last_applied_index_at_save": 2,
                    "replay_preview_ready": True, "pending_replay_items": [{"index": 3, "item_name": "Blizzard"}]}}
            },
            "ap_items.json": {"items": [{"index": 3, "item_name": "Blizzard"}]},
        }
        service = ClientStatusSnapshotService(
            presenter=self.presenter(),
            protocol_payload_reader=lambda name: payloads.get(name),
            sidecar_payload_reader=lambda name: payloads.get(name),
            latest_save_load_reader=lambda: None,
            current_save_slot_file="runtime_current_save_slot.json",
            display_save_slot_file="runtime_display_save_slot.json",
        )

        inputs = service.load_inputs()
        snapshot = service.build_snapshot_from_inputs(inputs)
        replay_slots = service.build_replay_slots_from_inputs(inputs)

        self.assertIsInstance(snapshot, ClientStatusSnapshot)
        self.assertEqual(snapshot.as_legacy_dict()["checks"], "0")
        slot_one = next(entry for entry in replay_slots if entry.label == "S1")
        self.assertEqual(slot_one.text, "S1: AP2")
        self.assertIn("#3 Blizzard", slot_one.tooltip)

class TestKH3ClientStartup(unittest.TestCase):
    @staticmethod
    def run_main(connect: str | None) -> SimpleNamespace:
        lifecycle = SimpleNamespace(
            start_gui=Mock(),
            start_cli=Mock(),
            start_bridge_watcher=Mock(),
            start_server=Mock(),
            shutdown=AsyncMock(),
        )
        context = SimpleNamespace(
            auth=None,
            kh3_mods_dir=Path("mods"),
            lifecycle_service=lifecycle,
            initialize_offline_bridge_after_startup=AsyncMock(return_value=True),
            exit_event=SimpleNamespace(wait=AsyncMock()),
        )
        with (
            patch.object(KH3ClientModule, "KH3Context", return_value=context),
            patch.object(KH3ClientModule, "gui_enabled", True),
            patch.object(KH3ClientModule, "deactivate_incompatible_seed_paks", return_value=True),
        ):
            asyncio.run(KH3ClientModule.main(connect=connect, integrated_bridge=False))
        return lifecycle

    def test_normal_gui_launch_starts_disconnected(self) -> None:
        lifecycle = self.run_main(None)

        lifecycle.start_server.assert_not_called()
        lifecycle.shutdown.assert_awaited_once_with()

    def test_explicit_connect_argument_still_connects_on_launch(self) -> None:
        lifecycle = self.run_main("127.0.0.1:38282")

        lifecycle.start_server.assert_called_once_with("127.0.0.1:38282", task_name="ServerLoop")
        lifecycle.shutdown.assert_awaited_once_with()


class TestKH3ClientStatus(unittest.TestCase):
    @staticmethod
    def build_seed_policy_service(
        *,
        seed_name: str = "AP_85269139745991038903",
        mods_dir: Path,
        save_management: SaveManagementService | Mock,
        installed_paks: list[dict[str, object]] | None = None,
        process_running=lambda: False,
        persistent_kh3: dict | None = None,
    ) -> SeedPolicyService:
        source = SimpleNamespace(seed_name=seed_name, offline_seed_name=None)
        return SeedPolicyService(
            seed_guard=SeedGuardService(source),
            save_management=save_management,
            mods_dir=lambda: mods_dir,
            installed_paks=lambda: installed_paks or [],
            process_running=process_running,
            load_persistent_kh3=lambda: persistent_kh3 or {},
            resolve_save_dir_input=lambda value: Path(value).expanduser().resolve(),
            resolve_save_dir_for_root=lambda root: root / "SaveGames",
            is_dynamic_save_root=lambda value: Path(value).name.casefold() == "steam",
            is_auto_save_dir=lambda value: Path(value).name.casefold() == "savegames",
            log_info=lambda *_args: None,
            log_warning=lambda *_args: None,
        )

    def test_seed_policy_service_makes_typed_pak_and_save_decisions_headlessly(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            mods_dir = root / "mods"
            save_dir = root / "SaveGames"
            mods_dir.mkdir()
            save_dir.mkdir()
            save_management = Mock(spec=SaveManagementService)
            save_management.should_clean_steam_sibling_save_dirs.return_value = False
            save_management.save_cleanup_targets.return_value = [save_dir]
            save_management.summarize_save_directory.return_value = {"active_files": 1}
            scope = SeedGuardService.save_seed_scope_key(save_dir)
            service = self.build_seed_policy_service(
                mods_dir=mods_dir,
                save_management=save_management,
                installed_paks=[{
                    "seed": "11111111111111111111",
                    "name": "old.pak",
                    "path": str(mods_dir / "old.pak"),
                }],
                persistent_kh3={
                    KH3_SAVE_SEED_ASSOCIATIONS_KEY: {
                        scope: {
                            "seed_id": "11111111111111111111",
                            "guard_version": KH3_SAVE_SEED_GUARD_VERSION,
                        }
                    }
                },
            )

            pak_decision = service.installed_pak_decision()
            save_decision = service.build_save_seed_decision(
                restart_required_seed=None,
                offline_seed_name=None,
            )
            selected_save = service.select_save_directory(root / "Steam")
            automatic_save = service.select_save_directory_for_game_root(
                root / "new-game",
                current_save_dir=save_dir,
                save_dir_override=False,
                dynamic_root_override=False,
            )

            self.assertIsInstance(pak_decision, InstalledPakDecision)
            self.assertFalse(pak_decision.compatible)
            self.assertEqual(
                pak_decision.status,
                "Mismatch: installed 11111111111111111111, expected 85269139745991038903",
            )
            self.assertIsInstance(save_decision, SaveSeedDecision)
            self.assertTrue(save_decision.mismatch)
            self.assertEqual(
                save_decision.status,
                "Mismatch: active KH3 saves were last associated with 11111111111111111111, "
                "expected 85269139745991038903",
            )
            self.assertTrue(selected_save.override)
            self.assertTrue(selected_save.dynamic_root_override)
            self.assertEqual(selected_save.persisted_value, str((root / "Steam").resolve()))
            self.assertIsNotNone(automatic_save)
            assert automatic_save is not None
            self.assertEqual(automatic_save.save_dir, root / "new-game" / "SaveGames")

    def test_seed_policy_service_ignores_other_steam_accounts(self) -> None:
        seed = "06949465657404259881"
        for has_current_save in (False, True):
            for sibling_seed in (None, seed, "11111111111111111111"):
                with self.subTest(has_current_save=has_current_save, sibling_seed=sibling_seed), \
                        TemporaryDirectory() as temp_dir:
                    root = Path(temp_dir)
                    steam_root = root / "Steam"
                    save_dir = steam_root / ("0" * 32) / "SaveGames"
                    sibling_dir = steam_root / "76561198000000000" / "SaveGames"
                    save_dir.mkdir(parents=True)
                    sibling_dir.mkdir(parents=True)
                    if has_current_save:
                        (save_dir / "RandomizerEvents.sav").write_bytes(b"current-save")
                    (sibling_dir / "RandomizerEvents.sav").write_bytes(b"other-account-save")
                    scope = SeedGuardService.save_seed_scope_key(save_dir)
                    sibling_scope = SeedGuardService.save_seed_scope_key(sibling_dir)
                    associations = {
                        scope: {"seed_id": seed, "guard_version": KH3_SAVE_SEED_GUARD_VERSION},
                    }
                    if sibling_seed is not None:
                        associations[sibling_scope] = {
                            "seed_id": sibling_seed,
                            "guard_version": KH3_SAVE_SEED_GUARD_VERSION,
                        }
                    persistent_kh3 = {KH3_SAVE_SEED_ASSOCIATIONS_KEY: associations}
                    save_management = SaveManagementService(
                        SimpleNamespace(bridge_save_dir=save_dir, save_dir_override=False,
                                        save_dir_dynamic_root_override=False),
                        process_running=lambda: False,
                    )
                    service = self.build_seed_policy_service(
                        seed_name=f"AP_{seed}", mods_dir=root, save_management=save_management,
                        persistent_kh3=persistent_kh3,
                    )
                    with patch("worlds.kh3.Client.Utils.persistent_load",
                               return_value={"kh3_client": persistent_kh3}), \
                            patch("worlds.kh3.Client.Utils.persistent_store") as persistent_store:
                        result = service.refresh_save_seed_guard(
                            restart_required_seed=None, offline_seed_name=None,
                            connected_seed_name=f"AP_{seed}", last_warning="",
                        )

                    self.assertTrue(result.allowed, result.status)
                    self.assertFalse(result.mismatch)
                    stored = persistent_store.call_args_list[0].args[2]
                    self.assertEqual(stored.get(sibling_scope), associations.get(sibling_scope))
                    self.assertEqual(stored[scope]["seed_id"], seed)

    def test_seed_policy_service_can_ignore_unverified_redirected_save_folder(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            save_dir = root / "redirected-account" / "SaveGames"
            save_management = Mock(spec=SaveManagementService)
            save_management.should_clean_steam_sibling_save_dirs.return_value = False
            save_management.save_cleanup_targets.return_value = [save_dir]
            save_management.summarize_save_directory.return_value = {"active_files": 1}
            service = self.build_seed_policy_service(
                mods_dir=root,
                save_management=save_management,
                persistent_kh3={},
            )

            result = service.refresh_save_seed_guard(
                restart_required_seed=None,
                offline_seed_name=None,
                connected_seed_name="AP_85269139745991038903",
                ignore_unverified_warning=True,
                last_warning="old warning",
            )

            self.assertTrue(result.allowed)
            self.assertFalse(result.mismatch)
            self.assertEqual(result.warning, "")
            self.assertTrue(result.status.startswith("Ignored by setting: Unverified:"))

    def test_seed_policy_service_rejects_duplicate_files_for_the_same_seed(self) -> None:
        with TemporaryDirectory() as temp_dir:
            mods_dir = Path(temp_dir)
            seed = "85269139745991038903"
            installed_paks = [
                {
                    "seed": seed,
                    "name": f"pakchunk9-Sora-randomizer-AP_{seed}-1_99_P.pak",
                    "path": str(mods_dir / "first.pak"),
                },
                {
                    "seed": seed,
                    "name": f"pakchunk9-Riku-randomizer-AP_{seed}-2_99_P.pak",
                    "path": str(mods_dir / "second.pak"),
                },
            ]
            service = self.build_seed_policy_service(
                mods_dir=mods_dir,
                save_management=Mock(spec=SaveManagementService),
                installed_paks=installed_paks,
            )

            guard = service.guard_installed_pak_before_connect(installed_paks)
            status = service.installed_pak_decision()

            self.assertFalse(guard.allowed)
            self.assertIn("Multiple KH3 AP seed paks were found", guard.warning)
            self.assertFalse(status.compatible)
            self.assertEqual(
                status.status,
                f"Multiple AP seed paks installed (2); expected {seed}",
            )

    def test_seed_policy_service_rejects_undersized_seed_pak(self) -> None:
        with TemporaryDirectory() as temp_dir:
            mods_dir = Path(temp_dir)
            seed = "85269139745991038903"
            installed_paks = [{
                "seed": seed,
                "name": f"pakchunk9-Sora-randomizer-AP_{seed}-1_99_P.pak",
                "path": str(mods_dir / "seed.pak"),
                "size": 872,
            }]
            service = self.build_seed_policy_service(
                mods_dir=mods_dir,
                save_management=Mock(spec=SaveManagementService),
                installed_paks=installed_paks,
            )

            guard = service.guard_installed_pak_before_connect(installed_paks)
            status = service.installed_pak_decision()

            self.assertFalse(guard.allowed)
            self.assertEqual(
                guard.warning,
                "The seed file had an error in generating. Please regenerate the seed file. "
                "The approximate size should be around 860kb.",
            )
            self.assertFalse(status.compatible)
            self.assertIn("minimum is 5120 bytes", status.status)

    def test_seed_policy_service_allows_seed_pak_at_minimum_size(self) -> None:
        with TemporaryDirectory() as temp_dir:
            mods_dir = Path(temp_dir)
            seed = "85269139745991038903"
            installed_paks = [{
                "seed": seed,
                "name": f"pakchunk9-Sora-randomizer-AP_{seed}-1_99_P.pak",
                "path": str(mods_dir / "seed.pak"),
                "size": KH3_MINIMUM_SEED_PAK_SIZE_BYTES,
            }]
            service = self.build_seed_policy_service(
                mods_dir=mods_dir,
                save_management=Mock(spec=SaveManagementService),
                installed_paks=installed_paks,
            )

            guard = service.guard_installed_pak_before_connect(installed_paks)
            status = service.installed_pak_decision()

            self.assertTrue(guard.allowed)
            self.assertEqual(guard.warning, "")
            self.assertTrue(status.compatible)
            self.assertEqual(status.status, f"OK {seed}")

    def test_seed_policy_service_disconnect_preserves_effect_order_and_result(self) -> None:
        with TemporaryDirectory() as temp_dir:
            save_management = Mock(spec=SaveManagementService)
            service = self.build_seed_policy_service(
                mods_dir=Path(temp_dir),
                save_management=save_management,
                process_running=lambda: True,
            )
            effects: list[str] = []

            result = service.disconnect_for_seed_guard(
                "save_seed_mismatch",
                "Mismatch: test",
                restart_required_seed=None,
                set_restart_required_seed=lambda seed: effects.append(f"restart:{seed}"),
                stop_bridge=lambda: effects.append("stop"),
                clear_locations=lambda: effects.append("clear"),
                reset_observations=lambda reason: effects.append(f"reset:{reason}"),
                write_bridge_files=lambda: effects.append("write"),
                schedule_disconnect=lambda: effects.append("schedule") or True,
            )

            self.assertIsInstance(result, DisconnectPolicyResult)
            self.assertEqual(result.restart_required_seed, "85269139745991038903")
            self.assertTrue(result.disconnect_scheduled)
            self.assertEqual(effects, [
                "restart:85269139745991038903",
                "stop",
                "clear",
                "reset:seed_guard_blocked:save_seed_mismatch",
                "write",
                "schedule",
            ])

    def test_seed_policy_service_preserves_context_compatibility_delegates(self) -> None:
        ctx = KH3Context.__new__(KH3Context)
        ctx.last_pak_status_warning = "old pak warning"
        ctx.kh3_restart_required_seed_id = None
        ctx.offline_seed_name = "AP_11111111111111111111"
        ctx.seed_name = "AP_85269139745991038903"
        ctx.auto_remove_saves_on_new_seed = True
        ctx.ignore_unverified_save_seed_warnings = False
        ctx.last_save_seed_status_warning = "old save warning"
        service = Mock(spec=SeedPolicyService)
        service.installed_pak_decision.return_value = InstalledPakDecision(
            "85269139745991038903",
            ("85269139745991038903",),
            "OK 85269139745991038903",
            True,
        )
        service.warn_if_installed_pak_mismatch.return_value = "new pak warning"
        service.build_save_seed_decision.return_value = SaveSeedDecision(
            "85269139745991038903",
            "OK 85269139745991038903",
            False,
            None,
            (),
        )
        service.refresh_save_seed_guard.return_value = SaveGuardResult(
            True,
            "OK 85269139745991038903",
            False,
            "new save warning",
            None,
            ctx.seed_name,
        )
        ctx._get_seed_policy_service = lambda: service

        self.assertEqual(ctx.build_installed_pak_status(), "OK 85269139745991038903")
        self.assertTrue(ctx.is_installed_pak_compatible())
        ctx.warn_if_installed_pak_mismatch()
        self.assertEqual(ctx.build_save_seed_status(), "OK 85269139745991038903")
        self.assertTrue(ctx.refresh_save_seed_guard())

        service.warn_if_installed_pak_mismatch.assert_called_once_with("old pak warning")
        service.build_save_seed_decision.assert_called_once_with(
            restart_required_seed=None,
            offline_seed_name="AP_11111111111111111111",
        )
        service.refresh_save_seed_guard.assert_called_once_with(
            restart_required_seed=None,
            offline_seed_name="AP_11111111111111111111",
            connected_seed_name="AP_85269139745991038903",
            ignore_unverified_warning=False,
            last_warning="old save warning",
        )
        self.assertEqual(ctx.last_pak_status_warning, "new pak warning")
        self.assertEqual(ctx.last_save_seed_status_warning, "new save warning")

    def test_seed_guard_service_preserves_context_compatibility_helpers(self) -> None:
        ctx = KH3Context.__new__(KH3Context)
        ctx.seed_name = "Archipelago_85269139745991038903"
        ctx.offline_seed_name = None

        service = ctx._get_seed_guard_service()

        self.assertIsInstance(service, SeedGuardService)
        self.assertEqual(service.expected_seed_id(), "85269139745991038903")
        self.assertEqual(ctx.expected_seed_id(), service.expected_seed_id())
        self.assertEqual(
            ctx.load_save_seed_associations({KH3_SAVE_SEED_ASSOCIATIONS_KEY: {"scope": "85269139745991038903"}}),
            {"scope": {"seed_id": "85269139745991038903", "guard_version": 0}},
        )

    def test_seed_guard_persists_exact_managed_run_owner_for_same_seed_profiles(self) -> None:
        with TemporaryDirectory() as temp_dir:
            save_dir = Path(temp_dir) / "SaveGames"
            run_id = "78815585780120999120-1-SaveMgrTest--localprofile"
            source = SimpleNamespace(kh3_active_connection_profile_run_id=run_id)
            service = SeedGuardService(source)
            scope = service.save_seed_scope_key(save_dir)

            with patch("worlds.kh3.client_support.seed_guard.Utils.persistent_load", return_value={}), \
                    patch("worlds.kh3.client_support.seed_guard.Utils.persistent_store") as persistent_store:
                service.store_save_seed_associations_for_targets(
                    [save_dir],
                    "78815585780120999120",
                )

            associations = {
                scope: {
                    "seed_id": "78815585780120999120",
                    "guard_version": KH3_SAVE_SEED_GUARD_VERSION,
                    "run_id": run_id,
                }
            }
            persistent_store.assert_any_call(
                "kh3_client",
                KH3_SAVE_SEED_ASSOCIATIONS_KEY,
                associations,
            )
            self.assertEqual(
                service.save_run_association_for_dir(
                    save_dir,
                    {KH3_SAVE_SEED_ASSOCIATIONS_KEY: associations},
                ),
                run_id,
            )

    def test_seed_guard_clears_only_the_deleted_active_run_scope(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            deleted_save_dir = root / "Deleted" / "SaveGames"
            retained_save_dir = root / "Retained" / "SaveGames"
            deleted_run_id = "78815585780120999120-1-SaveMgrTest--localprofile"
            service = SeedGuardService(SimpleNamespace())
            retained_record = {
                "seed_id": "78815585780120999120",
                "guard_version": KH3_SAVE_SEED_GUARD_VERSION,
                "run_id": "78815585780120999120-1-SaveMgrTest--hostedprofile",
            }
            persistent = {
                "kh3_client": {
                    KH3_SAVE_SEED_ASSOCIATIONS_KEY: {
                        service.save_seed_scope_key(deleted_save_dir): {
                            "seed_id": "78815585780120999120",
                            "guard_version": KH3_SAVE_SEED_GUARD_VERSION,
                            "run_id": deleted_run_id,
                        },
                        service.save_seed_scope_key(retained_save_dir): retained_record,
                    }
                }
            }

            with patch(
                "worlds.kh3.client_support.seed_guard.Utils.persistent_load",
                return_value=persistent,
            ), patch("worlds.kh3.client_support.seed_guard.Utils.persistent_store") as persistent_store:
                service.clear_save_seed_associations_for_targets(
                    [deleted_save_dir],
                    expected_run_id=deleted_run_id,
                )

            persistent_store.assert_called_once_with(
                "kh3_client",
                KH3_SAVE_SEED_ASSOCIATIONS_KEY,
                {service.save_seed_scope_key(retained_save_dir): retained_record},
            )

    def test_save_management_service_preserves_context_compatibility_delegates(self) -> None:
        ctx = KH3Context.__new__(KH3Context)
        service = Mock(spec=SaveManagementService)
        service.display_kh3_save_dir.return_value = Path("display-save")
        ctx._get_save_management_service = lambda: service

        self.assertEqual(ctx.display_kh3_save_dir(), Path("display-save"))
        ctx.backup_vanilla_saves_before_ap_install()

        service.display_kh3_save_dir.assert_called_once_with()
        service.backup_vanilla_saves_before_ap_install.assert_called_once_with()

    def test_headless_presenter_builds_typed_status_models(self) -> None:
        class StatusSource:
            checked_locations = {1}
            locations_checked = set()
            total_locations = 2
            location_key_to_id = {"TreasureTT:TT_LBOX_001": 1, "TreasureTT:TT_LBOX_002": 2}
            player_names = {1: "Sora"}
            slot = 1
            auth = "Player"
            seed_name = "Seed"
            offline_seed_name = None
            bridge_dir = Path("bridge")
            server = None
            items_received = [object()]

        payloads = {
            "item_delivery_payload": {"pending_items": [{"index": 2}], "total_received_item_count": 3},
            "item_delivery_state": {"last_applied_actions": []},
            "item_delivery_ledger": {},
            "native_save_receipts": {
                "slots": {"kh3sv2/data/KHIII_slot0.bin": {"last_applied_index_at_save": 1}}
            },
            "runtime_current_save_slot": {},
            "runtime_display_save_slot": {},
            "last_runtime_save_load_event": {},
            "bridge_runtime_status": {
                "status": "running", "updated_at_utc": "2026-07-09T12:00:00Z", "save_dir": "runtime-saves",
            },
            "runtime_loot_state": {},
            "bridge_notifications": {},
            "runtime_context": {"world": "World /Game/Levels/tt/tt_01/tt_01.tt_01"},
            "runtime_map_ready": {},
            "runtime_blocking_ui_state": {"blocking_ui_active": False},
            "last_safe_runtime_context": {"status": "ok"},
            "runtime_sora_state": {"special_battle_mode_active": False, "safety": "ok"},
        }
        payloads["runtime_status"] = {"presentation": {
            "current_map": "tt/tt_01/tt_01 (probe)", "ap_items_grantable": "DLL",
        }}
        presenter = ClientStatusPresenter(StatusSource())

        snapshot = presenter.build_status_snapshot_from_payloads(payloads)

        self.assertIsInstance(snapshot, ClientStatusSnapshot)
        self.assertIsInstance(snapshot.runtime_grant, RuntimeGrantSummary)
        self.assertTrue(all(isinstance(entry, ReplaySlotSummary) for entry in snapshot.replay_slots))
        self.assertEqual(snapshot.replay_slots[0].replay_index, 1)
        self.assertEqual(snapshot.value_for("current_map"), "tt/tt_01/tt_01 (probe)")
        self.assertEqual(snapshot.value_for("ap_items_grantable"), "DLL")
        self.assertEqual(snapshot.value_for("item_counts"), "- / Waiting for KH3 / -")
        self.assertEqual(snapshot.as_legacy_dict()["slot_replay_indices"], "AUTO: AP1")

    def test_installed_runtime_summary_formats_raw_status_headlessly(self) -> None:
        summary = build_installed_runtime_summary(
            {
                "ue4ss_files": {"UE4SS.dll": True},
                "ue4ss_proxy_installed": True,
                "ue4ss_settings_compliant": True,
                "ue4ss_custom_conflict": False,
                "ue4ss_existing": True,
                "probe_single_layout_ready": True,
                "probe_dll_installed": True,
                "probe_dll_matches_bundle": True,
                "probe_dll_version": "1.2.3",
                "probe_mod_enabled": True,
                "probe_config_installed": True,
            },
            {"status": "current", "expected_version": "2.0.0"},
        )

        self.assertIsInstance(summary, InstalledRuntimeSummary)
        self.assertTrue(summary.installed)
        self.assertEqual(summary.ue4ss_status, "v1.2.3")
        self.assertEqual(summary.probe_status, "Installed in active Mods layouts")
        self.assertEqual(summary.goa_status, "Current v2.0.0")
        self.assertIn("already ready", summary.patch_tooltip)

    def test_presenter_checks_goa_status_only_once_per_client_session(self) -> None:
        class StatusSource:
            kh3_dir = Path("kh3")
            goa_status_calls = 0

            def goa_mod_status(self) -> dict:
                self.goa_status_calls += 1
                raise OSError("synthetic GitHub rate limit")

            @staticmethod
            def has_goa_mod() -> bool:
                return True

        source = StatusSource()
        presenter = ClientStatusPresenter(source, runtime_status_loader=lambda _path: {})

        with self.assertLogs("worlds.kh3.client_support.presentation", level="WARNING"):
            first = presenter.build_installed_runtime_summary(now=0.0)
        second = presenter.build_installed_runtime_summary(now=3600.0)

        self.assertEqual(source.goa_status_calls, 1)
        self.assertEqual(first.goa_status, "Installed; version check unavailable")
        self.assertEqual(second.goa_status, first.goa_status)

    def test_presenter_rechecks_goa_status_after_cache_invalidation(self) -> None:
        class StatusSource:
            kh3_dir = Path("kh3")
            goa_status_calls = 0

            def goa_mod_status(self) -> dict:
                self.goa_status_calls += 1
                return {
                    "status": "outdated" if self.goa_status_calls == 1 else "current",
                    "expected_version": "test",
                }

            @staticmethod
            def has_goa_mod() -> bool:
                return True

        source = StatusSource()
        presenter = ClientStatusPresenter(source, runtime_status_loader=lambda _path: {})

        first = presenter.build_installed_runtime_summary(now=0.0)
        presenter.invalidate_goa_status_cache()
        second = presenter.build_installed_runtime_summary(now=1.0)

        self.assertEqual(source.goa_status_calls, 2)
        self.assertEqual(first.goa_status, "Outdated; Patch Game will update vtest")
        self.assertEqual(second.goa_status, "Current vtest")

    def test_presenter_empty_runtime_save_dir_uses_configured_fallback(self) -> None:
        configured = Path("configured-save")

        class StatusSource:
            bridge_save_dir = configured

            @staticmethod
            def _load_bridge_payload(_name: str) -> dict:
                return {"status": "running", "save_dir": "", "save_dir_source": "bridge"}

        self.assertEqual(
            ClientStatusPresenter(StatusSource()).active_runtime_save_directory(),
            (configured, "configured"),
        )

    def test_presenter_runtime_save_dir_error_logs_and_uses_configured_fallback(self) -> None:
        configured = Path("configured-save")

        class StatusSource:
            bridge_save_dir = configured

            @staticmethod
            def _load_bridge_payload(_name: str) -> dict:
                raise OSError("synthetic read failure")

        with self.assertLogs("worlds.kh3.client_support.presentation", level="WARNING") as captured:
            result = ClientStatusPresenter(StatusSource()).active_runtime_save_directory()

        self.assertEqual(result, (configured, "configured"))
        self.assertIn("Could not refresh KH3 bridge runtime status: synthetic read failure", captured.output[0])

    def test_is_process_running_non_windows_uses_process_status(self) -> None:
        with patch("worlds.kh3.ExternalBridge.os.name", "posix"), \
                patch("worlds.kh3.ExternalBridge.get_process_status", return_value={"running": True}) as status:
            self.assertTrue(is_process_running("KINGDOM HEARTS III.exe"))

        status.assert_called_once_with("KINGDOM HEARTS III.exe")

    def test_is_process_running_windows_tasklist_none_stdout_assumes_closed(self) -> None:
        class FakeStartupInfo:
            dwFlags = 0
            wShowWindow = 0

        class FakeResult:
            stdout = None

        with patch("worlds.kh3.ExternalBridge.os.name", "nt"), \
                patch("worlds.kh3.ExternalBridge._get_process_status_windows_api", return_value=None), \
                patch("worlds.kh3.ExternalBridge.subprocess.STARTUPINFO", return_value=FakeStartupInfo(), create=True), \
                patch("worlds.kh3.ExternalBridge.subprocess.STARTF_USESHOWWINDOW", 1, create=True), \
                patch("worlds.kh3.ExternalBridge.subprocess.SW_HIDE", 0, create=True), \
                patch("worlds.kh3.ExternalBridge.subprocess.CREATE_NO_WINDOW", 0, create=True), \
                patch("worlds.kh3.ExternalBridge.subprocess.run", return_value=FakeResult()):
            self.assertFalse(is_process_running("KINGDOM HEARTS III.exe"))

    def test_linux_proc_process_status_detects_wine_game_image(self) -> None:
        with TemporaryDirectory() as temp_dir:
            proc_root = Path(temp_dir)
            (proc_root / "stat").write_text("cpu  0 0 0 0\nbtime 1000\n", encoding="utf-8")
            process_dir = proc_root / "1234"
            process_dir.mkdir()
            process_dir.joinpath("cmdline").write_bytes(
                b"Z:\\steamapps\\common\\KINGDOM HEARTS III\\KINGDOM HEARTS III\\Binaries\\Win64\\KINGDOM HEARTS III.exe\x00"
                b"-private-store-arguments\x00"
            )
            process_dir.joinpath("stat").write_text(
                "1234 (KINGDOM HEARTS III.exe) S "
                + " ".join(["0"] * 18 + ["100"] + ["0"] * 8),
                encoding="utf-8",
            )

            with patch("worlds.kh3.ExternalBridge.os.sysconf", return_value=100, create=True), \
                    patch("worlds.kh3.ExternalBridge.os.sysconf_names", {"SC_CLK_TCK": "SC_CLK_TCK"}, create=True):
                status = _get_process_status_linux_proc("KINGDOM HEARTS III.exe", proc_root)

            self.assertTrue(status["running"])
            self.assertEqual(status["pid"], 1234)
            self.assertEqual(status["started_at_utc"], datetime.fromtimestamp(1001, timezone.utc).isoformat())

    def test_linux_proc_process_status_ignores_unrelated_steam_command_line(self) -> None:
        with TemporaryDirectory() as temp_dir:
            proc_root = Path(temp_dir)
            (proc_root / "stat").write_text("cpu  0 0 0 0\nbtime 1000\n", encoding="utf-8")
            process_dir = proc_root / "5678"
            process_dir.mkdir()
            process_dir.joinpath("cmdline").write_bytes(b"/app/bin/steam\x00steam://run/2552450\x00")
            process_dir.joinpath("stat").write_text(
                "5678 (steam) S " + " ".join(["0"] * 27),
                encoding="utf-8",
            )

            status = _get_process_status_linux_proc("KINGDOM HEARTS III.exe", proc_root)

            self.assertFalse(status["running"])

    def test_gui_status_snapshot_displays_native_status_without_reading_sora_sidecar(self) -> None:
        ctx = KH3Context.__new__(KH3Context)
        ctx.checked_locations = set()
        ctx.locations_checked = set()
        ctx.missing_locations = set()
        ctx.location_key_to_id = {}
        ctx.player_names = {}
        ctx.slot = None
        ctx.auth = "Player"
        ctx.seed_name = "Seed"
        ctx.bridge_dir = Path("bridge")
        ctx.server = None
        ctx.items_received = []
        ctx.installed_kh3_ap_seed_paks = lambda: []
        ctx._load_bridge_payload = lambda name: {}
        ctx._load_bridge_sidecar_payload = Mock(side_effect=lambda name: {
            "presentation": {"sora_state": "TresCharStateMagic (id=7, runtime_sora_state_sidecar, 2026-05-30T12:00:00Z)"}
        } if name == "runtime_status.json" else {})

        snapshot = ctx._get_status_snapshot_service().build_snapshot().as_legacy_dict()

        self.assertEqual(
            snapshot["sora_state"],
            "TresCharStateMagic (id=7, runtime_sora_state_sidecar, 2026-05-30T12:00:00Z)",
        )
        self.assertNotIn(unittest.mock.call("runtime_sora_state.json"), ctx._load_bridge_sidecar_payload.call_args_list)

    def test_gui_status_snapshot_includes_last_save_loaded_from_native_slot_projection(self) -> None:
        with TemporaryDirectory() as temp_dir:
            bridge_dir = Path(temp_dir)
            (bridge_dir / "runtime_autosave_probe_file_events.jsonl").write_text(
                "\n".join([
                    json.dumps({
                        "type": "autosave_probe_file_open",
                        "observed_at": "2026-06-04T06:14:00Z",
                        "event_index": 20,
                        "path": "C:/SaveGames/kh3sv2/data/KHIII_slot3.bin",
                        "desired_access": 2147483648,
                        "creation_disposition": 3,
                    }),
                    json.dumps({
                        "type": "autosave_probe_file_open",
                        "observed_at": "2026-06-04T06:15:00Z",
                        "event_index": 21,
                        "path": "C:/SaveGames/kh3sv2/data/KHIII_slot0.bin",
                        "desired_access": 1073741824,
                        "creation_disposition": 2,
                    }),
                ]),
                encoding="utf-8",
            )
            ctx = KH3Context.__new__(KH3Context)
            ctx.checked_locations = set()
            ctx.locations_checked = set()
            ctx.missing_locations = set()
            ctx.location_key_to_id = {}
            ctx.player_names = {}
            ctx.slot = None
            ctx.auth = "Player"
            ctx.seed_name = "Seed"
            ctx.bridge_dir = bridge_dir
            ctx.server = None
            ctx.items_received = []
            ctx.installed_kh3_ap_seed_paks = lambda: []
            ctx._load_bridge_payload = lambda name: {}
            ctx._load_bridge_sidecar_payload = lambda name: {"last_save_load_event": {"owner": "dll", "status": "known", "access_kind": "load", "save_slot": 3, "slot_file": "kh3sv2/data/KHIII_slot3.bin"}} if name == "runtime_reconciliation_state.json" else {}

            snapshot = ctx._get_status_snapshot_service().build_snapshot()

            self.assertEqual(
                [slot.label for slot in snapshot.replay_slots if slot.is_last_loaded],
                ["S3"],
            )

    def test_last_save_loaded_display_describes_auto_and_manual_slots(self) -> None:
        self.assertEqual(
            ClientStatusSnapshotService.describe_last_save_loaded(
                {
                    "event": "runtime_save_file_open",
                    "access_kind": "load",
                    "save_slot": "AUTO",
                    "slot_file": "kh3sv2/data/KHIII_slot0.bin",
                }
            ),
            "Auto (slot 0)",
        )
        self.assertEqual(
            ClientStatusSnapshotService.describe_last_save_loaded(
                {
                    "event": "runtime_save_file_open",
                    "access_kind": "load",
                    "slot_file": "kh3sv2/data/KHIII_slot4.bin",
                }
            ),
            "Slot 4",
        )
        self.assertEqual(
            ClientStatusSnapshotService.describe_last_save_loaded(
                {
                    "event": "runtime_save_file_open",
                    "access_kind": "save",
                    "slot_file": "kh3sv2/data/KHIII_slot4.bin",
                }
            ),
            "-",
        )


    def test_pending_item_display_prefers_runtime_delivery_payload_over_raw_ledger(self) -> None:
        self.assertEqual(
            ClientStatusSnapshotService.display_pending_item_count(
                {"pending_items": [], "native_delivery_ready": True,
                 "native_delivery_summary": {"pending_count": 23}},
            ),
            "23",
        )
        self.assertEqual(
            ClientStatusSnapshotService.display_pending_item_count(
                {"pending_items": [{"index": 1}, {"index": 2}]},
            ),
            "Waiting for KH3",
        )

    def test_save_slot_display_describes_auto_and_manual_slots(self) -> None:
        self.assertEqual(
            ClientStatusSnapshotService.describe_save_slot(
                {
                    "last_resolution": {
                        "status": "matched",
                        "save_slot": 0,
                        "slot_file": "kh3sv2/data/KHIII_slot0.bin",
                    },
                }
            ),
            "Auto (slot 0)",
        )
        self.assertEqual(
            ClientStatusSnapshotService.describe_save_slot(
                {
                    "last_resolution": {
                        "status": "matched",
                        "save_slot": 4,
                        "slot_file": "kh3sv2/data/KHIII_slot4.bin",
                    },
                }
            ),
            "Slot 4",
        )
        self.assertEqual(
            ClientStatusSnapshotService.describe_save_slot(
                {
                    "last_resolution": {
                        "status": "matched",
                        "save_slot": "AUTO",
                        "slot_file": "kh3sv2/data/KHIII_slot0.bin",
                    },
                },
                {
                    "event": "runtime_display_save_slot",
                    "status": "known",
                    "save_slot": 2,
                    "slot_file": "kh3sv2/data/KHIII_slot2.bin",
                },
            ),
            "Slot 2",
        )

    def test_save_slot_display_prefers_active_runtime_current_slot(self) -> None:
        self.assertEqual(
            ClientStatusSnapshotService.describe_save_slot(
                {},
                {
                    "event": "runtime_display_save_slot",
                    "status": "known",
                    "save_slot": 4,
                    "slot_file": "kh3sv2/data/KHIII_slot4.bin",
                },
                {
                    "event": "runtime_current_save_slot",
                    "status": "known",
                    "save_slot": "AUTO",
                    "save_slot_label": "AUTO",
                    "slot_file": "kh3sv2/data/KHIII_slot0.bin",
                },
            ),
            "Auto (slot 0) | Last manual: Slot 4",
        )

    def test_save_slot_display_falls_back_to_slot_file_number(self) -> None:
        self.assertEqual(
            ClientStatusSnapshotService.describe_save_slot(
                {
                    "last_matched_resolution": {
                        "status": "matched",
                        "slot_file": "kh3sv2/data/KHIII_slot2.bin",
                    },
                }
            ),
            "Slot 2",
        )

    def test_slot_replay_display_pins_last_loaded_manual_slot_with_five_recent_slots(self) -> None:
        slots = {
            "kh3sv2/data/KHIII_slot0.bin": {"last_applied_index_at_save": 16},
            **{
                f"kh3sv2/data/KHIII_slot{slot_number}.bin": {
                    "last_applied_index_at_save": slot_number,
                    "stamped_at_utc": f"2026-08-{20 + slot_number:02d}T12:00:00+00:00",
                }
                for slot_number in range(1, 8)
            },
        }

        entries = ClientStatusSnapshotService.describe_slot_replay_entries(
            {"slots": slots},
            last_runtime_save_load_event={
                "access_kind": "load",
                "save_slot": 1,
                "slot_file": "kh3sv2/data/KHIII_slot1.bin",
                "observed_at_utc": "2026-08-30T13:00:00+00:00",
            },
        )

        self.assertEqual([entry["label"] for entry in entries], ["Auto", "S1", "S7", "S6", "S5", "S4"])
        self.assertNotIn("last slot loaded", entries[1]["tooltip"])

    def test_replay_slots_keep_current_and_last_loaded_visible_including_auto(self) -> None:
        slots = {
            f"kh3sv2/data/KHIII_slot{number}.bin": {
                "last_applied_index_at_save": number,
                "stamped_at_utc": f"2026-08-{20 + number:02d}T12:00:00+00:00",
            }
            for number in range(8)
        }
        for current, loaded in ((1, 2), (0, 2), (0, 0)):
            with self.subTest(current=current, loaded=loaded):
                entries = build_replay_slot_summaries(ReplaySlotSelectionInput(
                    native_save_receipts={"slots": slots},
                    runtime_current_save_slot={"status": "known", "save_slot": current},
                    last_runtime_save_load_event={"access_kind": "load", "save_slot": loaded},
                ))
                def label(number):
                    return f"S{number}" if number else "Auto"
                self.assertEqual([entry.label for entry in entries if entry.is_current], [label(current)])
                self.assertEqual([entry.label for entry in entries if entry.is_last_loaded], [label(loaded)])
                self.assertEqual(len(entries), 6)
                self.assertTrue(all("last slot loaded" not in entry.tooltip for entry in entries))

    def test_slot_replay_display_pins_last_manual_save_and_supports_slot99(self) -> None:
        slots = {
            "kh3sv2/data/KHIII_slot0.bin": {"last_applied_index_at_save": 16},
            **{
                f"kh3sv2/data/KHIII_slot{slot_number}.bin": {
                    "last_applied_index_at_save": slot_number,
                    "stamped_at_utc": f"2026-08-{20 + slot_number:02d}T12:00:00+00:00",
                }
                for slot_number in range(1, 7)
            },
        }

        entries = ClientStatusSnapshotService.describe_slot_replay_entries(
            {"slots": slots},
            runtime_display_save_slot={
                "status": "known",
                "save_slot": 99,
                "slot_file": "kh3sv2/data/KHIII_slot99.bin",
                "last_applied_index_at_save": 22,
                "observed_at_utc": "2026-08-30T14:00:00+00:00",
            },
        )

        self.assertEqual([entry["label"] for entry in entries], ["Auto", "S99", "S6", "S5", "S4", "S3"])
        self.assertEqual(entries[1]["text"], "S99: AP22")
        self.assertIn("Last saved: 2026-08-30T14:00:00+00:00", entries[1]["tooltip"])

    def test_slot_replay_tooltips_render_native_exact_membership(self) -> None:
        entries = ClientStatusSnapshotService.describe_slot_replay_entries({"slots": {
            "kh3sv2/data/KHIII_slot0.bin": {"last_applied_index_at_save": 0, "replay_preview_ready": True,
                "pending_replay_items": [{"index": 1, "item_name": "Cure"}, {"index": 3, "item_name": "Blizzard"}]},
            "kh3sv2/data/KHIII_slot2.bin": {"last_applied_index_at_save": 3, "replay_preview_ready": True,
                "pending_replay_items": []},
        }})
        by_label = {entry["label"]: entry for entry in entries}
        self.assertEqual(by_label["Auto"]["text"], "AUTO: AP0")
        self.assertIn("#1 Cure", by_label["Auto"]["tooltip"])
        self.assertIn("#3 Blizzard", by_label["Auto"]["tooltip"])
        self.assertNotIn("#2", by_label["Auto"]["tooltip"])
        self.assertIn("No unapplied AP items", by_label["S2"]["tooltip"])


    def test_slot_replay_tooltips_require_native_preview_and_keep_fresh_ledger(self) -> None:
        slots = {"kh3sv2/data/KHIII_slot1.bin": {"last_applied_index_at_save": 6,
            "replay_preview_ready": True, "pending_replay_items": [{"index": 9, "item_name": "Remote Blizzard"}]}}
        entries = ClientStatusSnapshotService.describe_slot_replay_entries({"slots": slots},
            runtime_display_save_slot={"status": "known", "save_slot": 1, "replay_preview_ready": True,
                "last_applied_index_at_save": 8,
                "pending_replay_items": [{"index": 8, "item_name": "Stale item"}]})
        self.assertEqual(next(entry["text"] for entry in entries if entry["label"] == "S1"), "S1: AP6")
        tooltip = next(entry["tooltip"] for entry in entries if entry["label"] == "S1")
        self.assertIn("#9 Remote Blizzard", tooltip)
        self.assertNotIn("Stale item", tooltip)
        slots["kh3sv2/data/KHIII_slot1.bin"].pop("replay_preview_ready")
        entries = ClientStatusSnapshotService.describe_slot_replay_entries({"slots": slots})
        tooltip = next(entry["tooltip"] for entry in entries if entry["label"] == "S1")
        self.assertIn("Native replay preview unavailable", tooltip)
        self.assertNotIn("#9", tooltip)


    def test_highest_ap_index_display_uses_max_item_index(self) -> None:
        self.assertEqual(
            ClientStatusSnapshotService.describe_highest_ap_index(
                {
                    "total_received_item_count": 3,
                    "applied_item_indices": [0, 9],
                    "pending_items": [{"index": 4}, {"index": "12"}],
                    "native_delivery_summary": {"highest_received_index": 15},
                }
            ),
            "15",
        )
        self.assertEqual(
            ClientStatusSnapshotService.describe_highest_ap_index(
                {
                    "total_received_item_count": 3,
                    "applied_item_indices": [],
                    "pending_items": [],
                }
            ),
            "-",
        )
        self.assertEqual(
            ClientStatusSnapshotService.describe_highest_ap_index(
                {
                    "total_received_item_count": 14,
                    "applied_item_indices": [0, 1, 2, 3, 4, 5, 8, 10],
                    "pending_items": [],
                }
            ),
            "-",
        )
