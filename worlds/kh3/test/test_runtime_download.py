"""Release downloads must verify bytes before touching the installed runtime."""

import copy
import hashlib
import json
import os
import unittest
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from worlds.kh3 import RuntimeDownload

from ..installer_support import (
    installation as installer_installation,
)
from ..installer_support import (
    resources as installer_resources,
)
from ..installer_support import (
    status as installer_status,
)


class TestRuntimeDownload(unittest.TestCase):
    def setUp(self):
        prerequisite = patch.object(installer_installation, "prepare_xinput_runtime", return_value={"ready": True})
        prerequisite.start()
        self.addCleanup(prerequisite.stop)
        cache_dir = TemporaryDirectory()
        self.addCleanup(cache_dir.cleanup)
        cache_patch = patch.object(RuntimeDownload, "runtime_cache_path", return_value=Path(cache_dir.name) / "cache.json")
        cache_patch.start()
        self.addCleanup(cache_patch.stop)
        self.payloads = {name: b"MZ-test-" + name.encode() for name in RuntimeDownload.DLL_NAMES}
        self.config = {"repository": RuntimeDownload.REPOSITORY, "release_tag": "v3.0.4",
                       "ue4ss_sha256": hashlib.sha256(self.payloads["UE4SS.dll"]).hexdigest(),
                       "proxy_sha256": {
            name: hashlib.sha256(data).hexdigest()
            for name, data in self.payloads.items() if name != "UE4SS.dll"
        }}
        self.release = {"tag_name": "v3.0.4", "draft": False, "prerelease": False, "assets": [
            {"name": name, "size": len(data), "digest": "sha256:" + hashlib.sha256(data).hexdigest(),
             "browser_download_url": f"https://github.com/ashandarei/ue4ss-goa/releases/download/v3.0.4/{name}"}
            for name, data in self.payloads.items()
        ]}

    def read_url(self, url, limit):
        if url == RuntimeDownload.release_url(self.config):
            return json.dumps(self.release).encode()
        return self.payloads[url.rsplit("/", 1)[1]]

    def test_downloads_missing_files_and_skips_hash_matches(self):
        with TemporaryDirectory() as temp:
            win64, stage = Path(temp) / "Win64", Path(temp) / "stage"
            win64.mkdir()
            stage.mkdir()
            for name in ("dwmapi.dll",):
                (win64 / name).write_bytes(self.payloads[name])
            with patch.object(RuntimeDownload, "_read_url", side_effect=self.read_url) as fetch:
                result = RuntimeDownload.stage_runtime(self.config, win64, stage)
            self.assertEqual(result["downloaded"], ["UE4SS.dll"])
            self.assertEqual(fetch.call_count, 2)
            self.assertEqual((stage / "UE4SS.dll").read_bytes(), self.payloads["UE4SS.dll"])
            self.assertFalse((win64 / "UE4SS.dll").exists())

    def test_all_matching_files_only_fetch_release_metadata(self):
        with TemporaryDirectory() as temp:
            win64 = Path(temp)
            for name, data in self.payloads.items():
                (win64 / name).write_bytes(data)
            with patch.object(RuntimeDownload, "_read_url", side_effect=self.read_url) as fetch:
                result = RuntimeDownload.stage_runtime(self.config, win64, win64 / "unused")
            self.assertEqual(result["downloaded"], [])
            self.assertEqual(set(result["skipped"]), set(self.payloads))
            fetch.assert_called_once_with(RuntimeDownload.release_url(self.config), 1024 * 1024)

    def test_same_size_wrong_hash_is_downloaded(self):
        with TemporaryDirectory() as temp:
            win64, stage = Path(temp) / "Win64", Path(temp) / "stage"
            win64.mkdir()
            stage.mkdir()
            for name, data in self.payloads.items():
                (win64 / name).write_bytes(data)
            (win64 / "UE4SS.dll").write_bytes(b"X" * len(self.payloads["UE4SS.dll"]))
            with patch.object(RuntimeDownload, "_read_url", side_effect=self.read_url):
                result = RuntimeDownload.stage_runtime(self.config, win64, stage)
            self.assertEqual(result["downloaded"], ["UE4SS.dll"])

    def test_rejects_unpinned_proxy_and_missing_digest(self):
        for field, value in (("digest", "sha256:" + "0" * 64), ("digest", None),
                             ("browser_download_url", "https://example.com/dwmapi.dll")):
            with self.subTest(value=value):
                asset = self.release["assets"][1]
                original = asset[field]
                asset[field] = value
                with patch.object(RuntimeDownload, "_read_url", side_effect=self.read_url):
                    with self.assertRaises(ValueError):
                        RuntimeDownload.pinned_release(self.config)
                asset[field] = original

    def test_offline_patch_and_status_reuse_cached_manifest_and_hashes(self):
        with TemporaryDirectory() as temp:
            win64 = Path(temp)
            for name, data in self.payloads.items():
                (win64 / name).write_bytes(data)
            with patch.object(RuntimeDownload, "_read_url", side_effect=self.read_url):
                RuntimeDownload.stage_runtime(self.config, win64, win64 / "unused")
            # These operations may read the small JSON cache, never DLL bytes.
            with patch.object(RuntimeDownload, "_read_url", side_effect=OSError("offline")) as fetch, \
                    patch.object(Path, "read_bytes", side_effect=AssertionError("unexpected DLL hash")):
                states = RuntimeDownload.cached_file_states(self.config, win64)
                fetch.assert_not_called()
                self.assertEqual(set(states.values()), {"installed"})
                result = RuntimeDownload.stage_runtime(self.config, win64, win64 / "unused")
                self.assertEqual(result["downloaded"], [])
                self.assertEqual(fetch.call_count, 1)

    def test_bundled_pins_match_frozen_runtime_manifest(self):
        runtime = Path(__file__).resolve().parents[1] / "runtime/Win64"
        config = json.loads((runtime / RuntimeDownload.RELEASE_CONFIG_NAME).read_text())
        manifest = json.loads((runtime / "KH3AP-Licenses/manifest.json").read_text())
        self.assertEqual(config["release_tag"], manifest["release"])
        self.assertEqual(config["ue4ss_sha256"], manifest["frozen_dll_sha256"]["UE4SS.dll"])
        self.assertEqual(config["proxy_sha256"]["dwmapi.dll"], manifest["frozen_dll_sha256"]["dwmapi.dll"])
        self.assertEqual(RuntimeDownload.release_url(config),
                         "https://api.github.com/repos/ashandarei/ue4ss-goa/releases/tags/v3.0.4")

    def test_wrong_release_or_asset_metadata_never_stages_files(self):
        cases = []
        for field, value in (("tag_name", "v3.0.5"), ("draft", True), ("prerelease", True)):
            release = copy.deepcopy(self.release)
            release[field] = value
            cases.append(release)
        for field, value in (
            ("digest", "sha256:" + "0" * 64), ("digest", None),
            ("size", 0), ("size", True), ("size", RuntimeDownload.MAX_DLL_SIZE + 1),
            ("browser_download_url", "https://github.com/ashandarei/ue4ss-goa/releases/download/v3.0.5/UE4SS.dll"),
            ("browser_download_url", "https://github.com/ashandarei/ue4ss-goa/releases/download/v3.0.4/other.dll"),
        ):
            release = copy.deepcopy(self.release)
            release["assets"][0][field] = value
            cases.append(release)
        for assets in (self.release["assets"][1:], self.release["assets"] + [self.release["assets"][0]]):
            cases.append({**self.release, "assets": assets})
        for release in cases:
            with self.subTest(release=release), TemporaryDirectory() as temp, \
                    patch.object(RuntimeDownload, "_read_url", return_value=json.dumps(release).encode()) as fetch:
                with self.assertRaises(ValueError):
                    RuntimeDownload.stage_runtime(self.config, Path(temp) / "absent", Path(temp))
                fetch.assert_called_once_with(RuntimeDownload.release_url(self.config), 1024 * 1024)
                self.assertEqual(list(Path(temp).iterdir()), [])

    def test_missing_pins_fail_closed(self):
        for field in ("release_tag", "ue4ss_sha256"):
            with self.subTest(field=field):
                config = {key: value for key, value in self.config.items() if key != field}
                with patch.object(RuntimeDownload, "_read_url", side_effect=self.read_url):
                    with self.assertRaises(ValueError):
                        RuntimeDownload.pinned_release(config)

    def test_offline_cache_cannot_bypass_changed_pins_or_old_cache_format(self):
        for field, value in (("release_tag", "v3.0.5"), ("ue4ss_sha256", "0" * 64),
                             ("proxy_sha256", {"dwmapi.dll": "0" * 64}), ("version", 1)):
            with self.subTest(field=field), TemporaryDirectory() as temp:
                win64 = Path(temp)
                for name, data in self.payloads.items():
                    (win64 / name).write_bytes(data)
                with patch.object(RuntimeDownload, "_read_url", side_effect=self.read_url):
                    RuntimeDownload.stage_runtime(self.config, win64, win64 / "unused")
                cache = json.loads(RuntimeDownload.runtime_cache_path().read_text())
                cache[field] = value
                RuntimeDownload.runtime_cache_path().write_text(json.dumps(cache))
                with patch.object(RuntimeDownload, "_read_url", side_effect=OSError("offline")):
                    with self.assertRaises(OSError):
                        RuntimeDownload.stage_runtime(self.config, win64, win64 / "unused")

    def test_cached_manifest_is_revalidated_even_with_matching_cache_pins(self):
        for field, value in (("tag_name", "v3.0.5"), ("digest", "sha256:" + "0" * 64)):
            with self.subTest(field=field):
                manifest = copy.deepcopy(self.release)
                target = manifest if field == "tag_name" else manifest["assets"][0]
                target[field] = value
                self.assertIsNone(RuntimeDownload._cached_release(self.config, {"manifest": manifest}))

    def test_changed_file_invalidates_cached_hash(self):
        with TemporaryDirectory() as temp:
            win64, stage = Path(temp) / "Win64", Path(temp) / "stage"
            win64.mkdir()
            stage.mkdir()
            for name, data in self.payloads.items():
                (win64 / name).write_bytes(data)
            with patch.object(RuntimeDownload, "_read_url", side_effect=self.read_url):
                RuntimeDownload.stage_runtime(self.config, win64, stage)
                changed = win64 / "UE4SS.dll"
                original = changed.stat()
                changed.write_bytes(b"X" * len(self.payloads["UE4SS.dll"]))
                # Some filesystems coalesce timestamps for immediate writes.
                os.utime(changed, ns=(original.st_atime_ns, original.st_mtime_ns + 1_000_000_000))
                result = RuntimeDownload.stage_runtime(self.config, win64, stage)
            self.assertEqual(result["downloaded"], ["UE4SS.dll"])

    def test_loose_old_dlls_do_not_overwrite_release_downloads(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            bundle = root / "bundle"
            bundle.mkdir()
            (bundle / RuntimeDownload.RELEASE_CONFIG_NAME).write_text(json.dumps(self.config))
            (bundle / "UE4SS-settings.ini").write_text("[General]\n")
            for name in self.payloads:
                (bundle / name).write_bytes(b"old-loose-file")
            (bundle / "xinput1_3.dll").write_bytes(b"retired-microsoft-dll")
            probe = root / "probe.dll"
            probe.write_bytes(b"probe")
            game = root / "game"
            win64 = game / "Binaries" / "Win64"
            win64.mkdir(parents=True)
            with patch.object(installer_resources, "RUNTIME_WIN64_DIR", bundle), \
                    patch.object(installer_resources, "RUNTIME_PROBE_DLL", probe), \
                    patch.object(RuntimeDownload, "_read_url", side_effect=self.read_url):
                result = installer_installation.install_kh3_runtime(game)
            self.assertEqual(result["status"], "ok", result.get("errors"))
            for name, data in self.payloads.items():
                self.assertEqual((win64 / name).read_bytes(), data)
            self.assertFalse((win64 / "xinput1_3.dll").exists())

    def test_release_does_not_require_or_download_microsoft_xinput(self):
        self.release["assets"].append({"name": "xinput1_3.dll", "digest": None})
        with TemporaryDirectory() as temp, patch.object(RuntimeDownload, "_read_url", side_effect=self.read_url) as fetch:
            result = RuntimeDownload.stage_runtime(self.config, Path(temp) / "absent", Path(temp))
        self.assertEqual(set(result["downloaded"]), {"UE4SS.dll", "dwmapi.dll"})
        self.assertFalse(any("xinput" in call.args[0] for call in fetch.call_args_list))

    def test_missing_release_config_does_not_install_loose_dlls_or_run_prerequisites(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            bundle = root / "bundle"
            bundle.mkdir()
            for name in self.payloads:
                (bundle / name).write_bytes(b"old-loose-file")
            game = root / "game"
            win64 = game / "Binaries" / "Win64"
            win64.mkdir(parents=True)
            originals = {"UE4SS.dll": b"installed-runtime", "UE4SS-settings.ini": b"installed-settings"}
            for name, data in originals.items():
                (win64 / name).write_bytes(data)
            with patch.object(installer_resources, "RUNTIME_WIN64_DIR", bundle), \
                    patch.object(installer_installation, "prepare_xinput_runtime") as prerequisite, \
                    patch.object(RuntimeDownload, "_read_url") as fetch:
                result = installer_installation.install_kh3_runtime(game)
            self.assertEqual(result["status"], "error")
            self.assertIn("Missing UE4SS release configuration", result["errors"][0])
            prerequisite.assert_not_called()
            fetch.assert_not_called()
            self.assertEqual({path.relative_to(win64).as_posix(): path.read_bytes()
                              for path in win64.rglob("*") if path.is_file()}, originals)

    def test_failed_fetch_or_bad_download_preserves_entire_install(self):
        for failure in (OSError("offline"), b"bad-download", b"X" * len(self.payloads["UE4SS.dll"])):
            with self.subTest(failure=failure), TemporaryDirectory() as temp:
                root = Path(temp)
                bundle = root / "bundle"
                bundle.mkdir()
                (bundle / RuntimeDownload.RELEASE_CONFIG_NAME).write_text(json.dumps(self.config))
                (bundle / "UE4SS-settings.ini").write_bytes(b"new-settings")
                game = root / "game"
                win64 = game / "Binaries" / "Win64"
                win64.mkdir(parents=True)
                (win64 / "UE4SS.dll").write_bytes(b"old-runtime")
                (win64 / "UE4SS-settings.ini").write_bytes(b"old-settings")

                def failing_read(url, limit):
                    if isinstance(failure, Exception):
                        raise failure
                    return self.read_url(url, limit) if url == RuntimeDownload.release_url(self.config) else failure

                with patch.object(installer_resources, "RUNTIME_WIN64_DIR", bundle), \
                        patch.object(RuntimeDownload, "_read_url", side_effect=failing_read):
                    result = installer_installation.install_kh3_runtime(game)
                self.assertEqual(result["status"], "error")
                self.assertEqual((win64 / "UE4SS.dll").read_bytes(), b"old-runtime")
                self.assertEqual((win64 / "UE4SS-settings.ini").read_bytes(), b"old-settings")
                self.assertEqual(len(list(win64.iterdir())), 2)

    def test_apworld_without_dlls_downloads_and_backs_up_then_skips(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            archive = root / "kh3.apworld"
            license_root = Path(__file__).resolve().parents[1] / "runtime/Win64/KH3AP-Licenses"
            licenses = {path.relative_to(license_root).as_posix(): path.read_bytes()
                        for path in license_root.rglob("*") if path.is_file()}
            self.assertIn("manifest.json", licenses)
            self.assertIn("THIRD_PARTY_NOTICES.txt", licenses)
            with zipfile.ZipFile(archive, "w") as output:
                output.writestr("kh3/runtime/Win64/ue4ss-release.json", json.dumps(self.config))
                output.writestr("kh3/runtime/Win64/UE4SS-settings.ini", "[General]\n")
                output.writestr("kh3/runtime/Mods/KH3APProbeCpp/dlls/main.dll", b"probe")
                for name, data in licenses.items():
                    output.writestr(f"kh3/runtime/Win64/KH3AP-Licenses/{name}", data)
            game = root / "game"
            win64 = game / "Binaries" / "Win64"
            win64.mkdir(parents=True)
            (win64 / "UE4SS.dll").write_bytes(b"old-runtime")
            with patch.object(installer_resources, "__file__", str(archive / "kh3/installer_support/resources.py")), \
                    patch.object(installer_resources, "RUNTIME_WIN64_DIR", root / "absent"), \
                    patch.object(installer_resources, "RUNTIME_PROBE_DLL", root / "absent-probe"), \
                    patch.object(RuntimeDownload, "_read_url", side_effect=self.read_url) as fetch:
                result = installer_installation.install_kh3_runtime(game)
                self.assertEqual(result["status"], "ok", result.get("errors"))
                for name, data in self.payloads.items():
                    self.assertEqual((win64 / name).read_bytes(), data)
                for name, data in licenses.items():
                    self.assertEqual((win64 / "KH3AP-Licenses" / name).read_bytes(), data)
                self.assertEqual({call.args[0] for call in fetch.call_args_list},
                                 {RuntimeDownload.release_url(self.config),
                                  *(asset["browser_download_url"] for asset in self.release["assets"])})
                backups = list(win64.glob("UE4SS.dll.apbak-*"))
                self.assertEqual(len(backups), 1)
                self.assertEqual(backups[0].read_bytes(), b"old-runtime")
                fetch.reset_mock()
                second = installer_installation.install_kh3_runtime(game)
                self.assertEqual(second["status"], "ok", second.get("errors"))
                self.assertEqual(second["ue4ss_release"]["downloaded"], [])
                self.assertEqual(fetch.call_count, 1)
                for name, data in licenses.items():
                    self.assertEqual((win64 / "KH3AP-Licenses" / name).read_bytes(), data)
                missing_notice = win64 / "KH3AP-Licenses/THIRD_PARTY_NOTICES.txt"
                missing_notice.unlink()
                with patch.object(RuntimeDownload, "_read_url", side_effect=OSError("offline")):
                    offline = installer_installation.install_kh3_runtime(game)
                self.assertEqual(offline["status"], "ok", offline.get("errors"))
                self.assertEqual(offline["ue4ss_release"]["downloaded"], [])
                self.assertEqual(missing_notice.read_bytes(), licenses["THIRD_PARTY_NOTICES.txt"])
                fetch.reset_mock()
                installer_status.runtime_install_status(game)
                fetch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
