from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ..Bridge import BRIDGE_PROTOCOL_NAME, BRIDGE_PROTOCOL_VERSION
from ..client_support.run_manager import KH3RunManagerService
from ..client_support.seed_import_service import KH3SeedImportError, KH3SeedImportService
from ..client_support.seed_paks import extract_kh3_ap_seed_metadata_from_pak
from ..client_support.seed_policy_service import KH3_MINIMUM_SEED_PAK_SIZE_BYTES
from ..Locations import BASE_LOCATION_ID
from ..PakGenerator import KH3PakResult
from ..SlotData import decode_slot_data


def _write_generated_pak(
    output_dir: Path,
    *,
    seed: str,
    player: int,
    slot_name: str,
) -> Path:
    path = output_dir / f"pakchunk9-{slot_name}-randomizer-AP_{seed}-{player}_99_P.pak"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bytes([player]) * KH3_MINIMUM_SEED_PAK_SIZE_BYTES)
    return path


class KH3SeedImportServiceTest(unittest.TestCase):
    def _run_manager(self, root: Path, *, running=False) -> KH3RunManagerService:
        return KH3RunManagerService(
            run_root=root / "user" / "kh3" / "runs",
            bridge_root=root / "user" / "kh3" / "bridge",
            mods_dir=root / "game" / "~mods",
            save_dir=root / "saves" / "SaveGames",
            process_running=(running if callable(running) else lambda: bool(running)),
            now_utc=lambda: "2026-08-08T12:00:00Z",
        )

    def _service(
        self,
        root: Path,
        *,
        manager: KH3RunManagerService | None = None,
        running=False,
        archive_calls: list[int | None] | None = None,
        container_writer=None,
        seed_metadata_loader=None,
        unidentified_save_preparer=None,
    ) -> KH3SeedImportService:
        manager = manager or self._run_manager(root, running=running)

        def generate_container(_source: Path, output_dir: Path) -> KH3PakResult:
            pak = _write_generated_pak(
                output_dir,
                seed="11111111111111111111",
                player=1,
                slot_name="Sora",
            )
            return KH3PakResult(1, "Sora", pak, None)

        def generate_archive(
            _source: Path,
            output_dir: Path,
            *,
            requested_slot: int | None = None,
        ) -> list[KH3PakResult]:
            if archive_calls is not None:
                archive_calls.append(requested_slot)
            players = ((1, "Sora"), (2, "Riku"))
            results: list[KH3PakResult] = []
            for player, slot_name in players:
                if requested_slot is not None and player != requested_slot:
                    continue
                pak = _write_generated_pak(
                    output_dir,
                    seed="22222222222222222222",
                    player=player,
                    slot_name=slot_name,
                )
                results.append(KH3PakResult(player, slot_name, pak, None))
            return results

        def iter_slots(_multidata, requested_slot: int | None = None):
            slots = [
                {"slot": 1, "name": "Sora"},
                {"slot": 2, "name": "Riku"},
            ]
            return [slot for slot in slots if requested_slot is None or slot["slot"] == requested_slot]

        return KH3SeedImportService(
            run_manager=manager,
            mods_dir=manager.mods_dir,
            generated_root=root / "user" / "kh3" / "generated-paks",
            process_running=(running if callable(running) else lambda: bool(running)),
            container_generator=generate_container,
            archive_generator=generate_archive,
            archive_loader=lambda _path: {},
            slot_iterator=iter_slots,
            container_writer=container_writer,
            seed_metadata_loader=seed_metadata_loader,
            unidentified_save_preparer=unidentified_save_preparer,
        )

    def test_embedded_metadata_loader_defaults_to_all_platform_extractor(self) -> None:
        defaults = KH3SeedImportService.__init__.__kwdefaults__ or {}
        self.assertIs(defaults.get("seed_metadata_loader"), extract_kh3_ap_seed_metadata_from_pak)

    def test_import_apkh3_generates_and_manages_pak_without_installing_it(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "downloads" / "Sora.apkh3"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"container")
            manager = self._run_manager(root)
            service = self._service(root, manager=manager)

            result = service.import_source(source)

            self.assertEqual([run.slot_name for run in result.runs], ["Sora"])
            self.assertEqual(len(result.generated_paks), 1)
            self.assertTrue(result.generated_paks[0].is_file())
            self.assertRegex(result.generated_paks[0].parent.name, r"^[0-9a-f]{12}$")
            self.assertNotIn("Sora", result.generated_paks[0].parent.name)
            self.assertTrue(result.runs[0].managed_pak_path.is_file())
            self.assertEqual(list(manager.mods_dir.glob("*.pak")), [])
            self.assertTrue(source.is_file())
            self.assertIsNone(result.archived_source_path)

    def test_generated_apkh3_and_zip_cache_embedded_metadata_for_each_run(self) -> None:
        for source_name in ("Sora.apkh3", "AP_Test.zip"):
            with self.subTest(source_name=source_name), tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                manager = self._run_manager(root)
                source = root / "downloads" / source_name
                source.parent.mkdir(parents=True)
                source.write_bytes(b"seed source")
                loader_calls: list[Path] = []

                def load_metadata(pak_path: Path) -> dict:
                    loader_calls.append(pak_path)
                    identity = manager.pak_identity(pak_path)
                    return {
                        "format": "kh3_ap_seed_metadata",
                        "seed": f"AP_{identity.seed_id}-{identity.player}-{identity.slot_name}",
                        "slot": identity.player,
                        "slot_name": identity.slot_name,
                        "slot_data": {"bridge": {"locations": []}},
                    }

                service = self._service(
                    root,
                    manager=manager,
                    seed_metadata_loader=load_metadata,
                )
                progress: list[str] = []

                result = service.import_source(source, progress=progress.append)

                self.assertEqual(len(loader_calls), len(result.runs))
                for run in result.runs:
                    self.assertEqual(len(run.bridge_dirs), 1)
                    metadata_path = run.bridge_dirs[0] / "kh3_ap_seed_metadata.json"
                    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                    self.assertEqual(metadata["slot_name"], run.slot_name)
                    self.assertEqual(metadata["slot"], run.player)
                self.assertTrue(any("Reading embedded seed metadata" in message for message in progress))

    def test_raw_pak_import_caches_embedded_metadata_for_activation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manager = self._run_manager(root)
            source = _write_generated_pak(
                root / "downloads",
                seed="11111111111111111111",
                player=1,
                slot_name="Sora",
            )
            metadata = {
                "format": "kh3_ap_seed_metadata",
                "seed": "AP_11111111111111111111-1-Sora",
                "slot": 1,
                "slot_name": "Sora",
                "slot_data": {"bridge": {"locations": []}},
            }
            service = self._service(
                root,
                manager=manager,
                seed_metadata_loader=lambda _path: metadata,
            )

            result = service.import_source(source)

            metadata_path = result.runs[0].bridge_dirs[0] / "kh3_ap_seed_metadata.json"
            self.assertEqual(json.loads(metadata_path.read_text(encoding="utf-8")), metadata)

    def test_installed_pak_import_stores_connection_profile_and_becomes_active(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manager = self._run_manager(root)
            source = _write_generated_pak(
                manager.mods_dir,
                seed="45255042336741484925",
                player=1,
                slot_name="KH3EGSTest",
            )
            service = self._service(root, manager=manager)

            result = service.import_source(
                source,
                server_address="127.0.0.1:38282",
                connection_slot_name="KH3EGSTest",
                room_url="https://example.test/room/kh3-egs-test",
            )

            run = result.runs[0]
            self.assertTrue(source.is_file())
            self.assertTrue(run.managed)
            self.assertTrue(run.active)
            self.assertEqual(run.server_address, "127.0.0.1:38282")
            self.assertEqual(run.connection_slot_name, "KH3EGSTest")
            self.assertEqual(run.room_url, "https://example.test/room/kh3-egs-test")
            self.assertTrue(run.managed_pak_path.is_file())

    def test_import_container_directly_from_mods_archives_source_after_managed_copy(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manager = self._run_manager(root)
            source = manager.mods_dir / "Sora.apkh3"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"container")
            service = self._service(root, manager=manager)
            progress: list[str] = []

            result = service.import_source(source, progress=progress.append)

            self.assertFalse(source.exists())
            self.assertIsNotNone(result.archived_source_path)
            self.assertTrue(result.archived_source_path.is_file())
            self.assertEqual(result.archived_source_path.read_bytes(), b"container")
            self.assertTrue(any("take about a minute" in message for message in progress))
            self.assertTrue(any("Moving the processed seed source" in message for message in progress))

    def test_import_generated_zip_manages_every_kh3_player_without_installing_them(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "downloads" / "AP_Test.zip"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"archive")
            manager = self._run_manager(root)
            service = self._service(root, manager=manager)

            result = service.import_source(source)

            self.assertEqual([run.slot_name for run in result.runs], ["Sora", "Riku"])
            self.assertEqual(len(result.generated_paks), 2)
            self.assertEqual(list(manager.mods_dir.glob("*.pak")), [])

    def test_multiplayer_profile_import_rejects_before_expensive_generation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "AP_Test.zip"
            source.write_bytes(b"synthetic archive")
            calls: list[int | None] = []
            service = self._service(root, archive_calls=calls)
            with self.assertRaisesRegex(KH3SeedImportError, "one KH3 player"):
                service.import_source(source, server_address="localhost:38281", connection_slot_name="Sora")
            self.assertEqual(calls, [])
            self.assertTrue(source.is_file())
            self.assertEqual(service.run_manager.list_runs(), [])

    def test_prepare_apkh3_from_mods_generates_manages_and_installs_one_pak(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manager = self._run_manager(root)
            source = manager.mods_dir / "Sora.apkh3"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"container")
            service = self._service(root, manager=manager)

            progress: list[str] = []
            result = service.prepare_missing_installed_pak(progress=progress.append)

            self.assertEqual(result.status, "installed")
            self.assertEqual(result.installed_run.slot_name, "Sora")
            self.assertFalse(source.exists())
            self.assertIsNotNone(result.archived_source_path)
            self.assertTrue(result.archived_source_path.is_file())
            installed = list(manager.mods_dir.glob("*.pak"))
            self.assertEqual([path.name for path in installed], [result.installed_run.pak_name])
            self.assertTrue(manager.get_run(result.installed_run.run_id).active)
            self.assertTrue(any("Installing the generated KH3 seed pak" in message for message in progress))
            self.assertTrue(any("Moving the processed seed source" in message for message in progress))

    def test_prepare_multiplayer_zip_requires_or_uses_matching_slot_name(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manager = self._run_manager(root)
            source = manager.mods_dir / "AP_Test.zip"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"archive")
            archive_calls: list[int | None] = []
            service = self._service(root, manager=manager, archive_calls=archive_calls)

            with self.assertRaisesRegex(KH3SeedImportError, "contains multiple KH3 players"):
                service.prepare_missing_installed_pak()
            self.assertTrue(source.is_file())

            result = service.prepare_missing_installed_pak(preferred_slot_name="rIkU")

            self.assertEqual(archive_calls, [2])
            self.assertEqual(result.installed_run.slot_name, "Riku")
            self.assertEqual([path.name for path in manager.mods_dir.glob("*.pak")], [result.installed_run.pak_name])
            self.assertFalse(source.exists())
            self.assertTrue(result.archived_source_path.is_file())

    def test_source_archive_failure_keeps_successful_install_and_reports_warning(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manager = self._run_manager(root)
            source = manager.mods_dir / "Sora.apkh3"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"container")
            service = self._service(root, manager=manager)

            with patch(
                "worlds.kh3.client_support.seed_import_service.shutil.move",
                side_effect=OSError("source is locked"),
            ):
                result = service.prepare_missing_installed_pak()

            self.assertTrue(source.is_file())
            self.assertIsNone(result.archived_source_path)
            self.assertEqual(len(result.warnings), 1)
            self.assertIn("source is locked", result.warnings[0])
            self.assertTrue(result.installed_run.installed)

    def test_prepare_refuses_multiple_containers_or_a_running_game(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manager = self._run_manager(root)
            manager.mods_dir.mkdir(parents=True)
            (manager.mods_dir / "Sora.apkh3").write_bytes(b"container")
            (manager.mods_dir / "AP_Test.zip").write_bytes(b"archive")
            service = self._service(root, manager=manager)

            with self.assertRaisesRegex(KH3SeedImportError, "Multiple KH3 seed containers"):
                service.prepare_missing_installed_pak()

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manager = self._run_manager(root, running=True)
            manager.mods_dir.mkdir(parents=True)
            (manager.mods_dir / "Sora.apkh3").write_bytes(b"container")
            service = self._service(root, manager=manager, running=True)

            with self.assertRaisesRegex(KH3SeedImportError, "Close KINGDOM HEARTS III"):
                service.prepare_missing_installed_pak()

    def test_prepare_server_slot_data_writes_generates_manages_installs_and_remembers_server(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manager = self._run_manager(root)
            writes: list[tuple[Path, dict, str]] = []

            def write_container(path: Path, data: dict, server_address: str) -> None:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"server container")
                writes.append((path, data, server_address))

            service = self._service(root, manager=manager, container_writer=write_container)
            slot_data = {
                "protocol_name": BRIDGE_PROTOCOL_NAME, "protocol_version": BRIDGE_PROTOCOL_VERSION,
                "death_link": False,
                "kh3_randomizer": {"heart_piece_count": 7},
                "bridge": {
                    "protocol_name": BRIDGE_PROTOCOL_NAME, "protocol_version": BRIDGE_PROTOCOL_VERSION,
                    "locations": [{
                        "ap_location_id": BASE_LOCATION_ID,
                        "ap_location_name": "Test Location",
                        "assigned_item_id": 1263020002,
                    }],
                },
                "kh3_seed": {
                    "format": "kh3_server_seed",
                    "format_version": 1,
                    "seed_name": "11111111111111111111",
                    "archive_name": "AP_11111111111111111111.zip",
                    "slot": 1,
                    "slot_name": "Sora",
                    "all_players": ["Sora"],
                    "pak_seed": "AP_11111111111111111111_P1_Sora",
                    "remote_hint_placements": [],
                },
            }
            progress: list[str] = []
            selected_save_dir = root / "selected-profile" / "SaveGames"

            with self.assertLogs("Client", level="INFO") as captured_logs:
                result = service.prepare_server_slot_data(
                    slot_data,
                    server_address="archipelago.gg:38281",
                    expected_slot=1,
                    expected_slot_name="sora",
                    expected_seed_name="AP_11111111111111111111",
                    save_dir_override=selected_save_dir,
                    progress=progress.append,
                )

            self.assertEqual(result.status, "installed")
            self.assertEqual(result.installed_run.slot_name, "Sora")
            self.assertEqual(result.installed_run.server_address, "archipelago.gg:38281")
            self.assertEqual(result.activated_save_dir, selected_save_dir.resolve())
            self.assertEqual(len(writes), 1)
            self.assertEqual(writes[0][1]["format"], "kh3_ap_seed")
            self.assertEqual(decode_slot_data(writes[0][1]["slot_data"]), slot_data)
            self.assertEqual(writes[0][2], "archipelago.gg:38281")
            self.assertTrue(writes[0][0].is_file())
            self.assertEqual(
                [path.name for path in manager.mods_dir.glob("*.pak")],
                [result.installed_run.pak_name],
            )
            self.assertTrue(any("Received the KH3 seed from the server" in message for message in progress))
            self.assertTrue(any("Installing the generated KH3 seed pak" in message for message in progress))
            logs = "\n".join(captured_logs.output)
            self.assertIn("inline seed payload received", logs)
            self.assertIn("pak_seed=AP_11111111111111111111_P1_Sora", logs)
            self.assertIn("bridge_locations=1", logs)
            self.assertIn("inline seed container written", logs)
            self.assertIn("inline seed pak generated", logs)

    def test_prepare_server_slot_data_rejects_rooms_without_generation_payload(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            service = self._service(root)

            with self.assertRaisesRegex(KH3SeedImportError, "does not include a KH3 client-generation payload"):
                service.prepare_server_slot_data({"kh3_randomizer": {}, "bridge": {"locations": []}})

    def test_connected_seed_paths_cache_metadata_for_player_names_with_spaces(self) -> None:
        for source_kind in ("inline", "hosted"):
            with self.subTest(source_kind=source_kind), tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                manager = self._run_manager(root)
                seed = "60034101943795214820"
                player_name = "Shit Dom Fart"
                slot_data = {
                    "protocol_name": BRIDGE_PROTOCOL_NAME, "protocol_version": BRIDGE_PROTOCOL_VERSION,
                    "kh3_randomizer": {}, "bridge": {"protocol_name": BRIDGE_PROTOCOL_NAME, "protocol_version": BRIDGE_PROTOCOL_VERSION, "locations": []},
                    "kh3_seed": {
                        "format": "kh3_server_seed", "format_version": 1,
                        "seed_name": seed, "slot": 2, "slot_name": player_name,
                        "all_players": [player_name], "remote_hint_placements": [],
                        "pak_seed": f"AP_{seed}_P2_Shit_Dom_Fart",
                    },
                }
                metadata = {
                    "format": "kh3_ap_seed_metadata", "seed": f"AP_{seed}",
                    "slot": 2, "slot_name": player_name, "slot_data": slot_data,
                    "party_randomization_initially_enabled": True,
                }
                service = self._service(root, manager=manager, seed_metadata_loader=lambda _path: metadata)

                def generate(_source, output_dir):
                    pak = _write_generated_pak(
                        output_dir, seed=seed, player=2, slot_name="Shit_Dom_Fart",
                    )
                    return KH3PakResult(2, player_name, pak, None)

                service.container_generator = generate
                kwargs = {
                    "server_address": "archipelago.gg:38281", "expected_slot": 2,
                    "expected_slot_name": player_name, "expected_seed_name": seed,
                }
                if source_kind == "inline":
                    result = service.prepare_server_slot_data(slot_data, **kwargs)
                else:
                    pak = generate(None, root / "downloads").pak_path
                    result = service.prepare_player_artifact(pak, **kwargs)
                self.assertEqual(result.status, "installed")
                run = result.installed_run
                self.assertEqual(run.connection_slot_name, player_name)
                cached = run.bridge_dirs[0] / "kh3_ap_seed_metadata.json"
                from ..bridge_support.files import load_json_file
                from ..RuntimeSeed import metadata_payload, runtime_documents
                self.assertEqual(load_json_file(cached), metadata_payload(metadata))
                from ..RuntimeSeed import expand_document
                reloaded = expand_document("kh3_ap_seed_metadata.json", load_json_file(cached))
                self.assertEqual(runtime_documents(reloaded), runtime_documents(metadata))
                self.assertEqual(load_json_file(cached.parent / "slot_data.json"), reloaded["slot_data"])
                for name in ("runtime_seed_settings.json", "runtime_seed_hints.json"):
                    self.assertFalse((cached.parent / name).exists())

    def test_prepare_hosted_direct_pak_manages_installs_and_remembers_server(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manager = self._run_manager(root)
            source = _write_generated_pak(
                root / "downloads",
                seed="11111111111111111111",
                player=1,
                slot_name="Sora",
            )
            service = self._service(root, manager=manager)

            result = service.prepare_player_artifact(
                source,
                server_address="ap.aesais.net:38281",
                expected_slot=1,
                expected_slot_name="sora",
                expected_seed_name="AP_11111111111111111111",
            )

            self.assertTrue(source.is_file())
            self.assertTrue(result.installed_run.active)
            self.assertEqual(result.installed_run.slot_name, "Sora")
            self.assertEqual(result.installed_run.server_address, "ap.aesais.net:38281")
            self.assertEqual(
                [path.name for path in manager.mods_dir.glob("*.pak")],
                [source.name],
            )

    def test_prepare_hosted_pak_preserves_unidentified_saves_before_installing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manager = self._run_manager(root)
            source = _write_generated_pak(
                root / "downloads",
                seed="11111111111111111111",
                player=1,
                slot_name="Sora",
            )
            live_save = manager.save_dir / "kh3sv2" / "data" / "KHIII_slot1.bin"
            live_save.parent.mkdir(parents=True)
            live_save.write_bytes(b"unidentified")
            preserve_calls: list[Path] = []

            def preserve(save_dir: Path) -> dict:
                preserve_calls.append(save_dir)
                archive_dir = save_dir.parent / "SaveGames-Unmanaged-test"
                archived = archive_dir / live_save.relative_to(save_dir)
                archived.parent.mkdir(parents=True)
                live_save.replace(archived)
                return {
                    "status": "ok",
                    "mode": "unmanaged_archive",
                    "preserved_files": 1,
                    "preserved_dir": str(archive_dir),
                    "errors": [],
                }

            service = self._service(
                root,
                manager=manager,
                unidentified_save_preparer=preserve,
            )

            result = service.prepare_player_artifact(
                source,
                expected_slot=1,
                expected_slot_name="Sora",
                expected_seed_name="11111111111111111111",
            )

            self.assertEqual(preserve_calls, [manager.save_dir])
            self.assertTrue(result.installed_run.installed)
            self.assertEqual(len(result.warnings), 1)
            self.assertIn("existing vanilla backup was left unchanged", result.warnings[0])
            self.assertFalse(live_save.exists())

    def test_prepare_hosted_pak_preserves_saves_with_stale_seed_association(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manager = self._run_manager(root)
            source = _write_generated_pak(
                root / "downloads",
                seed="11111111111111111111",
                player=1,
                slot_name="Sora",
            )
            live_save = manager.save_dir / "kh3sv2" / "data" / "KHIII_slot1.bin"
            live_save.parent.mkdir(parents=True)
            live_save.write_bytes(b"stale association")
            preserve_calls: list[Path] = []

            def preserve(save_dir: Path) -> dict:
                preserve_calls.append(save_dir)
                archive_dir = save_dir.parent / "SaveGames-Unmanaged-test"
                archived = archive_dir / live_save.relative_to(save_dir)
                archived.parent.mkdir(parents=True)
                live_save.replace(archived)
                return {
                    "status": "ok",
                    "mode": "unmanaged_archive",
                    "preserved_files": 1,
                    "preserved_dir": str(archive_dir),
                    "errors": [],
                }

            service = self._service(
                root,
                manager=manager,
                unidentified_save_preparer=preserve,
            )

            result = service.prepare_player_artifact(
                source,
                expected_slot=1,
                expected_slot_name="Sora",
                expected_seed_name="11111111111111111111",
                active_save_seed_id="99999999999999999999",
            )

            self.assertEqual(preserve_calls, [manager.save_dir])
            self.assertTrue(result.installed_run.installed)
            self.assertEqual(len(result.warnings), 1)
            self.assertFalse(live_save.exists())

    def test_prepare_hosted_pak_uses_valid_managed_run_association(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manager = self._run_manager(root)
            previous_pak = _write_generated_pak(
                root / "previous",
                seed="22222222222222222222",
                player=2,
                slot_name="Riku",
            )
            previous_run = manager.import_pak(previous_pak)
            source = _write_generated_pak(
                root / "downloads",
                seed="11111111111111111111",
                player=1,
                slot_name="Sora",
            )
            live_save = manager.save_dir / "kh3sv2" / "data" / "KHIII_slot1.bin"
            live_save.parent.mkdir(parents=True)
            live_save.write_bytes(b"managed Riku save")
            service = self._service(
                root,
                manager=manager,
                unidentified_save_preparer=lambda _save_dir: self.fail(
                    "A valid managed run association must not be archived as unidentified."
                ),
            )

            result = service.prepare_player_artifact(
                source,
                expected_slot=1,
                expected_slot_name="Sora",
                expected_seed_name="11111111111111111111",
                active_save_seed_id=previous_run.seed_id,
                active_save_run_id=previous_run.run_id,
            )

            self.assertTrue(result.installed_run.installed)
            previous_snapshot = manager.get_run(previous_run.run_id).save_snapshot_dir
            self.assertIsNotNone(previous_snapshot)
            self.assertEqual(
                (previous_snapshot / live_save.relative_to(manager.save_dir)).read_bytes(),
                b"managed Riku save",
            )

    def test_prepare_hosted_pak_resolves_unique_seed_only_association(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manager = self._run_manager(root)
            previous_pak = _write_generated_pak(
                root / "previous",
                seed="22222222222222222222",
                player=2,
                slot_name="Riku",
            )
            previous_run = manager.import_pak(previous_pak)
            source = _write_generated_pak(
                root / "downloads",
                seed="11111111111111111111",
                player=1,
                slot_name="Sora",
            )
            live_save = manager.save_dir / "kh3sv2" / "data" / "KHIII_slot1.bin"
            live_save.parent.mkdir(parents=True)
            live_save.write_bytes(b"managed seed-only save")
            service = self._service(
                root,
                manager=manager,
                unidentified_save_preparer=lambda _save_dir: self.fail(
                    "A uniquely resolvable seed association must not be archived as unidentified."
                ),
            )

            result = service.prepare_player_artifact(
                source,
                expected_slot=1,
                expected_slot_name="Sora",
                expected_seed_name="11111111111111111111",
                active_save_seed_id=previous_run.seed_id,
            )

            self.assertTrue(result.installed_run.installed)
            previous_snapshot = manager.get_run(previous_run.run_id).save_snapshot_dir
            self.assertIsNotNone(previous_snapshot)
            self.assertEqual(
                (previous_snapshot / live_save.relative_to(manager.save_dir)).read_bytes(),
                b"managed seed-only save",
            )

    def test_prepare_hosted_pak_stops_when_unidentified_saves_cannot_be_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manager = self._run_manager(root)
            source = _write_generated_pak(
                root / "downloads",
                seed="11111111111111111111",
                player=1,
                slot_name="Sora",
            )
            service = self._service(
                root,
                manager=manager,
                unidentified_save_preparer=lambda _save_dir: {
                    "status": "partial",
                    "mode": "none",
                    "preserved_files": 0,
                    "preserved_dir": None,
                    "errors": ["synthetic archive failure"],
                },
            )

            with self.assertRaisesRegex(KH3SeedImportError, "synthetic archive failure"):
                service.prepare_player_artifact(
                    source,
                    expected_slot=1,
                    expected_slot_name="Sora",
                    expected_seed_name="11111111111111111111",
                )

            self.assertEqual(list(manager.mods_dir.glob("*.pak")), [])

    def test_prepare_hosted_apkh3_generates_only_the_authenticated_player_pak(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manager = self._run_manager(root)
            source = root / "downloads" / "Sora.apkh3"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"container")
            service = self._service(root, manager=manager)

            result = service.prepare_player_artifact(
                source,
                expected_slot=1,
                expected_slot_name="Sora",
                expected_seed_name="11111111111111111111",
            )

            self.assertTrue(source.is_file())
            self.assertEqual(result.installed_run.slot_name, "Sora")
            self.assertTrue(result.installed_run.installed)
            self.assertEqual(len(result.generated_paks), 1)

    def test_prepare_hosted_pak_rejects_wrong_player_before_managing_it(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manager = self._run_manager(root)
            source = _write_generated_pak(
                root / "downloads",
                seed="11111111111111111111",
                player=2,
                slot_name="Riku",
            )
            service = self._service(root, manager=manager)

            with self.assertRaisesRegex(KH3SeedImportError, "not slot 'Sora'"):
                service.prepare_player_artifact(
                    source,
                    expected_slot=1,
                    expected_slot_name="Sora",
                    expected_seed_name="11111111111111111111",
                )

            self.assertEqual(manager.list_runs(), [])
            self.assertEqual(list(manager.mods_dir.glob("*.pak")), [])


if __name__ == "__main__":
    unittest.main()
