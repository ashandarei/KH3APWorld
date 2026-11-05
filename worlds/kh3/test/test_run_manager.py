from __future__ import annotations

import json
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from ..Client import KH3Context
from ..client_support.client_transport_service import ClientTransportService, ConnectionPreparationInput
from ..client_support.run_manager import (
    KH3RunActivationResult,
    KH3RunDeleteResult,
    KH3RunEnrollmentResult,
    KH3RunInfo,
    KH3RunManagerError,
    KH3RunManagerService,
    format_kh3_run_server_address,
    kh3_run_connection_affinity,
    normalize_kh3_run_server_host,
    split_kh3_run_server_address,
)
from ..client_support.seed_import_service import KH3SeedPreparationResult
from ..client_support.seed_policy_service import KH3_MINIMUM_SEED_PAK_SIZE_BYTES
from . import empty_wire_slot_data


def _write_seed_pak(root: Path, *, seed: str, player: int, slot: str) -> Path:
    path = root / f"pakchunk9-{slot}-randomizer-AP_{seed}-{player}_99_P.pak"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"P" * KH3_MINIMUM_SEED_PAK_SIZE_BYTES)
    return path


class _FailTargetSaveInstallService(KH3RunManagerService):
    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.move_calls = 0

    def _move_gameplay_save_files(self, source: Path, destination: Path) -> int:
        self.move_calls += 1
        if self.move_calls == 2:
            raise OSError("synthetic target save install failure")
        return super()._move_gameplay_save_files(source, destination)


