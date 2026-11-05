"""Player-local music folder preference, shared by every seed."""
from __future__ import annotations

from pathlib import Path
from tempfile import NamedTemporaryFile

import Utils

from ..installer_support import (
    paths as installer_paths,
)
from ..platform_support.game_discovery import normalize_kh3_root_dir

SETTINGS_FILE = "custom_music_directory.txt"
PREFERENCE = "custom_music_dir"


def read_directory() -> str:
    value = Utils.persistent_load().get("kh3_client", {}).get(PREFERENCE, "")
    return value if isinstance(value, str) and Path(value).is_absolute() else ""


def sync_runtime(kh3_dir: Path, directory: str | None = None) -> None:
    """Publish UTF-8 paths to each applicable DLL layout, even without a seed."""
    root = normalize_kh3_root_dir(kh3_dir)
    if not installer_paths.kh3_win64_dir(root).is_dir():
        return
    directory = read_directory() if directory is None else directory
    text = installer_paths.wine_visible_path_text(Path(directory)) if directory else ""
    folders = [installer_paths.probe_config_path(root).parent]
    legacy = installer_paths.legacy_probe_dll_path(root).parent.parent
    if legacy.is_dir():
        folders.append(legacy)
    specific = installer_paths.path_specific_probe_config_path(root)
    if specific is not None:
        folders.append(specific.parent)
    for folder in dict.fromkeys(folders):
        target = folder / SETTINGS_FILE
        try:
            if target.read_text(encoding="utf-8") == text:
                continue
        except (FileNotFoundError, UnicodeError):
            pass
        folder.mkdir(parents=True, exist_ok=True)
        # Publish whole paths atomically: the game can read this while running.
        temporary = None
        try:
            with NamedTemporaryFile(dir=folder, prefix=SETTINGS_FILE + ".", suffix=".tmp", delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(text.encode("utf-8"))
            temporary.replace(target)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)


def save_directory(value: str, kh3_dir: Path) -> str:
    value = value.strip()
    if value:
        folder = Path(value).expanduser()
        if not folder.is_absolute() or any(ord(ch) < 32 for ch in value):
            raise ValueError("Choose an absolute path for the custom music folder.")
        if not folder.is_dir():
            raise ValueError("The custom music folder does not exist. Create it first, then select it.")
        value = str(folder.resolve())
    sync_runtime(kh3_dir, value)
    Utils.persistent_store("kh3_client", PREFERENCE, value)
    return value
