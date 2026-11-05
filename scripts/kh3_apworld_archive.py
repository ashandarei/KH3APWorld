"""KH3 archive construction, independent of website or hosting code."""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path


def file_digest(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def write_apworld(output_path: Path, world_directory: Path, files: list[str],
                  archive_root: str, manifest: dict, cache_dir: Path | None = None) -> bool:
    """Cache the actual archive inputs, after shared ignore/manifest logic runs.

    Content, member names, permissions and the generated manifest determine the
    key. Checkout mtimes deliberately do not: they change on every CI runner.
    Returns True for a verified cache hit. Cache loss/corruption is a cold build.
    The caller must serialize writers (the Docker cache mount uses sharing=locked).
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    files = sorted(files)
    manifest_json = json.dumps(manifest)
    key_inputs = {
        "builder": file_digest(Path(__file__)),
        "python": list(sys.version_info[:2]),
        "root": archive_root,
        "manifest": manifest_json,
        "files": [(name, (world_directory / name).stat().st_mode,
                   file_digest(world_directory / name)) for name in files],
    }
    key = hashlib.sha256(json.dumps(key_inputs, sort_keys=True).encode()).hexdigest()
    cached = cache_dir / output_path.name if cache_dir else None
    record = cache_dir / (output_path.name + ".json") if cache_dir else None
    if cached is not None and record is not None:
        try:
            metadata = json.loads(record.read_text(encoding="utf-8"))
            if (isinstance(metadata, dict) and metadata.get("key") == key
                    and metadata.get("sha256") == file_digest(cached)):
                shutil.copy2(cached, output_path)
                return True
        except (OSError, ValueError):
            pass

    # Never publish a partially written archive, even if compression fails.
    with tempfile.NamedTemporaryFile(dir=output_path.parent, delete=False) as temporary:
        temp_path = Path(temporary.name)
    try:
        with zipfile.ZipFile(temp_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
            for name in files:
                zf.write(world_directory / name, Path(archive_root, name))
            zf.writestr(f"{archive_root}/archipelago.json", manifest_json)
        temp_path.chmod(0o644)
        temp_path.replace(output_path)
    finally:
        temp_path.unlink(missing_ok=True)

    if cached is not None and record is not None:
        cache_dir.mkdir(parents=True, exist_ok=True)
        temp_cache = cached.with_name(cached.name + ".tmp")
        shutil.copy2(output_path, temp_cache)
        temp_cache.replace(cached)
        temp_record = record.with_name(record.name + ".tmp")
        temp_record.write_text(json.dumps({"key": key, "sha256": file_digest(cached)}), encoding="utf-8")
        temp_record.replace(record)
    return False


def build_world_apworld(world_directory: Path, game: str, output_path: Path,
                        cache_dir: Path | None = None, variant: str | None = None) -> bool:
    from Utils import local_path, read_apignore
    from worlds.Files import APWorldContainer

    archive_root = world_directory.name
    manifest_path = world_directory / "archipelago.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else {}
    if manifest_path.is_file() and manifest.get("game") != game:
        raise RuntimeError(f"{manifest_path} game field {manifest.get('game')!r} does not match {game!r}.")

    apworld = APWorldContainer(str(output_path))
    apworld.game = game
    manifest.update(apworld.get_manifest())

    global_ignores = read_apignore(local_path("data", "GLOBAL.apignore"))
    if not global_ignores:
        raise RuntimeError("Could not read global apignore file for APWorld build")
    local_ignores = read_apignore(world_directory / ".apignore")
    ignores = global_ignores + local_ignores if local_ignores else global_ignores
    if variant is not None:
        if variant != "host" or game != "Kingdom Hearts III" or archive_root != "kh3":
            raise RuntimeError(f"Unsupported APWorld build variant: {variant!r} for {game}")
        host_ignores = read_apignore(world_directory / ".aphostignore")
        if not host_ignores:
            raise RuntimeError("Missing KH3 host packaging rules (.aphostignore)")
        ignores += host_ignores
        manifest["package_variant"] = "host"
        manifest["release_name"] = "KH3 host"
        manifest["description"] = "KH3 generation and hosting support; players need the full APWorld."

    files = [str(file) for file in ignores.match_tree_files(str(world_directory), negate=True)]
    return write_apworld(output_path, world_directory, files, archive_root, manifest, cache_dir)
