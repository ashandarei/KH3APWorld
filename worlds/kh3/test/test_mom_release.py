import hashlib
import io
import json
import tempfile
import zipfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from ..client_support import mom_release as mod


def sha(data):
    return hashlib.sha256(data).hexdigest()


class TestMoMRelease(TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.cache = Path(self.temp.name)
        self.prefix = f"https://github.com/{mod.REPOSITORY}/releases/download/v0.1.4/"
        self.source = {"url": self.prefix + mod.SOURCE_ASSET, "sha256": sha(b"matching sources")}
        self.files = {name: b"required attribution" for name in mod.LEGAL_FILES}
        self.files["SOURCE.txt"] = (self.source["url"] + "\n" + self.source["sha256"]).encode()
        self.files["MoMController.dll"] = b"MZ our mod"
        self.manifest = {"schema_version": 1, "version": "0.1.4", "loader_version": "0.7.3",
                         "sha256": sha(self.files["MoMController.dll"]), "source": self.source}

    def fixture(self, *, extra=None, duplicate=None, manifest_change=None, corrupt_file=None):
        self.manifest["files"] = {name: sha(data) for name, data in self.files.items()}
        manifest = {**self.manifest, **(manifest_change or {})}
        files = {**self.files, "manifest.json": json.dumps(manifest).encode(), **(extra or {})}
        if corrupt_file:
            files[corrupt_file] = b"tampered"
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as archive:
            for name, data in files.items():
                archive.writestr(name, data)
            if duplicate:
                archive.writestr(duplicate, b"duplicate")
        runtime = stream.getvalue()
        release = {"tag_name": "v0.1.4", "draft": False, "prerelease": False, "assets": [
            {"name": mod.RUNTIME_ASSET, "size": len(runtime), "digest": "sha256:" + sha(runtime),
             "browser_download_url": self.prefix + mod.RUNTIME_ASSET},
            {"name": mod.SOURCE_ASSET, "size": len(b"matching sources"), "digest": "sha256:" + self.source["sha256"],
             "browser_download_url": self.source["url"]}]}
        return release, runtime

    def pins(self, release):
        return {asset["name"]: asset["digest"][7:] for asset in release["assets"]}

    def responses(self, release, runtime):
        return {mod.RELEASE_URL: json.dumps(release).encode(), self.prefix + mod.RUNTIME_ASSET: runtime}

    def test_verified_download_cache_and_no_source_download(self):
        release, runtime = self.fixture()
        responses = self.responses(release, runtime)
        with patch.dict(mod.ASSET_SHA256, self.pins(release), clear=True), \
                patch.object(mod, "_read_url", side_effect=lambda url, limit: responses[url]) as read:
            files, metadata = mod.download_mod(cache=self.cache)
            self.assertEqual(set(files), set(mod.MANAGED_FILES))
            self.assertEqual(files["Mods/MoMController.dll"], self.files["MoMController.dll"])
            self.assertEqual(metadata["release_tag"], "v0.1.4")
            self.assertEqual(mod.download_mod(cache=self.cache), (files, metadata))
        self.assertEqual([call.args[0] for call in read.call_args_list],
                         [mod.RELEASE_URL, self.prefix + mod.RUNTIME_ASSET, mod.RELEASE_URL])

    def test_corrupt_cache_is_replaced_only_by_a_verified_download(self):
        release, runtime = self.fixture()
        archive = self.cache / f"MoM-{sha(runtime)}.zip"
        archive.write_bytes(b"x" * len(runtime))
        responses = self.responses(release, runtime)
        with patch.dict(mod.ASSET_SHA256, self.pins(release), clear=True), \
                patch.object(mod, "_read_url", side_effect=lambda url, limit: responses[url]):
            mod.download_mod(cache=self.cache)
        self.assertEqual(archive.read_bytes(), runtime)

    def test_invalid_release_metadata_never_downloads_assets(self):
        mutations = [lambda r: r.update(draft=True), lambda r: r.update(prerelease=True),
                     lambda r: r.update(tag_name="../bad"), lambda r: r.update(tag_name="v0.1.5"), lambda r: r["assets"].pop(),
                     lambda r: r["assets"][0].update(digest=None),
                     lambda r: r["assets"][0].update(digest="sha256:" + "0" * 64),
                     lambda r: r["assets"][1].update(digest="sha256:" + "0" * 64),
                     lambda r: r["assets"][0].update(size=0),
                     lambda r: r["assets"][0].update(size=mod.MAX_RUNTIME_SIZE + 1),
                     lambda r: r["assets"][0].update(size=True),
                     lambda r: r["assets"][0].update(browser_download_url="https://example.org/evil.zip"),
                     lambda r: r["assets"].append(dict(r["assets"][0]))]
        for mutate in mutations:
            release, runtime = self.fixture()
            pins = self.pins(release)
            mutate(release)
            with self.subTest(mutate=mutate), patch.dict(mod.ASSET_SHA256, pins, clear=True), patch.object(mod, "_read_url", return_value=json.dumps(release).encode()) as read:
                with self.assertRaises(RuntimeError):
                    mod.download_mod(cache=self.cache)
                read.assert_called_once()
        self.assertEqual(list(self.cache.iterdir()), [])

    def test_bad_payloads_and_manifest_are_not_cached(self):
        fixtures = [self.fixture(extra={"../escape.dll": b"bad"}),
                    self.fixture(manifest_change={"loader_version": "other"}),
                    self.fixture(manifest_change={"version": "0.1.5"}),
                    self.fixture(manifest_change={"source": {"url": "https://example.org"}}),
                    self.fixture(manifest_change={"files": {}}),
                    self.fixture(corrupt_file="THIRD_PARTY_NOTICES.md")]
        release, runtime = self.fixture()
        fixtures.extend([(release, b"corrupt archive"), (release, runtime + b"corrupt size"),
                         (release, b"X" * len(runtime))])
        for release, runtime in fixtures:
            with self.subTest(size=len(runtime)), patch.dict(mod.ASSET_SHA256, self.pins(release), clear=True), patch.object(mod, "_read_url", side_effect=lambda url, limit: self.responses(release, runtime)[url]):
                with self.assertRaises(RuntimeError):
                    mod.download_mod(cache=self.cache)
            self.assertEqual(list(self.cache.iterdir()), [])

    def test_missing_empty_notices_and_duplicate_archive_entries_are_rejected(self):
        for name in mod.LEGAL_FILES:
            original = self.files.pop(name)
            release, runtime = self.fixture()
            with self.subTest(missing=name), patch.dict(mod.ASSET_SHA256, self.pins(release), clear=True), patch.object(mod, "_read_url", side_effect=lambda url, limit: self.responses(release, runtime)[url]):
                with self.assertRaises(RuntimeError):
                    mod.download_mod(cache=self.cache)
            self.files[name] = original
        release, runtime = self.fixture(duplicate="LICENSE")
        with patch.dict(mod.ASSET_SHA256, self.pins(release), clear=True), \
                patch.object(mod, "_read_url", side_effect=lambda url, limit: self.responses(release, runtime)[url]):
            with self.assertRaises(RuntimeError):
                mod.download_mod(cache=self.cache)

    def test_network_failure_leaves_existing_cache_unchanged(self):
        existing = self.cache / "existing.zip"
        existing.write_bytes(b"unchanged")
        with patch.object(mod, "_read_url", side_effect=OSError("offline")):
            with self.assertRaisesRegex(RuntimeError, "no game files changed"):
                mod.download_mod(cache=self.cache)
        self.assertEqual(list(self.cache.iterdir()), [existing])
        self.assertEqual(existing.read_bytes(), b"unchanged")

    def test_cached_archive_cannot_bypass_changed_or_missing_pins(self):
        release, runtime = self.fixture()
        archive = self.cache / f"MoM-{sha(runtime)}.zip"
        archive.write_bytes(runtime)
        for name in (mod.RUNTIME_ASSET, mod.SOURCE_ASSET):
            for replacement in (None, "0" * 64):
                pins = self.pins(release)
                if replacement is None:
                    del pins[name]
                else:
                    pins[name] = replacement
                with self.subTest(name=name, pin=replacement), \
                        patch.dict(mod.ASSET_SHA256, pins, clear=True), \
                        patch.object(mod, "_read_url", return_value=json.dumps(release).encode()) as read:
                    with self.assertRaisesRegex(RuntimeError, "pinned SHA-256"):
                        mod.download_mod(cache=self.cache)
                    read.assert_called_once_with(mod.RELEASE_URL, 1024 * 1024)
                self.assertEqual(archive.read_bytes(), runtime)

    def test_corrupt_download_does_not_replace_corrupt_cache(self):
        release, runtime = self.fixture()
        archive = self.cache / f"MoM-{sha(runtime)}.zip"
        original = b"X" * len(runtime)
        archive.write_bytes(original)
        responses = self.responses(release, b"Y" * len(runtime))
        with patch.dict(mod.ASSET_SHA256, self.pins(release), clear=True), \
                patch.object(mod, "_read_url", side_effect=lambda url, limit: responses[url]):
            with self.assertRaisesRegex(RuntimeError, "size/SHA-256"):
                mod.download_mod(cache=self.cache)
        self.assertEqual(archive.read_bytes(), original)

    def test_release_endpoint_is_the_fixed_tag(self):
        self.assertEqual(mod.RELEASE_URL,
                         "https://api.github.com/repos/ashandarei/MoM-GoA-AP/releases/tags/v0.1.4")
