from __future__ import annotations

import zipfile
from pathlib import Path


def apworld_archive_context(module_file: str | Path | None) -> tuple[Path, str] | None:
    raw_file = str(module_file or "")
    marker = ".apworld"
    marker_index = raw_file.lower().find(marker)
    if marker_index < 0:
        return None

    archive_path = Path(raw_file[:marker_index + len(marker)])
    inner_path = raw_file[marker_index + len(marker):].lstrip("\\/").replace("\\", "/")
    package_root = inner_path.split("/", 1)[0]
    if not archive_path.is_file() or not package_root:
        return None
    return archive_path, package_root


def read_package_text(relative_path: str, *, module_file: str | Path | None, encoding: str = "utf-8") -> str:
    normalized_path = relative_path.strip("/").replace("\\", "/")
    context = apworld_archive_context(module_file)
    if context is not None:
        archive_path, package_root = context
        inner_path = f"{package_root}/{normalized_path}"
        try:
            with zipfile.ZipFile(archive_path) as archive:
                return archive.read(inner_path).decode(encoding)
        except KeyError as exc:
            raise FileNotFoundError(f"Missing KH3 package resource in {archive_path}: {inner_path}") from exc

    package_dir = Path(__file__).resolve().parent
    resource_path = package_dir.joinpath(*normalized_path.split("/"))
    if not resource_path.is_file():
        raise FileNotFoundError(f"Missing KH3 package resource: {resource_path}")
    return resource_path.read_text(encoding=encoding)
