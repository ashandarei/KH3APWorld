"""Provision the authored host map and missing runtime defaults for a selected source."""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import urllib.request

from ..bridge_support.files import atomic_write_json
from ..installer_support.file_ops import _backup_existing_file, _replace_file_bytes
from ..installer_support.resources import _runtime_resource
from ..platform_support.download_tls import create_download_ssl_context

HOST_PAK = "KH02_CastleTownHost_P.pak"
HOST_MANIFEST = "host-manifest.json"
STORY_DEFAULTS = {"play_on_entry": False, "prepare_gameplay_on_entry": True}


def host_manifest(resource=_runtime_resource) -> dict:
    manifest = resource("runtime", "KH02", HOST_MANIFEST).read_bytes()
    if len(manifest) > 4096:
        raise ValueError("KH0.2 host manifest is too large")
    entry = json.loads(manifest)
    if (not isinstance(entry, dict) or entry.get("schema") != 1 or entry.get("file") != HOST_PAK or
            entry.get("repository") != "ashandarei/AP-GOA-KH3" or
            not isinstance(entry.get("release_tag"), str) or
            not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", entry["release_tag"]) or
            type(entry.get("size")) is not int or not 0 < entry["size"] <= 16 * 1024 * 1024 or
            not isinstance(entry.get("sha256"), str) or not re.fullmatch(r"[0-9a-f]{64}", entry["sha256"])):
        raise ValueError("Invalid KH0.2 host map manifest")
    return entry


def host_pak_bytes(resource=_runtime_resource) -> bytes:
    entry = host_manifest(resource)
    url = (f"https://github.com/{entry['repository']}/releases/download/"
           f"{entry['release_tag']}/{HOST_PAK}")
    with urllib.request.urlopen(url, timeout=60, context=create_download_ssl_context()) as response:
        data = response.read(entry["size"] + 1)
    if len(data) != entry["size"] or hashlib.sha256(data).hexdigest() != entry["sha256"]:
        raise ValueError("KH0.2 host map failed size/SHA-256 verification")
    return data


def provision_host(root: Path, resource=_runtime_resource) -> None:
    entry = host_manifest(resource)
    target = root / "Content/Paks/~mods" / HOST_PAK
    if (not target.is_file() or target.stat().st_size != entry["size"] or
            hashlib.sha256(target.read_bytes()).hexdigest() != entry["sha256"]):
        data = host_pak_bytes(resource)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            _backup_existing_file(target, datetime.now().strftime("%Y%m%d-%H%M%S-%f"))
        _replace_file_bytes(target, data)
    config = root / "Binaries/Win64/kh02_story_config.json"
    if not config.exists():
        atomic_write_json(config, STORY_DEFAULTS)
