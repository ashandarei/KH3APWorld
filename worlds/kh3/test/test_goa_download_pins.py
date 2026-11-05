"""GoA downloads and cached file hashes must obey the bundled compatibility pins."""

import hashlib
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from ..client_support import goa_download as mod


class TestGoAPins(TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.mods = self.root / "mods"
        self.mods.mkdir()
        self.cache = self.root / "cache.json"
        self.target = self.mods / mod.KH3_GOA_MOD_FILE_NAME
        self.payload = b"verified GoA pak"
        pins = patch.multiple(mod, KH3_GOA_MOD_SIZE=len(self.payload),
                              KH3_GOA_MOD_SHA256=hashlib.sha256(self.payload).hexdigest().upper())
        pins.start()
        self.addCleanup(pins.stop)

    def seed_cache(self):
        self.target.write_bytes(self.payload)
        cache = mod._new_goa_status_cache()
        mod._store_goa_file_sha256(self.target, mod.KH3_GOA_MOD_SHA256, cache)
        mod._write_goa_status_cache(self.cache, cache)
        return cache

    def test_fresh_install_downloads_only_the_pinned_asset(self):
        with patch.object(mod.urllib.request, "urlopen", return_value=BytesIO(self.payload)) as fetch:
            mod.download_goa_mod(self.mods, cache_path=self.cache)
        fetch.assert_called_once()
        self.assertEqual(fetch.call_args.args[0].full_url, mod.KH3_GOA_MOD_DOWNLOAD_URL)
        self.assertEqual(self.target.read_bytes(), self.payload)
        cache = mod._load_goa_status_cache(self.cache)
        self.assertNotIn("manifest", cache)
        self.assertNotIn("manifest_checked_at", cache)

    def test_correct_installed_pak_needs_no_metadata_cache_or_network(self):
        self.target.write_bytes(self.payload)
        with patch.object(mod.urllib.request, "urlopen", side_effect=AssertionError("network used")):
            self.assertEqual(mod.goa_mod_install_status(self.mods)["status"], "current")
            mod.download_goa_mod(self.mods, cache_path=self.cache)
        self.assertEqual(self.target.read_bytes(), self.payload)

    def test_verified_hash_cache_skips_network_and_rehashing(self):
        self.seed_cache()
        with patch.object(mod.urllib.request, "urlopen", side_effect=AssertionError("network used")), \
                patch.object(mod, "_sha256_file", side_effect=AssertionError("cached hash expected")):
            mod.download_goa_mod(self.mods, cache_path=self.cache)
            self.assertEqual(mod.goa_mod_install_status(self.mods, cache_path=self.cache,
                                                       allow_hash=False)["status"], "current")

    def test_old_cache_or_changed_pin_requires_rehashing(self):
        for field, value in (("version", 2), ("release_tag", "0.17.21"),
                             ("expected_sha256", "0" * 64), ("expected_size", 1),
                             ("file_name", "other.pak")):
            cache = self.seed_cache()
            cache[field] = value
            mod._write_goa_status_cache(self.cache, cache)
            with self.subTest(field=field), \
                    patch.object(mod.urllib.request, "urlopen", side_effect=AssertionError("network used")), \
                    patch.object(mod, "_sha256_file", wraps=mod._sha256_file) as hash_file:
                self.assertEqual(mod.goa_mod_install_status(self.mods, cache_path=self.cache,
                                                           allow_hash=False)["status"], "unknown")
                mod.download_goa_mod(self.mods, cache_path=self.cache)
                hash_file.assert_called_once_with(self.target)
            self.assertEqual(self.target.read_bytes(), self.payload)

    def test_changed_client_hash_does_not_trust_old_cached_verification(self):
        self.seed_cache()
        with patch.object(mod, "KH3_GOA_MOD_SHA256", "0" * 64), \
                patch.object(mod, "_sha256_file", wraps=mod._sha256_file) as hash_file:
            status = mod.goa_mod_install_status(self.mods, cache_path=self.cache)
        self.assertEqual(status["status"], "outdated")
        hash_file.assert_called_once_with(self.target)

    def test_bad_download_preserves_current_and_old_paks(self):
        old = self.mods / "pakchunk99-GardenOfAssemblageRandomizer_old.pak"
        old.write_bytes(b"old")
        self.target.write_bytes(b"installed")
        for data in (b"short", b"X" * len(self.payload), self.payload + b"extra"):
            with self.subTest(data=data), \
                    patch.object(mod.urllib.request, "urlopen", return_value=BytesIO(data)):
                mod.download_goa_mod(self.mods, cache_path=self.cache)
            self.assertEqual(self.target.read_bytes(), b"installed")
            self.assertEqual(old.read_bytes(), b"old")
            self.assertFalse(list(self.mods.glob("*.download")))

    def test_changed_installed_file_invalidates_cached_hash(self):
        self.seed_cache()
        self.target.write_bytes(b"X" * len(self.payload))
        with patch.object(mod.urllib.request, "urlopen", return_value=BytesIO(self.payload)) as download:
            mod.download_goa_mod(self.mods, cache_path=self.cache)
            download.assert_called_once()
        self.assertEqual(self.target.read_bytes(), self.payload)

    def test_download_reader_rejects_bytes_beyond_pinned_size(self):
        with patch.object(mod, "_open_goa_mod_url", return_value=BytesIO(self.payload + b"extra")):
            with self.assertRaisesRegex(ValueError, "pinned size"):
                mod._download_goa_mod_file(mod.KH3_GOA_MOD_DOWNLOAD_URL, self.root / "download")

    def test_status_always_exposes_bundled_expectations(self):
        status = mod.goa_mod_install_status(self.mods, allow_hash=False)
        self.assertEqual(status["status"], "missing")
        self.assertEqual(status["expected_version"], mod.KH3_GOA_MOD_RELEASE_TAG)
        self.assertEqual(status["expected_size"], len(self.payload))
        self.assertEqual(status["expected_sha256"], mod.KH3_GOA_MOD_SHA256)
        self.assertEqual(status["expected_url"], mod.KH3_GOA_MOD_DOWNLOAD_URL)
