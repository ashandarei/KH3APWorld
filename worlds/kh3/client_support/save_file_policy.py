"""Allowlisted KH3 save paths and containment checks shared by client actions.

Names come from the game's slot layout, GoA save slots and the supported
Coliseum mod. A new save producer must be added here explicitly. Unknown files,
settings and directories never become gameplay saves merely by their suffix.
"""

from __future__ import annotations

import os
import re
import shutil
import stat
from dataclasses import dataclass
from pathlib import Path


class SaveFileError(OSError):
    """A save operation would leave its scope or follow a redirected path."""


_NATIVE_NAME = re.compile(r"khiii_(?:slot[0-9]+|system)\.(?:bin|sav)", re.IGNORECASE)
_SIDECAR_NAMES = frozenset(name.casefold() for name in (
    "ChoicesMade.sav", "PlayerStart.sav", "WorldSaveDone.sav",
    "RandomizerChests.sav", "RandomizerEvents.sav", "RandomizerLevelUp.sav",
    "RandomizerRetry.sav", "RandomizerVBonus.sav",
    "SavePointMIDiscoveredTankYard.sav", "SavePointMIDiscoveredServiceArea.sav",
))
_WORLD_SIDECAR = re.compile(r"(?:FlagData|SavePoint)[a-z0-9]{2,4}\.sav", re.IGNORECASE)
_COLISEUM_SIDECAR = re.compile(r"ColiseumMod_Slot[0-9]+\.sav", re.IGNORECASE)
_SAVE_DIRECTORIES = frozenset({(), ("kh3sv2",), ("kh3sv2", "data")})


def is_gameplay_save(relative: Path) -> bool:
    """Match a relative filename *and* its expected directory, on either store."""
    parts = tuple(part.casefold() for part in relative.parts)
    if relative.is_absolute() or not parts:
        return False
    if parts[:-1] in {(), ("kh3sv2", "data")} and _NATIVE_NAME.fullmatch(parts[-1]):
        return True
    return len(parts) == 1 and bool(
        parts[0] in _SIDECAR_NAMES or _WORLD_SIDECAR.fullmatch(parts[0])
        or _COLISEUM_SIDECAR.fullmatch(parts[0])
    )


def _redirected(info: os.stat_result) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
    )


def checked_path(root: Path, path: Path) -> Path:
    """Keep lexical paths until links/reparse points have been rejected.

    Check ancestors too: resolving first would conceal a redirected source root or
    backup destination. Missing destination components are checked again after mkdir.
    """
    root = Path(os.path.abspath(root.expanduser()))
    path = Path(os.path.abspath(path.expanduser()))
    if root == Path(root.anchor):
        raise SaveFileError("A drive or filesystem root cannot be a save directory.")
    if not path.is_relative_to(root):
        raise SaveFileError(f"Save path is outside its directory: {path}")
    for candidate in (*reversed(path.parents), path):
        try:
            info = candidate.lstat()
        except FileNotFoundError:
            continue
        if _redirected(info):
            raise SaveFileError(f"Refusing a linked or redirected save path: {candidate}")
        if candidate != path and not stat.S_ISDIR(info.st_mode):
            raise SaveFileError(f"Save path parent is not a directory: {candidate}")
    if not path.resolve().is_relative_to(root.resolve()):
        raise SaveFileError(f"Save path resolves outside its directory: {path}")
    return path


def checked_root(root: Path) -> Path:
    root = checked_path(root, root)
    if root.exists() and not root.is_dir():
        raise SaveFileError(f"Not a save directory: {root}")
    return root


def checked_save(root: Path, path: Path) -> Path:
    root = checked_root(root)
    path = checked_path(root, path)
    if not is_gameplay_save(path.relative_to(root)):
        raise SaveFileError(f"Not an allowlisted KH3 save path: {path}")
    if path.exists() and not path.is_file():
        raise SaveFileError(f"Save payload is not a regular file: {path}")
    return path


@dataclass(frozen=True)
class SaveInventory:
    files: tuple[Path, ...]
    kept: tuple[Path, ...]
    directories: tuple[Path, ...]


def inspect_save_tree(root: Path) -> SaveInventory:
    """Inspect only the root and known native subdirectories; never follow links."""
    root = checked_root(root)
    files, kept, directories = [], [], []

    def visit(directory: Path) -> None:
        checked_path(root, directory)
        if not directory.exists():
            return
        for path in sorted(directory.iterdir()):
            relative = path.relative_to(root)
            managed_directory = tuple(part.casefold() for part in relative.parts) in _SAVE_DIRECTORIES
            if is_gameplay_save(relative):
                files.append(checked_save(root, path))
            elif managed_directory:
                checked_path(root, path)
                if not path.is_dir():
                    raise SaveFileError(f"Expected a KH3 save directory: {path}")
                visit(path)
                directories.append(path)
            else:
                kept.append(path)

    visit(root)
    return SaveInventory(tuple(files), tuple(kept), tuple(directories))


def check_save_transfer(source_root: Path, destination_root: Path, source: Path, destination: Path) -> None:
    source = checked_save(source_root, source)
    destination = checked_save(destination_root, destination)
    if source.relative_to(checked_root(source_root)) != destination.relative_to(checked_root(destination_root)):
        raise SaveFileError("Save transfer must preserve its relative path.")
    if not source.is_file():
        raise SaveFileError(f"Save source is missing: {source}")
    if destination.exists():
        raise SaveFileError(f"Save destination already exists: {destination}")


def transfer_save(source_root: Path, destination_root: Path, source: Path, destination: Path, *, move: bool) -> None:
    check_save_transfer(source_root, destination_root, source, destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    check_save_transfer(source_root, destination_root, source, destination)
    if move:
        shutil.move(str(source), str(destination))
    else:
        shutil.copy2(source, destination)


def remove_empty_save_directories(root: Path) -> int:
    """Only prune the known native layout; preserve unrecognized directories."""
    removed = 0
    for path in inspect_save_tree(root).directories:
        checked_path(root, path)
        try:
            path.rmdir()
            removed += 1
        except OSError:
            pass  # An unknown file keeps its directory alive.
    return removed
