"""KH3 HTTPS uses system trust while retaining certificate and hostname checks."""

import os
import ssl
import tempfile
import unittest
import zipfile
from io import BytesIO
from pathlib import Path
from unittest.mock import Mock, patch

from .. import PakGenerator, RuntimeDownload
from ..client_support import goa_download, mom_release, platform_seed_download
from ..platform_support import download_tls


class TestDownloadTLS(unittest.TestCase):
    def setUp(self) -> None:
        bundled_ca = patch("certifi.where", side_effect=AssertionError("KH3 accessed the bundled CA file"))
        bundled_ca.start()
        self.addCleanup(bundled_ca.stop)

    def test_download_contexts_use_default_trust_and_verify_servers(self) -> None:
        factories = (
            PakGenerator._tool_download_ssl_context,
            goa_download._goa_mod_ssl_context,
            platform_seed_download._platform_ssl_context,
        )
        for factory in factories:
            with (
                self.subTest(factory=factory.__name__),
                patch.object(ssl, "create_default_context", wraps=ssl.create_default_context) as create_context,
            ):
                context = factory()

                create_context.assert_called_once_with(ssl.Purpose.SERVER_AUTH)
                self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
                self.assertTrue(context.check_hostname)

    def test_runtime_download_uses_default_trust_and_verifies_servers(self) -> None:
        with (
            patch.object(ssl, "create_default_context", wraps=ssl.create_default_context) as create_context,
            patch.object(RuntimeDownload.urllib.request, "urlopen", return_value=BytesIO(b"release")) as urlopen,
        ):
            payload = RuntimeDownload._read_url(RuntimeDownload.RELEASE_API_URL + "v3.0.4", 1024)

        create_context.assert_called_once_with(ssl.Purpose.SERVER_AUTH)
        context = urlopen.call_args.kwargs["context"]
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(context.check_hostname)
        self.assertEqual(payload, b"release")

    def test_runtime_download_rejects_untrusted_server(self) -> None:
        with (
            patch.object(
                RuntimeDownload.urllib.request,
                "urlopen",
                side_effect=ssl.SSLCertVerificationError("untrusted server"),
            ),
            self.assertRaises(ssl.SSLCertVerificationError),
        ):
            RuntimeDownload._read_url(RuntimeDownload.RELEASE_API_URL + "v3.0.4", 1024)

    def test_mom_release_downloads_use_system_trust_without_archipelago_context(self) -> None:
        urls = (mom_release.RELEASE_URL,
                f"https://github.com/{mom_release.REPOSITORY}/releases/download/v0.1.4/{mom_release.RUNTIME_ASSET}")
        for url in urls:
            with (
                self.subTest(url=url),
                patch("CommonClient.get_ssl_context", side_effect=AssertionError("Archipelago CA context used")),
                patch.object(PakGenerator.Utils, "is_linux", False),
                patch.object(ssl, "create_default_context", wraps=ssl.create_default_context) as create,
                patch.object(PakGenerator.urllib.request, "urlopen", return_value=BytesIO(b"verified bytes")) as open_url,
            ):
                self.assertEqual(mom_release._read_url(url, 1024), b"verified bytes")
                create.assert_called_once_with(ssl.Purpose.SERVER_AUTH)
                context = open_url.call_args.kwargs["context"]
                self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
                self.assertTrue(context.check_hostname)

    def test_mom_melonloader_download_uses_system_trust_without_archipelago_context(self) -> None:
        from ..client_support import mom_installation

        # Exercise the real shared downloader/context creation with a valid
        # loader ZIP, rather than substituting a prebuilt SSL context.
        files = {"version.dll": b"proxy", "MelonLoader/net6/MelonLoader.dll": b"loader"}
        stream = BytesIO()
        with zipfile.ZipFile(stream, "w") as archive:
            for name, content in files.items():
                archive.writestr(name, content)
        data = stream.getvalue()
        with (
            tempfile.TemporaryDirectory() as directory,
            patch("CommonClient.get_ssl_context", side_effect=AssertionError("Archipelago CA context used")),
            patch.object(PakGenerator.Utils, "is_linux", False),
            patch.object(mom_installation, "LOADER_SHA256", mom_installation.digest(data)),
            patch.object(ssl, "create_default_context", wraps=ssl.create_default_context) as create,
            patch.object(PakGenerator.urllib.request, "urlopen", return_value=BytesIO(data)) as open_url,
        ):
            self.assertEqual(mom_installation.loader_files(cache=Path(directory)), files)
            create.assert_called_once_with(ssl.Purpose.SERVER_AUTH)
            context = open_url.call_args.kwargs["context"]
            self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
            self.assertTrue(context.check_hostname)


class TestLinuxDownloadTrust(unittest.TestCase):
    def context(self, *, platform="linux", env=None, exists=None):
        context = Mock(spec=ssl.SSLContext)
        self.context_factory = patch.object(ssl, "create_default_context", return_value=context)
        self.context_factory.start()
        self.addCleanup(self.context_factory.stop)
        patches = (
            patch.object(download_tls.sys, "platform", platform),
            patch.dict(os.environ, {} if env is None else env, clear=True),
            patch.object(download_tls.Path, "is_file", side_effect=exists),
        )
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        return context

    def test_packaged_openssl_loads_host_bundle_even_with_empty_default_store(self):
        context = self.context(exists=[True])
        context.cert_store_stats.return_value = {"x509_ca": 0}
        self.assertIs(download_tls.create_download_ssl_context(), context)
        context.load_verify_locations.assert_called_once_with(cafile=download_tls.LINUX_CA_FILES[0])

    def test_other_distro_bundle(self):
        context = self.context(exists=[False, True])
        download_tls.create_download_ssl_context()
        context.load_verify_locations.assert_called_once_with(cafile=download_tls.LINUX_CA_FILES[1])

    def test_invalid_bundle_tries_next_host_bundle(self):
        context = self.context(exists=[True, True])
        context.load_verify_locations.side_effect = [ssl.SSLError("invalid CA bundle"), None]
        download_tls.create_download_ssl_context()
        self.assertEqual(context.load_verify_locations.call_count, 2)
        self.assertEqual(context.load_verify_locations.call_args.kwargs["cafile"], download_tls.LINUX_CA_FILES[1])

    def test_explicit_trust_overrides_are_preserved(self):
        for name in ("SSL_CERT_FILE", "SSL_CERT_DIR"):
            with self.subTest(name=name), patch.dict(os.environ, {name: "/custom/trust"}, clear=True), \
                    patch.object(download_tls.sys, "platform", "linux"), \
                    patch.object(ssl, "create_default_context", wraps=ssl.create_default_context) as create, \
                    patch.object(download_tls.Path, "is_file") as exists:
                context = download_tls.create_download_ssl_context()
                exists.assert_not_called()
                create.assert_called_once_with(ssl.Purpose.SERVER_AUTH)
                self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
                self.assertTrue(context.check_hostname)

    def test_missing_host_bundles_retain_default_verification(self):
        with patch.object(download_tls.sys, "platform", "linux"), \
                patch.dict(os.environ, {}, clear=True), \
                patch.object(download_tls.Path, "is_file", return_value=False):
            context = download_tls.create_download_ssl_context()
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(context.check_hostname)

    def test_windows_keeps_default_trust(self):
        context = self.context(platform="win32")
        download_tls.create_download_ssl_context()
        context.load_verify_locations.assert_not_called()
