"""Prerequisite execution and migration must not damage an existing game install."""

import hashlib
import json
import os
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from worlds.kh3.client_support.presentation import build_installed_runtime_summary
from worlds.kh3.platform_support import xinput_runtime as xinput

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
from .runtime_download_fixture import runtime_release_fixture


class TestWindowsXInput(unittest.TestCase):
    def test_status_checks_x64_pe_without_network_or_signature_work(self):
        with TemporaryDirectory() as temporary:
            dll = Path(temporary) / "xinput1_3.dll"
            header = bytearray(64)
            header[:2] = b"MZ"
            header[60:64] = (64).to_bytes(4, "little")
            with patch.object(xinput, "windows_xinput_path", return_value=dll), \
                    patch.object(xinput, "_read_url", side_effect=AssertionError("network")), \
                    patch.object(xinput, "_verify_microsoft_signature", side_effect=AssertionError("signature")):
                self.assertFalse(xinput.windows_xinput_status()["ready"])
                dll.write_bytes(header + b"PE\0\0\x4c\x01")
                self.assertFalse(xinput.windows_xinput_status()["ready"])
                dll.write_bytes(header + b"PE\0\0\x64\x86")
                self.assertTrue(xinput.windows_xinput_status()["ready"])

    def test_32_bit_client_uses_64_bit_system_directory(self):
        with patch.object(xinput.sys, "maxsize", 2**31 - 1), \
                patch.dict(os.environ, {"SystemRoot": "C:/Windows", "PROCESSOR_ARCHITEW6432": "AMD64"}):
            self.assertEqual(xinput.windows_xinput_path().parent.name, "Sysnative")

    def test_verified_existing_runtime_needs_no_package(self):
        with patch.object(xinput, "windows_xinput_status", return_value={"ready": True}), \
                patch.object(xinput, "_verify_microsoft_signature") as signature, \
                patch.object(xinput, "_directx_package") as package:
            self.assertFalse(xinput.ensure_windows_xinput()["installed_now"])
        signature.assert_called_once()
        package.assert_not_called()

    def test_signature_check_does_not_inherit_powershell_core_modules(self):
        with patch.dict(os.environ, {"PSModulePath": "PowerShell-7-only"}), \
                patch.object(xinput.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)) as run:
            xinput._verify_microsoft_signature(Path("runtime.exe"))
        self.assertNotIn("PSModulePath", run.call_args.kwargs["env"])
        self.assertEqual(run.call_args.kwargs["env"]["KH3AP_SIGNATURE_FILE"], "runtime.exe")

    def test_elevated_setup_waits_for_exit_and_reports_cancellation(self):
        for cancelled in (True, False):
            with self.subTest(cancelled=cancelled):
                shell, kernel, ole = Mock(), Mock(), Mock()
                ole.CoInitializeEx.return_value = 0
                seen = []

                def launch(pointer):
                    info = pointer._obj
                    seen.append((info.lpVerb, info.lpParameters, info.nShow))
                    if not cancelled:
                        info.hProcess = 42
                    return not cancelled

                def exit_code(process, pointer):
                    pointer._obj.value = 1
                    return True

                shell.ShellExecuteExW.side_effect = launch
                kernel.WaitForSingleObject.return_value = 0
                kernel.GetExitCodeProcess.side_effect = exit_code
                with patch.object(xinput.ctypes, "WinDLL", create=True, side_effect=[shell, kernel, ole]), \
                        patch.object(xinput.ctypes, "get_last_error", create=True, return_value=1223):
                    with self.assertRaisesRegex(RuntimeError, "administrator prompt" if cancelled else "exit code 1"):
                        xinput._run_directx_setup(Path("DXSETUP.exe"))
                self.assertEqual(seen, [("runas", None, 1)])
                ole.CoUninitialize.assert_called_once()
                if cancelled:
                    kernel.WaitForSingleObject.assert_not_called()
                else:
                    kernel.WaitForSingleObject.assert_called_once_with(42, 0xFFFFFFFF)
                    kernel.CloseHandle.assert_called_once_with(42)

    def test_package_hash_and_signature_are_required_and_cache_works_offline(self):
        data = b"signed-package-fixture"
        with TemporaryDirectory() as temporary:
            cache = Path(temporary) / "cache/package.exe"
            with patch.object(xinput, "DIRECTX_SIZE", len(data)), \
                    patch.object(xinput, "DIRECTX_SHA256", hashlib.sha256(data).hexdigest()), \
                    patch.object(xinput, "directx_cache_path", return_value=cache), \
                    patch.object(xinput, "_read_url", return_value=b"bad") as fetch, \
                    patch.object(xinput, "_verify_microsoft_signature") as signature:
                with self.assertRaisesRegex(RuntimeError, "SHA-256"):
                    xinput._directx_package()
                self.assertFalse(cache.exists())
                signature.assert_not_called()
                fetch.return_value = data
                signature.side_effect = RuntimeError("bad signature")
                with self.assertRaisesRegex(RuntimeError, "signature"):
                    xinput._directx_package()
                self.assertFalse(cache.exists())
                self.assertEqual(list(cache.parent.iterdir()), [])
                signature.side_effect = None
                self.assertEqual(xinput._directx_package(), cache)
                self.assertEqual(cache.read_bytes(), data)
                fetch.reset_mock()
                fetch.side_effect = OSError("offline")
                self.assertEqual(xinput._directx_package(), cache)
                fetch.assert_not_called()
                signature.assert_called_with(cache)

    def _extract(self, command, **kwargs):
        extracted = Path(command[2][3:])
        (extracted / "DXSETUP.exe").write_bytes(b"signed-setup-fixture")
        return subprocess.CompletedProcess(command, 0)

    def test_setup_is_visible_without_silent_switch_and_checked_after_exit(self):
        with patch.object(xinput, "windows_xinput_status", side_effect=[{"ready": False}, {"ready": True}]), \
                patch.object(xinput, "_directx_package", return_value=Path("package.exe")), \
                patch.object(xinput.subprocess, "run", side_effect=self._extract), \
                patch.object(xinput, "_verify_microsoft_signature"), \
                patch.object(xinput, "_run_directx_setup") as setup:
            result = xinput.ensure_windows_xinput()
        self.assertTrue(result["installed_now"])
        self.assertEqual(len(setup.call_args.args), 1)
        self.assertEqual(setup.call_args.args[0].name, "DXSETUP.exe")
        self.assertFalse(setup.call_args.args[0].parent.exists())

    def test_setup_cancel_or_missing_x64_runtime_stops_installation(self):
        for failure in (RuntimeError("cancelled"), None):
            with self.subTest(failure=failure), \
                    patch.object(xinput, "windows_xinput_status", return_value={"ready": False}), \
                    patch.object(xinput, "_directx_package", return_value=Path("package.exe")), \
                    patch.object(xinput.subprocess, "run", side_effect=self._extract), \
                    patch.object(xinput, "_verify_microsoft_signature"), \
                    patch.object(xinput, "_run_directx_setup", side_effect=failure):
                with self.assertRaises(RuntimeError):
                    xinput.ensure_windows_xinput()


