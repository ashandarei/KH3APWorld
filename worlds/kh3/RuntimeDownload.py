"""Fetch the UE4SS release during Patch Game, before changing installed files."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any

from .bridge_support.files import atomic_write_json, load_optional_json_file
from .platform_support.download_tls import create_download_ssl_context

logger = logging.getLogger("Client")
REPOSITORY = "ashandarei/ue4ss-goa"
RELEASE_API_URL = f"https://api.github.com/repos/{REPOSITORY}/releases/tags/"
RELEASE_CONFIG_NAME = "ue4ss-release.json"
DLL_NAMES = ("UE4SS.dll", "dwmapi.dll")
MAX_DLL_SIZE = 128 * 1024 * 1024
_CACHE_LOCK = threading.RLock()


def runtime_cache_path() -> Path:
    import Utils
    return Path(Utils.user_path("kh3", "cache", "ue4ss-status.json")).resolve()


def _load_cache(config: dict[str, Any]) -> dict[str, Any]:
    expected = {"format": "kh3_ue4ss_status_cache", "version": 2,
                "repository": REPOSITORY, "proxy_sha256": config.get("proxy_sha256"),
                "release_tag": config.get("release_tag"), "ue4ss_sha256": config.get("ue4ss_sha256"),
                "manifest_checked_at": 0, "manifest": None, "file_hashes": {}}
    cache = load_optional_json_file(runtime_cache_path())
    if not isinstance(cache, dict) or any(cache.get(key) != expected[key] for key in
                                         ("format", "version", "repository", "proxy_sha256",
                                          "release_tag", "ue4ss_sha256")):
        return expected
    if not isinstance(cache.get("file_hashes"), dict):
        cache["file_hashes"] = {}
    return cache


def _write_cache(cache: dict[str, Any]) -> None:
    try:
        atomic_write_json(runtime_cache_path(), cache)
    except OSError as exc:
        logger.debug("Could not save UE4SS status cache: %s", exc)


def _signature(path: Path) -> list[int] | None:
    try:
        stat = path.stat()
        return [stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size]
    except OSError:
        return None


def _file_key(path: Path) -> str:
    return os.path.normcase(str(path.resolve()))


def _cached_hash(path: Path, cache: dict[str, Any]) -> str | None:
    entry = cache["file_hashes"].get(_file_key(path))
    if isinstance(entry, dict) and entry.get("signature") == _signature(path):
        digest = entry.get("sha256")
        if isinstance(digest, str) and re.fullmatch(r"[0-9a-f]{64}", digest):
            return digest
    return None


def _store_hash(path: Path, digest: str, cache: dict[str, Any]) -> None:
    hashes = cache["file_hashes"]
    hashes[_file_key(path)] = {"signature": _signature(path), "sha256": digest}
    # Bound cache growth when patching multiple installations.
    while len(hashes) > 24:
        del hashes[next(iter(hashes))]


def _cached_release(config: dict[str, Any], cache: dict[str, Any]) -> dict[str, Any] | None:
    try:
        return _normalize_release(cache.get("manifest"), config)
    except (ValueError, TypeError):
        return None


def cached_file_states(config: dict[str, Any], win64: Path) -> dict[str, str]:
    """Startup/status path: cached release and stat-bound hashes only; no network/hash work."""
    with _CACHE_LOCK:
        cache = _load_cache(config)
    release = _cached_release(config, cache)
    states = {}
    for name in DLL_NAMES:
        path = win64 / name
        signature = _signature(path)
        state = "missing" if signature is None else "installed"
        if signature is not None and release is not None:
            expected = release["files"][name]
            digest = _cached_hash(path, cache)
            if signature[2] != expected["size"] or (digest is not None and digest != expected["sha256"]):
                state = "different"
        states[name] = state
    return states


def record_installed(config: dict[str, Any], win64: Path, release: dict[str, Any]) -> None:
    with _CACHE_LOCK:
        cache = _load_cache(config)
        for name, asset in release["files"].items():
            _store_hash(win64 / name, asset["sha256"], cache)
        _write_cache(cache)


def _read_url(url: str, limit: int) -> bytes:
    request = urllib.request.Request(url, headers={
        "User-Agent": "KH3-Archipelago-Runtime",
        "Accept": "application/vnd.github+json" if url.startswith(RELEASE_API_URL) else "application/octet-stream",
    })
    context = create_download_ssl_context()
    with urllib.request.urlopen(request, timeout=60, context=context) as response:
        data = response.read(limit + 1)
    if len(data) > limit:
        raise ValueError(f"UE4SS response exceeds its size limit: {url}")
    return data


def release_url(config: dict[str, Any]) -> str:
    """Resolve only the release explicitly selected by this APWorld."""
    tag = config.get("release_tag")
    if config.get("repository") != REPOSITORY:
        raise ValueError("UE4SS release configuration has an unexpected repository")
    if not isinstance(tag, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", tag):
        raise ValueError("UE4SS release configuration has no valid pinned tag")
    return RELEASE_API_URL + tag


def pinned_release(config: dict[str, Any]) -> dict[str, Any]:
    """Require the pinned release and independently pinned SHA-256 for every DLL."""
    release = json.loads(_read_url(release_url(config), 1024 * 1024))
    return _normalize_release(release, config)


def _normalize_release(release: Any, config: dict[str, Any]) -> dict[str, Any]:
    release_url(config)
    if not isinstance(release, dict) or release.get("draft") or release.get("prerelease"):
        raise ValueError("UE4SS pinned release is not a published stable release")
    tag = release.get("tag_name")
    if not isinstance(tag, str) or not tag:
        raise ValueError("UE4SS release is missing its tag")
    if tag != config["release_tag"]:
        raise ValueError("UE4SS release differs from this APWorld's pinned tag")
    assets = release.get("assets")
    if not isinstance(assets, list):
        raise ValueError("UE4SS release is missing its assets")
    selected = {}
    for name in DLL_NAMES:
        matches = [asset for asset in assets if isinstance(asset, dict) and asset.get("name") == name]
        if len(matches) != 1:
            raise ValueError(f"UE4SS release {tag} must contain exactly one {name}")
        asset = matches[0]
        digest = asset.get("digest", "")
        size = asset.get("size")
        url = asset.get("browser_download_url", "")
        if not isinstance(digest, str) or not re.fullmatch(r"sha256:[0-9a-fA-F]{64}", digest):
            raise ValueError(f"UE4SS release asset {name} has no valid SHA-256 digest")
        if type(size) is not int or not 0 < size <= MAX_DLL_SIZE:
            raise ValueError(f"UE4SS release asset {name} has an invalid size")
        if url != f"https://github.com/{REPOSITORY}/releases/download/{tag}/{name}":
            raise ValueError(f"UE4SS release asset {name} has an unexpected download URL")
        sha256 = digest.split(":", 1)[1].lower()
        pin = config.get("ue4ss_sha256") if name == "UE4SS.dll" else config.get("proxy_sha256", {}).get(name)
        if not isinstance(pin, str) or sha256 != pin.lower():
            raise ValueError(f"UE4SS release {tag}: {name} differs from this APWorld's pinned DLL")
        selected[name] = {"sha256": sha256, "size": size, "url": url}
    return {"tag": tag, "files": selected, "manifest": release}


def stage_runtime(config: dict[str, Any], win64: Path, staging: Path) -> dict[str, Any]:
    """Download and verify every needed DLL before the installer writes anything."""
    with _CACHE_LOCK:
        cache = _load_cache(config)
    try:
        # Patch Game refreshes the pinned UE4SS release once. No
        # background/status caller enters this network path.
        release = pinned_release(config)
    except OSError as exc:
        release = _cached_release(config, cache)
        if release is None:
            raise
        logger.warning("UE4SS release check unavailable; using cached release %s: %s", release["tag"], exc)
    cache["manifest_checked_at"] = time.time()
    cache["manifest"] = release["manifest"]
    with _CACHE_LOCK:
        _write_cache(cache)
    downloaded = []
    skipped = []
    logger.info("UE4SS: checking release %s from %s.", release["tag"], REPOSITORY)
    for name, asset in release["files"].items():
        target = win64 / name
        digest = _cached_hash(target, cache)
        if target.is_file() and digest is None:
            digest = hashlib.sha256(target.read_bytes()).hexdigest()
            _store_hash(target, digest, cache)
        if target.is_file() and target.stat().st_size == asset["size"] and digest == asset["sha256"]:
            skipped.append(name)
            continue
        logger.info("UE4SS: downloading %s (%s bytes).", name, asset["size"])
        data = _read_url(asset["url"], asset["size"])
        if len(data) != asset["size"] or hashlib.sha256(data).hexdigest() != asset["sha256"]:
            raise ValueError(f"UE4SS download failed size/SHA-256 verification: {name}")
        (staging / name).write_bytes(data)
        downloaded.append(name)
    with _CACHE_LOCK:
        _write_cache(cache)
    return {**release, "downloaded": downloaded, "skipped": skipped}
