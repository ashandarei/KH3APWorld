"""Validate and install the Garden of Assemblage pak pinned by this client."""

from __future__ import annotations

import hashlib
import logging
import os
import re
import ssl
import threading
import urllib.request
from pathlib import Path
from typing import Any

import Utils

from ..bridge_support.files import atomic_write_json, load_optional_json_file
from ..platform_support.download_tls import create_download_ssl_context

logger = logging.getLogger("Client")

KH3_GOA_MOD_FILE_NAME = "pakchunk99-GardenOfAssemblageRandomizer_99_P.pak"
KH3_GOA_MOD_GITHUB_OWNER = "ashandarei"
KH3_GOA_MOD_GITHUB_REPO = "AP-GOA-KH3"
# Explicit compatibility selection for the 2.0 client; do not infer
# compatibility from whichever lower release happens to exist on GitHub.
KH3_GOA_MOD_RELEASE_TAG = "2.0.1"
KH3_GOA_MOD_SIZE = 27531879
KH3_GOA_MOD_SHA256 = "EAACFA102E52FF16673CC26599B7A29CD7BEDFB92D912B565F7EDAC3D4327B1C"
KH3_GOA_MOD_DOWNLOAD_URL = (
    f"https://github.com/{KH3_GOA_MOD_GITHUB_OWNER}/{KH3_GOA_MOD_GITHUB_REPO}/releases/download/"
    f"{KH3_GOA_MOD_RELEASE_TAG}/{KH3_GOA_MOD_FILE_NAME}"
)
KH3_GOA_MOD_PATTERN = "pakchunk99-GardenOfAssemblageRandomizer*.pak"
KH3_GOA_STATUS_CACHE_FORMAT = "kh3_goa_status_cache"
KH3_GOA_STATUS_CACHE_VERSION = 3

_GOA_STATUS_CACHE_LOCK = threading.Lock()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _goa_mod_ssl_context() -> ssl.SSLContext:
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
            [
                getattr(current, "reason", None),
                getattr(current, "__cause__", None),
                getattr(current, "__context__", None),
            ]
        )
    return False


def _format_goa_mod_download_error(exc: BaseException) -> str:
    message = str(exc)
    if _is_certificate_verification_error(exc):
        return (
            f"{message}. TLS certificate verification failed while downloading the Garden of Assemblage pak; "
            "check the system's trusted certificates or install the supported GoA pak manually from the KH3 setup page."
        )
    return message


def _open_goa_mod_url(url: str, *, timeout: float):
    request = urllib.request.Request(url)
    return urllib.request.urlopen(request, timeout=timeout, context=_goa_mod_ssl_context())


def _download_goa_mod_file(url: str, destination: Path) -> None:
    with _open_goa_mod_url(url, timeout=60) as response, destination.open("wb") as output:
        remaining = KH3_GOA_MOD_SIZE
        while remaining:
            chunk = response.read(min(1024 * 1024, remaining))
            if not chunk:
                break
            output.write(chunk)
            remaining -= len(chunk)
        if response.read(1):
            raise ValueError("GoA pak download exceeds this client's pinned size")


def _installed_goa_mod_paths(kh3_mods_dir: Path) -> list[Path]:
    if not kh3_mods_dir.is_dir():
        return []
    return sorted(
        (path for path in kh3_mods_dir.glob(KH3_GOA_MOD_PATTERN) if path.is_file()),
        key=lambda path: (path.name.casefold() != KH3_GOA_MOD_FILE_NAME.casefold(), path.name.casefold()),
    )


def goa_mod_status_cache_path() -> Path:
    """Return the user-scoped cache used by the KH3 client startup path."""

    return Path(Utils.user_path("kh3", "cache", "goa-status.json")).resolve()


def _new_goa_status_cache() -> dict[str, Any]:
    return {
        "format": KH3_GOA_STATUS_CACHE_FORMAT,
        "version": KH3_GOA_STATUS_CACHE_VERSION,
        "release_tag": KH3_GOA_MOD_RELEASE_TAG,
        "expected_size": KH3_GOA_MOD_SIZE,
        "expected_sha256": KH3_GOA_MOD_SHA256,
        "file_name": KH3_GOA_MOD_FILE_NAME,
        "file_hashes": {},
    }


def _load_goa_status_cache(cache_path: Path | None) -> dict[str, Any]:
    if cache_path is None:
        return _new_goa_status_cache()
    payload = load_optional_json_file(cache_path)
    if not isinstance(payload, dict):
        return _new_goa_status_cache()
    expected = _new_goa_status_cache()
    if any(
        payload.get(key) != expected[key]
        for key in ("format", "version", "release_tag", "expected_size", "expected_sha256", "file_name")
    ):
        return expected
    if not isinstance(payload.get("file_hashes"), dict):
        payload["file_hashes"] = {}
    return payload