class TestXInputMigration(unittest.TestCase):
    def test_windows_migrates_known_dll_only_after_success(self):
        for success in (False, True):
            with self.subTest(success=success), TemporaryDirectory() as temporary:
                root = Path(temporary)
                win64 = root / "Binaries/Win64"
                win64.mkdir(parents=True)
                local = win64 / "xinput1_3.dll"
                data = b"known-old-microsoft-runtime"
                local.write_bytes(data)
                with patch.object(installer_platform.os, "name", "nt"), \
                        patch.object(installer_platform, "LEGACY_XINPUT_SHA256", hashlib.sha256(data).hexdigest()), \
                        patch.object(installer_platform, "ensure_windows_xinput", return_value={"ready": True},
                                     side_effect=None if success else RuntimeError("cancelled")):
                    if success:
                        result = installer_platform.prepare_xinput_runtime(root, "test")
                        self.assertFalse(local.exists())
                        self.assertEqual(type(root)(result["legacy_backup"]).read_bytes(), data)
                    else:
                        with self.assertRaisesRegex(RuntimeError, "cancelled"):
                            installer_platform.prepare_xinput_runtime(root, "test")
                        self.assertEqual(local.read_bytes(), data)
                        self.assertEqual(list(win64.iterdir()), [local])

    def test_windows_preserves_unknown_local_xinput_and_reports_conflict(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            local = root / "Binaries/Win64/xinput1_3.dll"
            local.parent.mkdir(parents=True)
            local.write_bytes(b"another-mod")
            with patch.object(installer_platform.os, "name", "nt"), \
                    patch.object(installer_platform, "ensure_windows_xinput") as setup:
                with self.assertRaisesRegex(ValueError, "unrecognized"):
                    installer_platform.prepare_xinput_runtime(root, "test")
                setup.assert_not_called()
            self.assertEqual(local.read_bytes(), b"another-mod")

    def test_failed_prerequisite_preserves_runtime_probe_and_settings(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            win64 = root / "game/Binaries/Win64"
            bundle = root / "bundle"
            win64.mkdir(parents=True)
            bundle.mkdir()
            (bundle / "UE4SS.dll").write_bytes(b"replacement")
            originals = {"UE4SS.dll": b"runtime", "UE4SS-settings.ini": b"settings",
                         "Mods/KH3APProbeCpp/dlls/main.dll": b"probe", "xinput1_3.dll": b"old-xinput"}
            for name, data in originals.items():
                target = win64 / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
            with runtime_release_fixture(bundle), \
                    patch.object(installer_resources, "RUNTIME_WIN64_DIR", bundle), \
                    patch.object(installer_installation, "prepare_xinput_runtime", side_effect=RuntimeError("cancelled")):
                result = installer_installation.install_kh3_runtime(root / "game")
            self.assertEqual(result["status"], "error")
            self.assertIn("cancelled", result["errors"][0])
            self.assertEqual({path.relative_to(win64).as_posix(): path.read_bytes()
                              for path in win64.rglob("*") if path.is_file()}, originals)

    def test_linux_configures_game_overrides_and_preserves_other_games(self):
        for known in (False, True):
            with self.subTest(known=known), TemporaryDirectory() as temporary:
                root = Path(temporary)
                game = root / "steamapps/common/KINGDOM HEARTS III"
                local = game / "Binaries/Win64/xinput1_3.dll"
                local.parent.mkdir(parents=True)
                local.write_bytes(b"legacy" if known else b"other-mod")
                registry = root / "steamapps/compatdata/2552450/pfx/user.reg"
                registry.parent.mkdir(parents=True)
                original = (
                    'WINE REGISTRY Version 2\n\n[Software\\\\Wine\\\\DllOverrides] 1\n"xinput1_3"="native"\n\n'
                    '[Software\\\\Wine\\\\AppDefaults\\\\Other.exe\\\\DllOverrides] 1\n"xinput1_3"="native"\n\n'
                    + installer_platform.KH3_PROTON_APPDEFAULTS_DLL_OVERRIDE_SECTION + ' 1\n'
                    '"xinput1_3"="native,builtin"\n"dwmapi"="builtin"\n"other"="native"\n'
                )
                registry.write_text(original, encoding="utf-8")
                with patch.object(installer_platform.os, "name", "posix"), \
                        patch.object(installer_platform, "LEGACY_XINPUT_SHA256", hashlib.sha256(b"legacy").hexdigest()), \
                        patch.object(installer_platform, "ensure_windows_xinput", side_effect=AssertionError("Windows installer")), \
                        patch.object(xinput, "_read_url", side_effect=AssertionError("network")):
                    result = installer_platform.prepare_xinput_runtime(game, "test")
                    self.assertTrue(installer_platform.xinput_runtime_status(game)["ready"])
                    second = installer_platform.prepare_xinput_runtime(game, "test")
                self.assertFalse(second["changed"])
                text = registry.read_text(encoding="utf-8")
                self.assertIn('"xinput1_3"="builtin"', text)
                self.assertIn('"dwmapi"="native,builtin"', text)
                self.assertIn('"other"="native"', text)
                self.assertEqual(text.count('"xinput1_3"="native"'), 2)
                self.assertEqual(type(root)(result["backup"]).read_text(encoding="utf-8"), original)
                self.assertEqual(local.exists(), not known)
                if known:
                    self.assertEqual(type(root)(result["legacy_backup"]).read_bytes(), b"legacy")

    def test_unprepared_linux_prefix_stops_without_downloads_or_mod_writes(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch.object(installer_platform.os, "name", "posix"), \
                    patch.object(installer_paths, "wine_prefix_for_kh3", return_value=None), \
                    patch.object(xinput, "_read_url", side_effect=AssertionError("network")):
                with self.assertRaisesRegex(ValueError, "Launch KH3 once"):
                    installer_platform.prepare_xinput_runtime(root, "test")
            self.assertEqual(list(root.iterdir()), [])


class TestWinePrefixDiscovery(unittest.TestCase):
    def _heroic(self, config: Path, game: Path, prefix: Path, overrides=None):
        installed = config / "heroic/legendaryConfig/legendary/installed.json"
        installed.parent.mkdir(parents=True, exist_ok=True)
        installed.write_text(json.dumps({"KH3App": {"install_path": str(game)}}))
        settings = config / "heroic/GamesConfig/KH3App.json"
        settings.parent.mkdir(parents=True, exist_ok=True)
        settings.write_text(json.dumps({"version": "v0.1", "KH3App": {
            "winePrefix": str(prefix), "enviromentOptions": overrides or [],
        }}))

    def test_native_and_flatpak_heroic_match_selected_nested_game_only(self):
        for flatpak in (False, True):
            with self.subTest(flatpak=flatpak), TemporaryDirectory() as temporary:
                home = Path(temporary)
                game = home / "games/KH3"
                prefix = home / "prefixes/KH3"
                registry = prefix / "pfx/user.reg"
                registry.parent.mkdir(parents=True)
                registry.write_text("registry")
                config = home / (".var/app/com.heroicgameslauncher.hgl/config" if flatpak else ".config")
                self._heroic(config, game, prefix)
                with patch.object(type(home), "home", return_value=home), \
                        patch.dict(os.environ, {"XDG_CONFIG_HOME": str(home / ".config")}):
                    self.assertEqual(xinput.wine_prefix_for_kh3(game / "KINGDOM HEARTS III"), prefix / "pfx")
                    self.assertIsNone(xinput.wine_prefix_for_kh3(home / "unrelated"))

    def test_heroic_ambiguous_prefix_or_conflicting_override_is_rejected(self):
        with TemporaryDirectory() as temporary:
            home = Path(temporary)
            game = home / "game"
            for name in ("native", "flatpak"):
                prefix = home / name
                prefix.mkdir()
                (prefix / "user.reg").write_text("registry")
                config = home / (".config" if name == "native" else ".var/app/com.heroicgameslauncher.hgl/config")
                self._heroic(config, game, prefix)
            with patch.object(type(home), "home", return_value=home), \
                    patch.dict(os.environ, {"XDG_CONFIG_HOME": str(home / ".config")}):
                with self.assertRaisesRegex(ValueError, "Multiple Heroic"):
                    xinput.wine_prefix_for_kh3(game)
                self._heroic(home / ".config", game, home / "native", [{"key": "WINEDLLOVERRIDES", "value": "xinput1_3=n,b"}])
                with self.assertRaisesRegex(ValueError, "WINEDLLOVERRIDES"):
                    xinput.wine_prefix_for_kh3(game)

    def test_external_steam_library_can_use_primary_compatdata(self):
        with TemporaryDirectory() as temporary:
            home = Path(temporary)
            primary = home / ".local/share/Steam/steamapps/compatdata/2552450/pfx"
            primary.mkdir(parents=True)
            (primary / "user.reg").write_text("registry")
            with patch.object(type(home), "home", return_value=home):
                self.assertEqual(xinput.wine_prefix_for_kh3(home / "external/steamapps/common/KH3"), primary)


class TestXInputPresentation(unittest.TestCase):
    def test_wine_xinput_ready_still_requires_native_ue4ss_loader(self):
        summary = build_installed_runtime_summary({
            "ue4ss_files": {"UE4SS.dll": True}, "ue4ss_proxy_installed": True,
            "ue4ss_settings_compliant": True, "probe_single_layout_ready": True,
            "xinput_runtime_ready": True,
            "proton_dwmapi_override": {"applicable": True, "installed": False},
        }, {"status": "current"})
        self.assertFalse(summary.installed)
        self.assertIn("Configure Wine UE4SS loader", summary.patch_tooltip)

    def test_missing_prerequisite_cannot_show_runtime_ready(self):
        for platform in ("windows", "linux"):
            summary = build_installed_runtime_summary({
                "ue4ss_files": {"UE4SS.dll": True}, "ue4ss_proxy_installed": True,
                "ue4ss_settings_compliant": True, "probe_single_layout_ready": True,
                "xinput_runtime_ready": False, "xinput_runtime": {"platform": platform},
            }, {"status": "current"})
            self.assertFalse(summary.installed)
            self.assertIn("XInput", summary.patch_tooltip)


if __name__ == "__main__":
    unittest.main()