class KH3RunManagerTest(unittest.TestCase):
    def test_inventory_ignores_incompatible_optional_bridge_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            service = self._service(root)
            source = _write_seed_pak(root / "downloads", seed="11111111111111111111", player=1, slot="Sora")
            imported = service.import_pak(source)
            invalid = empty_wire_slot_data()
            invalid["location_catalog"]["sha256"] = "0" * 64
            bridge = service.bridge_root / "11111111111111111111_Sora"
            bridge.mkdir(parents=True)
            (bridge / "slot_data.json").write_text(json.dumps(invalid))
            unrelated = service.bridge_root / "unrelated"
            unrelated.mkdir()
            (unrelated / "kh3_ap_seed_metadata.json").write_text(json.dumps({"slot_data": invalid}))
            listed = service.list_runs(force_refresh=True)
            self.assertEqual(len(listed), 1)
            self.assertEqual(listed[0].run_id, imported.run_id)
            self.assertEqual(listed[0].room_url, "")

    def _service(
        self,
        root: Path,
        *,
        running=False,
        service_type=KH3RunManagerService,
    ) -> KH3RunManagerService:
        return service_type(
            run_root=root / "user" / "kh3" / "runs",
            bridge_root=root / "user" / "kh3" / "bridge",
            mods_dir=root / "game" / "Content" / "Paks" / "~mods",
            save_dir=root / "saves" / "SaveGames",
            process_running=(running if callable(running) else lambda: bool(running)),
            now_utc=lambda: "2026-08-08T12:00:00Z",
        )

    def test_import_pak_creates_managed_run_without_activating_it(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = _write_seed_pak(root / "downloads", seed="11111111111111111111", player=2, slot="Sora")
            service = self._service(root)

            imported = service.import_pak(source)

            self.assertTrue(imported.managed)
            self.assertFalse(imported.active)
            self.assertTrue(imported.pak_available)
            self.assertEqual(imported.seed_id, "11111111111111111111")
            self.assertEqual(imported.player, 2)
            self.assertEqual(imported.slot_name, "Sora")
            self.assertEqual(imported.managed_pak_path.read_bytes(), source.read_bytes())
            self.assertFalse(service.mods_dir.exists())

    def test_import_pak_preserves_underscores_in_slot_name(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = _write_seed_pak(
                root / "downloads",
                seed="44685372886330020568",
                player=1,
                slot="left_middle_right",
            )
            service = self._service(root)

            imported = service.import_pak(source)

            self.assertEqual(imported.slot_name, "left_middle_right")
            self.assertEqual(imported.run_id, "44685372886330020568-1-left_middle_right")
            self.assertEqual(imported.pak_name, source.name)

    def test_store_seed_metadata_creates_managed_bridge_cache_for_run(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = _write_seed_pak(
                root / "downloads",
                seed="44685372886330020568",
                player=1,
                slot="left_middle_right",
            )
            service = self._service(root)
            imported = service.import_pak(source)
            metadata = {
                "format": "kh3_ap_seed_metadata",
                "seed": "AP_44685372886330020568-1-left_middle_right",
                "slot": 1,
                "slot_name": "left_middle_right",
                "slot_data": {"bridge": {"locations": []}},
            }

            metadata_path = service.store_seed_metadata(imported.run_id, metadata)
            refreshed = service.get_run(imported.run_id)

            self.assertEqual(json.loads(metadata_path.read_text(encoding="utf-8")), metadata)
            self.assertNotIn(b"\n", metadata_path.read_bytes())
            self.assertTrue((metadata_path.parent / "slot_data.json").is_file())
            self.assertFalse((metadata_path.parent / "runtime_seed_settings.json").exists())
            self.assertFalse((metadata_path.parent / "runtime_seed_hints.json").exists())
            self.assertEqual(metadata_path.parent.parent, service.bridge_root)
            self.assertIn(metadata_path.parent, refreshed.bridge_dirs)

    def test_store_seed_metadata_rejects_another_run_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = _write_seed_pak(
                root / "downloads",
                seed="11111111111111111111",
                player=1,
                slot="Sora",
            )
            service = self._service(root)
            imported = service.import_pak(source)
            wrong_metadata = {
                "format": "kh3_ap_seed_metadata",
                "seed": "AP_22222222222222222222-1-Sora",
                "slot": 1,
                "slot_name": "Sora",
            }

            with self.assertRaisesRegex(KH3RunManagerError, "does not match managed run"):
                service.store_seed_metadata(imported.run_id, wrong_metadata)

            self.assertFalse(service.bridge_root.exists())

    def test_run_inventory_reuses_cache_until_forced_or_sources_change(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            service = self._service(root)
            source = _write_seed_pak(
                root / "downloads",
                seed="11111111111111111111",
                player=2,
                slot="Sora",
            )
            imported = service.import_pak(source)

            with patch.object(service, "_build_run_inventory", wraps=service._build_run_inventory) as build:
                cached = service.list_runs()
                self.assertEqual([run.run_id for run in cached], [imported.run_id])
                build.assert_not_called()

                service.list_runs(force_refresh=True)
                build.assert_called_once_with()

                _write_seed_pak(
                    service.mods_dir,
                    seed="11111111111111111111",
                    player=2,
                    slot="Sora",
                )
                externally_refreshed = service.list_runs()
                self.assertEqual(build.call_count, 2)
                self.assertTrue(externally_refreshed[0].active)

    def test_cached_inventory_does_not_rescan_stored_save_trees(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            service = self._service(root)
            source = _write_seed_pak(
                root / "downloads",
                seed="11111111111111111111",
                player=2,
                slot="Sora",
            )
            service.import_pak(source)

            with patch.object(
                service,
                "_has_gameplay_save_files",
                side_effect=AssertionError("cached inventory recursively scanned saves"),
            ):
                self.assertEqual(len(service.list_runs()), 1)

    def test_ensure_pak_managed_enrolls_once_and_requires_the_connected_seed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            service = self._service(root)
            installed = _write_seed_pak(
                service.mods_dir,
                seed="11111111111111111111",
                player=2,
                slot="Sora",
            )

            with self.assertRaisesRegex(KH3RunManagerError, "not the connected seed"):
                service.ensure_pak_managed(
                    installed,
                    expected_seed_id="22222222222222222222",
                )

            first = service.ensure_pak_managed(
                installed,
                expected_seed_id="11111111111111111111",
            )
            with patch.object(service, "_atomic_copy_file", wraps=service._atomic_copy_file) as copy_file:
                second = service.ensure_pak_managed(
                    installed,
                    expected_seed_id="11111111111111111111",
                )

            self.assertTrue(first.copied)
            self.assertFalse(second.copied)
            self.assertEqual(first.run.run_id, second.run.run_id)
            self.assertTrue(second.run.managed)
            copy_file.assert_not_called()

    def test_managed_run_recovers_room_url_from_matching_bridge_slot_data(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            service = self._service(root)
            source = _write_seed_pak(
                root / "downloads",
                seed="11111111111111111111",
                player=2,
                slot="Sora",
            )
            imported = service.import_pak(source, server_address="ap.aesais.net:50005", connection_slot_name="Sora")
            bridge_dir = service.bridge_root / imported.bridge_dir_name
            bridge_dir.mkdir(parents=True)
            (bridge_dir / "slot_data.json").write_text(json.dumps({
                "kh3_room": {
                    "uuid": "room-id",
                    "url": "http://web:3000/room/room-id",
                },
            }), encoding="utf-8")

            recovered = service.get_run(imported.run_id)

            self.assertEqual(recovered.room_url, "https://ap.aesais.net/room/room-id")

    def test_connected_localhost_room_can_be_stored_listed_and_edited(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            service = self._service(root)
            source = _write_seed_pak(root / "downloads", seed="11111111111111111111", player=1, slot="Sora")
            room_url = "http://localhost:3000/room/test-room"
            enrolled = service.ensure_pak_managed(
                source, expected_seed_id="11111111111111111111",
                server_address="192.168.1.175:50000", connection_slot_name="Sora", room_url=room_url,
            )
            self.assertEqual(enrolled.run.room_url, room_url)
            self.assertEqual(service.get_run(enrolled.run.run_id).room_url, room_url)
            updated = service.update_connection_profile(
                enrolled.run.run_id, slot_name="Sora", server="192.168.1.175", port="50000", room_url=room_url,
            )
            self.assertEqual(updated.room_url, room_url)
            self.assertEqual(service.list_runs(force_refresh=True)[0].room_url, room_url)

    def test_managed_run_edits_connection_profile_without_changing_pak_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            service = self._service(root)
            source = _write_seed_pak(
                root / "downloads",
                seed="11111111111111111111",
                player=2,
                slot="PakIdentity",
            )
            imported = service.import_pak(source)

            updated = service.update_connection_profile(
                imported.run_id,
                slot_name="Room Slot Name",
                server="wss://archipelago.gg",
                port="45678",
                room_url="https://archipelago.gg/room/example-room",
            )
            refreshed = service.import_pak(source)

            self.assertEqual(updated.slot_name, "PakIdentity")
            self.assertEqual(updated.connection_slot_name, "Room Slot Name")
            self.assertEqual(updated.effective_slot_name, "Room Slot Name")
            self.assertEqual(updated.server_address, "wss://archipelago.gg:45678")
            self.assertEqual(updated.room_url, "https://archipelago.gg/room/example-room")
            self.assertEqual(refreshed.connection_slot_name, "Room Slot Name")
            self.assertEqual(refreshed.server_address, "wss://archipelago.gg:45678")
            self.assertEqual(refreshed.room_url, "https://archipelago.gg/room/example-room")

            service.install_managed_pak_when_none(imported.run_id)
            startup_profile = service.active_connection_profile()
            self.assertIsNotNone(startup_profile)
            self.assertEqual(startup_profile.run_id, imported.run_id)
            self.assertEqual(startup_profile.slot_name, "PakIdentity")
            self.assertEqual(startup_profile.effective_slot_name, "Room Slot Name")
            self.assertEqual(startup_profile.server_address, "wss://archipelago.gg:45678")

            with self.assertRaisesRegex(KH3RunManagerError, "slot name"):
                service.update_connection_profile(
                    imported.run_id,
                    slot_name=" ",
                    server="archipelago.gg",
                    port="45678",
                    room_url="",
                )

            with self.assertRaisesRegex(KH3RunManagerError, "HTTP or HTTPS"):
                service.update_connection_profile(
                    imported.run_id,
                    slot_name="Room Slot Name",
                    server="archipelago.gg",
                    port="45678",
                    room_url="file:///not-a-room",
                )

            cleared = service.update_connection_profile(
                imported.run_id,
                slot_name="Room Slot Name",
                server="archipelago.gg",
                port="45678",
                room_url="",
            )
            self.assertEqual(cleared.room_url, "")

    def test_connection_profile_address_fields_round_trip_secure_and_ipv6_servers(self) -> None:
        self.assertEqual(
            format_kh3_run_server_address("wss://archipelago.gg", "45678"),
            "wss://archipelago.gg:45678",
        )
        self.assertEqual(
            format_kh3_run_server_address("ws://[::1]:38282", 38282),
            "[::1]:38282",
        )
        self.assertEqual(
            split_kh3_run_server_address("wss://archipelago.gg:45678"),
            ("wss://archipelago.gg", "45678"),
        )
        self.assertEqual(
            split_kh3_run_server_address("[::1]:38282"),
            ("[::1]", "38282"),
        )
        with self.assertRaisesRegex(KH3RunManagerError, "does not match"):
            format_kh3_run_server_address("archipelago.gg:45678", "45679")

    def test_connection_affinity_uses_hostname_and_slot_but_ignores_port(self) -> None:
        self.assertEqual(normalize_kh3_run_server_host("WSS://ARCHIPELAGO.GG.:45678"), "archipelago.gg")
        self.assertEqual(
            kh3_run_connection_affinity("archipelago.gg:45678", "SaveMgrTest"),
            kh3_run_connection_affinity("wss://ARCHIPELAGO.GG:50005", "savemgrtest"),
        )
        self.assertNotEqual(
            kh3_run_connection_affinity("archipelago.gg:45678", "SaveMgrTest"),
            kh3_run_connection_affinity("127.0.0.1:45678", "SaveMgrTest"),
        )

    def test_same_seed_on_different_hosts_creates_distinct_runs_while_port_change_reuses_one(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            service = self._service(root)
            source = _write_seed_pak(
                root / "downloads",
                seed="78815585780120999120",
                player=1,
                slot="SaveMgrTest",
            )
            service.import_pak(source)

            archipelago = service.ensure_pak_managed(
                source,
                expected_seed_id="78815585780120999120",
                server_address="wss://archipelago.gg:45678",
                connection_slot_name="SaveMgrTest",
                room_url="https://archipelago.gg/room/example",
            ).run
            local = service.ensure_pak_managed(
                source,
                expected_seed_id="78815585780120999120",
                server_address="127.0.0.1:38283",
                connection_slot_name="SaveMgrTest",
            ).run
            with patch.object(service, "_atomic_copy_file", wraps=service._atomic_copy_file) as copy_file:
                moved_port = service.ensure_pak_managed(
                    source,
                    expected_seed_id="78815585780120999120",
                    server_address="wss://archipelago.gg:50005",
                    connection_slot_name="SaveMgrTest",
                ).run

            self.assertNotEqual(archipelago.run_id, local.run_id)
            self.assertEqual(moved_port.run_id, archipelago.run_id)
            self.assertEqual(moved_port.server_address, "wss://archipelago.gg:50005")
            self.assertEqual(moved_port.room_url, "https://archipelago.gg/room/example")
            self.assertEqual(local.connection_host, "127.0.0.1")
            self.assertEqual(archipelago.bridge_dir_name, "78815585780120999120_SaveMgrTest")
            self.assertEqual(local.bridge_dir_name, archipelago.bridge_dir_name)
            self.assertEqual(len(service.list_runs()), 2)
            copy_file.assert_not_called()

    def test_restart_ignores_legacy_connection_suffix_in_bridge_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            service = self._service(root)
            source = _write_seed_pak(
                root / "downloads",
                seed="78815585780120999120",
                player=1,
                slot="SaveMgrTest",
            )
            profile = service.import_pak(
                source,
                server_address="127.0.0.1:38283",
                connection_slot_name="SaveMgrTest",
            )
            manifest_path = profile.run_dir / "run.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["bridge_dir_name"] = "78815585780120999120_SaveMgrTest--a912aaf4eab5"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            restarted_service = self._service(root)
            restarted = restarted_service.get_run(profile.run_id)
            restarted_service.ensure_pak_managed(
                source,
                server_address="127.0.0.1:38283",
                connection_slot_name="SaveMgrTest",
            )
            normalized_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

            self.assertEqual(restarted.bridge_dir_name, "78815585780120999120_SaveMgrTest")
            self.assertEqual(normalized_manifest["bridge_dir_name"], restarted.bridge_dir_name)

    def test_identical_seed_pak_profiles_keep_separate_save_snapshots(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            service = self._service(root)
            source = _write_seed_pak(
                root / "downloads",
                seed="78815585780120999120",
                player=1,
                slot="SaveMgrTest",
            )
            archipelago = service.ensure_pak_managed(
                source,
                server_address="wss://archipelago.gg:45678",
                connection_slot_name="SaveMgrTest",
            ).run
            archipelago = service.install_managed_pak_when_none(archipelago.run_id)
            live_save = service.save_dir / "kh3sv2" / "data" / "KHIII_slot1.bin"
            live_save.parent.mkdir(parents=True, exist_ok=True)
            live_save.write_bytes(b"archipelago-progress")

            local = service.ensure_pak_managed(
                source,
                server_address="127.0.0.1:38283",
                connection_slot_name="SaveMgrTest",
            ).run
            service.activate(
                local.run_id,
                active_save_seed_id=archipelago.seed_id,
                active_save_run_id=archipelago.run_id,
            )

            self.assertFalse(live_save.exists())
            self.assertEqual(
                (archipelago.save_snapshot_dir / "kh3sv2" / "data" / "KHIII_slot1.bin").read_bytes(),
                b"archipelago-progress",
            )
            live_save.parent.mkdir(parents=True, exist_ok=True)
            live_save.write_bytes(b"local-progress")

            restored = service.activate(
                archipelago.run_id,
                active_save_seed_id=local.seed_id,
                active_save_run_id=local.run_id,
            )

            self.assertEqual(live_save.read_bytes(), b"archipelago-progress")
            self.assertEqual(
                (local.save_snapshot_dir / "kh3sv2" / "data" / "KHIII_slot1.bin").read_bytes(),
                b"local-progress",
            )
            self.assertEqual(restored.previous_run_id, local.run_id)
            self.assertTrue(service.get_run(archipelago.run_id).active)
            self.assertFalse(service.get_run(local.run_id).active)

    def test_suspend_installed_run_preserves_pak_and_current_save_before_removing_live_pak(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            service = self._service(root)
            installed = _write_seed_pak(
                service.mods_dir,
                seed="11111111111111111111",
                player=1,
                slot="Current",
            )
            current_save = service.save_dir / "kh3sv2" / "data" / "KHIII_slot1.bin"
            current_save.parent.mkdir(parents=True)
            current_save.write_bytes(b"current-run")

            result = service.suspend_installed_run(active_save_seed_id="11111111111111111111")

            self.assertFalse(installed.exists())
            self.assertFalse(result.run.active)
            self.assertFalse(result.run.installed)
            self.assertEqual(result.saved_current_files, 1)
            self.assertTrue(result.run.managed_pak_path.is_file())
            self.assertEqual(result.run.managed_pak_path.read_bytes(), b"P" * KH3_MINIMUM_SEED_PAK_SIZE_BYTES)
            self.assertEqual(
                (result.run.save_snapshot_dir / "kh3sv2" / "data" / "KHIII_slot1.bin").read_bytes(),
                b"current-run",
            )
            self.assertEqual(current_save.read_bytes(), b"current-run")
            self.assertEqual(result.save_run.run_id, result.run.run_id)

    def test_suspend_routes_mismatched_live_saves_to_their_managed_run(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            service = self._service(root)
            installed = _write_seed_pak(
                service.mods_dir,
                seed="20724977911793543445",
                player=1,
                slot="Installed",
            )
            matching = service.import_pak(_write_seed_pak(
                root / "downloads",
                seed="78815585780120999120",
                player=1,
                slot="Matching",
            ))
            live_save = service.save_dir / "kh3sv2" / "data" / "KHIII_slot1.bin"
            live_save.parent.mkdir(parents=True)
            live_save.write_bytes(b"matching-run-progress")

            result = service.suspend_installed_run(active_save_seed_id=matching.seed_id)

            self.assertFalse(installed.exists())
            self.assertEqual(result.run.seed_id, "20724977911793543445")
            self.assertEqual(result.save_run.run_id, matching.run_id)
            self.assertEqual(result.saved_current_files, 1)
            self.assertEqual(
                (matching.save_snapshot_dir / "kh3sv2" / "data" / "KHIII_slot1.bin").read_bytes(),
                b"matching-run-progress",
            )
            self.assertFalse(
                (result.run.save_snapshot_dir / "kh3sv2" / "data" / "KHIII_slot1.bin").exists()
            )
            self.assertTrue(any("matching run" in warning for warning in result.warnings))

    def test_suspend_keeps_live_pak_when_mismatched_save_run_is_not_managed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            service = self._service(root)
            installed = _write_seed_pak(
                service.mods_dir,
                seed="20724977911793543445",
                player=1,
                slot="Installed",
            )
            live_save = service.save_dir / "kh3sv2" / "data" / "KHIII_slot1.bin"
            live_save.parent.mkdir(parents=True)
            live_save.write_bytes(b"unmatched-progress")

            with self.assertRaisesRegex(KH3RunManagerError, "no matching managed run"):
                service.suspend_installed_run(active_save_seed_id="78815585780120999120")

            self.assertTrue(installed.is_file())
            self.assertEqual(live_save.read_bytes(), b"unmatched-progress")

    def test_activate_recovers_when_live_saves_match_target_but_installed_pak_is_stale(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            service = self._service(root)
            stale_installed = _write_seed_pak(
                service.mods_dir,
                seed="20724977911793543445",
                player=1,
                slot="Stale",
            )
            target = service.import_pak(_write_seed_pak(
                root / "downloads",
                seed="78815585780120999120",
                player=1,
                slot="Target",
            ))
            live_save = service.save_dir / "kh3sv2" / "data" / "KHIII_slot1.bin"
            live_save.parent.mkdir(parents=True)
            live_save.write_bytes(b"target-live-progress")

            result = service.activate(target.run_id, active_save_seed_id=target.seed_id)

            self.assertTrue(result.switched)
            self.assertFalse(stale_installed.exists())
            self.assertTrue((service.mods_dir / target.pak_name).is_file())
            self.assertEqual(live_save.read_bytes(), b"target-live-progress")
            self.assertEqual(
                (target.save_snapshot_dir / "kh3sv2" / "data" / "KHIII_slot1.bin").read_bytes(),
                b"target-live-progress",
            )
            self.assertTrue(any("matching run" in warning for warning in result.warnings))

    def test_activate_installed_target_preserves_previous_seed_saves(self) -> None:
        for resume_target in (False, True):
            with self.subTest(resume_target=resume_target), tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                service = self._service(root)
                old = service.import_pak(_write_seed_pak(
                    root / "downloads", seed="11111111111111111111", player=1, slot="Old",
                ))
                target = service.import_pak(_write_seed_pak(
                    service.mods_dir, seed="22222222222222222222", player=1, slot="New",
                ))
                self.assertTrue(service.get_run(target.run_id).active)
                relative_save = Path("kh3sv2/data/KHIII_slot1.bin")
                live_save = service.save_dir / relative_save
                live_save.parent.mkdir(parents=True)
                live_save.write_bytes(b"old-seed-progress")
                if resume_target:
                    stored = target.save_snapshot_dir / relative_save
                    stored.parent.mkdir(parents=True)
                    stored.write_bytes(b"new-seed-progress")

                result = service.activate(
                    target.run_id, active_save_seed_id=old.seed_id, active_save_run_id=old.run_id,
                )

                self.assertTrue(result.switched)
                self.assertEqual((old.save_snapshot_dir / relative_save).read_bytes(), b"old-seed-progress")
                self.assertEqual(result.restored_target_files, int(resume_target))
                if resume_target:
                    self.assertEqual(live_save.read_bytes(), b"new-seed-progress")
                else:
                    self.assertFalse(live_save.exists())
                self.assertTrue(service.get_run(target.run_id).active)

    def test_activate_restores_same_installed_run_snapshot_into_empty_live_folder(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            service = self._service(root)
            target = service.import_pak(_write_seed_pak(
                root / "downloads",
                seed="78815585780120999120",
                player=1,
                slot="Target",
            ))
            stored_save = target.save_snapshot_dir / "kh3sv2" / "data" / "KHIII_slot1.bin"
            stored_save.parent.mkdir(parents=True)
            stored_save.write_bytes(b"stored-target-progress")
            _write_seed_pak(
                service.mods_dir,
                seed=target.seed_id,
                player=target.player,
                slot=target.slot_name,
            )

            result = service.activate(
                target.run_id,
                active_save_seed_id=target.seed_id,
                active_save_run_id=target.run_id,
            )

            live_save = service.save_dir / "kh3sv2" / "data" / "KHIII_slot1.bin"
            self.assertTrue(result.switched)
            self.assertEqual(result.saved_current_files, 0)
            self.assertEqual(result.restored_target_files, 1)
            self.assertEqual(live_save.read_bytes(), b"stored-target-progress")
            self.assertEqual(stored_save.read_bytes(), b"stored-target-progress")

    def test_switch_snapshots_current_run_and_restores_each_run_independently(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            service = self._service(root)
            current_pak = _write_seed_pak(
                service.mods_dir,
                seed="11111111111111111111",
                player=1,
                slot="Current",
            )
            target_source = _write_seed_pak(
                root / "downloads",
                seed="22222222222222222222",
                player=2,
                slot="Target",
            )
            target = service.import_pak(target_source)
            current_save = service.save_dir / "kh3sv2" / "data" / "KHIII_slot1.bin"
            current_save.parent.mkdir(parents=True)
            current_save.write_bytes(b"current-run")
            safe_file = service.save_dir / "steam_autocloud.vdf"
            safe_file.write_text("safe", encoding="utf-8")

            first = service.activate(target.run_id)

            self.assertTrue(first.switched)
            self.assertEqual(first.saved_current_files, 1)
            self.assertEqual(first.restored_target_files, 0)
            self.assertFalse(current_pak.exists())
            self.assertTrue((service.mods_dir / target.pak_name).is_file())
            self.assertFalse(current_save.exists())
            self.assertEqual(safe_file.read_text(encoding="utf-8"), "safe")

            current_run = next(run for run in service.list_runs() if run.slot_name == "Current")
            self.assertTrue((current_run.save_snapshot_dir / "kh3sv2" / "data" / "KHIII_slot1.bin").is_file())
            target_live_save = service.save_dir / "kh3sv2" / "data" / "KHIII_slot2.bin"
            target_live_save.parent.mkdir(parents=True, exist_ok=True)
            target_live_save.write_bytes(b"target-run")

            second = service.activate(current_run.run_id)

            self.assertEqual(second.saved_current_files, 1)
            self.assertEqual(second.restored_target_files, 1)
            self.assertEqual(current_save.read_bytes(), b"current-run")
            self.assertFalse(target_live_save.exists())
            stored_target = service.get_run(target.run_id)
            self.assertEqual(
                (stored_target.save_snapshot_dir / "kh3sv2" / "data" / "KHIII_slot2.bin").read_bytes(),
                b"target-run",
            )
            installed = list(service.mods_dir.glob("*.pak"))
            self.assertEqual([path.name for path in installed], [current_run.pak_name])

    def test_switch_refuses_to_touch_files_while_kh3_is_running(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            service = self._service(root, running=True)
            current_pak = _write_seed_pak(
                service.mods_dir,
                seed="11111111111111111111",
                player=1,
                slot="Current",
            )
            target = service.import_pak(_write_seed_pak(
                root / "downloads",
                seed="22222222222222222222",
                player=2,
                slot="Target",
            ))
            current_save = service.save_dir / "kh3sv2" / "data" / "KHIII_slot1.bin"
            current_save.parent.mkdir(parents=True)
            current_save.write_bytes(b"current")

            with self.assertRaisesRegex(KH3RunManagerError, "Close KINGDOM HEARTS III"):
                service.activate(target.run_id)

            self.assertTrue(current_pak.is_file())
            self.assertEqual(current_save.read_bytes(), b"current")
            self.assertFalse((service.mods_dir / target.pak_name).exists())

    def test_switch_aborts_staged_work_if_kh3_starts_before_commit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            process_states = iter((False, False, True))
            service = self._service(root, running=lambda: next(process_states))
            current_pak = _write_seed_pak(
                service.mods_dir,
                seed="11111111111111111111",
                player=1,
                slot="Current",
            )
            target = service.import_pak(_write_seed_pak(
                root / "downloads",
                seed="22222222222222222222",
                player=2,
                slot="Target",
            ))
            current_save = service.save_dir / "kh3sv2" / "data" / "KHIII_slot1.bin"
            current_save.parent.mkdir(parents=True)
            current_save.write_bytes(b"current")

            with self.assertRaisesRegex(KH3RunManagerError, "started while the run switch was being prepared"):
                service.activate(target.run_id)

            self.assertTrue(current_pak.is_file())
            self.assertEqual(current_save.read_bytes(), b"current")
            self.assertFalse((service.mods_dir / target.pak_name).exists())
            temporary_save_dirs = [
                path for path in service.save_dir.parent.iterdir()
                if path.name.startswith(f".{service.save_dir.name}-kh3-run-")
            ]
            self.assertEqual(temporary_save_dirs, [])

    def test_switch_retains_previous_snapshot_if_kh3_starts_during_save_copy(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            process_states = iter((False, True))
            service = self._service(root, running=lambda: next(process_states))
            current_pak = _write_seed_pak(
                service.mods_dir,
                seed="11111111111111111111",
                player=1,
                slot="Current",
            )
            current = service.import_pak(current_pak)
            previous_snapshot = current.save_snapshot_dir / "kh3sv2" / "data" / "KHIII_slot1.bin"
            previous_snapshot.parent.mkdir(parents=True)
            previous_snapshot.write_bytes(b"previous-snapshot")
            target = service.import_pak(_write_seed_pak(
                root / "downloads",
                seed="22222222222222222222",
                player=2,
                slot="Target",
            ))
            current_save = service.save_dir / "kh3sv2" / "data" / "KHIII_slot1.bin"
            current_save.parent.mkdir(parents=True)
            current_save.write_bytes(b"current-live-save")

            with self.assertRaisesRegex(KH3RunManagerError, "started while the current run was being saved"):
                service.activate(target.run_id)

            self.assertTrue(current_pak.is_file())
            self.assertEqual(current_save.read_bytes(), b"current-live-save")
            self.assertEqual(previous_snapshot.read_bytes(), b"previous-snapshot")
            self.assertFalse((service.mods_dir / target.pak_name).exists())

    def test_switch_rolls_back_current_pak_and_save_when_target_restore_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            service = self._service(root, service_type=_FailTargetSaveInstallService)
            current_pak = _write_seed_pak(
                service.mods_dir,
                seed="11111111111111111111",
                player=1,
                slot="Current",
            )
            target = service.import_pak(_write_seed_pak(
                root / "downloads",
                seed="22222222222222222222",
                player=2,
                slot="Target",
            ))
            target_snapshot = target.save_snapshot_dir / "kh3sv2" / "data" / "KHIII_slot2.bin"
            target_snapshot.parent.mkdir(parents=True)
            target_snapshot.write_bytes(b"target")
            current_save = service.save_dir / "kh3sv2" / "data" / "KHIII_slot1.bin"
            current_save.parent.mkdir(parents=True)
            current_save.write_bytes(b"current")

            with self.assertRaisesRegex(KH3RunManagerError, "synthetic target save install failure"):
                service.activate(target.run_id)

            self.assertTrue(current_pak.is_file())
            self.assertEqual(current_save.read_bytes(), b"current")
            self.assertFalse((service.mods_dir / target.pak_name).exists())
            self.assertEqual([path.name for path in service.mods_dir.glob("*.pak")], [current_pak.name])

    def test_switch_without_active_ap_pak_refuses_unidentified_gameplay_saves(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            service = self._service(root)
            target = service.import_pak(_write_seed_pak(
                root / "downloads",
                seed="22222222222222222222",
                player=2,
                slot="Target",
            ))
            live_save = service.save_dir / "kh3sv2" / "data" / "KHIII_slot1.bin"
            live_save.parent.mkdir(parents=True)
            live_save.write_bytes(b"possibly-vanilla")
            vanilla_backup = (
                service.save_dir.parent
                / "SaveGames-vanilla-backup"
                / "kh3sv2"
                / "data"
                / "KHIII_slot4.bin"
            )
            vanilla_backup.parent.mkdir(parents=True)
            vanilla_backup.write_bytes(b"protected-vanilla")

            with self.assertRaisesRegex(KH3RunManagerError, "Restore Vanilla KH3 before"):
                service.activate(target.run_id)

            self.assertEqual(live_save.read_bytes(), b"possibly-vanilla")
            self.assertEqual(vanilla_backup.read_bytes(), b"protected-vanilla")
            self.assertFalse((service.mods_dir / target.pak_name).exists())

    def test_switch_refuses_installed_pak_that_disagrees_with_save_association(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            service = self._service(root)
            current_pak = _write_seed_pak(
                service.mods_dir,
                seed="11111111111111111111",
                player=1,
                slot="Current",
            )
            target = service.import_pak(_write_seed_pak(
                root / "downloads",
                seed="22222222222222222222",
                player=2,
                slot="Target",
            ))
            current_save = service.save_dir / "kh3sv2" / "data" / "KHIII_slot1.bin"
            current_save.parent.mkdir(parents=True)
            current_save.write_bytes(b"current")

            with self.assertRaisesRegex(KH3RunManagerError, "active saves are associated with seed"):
                service.activate(
                    target.run_id,
                    active_save_seed_id="33333333333333333333",
                )

            self.assertTrue(current_pak.is_file())
            self.assertEqual(current_save.read_bytes(), b"current")
            self.assertFalse((service.mods_dir / target.pak_name).exists())

    def test_delete_inactive_run_removes_managed_storage_and_matching_bridge_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            service = self._service(root)
            inactive = service.import_pak(_write_seed_pak(
                root / "downloads",
                seed="11111111111111111111",
                player=1,
                slot="OldRun",
            ))
            active_pak = _write_seed_pak(
                service.mods_dir,
                seed="22222222222222222222",
                player=2,
                slot="ActiveRun",
            )
            active = service.import_pak(active_pak)
            bridge_dir = service.bridge_root / "11111111111111111111_OldRun"
            bridge_dir.mkdir(parents=True)
            (bridge_dir / "state.json").write_text("{}", encoding="utf-8")
            vanilla_backup = service.save_dir.parent / "SaveGames-vanilla-backup"
            vanilla_backup.mkdir(parents=True)
            (vanilla_backup / "KHIII_slot4.bin").write_bytes(b"vanilla")

            with self.assertRaisesRegex(KH3RunManagerError, "client is still using this run's bridge"):
                service.delete_run(inactive.run_id, active_bridge_dir=bridge_dir)
            result = service.delete_run(inactive.run_id)

            self.assertTrue(result.removed_run)
            self.assertEqual(result.removed_bridge_dirs, 1)
            self.assertFalse(inactive.run_dir.exists())
            self.assertFalse(bridge_dir.exists())
            self.assertTrue(active.run_dir.exists())
            self.assertTrue(active_pak.exists())
            self.assertEqual((vanilla_backup / "KHIII_slot4.bin").read_bytes(), b"vanilla")
            with self.assertRaisesRegex(KH3RunManagerError, "can be deleted only while"):
                service.delete_run(active.run_id)

    def test_deleting_one_connection_profile_preserves_shared_pak_bridge(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            service = self._service(root)
            source = _write_seed_pak(
                root / "downloads",
                seed="78815585780120999120",
                player=1,
                slot="SaveMgrTest",
            )
            archipelago = service.import_pak(
                source,
                server_address="wss://archipelago.gg:45678",
                connection_slot_name="SaveMgrTest",
            )
            local = service.import_pak(
                source,
                server_address="127.0.0.1:38283",
                connection_slot_name="SaveMgrTest",
            )
            bridge_dir = service.bridge_root / archipelago.bridge_dir_name
            bridge_dir.mkdir(parents=True)
            (bridge_dir / "state.json").write_text("{}", encoding="utf-8")
            service.invalidate_inventory_cache()

            first = service.delete_run(archipelago.run_id, active_bridge_dir=bridge_dir)

            self.assertTrue(first.removed_run)
            self.assertEqual(first.removed_bridge_dirs, 0)
            self.assertTrue(bridge_dir.is_dir())
            self.assertTrue(service.get_run(local.run_id).managed)

            second = service.delete_run(local.run_id)

            self.assertTrue(second.removed_run)
            self.assertEqual(second.removed_bridge_dirs, 1)
            self.assertFalse(bridge_dir.exists())

    def test_delete_unmanaged_installed_conflict_removes_exact_pak_and_bridge_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            service = self._service(root)
            target_pak = _write_seed_pak(
                service.mods_dir,
                seed="45255042336741484925",
                player=1,
                slot="KH3EGSTest",
            )
            other_pak = _write_seed_pak(
                service.mods_dir,
                seed="22222222222222222222",
                player=2,
                slot="OtherRun",
            )
            target_bridge = service.bridge_root / "45255042336741484925_KH3EGSTest"
            target_bridge.mkdir(parents=True)
            (target_bridge / "state.json").write_text("{}", encoding="utf-8")
            target = next(run for run in service.list_runs() if run.seed_id == "45255042336741484925")

            result = service.delete_run(target.run_id)

            self.assertFalse(result.removed_run)
            self.assertTrue(result.removed_installed_pak)
            self.assertEqual(result.removed_bridge_dirs, 1)
            self.assertFalse(target_pak.exists())
            self.assertFalse(target_bridge.exists())
            self.assertTrue(other_pak.is_file())

    def test_delete_managed_installed_conflict_removes_storage_and_exact_live_pak(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            service = self._service(root)
            target_pak = _write_seed_pak(
                service.mods_dir,
                seed="11111111111111111111",
                player=1,
                slot="TargetConflict",
            )
            other_pak = _write_seed_pak(
                service.mods_dir,
                seed="22222222222222222222",
                player=2,
                slot="OtherConflict",
            )
            target = service.import_pak(target_pak)
            service.import_pak(other_pak)
            target = service.get_run(target.run_id)
            self.assertTrue(target.installed)
            self.assertFalse(target.active)

            result = service.delete_run(target.run_id)

            self.assertTrue(result.removed_run)
            self.assertTrue(result.removed_installed_pak)
            self.assertFalse(target_pak.exists())
            self.assertFalse(target.run_dir.exists())
            self.assertTrue(other_pak.is_file())

    def test_delete_active_disconnected_run_removes_live_ap_files_but_preserves_vanilla_backup(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            service = self._service(root)
            installed_pak = _write_seed_pak(
                service.mods_dir,
                seed="78815585780120999120",
                player=1,
                slot="SaveMgrTest",
            )
            active = service.import_pak(installed_pak)
            service._write_active_run_id(active.run_id)
            active_save = service.save_dir / "RandomizerEvents.sav"
            safe_setting = service.save_dir / "steam_autocloud.vdf"
            active_save.parent.mkdir(parents=True)
            active_save.write_bytes(b"active-ap-save")
            safe_setting.write_text("safe", encoding="utf-8")
            snapshot = active.save_snapshot_dir / "RandomizerLevelUp.sav"
            snapshot.parent.mkdir(parents=True)
            snapshot.write_bytes(b"stored-ap-save")
            bridge_dir = service.bridge_root / active.bridge_dir_name
            bridge_dir.mkdir(parents=True)
            (bridge_dir / "state.json").write_text("{}", encoding="utf-8")
            vanilla_backup = service.save_dir.parent / "SaveGames-vanilla-backup"
            vanilla_backup.mkdir(parents=True)
            (vanilla_backup / "KHIII_slot4.bin").write_bytes(b"vanilla")

            result = service.delete_run(
                active.run_id,
                active_bridge_dir=bridge_dir,
                allow_active=True,
                save_dir_override=service.save_dir,
                active_save_seed_id=active.seed_id,
                active_save_run_id=active.run_id,
            )

            self.assertTrue(result.removed_run)
            self.assertTrue(result.removed_installed_pak)
            self.assertEqual(result.removed_active_save_files, 1)
            self.assertEqual(result.removed_bridge_dirs, 1)
            self.assertTrue(result.cleared_active_profile)
            self.assertFalse(active.run_dir.exists())
            self.assertFalse(installed_pak.exists())
            self.assertFalse(active_save.exists())
            self.assertFalse(bridge_dir.exists())
            self.assertTrue(safe_setting.is_file())
            self.assertEqual((vanilla_backup / "KHIII_slot4.bin").read_bytes(), b"vanilla")
            self.assertFalse((service.run_root / ".active-run.json").exists())

    def test_delete_active_run_refuses_saves_owned_by_another_profile(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            service = self._service(root)
            installed_pak = _write_seed_pak(
                service.mods_dir,
                seed="78815585780120999120",
                player=1,
                slot="SaveMgrTest",
            )
            active = service.import_pak(installed_pak)
            active_save = service.save_dir / "RandomizerEvents.sav"
            active_save.parent.mkdir(parents=True)
            active_save.write_bytes(b"other-profile")

            with self.assertRaisesRegex(KH3RunManagerError, "active saves belong to managed run"):
                service.delete_run(
                    active.run_id,
                    allow_active=True,
                    save_dir_override=service.save_dir,
                    active_save_seed_id=active.seed_id,
                    active_save_run_id=f"{active.run_id}--another-profile",
                )

            self.assertTrue(active.run_dir.is_dir())
            self.assertTrue(installed_pak.is_file())
            self.assertEqual(active_save.read_bytes(), b"other-profile")

    def test_delete_restored_vanilla_profile_clears_marker_without_touching_live_vanilla_save(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            service = self._service(root)
            stored = service.import_pak(_write_seed_pak(
                root / "downloads",
                seed="78815585780120999120",
                player=1,
                slot="SaveMgrTest",
            ))
            service._write_active_run_id(stored.run_id)
            live_vanilla_save = service.save_dir / "KHIII_slot4.bin"
            live_vanilla_save.parent.mkdir(parents=True)
            live_vanilla_save.write_bytes(b"restored-vanilla")

            result = service.delete_run(stored.run_id, allow_active=True)

            self.assertTrue(result.removed_run)
            self.assertFalse(result.removed_installed_pak)
            self.assertEqual(result.removed_active_save_files, 0)
            self.assertTrue(result.cleared_active_profile)
            self.assertEqual(live_vanilla_save.read_bytes(), b"restored-vanilla")
            self.assertFalse((service.run_root / ".active-run.json").exists())

    def test_delete_active_run_rolls_back_live_files_when_staging_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            service = self._service(root)
            installed_pak = _write_seed_pak(
                service.mods_dir,
                seed="78815585780120999120",
                player=1,
                slot="SaveMgrTest",
            )
            active = service.import_pak(installed_pak)
            active_save = service.save_dir / "RandomizerEvents.sav"
            active_save.parent.mkdir(parents=True)
            active_save.write_bytes(b"restore-me")
            real_replace = os.replace

            def fail_run_directory_stage(source: Path | str, destination: Path | str) -> None:
                if Path(source) == active.run_dir:
                    raise OSError("synthetic run staging failure")
                real_replace(source, destination)

            with patch(
                "worlds.kh3.client_support.run_manager.os.replace",
                side_effect=fail_run_directory_stage,
            ), self.assertRaisesRegex(KH3RunManagerError, "synthetic run staging failure"):
                service.delete_run(
                    active.run_id,
                    allow_active=True,
                    save_dir_override=service.save_dir,
                    active_save_seed_id=active.seed_id,
                    active_save_run_id=active.run_id,
                )

            self.assertTrue(active.run_dir.is_dir())
            self.assertTrue(installed_pak.is_file())
            self.assertEqual(active_save.read_bytes(), b"restore-me")

    def test_delete_aborts_if_kh3_starts_before_files_are_staged(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            process_states = iter((False, True))
            service = self._service(root, running=lambda: next(process_states))
            inactive = service.import_pak(_write_seed_pak(
                service.mods_dir,
                seed="11111111111111111111",
                player=1,
                slot="OldRun",
            ))
            bridge_dir = service.bridge_root / "11111111111111111111_OldRun"
            bridge_dir.mkdir(parents=True)
            (bridge_dir / "state.json").write_text("{}", encoding="utf-8")

            with self.assertRaisesRegex(KH3RunManagerError, "started while deletion was being prepared"):
                service.delete_run(inactive.run_id, allow_active=True)

            self.assertTrue(inactive.run_dir.is_dir())
            self.assertTrue(bridge_dir.is_dir())

    def test_import_rejects_non_generated_or_undersized_paks(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            service = self._service(root)
            invalid_name = root / "downloads" / "seed.pak"
            invalid_name.parent.mkdir(parents=True)
            invalid_name.write_bytes(b"large enough" * 1000)
            undersized = root / "downloads" / "pakchunk9-Sora-randomizer-AP_11111111111111111111-1_99_P.pak"
            undersized.write_bytes(b"small")

            with self.assertRaisesRegex(KH3RunManagerError, "Select a generated KH3 AP player pak"):
                service.import_pak(invalid_name)
            with self.assertRaisesRegex(KH3RunManagerError, "expected at least"):
                service.import_pak(undersized)


class KH3RunManagerContextTest(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _run(root: Path) -> KH3RunInfo:
        return KH3RunInfo(
            run_id="22222222222222222222-2-Target",
            seed_id="22222222222222222222",
            player=2,
            slot_name="Target",
            pak_name="pakchunk9-Target-randomizer-AP_22222222222222222222-2_99_P.pak",
            managed=True,
            active=True,
            installed=True,
            pak_available=True,
            has_save=True,
            run_dir=root / "run",
            managed_pak_path=root / "run" / "pak" / "seed.pak",
            installed_pak_path=root / "mods" / "seed.pak",
            save_snapshot_dir=root / "run" / "save",
            bridge_dirs=(root / "bridge" / "target",),
        )

    def _context(self, root: Path, result: KH3RunActivationResult) -> tuple[KH3Context, Mock]:
        ctx = KH3Context.__new__(KH3Context)
        ctx.kh3_run_action_running = False
        ctx.kh3_run_action_label = ""
        ctx.bridge_dir = root / "bridge" / "current"
        ctx.bridge_save_dir = root / "configured" / "SaveGames"
        presenter = Mock()
        presenter.active_runtime_save_directory.return_value = (root / "active" / "SaveGames", "runtime")
        ctx._get_client_status_presenter = Mock(return_value=presenter)
        ctx.save_seed_association_for_dir = Mock(return_value=("11111111111111111111", 2))
        ctx.disconnect = AsyncMock()
        ctx.wait_for_integrated_bridge_shutdown = AsyncMock()
        service = Mock()
        service.activate.return_value = result
        ctx._get_run_manager_service = Mock(return_value=service)
        ctx._apply_kh3_run_activation = Mock()
        ctx.try_initialize_offline_bridge_from_installed_pak = Mock(return_value=True)
        ctx.start_integrated_bridge = Mock()
        return ctx, service

    async def test_context_switch_disconnects_and_stops_bridge_before_file_activation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            run = self._run(root)
            result = KH3RunActivationResult(run, "old", True, 3, 4, root / "active" / "SaveGames")
            ctx, service = self._context(root, result)
            status_updates: list[str] = []
            ctx._set_kh3_run_action_status = Mock(side_effect=status_updates.append)

            with patch("worlds.kh3.Client.is_process_running", return_value=False):
                await ctx._activate_kh3_run_action(run.run_id)

            ctx.disconnect.assert_awaited_once_with()
            ctx.wait_for_integrated_bridge_shutdown.assert_awaited_once_with()
            service.activate.assert_called_once_with(
                run.run_id,
                save_dir_override=(root / "active" / "SaveGames").resolve(),
                active_save_seed_id="11111111111111111111",
            )
            ctx._apply_kh3_run_activation.assert_called_once_with(
                result,
                (root / "active" / "SaveGames").resolve(),
            )
            ctx.try_initialize_offline_bridge_from_installed_pak.assert_called_once_with()
            self.assertEqual(status_updates, [
                "Switching KH3 run...",
                "Stopping the current KH3 save watcher...",
                "Saving the current run and loading the selected saves...",
                "Initializing the selected KH3 run...",
            ])
            self.assertTrue(ctx.disconnected_intentionally)
            self.assertFalse(ctx.kh3_run_action_running)

    async def test_context_switch_preserves_files_when_save_watcher_does_not_stop(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            run = self._run(root)
            result = KH3RunActivationResult(run, None, True, 0, 0, root / "active" / "SaveGames")
            ctx, service = self._context(root, result)
            previous_bridge = ctx.bridge_dir
            ctx.wait_for_integrated_bridge_shutdown.side_effect = RuntimeError("The save watcher is still running")
            with patch("worlds.kh3.Client.is_process_running", return_value=False):
                await ctx._activate_kh3_run_action(run.run_id)
            service.activate.assert_not_called()
            ctx._apply_kh3_run_activation.assert_not_called()
            ctx.start_integrated_bridge.assert_not_called()
            self.assertEqual(ctx.bridge_dir, previous_bridge)
            self.assertFalse(ctx.kh3_run_action_running)

    async def test_context_switch_hard_blocks_before_disconnect_while_kh3_runs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            run = self._run(root)
            result = KH3RunActivationResult(run, None, True, 0, 0, root / "active" / "SaveGames")
            ctx, service = self._context(root, result)

            with patch("worlds.kh3.Client.is_process_running", return_value=True):
                await ctx._activate_kh3_run_action(run.run_id)

            ctx.disconnect.assert_not_awaited()
            ctx.wait_for_integrated_bridge_shutdown.assert_not_awaited()
            service.activate.assert_not_called()
            self.assertFalse(ctx.kh3_run_action_running)

    async def test_context_deletes_active_run_only_after_disconnected_bridge_shutdown(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            run = self._run(root)
            save_dir = (root / "active" / "SaveGames").resolve()
            ctx = KH3Context.__new__(KH3Context)
            ctx.kh3_run_action_running = False
            ctx.kh3_run_action_label = ""
            ctx.bridge_dir = run.bridge_dirs[0]
            ctx.bridge_save_dir = root / "configured" / "SaveGames"
            ctx.kh3_active_connection_profile_run_id = run.run_id
            ctx._is_ap_connected = Mock(return_value=False)
            ctx.stop_integrated_bridge = Mock()
            ctx.wait_for_integrated_bridge_shutdown = AsyncMock()
            ctx.start_integrated_bridge = Mock()
            presenter = Mock()
            presenter.active_runtime_save_directory.return_value = (save_dir, "runtime")
            ctx._get_client_status_presenter = Mock(return_value=presenter)
            ctx.save_seed_association_for_dir = Mock(return_value=(run.seed_id, 2))
            ctx.save_run_association_for_dir = Mock(return_value=run.run_id)
            ctx.clear_save_seed_associations_for_targets = Mock()
            ctx._apply_deleted_active_kh3_run = Mock()
            service = Mock()
            service.get_run.return_value = run
            service.delete_run.return_value = KH3RunDeleteResult(
                run_id=run.run_id,
                removed_run=True,
                removed_bridge_dirs=1,
                removed_installed_pak=True,
                removed_active_save_files=2,
                cleared_active_profile=True,
            )
            ctx._get_run_manager_service = Mock(return_value=service)

            with patch("worlds.kh3.Client.is_process_running", return_value=False):
                await ctx._delete_kh3_run_action(run.run_id)

            ctx.stop_integrated_bridge.assert_called_once_with()
            ctx.wait_for_integrated_bridge_shutdown.assert_awaited_once_with()
            service.delete_run.assert_called_once_with(
                run.run_id,
                active_bridge_dir=run.bridge_dirs[0],
                allow_active=True,
                save_dir_override=save_dir,
                active_save_seed_id=run.seed_id,
                active_save_run_id=run.run_id,
            )
            ctx.clear_save_seed_associations_for_targets.assert_called_once_with(
                [save_dir],
                expected_run_id=run.run_id,
            )
            ctx._apply_deleted_active_kh3_run.assert_called_once_with()
            ctx.start_integrated_bridge.assert_not_called()

    async def test_context_keeps_shared_bridge_running_when_deleting_inactive_profile(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            run = replace(self._run(root), active=False, installed=False)
            ctx = KH3Context.__new__(KH3Context)
            ctx.kh3_run_action_running = False
            ctx.kh3_run_action_label = ""
            ctx.bridge_dir = run.bridge_dirs[0]
            ctx.bridge_save_dir = root / "configured" / "SaveGames"
            ctx.kh3_active_connection_profile_run_id = "another-profile-for-the-same-pak"
            ctx._is_ap_connected = Mock(return_value=False)
            ctx.stop_integrated_bridge = Mock()
            ctx.wait_for_integrated_bridge_shutdown = AsyncMock()
            ctx.start_integrated_bridge = Mock()
            ctx._apply_deleted_active_kh3_run = Mock()
            service = Mock()
            service.get_run.return_value = run
            service.delete_run.return_value = KH3RunDeleteResult(
                run_id=run.run_id,
                removed_run=True,
                removed_bridge_dirs=0,
                removed_installed_pak=False,
                removed_active_save_files=0,
                cleared_active_profile=False,
            )
            ctx._get_run_manager_service = Mock(return_value=service)

            with patch("worlds.kh3.Client.is_process_running", return_value=False):
                await ctx._delete_kh3_run_action(run.run_id)

            ctx.stop_integrated_bridge.assert_not_called()
            ctx.wait_for_integrated_bridge_shutdown.assert_not_awaited()
            service.delete_run.assert_called_once_with(
                run.run_id,
                active_bridge_dir=run.bridge_dirs[0],
                allow_active=False,
                save_dir_override=None,
                active_save_seed_id=None,
                active_save_run_id=None,
            )
            ctx._apply_deleted_active_kh3_run.assert_not_called()

    async def test_context_refuses_active_run_deletion_while_connected(self) -> None:
        ctx = KH3Context.__new__(KH3Context)
        ctx._is_ap_connected = Mock(return_value=True)
        ctx._begin_kh3_run_action = Mock()

        service = Mock()
        service.list_runs.return_value = [SimpleNamespace(run_id="active-run", active=True)]
        ctx._get_run_manager_service = Mock(return_value=service)
        await ctx._delete_kh3_run_action("active-run")

        ctx._begin_kh3_run_action.assert_not_called()

    async def test_context_auto_enrolls_connected_pak_and_remembers_server_without_changing_saves(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            run = self._run(root)
            enrollment = KH3RunEnrollmentResult(replace(
                run,
                server_address="127.0.0.1:38282",
                connection_slot_name="Target",
                room_url="https://ap.example.test/room/room-id",
            ), True)
            ctx = KH3Context.__new__(KH3Context)
            ctx.kh3_run_action_running = False
            ctx.kh3_run_action_label = ""
            service = Mock()
            service.ensure_pak_managed.return_value = enrollment
            ctx._get_run_manager_service = Mock(return_value=service)
            pak_path = root / "mods" / run.pak_name

            await ctx._auto_enroll_connected_kh3_run_action(
                pak_path,
                run.seed_id,
                "127.0.0.1:38282",
                "https://ap.example.test/room/room-id",
            )

            service.ensure_pak_managed.assert_called_once_with(
                pak_path,
                expected_seed_id=run.seed_id,
                server_address="127.0.0.1:38282",
                connection_slot_name="Target",
                room_url="https://ap.example.test/room/room-id",
            )
            self.assertFalse(ctx.kh3_run_action_running)

    def test_context_applies_remembered_server_to_connection_state_and_gui(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            run = replace(self._run(root), server_address="127.0.0.1:38282")
            ctx = KH3Context.__new__(KH3Context)
            ctx.server_address = "old.example:12345"
            ctx.username = "old user"
            ctx.password = "old password"
            ctx.ui = Mock()

            ctx._apply_kh3_run_server_address(run)

            self.assertEqual(ctx.server_address, "127.0.0.1:38282")
            self.assertIsNone(ctx.username)
            self.assertIsNone(ctx.password)
            ctx.ui.update_address_bar.assert_called_once_with("127.0.0.1:38282")

    def test_context_loads_the_active_runs_edited_slot_and_server_profile(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            run = replace(
                self._run(root),
                connection_slot_name="Room Slot Name",
                server_address="wss://archipelago.gg:45678",
            )
            ctx = KH3Context.__new__(KH3Context)
            ctx.server = None
            ctx.username = "old-user"
            ctx.password = "old-password"
            ctx.ui = Mock()
            service = Mock()
            service.list_runs.return_value = [run]
            ctx._get_run_manager_service = Mock(return_value=service)

            self.assertEqual(ctx.list_kh3_runs(), [run])

            self.assertEqual(ctx.kh3_active_connection_profile_run_id, run.run_id)
            self.assertEqual(ctx.kh3_run_connection_slot_name, "Room Slot Name")
            self.assertEqual(ctx.auth, "Room Slot Name")
            self.assertEqual(ctx.server_address, "wss://archipelago.gg:45678")
            ctx.ui.update_address_bar.assert_called_once_with("wss://archipelago.gg:45678")

    def test_context_connection_preparation_prefers_edited_slot_over_pak_inference(self) -> None:
        ctx = KH3Context.__new__(KH3Context)
        ctx.auth = "PakIdentity"
        ctx.kh3_run_connection_slot_name = "Room Slot Name"
        ctx.seed_name = "old-seed"
        ctx.kh3_check_reporting_enabled = True
        ctx.locations_checked = {1}
        ctx.locations_scouted = {2}
        ctx.goal_status_sent = True
        ctx.last_processed_game_state_seq = 9
        ctx.last_processed_game_state_file_signature = (1, 2)
        ctx.save_seed_mismatch_active = True
        ctx.last_save_seed_status_warning = "old warning"
        ctx.infer_slot_name_from_installed_pak = Mock(return_value="PakIdentity")
        transport = Mock()
        transport.plan_connection_preparation.side_effect = ClientTransportService.plan_connection_preparation
        ctx._get_client_transport_service = Mock(return_value=transport)

        ctx.prepare_for_new_server_connection()

        transport.plan_connection_preparation.assert_called_once_with(ConnectionPreparationInput(
            auth="Room Slot Name",
            inferred_slot_name=None,
        ))
        ctx.infer_slot_name_from_installed_pak.assert_not_called()
        self.assertEqual(ctx.auth, "Room Slot Name")
        self.assertFalse(ctx.kh3_auth_inferred_from_installed_pak)

    async def test_context_edits_active_run_profile_and_applies_it_while_disconnected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            updated = replace(
                self._run(root),
                connection_slot_name="Room Slot Name",
                server_address="wss://archipelago.gg:45678",
            )
            ctx = KH3Context.__new__(KH3Context)
            ctx.kh3_run_action_running = False
            ctx.kh3_run_action_label = ""
            ctx.server = None
            ctx.username = "old-user"
            ctx.password = "old-password"
            ctx.ui = Mock()
            service = Mock()
            service.update_connection_profile.return_value = updated
            ctx._get_run_manager_service = Mock(return_value=service)

            await ctx._update_kh3_run_connection_profile_action(
                updated.run_id,
                slot_name="Room Slot Name",
                server="wss://archipelago.gg",
                port="45678",
                room_url="https://archipelago.gg/room/example-room",
            )

            service.update_connection_profile.assert_called_once_with(
                updated.run_id,
                slot_name="Room Slot Name",
                server="wss://archipelago.gg",
                port="45678",
                room_url="https://archipelago.gg/room/example-room",
            )
            self.assertEqual(ctx.auth, "Room Slot Name")
            self.assertEqual(ctx.server_address, "wss://archipelago.gg:45678")
            self.assertFalse(ctx.kh3_run_action_running)

    async def test_context_edits_inactive_run_without_global_activity_cycle(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            updated = replace(
                self._run(root),
                active=False,
                installed=False,
                connection_slot_name="Other Room Slot",
                server_address="127.0.0.1:38282",
            )
            ctx = KH3Context.__new__(KH3Context)
            ctx.kh3_run_action_running = False
            ctx.kh3_run_action_label = ""
            ctx._begin_kh3_run_action = Mock()
            ctx._finish_kh3_run_action = Mock()
            service = Mock()
            service.update_connection_profile.return_value = updated
            ctx._get_run_manager_service = Mock(return_value=service)

            await ctx._update_kh3_run_connection_profile_action(
                updated.run_id,
                slot_name="Other Room Slot",
                server="127.0.0.1",
                port="38282",
                room_url="https://example.test/room/other-room",
                show_activity=False,
            )

            ctx._begin_kh3_run_action.assert_not_called()
            ctx._finish_kh3_run_action.assert_not_called()
            service.update_connection_profile.assert_called_once_with(
                updated.run_id,
                slot_name="Other Room Slot",
                server="127.0.0.1",
                port="38282",
                room_url="https://example.test/room/other-room",
            )
            self.assertFalse(ctx.kh3_run_action_running)

    def test_context_normalizes_connected_server_to_host_and_explicit_port(self) -> None:
        self.assertEqual(
            KH3Context.normalized_kh3_run_server_address("ws://127.0.0.1:38282"),
            "127.0.0.1:38282",
        )
        self.assertEqual(
            KH3Context.normalized_kh3_run_server_address("wss://example.net"),
            "wss://example.net:38281",
        )
        self.assertEqual(
            KH3Context.normalized_kh3_run_server_address("ws://[::1]:38282"),
            "[::1]:38282",
        )
        self.assertIsNone(KH3Context.normalized_kh3_run_server_address(""))

    async def test_context_prepares_missing_container_before_connecting(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            run = self._run(root)
            preparation = KH3SeedPreparationResult(
                "installed",
                source_path=root / "mods" / "Player.apkh3",
                installed_run=run,
                generated_paks=(root / "generated" / run.pak_name,),
            )
            events: list[str] = []
            ctx = KH3Context.__new__(KH3Context)

            async def disconnect() -> None:
                events.append("disconnect")

            async def prepare(*, trigger: str) -> KH3SeedPreparationResult:
                self.assertEqual(trigger, "connecting")
                events.append("prepare")
                return preparation

            ctx.disconnect = disconnect
            ctx._prepare_missing_seed_pak = prepare
            ctx.guard_installed_seed_pak_before_connect = Mock(side_effect=lambda: events.append("guard") or True)
            ctx.prepare_for_new_server_connection = Mock(side_effect=lambda: events.append("client preparation"))
            lifecycle = Mock()
            lifecycle.start_server.side_effect = lambda *_args, **_kwargs: events.append("server")
            ctx._get_lifecycle_service = Mock(return_value=lifecycle)

            await ctx.connect("localhost:38282")

            self.assertEqual(events, ["disconnect", "prepare", "guard", "client preparation", "server"])
            lifecycle.start_server.assert_called_once_with("localhost:38282", task_name="server loop")

    async def test_context_does_not_start_connection_when_steam_cloud_confirmation_is_dismissed(self) -> None:
        ctx = KH3Context.__new__(KH3Context)
        ctx.confirm_steam_cloud_disabled_before_connect = AsyncMock(return_value=False)
        ctx.disconnect = AsyncMock()
        ctx._prepare_missing_seed_pak = AsyncMock()

        await ctx.connect("localhost:38282")

        ctx.confirm_steam_cloud_disabled_before_connect.assert_awaited_once_with()
        ctx.disconnect.assert_not_awaited()
        ctx._prepare_missing_seed_pak.assert_not_awaited()

    async def test_generated_pak_launch_runs_existing_save_guard_before_launch(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            run = self._run(root)
            preparation = KH3SeedPreparationResult(
                "installed",
                source_path=root / "mods" / "Player.apkh3",
                installed_run=run,
            )
            ctx = KH3Context.__new__(KH3Context)
            ctx._prepare_missing_seed_pak = AsyncMock(return_value=preparation)
            ctx.refresh_save_seed_guard = Mock(return_value=False)
            ctx.build_save_seed_status = Mock(return_value="mismatch")
            ctx._launch_kh3_game_now = Mock()

            await ctx._prepare_and_launch_kh3_game()

            ctx.refresh_save_seed_guard.assert_called_once_with()
            ctx._launch_kh3_game_now.assert_not_called()

            ctx.refresh_save_seed_guard.return_value = True
            await ctx._prepare_and_launch_kh3_game()

            self.assertEqual(ctx._launch_kh3_game_now.call_count, 1)

    def test_connected_package_requests_run_enrollment_only_after_seed_guards_pass(self) -> None:
        ctx = KH3Context.__new__(KH3Context)
        ctx.bridge_dir = None
        ctx.items_received = [object()]
        ctx.kh3_check_reporting_enabled = False
        ctx.locations_checked = set()
        ctx.locations_scouted = set()
        ctx.goal_status_sent = False
        ctx.last_deathlink_game_over_key = ""
        ctx.received_deathlink_game_over_request_id = None
        ctx._get_secret_report_hint_service = Mock(return_value=Mock())
        ctx.update_death_link = AsyncMock()
        ctx.initialize_bridge = Mock()
        ctx.log_location_check_transport_event = Mock()
        ctx.write_bridge_files = Mock()
        ctx.configure_probe_for_current_bridge = Mock()
        ctx.warn_if_installed_pak_mismatch = Mock()
        ctx.is_installed_pak_compatible = Mock(return_value=True)
        ctx.refresh_save_seed_guard = Mock(return_value=True)
        ctx.request_auto_enroll_connected_kh3_run = Mock()
        ctx.start_integrated_bridge = Mock()

        def close_coroutine(coroutine, *, name=None) -> None:
            del name
            coroutine.close()

        with patch("worlds.kh3.Client.Utils.async_start", side_effect=close_coroutine), patch(
            "worlds.kh3.Client.deathlink_settings.initialize_settings", return_value=(False, 0),
        ):
            ctx.on_package("Connected", {"slot_data": empty_wire_slot_data()})

        self.assertEqual(ctx.items_received, [])

        ctx.request_auto_enroll_connected_kh3_run.assert_called_once_with()
        self.assertTrue(ctx.kh3_check_reporting_enabled)
        ctx.start_integrated_bridge.assert_called_once_with()

    def test_connected_seed_pak_selection_requires_one_matching_installed_pak(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            pak_path = Path(temp_dir) / "pakchunk9-Sora-randomizer-AP_11111111111111111111-2_99_P.pak"
            ctx = KH3Context.__new__(KH3Context)
            ctx.expected_seed_id = Mock(return_value="11111111111111111111")
            ctx.installed_kh3_ap_seed_paks = Mock(return_value=[{
                "seed": "11111111111111111111",
                "path": str(pak_path),
            }])

            self.assertEqual(
                ctx.connected_kh3_seed_pak_path(),
                (pak_path.resolve(), "11111111111111111111"),
            )

            ctx.installed_kh3_ap_seed_paks.return_value = [{
                "seed": "22222222222222222222",
                "path": str(pak_path),
            }]
            self.assertIsNone(ctx.connected_kh3_seed_pak_path())


if __name__ == "__main__":
    unittest.main()