def _write_goa_status_cache(cache_path: Path | None, payload: dict[str, Any]) -> None:
    if cache_path is None:
        return
    try:
        atomic_write_json(cache_path, payload)
    except OSError as exc:
        logger.debug("Could not update KH3 GoA status cache %s: %s", cache_path, exc)


def _goa_cache_file_key(path: Path) -> str:
    try:
        resolved = path.expanduser().resolve()
    except OSError:
        resolved = path.expanduser()
    return os.path.normcase(str(resolved))


def _goa_file_signature(path: Path) -> tuple[int, int, int] | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    return stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size


def _cached_goa_file_sha256(path: Path, cache: dict[str, Any]) -> str | None:
    signature = _goa_file_signature(path)
    if signature is None:
        return None
    entry = cache.get("file_hashes", {}).get(_goa_cache_file_key(path))
    if not isinstance(entry, dict):
        return None
    sha256 = entry.get("sha256")
    if (
        entry.get("mtime_ns") != signature[0]
        or entry.get("ctime_ns") != signature[1]
        or entry.get("size") != signature[2]
        or not isinstance(sha256, str)
        or re.fullmatch(r"[0-9A-F]{64}", sha256) is None
    ):
        return None
    return sha256


def _store_goa_file_sha256(path: Path, sha256: str, cache: dict[str, Any]) -> None:
    signature = _goa_file_signature(path)
    if signature is None:
        return
    hashes = cache.setdefault("file_hashes", {})
    hashes[_goa_cache_file_key(path)] = {
        "path": str(path),
        "mtime_ns": signature[0],
        "ctime_ns": signature[1],
        "size": signature[2],
        "sha256": sha256,
    }
    if len(hashes) > 8:
        ordered = sorted(
            hashes.items(),
            key=lambda entry: int(entry[1].get("mtime_ns", 0)) if isinstance(entry[1], dict) else 0,
            reverse=True,
        )
        cache["file_hashes"] = dict(ordered[:8])


def _goa_mod_path_matches_pins(
    path: Path,
    *,
    cache: dict[str, Any] | None = None,
    allow_hash: bool = True,
) -> bool | None:
    if path.name.casefold() != KH3_GOA_MOD_FILE_NAME.casefold():
        return False
    try:
        if path.stat().st_size != KH3_GOA_MOD_SIZE:
            return False
        actual_sha256 = _cached_goa_file_sha256(path, cache) if cache is not None else None
        if actual_sha256 is None:
            if not allow_hash:
                return None
            actual_sha256 = _sha256_file(path)
            if cache is not None:
                _store_goa_file_sha256(path, actual_sha256, cache)
        return actual_sha256 == KH3_GOA_MOD_SHA256
    except OSError as exc:
        logger.warning("Could not hash installed Garden of Assemblage mod %s: %s", path, exc)
        return False


def goa_mod_install_status(
    kh3_mods_dir: Path,
    *,
    cache_path: Path | None = None,
    allow_hash: bool = True,
) -> dict[str, Any]:
    installed_goa_paths = _installed_goa_mod_paths(kh3_mods_dir)
    with _GOA_STATUS_CACHE_LOCK:
        cache = _load_goa_status_cache(cache_path)
    status: dict[str, Any] = {
        "status": "missing",
        "installed": bool(installed_goa_paths),
        "installed_paths": installed_goa_paths,
        "expected_version": KH3_GOA_MOD_RELEASE_TAG,
        "expected_file_name": KH3_GOA_MOD_FILE_NAME,
        "expected_size": KH3_GOA_MOD_SIZE,
        "expected_sha256": KH3_GOA_MOD_SHA256,
        "expected_url": KH3_GOA_MOD_DOWNLOAD_URL,
    }
    if installed_goa_paths:
        status["installed_path"] = installed_goa_paths[0]
        try:
            status["installed_size"] = installed_goa_paths[0].stat().st_size
        except OSError:
            status["installed_size"] = None
    path_matches = [
        (
            path,
            _goa_mod_path_matches_pins(
                path,
                cache=cache,
                allow_hash=allow_hash,
            ),
        )
        for path in installed_goa_paths
    ]
    current_path = next((path for path, matches in path_matches if matches is True), None)
    verification_deferred = any(matches is None for _path, matches in path_matches)
    status["status"] = (
        "current"
        if current_path is not None
        else "unknown"
        if verification_deferred
        else "outdated"
        if installed_goa_paths
        else "missing"
    )
    if verification_deferred:
        status["verification_deferred"] = True
    if current_path is not None:
        status["installed_path"] = current_path
        try:
            status["installed_size"] = current_path.stat().st_size
        except OSError:
            status["installed_size"] = None
    if cache_path is not None and allow_hash:
        with _GOA_STATUS_CACHE_LOCK:
            latest_cache = _load_goa_status_cache(cache_path)
            latest_cache["file_hashes"] = cache.get("file_hashes", {})
            _write_goa_status_cache(cache_path, latest_cache)
    return status


