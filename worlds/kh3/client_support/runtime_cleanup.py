"""Planning and execution for KH3 runtime uninstall."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..installer_support.paths import (
    legacy_mods_txt_path,
    mods_txt_path,
    ue4ss_path_specific_config_dir,
)
from ..installer_support.ue4ss_config import (
    UE4SS_MANAGED_FILES,
)
from ..platform_support.game_discovery import (
    kh3_mods_dir_from_root,
    kh3_win64_dir_from_root,
    normalize_kh3_root_dir,
)
from .goa_download import KH3_GOA_MOD_PATTERN
from .seed_paks import KH3_AP_SEED_PAK_PATTERN


@dataclass(frozen=True, slots=True)
class RuntimeUninstallPlan:
    """Every file, directory, and mods.txt entry owned by KH3 AP."""

    kh3_root: Path
    win64_dir: Path
    mods_dir: Path
    pak_files: tuple[Path, ...]
    runtime_files: tuple[Path, ...]
    runtime_dirs: tuple[Path, ...]
    mods_txt_files: tuple[Path, ...]
    empty_dirs: tuple[Path, ...]


def _remove_file_if_exists(path: Path, errors: list[str]) -> bool:
    if not path.exists() or not path.is_file():
        return False
    try:
        path.unlink()
        return True
    except OSError as exc:
        errors.append(f"{path}: {exc}")
        return False


# Player-owned entries of a runtime directory that an uninstall keeps: the
# custom music a player gathered in the probe's custom_music folder.
PRESERVED_RUNTIME_ENTRIES = frozenset({"custom_music"})


def _remove_dir_tree_if_exists(path: Path, errors: list[str], preserved: list[Path]) -> bool:
    if not path.exists() or not path.is_dir():
        return False
    try:
        kept = [child for child in path.iterdir() if child.name.lower() in PRESERVED_RUNTIME_ENTRIES]
        if not kept:
            shutil.rmtree(path)
            return True
        for child in path.iterdir():
            if child in kept:
                continue
            if child.is_dir() and not child.is_symlink():
                shutil.rmtree(child)
            else:
                child.unlink()
        preserved.extend(kept)
        return True
    except OSError as exc:
        errors.append(f"{path}: {exc}")
        return False


def _remove_empty_dirs(paths: list[Path]) -> int:
    removed = 0
    for path in sorted({path for path in paths if path.exists()}, key=lambda item: len(item.parts), reverse=True):
        if not path.is_dir():
            continue
        try:
            path.rmdir()
            removed += 1
        except OSError:
            pass
    return removed


def _remove_mod_entry(mods_txt: Path, mod_name: str, errors: list[str]) -> bool:
    if not mods_txt.is_file():
        return False
    try:
        lines = mods_txt.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        errors.append(f"{mods_txt}: {exc}")
        return False
    mod_prefix = f"{mod_name.casefold()} "
    kept_lines = [
        line for line in lines if not (line.strip().casefold().startswith(mod_prefix) and ":" in line.strip())
    ]
    if kept_lines == lines:
        return False
    try:
        if any(line.strip() for line in kept_lines):
            mods_txt.write_text("\n".join(kept_lines).rstrip() + "\n", encoding="utf-8")
        else:
            mods_txt.unlink()
        return True
    except OSError as exc:
        errors.append(f"{mods_txt}: {exc}")
        return False


def _runtime_uninstall_paths(kh3_root: Path, win64: Path) -> tuple[list[Path], list[Path], Path | None]:
    files = [win64 / relative_path for relative_path in UE4SS_MANAGED_FILES]
    dirs = [
        win64 / "UE4SS_Signatures",
        win64 / "CustomGameConfigs" / "Kingdom Hearts 3",
        win64 / "Mods" / "KH3APProbe",
        win64 / "Mods" / "KH3APProbeCpp",
        win64 / "KINGDOM HEARTS III" / "Mods" / "KH3APProbeCpp",
    ]
    files.extend(
        win64 / "KINGDOM HEARTS III" / name
        for name in ("MemberVariableLayout.ini", "UE4SS-settings.ini", "VTableLayout.ini")
    )
    path_specific = ue4ss_path_specific_config_dir(kh3_root)
    if path_specific is not None:
        files.extend(
            path_specific / name for name in ("MemberVariableLayout.ini", "UE4SS-settings.ini", "VTableLayout.ini")
        )
        dirs.extend(
            (
                path_specific / "UE4SS_Signatures",
                path_specific / "Mods" / "KH3APProbeCpp",
            )
        )
    return files, dirs, path_specific


def plan_uninstall_kh3_ap_files(kh3_dir: Path, kh3_mods_dir: Path | None = None) -> RuntimeUninstallPlan:
    """Calculate all uninstall targets without deleting or rewriting anything."""

    kh3_root = normalize_kh3_root_dir(kh3_dir)
    win64 = kh3_win64_dir_from_root(kh3_root)
    mods_dir = (kh3_mods_dir or kh3_mods_dir_from_root(kh3_root)).expanduser().resolve()
    pak_files: tuple[Path, ...] = ()
    if mods_dir.is_dir():
        pak_files = tuple(
            path
            for path in sorted(mods_dir.glob("*.pak"))
            if KH3_AP_SEED_PAK_PATTERN.search(path.name) is not None or path.match(KH3_GOA_MOD_PATTERN)
        )
    files, dirs, path_specific = _runtime_uninstall_paths(kh3_root, win64)
    empty_dirs = [
        win64 / "Mods",
        win64 / "KINGDOM HEARTS III" / "Mods",
        win64 / "KINGDOM HEARTS III",
        win64 / "CustomGameConfigs",
    ]
    mods_txt_files = [mods_txt_path(kh3_root), legacy_mods_txt_path(kh3_root)]
    if path_specific is not None:
        mods_txt_files.append(path_specific / "Mods" / "mods.txt")
        empty_dirs.extend((path_specific / "Mods", path_specific))
    return RuntimeUninstallPlan(
        kh3_root,
        win64,
        mods_dir,
        pak_files,
        tuple(files),
        tuple(dirs),
        tuple(mods_txt_files),
        tuple(empty_dirs),
    )


def uninstall_kh3_ap_files(kh3_dir: Path, kh3_mods_dir: Path | None = None) -> dict[str, Any]:
    plan = plan_uninstall_kh3_ap_files(kh3_dir, kh3_mods_dir)
    errors: list[str] = []
    preserved: list[Path] = []
    removed_files = 0
    removed_dirs = 0
    removed_paks = 0
    updated_mods_txt = 0
    for pak_path in plan.pak_files:
        if _remove_file_if_exists(pak_path, errors):
            removed_files += 1
            removed_paks += 1
    if plan.win64_dir.is_dir():
        for path in plan.runtime_files:
            if _remove_file_if_exists(path, errors):
                removed_files += 1
        for path in plan.runtime_dirs:
            if _remove_dir_tree_if_exists(path, errors, preserved):
                removed_dirs += 1
        for path in plan.mods_txt_files:
            if _remove_mod_entry(path, "KH3APProbeCpp", errors):
                updated_mods_txt += 1
        removed_dirs += _remove_empty_dirs(list(plan.empty_dirs))
    return {
        "status": "ok" if not errors else "partial",
        "kh3_dir": str(plan.kh3_root),
        "mods_dir": str(plan.mods_dir),
        "removed_files": removed_files,
        "removed_dirs": removed_dirs,
        "removed_paks": removed_paks,
        "updated_mods_txt": updated_mods_txt,
        "preserved": [str(path) for path in preserved],
        "errors": errors,
    }


__all__ = [
    "RuntimeUninstallPlan",
    "plan_uninstall_kh3_ap_files",
    "uninstall_kh3_ap_files",
]
