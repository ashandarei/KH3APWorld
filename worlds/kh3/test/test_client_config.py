from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import subprocess
import unittest
import zipfile
from datetime import datetime
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, patch
from urllib.error import HTTPError

from .. import Data, PakGenerator
from ..Bridge import BRIDGE_PROTOCOL_NAME, BRIDGE_PROTOCOL_VERSION
from ..Client import (
    KH3_SAVE_SEED_ASSOCIATIONS_KEY,
    KH3_SAVE_SEED_GUARD_VERSION,
    KH3_STEAM_CLOUD_ACKNOWLEDGED_KEY,
    KH3Context,
)
from ..client_support import goa_download, runtime_cleanup, save_management, seed_paks
from ..client_support.debug_bundle import export_kh3_debug_bundle
from ..client_support.goa_download import goa_mod_install_status
from ..client_support.save_management import (
    backup_vanilla_save_files,
    restore_vanilla_save_files,
    vanilla_save_backup_dir,
)
from ..client_support.save_service import SaveManagementService
from ..client_support.settings import KH3ClientSettings, load_kh3_client_settings, resolve_default_kh3_mods_dir
from ..installer_support import (
    file_ops as installer_file_ops,
)
from ..installer_support import (
    installation as installer_installation,
)
from ..installer_support import (
    paths as installer_paths,
)
from ..installer_support import (
    platform_config as installer_platform,
)
from ..installer_support import (
    resources as installer_resources,
)
from ..installer_support import (
    status as installer_status,
)
from ..installer_support import (
    ue4ss_config as installer_ue4ss_config,
)
from ..platform_support import game_discovery, game_launch, path_opening, runtime_install, save_discovery, steam_cloud
from ..platform_support.game_discovery import (
    detect_kh3_install_dir,
    is_epic_kh3_root_dir,
    kh3_ue4ss_log_from_root,
    normalize_kh3_root_dir,
    resolve_kh3_executable_path,
    resolve_steam_launch_uri_for_kh3_executable,
)
from ..platform_support.game_launch import launch_kh3_game as launch_kh3_game_config
from ..platform_support.runtime_install import install_kh3_runtime_files
from ..platform_support.save_discovery import (
    is_kh3_auto_save_dir,
    is_kh3_save_dir_dynamic_root,
    resolve_default_kh3_save_dir,
    resolve_kh3_save_dir_for_root,
    resolve_kh3_save_dir_input,
)
from ..platform_support.save_paths import STEAM_SHARED_SAVE_ACCOUNT_ID, default_save_directory, normalize_save_directory
from ..platform_support.steam_cloud import (
    SteamCloudDisableResult,
    SteamCloudDisableStatus,
    SteamCloudState,
    detect_kh3_steam_cloud_state,
    steam_cloud_state_from_sharedconfig,
    steam_userdata_account_id,
)
from .runtime_download_fixture import runtime_release_fixture


