"""Download the pinned MoM runtime and notices only during explicit Install/Repair."""
from __future__ import annotations

import hashlib
import io
import json
import re
import tempfile
import zipfile
from pathlib import Path

REPOSITORY = "ashandarei/MoM-GoA-AP"
RELEASE_TAG = "v0.1.4"
RELEASE_URL = f"https://api.github.com/repos/{REPOSITORY}/releases/tags/{RELEASE_TAG}"
RUNTIME_ASSET = "MoM-GoA-AP-runtime.zip"
SOURCE_ASSET = "MoM-GoA-AP-source.zip"
# Pin the complete runtime (DLL, manifest and notices) and its matching source.
# Update together only after checking compatibility with this client's loader.
ASSET_SHA256 = {
    RUNTIME_ASSET: "7fb88ebc832f872dfb6e35cfde86b7bc36cd6082fce2e55c0b8c8419f4a28bf1",
    SOURCE_ASSET: "3e5d736e11b4b74f5fc9cf7b92c679f321d6cf026f885a4ec73c16db481a7200",
}
NOTICE_DIR = "UserData/MoMController"
LEGAL_FILES = ("LICENSE", "THIRD_PARTY_NOTICES.md", "THIRD_PARTY_LICENSES.txt", "SOURCE.txt")
RUNTIME_FILES = ("MoMController.dll", "manifest.json", *LEGAL_FILES)
MANAGED_FILES = ("Mods/MoMController.dll", *(f"{NOTICE_DIR}/{name}" for name in ("manifest.json", *LEGAL_FILES)))
MAX_RUNTIME_SIZE = 16 * 1024 * 1024


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read_url(url: str, limit: int) -> bytes:
    # Use the same downloader as MelonLoader/GoA, including system TLS trust and
    # host curl/wget in the frozen Linux client. Never ship GitHub credentials.
    from ..PakGenerator import _download_file
    with tempfile.TemporaryDirectory(prefix="mom-release-") as directory:
        path = Path(directory) / "download"
        _download_file(url, path)
        with path.open("rb") as stream:
            data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError("MoM release download exceeds its size limit.")
    return data


def _asset(release: dict, name: str, limit: int) -> dict:
    assets = release.get("assets")
    if not isinstance(assets, list):
        raise ValueError("MoM release has no assets.")
    matches = [item for item in assets if isinstance(item, dict) and item.get("name") == name]
    if len(matches) != 1:
        raise ValueError(f"MoM release must contain exactly one {name}.")
    asset = matches[0]
    digest = asset.get("digest")
    size = asset.get("size")
    expected_url = f"https://github.com/{REPOSITORY}/releases/download/{release['tag_name']}/{name}"
    if (not isinstance(digest, str) or not re.fullmatch(r"sha256:[a-fA-F0-9]{64}", digest)
            or type(size) is not int or not 0 < size <= limit
            or asset.get("browser_download_url") != expected_url):
        raise ValueError(f"MoM release asset {name} has invalid digest, size or URL.")
    if digest[7:].lower() != ASSET_SHA256.get(name):
        raise ValueError(f"MoM release asset {name} differs from this APWorld's pinned SHA-256.")
    return {"sha256": digest[7:].lower(), "size": size, "url": expected_url}


def _unpack(data: bytes, release: dict, source: dict, loader_version: str) -> tuple[dict[str, bytes], dict]:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        entries = archive.infolist()
        # Exact names disallow traversal, symlinks, unrelated binaries and case
        # collisions without extracting any archive entry onto the filesystem.
        if (len(entries) != len(RUNTIME_FILES) or {item.filename for item in entries} != set(RUNTIME_FILES)
                or any(item.is_dir() or (item.external_attr >> 16) & 0o170000 == 0o120000 for item in entries)
                or sum(item.file_size for item in entries) > MAX_RUNTIME_SIZE):
            raise ValueError("MoM runtime archive has unexpected or unsafe files.")
        payload = {item.filename: archive.read(item) for item in entries}
    metadata = json.loads(payload["manifest.json"])
    if (not isinstance(metadata, dict) or metadata.get("schema_version") != 1
            or f"v{metadata.get('version')}" != release["tag_name"]
            or metadata.get("loader_version") != loader_version):
        raise ValueError("MoM release manifest is incompatible with this client/loader.")
    expected_files = set(RUNTIME_FILES) - {"manifest.json"}
    if not isinstance(metadata.get("files"), dict) or set(metadata["files"]) != expected_files:
        raise ValueError("MoM runtime manifest has an incomplete file inventory.")
    for name in expected_files:
        if not payload[name] or _sha(payload[name]) != metadata["files"][name]:
            raise ValueError(f"MoM runtime file failed SHA-256 verification: {name}")
    if not payload["MoMController.dll"].startswith(b"MZ") or _sha(payload["MoMController.dll"]) != metadata.get("sha256"):
        raise ValueError("MoM controller DLL failed verification.")
    if metadata.get("source") != {"url": source["url"], "sha256": source["sha256"]}:
        raise ValueError("MoM source asset does not match the runtime manifest.")
    pointer = payload["SOURCE.txt"].decode("utf-8")
    if source["url"] not in pointer or source["sha256"] not in pointer:
        raise ValueError("MoM runtime is missing its matching source download directions.")
    files = {"Mods/MoMController.dll": payload.pop("MoMController.dll")}
    files.update({f"{NOTICE_DIR}/{name}": content for name, content in payload.items()})
    return files, {**metadata, "release_tag": release["tag_name"]}


def download_mod(progress=lambda message: None, *, cache: Path | None = None,
                 loader_version: str = "0.7.3") -> tuple[dict[str, bytes], dict]:
    if cache is None:
        import Utils
        cache = Path(Utils.user_path("kh3", "cache", "mom"))
    cache.mkdir(parents=True, exist_ok=True)
    progress(f"Checking MoM mod {RELEASE_TAG}.")
    try:
        release = json.loads(_read_url(RELEASE_URL, 1024 * 1024))
        if (not isinstance(release, dict) or release.get("draft") or release.get("prerelease")
                or release.get("tag_name") != RELEASE_TAG):
            raise ValueError("MoM release is not this APWorld's pinned stable version.")
        runtime = _asset(release, RUNTIME_ASSET, MAX_RUNTIME_SIZE)
        source = _asset(release, SOURCE_ASSET, 64 * 1024 * 1024)
        archive = cache / f"MoM-{runtime['sha256']}.zip"
        data = archive.read_bytes() if archive.is_file() and archive.stat().st_size == runtime["size"] else b""
        cached = bool(data) and _sha(data) == runtime["sha256"]
        if not cached:
            progress(f"Downloading MoM mod {release['tag_name']} and license files…")
            data = _read_url(runtime["url"], runtime["size"])
        if len(data) != runtime["size"] or _sha(data) != runtime["sha256"]:
            raise ValueError("MoM runtime download failed size/SHA-256 verification.")
        payload = _unpack(data, release, source, loader_version)
        if not cached:
            with tempfile.TemporaryDirectory(prefix=".mom-", dir=cache) as temporary:
                staged = Path(temporary) / "runtime.zip"
                staged.write_bytes(data)
                staged.replace(archive)
        return payload
    except (OSError, ValueError, TypeError, KeyError, zipfile.BadZipFile) as exc:
        raise RuntimeError(f"Could not fetch/verify the MoM release; no game files changed. {exc}") from exc
