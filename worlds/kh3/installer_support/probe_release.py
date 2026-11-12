"""Resolve and verify the native mod selected by this APWorld."""

from __future__ import annotations

import hashlib
import io
import json
import re
import urllib.request
import zipfile
from pathlib import Path
from types import SimpleNamespace

from ..platform_support.download_tls import create_download_ssl_context

CONFIG_PATH = ("runtime", "Mods", "KH3APProbeCpp", "runtime-release.json")
REPOSITORY = "ashandarei/KH3Runtime"
DLL_ENTRY = "KH3APProbeCpp/dlls/main.dll"
MAX_SIZE = 128 * 1024 * 1024


def validate_config(config: dict) -> dict:
    if not isinstance(config, dict) or config.get("repository") != REPOSITORY:
        raise ValueError("Unexpected KH3Runtime release repository")
    version = config.get("version")
    if not isinstance(version, str) or not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError("Invalid KH3Runtime version")
    release_tag = config.get("release_tag")
    if not isinstance(release_tag, str) or not re.fullmatch(
        rf"v?{re.escape(version)}(?:-beta\.[1-9][0-9]*)?", release_tag
    ):
        raise ValueError("KH3Runtime release tag must match the version or a numbered beta")
    for field in ("sha256", "archive_sha256"):
        if not isinstance(config.get(field), str) or not re.fullmatch(r"[0-9a-f]{64}", config[field]):
            raise ValueError(f"Invalid KH3Runtime {field}")
    for field in ("size", "archive_size"):
        if type(config.get(field)) is not int or not 0 < config[field] <= MAX_SIZE:
            raise ValueError(f"Invalid KH3Runtime {field}")
    expected_asset = f"KH3Runtime-{config['version']}-{config['sha256'][:12]}.zip"
    if config.get("asset") != expected_asset:
        raise ValueError("Unexpected KH3Runtime release asset")
    return config


def _download(url: str, size: int) -> bytes:
    request = urllib.request.Request(url, headers={
        "User-Agent": "KH3-Archipelago-Runtime", "Accept": "application/octet-stream"})
    with urllib.request.urlopen(request, timeout=60, context=create_download_ssl_context()) as response:
        return response.read(size + 1)


def _player_metadata(package: zipfile.ZipFile, config: dict) -> None:
    records = []
    for name in (DLL_ENTRY + ".build.json", "manifest.json"):
        entries = [entry for entry in package.infolist() if entry.filename == name]
        if len(entries) != 1 or not 0 < entries[0].file_size <= 65536:
            raise ValueError("KH3Runtime archive has invalid player metadata")
        record = json.loads(package.read(entries[0]))
        if not isinstance(record, dict) or record.get("sha256") != config["sha256"] or (
            record.get("first_party_license") != "MIT"
            or record.get("performance_diagnostics") is not False
            or record.get("automation_handlers") is not False
        ):
            raise ValueError("KH3Runtime release must contain a verified MIT player build")
        records.append(record)
    build, manifest = records
    if build.get("schema") != 3 or build.get("release_eligible") is not True or (
        build.get("ue4ss_dirty") is not False or build.get("allow_unpinned_ue4ss") is not False
        or not re.fullmatch(r"[0-9a-f]{40}", str(build.get("ue4ss_revision", "")))
        or manifest.get("version") != config["version"]
    ):
        raise ValueError("KH3Runtime release has invalid provenance or version")


class PinnedProbeResource:
    """Metadata is local; only explicit installation reads/downloads the DLL."""

    name = "main.dll"

    def __init__(self, config_resource):
        self.config_resource = config_resource
        self._data: bytes | None = None
        self._config: dict | None = None

    @property
    def config(self) -> dict:
        if self._config is None:
            self._config = validate_config(json.loads(self.config_resource.read_bytes()))
        return self._config

    def __str__(self) -> str:
        return f"KH3Runtime release ({self.config_resource})"

    def is_file(self) -> bool:
        try:
            self.config
            return True
        except (OSError, ValueError, TypeError):
            return False

    def stat(self):
        return SimpleNamespace(st_size=self.config["size"])

    def matches(self, info: dict) -> bool:
        return self.is_file() and info.get("size") == self.config["size"] and (
            str(info.get("sha256", "")).lower() == self.config["sha256"])

    def use_installed(self, path: Path) -> None:
        """A verified existing DLL can repair another layout without network access."""
        if self._data is not None:
            return
        if path.is_file() and path.stat().st_size == self.config["size"]:
            data = path.read_bytes()
            if hashlib.sha256(data).hexdigest() == self.config["sha256"]:
                self._data = data

    def read_bytes(self) -> bytes:
        if self._data is not None:
            return self._data
        config = self.config
        url = f"https://github.com/{REPOSITORY}/releases/download/{config['release_tag']}/{config['asset']}"
        archive = _download(url, config["archive_size"])
        if len(archive) != config["archive_size"] or hashlib.sha256(archive).hexdigest() != config["archive_sha256"]:
            raise ValueError("KH3Runtime archive failed size/SHA-256 verification")
        with zipfile.ZipFile(io.BytesIO(archive)) as package:
            matches = [entry for entry in package.infolist() if entry.filename == DLL_ENTRY]
            if len(matches) != 1 or matches[0].file_size != config["size"]:
                raise ValueError("KH3Runtime archive has an invalid DLL entry")
            data = package.read(matches[0])
            _player_metadata(package, config)
        if len(data) != config["size"] or hashlib.sha256(data).hexdigest() != config["sha256"]:
            raise ValueError("KH3Runtime main.dll failed size/SHA-256 verification")
        self._data = data
        return data
