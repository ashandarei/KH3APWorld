"""Download a connected KH3 player's hosted seed artifact from the hosting platform."""

from __future__ import annotations

import hashlib
import http.client
import json
import logging
import os
import re
import ssl
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from email.message import Message
from pathlib import Path
from typing import Any

from ..platform_support.download_tls import create_download_ssl_context

KH3_PLATFORM_ROOM_FORMAT = "kh3_platform_room"
KH3_PLATFORM_ROOM_FORMAT_VERSION = 1
KH3_PLATFORM_ROOM_KEY = "kh3_room"
KH3_PLATFORM_ROOM_SUMMARY_MAX_BYTES = 2 * 1024 * 1024
KH3_PLATFORM_ARTIFACT_MAX_BYTES = 2 * 1024 * 1024 * 1024
KH3_PLATFORM_ARTIFACT_SUFFIXES = frozenset({".pak", ".apkh3", ".zip"})
KH3_PLATFORM_DOWNLOAD_USER_AGENT = "Archipelago-KH3-Client/hosted-seed"
KH3_PLATFORM_INTERNAL_ROOM_HOSTS = frozenset(
    {
        "host.docker.internal",
        "localhost",
        "web",
    }
)

ProgressCallback = Callable[[str], None]
logger = logging.getLogger("Client")


def _url_for_log(value: str) -> str:
    """Strip query parameters and fragments so signed download credentials never reach logs."""

    parsed = urllib.parse.urlsplit(str(value or ""))
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def open_kh3_room_page_url(room_url: str | None) -> bool:
    """Open an already validated, exact room URL in the default browser."""
    if room_url is None:
        logger.warning(
            "This AP server did not publish an exact room page URL. The connection host and port alone do not "
            "identify an archipelago.gg room page."
        )
        return False
    try:
        import webbrowser

        opened = bool(webbrowser.open(room_url))
    except Exception as exc:
        logger.warning("Could not open the KH3 room page %s: %s", room_url, exc)
        return False
    if opened:
        logger.info("Opened KH3 room page: %s", room_url)
    else:
        logger.warning("The default browser did not accept the KH3 room page: %s", room_url)
    return opened


class KH3PlatformSeedDownloadError(RuntimeError):
    """A user-actionable hosted player-artifact download failure."""


@dataclass(frozen=True, slots=True)
class KH3PlatformSeedDownloadResult:
    source_path: Path
    room_url: str
    artifact_url: str
    slot_name: str


def _platform_ssl_context() -> ssl.SSLContext:
    return create_download_ssl_context()


def _is_certificate_verification_error(exc: BaseException) -> bool:
    checked: set[int] = set()
    pending: list[Any] = [exc]
    while pending:
        current = pending.pop()
        if current is None or id(current) in checked:
            continue
        checked.add(id(current))
        if isinstance(current, ssl.SSLCertVerificationError):
            return True
        if "CERTIFICATE_VERIFY_FAILED" in str(current).upper():
            return True
        pending.extend(
            (
                getattr(current, "reason", None),
                getattr(current, "__cause__", None),
                getattr(current, "__context__", None),
            )
        )
    return False


