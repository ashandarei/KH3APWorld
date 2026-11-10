"""KH3 runtime resources operations."""

from __future__ import annotations

import hashlib
import re
import zipfile
from importlib import resources
from pathlib import Path
from typing import Any

RUNTIME_PACKAGE = __package__.rpartition(".")[0] or "worlds.kh3"


RUNTIME_BUNDLE_DIR = Path(__file__).parents[1] / "runtime"


RUNTIME_WIN64_DIR = RUNTIME_BUNDLE_DIR / "Win64"


RUNTIME_WIN64_RELATIVE = ("runtime", "Win64")


PROBE_DLL_VERSION_PATTERN = re.compile(r"\b\d+\.\d+\.\d+\b")


PROBE_DLL_BUILD_STAMP_PATTERN = re.compile(r"\b20\d\d-\d\d-\d\dT\d\d:\d\d [ -~]{3,96}")


class _ZipResourceStat:
    def __init__(self, size: int) -> None:
        self.st_size = size


_ZipIndex = tuple[tuple[int, int], dict[str, tuple[int, bool]], tuple[str, ...]]


_ZIP_RESOURCE_INDEX_CACHE: dict[Path, _ZipIndex] = {}


def _zip_archive_signature(archive_path: Path) -> tuple[int, int] | None:
    try:
        stat = archive_path.stat()
    except OSError:
        return None
    return stat.st_mtime_ns, stat.st_size


def _zip_resource_index(archive_path: Path) -> tuple[dict[str, tuple[int, bool]], tuple[str, ...]]:
    signature = _zip_archive_signature(archive_path)
    if signature is None:
        return {}, ()

    cached = _ZIP_RESOURCE_INDEX_CACHE.get(archive_path)
    if cached is not None and cached[0] == signature:
        return cached[1], cached[2]

    try:
        with zipfile.ZipFile(archive_path) as archive:
            infos = {
                info.filename: (info.file_size, info.is_dir())
                for info in archive.infolist()
                if isinstance(info.filename, str)
            }
    except Exception:
        if cached is not None:
            return cached[1], cached[2]
        return {}, ()

    names = tuple(infos)
    _ZIP_RESOURCE_INDEX_CACHE[archive_path] = (signature, infos, names)
    return infos, names


class _ZipRuntimeResource:
    def __init__(self, archive_path: Path, inner_path: str) -> None:
        self.archive_path = archive_path
        self.inner_path = inner_path.strip("/")
        self.name = self.inner_path.rsplit("/", 1)[-1] if self.inner_path else archive_path.name

    def __str__(self) -> str:
        return f"{self.archive_path}!/{self.inner_path}"

    def joinpath(self, *parts: str):
        clean_parts = [str(part).strip("/") for part in parts if str(part).strip("/")]
        if not clean_parts:
            return self
        inner_path = "/".join([part for part in (self.inner_path, *clean_parts) if part])
        return _ZipRuntimeResource(self.archive_path, inner_path)

    def _get_entry(self) -> tuple[int, bool] | None:
        infos, _ = _zip_resource_index(self.archive_path)
        return infos.get(self.inner_path)

    def is_file(self) -> bool:
        entry = self._get_entry()
        return entry is not None and not entry[1]

    def is_dir(self) -> bool:
        prefix = f"{self.inner_path}/" if self.inner_path else ""
        _, names = _zip_resource_index(self.archive_path)
        return any(name.startswith(prefix) and name != prefix for name in names)

    def stat(self) -> _ZipResourceStat:
        entry = self._get_entry()
        if entry is None:
            raise FileNotFoundError(str(self))
        return _ZipResourceStat(entry[0])

    def read_bytes(self) -> bytes:
        try:
            with zipfile.ZipFile(self.archive_path) as archive:
                return archive.read(self.inner_path)
        except Exception as exc:
            raise FileNotFoundError(str(self)) from exc

    def iterdir(self):
        prefix = f"{self.inner_path}/" if self.inner_path else ""
        children: set[str] = set()
        _, names = _zip_resource_index(self.archive_path)
        for name in names:
            if not name.startswith(prefix) or name == prefix:
                continue
            child = name[len(prefix) :].split("/", 1)[0]
            if child:
                children.add(child)
        for child in sorted(children):
            yield self.joinpath(child)