def _remove_existing_goa_mods(kh3_mods_dir: Path, *, keep_path: Path | None = None) -> int:
    removed = 0
    keep_resolved = keep_path.resolve() if keep_path is not None else None
    for existing_path in _installed_goa_mod_paths(kh3_mods_dir):
        if keep_resolved is not None and existing_path.resolve() == keep_resolved:
            continue
        try:
            existing_path.unlink(missing_ok=True)
            removed += 1
        except OSError as exc:
            logger.warning("Could not remove stale Garden of Assemblage pak %s: %s", existing_path, exc)
    return removed


def _validate_downloaded_goa_mod(path: Path) -> None:
    actual_size = path.stat().st_size
    if actual_size != KH3_GOA_MOD_SIZE:
        raise RuntimeError(f"downloaded GoA pak size mismatch: expected {KH3_GOA_MOD_SIZE}, got {actual_size}")
    actual_sha256 = _sha256_file(path)
    if actual_sha256 != KH3_GOA_MOD_SHA256:
        raise RuntimeError(f"downloaded GoA pak sha256 mismatch: expected {KH3_GOA_MOD_SHA256}, got {actual_sha256}")


def download_goa_mod(kh3_mods_dir: Path, *, cache_path: Path | None = None) -> None:
    kh3_mods_dir.mkdir(parents=True, exist_ok=True)
    with _GOA_STATUS_CACHE_LOCK:
        cache = _load_goa_status_cache(cache_path)
    current_path = next(
        (
            path
            for path in _installed_goa_mod_paths(kh3_mods_dir)
            if _goa_mod_path_matches_pins(path, cache=cache) is True
        ),
        None,
    )
    if current_path is not None:
        if cache_path is not None:
            with _GOA_STATUS_CACHE_LOCK:
                _write_goa_status_cache(cache_path, cache)
        removed = _remove_existing_goa_mods(kh3_mods_dir, keep_path=current_path)
        logger.info(
            "Garden of Assemblage mod is ready (version %s, verified); no update needed.",
            KH3_GOA_MOD_RELEASE_TAG,
        )
        logger.debug(
            "Verified Garden of Assemblage mod at %s (sha256 %s).",
            current_path,
            KH3_GOA_MOD_SHA256[:8],
        )
        if removed:
            logger.info("Removed %s older Garden of Assemblage pak(s).", removed)
        return

    target_path = kh3_mods_dir / KH3_GOA_MOD_FILE_NAME
    temp_path = kh3_mods_dir / f"{KH3_GOA_MOD_FILE_NAME}.download"
    try:
        temp_path.unlink(missing_ok=True)
        logger.info("Downloading Garden of Assemblage mod to %s", target_path)
        _download_goa_mod_file(KH3_GOA_MOD_DOWNLOAD_URL, temp_path)
        _validate_downloaded_goa_mod(temp_path)
        temp_path.replace(target_path)
        if cache_path is not None:
            _store_goa_file_sha256(target_path, KH3_GOA_MOD_SHA256, cache)
            with _GOA_STATUS_CACHE_LOCK:
                _write_goa_status_cache(cache_path, cache)
        removed = _remove_existing_goa_mods(kh3_mods_dir, keep_path=target_path)
        logger.info(
            "Garden of Assemblage mod installed and verified (version %s).",
            KH3_GOA_MOD_RELEASE_TAG,
        )
        logger.debug("Installed Garden of Assemblage mod at %s.", target_path)
        if removed:
            logger.info("Removed %s older Garden of Assemblage pak(s).", removed)
    except Exception as exc:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass
        logger.warning(
            "Could not download Garden of Assemblage mod to %s: %s",
            target_path,
            _format_goa_mod_download_error(exc),
        )


__all__ = [
    "KH3_GOA_MOD_DOWNLOAD_URL",
    "KH3_GOA_MOD_FILE_NAME",
    "KH3_GOA_MOD_GITHUB_OWNER",
    "KH3_GOA_MOD_GITHUB_REPO",
    "KH3_GOA_MOD_PATTERN",
    "KH3_GOA_MOD_RELEASE_TAG",
    "KH3_GOA_STATUS_CACHE_FORMAT",
    "KH3_GOA_STATUS_CACHE_VERSION",
    "download_goa_mod",
    "goa_mod_install_status",
    "goa_mod_status_cache_path",
]