def _safe_path_fragment(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", str(value)).strip("._") or "kh3"


def _origin(url: str) -> tuple[str, str, int | None]:
    parsed = urllib.parse.urlparse(url)
    scheme = parsed.scheme.casefold()
    default_port = 443 if scheme == "https" else 80 if scheme == "http" else None
    return scheme, str(parsed.hostname or "").casefold(), parsed.port or default_port


def normalize_kh3_room_page_url(value: Any) -> str | None:
    """Return one safe HTTP(S) room page URL without guessing from a websocket address."""

    candidate = str(value or "").strip()
    if not candidate:
        return None
    try:
        parsed = urllib.parse.urlparse(candidate)
    except ValueError:
        return None
    if (
        len(candidate) <= 2048
        and parsed.scheme.casefold() in {"http", "https"}
        and parsed.netloc
        and parsed.username is None
        and parsed.password is None
    ):
        return candidate
    return None


def kh3_platform_room_metadata_from_url(room_url: Any) -> dict[str, Any] | None:
    """Build hosted-download metadata from an authenticated Connected room URL."""

    normalized_url = normalize_kh3_room_page_url(room_url)
    if normalized_url is None:
        return None
    parsed = urllib.parse.urlparse(normalized_url)
    path_parts = [part for part in parsed.path.split("/") if part]
    if len(path_parts) < 2 or path_parts[-2].casefold() != "room":
        return None
    room_id = urllib.parse.unquote(path_parts[-1]).strip()
    if not room_id or len(room_id) > 256:
        return None
    return {
        "format": KH3_PLATFORM_ROOM_FORMAT,
        "format_version": KH3_PLATFORM_ROOM_FORMAT_VERSION,
        "uuid": room_id,
        "url": normalized_url,
    }


def kh3_slot_data_with_connected_room_url(slot_data: Any, room_url: Any) -> dict[str, Any]:
    """Overlay authenticated host metadata onto KH3 Slot Data for existing download services."""

    result = dict(slot_data) if isinstance(slot_data, dict) else {}
    metadata = kh3_platform_room_metadata_from_url(room_url)
    if metadata is None:
        return result
    existing = result.get(KH3_PLATFORM_ROOM_KEY)
    merged = dict(existing) if isinstance(existing, dict) else {}
    merged.update(metadata)
    result[KH3_PLATFORM_ROOM_KEY] = merged
    return result


def kh3_platform_room_fallback_url(slot_data: Any, server_address: str | None) -> str | None:
    """Derive the public room page for metadata stamped with a known container-only hostname."""

    if not isinstance(slot_data, dict):
        return None
    metadata = slot_data.get(KH3_PLATFORM_ROOM_KEY)
    if not isinstance(metadata, dict):
        return None
    room_id = str(metadata.get("uuid") or "").strip()
    address = str(server_address or "").strip()
    if not room_id or not address:
        return None
    parse_target = address if "://" in address else f"wss://{address}"
    try:
        parsed = urllib.parse.urlparse(parse_target)
        hostname = str(parsed.hostname or "").strip()
    except ValueError:
        return None
    if not hostname or hostname.casefold() in KH3_PLATFORM_INTERNAL_ROOM_HOSTS:
        return None
    display_host = f"[{hostname}]" if ":" in hostname else hostname
    room_path = f"/room/{urllib.parse.quote(room_id, safe='')}"
    return urllib.parse.urlunparse(("https", display_host, room_path, "", "", ""))


def kh3_room_page_url_from_slot_data(slot_data: Any, server_address: str | None = None) -> str | None:
    """Extract one exact HTTP(S) room page from KH3 platform Slot Data."""

    if not isinstance(slot_data, dict):
        return None
    metadata = slot_data.get(KH3_PLATFORM_ROOM_KEY)
    candidates: list[str] = []
    if isinstance(metadata, dict):
        candidates.append(str(metadata.get("url") or "").strip())
    candidates.append(str(slot_data.get("room_url") or "").strip())
    for candidate in candidates:
        if not candidate:
            continue
        try:
            parsed = urllib.parse.urlparse(candidate)
        except ValueError:
            continue
        if str(parsed.hostname or "").casefold() in KH3_PLATFORM_INTERNAL_ROOM_HOSTS:
            candidate = kh3_platform_room_fallback_url(slot_data, server_address) or ""
        normalized_url = normalize_kh3_room_page_url(candidate)
        if normalized_url is not None:
            return normalized_url
    return None


class KH3PlatformSeedDownloadService:
    """Resolve a platform room's signed download and store one bounded player artifact."""

    def __init__(
        self,
        *,
        download_root: Path | Callable[[], Path],
        attempts: int = 3,
        retry_delay: float = 0.25,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._download_root_source = download_root
        self.attempts = max(1, int(attempts))
        self.retry_delay = max(0.0, float(retry_delay))
        self.sleep = sleep

    @property
    def download_root(self) -> Path:
        value = self._download_root_source() if callable(self._download_root_source) else self._download_root_source
        return Path(value).expanduser().resolve()

    @staticmethod
    def has_platform_room(slot_data: Any) -> bool:
        return isinstance(slot_data, dict) and isinstance(slot_data.get(KH3_PLATFORM_ROOM_KEY), dict)

    @staticmethod
    def _metadata(slot_data: dict[str, Any]) -> dict[str, Any] | None:
        metadata = slot_data.get(KH3_PLATFORM_ROOM_KEY)
        if metadata is None:
            return None
        if not isinstance(metadata, dict):
            raise KH3PlatformSeedDownloadError("The room's KH3 hosted-download metadata is invalid.")
        if metadata.get("format") != KH3_PLATFORM_ROOM_FORMAT:
            raise KH3PlatformSeedDownloadError("The room uses an unsupported KH3 hosted-download format.")
        if metadata.get("format_version") != KH3_PLATFORM_ROOM_FORMAT_VERSION:
            raise KH3PlatformSeedDownloadError(
                f"Unsupported KH3 hosted-download version: {metadata.get('format_version')!r}."
            )
        return metadata

    @staticmethod
    def _room_summary_url(
        metadata: dict[str, Any],
        *,
        fallback_room_url: str | None = None,
    ) -> tuple[str, str, str]:
        room_url = str(metadata.get("url") or "").strip()
        room_id = str(metadata.get("uuid") or "").strip()
        parsed = urllib.parse.urlparse(room_url)
        if parsed.scheme.casefold() not in {"http", "https"} or not parsed.netloc:
            raise KH3PlatformSeedDownloadError("The room did not provide a valid hosted-download URL.")
        if not room_id:
            path_parts = [part for part in parsed.path.split("/") if part]
            if len(path_parts) >= 2 and path_parts[-2].casefold() == "room":
                room_id = urllib.parse.unquote(path_parts[-1])
        if not room_id:
            raise KH3PlatformSeedDownloadError("The room did not provide its hosted room id.")

        if str(parsed.hostname or "").casefold() in KH3_PLATFORM_INTERNAL_ROOM_HOSTS and fallback_room_url:
            fallback = str(fallback_room_url).strip()
            fallback_parsed = urllib.parse.urlparse(fallback)
            fallback_parts = [part for part in fallback_parsed.path.split("/") if part]
            fallback_room_id = (
                urllib.parse.unquote(fallback_parts[-1])
                if len(fallback_parts) >= 2 and fallback_parts[-2].casefold() == "room"
                else ""
            )
            if (
                fallback_parsed.scheme.casefold() not in {"http", "https"}
                or not fallback_parsed.netloc
                or fallback_room_id != room_id
            ):
                raise KH3PlatformSeedDownloadError("The connected server did not provide a valid public room URL.")
            room_url = fallback
            parsed = fallback_parsed

        api_path = f"/api/rooms/{urllib.parse.quote(room_id, safe='')}"
        api_url = urllib.parse.urlunparse((parsed.scheme, parsed.netloc, api_path, "", "probe=0", ""))
        return room_url, room_id, api_url

    @staticmethod
    def _validate_same_origin_url(url: str, *, base_url: str, label: str) -> str:
        absolute = urllib.parse.urljoin(base_url, str(url or "").strip())
        parsed = urllib.parse.urlparse(absolute)
        if parsed.scheme.casefold() not in {"http", "https"} or not parsed.netloc:
            raise KH3PlatformSeedDownloadError(f"The hosted {label} URL is invalid.")
        if _origin(absolute) != _origin(base_url):
            raise KH3PlatformSeedDownloadError(f"The hosted {label} URL does not match the room server.")
        return absolute

    @staticmethod
    def _open(url: str, *, timeout: float, accept: str):
        request = urllib.request.Request(
            url,
            headers={"Accept": accept, "User-Agent": KH3_PLATFORM_DOWNLOAD_USER_AGENT},
        )
        if urllib.parse.urlparse(url).scheme.casefold() == "https":
            return urllib.request.urlopen(request, timeout=timeout, context=_platform_ssl_context())
        return urllib.request.urlopen(request, timeout=timeout)

    @staticmethod
    def _read_room_summary(api_url: str) -> dict[str, Any]:
        with KH3PlatformSeedDownloadService._open(
            api_url,
            timeout=20,
            accept="application/json",
        ) as response:
            payload = response.read(KH3_PLATFORM_ROOM_SUMMARY_MAX_BYTES + 1)
        if len(payload) > KH3_PLATFORM_ROOM_SUMMARY_MAX_BYTES:
            raise KH3PlatformSeedDownloadError("The hosted room response is larger than 2 MiB.")
        try:
            summary = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise KH3PlatformSeedDownloadError("The hosted room returned an invalid response.") from exc
        if not isinstance(summary, dict):
            raise KH3PlatformSeedDownloadError("The hosted room response is not a JSON object.")
        return summary

    def _resolve_artifact_url(self, api_url: str, room_id: str, slot_name: str) -> str:
        last_error: BaseException | None = None
        for attempt in range(self.attempts):
            try:
                summary = self._read_room_summary(api_url)
                if str(summary.get("roomId") or "").strip() != room_id:
                    raise KH3PlatformSeedDownloadError("The hosted room response does not match the connected room.")
                downloads = summary.get("downloads")
                if not isinstance(downloads, list):
                    raise KH3PlatformSeedDownloadError("The hosted room did not include player downloads.")
                matches = [
                    entry
                    for entry in downloads
                    if isinstance(entry, dict)
                    and str(entry.get("slotName") or "").strip().casefold() == slot_name.casefold()
                ]
                if len(matches) != 1:
                    raise KH3PlatformSeedDownloadError(
                        f"The hosted room does not have exactly one download for KH3 slot {slot_name!r}."
                    )
                download_url = str(matches[0].get("url") or "").strip()
                if not download_url:
                    raise KH3PlatformSeedDownloadError(
                        f"The hosted room has not published a file for KH3 slot {slot_name!r}."
                    )
                return self._validate_same_origin_url(download_url, base_url=api_url, label="player download")
            except (KH3PlatformSeedDownloadError, OSError, urllib.error.URLError, http.client.HTTPException) as exc:
                last_error = exc
                if attempt + 1 < self.attempts:
                    self.sleep(self.retry_delay * (attempt + 1))
        if isinstance(last_error, KH3PlatformSeedDownloadError):
            raise last_error
        if last_error is not None and _is_certificate_verification_error(last_error):
            raise KH3PlatformSeedDownloadError(
                "TLS certificate verification failed while looking up the KH3 player download. "
                "Check the system's trusted certificates or import the player file manually."
            ) from last_error
        raise KH3PlatformSeedDownloadError(f"Could not look up the KH3 player download: {last_error}") from last_error

    @staticmethod
    def _response_filename(response: Any) -> str:
        disposition = str(response.headers.get("Content-Disposition") or "")
        message = Message()
        if disposition:
            message["Content-Disposition"] = disposition
        filename = str(message.get_filename() or "").strip()
        if not filename:
            filename = Path(urllib.parse.urlparse(str(response.geturl() or "")).path).name
        if not filename or Path(filename).name != filename or filename in {".", ".."}:
            raise KH3PlatformSeedDownloadError("The hosted player download did not provide a safe filename.")
        if Path(filename).suffix.casefold() not in KH3_PLATFORM_ARTIFACT_SUFFIXES:
            raise KH3PlatformSeedDownloadError(
                f"The hosted KH3 player file {filename!r} is not a .pak, .apkh3, or generated .zip."
            )
        return filename

    def _download_artifact(self, artifact_url: str, destination_dir: Path) -> Path:
        destination_dir.mkdir(parents=True, exist_ok=True)
        stage_path: Path | None = None
        try:
            with self._open(artifact_url, timeout=120, accept="application/octet-stream") as response:
                final_url = str(response.geturl() or artifact_url)
                self._validate_same_origin_url(final_url, base_url=artifact_url, label="download response")
                filename = self._response_filename(response)
                content_length = response.headers.get("Content-Length")
                declared_size: int | None = None
                if content_length is not None:
                    try:
                        declared_size = int(content_length)
                    except (TypeError, ValueError) as exc:
                        raise KH3PlatformSeedDownloadError("The hosted player download has an invalid size.") from exc
                    if declared_size < 0 or declared_size > KH3_PLATFORM_ARTIFACT_MAX_BYTES:
                        raise KH3PlatformSeedDownloadError("The hosted KH3 player file is larger than 2 GiB.")

                descriptor, temporary_name = tempfile.mkstemp(
                    prefix=f".{_safe_path_fragment(filename)}.",
                    suffix=".part",
                    dir=destination_dir,
                )
                os.close(descriptor)
                stage_path = Path(temporary_name)
                downloaded = 0
                digest = hashlib.sha256()
                with stage_path.open("wb") as output:
                    while True:
                        chunk = response.read(1024 * 1024)
                        if not chunk:
                            break
                        downloaded += len(chunk)
                        if downloaded > KH3_PLATFORM_ARTIFACT_MAX_BYTES:
                            raise KH3PlatformSeedDownloadError("The hosted KH3 player file is larger than 2 GiB.")
                        digest.update(chunk)
                        output.write(chunk)
                    output.flush()
                    os.fsync(output.fileno())
                if downloaded == 0:
                    raise KH3PlatformSeedDownloadError("The hosted KH3 player file is empty.")
                if declared_size is not None and downloaded != declared_size:
                    raise KH3PlatformSeedDownloadError(
                        f"The hosted KH3 player file was truncated ({downloaded} of {declared_size} bytes)."
                    )
                destination = destination_dir / filename
                os.replace(stage_path, destination)
                stage_path = None
                resolved_destination = destination.resolve()
                logger.info(
                    "KH3 hosted player artifact downloaded: file=%s bytes=%d sha256=%s source=%s destination=%s",
                    filename,
                    downloaded,
                    digest.hexdigest(),
                    _url_for_log(final_url),
                    resolved_destination,
                )
                return resolved_destination
        except KH3PlatformSeedDownloadError:
            raise
        except (OSError, urllib.error.URLError, http.client.HTTPException) as exc:
            if _is_certificate_verification_error(exc):
                raise KH3PlatformSeedDownloadError(
                    "TLS certificate verification failed while downloading the KH3 player file. "
                    "Check the system's trusted certificates or import the player file manually."
                ) from exc
            raise KH3PlatformSeedDownloadError(f"Could not download the KH3 player file: {exc}") from exc
        finally:
            if stage_path is not None:
                try:
                    stage_path.unlink(missing_ok=True)
                except OSError:
                    pass

    def download_player_artifact(
        self,
        slot_data: dict[str, Any],
        *,
        slot_name: str,
        fallback_room_url: str | None = None,
        progress: ProgressCallback | None = None,
    ) -> KH3PlatformSeedDownloadResult | None:
        metadata = self._metadata(slot_data)
        if metadata is None:
            return None
        requested_slot = str(slot_name or "").strip()
        if not requested_slot:
            raise KH3PlatformSeedDownloadError("The KH3 slot name is required to select its hosted player file.")
        advertised_room_url = str(metadata.get("url") or "").strip()
        room_url, room_id, api_url = self._room_summary_url(
            metadata,
            fallback_room_url=fallback_room_url,
        )
        if progress is not None and room_url != advertised_room_url:
            progress("The room advertised an internal server address; using the connected server's public address...")
        if progress is not None:
            progress("Looking up this KH3 slot's hosted player file...")
        logger.info(
            "KH3 hosted player artifact lookup: room=%s slot=%r endpoint=%s",
            room_id,
            requested_slot,
            _url_for_log(api_url),
        )
        try:
            artifact_url = self._resolve_artifact_url(api_url, room_id, requested_slot)
        except KH3PlatformSeedDownloadError as exc:
            logger.warning(
                "KH3 hosted player artifact lookup failed: room=%s slot=%r endpoint=%s error=%s",
                room_id,
                requested_slot,
                _url_for_log(api_url),
                exc,
            )
            raise
        if progress is not None:
            progress("Downloading the KH3 player file. Large seed paks can take a moment...")
        destination_dir = self.download_root / _safe_path_fragment(room_id) / _safe_path_fragment(requested_slot)
        logger.info(
            "KH3 hosted player artifact download attempt: room=%s slot=%r source=%s destination=%s",
            room_id,
            requested_slot,
            _url_for_log(artifact_url),
            destination_dir,
        )
        try:
            source_path = self._download_artifact(artifact_url, destination_dir)
        except KH3PlatformSeedDownloadError as exc:
            logger.warning(
                "KH3 hosted player artifact download failed: room=%s slot=%r source=%s error=%s",
                room_id,
                requested_slot,
                _url_for_log(artifact_url),
                exc,
            )
            raise
        return KH3PlatformSeedDownloadResult(
            source_path=source_path,
            room_url=room_url,
            artifact_url=artifact_url,
            slot_name=requested_slot,
        )


__all__ = [
    "KH3_PLATFORM_ARTIFACT_MAX_BYTES",
    "KH3_PLATFORM_ARTIFACT_SUFFIXES",
    "KH3_PLATFORM_ROOM_FORMAT",
    "KH3_PLATFORM_ROOM_FORMAT_VERSION",
    "KH3_PLATFORM_ROOM_KEY",
    "KH3PlatformSeedDownloadError",
    "KH3PlatformSeedDownloadResult",
    "KH3PlatformSeedDownloadService",
    "kh3_platform_room_metadata_from_url",
    "kh3_platform_room_fallback_url",
    "kh3_room_page_url_from_slot_data",
    "kh3_slot_data_with_connected_room_url",
    "normalize_kh3_room_page_url",
]