class TestKH3SettingsAndDiscovery(unittest.TestCase):
    def test_nested_vanilla_save_directory_uses_sidecar_root(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "KINGDOM HEARTS III"
            epic_save = root / "Epic Games Store" / "example-account" / "SaveGames"
            steam_save = root / "Steam" / "76561197982685695" / "SaveGames"
            shared_save = steam_save.parent.parent / STEAM_SHARED_SAVE_ACCOUNT_ID / "SaveGames"
            for supplied, expected in (
                (epic_save / "kh3sv2", epic_save),
                (epic_save / "KH3SV2", epic_save),
                (epic_save, epic_save),
                (steam_save / "kh3sv2", shared_save),
                (root / "custom" / "kh3sv2", root / "custom" / "kh3sv2"),
            ):
                with self.subTest(supplied=supplied):
                    self.assertEqual(normalize_save_directory(supplied), expected.resolve())
                    self.assertEqual(resolve_kh3_save_dir_input(supplied), expected.resolve())

    def test_steam_save_discovery_uses_platform_specific_roots(self) -> None:
        for platform in ("win32", "linux"):
            for existing in (False, True):
                with self.subTest(platform=platform, existing=existing), TemporaryDirectory() as temp_dir:
                    root = Path(temp_dir)
                    steamapps = root / "SteamLibrary" / "steamapps"
                    game_root = steamapps / "common" / "KINGDOM HEARTS III" / "KINGDOM HEARTS III"
                    windows_root = root / "Documents" / "My Games" / "KINGDOM HEARTS III" / "Steam"
                    proton_root = (steamapps / "compatdata" / "2552450" / "pfx" / "drive_c"
                                   / "users" / "steamuser" / "My Documents" / "My Games"
                                   / "KINGDOM HEARTS III" / "Steam")
                    if existing:
                        windows_root.mkdir(parents=True)
                        proton_root.mkdir(parents=True)
                    expected_root = proton_root if platform == "linux" else windows_root
                    with (
                        patch.object(save_discovery.sys, "platform", platform),
                        patch.object(save_discovery, "_candidate_documents_dirs", return_value=[root / "Documents"]),
                        patch.object(save_discovery, "is_epic_kh3_root_dir", return_value=False),
                    ):
                        self.assertEqual(
                            resolve_kh3_save_dir_for_root(game_root, KH3ClientSettings()),
                            (expected_root / STEAM_SHARED_SAVE_ACCOUNT_ID / "SaveGames").resolve(),
                        )

    def test_client_settings_parse_kh3_environment_in_one_typed_model(self) -> None:
        settings = load_kh3_client_settings(
            {
                "KH3_DIR": "game-root",
                "KH3_WIN64_DIR": "win64-root",
                "KH3_SAVE_DIR": "save-root",
                "KH3_MODS_DIR": "mods-root",
                "KH3_UE4SS_LOG": "ue4ss.log",
                "KH3_AP_PROBE_CONFIG": "config.lua",
                "KH3_STEAMAPPS_DIR": "steamapps-root",
                "KH3_EPIC_MANIFEST_DIR": "manifest-root",
                "KH3_STEAM_ACCOUNT_ID": "76561197982685695",
            }
        )

        self.assertIsInstance(settings, KH3ClientSettings)
        self.assertEqual(settings.kh3_dir, Path("game-root"))
        self.assertEqual(settings.kh3_win64_dir, Path("win64-root"))
        self.assertEqual(settings.kh3_save_dir, Path("save-root"))
        self.assertEqual(settings.kh3_mods_dir, Path("mods-root"))
        self.assertEqual(settings.kh3_ue4ss_log, Path("ue4ss.log"))
        self.assertEqual(settings.kh3_ap_probe_config, Path("config.lua"))
        self.assertEqual(settings.kh3_steamapps_dir, Path("steamapps-root"))
        self.assertEqual(settings.kh3_epic_manifest_dir, Path("manifest-root"))
        self.assertEqual(settings.kh3_steam_account_id, "76561197982685695")

    def test_client_settings_normalize_empty_environment_values(self) -> None:
        settings = load_kh3_client_settings(
            {
                "KH3_DIR": "",
                "KH3_STEAM_ACCOUNT_ID": "",
            }
        )

        self.assertIsNone(settings.kh3_dir)
        self.assertIsNone(settings.kh3_steam_account_id)

    def test_platform_path_opening_can_create_the_target_directory(self) -> None:
        with TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "created" / "directory"
            with patch.object(path_opening.Utils, "open_file") as open_file:
                path_opening.open_path(target, description="test directory", create_directory=True)

            self.assertTrue(target.is_dir())
            open_file.assert_called_once_with(target)

    def test_platform_runtime_install_writes_probe_config(self) -> None:
        with TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "Mods" / "KH3APProbeCpp" / "config.lua"
            bridge_dir = Path(temp_dir) / "bridge"
            with patch.object(
                runtime_install,
                "wine_visible_path_text",
                return_value=r"Z:\bridge\runtime_loot_event.json",
            ):
                runtime_install.write_probe_config(config_path, bridge_dir)

            self.assertEqual(
                config_path.read_text(encoding="utf-8"),
                "return {\n"
                "    event_output_paths = {\n"
                "        [[Mods\\KH3APProbeCpp\\runtime_loot_event.json]],\n"
                "        [[Z:\\bridge\\runtime_loot_event.json]],\n"
                "    },\n"
                "}\n",
            )

    def test_platform_steam_launch_requests_proton_override_before_uri(self) -> None:
        with TemporaryDirectory() as temp_dir:
            steamapps = Path(temp_dir) / "Steam" / "steamapps"
            install_dir = steamapps / "common" / "KINGDOM HEARTS III"
            game_root = install_dir / "KINGDOM HEARTS III"
            executable = game_root / "Binaries" / "Win64" / "KINGDOM HEARTS III.exe"
            executable.parent.mkdir(parents=True)
            executable.write_bytes(b"executable")
            (steamapps / "appmanifest_2552450.acf").write_text(
                '"AppState"\n{\n"appid" "2552450"\n"installdir" "KINGDOM HEARTS III"\n}\n',
                encoding="utf-8",
            )

            with (
                patch.object(game_launch, "_ensure_kh3_proton_dwmapi_override") as ensure_override,
                patch.object(game_launch, "_open_launch_uri", return_value=True) as uri_launch,
            ):
                launch_kh3_game_config(game_root)

            ensure_override.assert_called_once_with(game_root)
            uri_launch.assert_called_once_with("steam://run/2552450", description="Steam")

    def test_explicit_settings_drive_steam_install_but_save_uses_shared_account(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            steamapps_dir = root / "Steam" / "steamapps"
            game_root = steamapps_dir / "common" / "KINGDOM HEARTS III" / "KINGDOM HEARTS III"
            executable = game_root / "Binaries" / "Win64" / "KINGDOM HEARTS III.exe"
            executable.parent.mkdir(parents=True)
            executable.write_bytes(b"exe")
            (steamapps_dir / "appmanifest_2552450.acf").write_text(
                '"AppState"\n{\n"appid" "2552450"\n"installdir" "KINGDOM HEARTS III"\n}\n',
                encoding="utf-8",
            )
            account_id = "76561197982685695"
            account_save_dir = (
                root
                / "Documents"
                / "My Games"
                / "KINGDOM HEARTS III"
                / "Steam"
                / account_id
                / "SaveGames"
            )
            shared_save_dir = account_save_dir.parents[1] / "00000000000000000000000000000000" / "SaveGames"
            account_save_dir.mkdir(parents=True)
            shared_save_dir.mkdir(parents=True)
            settings = KH3ClientSettings(
                kh3_steamapps_dir=steamapps_dir,
                kh3_steam_account_id=account_id,
            )

            self.assertEqual(game_discovery.detect_kh3_install_dir(settings), game_root.resolve())
            with patch.dict(os.environ, {"USERPROFILE": str(root)}, clear=True):
                self.assertEqual(
                    save_discovery.resolve_kh3_save_dir_for_root(game_root, settings),
                    shared_save_dir.resolve(),
                )

    def test_client_detects_kh3_install_from_windows_steam_registry_and_legacy_library_vdf(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            steam_root = root / "CustomSteam"
            library_root = root / "GameLibrary"
            steamapps_dir = library_root / "steamapps"
            game_root = steamapps_dir / "common" / "KINGDOM HEARTS III" / "KINGDOM HEARTS III"
            executable = game_root / "Binaries" / "Win64" / "KINGDOM HEARTS III.exe"
            executable.parent.mkdir(parents=True)
            executable.write_bytes(b"exe")
            (steamapps_dir / "appmanifest_2552450.acf").write_text(
                '"AppState"\n{\n'
                '    "appid"        "2552450"\n'
                '    "installdir"   "KINGDOM HEARTS III"\n'
                '}\n',
                encoding="utf-8",
            )
            steam_config = steam_root / "config"
            steam_config.mkdir(parents=True)
            (steam_root / "steamapps").mkdir()
            library_path_text = str(library_root).replace("\\", "\\\\")
            (steam_config / "libraryfolders.vdf").write_text(
                '"LibraryFolders"\n{\n'
                f'    "0"        "{library_path_text}"\n'
                '}\n',
                encoding="utf-8",
            )

            class FakeRegistryKey:
                def __enter__(self):
                    return self

                def __exit__(self, *_args):
                    return False

            class FakeWinreg:
                HKEY_CURRENT_USER = "HKCU"
                HKEY_LOCAL_MACHINE = "HKLM"
                KEY_READ = 0x20019
                KEY_WOW64_64KEY = 0x100
                KEY_WOW64_32KEY = 0x200

                @staticmethod
                def OpenKey(*_args):
                    return FakeRegistryKey()

                @staticmethod
                def QueryValueEx(_key, value_name):
                    if value_name == "SteamPath":
                        return str(steam_root), 1
                    raise FileNotFoundError(value_name)

            settings = KH3ClientSettings()
            with patch.object(game_discovery.os, "name", "nt"), \
                    patch.object(game_discovery, "winreg", FakeWinreg), \
                    patch.object(game_discovery, "_epic_manifest_kh3_roots", return_value=[]), \
                    patch.object(game_discovery, "_default_epic_kh3_roots", return_value=[]):
                self.assertEqual(game_discovery._windows_steam_registry_roots(), [steam_root.resolve()])
                self.assertEqual(game_discovery.detect_kh3_install_dir(settings), game_root.resolve())

class TestKH3ItemConsumerPlan(unittest.TestCase):
    def setUp(self) -> None:
        # Native prerequisite execution is covered separately, never run a real
        # administrator installer from the runtime filesystem fixture tests.
        prerequisite = patch.object(installer_installation, "prepare_xinput_runtime", return_value={"ready": True})
        prerequisite.start()
        self.addCleanup(prerequisite.stop)
        self._client_cache_temp_dir = TemporaryDirectory()
        self.addCleanup(self._client_cache_temp_dir.cleanup)
        cache_dir = Path(self._client_cache_temp_dir.name)
        self.goa_status_cache_path = cache_dir / "goa-status.json"
        self.seed_pak_metadata_cache_path = cache_dir / "seed-pak-metadata.json"
        for target, return_value in (
            ("worlds.kh3.Client.goa_mod_status_cache_path", self.goa_status_cache_path),
            ("worlds.kh3.client_support.command_executor.goa_mod_status_cache_path", self.goa_status_cache_path),
            ("worlds.kh3.Client.seed_pak_metadata_cache_path", self.seed_pak_metadata_cache_path),
        ):
            patcher = patch(target, return_value=return_value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_client_delete_save_files_archives_gameplay_saves_and_keeps_safe_files(self) -> None:
        with TemporaryDirectory() as temp_dir:
            save_dir = Path(temp_dir) / "SaveGames"
            nested_dir = save_dir / "kh3sv2" / "data"
            nested_dir.mkdir(parents=True)
            (save_dir / "KHIII_slot0.bin").write_text("slot", encoding="utf-8")
            (save_dir / "steam_autocloud.vdf").write_text("safe", encoding="utf-8")
            (nested_dir / "KHIII_slot1.bin").write_text("events", encoding="utf-8")

            result = KH3Context.delete_save_files(save_dir, archive_label="AP_12345")

            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["removed_files"], 0)
            self.assertEqual(result["archived_files"], 2)
            self.assertEqual(result["kept_files"], 1)
            self.assertTrue(save_dir.exists())
            self.assertFalse((save_dir / "KHIII_slot0.bin").exists())
            self.assertTrue((save_dir / "steam_autocloud.vdf").exists())
            self.assertFalse(nested_dir.exists())
            archive_dir = Path(result["archive_dir"])
            self.assertTrue(archive_dir.name.startswith("SaveGames-AP_12345-"))
            self.assertEqual((archive_dir / "KHIII_slot0.bin").read_text(encoding="utf-8"), "slot")
            self.assertEqual((archive_dir / "kh3sv2" / "data" / "KHIII_slot1.bin").read_text(encoding="utf-8"), "events")

    def test_client_delete_save_files_can_delete_without_backup(self) -> None:
        with TemporaryDirectory() as temp_dir:
            save_dir = Path(temp_dir) / "SaveGames"
            save_dir.mkdir()
            (save_dir / "KHIII_slot0.bin").write_text("slot", encoding="utf-8")
            (save_dir / "steam_autocloud.vdf").write_text("safe", encoding="utf-8")

            result = KH3Context.delete_save_files(save_dir, backup_current_save=False)

            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["removed_files"], 1)
            self.assertEqual(result["archived_files"], 0)
            self.assertIsNone(result["archive_dir"])
            self.assertFalse((save_dir / "KHIII_slot0.bin").exists())
            self.assertTrue((save_dir / "steam_autocloud.vdf").exists())

    def test_client_delete_save_files_preserves_settings_even_when_root_removal_requested(self) -> None:
        with TemporaryDirectory() as temp_dir:
            steam_root = Path(temp_dir) / "Steam"
            save_dir = steam_root / "76561198014076540" / "SaveGames"
            save_dir.mkdir(parents=True)
            (save_dir / "KHIII_slot0.bin").write_text("slot", encoding="utf-8")
            (save_dir / "steam_autocloud.vdf").write_text("safe", encoding="utf-8")
            (save_dir / "ModLoaderSaves.sav").write_bytes(b"mod-settings")

            result = KH3Context.delete_save_files(
                save_dir,
                preserve_root=False,
                backup_current_save=False,
            )

            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["removed_files"], 1)
            self.assertEqual(result["kept_files"], 2)
            self.assertTrue((save_dir / "ModLoaderSaves.sav").exists())
            self.assertTrue((save_dir / "steam_autocloud.vdf").exists())
            self.assertEqual((steam_root / "ModLoaderSaves.sav").read_bytes(), b"mod-settings")

    def test_client_delete_save_files_archives_gameplay_but_keeps_unmanaged_files_and_root(self) -> None:
        with TemporaryDirectory() as temp_dir:
            save_dir = Path(temp_dir) / "Account" / "SaveGames"
            save_dir.mkdir(parents=True)
            (save_dir / "KHIII_slot0.bin").write_text("slot", encoding="utf-8")
            (save_dir / "steam_autocloud.vdf").write_text("safe", encoding="utf-8")

            result = KH3Context.delete_save_files(
                save_dir,
                preserve_root=False,
                archive_label="AP_RemoveRoot",
            )

            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["archived_files"], 1)
            self.assertEqual(result["removed_files"], 0)
            self.assertEqual(result["kept_files"], 1)
            self.assertTrue((save_dir / "steam_autocloud.vdf").exists())
            archive_dir = Path(result["archive_dir"])
            self.assertEqual((archive_dir / "KHIII_slot0.bin").read_text(encoding="utf-8"), "slot")

    def test_client_delete_save_files_keeps_root_when_modloader_settings_cannot_be_preserved(self) -> None:
        with TemporaryDirectory() as temp_dir:
            steam_root = Path(temp_dir) / "Steam"
            save_dir = steam_root / "76561198014076540" / "SaveGames"
            save_dir.mkdir(parents=True)
            (save_dir / "KHIII_slot0.bin").write_text("slot", encoding="utf-8")
            modloader_save = save_dir / "ModLoaderSaves.sav"
            modloader_save.write_bytes(b"mod-settings")
            (steam_root / "ModLoaderSaves.sav").mkdir()

            result = KH3Context.delete_save_files(
                save_dir,
                preserve_root=False,
                backup_current_save=False,
            )

            self.assertEqual(result["status"], "partial")
            self.assertTrue(result["errors"])
            self.assertTrue(save_dir.exists())
            self.assertTrue(modloader_save.exists())
            self.assertFalse((save_dir / "KHIII_slot0.bin").exists())

    def test_client_delete_save_files_preserves_modloader_save_and_updates_shared_copy(self) -> None:
        with TemporaryDirectory() as temp_dir:
            steam_root = Path(temp_dir) / "Steam"
            save_dir = steam_root / "7656119801407654" / "SaveGames"
            save_dir.mkdir(parents=True)
            modloader_save = save_dir / "ModLoaderSaves.sav"
            shared_save = steam_root / "ModLoaderSaves.sav"
            modloader_save.write_bytes(b"active-newer")
            shared_save.write_bytes(b"shared-older")
            (save_dir / "RandomizerEvents.sav").write_bytes(b"events")

            result = KH3Context.delete_save_files(save_dir, archive_label="AP_ModLoader")

            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["modloader_save"]["status"], "updated_shared")
            self.assertEqual(result["removed_files"], 0)
            self.assertEqual(result["archived_files"], 1)
            self.assertEqual(result["kept_files"], 1)
            self.assertEqual(modloader_save.read_bytes(), b"active-newer")
            self.assertEqual(shared_save.read_bytes(), b"active-newer")
            archive_dir = Path(result["archive_dir"])
            self.assertEqual((archive_dir / "RandomizerEvents.sav").read_bytes(), b"events")
            self.assertFalse((archive_dir / "ModLoaderSaves.sav").exists())

    def test_client_delete_save_files_restores_shared_modloader_save_into_savegames(self) -> None:
        with TemporaryDirectory() as temp_dir:
            steam_root = Path(temp_dir) / "Steam"
            save_dir = steam_root / "7656119801407654" / "SaveGames"
            shared_save = steam_root / "ModLoaderSaves.sav"
            shared_save.parent.mkdir(parents=True)
            shared_save.write_bytes(b"shared-settings")

            result = KH3Context.delete_save_files(save_dir, backup_current_save=False)

            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["modloader_save"]["status"], "restored")
            self.assertEqual(result["kept_files"], 1)
            self.assertEqual((save_dir / "ModLoaderSaves.sav").read_bytes(), b"shared-settings")

    def test_client_delete_save_files_migrates_modloader_save_from_latest_legacy_archive(self) -> None:
        with TemporaryDirectory() as temp_dir:
            steam_root = Path(temp_dir) / "Steam"
            account_dir = steam_root / "7656119801407654"
            save_dir = account_dir / "SaveGames"
            older_archive = account_dir / "SaveGames-AP_Old-20260701-010101"
            newer_archive = account_dir / "SaveGames-AP_New-20260702-010101"
            older_archive.mkdir(parents=True)
            newer_archive.mkdir(parents=True)
            (older_archive / "ModLoaderSaves.sav").write_bytes(b"older")
            newest_save = newer_archive / "ModLoaderSaves.sav"
            newest_save.write_bytes(b"newer")
            os.utime(older_archive / "ModLoaderSaves.sav", ns=(1, 1))
            os.utime(newest_save, ns=(2, 2))

            result = KH3Context.delete_save_files(save_dir, backup_current_save=False)

            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["modloader_save"]["status"], "migrated")
            self.assertEqual((save_dir / "ModLoaderSaves.sav").read_bytes(), b"newer")
            self.assertEqual((steam_root / "ModLoaderSaves.sav").read_bytes(), b"newer")
            self.assertTrue(newest_save.exists())

    def test_save_cleanup_plan_is_explicit_and_does_not_mutate_files(self) -> None:
        with TemporaryDirectory() as temp_dir:
            save_dir = Path(temp_dir) / "SaveGames"
            nested_dir = save_dir / "kh3sv2" / "data"
            nested_dir.mkdir(parents=True)
            gameplay_save = save_dir / "KHIII_slot0.bin"
            sidecar = nested_dir / "KHIII_slot1.bin"
            safe_file = save_dir / "steam_autocloud.vdf"
            gameplay_save.write_bytes(b"slot")
            sidecar.write_bytes(b"events")
            safe_file.write_text("safe", encoding="utf-8")

            plan = save_management.plan_delete_save_files(
                save_dir,
                archive_label="AP_Test",
                now=datetime(2026, 7, 9, 12, 34, 56),
            )

            self.assertEqual(plan.status, "ready")
            self.assertEqual({move.source for move in plan.moves}, {gameplay_save, sidecar})
            self.assertEqual(plan.archive_dir, save_dir.parent / "SaveGames-AP_Test-20260709-123456")
            self.assertEqual(plan.kept_files, (safe_file,))
            self.assertTrue(gameplay_save.exists())
            self.assertTrue(sidecar.exists())
            self.assertTrue(safe_file.exists())
            self.assertFalse(plan.archive_dir.exists())

    def test_save_backup_and_restore_plans_are_read_only(self) -> None:
        with TemporaryDirectory() as temp_dir:
            save_dir = Path(temp_dir) / "SaveGames"
            save_dir.mkdir()
            vanilla_save = save_dir / "KHIII_slot0.bin"
            vanilla_save.write_bytes(b"vanilla")

            backup_plan = save_management.plan_backup_vanilla_save_files(save_dir)
            self.assertEqual(backup_plan.status, "ready")
            self.assertEqual(backup_plan.moves[0].source, vanilla_save)
            self.assertTrue(vanilla_save.exists())
            self.assertFalse(vanilla_save_backup_dir(save_dir).exists())

            save_management.execute_vanilla_save_backup_plan(backup_plan)
            ap_save = save_dir / "KHIII_slot0.bin"
            ap_save.write_bytes(b"ap")
            restore_plan = save_management.plan_restore_vanilla_save_files(
                save_dir,
                now=datetime(2026, 7, 9, 12, 34, 56),
            )

            self.assertEqual(restore_plan.status, "ready")
            self.assertEqual(restore_plan.restores[0].destination, ap_save)
            self.assertTrue(ap_save.exists())
            self.assertTrue(restore_plan.restores[0].source.exists())

    def test_backup_vanilla_save_files_moves_gameplay_saves_to_protected_backup(self) -> None:
        with TemporaryDirectory() as temp_dir:
            save_dir = Path(temp_dir) / "SaveGames"
            nested_dir = save_dir / "kh3sv2" / "data"
            nested_dir.mkdir(parents=True)
            (save_dir / "KHIII_slot0.bin").write_text("vanilla", encoding="utf-8")
            (save_dir / "steam_autocloud.vdf").write_text("safe", encoding="utf-8")
            (nested_dir / "KHIII_slot1.bin").write_text("events", encoding="utf-8")

            result = backup_vanilla_save_files(save_dir)
            backup_dir = vanilla_save_backup_dir(save_dir)

            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["moved_files"], 2)
            self.assertEqual(result["kept_files"], 1)
            self.assertTrue(save_dir.exists())
            self.assertFalse((save_dir / "KHIII_slot0.bin").exists())
            self.assertTrue((save_dir / "steam_autocloud.vdf").exists())
            self.assertFalse(nested_dir.exists())
            self.assertEqual((backup_dir / "KHIII_slot0.bin").read_text(encoding="utf-8"), "vanilla")
            self.assertEqual((backup_dir / "kh3sv2" / "data" / "KHIII_slot1.bin").read_text(encoding="utf-8"), "events")

    def test_backup_vanilla_save_files_keeps_modloader_save_out_of_backup(self) -> None:
        with TemporaryDirectory() as temp_dir:
            save_dir = Path(temp_dir) / "SaveGames"
            save_dir.mkdir()
            modloader_save = save_dir / "ModLoaderSaves.sav"
            modloader_save.write_bytes(b"settings")
            (save_dir / "KHIII_slot0.bin").write_bytes(b"vanilla")

            result = backup_vanilla_save_files(save_dir)
            backup_dir = vanilla_save_backup_dir(save_dir)

            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["moved_files"], 1)
            self.assertEqual(result["kept_files"], 1)
            self.assertEqual(modloader_save.read_bytes(), b"settings")
            self.assertEqual((save_dir.parent / "ModLoaderSaves.sav").read_bytes(), b"settings")
            self.assertFalse((backup_dir / "ModLoaderSaves.sav").exists())

    def test_unidentified_saves_preserve_vanilla_then_use_separate_archive(self) -> None:
        with TemporaryDirectory() as temp_dir:
            save_dir = Path(temp_dir) / "SaveGames"
            save_dir.mkdir()
            first_save = save_dir / "KHIII_slot0.bin"
            first_save.write_bytes(b"first unidentified save")
            service = SaveManagementService(object(), process_running=lambda: False)

            vanilla_result = service.preserve_unidentified_saves_before_ap_install(save_dir)

            self.assertEqual(vanilla_result["status"], "ok")
            self.assertEqual(vanilla_result["mode"], "vanilla_backup")
            self.assertEqual(vanilla_result["preserved_files"], 1)
            self.assertEqual((vanilla_save_backup_dir(save_dir) / "KHIII_slot0.bin").read_bytes(), b"first unidentified save")

            second_save = save_dir / "KHIII_slot0.bin"
            second_save.write_bytes(b"later unidentified save")
            archive_result = service.preserve_unidentified_saves_before_ap_install(save_dir)

            self.assertEqual(archive_result["status"], "ok")
            self.assertEqual(archive_result["mode"], "unmanaged_archive")
            self.assertEqual(archive_result["preserved_files"], 1)
            self.assertFalse(second_save.exists())
            self.assertEqual(
                (Path(archive_result["preserved_dir"]) / "KHIII_slot0.bin").read_bytes(),
                b"later unidentified save",
            )
            self.assertEqual((vanilla_save_backup_dir(save_dir) / "KHIII_slot0.bin").read_bytes(), b"first unidentified save")

    def test_restore_vanilla_save_files_archives_ap_saves_and_restores_backup(self) -> None:
        with TemporaryDirectory() as temp_dir:
            save_dir = Path(temp_dir) / "SaveGames"
            backup_dir = vanilla_save_backup_dir(save_dir)
            save_dir.mkdir()
            backup_dir.mkdir()
            (save_dir / "KHIII_slot0.bin").write_text("ap", encoding="utf-8")
            (save_dir / "steam_autocloud.vdf").write_text("safe", encoding="utf-8")
            (backup_dir / "KHIII_slot0.bin").write_text("vanilla", encoding="utf-8")

            result = restore_vanilla_save_files(save_dir)

            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["restored_files"], 1)
            self.assertEqual(result["archived_current_files"], 1)
            self.assertFalse(backup_dir.exists())
            self.assertEqual((save_dir / "KHIII_slot0.bin").read_text(encoding="utf-8"), "vanilla")
            self.assertEqual((save_dir / "steam_autocloud.vdf").read_text(encoding="utf-8"), "safe")
            archive_dir = Path(result["archive_dir"])
            self.assertTrue(archive_dir.name.startswith("SaveGames-AP_Restore-"))
            self.assertEqual((archive_dir / "KHIII_slot0.bin").read_text(encoding="utf-8"), "ap")

    def test_restore_vanilla_save_files_keeps_active_modloader_save_over_legacy_backup_copy(self) -> None:
        with TemporaryDirectory() as temp_dir:
            save_dir = Path(temp_dir) / "SaveGames"
            backup_dir = vanilla_save_backup_dir(save_dir)
            save_dir.mkdir()
            backup_dir.mkdir()
            (save_dir / "ModLoaderSaves.sav").write_bytes(b"active-newer")
            (save_dir / "KHIII_slot0.bin").write_bytes(b"ap")
            (backup_dir / "ModLoaderSaves.sav").write_bytes(b"legacy-older")
            (backup_dir / "KHIII_slot0.bin").write_bytes(b"vanilla")

            result = restore_vanilla_save_files(save_dir)

            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["restored_files"], 1)
            self.assertEqual(result["removed_files"], 0)
            self.assertEqual((save_dir / "ModLoaderSaves.sav").read_bytes(), b"active-newer")
            self.assertEqual((save_dir.parent / "ModLoaderSaves.sav").read_bytes(), b"active-newer")
            self.assertEqual((backup_dir / "ModLoaderSaves.sav").read_bytes(), b"legacy-older")

    def test_restore_vanilla_save_files_removes_empty_backup_dir(self) -> None:
        with TemporaryDirectory() as temp_dir:
            save_dir = Path(temp_dir) / "SaveGames"
            backup_dir = vanilla_save_backup_dir(save_dir)
            nested_dir = backup_dir / "kh3sv2" / "data"
            nested_dir.mkdir(parents=True)

            result = restore_vanilla_save_files(save_dir)

            self.assertEqual(result["status"], "empty")
            self.assertGreaterEqual(result["removed_dirs"], 2)
            self.assertFalse(backup_dir.exists())

    def test_install_update_ap_backs_up_vanilla_saves_before_first_install(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            save_dir = root / "SaveGames"
            mods_dir = root / "KINGDOM HEARTS III" / "Content" / "Paks" / "~mods"
            save_dir.mkdir()
            (save_dir / "KHIII_slot0.bin").write_text("vanilla", encoding="utf-8")

            ctx = KH3Context.__new__(KH3Context)
            ctx.kh3_dir = root / "KINGDOM HEARTS III"
            ctx.kh3_mods_dir = mods_dir
            ctx.bridge_save_dir = save_dir
            ctx.bridge_dir = None
            ctx.probe_config_path = None

            with (
                patch("worlds.kh3.Client.is_process_running", return_value=False),
                patch("worlds.kh3.client_support.command_executor.download_goa_mod") as mocked_download_goa,
                patch("worlds.kh3.client_support.runtime_artifact_executor.provision_host"),
                patch(
                    "worlds.kh3.client_support.runtime_artifact_executor.install_kh3_runtime_files",
                    return_value={"status": "ok"},
                ),
            ):
                ctx.install_kh3_runtime_files()

            backup_dir = vanilla_save_backup_dir(save_dir)
            self.assertFalse((save_dir / "KHIII_slot0.bin").exists())
            self.assertEqual((backup_dir / "KHIII_slot0.bin").read_text(encoding="utf-8"), "vanilla")
            mocked_download_goa.assert_called_once_with(
                mods_dir,
                cache_path=self.goa_status_cache_path,
            )

    def test_install_update_ap_backs_up_vanilla_saves(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            save_dir = root / "SaveGames"
            mods_dir = root / "KINGDOM HEARTS III" / "Content" / "Paks" / "~mods"
            save_dir.mkdir()
            mods_dir.mkdir(parents=True)
            (save_dir / "KHIII_slot0.bin").write_text("vanilla", encoding="utf-8")
            (mods_dir / "pakchunk99-GardenOfAssemblageRandomizer_99_P.pak").write_text("goa", encoding="utf-8")

            ctx = KH3Context.__new__(KH3Context)
            ctx.kh3_dir = root / "KINGDOM HEARTS III"
            ctx.kh3_mods_dir = mods_dir
            ctx.bridge_save_dir = save_dir
            ctx.bridge_dir = None
            ctx.probe_config_path = None

            with (
                patch("worlds.kh3.Client.is_process_running", return_value=False),
                patch("worlds.kh3.client_support.command_executor.download_goa_mod") as mocked_download_goa,
                patch("worlds.kh3.client_support.runtime_artifact_executor.provision_host"),
                patch(
                    "worlds.kh3.client_support.runtime_artifact_executor.install_kh3_runtime_files",
                    return_value={"status": "ok"},
                ),
            ):
                ctx.install_kh3_runtime_files()

            backup_dir = vanilla_save_backup_dir(save_dir)
            self.assertFalse((save_dir / "KHIII_slot0.bin").exists())
            self.assertEqual((backup_dir / "KHIII_slot0.bin").read_text(encoding="utf-8"), "vanilla")
            mocked_download_goa.assert_called_once_with(
                mods_dir,
                cache_path=self.goa_status_cache_path,
            )

    def test_client_delete_save_files_and_backups_archives_active_saves_and_preserves_existing_backups(self) -> None:
        with TemporaryDirectory() as temp_dir:
            steam_id_dir = Path(temp_dir) / "00000000000000000000000000000000"
            save_dir = steam_id_dir / "SaveGames"
            backup_dir = steam_id_dir / "SaveGames-backup-test"
            save_dir.mkdir(parents=True)
            backup_dir.mkdir(parents=True)
            (save_dir / "KHIII_slot0.bin").write_text("slot", encoding="utf-8")
            (backup_dir / "KHIII_slot0.bin").write_text("backup", encoding="utf-8")

            result = KH3Context.delete_save_files_and_backups(save_dir)

            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["removed_files"], 0)
            self.assertEqual(result["archived_files"], 1)
            self.assertEqual(result["removed_backup_dirs"], 0)
            self.assertTrue(save_dir.exists())
            self.assertFalse((save_dir / "KHIII_slot0.bin").exists())
            self.assertTrue(backup_dir.exists())
            self.assertEqual((backup_dir / "KHIII_slot0.bin").read_text(encoding="utf-8"), "backup")

    def test_default_save_directory_uses_env_override(self) -> None:
        with TemporaryDirectory() as temp_dir:
            override = Path(temp_dir) / "CustomSaveGames"
            with patch.dict(os.environ, {"KH3_SAVE_DIR": str(override)}, clear=False):
                self.assertEqual(default_save_directory(), override.resolve())

    def test_default_save_directory_normalizes_real_steam_account_override(self) -> None:
        with TemporaryDirectory() as temp_dir:
            steam_root = Path(temp_dir) / "Documents" / "My Games" / "KINGDOM HEARTS III" / "Steam"
            real_save_dir = steam_root / "76561197982685695" / "SaveGames"
            shared_save_dir = steam_root / STEAM_SHARED_SAVE_ACCOUNT_ID / "SaveGames"
            with patch.dict(os.environ, {"KH3_SAVE_DIR": str(real_save_dir)}, clear=False):
                self.assertEqual(default_save_directory(), shared_save_dir.resolve())

    def test_default_save_directory_uses_shared_account_when_real_account_is_newer(self) -> None:
        with TemporaryDirectory() as temp_dir:
            user_profile = Path(temp_dir)
            steam_root = (
                user_profile
                / "Documents"
                / "My Games"
                / "KINGDOM HEARTS III"
                / "Steam"
            )
            old_save_dir = steam_root / "00000000000000000000000000000000" / "SaveGames"
            new_save_dir = steam_root / "76561197982685695" / "SaveGames"
            old_save_dir.mkdir(parents=True)
            new_save_dir.mkdir(parents=True)
            os.utime(old_save_dir, (1, 1))
            os.utime(new_save_dir, (2, 2))

            with patch.dict(os.environ, {"USERPROFILE": str(user_profile)}, clear=False):
                os.environ.pop("KH3_SAVE_DIR", None)
                self.assertEqual(default_save_directory(), old_save_dir.resolve())

    def test_default_save_directory_uses_shared_account_with_randomizer_activity(self) -> None:
        with TemporaryDirectory() as temp_dir:
            user_profile = Path(temp_dir)
            steam_root = (
                user_profile
                / "Documents"
                / "My Games"
                / "KINGDOM HEARTS III"
                / "Steam"
            )
            fallback_save_dir = steam_root / "00000000000000000000000000000000" / "SaveGames"
            steam_id_save_dir = steam_root / "76561197982685695" / "SaveGames"
            fallback_save_dir.mkdir(parents=True)
            steam_id_save_dir.mkdir(parents=True)
            fallback_events = fallback_save_dir / "RandomizerEvents.sav"
            steam_id_events = steam_id_save_dir / "RandomizerEvents.sav"
            fallback_events.write_text("active", encoding="utf-8")
            steam_id_events.write_text("stale", encoding="utf-8")
            os.utime(fallback_events, (20, 20))
            os.utime(steam_id_events, (10, 10))
            os.utime(fallback_save_dir, (1, 1))
            os.utime(steam_id_save_dir, (30, 30))

            with patch.dict(os.environ, {"USERPROFILE": str(user_profile)}, clear=False):
                os.environ.pop("KH3_SAVE_DIR", None)
                self.assertEqual(default_save_directory(), fallback_save_dir.resolve())

    def test_default_save_directory_ignores_real_account_steam_metadata(self) -> None:
        with TemporaryDirectory() as temp_dir:
            user_profile = Path(temp_dir)
            steam_root = (
                user_profile
                / "Documents"
                / "My Games"
                / "KINGDOM HEARTS III"
                / "Steam"
            )
            fallback_save_dir = steam_root / "00000000000000000000000000000000" / "SaveGames"
            steam_id_save_dir = steam_root / "76561197982685695" / "SaveGames"
            fallback_save_dir.mkdir(parents=True)
            steam_id_save_dir.mkdir(parents=True)
            steam_autocloud = steam_id_save_dir / "steam_autocloud.vdf"
            steam_autocloud.write_text("cloud", encoding="utf-8")
            os.utime(steam_autocloud, (20, 20))
            os.utime(fallback_save_dir, (30, 30))
            os.utime(steam_id_save_dir, (1, 1))

            with patch.dict(os.environ, {"USERPROFILE": str(user_profile)}, clear=False):
                os.environ.pop("KH3_SAVE_DIR", None)
                self.assertEqual(default_save_directory(), fallback_save_dir.resolve())

    def test_client_default_kh3_save_dir_uses_persisted_override(self) -> None:
        with TemporaryDirectory() as temp_dir:
            override = Path(temp_dir) / "EpicUser" / "SaveGames"
            with patch.dict(os.environ, {}, clear=True), patch(
                "worlds.kh3.platform_support.save_discovery.Utils.persistent_load",
                return_value={"kh3_client": {"save_dir": str(override)}},
            ):
                self.assertEqual(resolve_default_kh3_save_dir(), override.resolve())

    def test_client_default_kh3_save_dir_ignores_persisted_auto_save_path(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            kh3_root = root / "Steam" / "steamapps" / "common" / "KINGDOM HEARTS III" / "KINGDOM HEARTS III"
            (kh3_root / "Binaries" / "Win64").mkdir(parents=True)
            steam_save_dir = (
                root
                / "Documents"
                / "My Games"
                / "KINGDOM HEARTS III"
                / "Steam"
                / "00000000000000000000000000000000"
                / "SaveGames"
            )
            epic_save_dir = (
                root
                / "Documents"
                / "KINGDOM HEARTS III"
                / "Epic Games Store"
                / "9131d98cbe0347d68444577310d4e7d1"
                / "SaveGames"
            )
            steam_save_dir.mkdir(parents=True)
            epic_save_dir.mkdir(parents=True)

            with patch.dict(os.environ, {"USERPROFILE": str(root)}, clear=True), patch(
                "worlds.kh3.platform_support.save_discovery.Utils.persistent_load",
                return_value={"kh3_client": {"save_dir": str(epic_save_dir)}},
            ):
                self.assertTrue(is_kh3_auto_save_dir(epic_save_dir))
                self.assertEqual(resolve_default_kh3_save_dir(kh3_root), steam_save_dir.resolve())

    def test_client_default_kh3_save_dir_prefers_env_over_persisted_override(self) -> None:
        with TemporaryDirectory() as temp_dir:
            env_override = Path(temp_dir) / "EnvSaveGames"
            persisted_override = Path(temp_dir) / "PersistedSaveGames"
            with patch.dict(os.environ, {"KH3_SAVE_DIR": str(env_override)}, clear=True), patch(
                "worlds.kh3.platform_support.save_discovery.Utils.persistent_load",
                return_value={"kh3_client": {"save_dir": str(persisted_override)}},
            ):
                self.assertEqual(resolve_default_kh3_save_dir(), env_override.resolve())

    def test_client_resolves_steam_save_dir_to_shared_account_when_real_account_is_newer(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            kh3_root = root / "Steam" / "steamapps" / "common" / "KINGDOM HEARTS III" / "KINGDOM HEARTS III"
            (kh3_root / "Binaries" / "Win64").mkdir(parents=True)
            steam_root = root / "Documents" / "My Games" / "KINGDOM HEARTS III" / "Steam"
            old_save_dir = steam_root / "00000000000000000000000000000000" / "SaveGames"
            new_save_dir = steam_root / "76561197982685695" / "SaveGames"
            old_save_dir.mkdir(parents=True)
            new_save_dir.mkdir(parents=True)
            os.utime(old_save_dir, (1, 1))
            os.utime(new_save_dir, (2, 2))

            with patch.dict(os.environ, {"USERPROFILE": str(root)}, clear=True):
                self.assertEqual(resolve_kh3_save_dir_for_root(kh3_root), old_save_dir.resolve())

    def test_client_resolves_steam_save_dir_to_shared_account_despite_real_account_activity(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            kh3_root = root / "Steam" / "steamapps" / "common" / "KINGDOM HEARTS III" / "KINGDOM HEARTS III"
            (kh3_root / "Binaries" / "Win64").mkdir(parents=True)
            steam_root = root / "Documents" / "My Games" / "KINGDOM HEARTS III" / "Steam"
            fallback_save_dir = steam_root / "00000000000000000000000000000000" / "SaveGames"
            steam_id_save_dir = steam_root / "76561197982685695" / "SaveGames"
            fallback_save_dir.mkdir(parents=True)
            steam_id_save_dir.mkdir(parents=True)
            fallback_vbonus = fallback_save_dir / "RandomizerVBonus.sav"
            steam_id_vbonus = steam_id_save_dir / "RandomizerVBonus.sav"
            fallback_vbonus.write_text("stale", encoding="utf-8")
            steam_id_vbonus.write_text("active", encoding="utf-8")
            os.utime(fallback_vbonus, (10, 10))
            os.utime(steam_id_vbonus, (20, 20))
            os.utime(fallback_save_dir, (30, 30))
            os.utime(steam_id_save_dir, (1, 1))

            with patch.dict(os.environ, {"USERPROFILE": str(root)}, clear=True):
                self.assertEqual(resolve_kh3_save_dir_for_root(kh3_root), fallback_save_dir.resolve())

    def test_client_save_dir_input_accepts_steam_parent_root(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            steam_root = root / "Documents" / "My Games" / "KINGDOM HEARTS III" / "Steam"
            fallback_save_dir = steam_root / "00000000000000000000000000000000" / "SaveGames"
            steam_id_save_dir = steam_root / "76561197982685695" / "SaveGames"
            fallback_save_dir.mkdir(parents=True)
            steam_id_save_dir.mkdir(parents=True)
            fallback_vbonus = fallback_save_dir / "RandomizerVBonus.sav"
            fallback_vbonus.write_text("stale", encoding="utf-8")
            active_vbonus = steam_id_save_dir / "RandomizerVBonus.sav"
            active_vbonus.write_text("active", encoding="utf-8")
            os.utime(fallback_vbonus, (10, 10))
            os.utime(active_vbonus, (20, 20))

            self.assertTrue(is_kh3_save_dir_dynamic_root(steam_root))
            self.assertEqual(resolve_kh3_save_dir_input(steam_root), fallback_save_dir.resolve())

    def test_client_save_dir_input_normalizes_real_steam_account_root(self) -> None:
        with TemporaryDirectory() as temp_dir:
            account_root = (
                Path(temp_dir)
                / "Documents"
                / "My Games"
                / "KINGDOM HEARTS III"
                / "Steam"
                / "76561197982685695"
            )
            real_save_dir = account_root / "SaveGames"
            shared_save_dir = account_root.parent / STEAM_SHARED_SAVE_ACCOUNT_ID / "SaveGames"
            real_save_dir.mkdir(parents=True)
            shared_save_dir.mkdir(parents=True)

            self.assertTrue(is_kh3_save_dir_dynamic_root(account_root))
            self.assertEqual(resolve_kh3_save_dir_input(account_root), shared_save_dir.resolve())

    def test_client_save_dir_input_normalizes_exact_real_steam_savegames_path(self) -> None:
        with TemporaryDirectory() as temp_dir:
            steam_root = Path(temp_dir) / "Documents" / "My Games" / "KINGDOM HEARTS III" / "Steam"
            real_save_dir = steam_root / "76561197982685695" / "SaveGames"
            shared_save_dir = steam_root / STEAM_SHARED_SAVE_ACCOUNT_ID / "SaveGames"
            real_save_dir.mkdir(parents=True)
            shared_save_dir.mkdir(parents=True)

            self.assertEqual(resolve_kh3_save_dir_input(real_save_dir), shared_save_dir.resolve())

    def test_client_steam_save_dir_always_uses_shared_account(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            steam_root = root / "Steam"
            kh3_root = steam_root / "steamapps" / "common" / "KINGDOM HEARTS III" / "KINGDOM HEARTS III"
            (kh3_root / "Binaries" / "Win64").mkdir(parents=True)
            steam_config = steam_root / "config"
            steam_config.mkdir(parents=True)
            (steam_config / "loginusers.vdf").write_text(
                '"users"\n'
                "{\n"
                '    "76561111111111111"\n'
                "    {\n"
                '        "MostRecent" "0"\n'
                '        "Timestamp" "200"\n'
                "    }\n"
                '    "76561197982685695"\n'
                "    {\n"
                '        "MostRecent" "1"\n'
                '        "Timestamp" "100"\n'
                "    }\n"
                "}\n",
                encoding="utf-8",
            )
            save_root = root / "Documents" / "My Games" / "KINGDOM HEARTS III" / "Steam"
            fallback_save_dir = save_root / "00000000000000000000000000000000" / "SaveGames"
            steam_id_save_dir = save_root / "76561197982685695" / "SaveGames"
            fallback_save_dir.mkdir(parents=True)
            steam_id_save_dir.mkdir(parents=True)
            fallback_vbonus = fallback_save_dir / "RandomizerVBonus.sav"
            fallback_vbonus.write_text("stale-but-newer", encoding="utf-8")
            os.utime(fallback_vbonus, (999, 999))

            with patch.dict(os.environ, {"USERPROFILE": str(root)}, clear=True):
                self.assertEqual(resolve_kh3_save_dir_for_root(kh3_root), fallback_save_dir.resolve())

    def test_client_steam_account_setting_does_not_override_shared_save_account(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            kh3_root = root / "Steam" / "steamapps" / "common" / "KINGDOM HEARTS III" / "KINGDOM HEARTS III"
            (kh3_root / "Binaries" / "Win64").mkdir(parents=True)
            save_root = root / "Documents" / "My Games" / "KINGDOM HEARTS III" / "Steam"
            real_account_save_dir = save_root / "76561197982685695" / "SaveGames"
            expected_save_dir = save_root / "00000000000000000000000000000000" / "SaveGames"
            real_account_save_dir.mkdir(parents=True)
            expected_save_dir.mkdir(parents=True)

            with patch.dict(
                os.environ,
                {"USERPROFILE": str(root), "KH3_STEAM_ACCOUNT_ID": "76561197982685695"},
                clear=True,
            ):
                self.assertEqual(resolve_kh3_save_dir_for_root(kh3_root), expected_save_dir.resolve())

    def test_client_resolves_existing_steam_save_dir_for_secondary_library(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            primary_steam = root / "PrimarySteam"
            secondary_library = root / "SteamLibrary"
            kh3_root = secondary_library / "steamapps" / "common" / "KINGDOM HEARTS III" / "KINGDOM HEARTS III"
            (kh3_root / "Binaries" / "Win64").mkdir(parents=True)
            primary_steamapps = primary_steam / "steamapps"
            primary_steamapps.mkdir(parents=True)
            secondary_library_vdf_path = str(secondary_library).replace("\\", "\\\\")
            (primary_steamapps / "libraryfolders.vdf").write_text(
                '"libraryfolders"\n'
                "{\n"
                '    "1"\n'
                "    {\n"
                f'        "path" "{secondary_library_vdf_path}"\n'
                "    }\n"
                "}\n",
                encoding="utf-8",
            )
            steam_config = primary_steam / "config"
            steam_config.mkdir(parents=True)
            (steam_config / "loginusers.vdf").write_text(
                '"users"\n'
                "{\n"
                '    "76561197982685695"\n'
                "    {\n"
                '        "MostRecent" "1"\n'
                "    }\n"
                "}\n",
                encoding="utf-8",
            )
            expected_save_dir = (
                root
                / "Documents"
                / "My Games"
                / "KINGDOM HEARTS III"
                / "Steam"
                / "00000000000000000000000000000000"
                / "SaveGames"
            )
            expected_save_dir.mkdir(parents=True)

            with patch.dict(
                os.environ,
                {"USERPROFILE": str(root), "KH3_STEAMAPPS_DIR": str(primary_steamapps)},
                clear=True,
            ):
                self.assertEqual(resolve_kh3_save_dir_for_root(kh3_root), expected_save_dir.resolve())

    def test_client_steam_save_dir_detection_skips_expensive_egs_signature_scan(self) -> None:
        with TemporaryDirectory() as temp_dir:
            kh3_root = Path(temp_dir) / "SteamLibrary" / "steamapps" / "common" / "KINGDOM HEARTS III" / "KINGDOM HEARTS III"
            (kh3_root / "Binaries" / "Win64").mkdir(parents=True)

            with patch(
                "worlds.kh3.platform_support.game_discovery.resolve_epic_launch_uri_for_kh3_executable"
            ) as epic_manifest, patch(
                "worlds.kh3.platform_support.game_discovery.kh3_executable_contains_signature"
            ) as signature_scan:
                self.assertFalse(is_epic_kh3_root_dir(kh3_root))

            epic_manifest.assert_not_called()
            signature_scan.assert_not_called()

    def test_client_detects_explicit_kh3_steam_cloud_state(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "Steam"
            steamapps = root / "steamapps"
            kh3_root = steamapps / "common" / "KINGDOM HEARTS III" / "KINGDOM HEARTS III"
            (kh3_root / "Binaries" / "Win64").mkdir(parents=True)
            loginusers = root / "config" / "loginusers.vdf"
            loginusers.parent.mkdir(parents=True)
            loginusers.write_text(
                '"users"\n{\n"76561197982685695"\n{\n"MostRecent" "1"\n"Timestamp" "10"\n}\n}\n',
                encoding="utf-8",
            )
            sharedconfig = root / "userdata" / "22419967" / "7" / "remote" / "sharedconfig.vdf"
            sharedconfig.parent.mkdir(parents=True)

            def write_cloud_value(value: str | None) -> None:
                setting = f'"cloudenabled" "{value}"' if value is not None else '"other" "1"'
                sharedconfig.write_text(
                    f'"UserRoamingConfigStore"\n{{\n"Software"\n{{\n"Valve"\n{{\n"Steam"\n{{\n'
                    f'"apps"\n{{\n"2552450"\n{{\n{setting}\n}}\n}}\n}}\n}}\n}}\n}}\n',
                    encoding="utf-8",
                )

            write_cloud_value("0")
            self.assertEqual(detect_kh3_steam_cloud_state(kh3_root), SteamCloudState.DISABLED)
            write_cloud_value("1")
            self.assertEqual(detect_kh3_steam_cloud_state(kh3_root), SteamCloudState.ENABLED)
            write_cloud_value(None)
            self.assertEqual(detect_kh3_steam_cloud_state(kh3_root), SteamCloudState.UNKNOWN)

    def test_client_disables_steam_cloud_through_remote_storage_api(self) -> None:
        cloud_enabled = True
        set_calls: list[bool] = []
        shutdown_calls = 0

        class FakeFunction:
            def __init__(self, callback):
                self.callback = callback
                self.argtypes = None
                self.restype = None

            def __call__(self, *args):
                return self.callback(*args)

        def set_cloud_enabled(_remote_storage, enabled: bool) -> None:
            nonlocal cloud_enabled
            cloud_enabled = bool(enabled)
            set_calls.append(bool(enabled))

        def shutdown() -> None:
            nonlocal shutdown_calls
            shutdown_calls += 1

        fake_api = type("FakeSteamApi", (), {})()
        fake_api.SteamAPI_Init = FakeFunction(lambda: True)
        fake_api.SteamAPI_Shutdown = FakeFunction(shutdown)
        fake_api.SteamAPI_SteamRemoteStorage_v016 = FakeFunction(lambda: 0x1234)
        fake_api.SteamAPI_ISteamRemoteStorage_IsCloudEnabledForApp = FakeFunction(
            lambda _remote_storage: cloud_enabled
        )
        fake_api.SteamAPI_ISteamRemoteStorage_SetCloudEnabledForApp = FakeFunction(set_cloud_enabled)

        changed = steam_cloud._disable_cloud_with_loaded_steam_api(fake_api)
        already_disabled = steam_cloud._disable_cloud_with_loaded_steam_api(fake_api)

        self.assertEqual(changed.status, SteamCloudDisableStatus.DISABLED)
        self.assertTrue(changed.succeeded)
        self.assertTrue(changed.changed)
        self.assertEqual(already_disabled.status, SteamCloudDisableStatus.ALREADY_DISABLED)
        self.assertTrue(already_disabled.succeeded)
        self.assertFalse(already_disabled.changed)
        self.assertFalse(cloud_enabled)
        self.assertEqual(set_calls, [False, False])
        self.assertEqual(shutdown_calls, 2)

    def test_client_runs_steam_api_only_in_short_lived_helper_process(self) -> None:
        events: list[str] = []

        class FakeReceiver:
            def poll(self, timeout_seconds: float) -> bool:
                self.timeout_seconds = timeout_seconds
                return True

            @staticmethod
            def recv() -> tuple[str, str]:
                return SteamCloudDisableStatus.DISABLED.value, ""

            def close(self) -> None:
                events.append("receiver_closed")

        class FakeSender:
            def close(self) -> None:
                events.append("sender_closed")

        class FakeProcess:
            def __init__(self, **kwargs) -> None:
                self.kwargs = kwargs

            def start(self) -> None:
                events.append("started")

            def join(self, timeout: float) -> None:
                events.append(f"joined:{timeout}")

            @staticmethod
            def is_alive() -> bool:
                return False

            def close(self) -> None:
                events.append("process_closed")

        receiver = FakeReceiver()
        sender = FakeSender()
        process: FakeProcess | None = None

        class FakeContext:
            @staticmethod
            def Pipe(*, duplex: bool):
                self.assertFalse(duplex)
                return receiver, sender

            def Process(self, **kwargs):
                nonlocal process
                process = FakeProcess(**kwargs)
                return process

        with patch.object(
            steam_cloud.multiprocessing,
            "get_context",
            return_value=FakeContext(),
        ) as get_context, patch.object(steam_cloud, "_load_steam_api64") as load_steam_api:
            result = steam_cloud._disable_cloud_in_helper_process(Path("steam_api64.dll"))

        self.assertEqual(result.status, SteamCloudDisableStatus.DISABLED)
        get_context.assert_called_once_with("spawn")
        load_steam_api.assert_not_called()
        self.assertIsNotNone(process)
        self.assertIs(process.kwargs["target"], steam_cloud._steam_cloud_disable_worker)
        self.assertEqual(process.kwargs["name"], "KH3SteamCloudHelper")
        self.assertEqual(process.kwargs["args"], ("steam_api64.dll", sender))
        self.assertEqual(events, ["started", "sender_closed", "receiver_closed", "joined:1.0", "process_closed"])

    def test_client_uses_newest_bundled_steam_api(self) -> None:
        with TemporaryDirectory() as temp_dir:
            outer_root = Path(temp_dir) / "steamapps" / "common" / "KINGDOM HEARTS III"
            kh3_root = outer_root / "KINGDOM HEARTS III"
            older = (
                kh3_root
                / "Engine"
                / "Binaries"
                / "ThirdParty"
                / "Steamworks"
                / "Steamv150"
                / "Win64"
                / "steam_api64.dll"
            )
            newest = (
                outer_root
                / "Engine"
                / "Binaries"
                / "ThirdParty"
                / "Steamworks"
                / "Steamv153"
                / "Win64"
                / "steam_api64.dll"
            )
            older.parent.mkdir(parents=True)
            newest.parent.mkdir(parents=True)
            older.touch()
            newest.touch()

            self.assertEqual(steam_cloud._steam_api64_path_for_kh3_root(kh3_root), newest)

    def test_client_parses_steam_cloud_config_and_userdata_account(self) -> None:
        self.assertEqual(steam_userdata_account_id("76561197982685695"), "22419967")
        self.assertIsNone(steam_userdata_account_id("not-a-steam-id"))
        self.assertEqual(
            steam_cloud_state_from_sharedconfig(
                '"store" { "Apps" { "2552450" { "CloudEnabled" "false" } } }'
            ),
            SteamCloudState.DISABLED,
        )

    def test_client_warns_about_enabled_or_unknown_steam_cloud_in_yellow(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            steam_root = root / "SteamLibrary" / "steamapps" / "common" / "KINGDOM HEARTS III"
            epic_root = root / "Epic Games" / "KINGDOM HEARTS III"
            ctx = KH3Context.__new__(KH3Context)
            messages: list[dict] = []
            ctx.on_print_json = messages.append

            ctx.kh3_dir = epic_root
            self.assertFalse(ctx.warn_about_steam_cloud_if_needed())
            self.assertEqual(messages, [])

            ctx.kh3_dir = steam_root
            with patch(
                "worlds.kh3.Client.detect_kh3_steam_cloud_state",
                return_value=SteamCloudState.DISABLED,
            ):
                self.assertFalse(ctx.warn_about_steam_cloud_if_needed())
            self.assertEqual(messages, [])

            with patch(
                "worlds.kh3.Client.detect_kh3_steam_cloud_state",
                return_value=SteamCloudState.ENABLED,
            ):
                self.assertTrue(ctx.warn_about_steam_cloud_if_needed())
                self.assertFalse(ctx.warn_about_steam_cloud_if_needed())
            self.assertEqual(messages[0]["data"][0]["color"], "yellow")
            self.assertIn("disable Steam Cloud", messages[0]["data"][0]["text"])
            self.assertEqual(len(messages), 1)

            with patch(
                "worlds.kh3.Client.detect_kh3_steam_cloud_state",
                return_value=SteamCloudState.UNKNOWN,
            ):
                self.assertTrue(ctx.warn_about_steam_cloud_if_needed())
            self.assertEqual(messages[1]["data"][0]["color"], "yellow")
            self.assertIn("could not verify", messages[1]["data"][0]["text"])

    def test_patch_game_disables_cloud_only_for_steam_and_remembers_success(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            steam_root = root / "SteamLibrary" / "steamapps" / "common" / "KINGDOM HEARTS III"
            epic_root = root / "Epic Games" / "KINGDOM HEARTS III"
            steam = KH3Context.__new__(KH3Context)
            steam.kh3_dir = steam_root
            steam._get_steam_cloud_service().warning_key = ("old", SteamCloudState.ENABLED)
            steam.steam_cloud_disabled_acknowledged = False
            epic = KH3Context.__new__(KH3Context)
            epic.kh3_dir = epic_root

            success = SteamCloudDisableResult(SteamCloudDisableStatus.DISABLED)
            with patch("worlds.kh3.Client.disable_kh3_steam_cloud", return_value=success) as disable, patch(
                "worlds.kh3.Client.Utils.persistent_store"
            ) as store:
                self.assertTrue(steam.disable_steam_cloud_during_patch_game())
                self.assertTrue(epic.disable_steam_cloud_during_patch_game())

            disable.assert_called_once_with(steam_root.resolve())
            store.assert_called_once_with("kh3_client", KH3_STEAM_CLOUD_ACKNOWLEDGED_KEY, True)
            self.assertTrue(steam.steam_cloud_disabled_acknowledged)
            self.assertIsNone(steam._get_steam_cloud_service().warning_key)

    def test_patch_game_continues_when_steam_cloud_disable_fails(self) -> None:
        with TemporaryDirectory() as temp_dir:
            steam = KH3Context.__new__(KH3Context)
            steam.kh3_dir = (
                Path(temp_dir) / "SteamLibrary" / "steamapps" / "common" / "KINGDOM HEARTS III"
            )
            steam._get_steam_cloud_service().warning_key = None
            steam.steam_cloud_disabled_acknowledged = True
            steam.warn_about_steam_cloud_if_needed = lambda: True

            with patch(
                "worlds.kh3.Client.disable_kh3_steam_cloud",
                side_effect=RuntimeError("test failure"),
            ), patch("worlds.kh3.Client.Utils.persistent_store") as store:
                self.assertFalse(steam.disable_steam_cloud_during_patch_game())

            store.assert_called_once_with("kh3_client", KH3_STEAM_CLOUD_ACKNOWLEDGED_KEY, False)
            self.assertFalse(steam.steam_cloud_disabled_acknowledged)

    def test_client_persists_one_global_steam_cloud_confirmation(self) -> None:
        with TemporaryDirectory() as temp_dir:
            steam_root = Path(temp_dir) / "SteamLibrary" / "steamapps" / "common" / "KINGDOM HEARTS III"
            persisted: dict[str, bool] = {}
            first_prompt = AsyncMock(return_value=True)
            second_prompt = AsyncMock(return_value=True)

            first = KH3Context.__new__(KH3Context)
            first.kh3_dir = steam_root
            first.ui = type("SteamCloudUI", (), {
                "prompt_steam_cloud_disabled_confirmation": first_prompt,
            })()
            first.steam_cloud_disabled_acknowledged = False

            second = KH3Context.__new__(KH3Context)
            second.kh3_dir = steam_root
            second.ui = type("SteamCloudUI", (), {
                "prompt_steam_cloud_disabled_confirmation": second_prompt,
            })()
            second.steam_cloud_disabled_acknowledged = False

            def persistent_load() -> dict[str, dict[str, bool]]:
                return {"kh3_client": persisted}

            def persistent_store(category: str, key: str, value: bool) -> None:
                self.assertEqual(category, "kh3_client")
                persisted[key] = value

            async def run_case() -> tuple[bool, bool]:
                return (
                    await first.confirm_steam_cloud_disabled_before_connect(),
                    await second.confirm_steam_cloud_disabled_before_connect(),
                )

            with patch("worlds.kh3.Client.Utils.persistent_load", side_effect=persistent_load), patch(
                "worlds.kh3.Client.Utils.persistent_store",
                side_effect=persistent_store,
            ) as store:
                first_result, second_result = asyncio.run(run_case())

            self.assertTrue(first_result)
            self.assertTrue(second_result)
            first_prompt.assert_awaited_once_with()
            second_prompt.assert_not_awaited()
            store.assert_called_once_with("kh3_client", KH3_STEAM_CLOUD_ACKNOWLEDGED_KEY, True)
            self.assertIs(persisted[KH3_STEAM_CLOUD_ACKNOWLEDGED_KEY], True)

    def test_client_cancels_unacknowledged_steam_prompt_and_skips_it_for_epic(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            steam_prompt = AsyncMock(return_value=False)
            steam = KH3Context.__new__(KH3Context)
            steam.kh3_dir = root / "SteamLibrary" / "steamapps" / "common" / "KINGDOM HEARTS III"
            steam.ui = type("SteamCloudUI", (), {
                "prompt_steam_cloud_disabled_confirmation": steam_prompt,
            })()
            steam.steam_cloud_disabled_acknowledged = False

            epic_prompt = AsyncMock(return_value=True)
            epic = KH3Context.__new__(KH3Context)
            epic.kh3_dir = root / "Epic Games" / "KINGDOM HEARTS III"
            epic.ui = type("SteamCloudUI", (), {
                "prompt_steam_cloud_disabled_confirmation": epic_prompt,
            })()

            with patch("worlds.kh3.Client.Utils.persistent_load", return_value={"kh3_client": {}}), patch(
                "worlds.kh3.Client.Utils.persistent_store"
            ) as store:
                steam_result = asyncio.run(steam.confirm_steam_cloud_disabled_before_connect())
                epic_result = asyncio.run(epic.confirm_steam_cloud_disabled_before_connect())

            self.assertFalse(steam_result)
            self.assertTrue(epic_result)
            steam_prompt.assert_awaited_once_with()
            epic_prompt.assert_not_awaited()
            store.assert_not_called()

    def test_client_resolves_epic_save_dir_from_kh3_root(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            kh3_root = root / "EGS" / "KH_3" / "KINGDOM HEARTS III"
            executable = kh3_root / "Binaries" / "Win64" / "KINGDOM HEARTS III.exe"
            executable.parent.mkdir(parents=True)
            executable.write_bytes(bytes.fromhex(
                "48 8D 3D 11 22 33 44 48 89 78 08 48 8B CF FF 15 "
                "55 66 77 88 85 C0 75 0A 48 8B CF FF 15"
            ))
            epic_root = root / "Documents" / "KINGDOM HEARTS III" / "Epic Games Store"
            save_dir = epic_root / "9131d98cbe0347d68444577310d4e7d1" / "SaveGames"
            save_dir.mkdir(parents=True)

            with patch.dict(os.environ, {"USERPROFILE": str(root)}, clear=True):
                self.assertEqual(resolve_kh3_save_dir_for_root(kh3_root), save_dir.resolve())

    def test_client_set_kh3_dir_updates_auto_save_dir_without_clobbering_override(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            auto_save_dir = root / "Auto" / "SaveGames"
            manual_save_dir = root / "Manual" / "SaveGames"
            kh3_root = root / "KINGDOM HEARTS III"
            ctx = KH3Context.__new__(KH3Context)
            ctx.ue4ss_log_override = False
            ctx.probe_config_override = False
            ctx.kh3_mods_dir_override = False
            ctx.save_dir_override = False
            ctx.bridge_save_dir = manual_save_dir
            ctx.bridge_dir = Path("bridge")

            with patch("worlds.kh3.Client.resolve_kh3_save_dir_for_root", return_value=auto_save_dir.resolve()):
                ctx.set_kh3_dir(kh3_root, persist=False)

            self.assertEqual(ctx.bridge_save_dir, auto_save_dir.resolve())

            ctx.save_dir_override = True
            ctx.bridge_save_dir = manual_save_dir.resolve()
            with patch("worlds.kh3.Client.resolve_kh3_save_dir_for_root", return_value=auto_save_dir.resolve()):
                ctx.set_kh3_dir(kh3_root, persist=False)

            self.assertEqual(ctx.bridge_save_dir, manual_save_dir.resolve())

    def test_client_set_kh3_dir_clears_persisted_auto_save_override(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            old_auto_save_dir = (
                root
                / "Documents"
                / "KINGDOM HEARTS III"
                / "Epic Games Store"
                / "9131d98cbe0347d68444577310d4e7d1"
                / "SaveGames"
            )
            new_auto_save_dir = root / "SteamSave" / "SaveGames"
            old_auto_save_dir.mkdir(parents=True)
            kh3_root = root / "KINGDOM HEARTS III"
            ctx = KH3Context.__new__(KH3Context)
            ctx.ue4ss_log_override = False
            ctx.probe_config_override = False
            ctx.kh3_mods_dir_override = False
            ctx.save_dir_override = True
            ctx.bridge_save_dir = old_auto_save_dir.resolve()
            ctx.bridge_dir = Path("bridge")

            with patch.dict(os.environ, {"USERPROFILE": str(root)}, clear=True), \
                    patch("worlds.kh3.Client.resolve_kh3_save_dir_for_root", return_value=new_auto_save_dir.resolve()), \
                    patch("worlds.kh3.Client.Utils.persistent_store") as persistent_store:
                ctx.set_kh3_dir(kh3_root, persist=True)

            self.assertEqual(ctx.bridge_save_dir, new_auto_save_dir.resolve())
            self.assertFalse(ctx.save_dir_override)
            persistent_store.assert_any_call("kh3_client", "save_dir", "")

    def test_client_save_dir_override_can_be_set_and_cleared(self) -> None:
        with TemporaryDirectory() as temp_dir:
            manual_save_dir = Path(temp_dir) / "Manual" / "SaveGames"
            auto_save_dir = Path(temp_dir) / "Auto" / "SaveGames"
            ctx = KH3Context.__new__(KH3Context)
            ctx.integrated_bridge_enabled = False
            ctx.bridge_dir = None

            with patch("worlds.kh3.Client.Utils.persistent_store") as persistent_store:
                ctx.set_kh3_save_dir(manual_save_dir, persist=True)

            self.assertEqual(ctx.bridge_save_dir, manual_save_dir.resolve())
            self.assertTrue(ctx.save_dir_override)
            persistent_store.assert_called_once_with("kh3_client", "save_dir", str(manual_save_dir.resolve()))

            ctx.kh3_dir = Path(temp_dir) / "KINGDOM HEARTS III"
            with patch("worlds.kh3.Client.resolve_kh3_save_dir_for_root", return_value=auto_save_dir.resolve()), patch(
                "worlds.kh3.Client.Utils.persistent_store"
            ) as persistent_store:
                ctx.clear_kh3_save_dir_override(persist=True)

            self.assertEqual(ctx.bridge_save_dir, auto_save_dir.resolve())
            self.assertFalse(ctx.save_dir_override)
            persistent_store.assert_called_once_with("kh3_client", "save_dir", "")

    def test_client_save_directory_change_restarts_bridge_after_orderly_shutdown(self) -> None:
        async def run_case() -> tuple[list[str], bool]:
            ctx = KH3Context.__new__(KH3Context)
            ctx.integrated_bridge_enabled = True
            ctx.bridge_dir = Path("synthetic-bridge")
            ctx.bridge_save_dir = Path("new-save-context")
            ctx._lifecycle_state().save_directory_restart_pending = False
            order: list[str] = []

            async def wait_for_shutdown() -> None:
                order.append("stop")
                await asyncio.sleep(0)

            def start_bridge() -> None:
                order.append(f"start:{ctx.bridge_save_dir}")

            ctx.wait_for_integrated_bridge_shutdown = wait_for_shutdown
            ctx.start_integrated_bridge = start_bridge
            with patch("worlds.kh3.Client.is_process_running", return_value=False):
                ctx._schedule_integrated_bridge_restart_for_save_directory_change()
                while ctx._lifecycle_state().save_directory_restart_pending:
                    await asyncio.sleep(0)
            return order, ctx._lifecycle_state().save_directory_restart_pending

        order, pending = asyncio.run(run_case())

        self.assertEqual(order, ["stop", "start:new-save-context"])
        self.assertFalse(pending)

    def test_client_save_directory_change_does_not_restart_bridge_while_kh3_runs(self) -> None:
        ctx = KH3Context.__new__(KH3Context)
        ctx.integrated_bridge_enabled = True
        ctx.bridge_dir = Path("synthetic-bridge")
        ctx.bridge_save_dir = Path("new-save-context")
        ctx._lifecycle_state().save_directory_restart_pending = False

        with patch("worlds.kh3.Client.is_process_running", return_value=True), patch(
            "worlds.kh3.Client.Utils.async_start"
        ) as async_start:
            with self.assertLogs("Client", level="WARNING"):
                ctx._schedule_integrated_bridge_restart_for_save_directory_change()

        async_start.assert_not_called()
        self.assertFalse(ctx._lifecycle_state().save_directory_restart_pending)

    def test_client_save_dir_override_parent_root_tracks_dynamic_root(self) -> None:
        with TemporaryDirectory() as temp_dir:
            steam_root = Path(temp_dir) / "Documents" / "My Games" / "KINGDOM HEARTS III" / "Steam"
            fallback_save_dir = steam_root / "00000000000000000000000000000000" / "SaveGames"
            steam_id_save_dir = steam_root / "76561197982685695" / "SaveGames"
            fallback_save_dir.mkdir(parents=True)
            steam_id_save_dir.mkdir(parents=True)
            active_vbonus = steam_id_save_dir / "RandomizerVBonus.sav"
            active_vbonus.write_text("active", encoding="utf-8")
            os.utime(active_vbonus, (20, 20))
            ctx = KH3Context.__new__(KH3Context)
            ctx.integrated_bridge_enabled = False
            ctx.bridge_dir = None

            with patch("worlds.kh3.Client.Utils.persistent_store") as persistent_store:
                ctx.set_kh3_save_dir(steam_root, persist=True)

            self.assertEqual(ctx.bridge_save_dir, fallback_save_dir.resolve())
            self.assertTrue(ctx.save_dir_override)
            self.assertTrue(ctx.save_dir_dynamic_root_override)
            persistent_store.assert_called_once_with("kh3_client", "save_dir", str(steam_root.resolve()))

    def test_client_display_save_dir_hides_steam_account_for_auto_detect(self) -> None:
        with TemporaryDirectory() as temp_dir:
            steam_root = Path(temp_dir) / "Documents" / "My Games" / "KINGDOM HEARTS III" / "Steam"
            save_dir = steam_root / "76561197982685695" / "SaveGames"
            save_dir.mkdir(parents=True)

            ctx = KH3Context.__new__(KH3Context)
            ctx.bridge_save_dir = save_dir
            ctx.save_dir_override = False
            ctx.save_dir_dynamic_root_override = False

            self.assertEqual(ctx.display_kh3_save_dir(), steam_root.resolve())

    def test_client_display_save_dir_keeps_exact_savegames_override(self) -> None:
        with TemporaryDirectory() as temp_dir:
            steam_root = Path(temp_dir) / "Documents" / "My Games" / "KINGDOM HEARTS III" / "Steam"
            save_dir = steam_root / "76561197982685695" / "SaveGames"
            save_dir.mkdir(parents=True)

            ctx = KH3Context.__new__(KH3Context)
            ctx.bridge_save_dir = save_dir
            ctx.save_dir_override = True
            ctx.save_dir_dynamic_root_override = False

            self.assertEqual(ctx.display_kh3_save_dir(), save_dir.resolve())

    def test_client_display_save_dir_hides_steam_account_for_dynamic_override(self) -> None:
        with TemporaryDirectory() as temp_dir:
            steam_root = Path(temp_dir) / "Documents" / "My Games" / "KINGDOM HEARTS III" / "Steam"
            save_dir = steam_root / "76561197982685695" / "SaveGames"
            save_dir.mkdir(parents=True)

            ctx = KH3Context.__new__(KH3Context)
            ctx.bridge_save_dir = save_dir
            ctx.save_dir_override = True
            ctx.save_dir_dynamic_root_override = True

            self.assertEqual(ctx.display_kh3_save_dir(), steam_root.resolve())

    def test_client_display_save_dir_keeps_epic_account_for_auto_detect(self) -> None:
        with TemporaryDirectory() as temp_dir:
            epic_root = Path(temp_dir) / "Documents" / "My Games" / "KINGDOM HEARTS III" / "Epic Games Store"
            save_dir = epic_root / "9131d98cbe0347d68444577310d4e7d1" / "SaveGames"
            save_dir.mkdir(parents=True)

            ctx = KH3Context.__new__(KH3Context)
            ctx.bridge_save_dir = save_dir
            ctx.save_dir_override = False
            ctx.save_dir_dynamic_root_override = False

            self.assertEqual(ctx.display_kh3_save_dir(), save_dir.resolve())

    def test_client_default_kh3_mods_dir_uses_content_paks_mods(self) -> None:
        with TemporaryDirectory() as temp_dir:
            game_root = Path(temp_dir) / "KINGDOM HEARTS III"
            win64_dir = game_root / "Binaries" / "Win64"
            expected = game_root / "Content" / "Paks" / "~mods"

            with patch.dict(os.environ, {"KH3_WIN64_DIR": str(win64_dir)}, clear=False):
                os.environ.pop("KH3_MODS_DIR", None)
                self.assertEqual(resolve_default_kh3_mods_dir(), expected.resolve())


    def test_client_caches_installed_seed_pak_metadata_across_reinstall_until_pak_changes(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            mods_dir = root / "~mods"
            mods_dir.mkdir()
            pak_path = mods_dir / "pakchunk9-Sora-randomizer-AP_123456789-1_99_P.pak"
            managed_pak = root / "managed" / pak_path.name
            managed_pak.parent.mkdir()
            managed_pak.write_bytes(b"seed-pak")
            shutil.copy2(managed_pak, pak_path)
            cache_path = root / "cache" / "seed-pak-metadata.json"
            metadata = {
                "format": "kh3_ap_seed_metadata",
                "seed": "AP_123456789",
                "slot": 1,
                "slot_name": "Sora",
            }

            with patch.object(
                seed_paks,
                "extract_kh3_ap_seed_metadata_from_pak",
                return_value=metadata,
            ) as extract_metadata:
                first = seed_paks.load_installed_kh3_ap_seed_metadata(
                    mods_dir,
                    expected_seed="123456789",
                    metadata_cache_path=cache_path,
                )
                second = seed_paks.load_installed_kh3_ap_seed_metadata(
                    mods_dir,
                    expected_seed="123456789",
                    metadata_cache_path=cache_path,
                )
                pak_path.unlink()
                shutil.copy2(managed_pak, pak_path)
                third = seed_paks.load_installed_kh3_ap_seed_metadata(
                    mods_dir,
                    expected_seed="123456789",
                    metadata_cache_path=cache_path,
                )
                pak_path.write_bytes(b"changed-seed-pak")
                fourth = seed_paks.load_installed_kh3_ap_seed_metadata(
                    mods_dir,
                    expected_seed="123456789",
                    metadata_cache_path=cache_path,
                )

            self.assertEqual(first, (metadata, pak_path.resolve()))
            self.assertEqual(second, first)
            self.assertEqual(third, first)
            self.assertEqual(fourth, first)
            self.assertEqual(extract_metadata.call_count, 2)
            self.assertTrue(cache_path.is_file())


    def test_metadata_identity_accepts_sanitized_names_without_weakening_seed_or_slot(self) -> None:
        cases = (
            ("Shit Dom Fart", "Shit_Dom_Fart"),
            ("Player: /Name?", "Player_Name"),
            ("..Player__", "Player"),
            ("\u65e5\u672c", "Player"),
            ("Player_Name-2.0", "Player_Name-2.0"),
        )
        for name, filename_name in cases:
            with self.subTest(name=name):
                pak = f"pakchunk9-{filename_name}-randomizer-AP_60034101943795214820-2_99_P.pak"
                metadata = {
                    "format": "kh3_ap_seed_metadata", "seed": "AP_60034101943795214820",
                    "slot": 2, "slot_name": name,
                }
                self.assertTrue(seed_paks.kh3_ap_seed_metadata_matches_pak(metadata, pak))
                for changes in ({"slot": 3}, {"slot": True}, {"seed": "AP_99999999999999999999"},
                                {"slot_name": "Different Player"}, {"slot_name": ""}):
                    self.assertFalse(seed_paks.kh3_ap_seed_metadata_matches_pak(metadata | changes, pak))
                self.assertEqual(metadata["slot_name"], name)


    def test_client_open_kh3_mods_dir_creates_folder_before_opening(self) -> None:
        with TemporaryDirectory() as temp_dir:
            ctx = KH3Context.__new__(KH3Context)
            ctx.kh3_mods_dir = Path(temp_dir) / "KINGDOM HEARTS III" / "Content" / "Paks" / "~mods"

            with patch("worlds.kh3.Client.Utils.open_file") as open_file:
                ctx.open_kh3_mods_dir()

            self.assertTrue(ctx.kh3_mods_dir.is_dir())
            open_file.assert_called_once_with(ctx.kh3_mods_dir)

    def test_client_open_bridge_dir_uses_bound_context_method(self) -> None:
        with TemporaryDirectory() as temp_dir:
            ctx = KH3Context.__new__(KH3Context)
            ctx.bridge_dir = Path(temp_dir) / "kh3" / "bridge" / "seed_player"
            ctx.bridge_dir.mkdir(parents=True)

            with patch("worlds.kh3.Client.Utils.open_file") as open_file:
                ctx.open_bridge_dir()

            open_file.assert_called_once_with(ctx.bridge_dir)

    def _pin_goa_test_payload(self, data: bytes = b"goa") -> None:
        cache_dir = TemporaryDirectory()
        self.addCleanup(cache_dir.cleanup)
        patches = (
            patch.object(goa_download, "KH3_GOA_MOD_SIZE", len(data)),
            patch.object(goa_download, "KH3_GOA_MOD_SHA256", hashlib.sha256(data).hexdigest().upper()),
            patch("worlds.kh3.client_support.command_executor.goa_mod_status_cache_path",
                  return_value=Path(cache_dir.name) / "goa-status.json"),
        )
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

    def test_client_download_goa_mod_uses_pinned_asset_without_metadata_request(self) -> None:
        self._pin_goa_test_payload()
        with TemporaryDirectory() as temp_dir:
            mods_dir = Path(temp_dir) / "~mods"
            ctx = KH3Context.__new__(KH3Context)
            ctx.kh3_mods_dir = mods_dir
            with patch.object(goa_download.urllib.request, "urlopen", return_value=BytesIO(b"goa")) as urlopen:
                ctx.download_goa_mod()
            urlopen.assert_called_once()
            self.assertEqual(urlopen.call_args.args[0].full_url, goa_download.KH3_GOA_MOD_DOWNLOAD_URL)
            self.assertIn(f"/releases/download/{goa_download.KH3_GOA_MOD_RELEASE_TAG}/", urlopen.call_args.args[0].full_url)
            self.assertEqual((mods_dir / goa_download.KH3_GOA_MOD_FILE_NAME).read_bytes(), b"goa")

    def test_client_download_goa_mod_does_not_fall_back_to_lower_release(self) -> None:
        self._pin_goa_test_payload()
        with TemporaryDirectory() as temp_dir:
            mods_dir = Path(temp_dir) / "~mods"
            ctx = KH3Context.__new__(KH3Context)
            ctx.kh3_mods_dir = mods_dir
            with patch.object(goa_download.urllib.request, "urlopen", side_effect=OSError("pinned release not found")) as urlopen:
                ctx.download_goa_mod()
            urlopen.assert_called_once()
            self.assertEqual(urlopen.call_args.args[0].full_url, goa_download.KH3_GOA_MOD_DOWNLOAD_URL)
            self.assertEqual(list(mods_dir.iterdir()), [])

    def test_client_goa_mod_status_checks_installed_bytes_without_network(self) -> None:
        self._pin_goa_test_payload()
        with TemporaryDirectory() as temp_dir:
            mods_dir = Path(temp_dir) / "~mods"
            mods_dir.mkdir()
            target = mods_dir / goa_download.KH3_GOA_MOD_FILE_NAME
            with patch.object(goa_download.urllib.request, "urlopen", side_effect=AssertionError("network used")):
                for data, expected in ((b"old", "outdated"), (b"goa", "current")):
                    with self.subTest(data=data):
                        target.write_bytes(data)
                        status = goa_mod_install_status(mods_dir)
                        self.assertEqual(status["status"], expected)
                        self.assertEqual(status["installed_path"], target)
                        self.assertEqual(status["expected_size"], 3)
                        self.assertEqual(status["expected_sha256"], hashlib.sha256(b"goa").hexdigest().upper())
                        self.assertEqual(status["expected_version"], goa_download.KH3_GOA_MOD_RELEASE_TAG)

    def test_client_goa_status_startup_path_uses_cached_hash_only(self) -> None:
        self._pin_goa_test_payload()
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            mods_dir = root / "~mods"
            mods_dir.mkdir()
            target = mods_dir / goa_download.KH3_GOA_MOD_FILE_NAME
            target.write_bytes(b"goa")
            cache_path = root / "cache" / "goa-status.json"
            with (
                patch.object(goa_download.urllib.request, "urlopen", side_effect=AssertionError("network used")),
                patch.object(goa_download, "_sha256_file", side_effect=AssertionError("hash used")),
            ):
                cold_status = goa_mod_install_status(mods_dir, cache_path=cache_path, allow_hash=False)
            self.assertEqual(cold_status["status"], "unknown")
            self.assertTrue(cold_status["verification_deferred"])
            self.assertEqual(cold_status["expected_version"], goa_download.KH3_GOA_MOD_RELEASE_TAG)
            self.assertFalse(cache_path.exists())
            with (
                patch.object(goa_download.urllib.request, "urlopen", side_effect=AssertionError("network used")),
                patch.object(goa_download, "_sha256_file", wraps=goa_download._sha256_file) as hash_file,
            ):
                refreshed_status = goa_mod_install_status(mods_dir, cache_path=cache_path)
            self.assertEqual(refreshed_status["status"], "current")
            hash_file.assert_called_once_with(target)
            with (
                patch.object(goa_download.urllib.request, "urlopen", side_effect=AssertionError("network used")),
                patch.object(goa_download, "_sha256_file", side_effect=AssertionError("hash used")),
            ):
                cached_status = goa_mod_install_status(mods_dir, cache_path=cache_path, allow_hash=False)
            self.assertEqual(cached_status["status"], "current")

    def test_client_download_goa_mod_replaces_existing_goa_paks(self) -> None:
        self._pin_goa_test_payload()
        with TemporaryDirectory() as temp_dir:
            mods_dir = Path(temp_dir) / "~mods"
            mods_dir.mkdir()
            old_goa = mods_dir / "pakchunk99-GardenOfAssemblageRandomizer_old_99_P.pak"
            unrelated_pak = mods_dir / "pakchunk9-Player-randomizer-AP_123-1_99_P.pak"
            old_goa.write_bytes(b"old")
            unrelated_pak.write_bytes(b"keep")
            ctx = KH3Context.__new__(KH3Context)
            ctx.kh3_mods_dir = mods_dir
            with patch.object(goa_download.urllib.request, "urlopen", return_value=BytesIO(b"goa")):
                ctx.download_goa_mod()
            self.assertEqual((mods_dir / goa_download.KH3_GOA_MOD_FILE_NAME).read_bytes(), b"goa")
            self.assertFalse(old_goa.exists())
            self.assertEqual(unrelated_pak.read_bytes(), b"keep")
            self.assertFalse(list(mods_dir.glob("*.download")))

    def test_client_download_goa_mod_skips_current_pak_and_removes_stale_goa_paks(self) -> None:
        self._pin_goa_test_payload()
        with TemporaryDirectory() as temp_dir:
            mods_dir = Path(temp_dir) / "~mods"
            mods_dir.mkdir()
            target = mods_dir / goa_download.KH3_GOA_MOD_FILE_NAME
            old_goa = mods_dir / "pakchunk99-GardenOfAssemblageRandomizer_old_99_P.pak"
            target.write_bytes(b"goa")
            old_goa.write_bytes(b"old")
            ctx = KH3Context.__new__(KH3Context)
            ctx.kh3_mods_dir = mods_dir
            with self.assertLogs("Client", level="INFO") as captured_logs:
                with patch.object(goa_download.urllib.request, "urlopen", side_effect=AssertionError("network used")):
                    ctx.download_goa_mod()
            self.assertEqual(target.read_bytes(), b"goa")
            self.assertFalse(old_goa.exists())
            log_output = "\n".join(captured_logs.output)
            self.assertIn("verified); no update needed.", log_output)
            self.assertIn("Removed 1 older Garden of Assemblage pak(s).", log_output)
            self.assertNotIn("sha256", log_output)

    def test_client_download_goa_mod_keeps_existing_when_download_unavailable(self) -> None:
        self._pin_goa_test_payload()
        with TemporaryDirectory() as temp_dir:
            mods_dir = Path(temp_dir) / "~mods"
            mods_dir.mkdir()
            target = mods_dir / goa_download.KH3_GOA_MOD_FILE_NAME
            target.write_bytes(b"installed")
            ctx = KH3Context.__new__(KH3Context)
            ctx.kh3_mods_dir = mods_dir
            with patch.object(goa_download.urllib.request, "urlopen", side_effect=OSError("offline")):
                ctx.download_goa_mod()
            self.assertEqual(target.read_bytes(), b"installed")

    def test_client_download_goa_mod_http_403_keeps_existing_goa_pak(self) -> None:
        self._pin_goa_test_payload()
        with TemporaryDirectory() as temp_dir:
            mods_dir = Path(temp_dir) / "~mods"
            mods_dir.mkdir()
            target = mods_dir / goa_download.KH3_GOA_MOD_FILE_NAME
            target.write_bytes(b"installed")
            error = HTTPError(goa_download.KH3_GOA_MOD_DOWNLOAD_URL, 403, "forbidden", {}, BytesIO(b"forbidden"))
            with patch.object(goa_download.urllib.request, "urlopen", side_effect=error) as urlopen:
                goa_download.download_goa_mod(mods_dir)
            urlopen.assert_called_once()
            self.assertEqual(target.read_bytes(), b"installed")

    def test_client_download_goa_mod_install_failure_keeps_existing_goa_pak(self) -> None:
        self._pin_goa_test_payload(b"replacement")
        with TemporaryDirectory() as temp_dir:
            mods_dir = Path(temp_dir) / "~mods"
            mods_dir.mkdir()
            target = mods_dir / goa_download.KH3_GOA_MOD_FILE_NAME
            target.write_bytes(b"installed")
            with (
                patch.object(goa_download.urllib.request, "urlopen", return_value=BytesIO(b"replacement")),
                patch("pathlib.Path.replace", side_effect=OSError("synthetic install failure")),
            ):
                goa_download.download_goa_mod(mods_dir)
            self.assertEqual(target.read_bytes(), b"installed")
            self.assertFalse(list(mods_dir.glob("*.download")))

    def test_client_download_goa_mod_uses_ssl_context(self) -> None:
        self._pin_goa_test_payload()
        with TemporaryDirectory() as temp_dir:
            context = object()
            ctx = KH3Context.__new__(KH3Context)
            ctx.kh3_mods_dir = Path(temp_dir) / "~mods"
            with (
                patch.object(goa_download, "_goa_mod_ssl_context", return_value=context),
                patch.object(goa_download.urllib.request, "urlopen", return_value=BytesIO(b"goa")) as urlopen,
            ):
                ctx.download_goa_mod()
            urlopen.assert_called_once()
            self.assertIs(urlopen.call_args.kwargs.get("context"), context)

    def test_client_export_debug_bundle_collects_bridge_log_and_crash_dump(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            kh3_dir = root / "KINGDOM HEARTS III"
            win64_dir = kh3_dir / "Binaries" / "Win64"
            bridge_dir = root / "bridge" / "seed_PlayerKH"
            mods_dir = kh3_dir / "Content" / "Paks" / "~mods"
            save_dir = root / "SaveGames"
            output_dir = root / "debug"
            win64_dir.mkdir(parents=True)
            bridge_dir.mkdir(parents=True)
            mods_dir.mkdir(parents=True)
            save_dir.mkdir()
            (bridge_dir / "runtime_loot_event.json").write_text('{"bridge_key":"TreasureTT:TT_SBOX_001"}', encoding="utf-8")
            (bridge_dir / "item_delivery_ledger.json").write_text(json.dumps({
                "protocol_name": BRIDGE_PROTOCOL_NAME,
                "protocol_version": BRIDGE_PROTOCOL_VERSION,
                "items": {
                    "0": {
                        "index": 0,
                        "item_name": "Mythril Shard",
                        "location_name": "AP Quest",
                        "status": "applied",
                        "replay_policy": "runtime_only",
                        "apply_source": "runtime_item_apply_event",
                        "applied_at_utc": "2026-04-30T04:56:06Z",
                    }
                },
                "updated_at_utc": "2026-04-30T04:56:07Z",
            }), encoding="utf-8")
            (win64_dir / "UE4SS.log").write_text("ue4ss log", encoding="utf-8")
            (win64_dir / "UE4SS.dll").write_bytes(b"ue4ss")
            (win64_dir / "UE4SS-settings.ini").write_text("bUseUObjectArrayCache = false\n", encoding="utf-8")
            (win64_dir / "override.txt").write_text(".\n", encoding="utf-8")
            (win64_dir / "dwmapi.dll").write_bytes(b"proxy")
            (win64_dir / "xinput1_3.dll").write_bytes(b"xinput")
            preferred_ue4ss_dir = win64_dir / "ue4ss"
            preferred_ue4ss_dir.mkdir()
            (preferred_ue4ss_dir / "UE4SS.dll").write_bytes(b"other-runtime")
            (preferred_ue4ss_dir / "UE4SS-settings.ini").write_text("other settings\n", encoding="utf-8")
            (preferred_ue4ss_dir / "MemberVariableLayout.ini").write_text("other layout\n", encoding="utf-8")
            (preferred_ue4ss_dir / "VTableLayout.ini").write_text("other vtable\n", encoding="utf-8")
            (preferred_ue4ss_dir / "Mods").mkdir()
            (preferred_ue4ss_dir / "Mods" / "mods.txt").write_text("99SaveSlots : 1\n", encoding="utf-8")
            (win64_dir / "UE4SS_Signatures").mkdir()
            (win64_dir / "UE4SS_Signatures" / "FName_Constructor.notlua").write_text("fname", encoding="utf-8")
            (win64_dir / "UE4SS_Signatures" / "GUObjectArray.lua").write_text("objects", encoding="utf-8")
            custom_config_dir = win64_dir / "CustomGameConfigs" / "Kingdom Hearts 3"
            custom_config_dir.mkdir(parents=True)
            (custom_config_dir / "MemberVariableLayout.ini").write_text("layout", encoding="utf-8")
            (custom_config_dir / "UE4SS-settings.ini").write_text("HookProcessInternal = 0\n", encoding="utf-8")
            (custom_config_dir / "VTableLayout.ini").write_text("vtable", encoding="utf-8")
            (custom_config_dir / "UE4SS_Signatures").mkdir()
            legacy_custom_config_dir = win64_dir / "KINGDOM HEARTS III"
            legacy_custom_config_dir.mkdir()
            (legacy_custom_config_dir / "MemberVariableLayout.ini").write_text("layout", encoding="utf-8")
            (legacy_custom_config_dir / "UE4SS-settings.ini").write_text("HookProcessInternal = 0\n", encoding="utf-8")
            (legacy_custom_config_dir / "VTableLayout.ini").write_text("vtable", encoding="utf-8")
            (legacy_custom_config_dir / "UE4SS_Signatures").mkdir()
            (win64_dir / "Mods").mkdir()
            (win64_dir / "Mods" / "mods.txt").write_text("KH3APProbeCpp : 1\n", encoding="utf-8")
            probe_dll = win64_dir / "Mods" / "KH3APProbeCpp" / "dlls" / "main.dll"
            probe_dll.parent.mkdir(parents=True)
            probe_dll.write_bytes(b"probe")
            (win64_dir / "Mods" / "KH3APProbeCpp" / "config.lua").write_text("return {}\n", encoding="utf-8")
            (win64_dir / "Mods" / "KH3APProbeCpp" / "runtime_item_apply_event.json").write_text(json.dumps({
                "protocol_name": BRIDGE_PROTOCOL_NAME,
                "protocol_version": BRIDGE_PROTOCOL_VERSION,
                "request_id": "runtime-grant-batch-debug-cpp",
                "action_kind": "runtime_item_grant_batch",
                "status": "applied",
                "applied_indices": [20],
            }, indent=2), encoding="utf-8")
            (win64_dir / "Mods" / "KH3APProbeCpp" / "runtime_grant_cpp_profile.jsonl").write_text(
                '{"event":"runtime_batch_apply_event_write"}\n',
                encoding="utf-8",
            )
            legacy_probe_dll = win64_dir / "KINGDOM HEARTS III" / "Mods" / "KH3APProbeCpp" / "dlls" / "main.dll"
            legacy_probe_dll.parent.mkdir(parents=True)
            legacy_probe_dll.write_bytes(b"legacy-probe")
            (win64_dir / "KINGDOM HEARTS III" / "Mods" / "mods.txt").write_text("KH3APProbeCpp : 1\n", encoding="utf-8")
            (win64_dir / "crash_2026_04_29_16_36_19.dmp").write_bytes(b"dump")
            (mods_dir / "pakchunk9-PlayerKH-randomizer-AP_123-1_99_P.pak").write_bytes(b"pak")
            randomizer_saves = {
                "ChoicesMade.sav": b"choices",
                "FlagDataDW.sav": b"flag-data",
                "PlayerStart.sav": b"player-start",
                "RandomizerRetry.sav": b"retry",
                "SavePointFZ.sav": b"save-point",
                "WorldSaveDone.sav": b"world-done",
            }
            for name, contents in randomizer_saves.items():
                (save_dir / name).write_bytes(contents)
            retired_saves = (
                "RandomizerEvents.sav", "RandomizerChests.sav", "RandomizerVBonus.sav", "RandomizerLevelUp.sav",
            )
            for name in retired_saves:
                (save_dir / name).write_bytes(b"retired-sidecar")
            (save_dir / "KHIII_slot1.sav").write_bytes(b"vanilla-slot")
            (save_dir / "UnrelatedMod.sav").write_bytes(b"not-allowlisted")
            vanilla_data_dir = save_dir / "kh3sv2" / "data"
            vanilla_data_dir.mkdir(parents=True)
            (vanilla_data_dir / "KHIII_slot1.bin").write_bytes(b"vanilla-bin-slot")
            (vanilla_data_dir / "KHIII_system.bin").write_bytes(b"vanilla-system")

            with (
                patch(
                    "worlds.kh3.client_support.debug_bundle.get_process_status",
                    return_value={"running": True, "pid": 4242, "started_at_utc": "2026-04-30T04:56:00Z"},
                ),
                patch(
                    "worlds.kh3.client_support.debug_bundle.get_process_module_snapshot",
                    return_value={
                        "status": "collected",
                        "modules": [{"name": "UE4SS.dll", "path": str(win64_dir / "UE4SS.dll"), "size": 5}],
                        "missing_modules": ["main.dll"],
                    },
                ),
            ):
                result = export_kh3_debug_bundle(
                    kh3_dir=kh3_dir,
                    bridge_dir=bridge_dir,
                    kh3_mods_dir=mods_dir,
                    save_dir=save_dir,
                    ue4ss_log=win64_dir / "UE4SS.log",
                    output_dir=output_dir,
                    include_crash_dumps=True,
                )

            bundle_path = Path(result["bundle_path"])
            self.assertTrue(bundle_path.is_file())
            with zipfile.ZipFile(bundle_path) as archive:
                names = set(archive.namelist())
                self.assertIn("debug_summary.json", names)
                self.assertIn("bridge/seed_PlayerKH/runtime_loot_event.json", names)
                self.assertIn("bridge/seed_PlayerKH/item_delivery_ledger.json", names)
                self.assertIn("logs/UE4SS.log", names)
                self.assertIn("runtime/Win64/UE4SS-settings.ini", names)
                self.assertIn("runtime/Win64/override.txt", names)
                self.assertIn("runtime/Win64/ue4ss/UE4SS-settings.ini", names)
                self.assertIn("runtime/Win64/ue4ss/MemberVariableLayout.ini", names)
                self.assertIn("runtime/Win64/ue4ss/VTableLayout.ini", names)
                self.assertIn("runtime/Win64/ue4ss/Mods/mods.txt", names)
                self.assertIn("runtime/Win64/CustomGameConfigs/Kingdom Hearts 3/UE4SS-settings.ini", names)
                self.assertIn("runtime/Win64/CustomGameConfigs/Kingdom Hearts 3/MemberVariableLayout.ini", names)
                self.assertIn("runtime/Win64/KINGDOM HEARTS III/UE4SS-settings.ini", names)
                self.assertIn("runtime/Win64/KINGDOM HEARTS III/MemberVariableLayout.ini", names)
                self.assertIn("runtime/Win64/Mods/mods.txt", names)
                self.assertIn("runtime/Win64/Mods/KH3APProbeCpp/config.lua", names)
                self.assertIn("runtime/Win64/Mods/KH3APProbeCpp/runtime_item_apply_event.json", names)
                self.assertIn("runtime/Win64/Mods/KH3APProbeCpp/runtime_grant_cpp_profile.jsonl", names)
                self.assertIn("crashes/crash_2026_04_29_16_36_19.dmp", names)
                for name, contents in randomizer_saves.items():
                    archive_name = f"randomizer-saves/{name}"
                    self.assertIn(archive_name, names)
                    self.assertEqual(archive.read(archive_name), contents)
                for name in retired_saves:
                    self.assertNotIn(f"randomizer-saves/{name}", names)
                self.assertNotIn("randomizer-saves/KHIII_slot1.sav", names)
                self.assertNotIn("randomizer-saves/UnrelatedMod.sav", names)
                self.assertNotIn("randomizer-saves/KHIII_slot1.bin", names)
                self.assertNotIn("randomizer-saves/KHIII_system.bin", names)
                self.assertNotIn("pakchunk9-PlayerKH-randomizer-AP_123-1_99_P.pak", names)
                summary = json.loads(archive.read("debug_summary.json").decode("utf-8"))
                self.assertEqual(summary["installed_paks"][0]["size"], 3)
                self.assertEqual(summary["crash_dumps"][0]["size"], 4)
                self.assertTrue(summary["bridge_files"]["item_delivery_ledger.json"]["exists"])
                self.assertEqual(summary["runtime_files"]["UE4SS.dll"]["sha256"], "35C207762B07DA5DC56F5B5AD8A1722E57065787AF5EA008177C32D80794912A")
                self.assertTrue(summary["runtime_files"]["override.txt"]["exists"])
                self.assertTrue(summary["runtime_files"]["ue4ss/UE4SS.dll"]["exists"])
                self.assertTrue(summary["runtime_files"]["ue4ss/Mods/mods.txt"]["exists"])
                self.assertTrue(summary["runtime_files"]["CustomGameConfigs/Kingdom Hearts 3/UE4SS-settings.ini"]["exists"])
                self.assertTrue(summary["runtime_files"]["KINGDOM HEARTS III/UE4SS-settings.ini"]["exists"])
                self.assertTrue(summary["runtime_files"]["Mods/KH3APProbeCpp/dlls/main.dll"]["exists"])
                self.assertTrue(summary["runtime_files"]["KINGDOM HEARTS III/Mods/mods.txt"]["exists"])
                self.assertTrue(summary["runtime_files"]["KINGDOM HEARTS III/Mods/KH3APProbeCpp/dlls/main.dll"]["exists"])
                self.assertTrue(summary["runtime_sidecar_files"]["Mods/KH3APProbeCpp/runtime_item_apply_event.json"]["exists"])
                self.assertTrue(summary["runtime_sidecar_files"]["Mods/KH3APProbeCpp/runtime_grant_cpp_profile.jsonl"]["exists"])
                self.assertTrue(summary["game_process"]["running"])
                self.assertEqual(summary["game_process"]["runtime_module_snapshot"]["missing_modules"], ["main.dll"])
                self.assertEqual(set(summary["randomizer_save_files"]), set(randomizer_saves))
                self.assertEqual(result["randomizer_save_count"], len(randomizer_saves))
                self.assertEqual(summary["item_delivery_ledger"]["status_counts"], {"applied": 1})
                self.assertEqual(summary["item_delivery_ledger"]["replay_policy_counts"], {"runtime_only": 1})

    def test_client_export_debug_bundle_collects_latest_unreal_crash_reports(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            kh3_dir = root / "KINGDOM HEARTS III"
            win64_dir = kh3_dir / "Binaries" / "Win64"
            mods_dir = kh3_dir / "Content" / "Paks" / "~mods"
            save_dir = root / "SaveGames"
            output_dir = root / "debug"
            local_appdata = root / "LocalAppData"
            crash_root = local_appdata / "KINGDOM HEARTS III" / "Saved" / "Crashes"
            win64_dir.mkdir(parents=True)
            mods_dir.mkdir(parents=True)
            save_dir.mkdir()
            crash_root.mkdir(parents=True)

            def write_report(name: str, timestamp: float, dump_name: str = "UE4Minidump.dmp") -> None:
                report_dir = crash_root / name
                report_dir.mkdir()
                dump_path = report_dir / dump_name
                context_path = report_dir / "CrashContext.runtime-xml"
                dump_path.write_bytes(name.encode("ascii"))
                context_path.write_text(f"<Crash>{name}</Crash>\n", encoding="utf-8")
                for path in (report_dir, dump_path, context_path):
                    os.utime(path, (timestamp, timestamp))

            today = datetime.now().replace(hour=12, minute=0, second=0, microsecond=0).timestamp()
            write_report("UE4CC-Windows-oldest", today)
            write_report("UE4CC-Windows-middle", today + 100)
            write_report("UE4CC-Windows-newest", today + 200, "UE4Minidump(1).dmp")

            with patch.dict(os.environ, {"LOCALAPPDATA": str(local_appdata)}):
                result = export_kh3_debug_bundle(
                    kh3_dir=kh3_dir,
                    bridge_dir=None,
                    kh3_mods_dir=mods_dir,
                    save_dir=save_dir,
                    ue4ss_log=None,
                    output_dir=output_dir,
                    include_crash_dumps=True,
                    max_crash_dumps=2,
                )

            bundle_path = Path(result["bundle_path"])
            with zipfile.ZipFile(bundle_path) as archive:
                names = set(archive.namelist())
                self.assertIn("crashes/unreal/UE4CC-Windows-newest/UE4Minidump(1).dmp", names)
                self.assertIn("crashes/unreal/UE4CC-Windows-newest/CrashContext.runtime-xml", names)
                self.assertIn("crashes/unreal/UE4CC-Windows-middle/UE4Minidump.dmp", names)
                self.assertIn("crashes/unreal/UE4CC-Windows-middle/CrashContext.runtime-xml", names)
                self.assertNotIn("crashes/unreal/UE4CC-Windows-oldest/UE4Minidump.dmp", names)
                summary = json.loads(archive.read("debug_summary.json").decode("utf-8"))
                self.assertEqual(len(summary["crash_reports"]), 2)
                self.assertEqual(len(summary["crash_dumps"]), 2)

    def test_client_uninstall_ap_removes_runtime_probe_and_randomizer_files(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            kh3_root = root / "EGS" / "KH_3" / "KINGDOM HEARTS III"
            win64 = kh3_root / "Binaries" / "Win64"
            mods_dir = kh3_root / "Content" / "Paks" / "~mods"
            win64.mkdir(parents=True)
            mods_dir.mkdir(parents=True)

            ap_pak = mods_dir / "pakchunk9-PlayerKH-randomizer-AP_22222222222222222222-1_99_P.pak"
            goa_pak = mods_dir / "pakchunk99-GardenOfAssemblageRandomizer_99_P.pak"
            unrelated_pak = mods_dir / "pakchunk99-unrelated_99_P.pak"
            ap_pak.write_bytes(b"ap")
            goa_pak.write_bytes(b"goa")
            unrelated_pak.write_bytes(b"other")

            for file_name in installer_ue4ss_config.UE4SS_MANAGED_FILES:
                (win64 / file_name).write_bytes(b"managed")
            (win64 / "UE4SS_Signatures").mkdir()
            (win64 / "UE4SS_Signatures" / "FName_Constructor.lua").write_text("sig", encoding="utf-8")
            custom_config = win64 / "CustomGameConfigs" / "Kingdom Hearts 3"
            custom_config.mkdir(parents=True)
            (custom_config / "UE4SS-settings.ini").write_text("settings", encoding="utf-8")
            probe_config = win64 / "Mods" / "KH3APProbeCpp" / "config.lua"
            probe_config.parent.mkdir(parents=True)
            probe_config.write_text("return {}\n", encoding="utf-8")
            probe_dll = win64 / "Mods" / "KH3APProbeCpp" / "dlls" / "main.dll"
            probe_dll.parent.mkdir(parents=True)
            probe_dll.write_bytes(b"probe")
            mods_txt = win64 / "Mods" / "mods.txt"
            mods_txt.write_text("ActorDumperMod : 0\nKH3APProbeCpp : 1\n", encoding="utf-8")
            legacy_mods_txt = win64 / "KINGDOM HEARTS III" / "Mods" / "mods.txt"
            legacy_mods_txt.parent.mkdir(parents=True)
            legacy_mods_txt.write_text("KH3APProbeCpp : 1\n", encoding="utf-8")
            legacy_probe = win64 / "KINGDOM HEARTS III" / "Mods" / "KH3APProbeCpp" / "dlls" / "main.dll"
            legacy_probe.parent.mkdir(parents=True)
            legacy_probe.write_bytes(b"legacy")
            path_specific_probe = win64 / "KH_3" / "Mods" / "KH3APProbeCpp" / "dlls" / "main.dll"
            path_specific_probe.parent.mkdir(parents=True)
            path_specific_probe.write_bytes(b"path-specific")
            path_specific_mods_txt = win64 / "KH_3" / "Mods" / "mods.txt"
            path_specific_mods_txt.write_text(
                "ActorDumperMod : 0\nKH3APProbeCpp : 1\n",
                encoding="utf-8",
            )

            ctx = KH3Context.__new__(KH3Context)
            ctx.kh3_dir = kh3_root
            ctx.kh3_mods_dir = mods_dir
            ctx.bridge_save_dir = root / "SaveGames"

            ctx.uninstall_kh3_ap_files()

            self.assertFalse(ap_pak.exists())
            self.assertFalse(goa_pak.exists())
            self.assertTrue(unrelated_pak.exists())
            self.assertFalse((win64 / "UE4SS.dll").exists())
            self.assertFalse((win64 / "UE4SS_Signatures").exists())
            self.assertFalse(custom_config.exists())
            self.assertFalse(probe_config.parent.exists())
            self.assertFalse(probe_dll.parent.parent.exists())
            self.assertFalse(legacy_probe.parent.parent.exists())
            self.assertFalse(path_specific_probe.parent.parent.exists())
            self.assertEqual(mods_txt.read_text(encoding="utf-8"), "ActorDumperMod : 0\n")
            self.assertFalse(legacy_mods_txt.exists())
            self.assertEqual(path_specific_mods_txt.read_text(encoding="utf-8"), "ActorDumperMod : 0\n")

    def test_runtime_uninstall_keeps_the_players_custom_music(self) -> None:
        with TemporaryDirectory() as temp_dir:
            kh3_root = Path(temp_dir) / "KINGDOM HEARTS III"
            win64 = kh3_root / "Binaries" / "Win64"
            probe_dir = win64 / "Mods" / "KH3APProbeCpp"
            probe_dll = probe_dir / "dlls" / "main.dll"
            probe_dll.parent.mkdir(parents=True)
            probe_dll.write_bytes(b"probe")
            (probe_dir / "config.lua").write_text("return {}\n", encoding="utf-8")
            song = probe_dir / "custom_music" / "GoA_01.ogg"
            song.parent.mkdir()
            song.write_bytes(b"music")

            result = runtime_cleanup.uninstall_kh3_ap_files(kh3_root, kh3_root / "Content" / "Paks" / "~mods")

            self.assertFalse(probe_dll.parent.exists())
            self.assertFalse((probe_dir / "config.lua").exists())
            self.assertEqual(song.read_bytes(), b"music")
            self.assertEqual(result["preserved"], [str(song.parent)])

    def test_runtime_uninstall_plan_lists_targets_without_mutating_install(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            kh3_root = root / "KINGDOM HEARTS III"
            win64 = kh3_root / "Binaries" / "Win64"
            mods_dir = kh3_root / "Content" / "Paks" / "~mods"
            probe_dir = win64 / "Mods" / "KH3APProbeCpp"
            probe_dir.mkdir(parents=True)
            mods_dir.mkdir(parents=True)
            ap_pak = mods_dir / "pakchunk9-PlayerKH-randomizer-AP_22222222222222222222-1_99_P.pak"
            goa_pak = mods_dir / "pakchunk99-GardenOfAssemblageRandomizer_99_P.pak"
            unrelated_pak = mods_dir / "pakchunk99-unrelated_99_P.pak"
            ap_pak.write_bytes(b"ap")
            goa_pak.write_bytes(b"goa")
            unrelated_pak.write_bytes(b"other")
            probe_file = probe_dir / "config.lua"
            probe_file.write_text("return {}\n", encoding="utf-8")

            plan = runtime_cleanup.plan_uninstall_kh3_ap_files(kh3_root, mods_dir)

            self.assertEqual(set(plan.pak_files), {ap_pak, goa_pak})
            self.assertIn(probe_dir, plan.runtime_dirs)
            self.assertNotIn(unrelated_pak, plan.pak_files)
            self.assertTrue(ap_pak.exists())
            self.assertTrue(goa_pak.exists())
            self.assertTrue(unrelated_pak.exists())
            self.assertTrue(probe_file.exists())

    def test_client_preserves_active_saves_on_new_seed_across_steam_accounts(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bridge_dir = root / "bridge"
            steam_root = root / "Documents" / "My Games" / "KINGDOM HEARTS III" / "Steam"
            save_dir_a = steam_root / "00000000000000000000000000000000" / "SaveGames"
            save_dir_b = steam_root / "76561198250377274" / "SaveGames"
            bridge_dir.mkdir()
            save_dir_a.mkdir(parents=True)
            save_dir_b.mkdir(parents=True)
            (save_dir_a / "RandomizerEvents.sav").write_bytes(b"old-a")
            (save_dir_b / "RandomizerVBonus.sav").write_bytes(b"old-b")
            (save_dir_b / "steam_autocloud.vdf").write_text("safe", encoding="utf-8")

            ctx = KH3Context.__new__(KH3Context)
            ctx.bridge_dir = bridge_dir
            ctx.bridge_save_dir = save_dir_a
            ctx.save_dir_override = False
            ctx.save_dir_dynamic_root_override = False
            ctx.seed_name = "AP_85269139745991038903"
            ctx.offline_seed_name = None
            ctx.kh3_restart_required_seed_id = None
            ctx.auto_remove_saves_on_new_seed = True
            ctx.last_processed_game_state_seq = -1
            ctx.last_processed_bridge_notification_seq = 0
            ctx.last_processed_client_command_seq = 0
            ctx.goal_status_sent = False
            ctx.write_bridge_files = lambda: None
            save_key_a = KH3Context.save_seed_scope_key(save_dir_a)
            save_key_b = KH3Context.save_seed_scope_key(save_dir_b)

            with patch("worlds.kh3.Client.is_process_running", return_value=False), \
                    patch("worlds.kh3.Client.Utils.persistent_load", return_value={
                        "kh3_client": {
                            KH3_SAVE_SEED_ASSOCIATIONS_KEY: {
                                save_key_a: {
                                    "seed_id": "11111111111111111111",
                                    "guard_version": KH3_SAVE_SEED_GUARD_VERSION,
                                },
                                save_key_b: {
                                    "seed_id": "11111111111111111111",
                                    "guard_version": KH3_SAVE_SEED_GUARD_VERSION,
                                },
                            },
                            "last_save_seed_id": "11111111111111111111",
                            "last_save_seed_guard_version": KH3_SAVE_SEED_GUARD_VERSION,
                        }
                    }), patch("worlds.kh3.Client.Utils.persistent_store") as persistent_store:
                self.assertFalse(ctx.refresh_save_seed_guard())

            self.assertTrue((save_dir_a / "RandomizerEvents.sav").exists())
            self.assertTrue((save_dir_b / "RandomizerVBonus.sav").exists())
            self.assertTrue((save_dir_b / "steam_autocloud.vdf").exists())
            persistent_store.assert_not_called()

    def test_client_legacy_auto_remove_exact_save_override_does_not_clean_steam_siblings(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bridge_dir = root / "bridge"
            steam_root = root / "Documents" / "My Games" / "KINGDOM HEARTS III" / "Steam"
            save_dir_a = steam_root / "00000000000000000000000000000000" / "SaveGames"
            save_dir_b = steam_root / "76561198250377274" / "SaveGames"
            bridge_dir.mkdir()
            save_dir_a.mkdir(parents=True)
            save_dir_b.mkdir(parents=True)
            (save_dir_a / "RandomizerEvents.sav").write_bytes(b"old-a")
            (save_dir_b / "RandomizerVBonus.sav").write_bytes(b"old-b")

            ctx = KH3Context.__new__(KH3Context)
            ctx.bridge_dir = bridge_dir
            ctx.bridge_save_dir = save_dir_a
            ctx.save_dir_override = True
            ctx.save_dir_dynamic_root_override = False
            ctx.seed_name = "AP_85269139745991038903"
            ctx.offline_seed_name = None
            ctx.kh3_restart_required_seed_id = None
            ctx.auto_remove_saves_on_new_seed = True
            ctx.last_processed_game_state_seq = -1
            ctx.last_processed_bridge_notification_seq = 0
            ctx.last_processed_client_command_seq = 0
            ctx.goal_status_sent = False
            ctx.write_bridge_files = lambda: None
            save_key_a = KH3Context.save_seed_scope_key(save_dir_a)

            with patch("worlds.kh3.Client.is_process_running", return_value=False), \
                    patch("worlds.kh3.Client.Utils.persistent_load", return_value={
                        "kh3_client": {
                            KH3_SAVE_SEED_ASSOCIATIONS_KEY: {
                                save_key_a: {
                                    "seed_id": "11111111111111111111",
                                    "guard_version": KH3_SAVE_SEED_GUARD_VERSION,
                                }
                            },
                            "last_save_seed_id": "11111111111111111111",
                            "last_save_seed_guard_version": KH3_SAVE_SEED_GUARD_VERSION,
                        }
                    }), patch("worlds.kh3.Client.Utils.persistent_store"):
                self.assertFalse(ctx.refresh_save_seed_guard())

            self.assertTrue((save_dir_a / "RandomizerEvents.sav").exists())
            self.assertTrue((save_dir_b / "RandomizerVBonus.sav").exists())

    def test_client_kh3_root_dir_accepts_win64_directory(self) -> None:
        with TemporaryDirectory() as temp_dir:
            game_root = Path(temp_dir) / "KINGDOM HEARTS III"
            win64_dir = game_root / "Binaries" / "Win64"

            self.assertEqual(normalize_kh3_root_dir(win64_dir), game_root.resolve())

    def test_client_kh3_root_dir_accepts_outer_steam_folder(self) -> None:
        with TemporaryDirectory() as temp_dir:
            steam_folder = Path(temp_dir) / "Steam" / "steamapps" / "common" / "KINGDOM HEARTS III"
            game_root = steam_folder / "KINGDOM HEARTS III"
            (game_root / "Binaries" / "Win64").mkdir(parents=True)

            self.assertEqual(normalize_kh3_root_dir(steam_folder), game_root.resolve())
            self.assertEqual(normalize_kh3_root_dir(game_root), game_root.resolve())

    def test_client_prefers_active_nested_ue4ss_log(self) -> None:
        with TemporaryDirectory() as temp_dir:
            game_root = Path(temp_dir) / "KINGDOM HEARTS III"
            win64_dir = game_root / "Binaries" / "Win64"
            nested_log = win64_dir / "KINGDOM HEARTS III" / "UE4SS.log"
            root_log = win64_dir / "UE4SS.log"
            nested_log.parent.mkdir(parents=True)
            root_log.write_text("old log", encoding="utf-8")
            nested_log.write_text("active log", encoding="utf-8")
            os.utime(root_log, (1000, 1000))
            os.utime(nested_log, (2000, 2000))

            self.assertEqual(kh3_ue4ss_log_from_root(game_root), nested_log)

    def test_client_prefers_active_proxy_preferred_ue4ss_log(self) -> None:
        with TemporaryDirectory() as temp_dir:
            game_root = Path(temp_dir) / "KINGDOM HEARTS III"
            win64_dir = game_root / "Binaries" / "Win64"
            preferred_log = win64_dir / "ue4ss" / "UE4SS.log"
            root_log = win64_dir / "UE4SS.log"
            preferred_log.parent.mkdir(parents=True)
            root_log.write_text("old root log", encoding="utf-8")
            preferred_log.write_text("active preferred log", encoding="utf-8")
            os.utime(root_log, (1000, 1000))
            os.utime(preferred_log, (3000, 3000))

            self.assertEqual(kh3_ue4ss_log_from_root(game_root), preferred_log)

    def test_client_prefers_active_egs_path_specific_ue4ss_log(self) -> None:
        with TemporaryDirectory() as temp_dir:
            outer_root = Path(temp_dir) / "Kingdom Hearts III and Re-Mind"
            game_root = outer_root / "KINGDOM HEARTS III"
            win64_dir = game_root / "Binaries" / "Win64"
            path_specific_log = win64_dir / outer_root.name / "UE4SS.log"
            root_log = win64_dir / "UE4SS.log"
            path_specific_log.parent.mkdir(parents=True)
            root_log.write_text("old root log", encoding="utf-8")
            path_specific_log.write_text("active EGS log", encoding="utf-8")
            os.utime(root_log, (1000, 1000))
            os.utime(path_specific_log, (4000, 4000))

            self.assertEqual(kh3_ue4ss_log_from_root(game_root), path_specific_log)

    def test_client_export_debug_bundle_collects_egs_path_specific_dump_and_log(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            outer_root = root / "Kingdom Hearts III and Re-Mind"
            kh3_dir = outer_root / "KINGDOM HEARTS III"
            win64_dir = kh3_dir / "Binaries" / "Win64"
            path_specific_dir = win64_dir / outer_root.name
            mods_dir = kh3_dir / "Content" / "Paks" / "~mods"
            save_dir = root / "SaveGames"
            output_dir = root / "debug"
            path_specific_dir.mkdir(parents=True)
            mods_dir.mkdir(parents=True)
            save_dir.mkdir()
            (path_specific_dir / "UE4SS.log").write_text("active EGS log", encoding="utf-8")
            (path_specific_dir / "UE4SS-settings.ini").write_text("HookProcessInternal = 0\n", encoding="utf-8")
            (path_specific_dir / "crash_2026_08_31_01_02_48.dmp").write_bytes(b"egs-dump")

            with patch.dict(os.environ, {"LOCALAPPDATA": str(root / "LocalAppData")}):
                result = export_kh3_debug_bundle(
                    kh3_dir=kh3_dir,
                    bridge_dir=None,
                    kh3_mods_dir=mods_dir,
                    save_dir=save_dir,
                    ue4ss_log=None,
                    output_dir=output_dir,
                    include_crash_dumps=True,
                )

            with zipfile.ZipFile(Path(result["bundle_path"])) as archive:
                names = set(archive.namelist())
                self.assertIn("logs/UE4SS.log", names)
                self.assertIn("crashes/crash_2026_08_31_01_02_48.dmp", names)
                self.assertIn(
                    "runtime/Win64/Kingdom Hearts III and Re-Mind/UE4SS-settings.ini",
                    names,
                )
                summary = json.loads(archive.read("debug_summary.json").decode("utf-8"))
                self.assertTrue(
                    summary["runtime_files"][
                        "Kingdom Hearts III and Re-Mind/UE4SS-settings.ini"
                    ]["exists"]
                )
                self.assertEqual(result["crash_dump_count"], 1)

    def test_client_resolves_kh3_executable_from_game_root(self) -> None:
        with TemporaryDirectory() as temp_dir:
            game_root = Path(temp_dir) / "KINGDOM HEARTS III"
            executable = game_root / "Binaries" / "Win64" / "KINGDOM HEARTS III.exe"
            executable.parent.mkdir(parents=True)
            executable.write_bytes(b"exe")

            self.assertEqual(resolve_kh3_executable_path(game_root), executable.resolve())

    def test_client_resolves_kh3_executable_from_outer_steam_folder(self) -> None:
        with TemporaryDirectory() as temp_dir:
            steam_folder = Path(temp_dir) / "KINGDOM HEARTS III"
            game_root = steam_folder / "KINGDOM HEARTS III"
            executable = game_root / "Binaries" / "Win64" / "KINGDOM HEARTS III.exe"
            executable.parent.mkdir(parents=True)
            executable.write_bytes(b"exe")

            self.assertEqual(resolve_kh3_executable_path(steam_folder), executable.resolve())

    def test_client_launch_kh3_game_uses_resolved_executable(self) -> None:
        with TemporaryDirectory() as temp_dir:
            game_root = Path(temp_dir) / "KINGDOM HEARTS III"
            executable = game_root / "Binaries" / "Win64" / "KINGDOM HEARTS III.exe"
            executable.parent.mkdir(parents=True)
            executable.write_bytes(b"exe")

            with patch("worlds.kh3.platform_support.game_launch.subprocess.Popen") as popen:
                launch_kh3_game_config(game_root)

            popen.assert_called_once()
            _, kwargs = popen.call_args
            self.assertEqual(kwargs["cwd"], str(executable.parent))
            self.assertEqual(popen.call_args.args[0], [str(executable)])

    def test_client_launch_kh3_game_uses_steam_uri_when_manifest_available(self) -> None:
        with TemporaryDirectory() as temp_dir:
            steamapps_dir = Path(temp_dir) / "SteamLibrary" / "steamapps"
            steam_folder = steamapps_dir / "common" / "KINGDOM HEARTS III"
            game_root = steam_folder / "KINGDOM HEARTS III"
            executable = game_root / "Binaries" / "Win64" / "KINGDOM HEARTS III.exe"
            executable.parent.mkdir(parents=True)
            executable.write_bytes(b"exe")
            (steamapps_dir / "appmanifest_2552450.acf").write_text(
                '"AppState"\n{\n'
                '    "appid"        "2552450"\n'
                '    "installdir"   "KINGDOM HEARTS III"\n'
                '}\n',
                encoding="utf-8",
            )

            with patch("worlds.kh3.platform_support.game_launch.os.startfile", create=True) as startfile, \
                    patch("worlds.kh3.platform_support.game_launch.subprocess.Popen") as popen:
                launch_kh3_game_config(steam_folder)

            startfile.assert_called_once_with("steam://run/2552450")
            popen.assert_not_called()

    def test_client_launch_kh3_game_uses_steam_exe_without_user_id_override(self) -> None:
        with TemporaryDirectory() as temp_dir:
            steam_root = Path(temp_dir) / "Steam"
            steam_exe = steam_root / "steam.exe"
            steam_exe.parent.mkdir(parents=True)
            steam_exe.write_bytes(b"steam")
            steamapps_dir = steam_root / "steamapps"
            steam_folder = steamapps_dir / "common" / "KINGDOM HEARTS III"
            game_root = steam_folder / "KINGDOM HEARTS III"
            executable = game_root / "Binaries" / "Win64" / "KINGDOM HEARTS III.exe"
            executable.parent.mkdir(parents=True)
            executable.write_bytes(b"exe")
            (steamapps_dir / "appmanifest_2552450.acf").write_text(
                '"AppState"\n{\n'
                '    "appid"        "2552450"\n'
                '    "installdir"   "KINGDOM HEARTS III"\n'
                '}\n',
                encoding="utf-8",
            )

            with patch.dict(os.environ, {}, clear=True), \
                    patch("worlds.kh3.platform_support.game_launch.os.startfile", create=True) as startfile, \
                    patch("worlds.kh3.platform_support.game_launch.subprocess.Popen") as popen:
                launch_kh3_game_config(steam_folder)

            popen.assert_called_once()
            startfile.assert_not_called()
            self.assertEqual(
                popen.call_args.args[0],
                [
                    str(steam_exe),
                    "-applaunch",
                    "2552450",
                ],
            )
            self.assertEqual(popen.call_args.kwargs["cwd"], str(steam_root))

    def test_client_resolves_steam_uri_from_app_manifest(self) -> None:
        with TemporaryDirectory() as temp_dir:
            steamapps_dir = Path(temp_dir) / "SteamLibrary" / "steamapps"
            game_root = steamapps_dir / "common" / "KINGDOM HEARTS III" / "KINGDOM HEARTS III"
            executable = game_root / "Binaries" / "Win64" / "KINGDOM HEARTS III.exe"
            executable.parent.mkdir(parents=True)
            executable.write_bytes(b"exe")
            (steamapps_dir / "appmanifest_2552450.acf").write_text(
                '"AppState"\n{\n'
                '    "appid"        "2552450"\n'
                '    "installdir"   "KINGDOM HEARTS III"\n'
                '}\n',
                encoding="utf-8",
            )

            self.assertEqual(resolve_steam_launch_uri_for_kh3_executable(executable), "steam://run/2552450")

    def test_client_detects_kh3_install_from_steam_manifest(self) -> None:
        with TemporaryDirectory() as temp_dir:
            steamapps_dir = Path(temp_dir) / "SteamLibrary" / "steamapps"
            game_root = steamapps_dir / "common" / "KINGDOM HEARTS III" / "KINGDOM HEARTS III"
            executable = game_root / "Binaries" / "Win64" / "KINGDOM HEARTS III.exe"
            executable.parent.mkdir(parents=True)
            executable.write_bytes(b"exe")
            (steamapps_dir / "appmanifest_2552450.acf").write_text(
                '"AppState"\n{\n'
                '    "appid"        "2552450"\n'
                '    "installdir"   "KINGDOM HEARTS III"\n'
                '}\n',
                encoding="utf-8",
            )

            with patch(
                "worlds.kh3.platform_support.game_discovery._default_steamapps_dirs",
                return_value=[steamapps_dir],
            ), patch(
                "worlds.kh3.platform_support.game_discovery._epic_manifest_kh3_roots",
                return_value=[],
            ), patch(
                "worlds.kh3.platform_support.game_discovery._default_epic_kh3_roots",
                return_value=[],
            ):
                self.assertEqual(detect_kh3_install_dir(), game_root.resolve())

    def test_client_can_limit_kh3_install_detection_to_steam_or_egs(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            steamapps_dir = root / "SteamLibrary" / "steamapps"
            steam_game_root = steamapps_dir / "common" / "KINGDOM HEARTS III" / "KINGDOM HEARTS III"
            steam_executable = steam_game_root / "Binaries" / "Win64" / "KINGDOM HEARTS III.exe"
            steam_executable.parent.mkdir(parents=True)
            steam_executable.write_bytes(b"exe")
            (steamapps_dir / "appmanifest_2552450.acf").write_text(
                '"AppState"\n{\n'
                '    "appid"        "2552450"\n'
                '    "installdir"   "KINGDOM HEARTS III"\n'
                '}\n',
                encoding="utf-8",
            )

            epic_root = root / "KH_3" / "KINGDOM HEARTS III"
            epic_executable = epic_root / "Binaries" / "Win64" / "KINGDOM HEARTS III.exe"
            epic_executable.parent.mkdir(parents=True)
            epic_executable.write_bytes(b"exe")

            settings = KH3ClientSettings(kh3_steamapps_dir=steamapps_dir)
            with patch.object(game_discovery, "_epic_manifest_kh3_roots", return_value=[epic_root]), \
                    patch.object(game_discovery, "_default_epic_kh3_roots", return_value=[]), \
                    patch.object(game_discovery, "DEFAULT_KH3_ROOT_DIR", root / "missing-default"):
                self.assertEqual(
                    game_discovery.detect_kh3_install_dir(settings, source="steam"),
                    steam_game_root.resolve(),
                )
                self.assertEqual(
                    game_discovery.detect_kh3_install_dir(settings, source="egs"),
                    epic_root.resolve(),
                )

    def test_client_launch_kh3_game_uses_epic_manifest_when_available(self) -> None:
        with TemporaryDirectory() as temp_dir:
            install_root = Path(temp_dir) / "KH_3"
            game_root = install_root / "KINGDOM HEARTS III"
            executable = game_root / "Binaries" / "Win64" / "KINGDOM HEARTS III.exe"
            executable.parent.mkdir(parents=True)
            executable.write_bytes(b"exe")

            manifest_dir = Path(temp_dir) / "Manifests"
            manifest_dir.mkdir()
            (manifest_dir / "kh3.item").write_text(json.dumps({
                "AppName": "fd711544a06543e0ab1b0808de334120",
                "InstallLocation": str(install_root),
                "LaunchExecutable": "KINGDOM HEARTS III/Binaries/Win64/KINGDOM HEARTS III.exe",
            }), encoding="utf-8")

            with patch.dict(os.environ, {"KH3_EPIC_MANIFEST_DIR": str(manifest_dir)}), \
                    patch("worlds.kh3.platform_support.game_launch.os.startfile", create=True) as startfile, \
                    patch("worlds.kh3.platform_support.game_launch.subprocess.Popen") as popen:
                launch_kh3_game_config(game_root)

            startfile.assert_called_once_with(
                "com.epicgames.launcher://apps/fd711544a06543e0ab1b0808de334120?action=launch&silent=true"
            )
            popen.assert_not_called()

    def test_client_detects_kh3_install_from_epic_manifest(self) -> None:
        with TemporaryDirectory() as temp_dir:
            install_root = Path(temp_dir) / "KH_3"
            game_root = install_root / "KINGDOM HEARTS III"
            executable = game_root / "Binaries" / "Win64" / "KINGDOM HEARTS III.exe"
            executable.parent.mkdir(parents=True)
            executable.write_bytes(b"exe")

            manifest_dir = Path(temp_dir) / "Manifests"
            manifest_dir.mkdir()
            (manifest_dir / "kh3.item").write_text(json.dumps({
                "AppName": "KH_3",
                "InstallLocation": str(install_root),
                "LaunchExecutable": "KINGDOM HEARTS III/Binaries/Win64/KINGDOM HEARTS III.exe",
            }), encoding="utf-8")

            with patch.dict(os.environ, {"KH3_EPIC_MANIFEST_DIR": str(manifest_dir)}), patch(
                "worlds.kh3.platform_support.game_discovery._default_steamapps_dirs",
                return_value=[],
            ), patch(
                "worlds.kh3.platform_support.game_discovery._default_epic_kh3_roots",
                return_value=[],
            ):
                self.assertEqual(detect_kh3_install_dir(), game_root.resolve())

    def test_runtime_installer_enables_probe_mod_without_clobbering_mods_txt(self) -> None:
        with TemporaryDirectory() as temp_dir:
            mods_txt = Path(temp_dir) / "Mods" / "mods.txt"
            mods_txt.parent.mkdir(parents=True)
            mods_txt.write_text("ActorDumperMod : 0\nKH3APProbeCpp : 0\n", encoding="utf-8")

            result = installer_ue4ss_config.ensure_mod_enabled(mods_txt, "KH3APProbeCpp")

            self.assertTrue(result["changed"])
            self.assertEqual(
                mods_txt.read_text(encoding="utf-8"),
                "ActorDumperMod : 0\nKH3APProbeCpp : 1\n",
            )

    def test_runtime_installer_can_disable_probe_mod_without_clobbering_mods_txt(self) -> None:
        with TemporaryDirectory() as temp_dir:
            mods_txt = Path(temp_dir) / "Mods" / "mods.txt"
            mods_txt.parent.mkdir(parents=True)
            mods_txt.write_text("ActorDumperMod : 0\nKH3APProbeCpp : 1\n", encoding="utf-8")

            result = installer_ue4ss_config.ensure_mod_enabled(mods_txt, "KH3APProbeCpp", enabled=False)

            self.assertTrue(result["changed"])
            self.assertEqual(
                mods_txt.read_text(encoding="utf-8"),
                "ActorDumperMod : 0\nKH3APProbeCpp : 0\n",
            )

    def test_runtime_installer_disables_legacy_lua_mod_only_when_present(self) -> None:
        with TemporaryDirectory() as temp_dir:
            mods_txt = Path(temp_dir) / "Mods" / "mods.txt"
            mods_txt.parent.mkdir(parents=True)
            mods_txt.write_text("ActorDumperMod : 0\nKH3APProbe : 1\n", encoding="utf-8")

            result = installer_ue4ss_config.ensure_mod_enabled(
                mods_txt,
                "KH3APProbe",
                enabled=False,
                add_if_missing=False,
            )

            self.assertTrue(result["changed"])
            self.assertEqual(
                mods_txt.read_text(encoding="utf-8"),
                "ActorDumperMod : 0\nKH3APProbe : 0\n",
            )

            clean_mods_txt = Path(temp_dir) / "CleanMods" / "mods.txt"
            missing_result = installer_ue4ss_config.ensure_mod_enabled(
                clean_mods_txt,
                "KH3APProbe",
                enabled=False,
                add_if_missing=False,
            )

            self.assertFalse(missing_result["changed"])
            self.assertFalse(clean_mods_txt.exists())

    def test_runtime_installer_resolves_zip_resources_one_path_part_at_a_time(self) -> None:
        class SinglePartResource:
            def __init__(self, parts: tuple[str, ...] = ()) -> None:
                self.parts = parts

            def joinpath(self, part: str):
                return SinglePartResource((*self.parts, part))

        root = SinglePartResource()
        with patch.object(installer_resources.resources, "files", return_value=root):
            resource = installer_resources._runtime_resource("runtime", "Win64")

        self.assertEqual(resource.parts, ("runtime", "Win64"))

    def test_runtime_installer_reads_runtime_resources_from_apworld_without_zipfile_path(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            apworld_path = root / "kh3.apworld"
            with zipfile.ZipFile(apworld_path, "w") as archive:
                archive.writestr("kh3/runtime/Win64/UE4SS.dll", b"ue4ss")
                archive.writestr("kh3/runtime/Win64/UE4SS-settings.ini", b"settings")

            module_file = str(apworld_path) + "\\kh3\\installer_support\\resources.py"
            with patch.object(installer_resources, "__file__", module_file):
                resource = installer_resources._runtime_resource("runtime", "Win64")
                children = {child.name: child for child in resource.iterdir()}

                self.assertTrue(resource.is_dir())
                self.assertEqual(children["UE4SS.dll"].read_bytes(), b"ue4ss")
                self.assertEqual(children["UE4SS.dll"].stat().st_size, 5)
                self.assertTrue(children["UE4SS-settings.ini"].is_file())

    def test_data_loader_reads_extracted_data_from_apworld_without_zipfile_path(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            apworld_path = root / "kh3.apworld"
            payload = {"items": [{"name": "Potion", "id": "ITEM_POTION"}], "locations": [], "meta": {}}
            with zipfile.ZipFile(apworld_path, "w") as archive:
                archive.writestr("kh3/data/extracted_locations.json", json.dumps(payload))

            module_file = str(apworld_path) + "\\kh3\\Data.py"
            with patch.object(Data, "__file__", module_file), patch.object(
                Data,
                "finalize_location_catalogs",
                side_effect=lambda extracted: extracted,
            ):
                Data.load_extracted_data.cache_clear() if hasattr(Data.load_extracted_data, "cache_clear") else None
                loaded_payload = Data.load_extracted_data()

            self.assertEqual(loaded_payload, payload)


    def test_runtime_install_status_uses_resource_metadata_without_reading_bundle_bytes(self) -> None:
        class MetadataOnlyFile:
            def __init__(self, size: int) -> None:
                self._size = size

            def is_file(self) -> bool:
                return True

            def stat(self):
                return type("Stat", (), {"st_size": self._size})()

            def read_bytes(self) -> bytes:
                raise AssertionError("runtime status should not read bundled resource bytes")

        class MetadataOnlyDir:
            def is_dir(self) -> bool:
                return True

            def joinpath(self, name: str):
                if name == "ue4ss-release.json":
                    return installer_resources._MissingRuntimeResource(name)
                return MetadataOnlyFile(5)

        with TemporaryDirectory() as temp_dir:
            kh3_root = Path(temp_dir) / "KINGDOM HEARTS III"
            win64 = kh3_root / "Binaries" / "Win64"
            win64.mkdir(parents=True)
            for name in installer_ue4ss_config.UE4SS_MANAGED_FILES:
                (win64 / name).write_bytes(b"12345")

            with patch.object(installer_resources, "RUNTIME_WIN64_DIR", Path(temp_dir) / "missing-runtime"), \
                    patch.object(installer_resources, "_runtime_resource", return_value=MetadataOnlyDir()):
                status = installer_status.runtime_install_status(kh3_root)

        self.assertFalse(status["ue4ss_custom_conflict"])
        self.assertEqual(
            status["ue4ss_file_state"],
            {name: "installed" if name in installer_status.DLL_NAMES else "bundled"
             for name in installer_ue4ss_config.UE4SS_MANAGED_FILES},
        )

    def test_runtime_install_status_indexes_apworld_zip_once(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            apworld_path = root / "kh3.apworld"
            with zipfile.ZipFile(apworld_path, "w") as archive:
                for name in installer_ue4ss_config.UE4SS_MANAGED_FILES:
                    archive.writestr(f"kh3/runtime/Win64/{name}", b"12345")
                archive.writestr("kh3/runtime/Mods/KH3APProbeCpp/dlls/main.dll", b"probe")

            kh3_root = root / "KINGDOM HEARTS III"
            win64 = kh3_root / "Binaries" / "Win64"
            win64.mkdir(parents=True)
            for name in installer_ue4ss_config.UE4SS_MANAGED_FILES:
                (win64 / name).write_bytes(b"12345")
            (win64 / "Mods").mkdir()
            (win64 / "Mods" / "mods.txt").write_text("KH3APProbeCpp : 1\n", encoding="utf-8")
            probe_dll = win64 / "Mods" / "KH3APProbeCpp" / "dlls" / "main.dll"
            probe_dll.parent.mkdir(parents=True)
            probe_dll.write_bytes(b"probe")
            probe_config = win64 / "Mods" / "KH3APProbeCpp" / "config.lua"
            probe_config.parent.mkdir(parents=True, exist_ok=True)
            probe_config.write_text("return {}\n", encoding="utf-8")

            real_zip_file = zipfile.ZipFile
            open_count = 0

            class CountingZipFile(real_zip_file):
                def __init__(self, *args, **kwargs):
                    nonlocal open_count
                    open_count += 1
                    super().__init__(*args, **kwargs)

            module_file = str(apworld_path) + "\\kh3\\installer_support\\resources.py"
            with patch.object(installer_resources, "__file__", module_file), \
                    patch.object(installer_resources, "RUNTIME_WIN64_DIR", root / "missing-runtime"), \
                    patch.object(installer_resources, "RUNTIME_PROBE_DLL", root / "missing-probe.dll"), \
                    patch.object(installer_resources.zipfile, "ZipFile", CountingZipFile):
                status = installer_status.runtime_install_status(kh3_root)

            self.assertEqual(open_count, 1)
            self.assertTrue(status["bundle_win64_exists"])
            self.assertTrue(status["bundle_probe_exists"])
            self.assertEqual(
                status["ue4ss_file_state"],
                {name: "installed" if name in installer_status.DLL_NAMES else "bundled"
                 for name in installer_ue4ss_config.UE4SS_MANAGED_FILES},
            )

    def test_runtime_install_status_treats_zipfile_internal_failure_as_unavailable(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            apworld_path = root / "kh3.apworld"
            apworld_path.write_bytes(b"not consulted")
            kh3_root = root / "KINGDOM HEARTS III"
            (kh3_root / "Binaries" / "Win64").mkdir(parents=True)

            module_file = str(apworld_path) + "\\kh3\\installer_support\\resources.py"
            with patch.object(installer_resources, "__file__", module_file), \
                    patch.object(installer_resources, "RUNTIME_WIN64_DIR", root / "missing-runtime"), \
                    patch.object(installer_resources, "RUNTIME_PROBE_DLL", root / "missing-probe.dll"), \
                    patch.object(installer_resources.zipfile, "ZipFile", side_effect=TypeError("'ZipInfo' object is not iterable")):
                status = installer_status.runtime_install_status(kh3_root)

            self.assertFalse(status["bundle_win64_exists"])
            self.assertFalse(status["bundle_probe_exists"])

    def test_runtime_install_status_treats_importlib_resource_failure_as_unavailable(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            kh3_root = root / "KINGDOM HEARTS III"
            (kh3_root / "Binaries" / "Win64").mkdir(parents=True)

            with patch.object(installer_resources, "__file__", str(root / "resources.py")), \
                    patch.object(installer_resources, "RUNTIME_WIN64_DIR", root / "missing-runtime"), \
                    patch.object(installer_resources, "RUNTIME_PROBE_DLL", root / "missing-probe.dll"), \
                    patch.object(installer_resources.resources, "files", side_effect=TypeError("zip path failure")):
                status = installer_status.runtime_install_status(kh3_root)

            self.assertFalse(status["bundle_win64_exists"])
            self.assertFalse(status["bundle_probe_exists"])
            self.assertFalse(status["ue4ss_custom_conflict"])

    def test_runtime_installer_applies_linux_proton_dwmapi_override(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            steamapps = root / "Steam" / "steamapps"
            kh3_root = steamapps / "common" / "KINGDOM HEARTS III" / "KINGDOM HEARTS III"
            user_reg = steamapps / "compatdata" / installer_platform.KH3_STEAM_APP_ID / "pfx" / "user.reg"
            user_reg.parent.mkdir(parents=True)
            user_reg.write_text(
                "[Software\\\\Wine\\\\DllOverrides]\n"
                '"global"="builtin"\n'
                "\n"
                "[Software\\\\Wine\\\\AppDefaults\\\\KINGDOM HEARTS III.exe\\\\DllOverrides] 1\n"
                '"dwmapi"="builtin"\n'
                '"xinput1_3"="native,builtin"\n',
                encoding="utf-8",
            )

            with patch.object(installer_platform.os, "name", "posix"):
                result = installer_platform.ensure_proton_dwmapi_override(kh3_root, "20260612-190000")
                status = installer_platform.proton_dwmapi_override_status(kh3_root)

            text = user_reg.read_text(encoding="utf-8")
            self.assertTrue(result["applicable"])
            self.assertTrue(result["changed"])
            self.assertTrue(Path(result["backup"]).is_file())
            self.assertTrue(status["installed"])
            self.assertIn('"dwmapi"="native,builtin"', text)
            self.assertIn('"xinput1_3"="native,builtin"', text)
            self.assertIn('"global"="builtin"', text)

    def test_runtime_installer_leaves_existing_proton_dwmapi_override_unchanged(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            steamapps = root / "Steam" / "steamapps"
            kh3_root = steamapps / "common" / "KINGDOM HEARTS III" / "KINGDOM HEARTS III"
            user_reg = steamapps / "compatdata" / installer_platform.KH3_STEAM_APP_ID / "pfx" / "user.reg"
            user_reg.parent.mkdir(parents=True)
            original_text = (
                "[Software\\\\Wine\\\\AppDefaults\\\\KINGDOM HEARTS III.exe\\\\DllOverrides] 1\n"
                '"dwmapi"="native,builtin"\n'
            )
            user_reg.write_text(original_text, encoding="utf-8")

            with patch.object(installer_platform.os, "name", "posix"):
                result = installer_platform.ensure_proton_dwmapi_override(kh3_root, "20260612-190000")

            self.assertTrue(result["applicable"])
            self.assertFalse(result["changed"])
            self.assertIsNone(result["backup"])
            self.assertEqual(user_reg.read_text(encoding="utf-8"), original_text)
            self.assertFalse(list(user_reg.parent.glob("user.reg.apbak-*")))

    def test_runtime_installer_skips_proton_dwmapi_override_without_steam_prefix(self) -> None:
        with TemporaryDirectory() as temp_dir:
            kh3_root = Path(temp_dir) / "KINGDOM HEARTS III"

            with patch.object(installer_platform.os, "name", "posix"):
                result = installer_platform.ensure_proton_dwmapi_override(kh3_root, "20260612-190000")

            self.assertFalse(result["applicable"])
            self.assertFalse(result["changed"])
            self.assertEqual(result["errors"], [])
            self.assertEqual(result["reason"], "steamapps_not_found")


    def test_pak_generator_tool_download_env_does_not_inject_certificate_roots(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            env = PakGenerator._tool_download_env()

        self.assertNotIn("SSL_CERT_FILE", env)
        self.assertNotIn("CURL_CA_BUNDLE", env)
        self.assertNotIn("REQUESTS_CA_BUNDLE", env)

    def test_pak_generator_tool_download_env_preserves_host_trust_settings(self) -> None:
        host_settings = {
            "SSL_CERT_FILE": "/etc/ssl/certs/ca-certificates.crt",
            "CURL_CA_BUNDLE": "/etc/ssl/certs/ca-certificates.crt",
            "REQUESTS_CA_BUNDLE": "/etc/ssl/certs/ca-certificates.crt",
        }
        with patch.dict(os.environ, host_settings, clear=True):
            env = PakGenerator._tool_download_env()

        for variable, value in host_settings.items():
            self.assertEqual(env[variable], value)

    def test_pak_generator_tool_download_env_removes_appimage_and_steam_loader_state(self) -> None:
        with patch.dict(
                os.environ,
                {
                    "LD_LIBRARY_PATH": "/tmp/.mount_Archipelago/usr/lib",
                    "LD_PRELOAD": "/home/deck/.local/share/Steam/ubuntu12_32/gameoverlayrenderer.so",
                    "HTTPS_PROXY": "http://proxy.example.test:8080",
                },
                clear=True,
            ):
            env = PakGenerator._tool_download_env()

        self.assertNotIn("LD_LIBRARY_PATH", env)
        self.assertNotIn("LD_PRELOAD", env)
        self.assertEqual(env["HTTPS_PROXY"], "http://proxy.example.test:8080")

    def test_pak_generator_tool_download_uses_https_context(self) -> None:
        with TemporaryDirectory() as temp_dir:
            destination = Path(temp_dir) / "tool.bin"
            ssl_context = object()
            with patch.object(PakGenerator, "_tool_download_ssl_context", return_value=ssl_context), \
                    patch.object(
                        PakGenerator.urllib.request,
                        "urlopen",
                        return_value=BytesIO(b"downloaded tool"),
                    ) as urlopen:
                PakGenerator._download_file("https://example.test/tool", destination)
            request = urlopen.call_args.args[0]
            self.assertEqual(request.full_url, "https://example.test/tool")
            self.assertEqual(request.get_header("User-agent"), PakGenerator.KH3_TOOL_DOWNLOAD_USER_AGENT)
            self.assertIs(urlopen.call_args.kwargs["context"], ssl_context)
            self.assertEqual(destination.read_bytes(), b"downloaded tool")

    def test_pak_generator_frozen_linux_download_skips_in_process_openssl(self) -> None:
        with TemporaryDirectory() as temp_dir:
            destination = Path(temp_dir) / "tool.bin"

            def fake_run(command, **_kwargs):
                output_index = command.index("--output") + 1
                Path(command[output_index]).write_bytes(b"downloaded with host curl")
                return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

            def fake_executable(executable: str) -> str | None:
                return "/usr/bin/curl" if executable == "curl" else None

            with patch.object(PakGenerator.Utils, "is_linux", True), \
                    patch.object(PakGenerator.Utils, "is_frozen", return_value=True), \
                    patch.object(PakGenerator.urllib.request, "urlopen") as urlopen, \
                    patch.object(PakGenerator, "_tool_download_ssl_context") as ssl_context, \
                    patch.object(PakGenerator, "executable_on_path", side_effect=fake_executable), \
                    patch.object(PakGenerator.subprocess, "run", side_effect=fake_run):
                PakGenerator._download_file("https://example.test/tool", destination)

            self.assertEqual(destination.read_bytes(), b"downloaded with host curl")
            urlopen.assert_not_called()
            ssl_context.assert_not_called()

    def test_pak_generator_tool_download_falls_back_to_host_curl(self) -> None:
        with TemporaryDirectory() as temp_dir:
            destination = Path(temp_dir) / "tool.bin"

            def fake_run(command, **_kwargs):
                output_index = command.index("--output") + 1
                Path(command[output_index]).write_bytes(b"downloaded with curl")
                return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

            def fake_executable(executable: str) -> str | None:
                return "/usr/bin/curl" if executable == "curl" else None

            with patch.object(
                    PakGenerator.urllib.request,
                    "urlopen",
                    side_effect=OSError("certificate verify failed"),
                ), patch.object(
                    PakGenerator,
                    "executable_on_path",
                    side_effect=fake_executable,
                ), patch.object(
                    PakGenerator.subprocess,
                    "run",
                    side_effect=fake_run,
                ) as run:
                PakGenerator._download_file("https://example.test/tool", destination)

            self.assertEqual(destination.read_bytes(), b"downloaded with curl")
            self.assertEqual(run.call_args.args[0][0], "/usr/bin/curl")
            self.assertIn("--fail", run.call_args.args[0])
            self.assertIn("--location", run.call_args.args[0])


    def test_pak_generator_uses_python_pak_on_windows(self) -> None:
        with patch.dict(os.environ, {}, clear=True), \
                patch.object(PakGenerator, "_download_file") as download:
            tools = PakGenerator.ensure_kh3_generation_tools_available()
        self.assertEqual(tools["randomizer"]["implementation"], "python")
        self.assertEqual(tools["pak"]["implementation"], "python")
        download.assert_not_called()


    def test_pak_generator_rejects_incomplete_pak_output(self) -> None:
        with TemporaryDirectory() as temp_dir:
            work_dir = Path(temp_dir)
            seed = "AP_18694758094464816395-5-rederbkh3"
            pak_path = work_dir / "pakchunk9-rederbkh3-randomizer-AP_18694758094464816395-5_99_P.pak"
            pak_path.write_bytes(b"x" * 872)

            with self.assertRaises(PakGenerator.KH3PakGenerationError) as raised:
                PakGenerator.select_generated_pak(work_dir, seed, "rederbkh3")

        self.assertIn("incomplete pak", str(raised.exception))
        self.assertIn("872 bytes", str(raised.exception))

    def test_pak_generator_accepts_output_at_minimum_size(self) -> None:
        with TemporaryDirectory() as temp_dir:
            work_dir = Path(temp_dir)
            seed = "AP_18694758094464816395-5-rederbkh3"
            pak_path = work_dir / "pakchunk9-rederbkh3-randomizer-AP_18694758094464816395-5_99_P.pak"
            pak_path.write_bytes(b"x" * PakGenerator.KH3_MINIMUM_GENERATED_PAK_SIZE_BYTES)

            selected = PakGenerator.select_generated_pak(work_dir, seed, "rederbkh3")

        self.assertEqual(selected, pak_path)

    def test_pak_generator_removes_stale_matching_output_before_generation(self) -> None:
        with TemporaryDirectory() as temp_dir:
            work_dir = Path(temp_dir)
            seed = "AP_18694758094464816395-5-rederbkh3"
            stale_path = work_dir / "pakchunk9-rederbkh3-randomizer-AP_18694758094464816395-5_99_P.pak"
            unrelated_path = work_dir / "pakchunk9-other-randomizer-AP_99999999999999999999-1_99_P.pak"
            stale_path.write_bytes(b"stale")
            unrelated_path.write_bytes(b"keep")

            PakGenerator.remove_existing_pak_candidates(work_dir, seed)

            self.assertFalse(stale_path.exists())
            self.assertTrue(unrelated_path.exists())


    def test_runtime_installer_installs_release_and_bundle_files(self) -> None:
        with TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            bundle_win64 = temp_root / "bundle" / "Win64"
            bundle_probe = temp_root / "bundle" / "Mods" / "KH3APProbeCpp" / "dlls" / "main.dll"
            bundle_win64.mkdir(parents=True)
            bundle_probe.parent.mkdir(parents=True)
            (bundle_win64 / "UE4SS.dll").write_bytes(b"ue4ss")
            (bundle_win64 / "UE4SS-settings.ini").write_text("[UE4SS]\n", encoding="utf-8")
            (bundle_win64 / "dwmapi.dll").write_bytes(b"proxy")
            (bundle_win64 / "xinput1_3.dll").write_bytes(b"xinput")
            bundle_probe.write_bytes(b"probe")

            kh3_root = temp_root / "KINGDOM HEARTS III"
            win64 = kh3_root / "Binaries" / "Win64"
            win64.mkdir(parents=True)
            stale_guobject_signature = win64 / "UE4SS_Signatures" / "GUObjectArray.lua"
            stale_guobject_signature.parent.mkdir(parents=True)
            stale_guobject_signature.write_text("stale", encoding="utf-8")
            legacy_lua_probe = win64 / "Mods" / "KH3APProbe"
            legacy_lua_probe.mkdir(parents=True)
            (legacy_lua_probe / "enabled.txt").write_text("", encoding="utf-8")

            with runtime_release_fixture(bundle_win64), \
                    patch.object(installer_resources, "RUNTIME_WIN64_DIR", bundle_win64), \
                    patch.object(installer_resources, "RUNTIME_PROBE_DLL", bundle_probe):
                result = installer_installation.install_kh3_runtime(kh3_root)
                status = installer_status.runtime_install_status(kh3_root)

            self.assertEqual(result["status"], "ok")
            self.assertTrue((kh3_root / "Binaries" / "Win64" / "UE4SS.dll").is_file())
            self.assertTrue((kh3_root / "Binaries" / "Win64" / "dwmapi.dll").is_file())
            self.assertFalse((kh3_root / "Binaries" / "Win64" / "xinput1_3.dll").is_file())
            self.assertEqual(result["stale_proxies"]["removed"], [])
            self.assertEqual(result["stale_runtime_files"]["removed"], ["UE4SS_Signatures/GUObjectArray.lua"])
            self.assertFalse(stale_guobject_signature.exists())
            self.assertTrue(result["legacy_lua_probe"]["changed"])
            self.assertFalse(legacy_lua_probe.exists())
            self.assertTrue(Path(result["legacy_lua_probe"]["backup"]).is_dir())
            self.assertTrue((kh3_root / "Binaries" / "Win64" / "Mods" / "KH3APProbeCpp" / "dlls" / "main.dll").is_file())
            self.assertFalse(
                (
                    kh3_root
                    / "Binaries"
                    / "Win64"
                    / "KINGDOM HEARTS III"
                    / "Mods"
                    / "KH3APProbeCpp"
                    / "dlls"
                    / "main.dll"
                ).exists()
            )
            self.assertTrue((kh3_root / "Binaries" / "Win64" / "Mods" / "KH3APProbeCpp" / "config.lua").is_file())
            self.assertTrue(status["probe_mod_enabled"])
            self.assertFalse(status["legacy_probe_mod_enabled"])
            self.assertTrue(status["probe_mod_layouts_enabled"])
            self.assertTrue(status["probe_dll_layouts_ready"])
            self.assertTrue(status["probe_single_layout_ready"])
            self.assertTrue(status["ue4ss_settings_compliant"])
            self.assertEqual(status["ue4ss_file_state"]["UE4SS-settings.ini"], "different")
            self.assertFalse(status["ue4ss_custom_conflict"])
            self.assertEqual((kh3_root / "Binaries" / "Win64" / "Mods" / "mods.txt").read_text(encoding="utf-8"), "KH3APProbeCpp : 1\n")
            self.assertEqual(
                (
                    kh3_root
                    / "Binaries"
                    / "Win64"
                    / "KINGDOM HEARTS III"
                    / "Mods"
                    / "mods.txt"
                ).read_text(encoding="utf-8"),
                "KH3APProbeCpp : 0\n",
            )
            self.assertEqual(
                result["ue4ss_settings"]["changed"],
                [
                    "UE4SS-settings.ini",
                    "CustomGameConfigs/Kingdom Hearts 3/UE4SS-settings.ini",
                    "KINGDOM HEARTS III/UE4SS-settings.ini",
                ],
            )
            for relative_path in installer_ue4ss_config.ue4ss_settings_relative_paths(win64):
                text = (win64 / relative_path).read_text(encoding="utf-8")
                self.assertIn("HookProcessInternal = 0", text)
                self.assertIn("HookProcessLocalScriptFunction = 0", text)
                self.assertIn("HookUObjectProcessEvent = 0", text)
                self.assertIn("HookGameViewportClientTick = 0", text)
                self.assertIn(f"ModsFolderPath = {installer_paths.wine_visible_path_text(win64 / 'Mods')}", text)
                self.assertIn(
                    f"ControllingModsTxt = {installer_paths.wine_visible_path_text(win64 / 'Mods' / 'mods.txt')}",
                    text,
                )

    def test_runtime_installer_preempts_other_mod_nested_ue4ss_without_overwriting_it(self) -> None:
        with TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            bundle_win64 = temp_root / "bundle" / "Win64"
            bundle_probe = temp_root / "bundle" / "Mods" / "KH3APProbeCpp" / "dlls" / "main.dll"
            bundle_win64.mkdir(parents=True)
            bundle_probe.parent.mkdir(parents=True)
            (bundle_win64 / "UE4SS.dll").write_bytes(b"ap-runtime")
            (bundle_win64 / "UE4SS-settings.ini").write_text("[UE4SS]\n", encoding="utf-8")
            (bundle_win64 / "dwmapi.dll").write_bytes(b"proxy")
            bundle_probe.write_bytes(b"probe")

            kh3_root = temp_root / "KINGDOM HEARTS III"
            win64 = kh3_root / "Binaries" / "Win64"
            nested_ue4ss = win64 / "ue4ss" / "UE4SS.dll"
            nested_ue4ss.parent.mkdir(parents=True)
            nested_ue4ss.write_bytes(b"other-mod-runtime")
            nested_settings = nested_ue4ss.parent / "UE4SS-settings.ini"
            nested_settings.write_text("other mod settings\n", encoding="utf-8")

            with runtime_release_fixture(bundle_win64), \
                    patch.object(installer_resources, "RUNTIME_WIN64_DIR", bundle_win64), \
                    patch.object(installer_resources, "RUNTIME_PROBE_DLL", bundle_probe):
                before = installer_status.runtime_install_status(kh3_root)
                result = installer_installation.install_kh3_runtime(kh3_root)
                after = installer_status.runtime_install_status(kh3_root)

            root_ue4ss = win64 / "UE4SS.dll"
            self.assertTrue(before["ue4ss_loader_precedence_conflict"])
            self.assertEqual(before["ue4ss_effective_runtime_path"], str(nested_ue4ss))
            self.assertTrue(result["existing_ue4ss_custom_conflict"])
            self.assertTrue(result["ue4ss_proxy_override"]["changed"])
            self.assertIsNone(result["ue4ss_proxy_override"]["backup"])
            self.assertEqual((win64 / "override.txt").read_text(encoding="utf-8"), ".\n")
            self.assertEqual(root_ue4ss.read_bytes(), b"ap-runtime")
            self.assertEqual(nested_ue4ss.read_bytes(), b"other-mod-runtime")
            self.assertEqual(nested_settings.read_text(encoding="utf-8"), "other mod settings\n")
            self.assertTrue(after["ue4ss_proxy_override_installed"])
            self.assertFalse(after["ue4ss_loader_precedence_conflict"])
            self.assertFalse(after["ue4ss_custom_conflict"])
            self.assertEqual(after["ue4ss_effective_runtime_path"], str(root_ue4ss))
            self.assertTrue(after["probe_dll_layouts_ready"])

    def test_runtime_installer_backs_up_existing_proxy_override(self) -> None:
        with TemporaryDirectory() as temp_dir:
            win64 = Path(temp_dir) / "Win64"
            win64.mkdir()
            override = win64 / "override.txt"
            override.write_text("custom-runtime\n", encoding="utf-8")

            result = installer_ue4ss_config.ensure_ue4ss_proxy_override(win64, "20260831-020000")

            self.assertTrue(result["changed"])
            self.assertEqual(override.read_text(encoding="utf-8"), ".\n")
            self.assertEqual(Path(result["backup"]).read_text(encoding="utf-8"), "custom-runtime\n")
            self.assertTrue(installer_ue4ss_config.ue4ss_proxy_override_status(win64)["installed"])

    def test_runtime_installer_removes_retired_loader_canary(self) -> None:
        with TemporaryDirectory() as temp_dir:
            win64 = Path(temp_dir) / "Win64"
            canary = win64 / "Mods" / "KH3APProbeCpp" / "runtime_loader_canary.jsonl"
            canary.parent.mkdir(parents=True)
            canary.write_text("retired canary\n", encoding="utf-8")

            result = installer_ue4ss_config.remove_stale_runtime_files(win64, "20260831-020000")

            self.assertFalse(canary.exists())
            self.assertIn("Mods/KH3APProbeCpp/runtime_loader_canary.jsonl", result["removed"])
            self.assertEqual(len(result["backups"]), 1)
            self.assertEqual(Path(result["backups"][0]).read_text(encoding="utf-8"), "retired canary\n")

    def test_runtime_install_status_detects_split_probe_layout_drift(self) -> None:
        with TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            bundle_win64 = temp_root / "bundle" / "Win64"
            bundle_probe = temp_root / "bundle" / "Mods" / "KH3APProbeCpp" / "dlls" / "main.dll"
            bundle_win64.mkdir(parents=True)
            bundle_probe.parent.mkdir(parents=True)
            (bundle_win64 / "UE4SS.dll").write_bytes(b"ue4ss")
            (bundle_win64 / "UE4SS-settings.ini").write_text("[UE4SS]\n", encoding="utf-8")
            bundle_probe.write_bytes(b"probe-current")

            kh3_root = temp_root / "KINGDOM HEARTS III"
            win64 = kh3_root / "Binaries" / "Win64"
            root_probe = win64 / "Mods" / "KH3APProbeCpp" / "dlls" / "main.dll"
            legacy_probe = win64 / "KINGDOM HEARTS III" / "Mods" / "KH3APProbeCpp" / "dlls" / "main.dll"
            root_probe.parent.mkdir(parents=True)
            legacy_probe.parent.mkdir(parents=True)
            root_probe.write_bytes(b"probe-current")
            legacy_probe.write_bytes(b"probe-old")
            (win64 / "Mods" / "mods.txt").write_text("KH3APProbeCpp : 1\n", encoding="utf-8")
            legacy_mods_txt = win64 / "KINGDOM HEARTS III" / "Mods" / "mods.txt"
            legacy_mods_txt.parent.mkdir(parents=True, exist_ok=True)
            legacy_mods_txt.write_text("KH3APProbeCpp : 1\n", encoding="utf-8")

            with patch.object(installer_resources, "RUNTIME_WIN64_DIR", bundle_win64), \
                    patch.object(installer_resources, "RUNTIME_PROBE_DLL", bundle_probe):
                status = installer_status.runtime_install_status(kh3_root)

            self.assertTrue(status["probe_dll_matches_bundle"])
            self.assertFalse(status["legacy_probe_dll_matches_bundle"])
            self.assertFalse(status["probe_dll_layouts_ready"])
            self.assertTrue(status["probe_mod_enabled"])
            self.assertTrue(status["legacy_probe_mod_enabled"])
            self.assertFalse(status["probe_mod_layouts_enabled"])
            self.assertTrue(status["probe_legacy_layout_present"])

    def test_runtime_installer_writes_egs_guobject_array_signature_override(self) -> None:
        with TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            bundle_win64 = temp_root / "bundle" / "Win64"
            bundle_probe = temp_root / "bundle" / "Mods" / "KH3APProbeCpp" / "dlls" / "main.dll"
            bundle_win64.mkdir(parents=True)
            bundle_probe.parent.mkdir(parents=True)
            (bundle_win64 / "UE4SS.dll").write_bytes(b"ue4ss")
            (bundle_win64 / "UE4SS-settings.ini").write_text("[UE4SS]\n", encoding="utf-8")
            (bundle_win64 / "UE4SS_Signatures").mkdir()
            (bundle_win64 / "UE4SS_Signatures" / "FName_Constructor.lua").write_text("fname", encoding="utf-8")
            steam_config_dir = bundle_win64 / "KINGDOM HEARTS III"
            steam_config_dir.mkdir()
            (steam_config_dir / "MemberVariableLayout.ini").write_text("layout", encoding="utf-8")
            (steam_config_dir / "UE4SS-settings.ini").write_text("[Hooks]\n", encoding="utf-8")
            (steam_config_dir / "VTableLayout.ini").write_text("vtable", encoding="utf-8")
            bundle_probe.write_bytes(b"probe")

            kh3_root = temp_root / "EGS" / "KH_3" / "KINGDOM HEARTS III"
            win64 = kh3_root / "Binaries" / "Win64"
            win64.mkdir(parents=True)
            (win64 / "KINGDOM HEARTS III.exe").write_bytes(bytes.fromhex(
                "48 8D 3D 11 22 33 44 48 89 78 08 48 8B CF FF 15 "
                "55 66 77 88 85 C0 75 0A 48 8B CF FF 15"
            ))

            with runtime_release_fixture(bundle_win64), \
                    patch.object(installer_resources, "RUNTIME_WIN64_DIR", bundle_win64), \
                    patch.object(installer_resources, "RUNTIME_PROBE_DLL", bundle_probe):
                result = installer_installation.install_kh3_runtime(kh3_root)
                status = installer_status.runtime_install_status(kh3_root)

            signature_path = win64 / "UE4SS_Signatures" / "GUObjectArray.lua"
            config_signature_path = win64 / "KH_3" / "UE4SS_Signatures" / "GUObjectArray.lua"
            self.assertEqual(result["status"], "ok")
            self.assertEqual(
                result["signature_overrides"]["installed"],
                ["UE4SS_Signatures/GUObjectArray.lua", "KH_3/UE4SS_Signatures/GUObjectArray.lua"],
            )
            self.assertIn("AddressLoaded - 0x20", signature_path.read_text(encoding="utf-8"))
            self.assertIn("AddressLoaded - 0x20", config_signature_path.read_text(encoding="utf-8"))
            self.assertTrue((win64 / "KH_3" / "MemberVariableLayout.ini").is_file())
            self.assertEqual((win64 / "KH_3" / "UE4SS_Signatures" / "FName_Constructor.lua").read_text(encoding="utf-8"), "fname")
            path_specific_probe = win64 / "KH_3" / "Mods" / "KH3APProbeCpp"
            self.assertEqual((path_specific_probe / "dlls" / "main.dll").read_bytes(), b"probe")
            self.assertTrue((path_specific_probe / "config.lua").is_file())
            self.assertEqual(
                (win64 / "KH_3" / "Mods" / "mods.txt").read_text(encoding="utf-8"),
                "KH3APProbeCpp : 1\n",
            )
            self.assertTrue(status["path_specific_probe_required"])
            self.assertTrue(status["path_specific_probe_dll_matches_bundle"])
            self.assertTrue(status["path_specific_probe_config_installed"])
            self.assertTrue(status["path_specific_probe_mod_enabled"])
            self.assertTrue(status["probe_dll_layouts_ready"])

    def test_runtime_install_status_reports_probe_dll_version_info(self) -> None:
        with TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            bundle_win64 = temp_root / "bundle" / "Win64"
            bundle_probe = temp_root / "bundle" / "Mods" / "KH3APProbeCpp" / "dlls" / "main.dll"
            bundle_win64.mkdir(parents=True)
            bundle_probe.parent.mkdir(parents=True)
            (bundle_win64 / "UE4SS.dll").write_bytes(b"ue4ss")
            (bundle_win64 / "UE4SS-settings.ini").write_text("[UE4SS]\n", encoding="utf-8")
            bundle_probe.write_bytes(b"probe")

            kh3_root = temp_root / "KINGDOM HEARTS III"
            win64 = kh3_root / "Binaries" / "Win64"
            probe_dll = win64 / "Mods" / "KH3APProbeCpp" / "dlls" / "main.dll"
            probe_dll.parent.mkdir(parents=True)
            probe_bytes = (
                "KH3APProbeCpp ModVersion 0.3.61 "
                "2026-05-07T04:25 dev-test-build"
            ).encode("utf-16le")
            probe_dll.write_bytes(probe_bytes)

            with patch.object(installer_resources, "RUNTIME_WIN64_DIR", bundle_win64), \
                    patch.object(installer_resources, "RUNTIME_PROBE_DLL", bundle_probe):
                status = installer_status.runtime_install_status(kh3_root)

            self.assertEqual(status["probe_dll_version"], "0.3.61")
            self.assertEqual(status["probe_dll_build_stamp"], "2026-05-07T04:25 dev-test-build")
            self.assertEqual(status["probe_dll_short_hash"], hashlib.sha256(probe_bytes).hexdigest().upper()[:8])

    def test_runtime_installer_enforces_required_ue4ss_settings_in_all_layouts(self) -> None:
        with TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            bundle_win64 = temp_root / "bundle" / "Win64"
            bundle_probe = temp_root / "bundle" / "Mods" / "KH3APProbeCpp" / "dlls" / "main.dll"
            bundle_win64.mkdir(parents=True)
            bundle_probe.parent.mkdir(parents=True)
            (bundle_win64 / "UE4SS.dll").write_bytes(b"ue4ss")
            (bundle_win64 / "UE4SS-settings.ini").write_text("HookProcessInternal = 0\n", encoding="utf-8")
            custom_settings = bundle_win64 / "CustomGameConfigs" / "Kingdom Hearts 3" / "UE4SS-settings.ini"
            custom_settings.parent.mkdir(parents=True)
            custom_settings.write_text("HookProcessLocalScriptFunction = 0\n", encoding="utf-8")
            legacy_settings = bundle_win64 / "KINGDOM HEARTS III" / "UE4SS-settings.ini"
            legacy_settings.parent.mkdir(parents=True)
            legacy_settings.write_text("[Hooks]\n", encoding="utf-8")
            bundle_probe.write_bytes(b"probe")

            kh3_root = temp_root / "KINGDOM HEARTS III"
            win64 = kh3_root / "Binaries" / "Win64"
            win64.mkdir(parents=True)

            with runtime_release_fixture(bundle_win64), \
                    patch.object(installer_resources, "RUNTIME_WIN64_DIR", bundle_win64), \
                    patch.object(installer_resources, "RUNTIME_PROBE_DLL", bundle_probe):
                result = installer_installation.install_kh3_runtime(kh3_root)

            self.assertEqual(result["status"], "ok")
            self.assertEqual(
                result["ue4ss_settings"]["changed"],
                [
                    "UE4SS-settings.ini",
                    "CustomGameConfigs/Kingdom Hearts 3/UE4SS-settings.ini",
                    "KINGDOM HEARTS III/UE4SS-settings.ini",
                ],
            )
            for relative_path in installer_ue4ss_config.ue4ss_settings_relative_paths(win64):
                text = (win64 / relative_path).read_text(encoding="utf-8")
                self.assertIn("HookProcessInternal = 0", text)
                self.assertIn("HookProcessLocalScriptFunction = 0", text)
                self.assertIn("HookUObjectProcessEvent = 0", text)
                self.assertIn("HookGameViewportClientTick = 0", text)
                self.assertIn(f"ModsFolderPath = {installer_paths.wine_visible_path_text(win64 / 'Mods')}", text)
                self.assertIn(
                    f"ControllingModsTxt = {installer_paths.wine_visible_path_text(win64 / 'Mods' / 'mods.txt')}",
                    text,
                )

    @unittest.skipIf(os.name == "nt", "POSIX directory replacement semantics")
    def test_runtime_installer_replaces_read_only_probe_in_writable_directory(self) -> None:
        with TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            source = temp_root / "bundled.dll"
            target_dir = temp_root / "game" / "dlls"
            target = target_dir / "main.dll"
            source.write_bytes(b"new probe")
            target_dir.mkdir(parents=True)
            target.write_bytes(b"old probe")
            target.chmod(0o444)

            result = installer_file_ops._copy_file(source, target, "20260819-170000")

            self.assertTrue(result["copied"])
            self.assertIsNone(result["error"])
            self.assertEqual(target.read_bytes(), b"new probe")
            self.assertEqual(Path(result["backup"]).read_bytes(), b"old probe")

    @unittest.skipIf(os.name == "nt", "POSIX directory replacement semantics")
    def test_runtime_installer_replaces_read_only_ue4ss_file_in_writable_directory(self) -> None:
        with TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            source_dir = temp_root / "bundle"
            target_dir = temp_root / "game"
            source = source_dir / "UE4SS.dll"
            target = target_dir / "UE4SS.dll"
            source_dir.mkdir()
            target_dir.mkdir()
            source.write_bytes(b"new runtime")
            target.write_bytes(b"old runtime")
            target.chmod(0o444)

            result = installer_file_ops._copy_tree_contents(source_dir, target_dir, "20260819-170000")

            self.assertEqual(result["copied"], ["UE4SS.dll"])
            self.assertEqual(result["errors"], [])
            self.assertEqual(target.read_bytes(), b"new runtime")
            self.assertEqual(Path(result["backups"][0]).read_bytes(), b"old runtime")

    def test_runtime_installer_reports_probe_copy_failure_without_raising(self) -> None:
        with TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            bundle_win64 = temp_root / "bundle" / "Win64"
            bundle_probe = temp_root / "bundle" / "Mods" / "KH3APProbeCpp" / "dlls" / "main.dll"
            bundle_win64.mkdir(parents=True)
            bundle_probe.parent.mkdir(parents=True)
            (bundle_win64 / "UE4SS.dll").write_bytes(b"ue4ss")
            (bundle_win64 / "UE4SS-settings.ini").write_text("[UE4SS]\n", encoding="utf-8")
            bundle_probe.write_bytes(b"probe")

            kh3_root = temp_root / "KINGDOM HEARTS III"
            (kh3_root / "Binaries" / "Win64").mkdir(parents=True)

            with runtime_release_fixture(bundle_win64), \
                    patch.object(installer_resources, "RUNTIME_WIN64_DIR", bundle_win64), \
                    patch.object(installer_resources, "RUNTIME_PROBE_DLL", bundle_probe), \
                    patch("worlds.kh3.installer_support.installation._copy_file", return_value={
                        "copied": False,
                        "skipped": False,
                        "backup": None,
                        "missing": None,
                        "error": "permission denied",
                    }):
                result = installer_installation.install_kh3_runtime(kh3_root)

            self.assertEqual(result["status"], "partial")
            self.assertIn("permission denied", "; ".join(result["errors"]))

    def test_client_runtime_install_wrapper_reports_unexpected_error_without_raising(self) -> None:
        with patch.object(runtime_install, "install_kh3_runtime", side_effect=RuntimeError("boom")):
            result = install_kh3_runtime_files(Path("C:/missing"), None)

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["errors"], ["boom"])

    def test_runtime_installer_backs_up_existing_ue4ss_without_cached_release(self) -> None:
        with TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            bundle_win64 = temp_root / "bundle" / "Win64"
            bundle_probe = temp_root / "bundle" / "Mods" / "KH3APProbeCpp" / "dlls" / "main.dll"
            bundle_win64.mkdir(parents=True)
            bundle_probe.parent.mkdir(parents=True)
            (bundle_win64 / "UE4SS.dll").write_bytes(b"release")
            (bundle_win64 / "UE4SS-settings.ini").write_text("[UE4SS]\n", encoding="utf-8")
            (bundle_win64 / "dwmapi.dll").write_bytes(b"proxy")
            (bundle_win64 / "xinput1_3.dll").write_bytes(b"xinput")
            bundle_probe.write_bytes(b"probe")

            kh3_root = temp_root / "KINGDOM HEARTS III"
            win64 = kh3_root / "Binaries" / "Win64"
            win64.mkdir(parents=True)
            (win64 / "UE4SS.dll").write_bytes(b"other")

            with runtime_release_fixture(bundle_win64), \
                    patch.object(installer_resources, "RUNTIME_WIN64_DIR", bundle_win64), \
                    patch.object(installer_resources, "RUNTIME_PROBE_DLL", bundle_probe):
                before = installer_status.runtime_install_status(kh3_root)
                result = installer_installation.install_kh3_runtime(kh3_root)

            self.assertEqual(before["ue4ss_file_state"]["UE4SS.dll"], "installed")
            self.assertFalse(before["ue4ss_custom_conflict"])
            self.assertFalse(result["existing_ue4ss_custom_conflict"])
            self.assertTrue(result["replaced_existing_ue4ss"])
            self.assertEqual((win64 / "UE4SS.dll").read_bytes(), b"release")
            self.assertTrue(list(win64.glob("UE4SS.dll.apbak-*")))