class _MissingRuntimeResource:
    def __init__(self, path: str) -> None:
        self.path = path
        self.name = path.rsplit("/", 1)[-1] if path else ""

    def __str__(self) -> str:
        return self.path

    def joinpath(self, *parts: str):
        clean_parts = [str(part).strip("/") for part in parts if str(part).strip("/")]
        suffix = "/".join(clean_parts)
        path = "/".join(part for part in (self.path, suffix) if part)
        return _MissingRuntimeResource(path)

    def is_file(self) -> bool:
        return False

    def is_dir(self) -> bool:
        return False

    def stat(self) -> _ZipResourceStat:
        raise FileNotFoundError(self.path)

    def read_bytes(self) -> bytes:
        raise FileNotFoundError(self.path)

    def iterdir(self):
        return iter(())


def _apworld_archive_context(module_file: str | Path | None = None) -> tuple[Path, str] | None:
    raw_file = str(Path(module_file or __file__))
    marker = ".apworld"
    marker_index = raw_file.lower().find(marker)
    if marker_index < 0:
        return None

    archive_path = Path(raw_file[: marker_index + len(marker)])
    inner_path = raw_file[marker_index + len(marker) :].lstrip("\\/").replace("\\", "/")
    package_root = inner_path.split("/", 1)[0]
    if not archive_path.is_file() or not package_root:
        return None
    return archive_path, package_root


def _runtime_resource(*parts: str):
    context = _apworld_archive_context()
    if context is not None:
        archive_path, package_root = context
        return _ZipRuntimeResource(archive_path, package_root).joinpath(*parts)

    try:
        resource = resources.files(RUNTIME_PACKAGE)
        for part in parts:
            resource = resource.joinpath(part)
        return resource
    except Exception:
        return _MissingRuntimeResource("/".join((RUNTIME_PACKAGE, *parts)))


def _runtime_win64_source():
    return RUNTIME_WIN64_DIR if RUNTIME_WIN64_DIR.exists() else _runtime_resource(*RUNTIME_WIN64_RELATIVE)


def _runtime_probe_source():
    from .probe_release import CONFIG_PATH, PinnedProbeResource
    return PinnedProbeResource(_runtime_resource(*CONFIG_PATH))


def _resource_is_file(resource) -> bool:
    try:
        return resource.is_file()
    except Exception:
        return False


def _resource_is_dir(resource) -> bool:
    try:
        return resource.is_dir()
    except Exception:
        return False


def _resource_bytes_equal(source, target: Path) -> bool:
    if not target.is_file() or not _resource_is_file(source):
        return False
    try:
        return target.read_bytes() == source.read_bytes()
    except OSError:
        return False


def _resource_size(resource) -> int | None:
    try:
        return resource.stat().st_size
    except Exception:
        return None


def _resource_metadata_equal(source, target: Path) -> bool:
    if not target.is_file() or not _resource_is_file(source):
        return False
    source_size = _resource_size(source)
    if source_size is None:
        return False
    try:
        return target.stat().st_size == source_size
    except OSError:
        return False


def _decode_probe_dll_text(data: bytes) -> str:
    try:
        return data.decode("utf-16le", errors="ignore")
    except Exception:
        return ""


def _find_probe_dll_version(text: str) -> str | None:
    for match in PROBE_DLL_VERSION_PATTERN.finditer(text):
        start = max(0, match.start() - 80)
        end = min(len(text), match.end() + 80)
        context = text[start:end]
        if "KH3APProbeCpp" in context or "ModVersion" in context:
            return match.group(0)
    return None


def _find_probe_dll_build_stamp(text: str) -> str | None:
    matches = [match.group(0).rstrip("\x00").strip() for match in PROBE_DLL_BUILD_STAMP_PATTERN.finditer(text)]
    return max(matches, key=len) if matches else None


def probe_dll_version_info(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {
            "installed": False,
            "path": str(path),
        }

    try:
        data = path.read_bytes()
    except OSError as exc:
        return {
            "installed": True,
            "path": str(path),
            "error": str(exc),
        }

    text = _decode_probe_dll_text(data)
    sha256 = hashlib.sha256(data).hexdigest().upper()
    return {
        "installed": True,
        "path": str(path),
        "size": len(data),
        "sha256": sha256,
        "short_hash": sha256[:8],
        "version": _find_probe_dll_version(text),
        "build_stamp": _find_probe_dll_build_stamp(text),
    }


def _iter_resource_files(source_dir, relative_parts: tuple[str, ...] = ()):
    for child in sorted(source_dir.iterdir(), key=lambda resource: resource.name):
        child_relative_parts = (*relative_parts, child.name)
        if _resource_is_dir(child):
            yield from _iter_resource_files(child, child_relative_parts)
        elif _resource_is_file(child):
            yield child_relative_parts, child
