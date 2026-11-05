import io
import json
import ssl
import subprocess
import tempfile
import urllib.error
import zipfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from .. import PakGenerator
from ..client_support import mom_installation as mom
from ..client_support import mom_release


class TestMoMInstallation(TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / mom.MOM_EXE).write_bytes(b"game")
        (self.root / "GameAssembly.dll").write_bytes(b"game assembly")
        self.loader = {"version.dll": b"proxy", "MelonLoader/net6/MelonLoader.dll": b"loader",
                       "MelonLoader/net6/dependency.dll": b"dependency"}
        self.payload = self.mod_payload(b"our mod", "test")
        self.closed = patch.object(mom, "require_closed")
        self.closed.start()
        self.addCleanup(self.closed.stop)

    def mod_payload(self, data, version):
        files = {name: f"{version}: {name}".encode() for name in mom_release.MANAGED_FILES}
        files[mom.MOD_PATH] = data
        return files, {"version": version}

    def install(self, **kwargs):
        return mom.install(self.root, payload=kwargs.pop("payload", self.payload), loader=self.loader, **kwargs)

    def put(self, name, data):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def test_fresh_install_repair_and_uninstall_preserve_game_and_user_data(self):
        self.put("UserData/results.json", b"earned results")
        self.install()
        (self.root / "MelonLoader/net6/dependency.dll").unlink()
        self.install()
        self.assertEqual((self.root / "MelonLoader/net6/dependency.dll").read_bytes(), b"dependency")
        mom.uninstall(self.root)
        for name in (*self.loader, *self.payload[0]):
            self.assertFalse((self.root / name).exists())
        self.assertEqual((self.root / "GameAssembly.dll").read_bytes(), b"game assembly")
        self.assertEqual((self.root / "UserData/results.json").read_bytes(), b"earned results")
        self.assertIn("No client-managed", mom.uninstall(self.root))

    def test_existing_loader_is_reused_and_previous_mod_restored(self):
        for name, data in self.loader.items():
            self.put(name, data)
        self.put(mom.MOD_PATH, b"previous mod")
        self.install()
        self.assertEqual(set(mom.read_state(self.root)["files"]), set(self.payload[0]))
        self.install(payload=self.mod_payload(b"updated mod", "next"))
        mom.uninstall(self.root)
        self.assertEqual((self.root / mom.MOD_PATH).read_bytes(), b"previous mod")
        self.assertEqual((self.root / "version.dll").read_bytes(), b"proxy")

    def test_loader_is_retained_if_another_mod_was_added(self):
        self.install()
        self.put("Mods/OtherAuthor.dll", b"other mod")
        mom.uninstall(self.root)
        self.assertTrue((self.root / "version.dll").exists())
        self.assertFalse((self.root / mom.MOD_PATH).exists())
        self.assertEqual((self.root / "Mods/OtherAuthor.dll").read_bytes(), b"other mod")
        for name in self.payload[0]:
            self.assertFalse((self.root / name).exists())

    def test_foreign_proxy_and_loader_are_never_overwritten(self):
        self.put("version.dll", b"another injector")
        with self.assertRaisesRegex(RuntimeError, "conflict"):
            self.install()
        self.assertEqual((self.root / "version.dll").read_bytes(), b"another injector")
        self.assertFalse((self.root / mom.MOD_PATH).exists())

    def test_changed_managed_file_is_preserved(self):
        self.install()
        self.put(mom.MOD_PATH, b"user replacement")
        with self.assertRaisesRegex(RuntimeError, "changed outside"):
            self.install()
        message = mom.uninstall(self.root)
        self.assertIn("externally modified", message)
        self.assertEqual((self.root / mom.MOD_PATH).read_bytes(), b"user replacement")

    def test_verified_download_repairs_stale_record_without_online_status_checks(self):
        self.put(mom.MOD_PATH, b"original third-party mod")
        self.install()
        previous = mom.read_state(self.root)["files"][mom.MOD_PATH]
        current = self.mod_payload(b"current released mod", "next")
        # The developer build installs the new payload without updating the journal.
        self.put(mom.MOD_PATH, current[0][mom.MOD_PATH])
        self.assertFalse(mom.status(self.root).ok)
        self.install(payload=current)
        state = mom.read_state(self.root)
        entry = state["files"][mom.MOD_PATH]
        self.assertEqual(state["mod_version"], "next")
        self.assertEqual(entry["sha256"], mom.digest(current[0][mom.MOD_PATH]))
        self.assertIn(previous["sha256"], entry["previous_hashes"])
        self.assertEqual(entry["backup"], previous["backup"])
        self.assertEqual(entry["original_sha256"], previous["original_sha256"])
        self.install(payload=current)
        mom.uninstall(self.root)
        self.assertEqual((self.root / mom.MOD_PATH).read_bytes(), b"original third-party mod")

    def test_current_mod_does_not_hide_changed_loader_files(self):
        self.install()
        self.put("MelonLoader/net6/dependency.dll", b"unknown replacement")
        self.assertFalse(mom.status(self.root).ok)
        with self.assertRaisesRegex(RuntimeError, "changed outside"):
            self.install()
        self.assertEqual((self.root / "MelonLoader/net6/dependency.dll").read_bytes(), b"unknown replacement")

    def test_interrupted_upgrade_can_be_uninstalled(self):
        self.install()
        original = mom.atomic_bytes
        def fail_mod(path, data):
            if path == self.root / mom.MOD_PATH:
                raise OSError("disk failure")
            original(path, data)
        with patch.object(mom, "atomic_bytes", side_effect=fail_mod):
            with self.assertRaisesRegex(OSError, "disk failure"):
                self.install(payload=self.mod_payload(b"new mod", "next"))
        mom.uninstall(self.root)
        self.assertFalse((self.root / mom.MOD_PATH).exists())
        self.assertFalse((self.root / "version.dll").exists())

    def test_bad_backup_prevents_any_uninstall_mutation(self):
        self.put(mom.MOD_PATH, b"prior")
        self.install()
        backup = mom.read_state(self.root)["files"][mom.MOD_PATH]["backup"]
        self.put(backup, b"corrupt")
        with self.assertRaisesRegex(RuntimeError, "backup failed"):
            mom.uninstall(self.root)
        self.assertEqual((self.root / mom.MOD_PATH).read_bytes(), b"our mod")
        self.assertTrue((self.root / "version.dll").exists())

    def test_unsafe_paths_and_corrupt_journal_are_rejected(self):
        for name in ("../escape", "C:/escape", "/escape", "..\\escape", "file:stream"):
            with self.assertRaises(ValueError):
                mom.safe_path(self.root, name)
        self.install()
        journal = self.root / mom.STATE_DIR / "installation.json"
        state = json.loads(journal.read_text())
        state["files"]["../outside"] = {"sha256": "anything"}
        journal.write_text(json.dumps(state))
        with self.assertRaises(ValueError):
            mom.uninstall(self.root)
        self.assertTrue((self.root / mom.MOD_PATH).exists())

    def test_autodetection_checks_game_files_and_escapes_epic_uri(self):
        manifests = self.root / "manifests"
        manifests.mkdir()
        (manifests / "game.item").write_text(json.dumps({"LaunchExecutable": mom.MOM_EXE,
            "InstallLocation": str(self.root), "AppName": "app:name?bad"}))
        (manifests / "broken.item").write_text("not json")
        self.assertEqual(mom.discover_installs(manifests), [(self.root, "com.epicgames.launcher://apps/app%3Aname%3Fbad?action=launch&silent=true")])

    def test_autodetection_resolves_epic_launch_paths_to_the_runtime_folder(self):
        manifests = self.root / "manifests"
        manifests.mkdir()
        nested = self.root / "Melody of Memory"
        nested.mkdir()
        (nested / mom.MOM_EXE).write_bytes(b"nested game")
        (nested / "GameAssembly.dll").write_bytes(b"nested game assembly")
        references = (
            ("./" + mom.MOM_EXE, self.root),
            (".\\" + mom.MOM_EXE, self.root),
            (mom.MOM_EXE.lower(), self.root),
            (str(self.root / mom.MOM_EXE), self.root),
            ("Melody of Memory/" + mom.MOM_EXE, nested),
            ("Melody of Memory\\" + mom.MOM_EXE, nested),
            (str(nested / mom.MOM_EXE), nested),
        )
        for executable, expected_root in references:
            with self.subTest(executable=executable):
                (manifests / "game.item").write_text(json.dumps({
                    "LaunchExecutable": executable, "InstallLocation": str(self.root), "AppName": "mom-app",
                }))
                self.assertEqual(mom.discover_installs(manifests), [
                    (expected_root, "com.epicgames.launcher://apps/mom-app?action=launch&silent=true"),
                ])

    def test_autodetection_skips_invalid_records_without_hiding_a_valid_install(self):
        manifests = self.root / "manifests"
        manifests.mkdir()
        valid = {"LaunchExecutable": mom.MOM_EXE, "InstallLocation": str(self.root), "AppName": "mom-app"}
        invalid = [[], None, {**valid, "AppName": ""}, {**valid, "InstallLocation": ""},
                   {**valid, "LaunchExecutable": "OtherGame.exe"}, {**valid, "LaunchExecutable": None},
                   {**valid, "InstallLocation": str(self.root / "missing")}]
        for index, record in enumerate(invalid):
            (manifests / f"invalid-{index}.item").write_text(json.dumps(record))
        (manifests / "00-valid.item").write_text(json.dumps(valid))
        self.assertEqual(mom.discover_installs(manifests), [
            (self.root, "com.epicgames.launcher://apps/mom-app?action=launch&silent=true"),
        ])

    def test_selected_install_uses_epic_registration_for_a_nested_launch_path(self):
        manifests = self.root / "manifests"
        manifests.mkdir()
        (manifests / "game.item").write_text(json.dumps({
            "LaunchExecutable": self.root.name + "\\" + mom.MOM_EXE,
            "InstallLocation": str(self.root.parent), "AppName": "mom-app",
        }))
        discover = mom.discover_installs
        with patch("Utils.persistent_load", return_value={"kh3_client": {"mom_dir": str(self.root)}}), \
                patch.object(mom, "discover_installs", side_effect=lambda: discover(manifests)):
            self.assertEqual(mom.selected_install(), (
                self.root, "com.epicgames.launcher://apps/mom-app?action=launch&silent=true",
            ))

    def test_selected_install_reports_detection_failure_without_claiming_epic_is_broken(self):
        other = self.root / "another-install"
        with patch("Utils.persistent_load", return_value={"kh3_client": {"mom_dir": str(self.root)}}), \
                patch.object(mom, "discover_installs", return_value=[(other, "epic-uri")]):
            with self.assertRaises(RuntimeError) as failure:
                mom.selected_install()
        message = str(failure.exception)
        self.assertIn("The KH3 client could not match", message)
        self.assertIn(str(self.root), message)
        self.assertIn(str(other), message)
        self.assertNotIn("not registered with Epic", message)
        self.assertNotIn("repair it in Epic", message)

    def test_game_running_blocks_before_any_install_mutation(self):
        with patch.object(mom, "require_closed", side_effect=RuntimeError("Close Melody of Memory")):
            with self.assertRaises(RuntimeError):
                self.install()
        self.assertFalse((self.root / mom.STATE_DIR).exists())

    def test_install_downloads_mod_and_notices_and_status_stays_offline(self):
        with patch.object(mom, "download_mod", return_value=self.payload) as fetch:
            mom.install(self.root, loader=self.loader)
            self.assertTrue(mom.status(self.root).ok)
            self.assertTrue(mom.status(self.root).ok)
        fetch.assert_called_once()
        for name, data in self.payload[0].items():
            self.assertEqual((self.root / name).read_bytes(), data)
        self.assertFalse((self.root / "LICENSE").exists())
        mom.uninstall(self.root)
        for name in self.payload[0]:
            self.assertFalse((self.root / name).exists())

    def test_failed_mod_download_does_not_start_install_or_download_loader(self):
        with patch.object(mom, "download_mod", side_effect=RuntimeError("release unavailable")), \
                patch.object(mom, "loader_files") as loader:
            with self.assertRaisesRegex(RuntimeError, "release unavailable"):
                mom.install(self.root)
        loader.assert_not_called()
        self.assertFalse((self.root / mom.STATE_DIR).exists())

    def test_bad_download_is_rejected_before_cache_or_install(self):
        with patch.object(PakGenerator.urllib.request, "urlopen", return_value=io.BytesIO(b"corrupt archive")):
            with self.assertRaisesRegex(RuntimeError, "SHA-256"):
                mom.loader_files(cache=self.root / "cache")
        self.assertEqual(list((self.root / "cache").glob("*.zip")), [])
        self.assertFalse((self.root / mom.MOD_PATH).exists())

    def loader_archive(self):
        data = io.BytesIO()
        with zipfile.ZipFile(data, "w") as archive:
            for name, payload in self.loader.items():
                archive.writestr(name, payload)
        return data.getvalue()

    def test_loader_download_uses_verified_https_and_reuses_cache(self):
        data = self.loader_archive()
        context = PakGenerator._tool_download_ssl_context()
        self.assertTrue(context.check_hostname)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        with patch.object(mom, "LOADER_SHA256", mom.digest(data)), \
                patch.object(PakGenerator, "_tool_download_ssl_context", return_value=context), \
                patch.object(PakGenerator.urllib.request, "urlopen", return_value=io.BytesIO(data)) as urlopen:
            self.assertEqual(mom.loader_files(cache=self.root / "cache"), self.loader)
            self.assertEqual(mom.loader_files(cache=self.root / "cache"), self.loader)
        urlopen.assert_called_once()
        self.assertEqual(urlopen.call_args.args[0].full_url, mom.LOADER_URL)
        self.assertIs(urlopen.call_args.kwargs["context"], context)

    def test_loader_download_uses_host_curl_for_appimage_or_certificate_failure(self):
        data = self.loader_archive()
        for frozen in (False, True):
            with self.subTest(frozen=frozen):
                cache = self.root / f"cache-{frozen}"

                def download(command, **kwargs):
                    self.assertNotIn("--insecure", command)
                    self.assertNotIn("LD_LIBRARY_PATH", kwargs["env"])
                    self.assertNotIn("LD_PRELOAD", kwargs["env"])
                    Path(command[command.index("--output") + 1]).write_bytes(data)
                    return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

                with patch.object(mom, "LOADER_SHA256", mom.digest(data)), \
                        patch.object(PakGenerator.Utils, "is_linux", True), \
                        patch.object(PakGenerator.Utils, "is_frozen", return_value=frozen), \
                        patch.object(PakGenerator.urllib.request, "urlopen", side_effect=urllib.error.URLError(
                            ssl.SSLCertVerificationError("unable to get local issuer certificate"))) as urlopen, \
                        patch.object(PakGenerator, "executable_on_path",
                                     side_effect=lambda name: "/usr/bin/curl" if name == "curl" else None), \
                        patch.object(PakGenerator.subprocess, "run", side_effect=download) as run:
                    self.assertEqual(mom.loader_files(cache=cache), self.loader)
                self.assertEqual(urlopen.call_count, 0 if frozen else 1)
                run.assert_called_once()
                self.assertEqual(list(cache.glob(".mom-download-*")), [])

    def test_failed_loader_download_preserves_cache_and_cleans_staging(self):
        cache = self.root / "cache"
        cache.mkdir()
        archive = cache / f"MelonLoader-{mom.LOADER_VERSION}-{mom.LOADER_SHA256[:12]}.zip"
        archive.write_bytes(b"old invalid archive")

        def fail_download(url, destination):
            destination.write_bytes(b"partial download")
            raise OSError("certificate verify failed")

        with patch.object(PakGenerator, "_download_file", side_effect=fail_download):
            with self.assertRaisesRegex(RuntimeError, "Could not download MelonLoader; nothing was installed"):
                mom.loader_files(cache=cache)
        self.assertEqual(archive.read_bytes(), b"old invalid archive")
        self.assertEqual(list(cache.iterdir()), [archive])
        self.assertFalse((self.root / mom.STATE_DIR).exists())

    def test_concurrent_installation_lock_is_rejected(self):
        with mom.install_lock(self.root):
            with self.assertRaises((RuntimeError, BlockingIOError)):
                with mom.install_lock(self.root):
                    self.fail("A second installer acquired the lock")
