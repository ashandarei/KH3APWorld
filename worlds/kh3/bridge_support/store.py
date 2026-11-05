"""Focused persistence boundary for KH3 bridge sidecar files."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .files import (
    append_json_line,
    atomic_write_json,
    file_signature,
    load_json_file,
    load_optional_json_file,
    load_optional_json_view,
    safe_unlink,
)

FileSignature = tuple[str, int, int] | tuple[str, None, None]


class BridgeStore:
    """Read and mutate bridge sidecars underneath one constrained root."""

    def __init__(
        self,
        root: Path,
        *,
        protocol_name: str = "kh3_ap_file_bridge",
        protocol_version: int = 1,
    ) -> None:
        self.root = root.expanduser().resolve()
        self.protocol_name = protocol_name
        self.protocol_version = protocol_version
        self._path_cache: dict[str, Path] = {}

    def path(self, file_name: str | Path) -> Path:
        """Resolve a relative sidecar name while preventing root escapes."""
        relative_path = Path(file_name)
        if relative_path.is_absolute():
            raise ValueError(f"Bridge sidecar path must be relative: {file_name}")
        cache_key = relative_path.as_posix()
        cached_path = self._path_cache.get(cache_key)
        if cached_path is not None:
            return cached_path
        path = (self.root / relative_path).resolve()
        try:
            path.relative_to(self.root)
        except ValueError as exc:
            raise ValueError(f"Bridge sidecar path escapes store root: {file_name}") from exc
        self._path_cache[cache_key] = path
        return path

    def read_json(self, file_name: str | Path) -> dict[str, Any]:
        return load_json_file(self.path(file_name))

    def read_optional_json(self, file_name: str | Path) -> dict[str, Any] | None:
        return load_optional_json_file(self.path(file_name))

    def read_optional_json_view(self, file_name: str | Path) -> dict[str, Any] | None:
        return load_optional_json_view(self.path(file_name))

    def write_json(self, file_name: str | Path, payload: dict[str, Any] | list[Any]) -> None:
        atomic_write_json(self.path(file_name), payload)

    def append_jsonl(self, file_name: str | Path, payload: dict[str, Any]) -> None:
        append_json_line(self.path(file_name), payload)

    def protocol_metadata(self) -> dict[str, Any]:
        return {
            "protocol_name": self.protocol_name,
            "protocol_version": self.protocol_version,
        }

    def signature(self, file_name: str | Path) -> FileSignature:
        name = Path(file_name).as_posix()
        modified_ns, size = file_signature(self.path(file_name))
        return (name, modified_ns, size)

    def delete(self, file_name: str | Path) -> bool:
        return safe_unlink(self.path(file_name))
