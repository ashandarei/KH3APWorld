from __future__ import annotations

import hashlib
import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from unittest.mock import patch

from ..client_support.platform_seed_download import (
    KH3PlatformSeedDownloadError,
    KH3PlatformSeedDownloadService,
    kh3_platform_room_metadata_from_url,
    kh3_slot_data_with_connected_room_url,
    open_kh3_room_page_url,
)


class _ArtifactServer(ThreadingHTTPServer):
    artifact_name: str
    artifact_payload: bytes
    slot_name: str
    cross_origin_url: str | None


class _ArtifactHandler(BaseHTTPRequestHandler):
    server: _ArtifactServer

    def do_GET(self) -> None:
        if self.path.startswith("/api/rooms/test-room"):
            artifact_url = self.server.cross_origin_url or (f"http://127.0.0.1:{self.server.server_port}/artifact")
            payload = json.dumps(
                {
                    "roomId": "test-room",
                    "downloads": [
                        {
                            "slotName": self.server.slot_name,
                            "url": artifact_url,
                        }
                    ],
                }
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        if self.path.startswith("/artifact"):
            payload = self.server.artifact_payload
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Disposition", f'attachment; filename="{self.server.artifact_name}"')
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        self.send_error(404)

    def log_message(self, _format: str, *args: Any) -> None:
        return


class KH3PlatformSeedDownloadTest(unittest.TestCase):
    def _serve(self, *, artifact_name: str, artifact_payload: bytes, slot_name: str = "Sora"):
        server = _ArtifactServer(("127.0.0.1", 0), _ArtifactHandler)
        server.artifact_name = artifact_name
        server.artifact_payload = artifact_payload
        server.slot_name = slot_name
        server.cross_origin_url = None
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        return server, thread

    @staticmethod
    def _slot_data(port: int) -> dict[str, Any]:
        return {
            "kh3_room": {
                "format": "kh3_platform_room",
                "format_version": 1,
                "uuid": "test-room",
                "url": f"http://127.0.0.1:{port}/room/test-room",
            }
        }

    def test_downloads_direct_pak_or_patch_container_for_matching_slot(self) -> None:
        for suffix in (".pak", ".apkh3", ".zip"):
            with self.subTest(suffix=suffix), tempfile.TemporaryDirectory() as temp_dir:
                payload = f"payload-{suffix}".encode("ascii")
                server, thread = self._serve(artifact_name=f"Sora{suffix}", artifact_payload=payload)
                try:
                    statuses: list[str] = []
                    service = KH3PlatformSeedDownloadService(
                        download_root=Path(temp_dir),
                        attempts=1,
                    )

                    with self.assertLogs("Client", level="INFO") as captured_logs:
                        result = service.download_player_artifact(
                            self._slot_data(server.server_port),
                            slot_name="sora",
                            progress=statuses.append,
                        )

                    self.assertIsNotNone(result)
                    assert result is not None
                    self.assertEqual(result.source_path.name, f"Sora{suffix}")
                    self.assertEqual(result.source_path.read_bytes(), payload)
                    self.assertTrue(any("Looking up" in status for status in statuses))
                    self.assertTrue(any("Downloading" in status for status in statuses))
                    logs = "\n".join(captured_logs.output)
                    self.assertIn("hosted player artifact download attempt", logs)
                    self.assertIn(f"file=Sora{suffix}", logs)
                    self.assertIn(f"bytes={len(payload)}", logs)
                    self.assertIn(hashlib.sha256(payload).hexdigest(), logs)
                finally:
                    server.shutdown()
                    server.server_close()
                    thread.join(timeout=2)

    def test_rejects_player_download_on_another_origin(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            server, thread = self._serve(artifact_name="Sora.pak", artifact_payload=b"pak")
            server.cross_origin_url = "https://example.invalid/Sora.pak"
            try:
                service = KH3PlatformSeedDownloadService(
                    download_root=Path(temp_dir),
                    attempts=1,
                )
                with self.assertLogs("Client", level="WARNING") as captured_logs:
                    with self.assertRaisesRegex(KH3PlatformSeedDownloadError, "does not match the room server"):
                        service.download_player_artifact(
                            self._slot_data(server.server_port),
                            slot_name="Sora",
                        )
                self.assertIn("hosted player artifact lookup failed", "\n".join(captured_logs.output))
                self.assertEqual(list(Path(temp_dir).rglob("*")), [])
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def test_download_logging_redacts_signed_query_parameters(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            server, thread = self._serve(artifact_name="Sora.pak", artifact_payload=b"pak")
            server.cross_origin_url = f"http://127.0.0.1:{server.server_port}/artifact?token=do-not-log-this"
            try:
                service = KH3PlatformSeedDownloadService(
                    download_root=Path(temp_dir),
                    attempts=1,
                )
                with self.assertLogs("Client", level="INFO") as captured_logs:
                    result = service.download_player_artifact(
                        self._slot_data(server.server_port),
                        slot_name="Sora",
                    )

                self.assertIsNotNone(result)
                logs = "\n".join(captured_logs.output)
                self.assertIn("/artifact", logs)
                self.assertNotIn("do-not-log-this", logs)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def test_replaces_docker_internal_room_url_with_connected_server_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            server, thread = self._serve(artifact_name="Sora.pak", artifact_payload=b"pak")
            try:
                statuses: list[str] = []
                service = KH3PlatformSeedDownloadService(
                    download_root=Path(temp_dir),
                    attempts=1,
                )
                slot_data = self._slot_data(server.server_port)
                slot_data["kh3_room"]["url"] = "http://web:3000/room/test-room"

                result = service.download_player_artifact(
                    slot_data,
                    slot_name="Sora",
                    fallback_room_url=f"http://127.0.0.1:{server.server_port}/room/test-room",
                    progress=statuses.append,
                )

                self.assertIsNotNone(result)
                assert result is not None
                self.assertEqual(result.source_path.read_bytes(), b"pak")
                self.assertEqual(
                    result.room_url,
                    f"http://127.0.0.1:{server.server_port}/room/test-room",
                )
                self.assertTrue(any("internal server address" in status for status in statuses))
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def test_room_without_platform_metadata_is_not_a_download_room(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = KH3PlatformSeedDownloadService(download_root=Path(temp_dir), attempts=1)
            self.assertIsNone(service.download_player_artifact({}, slot_name="Sora"))

    def test_authenticated_connected_url_builds_download_metadata(self) -> None:
        room_url = "https://ap.aesais.net/room/5f7d49fc-f8aa-4212-8c4a-446de7989a4d"

        metadata = kh3_platform_room_metadata_from_url(room_url)
        slot_data = kh3_slot_data_with_connected_room_url(
            {"kh3_seed": {"format": "kh3_server_seed"}},
            room_url,
        )

        self.assertEqual(
            metadata,
            {
                "format": "kh3_platform_room",
                "format_version": 1,
                "uuid": "5f7d49fc-f8aa-4212-8c4a-446de7989a4d",
                "url": room_url,
            },
        )
        self.assertEqual(slot_data["kh3_room"], metadata)

    def test_authenticated_connected_url_overrides_stale_slot_room(self) -> None:
        connected_url = "https://ap.aesais.net/room/current-room"
        slot_data = kh3_slot_data_with_connected_room_url(
            {
                "kh3_room": {
                    "format": "kh3_platform_room",
                    "format_version": 1,
                    "uuid": "stale-room",
                    "url": "https://ap.aesais.net/room/stale-room",
                    "name": "Preserved display name",
                }
            },
            connected_url,
        )

        self.assertEqual(slot_data["kh3_room"]["uuid"], "current-room")
        self.assertEqual(slot_data["kh3_room"]["url"], connected_url)
        self.assertEqual(slot_data["kh3_room"]["name"], "Preserved display name")


class TestKH3RoomPageOpening(unittest.TestCase):
    def test_missing_exact_room_url_does_not_open_a_browser(self) -> None:
        with patch("webbrowser.open") as browser:
            self.assertFalse(open_kh3_room_page_url(None))
        browser.assert_not_called()

    def test_browser_success_and_rejection_are_reported(self) -> None:
        url = "https://example.test/room/current"
        for accepted in (True, False):
            with self.subTest(accepted=accepted), patch("webbrowser.open", return_value=accepted) as browser:
                self.assertIs(open_kh3_room_page_url(url), accepted)
                browser.assert_called_once_with(url)

    def test_browser_failure_is_reported_without_escaping_to_the_ui(self) -> None:
        with patch("webbrowser.open", side_effect=OSError("browser unavailable")):
            self.assertFalse(open_kh3_room_page_url("https://example.test/room/current"))

    def test_client_uses_an_explicit_validated_url_and_rejects_unsafe_input(self) -> None:
        from ..Client import KH3Context

        ctx = KH3Context.__new__(KH3Context)
        ctx.kh3_room_page_url = lambda: "https://example.test/room/connected"
        with patch("webbrowser.open", return_value=True) as browser:
            self.assertTrue(ctx.open_kh3_room_page())
            browser.assert_called_once_with("https://example.test/room/connected")
            browser.reset_mock()
            self.assertFalse(ctx.open_kh3_room_page("file:///local-path"))
            browser.assert_not_called()
            self.assertTrue(ctx.open_kh3_room_page("https://example.test/room/selected"))
            browser.assert_called_once_with("https://example.test/room/selected")


if __name__ == "__main__":
    unittest.main()
