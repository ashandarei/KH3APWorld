"""Read-only KH3 save-directory and Steam account discovery."""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import Utils

from ..client_support.settings import KH3ClientSettings, load_kh3_client_settings
from .game_discovery import (
    KH3_STEAM_APP_ID,
    _steam_root_for_executable,
    is_epic_kh3_root_dir,
    normalize_kh3_root_dir,
    resolve_kh3_executable_path,
)
from .save_paths import (
    STEAM_ACCOUNT_ID_RE,
    STEAM_SHARED_SAVE_ACCOUNT_ID,
    choose_active_savegames_dir,
    default_save_directory,
    normalize_save_directory,
    steam_save_root_from_path,
    steam_shared_savegames_dir,
)


def _parse_steam_loginusers(text: str) -> list[tuple[str, dict[str, str]]]:
    users: list[tuple[str, dict[str, str]]] = []
    current_id: str | None = None
    current_fields: dict[str, str] = {}

    def flush_current() -> None:
        nonlocal current_id, current_fields
        if current_id is not None:
            users.append((current_id, current_fields))
        current_id = None
        current_fields = {}

    for line in text.splitlines():
        match = re.match(r'\s*"([^"]+)"(?:\s+"([^"]*)")?', line)
        if match is None:
            continue
        key = match.group(1)
        value = match.group(2)
        if value is None:
            if STEAM_ACCOUNT_ID_RE.fullmatch(key):
                flush_current()
                current_id = key
            continue
        if current_id is not None:
            current_fields[key] = value
    flush_current()
    return users


def _steam_loginusers_timestamp(fields: dict[str, str]) -> int:
    try:
        return int(fields.get("Timestamp", "0") or "0")
    except ValueError:
        return 0


def _active_steam_account_id_from_loginusers(loginusers_path: Path) -> str | None:
    try:
        users = _parse_steam_loginusers(loginusers_path.read_text(encoding="utf-8-sig", errors="replace"))
    except OSError:
        return None
    if not users:
        return None
    return max(
        users,
        key=lambda entry: (
            1 if entry[1].get("MostRecent") == "1" else 0,
            _steam_loginusers_timestamp(entry[1]),
            entry[0],
        ),
    )[0]


def active_steam_account_id_for_kh3_root(
    kh3_root: Path,
    settings: KH3ClientSettings | None = None,
) -> str | None:
    """Discover the configured or most recently active Steam account."""

    settings = settings or load_kh3_client_settings()
    configured = settings.kh3_steam_account_id
    if configured and STEAM_ACCOUNT_ID_RE.fullmatch(configured.strip()):
        return configured.strip()
    executable_path = resolve_kh3_executable_path(kh3_root)
    steam_root = _steam_root_for_executable(executable_path, settings)
    if steam_root is None:
        return None
    return _active_steam_account_id_from_loginusers(steam_root / "config" / "loginusers.vdf")


def _candidate_documents_dirs() -> list[Path]:
    home_value = os.environ.get("USERPROFILE")
    try:
        home = Path(home_value).expanduser() if home_value else Path.home()
    except RuntimeError:
        home = Path(".")
    candidates = []
    if os.name == "nt":
        # Windows redirects Documents (often to OneDrive); use its configured
        # location before fallbacks rather than picking a stale local folder.
        try:
            import ctypes

            documents = ctypes.create_unicode_buffer(32768)
            if ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, documents) == 0 and documents.value:
                candidates.append(Path(documents.value))
        except (AttributeError, OSError):
            pass
    candidates.extend([home / "Documents", home / "OneDrive" / "Documents"])
    try:
        candidates.append(Path.home() / "Documents")
    except RuntimeError:
        pass
    unique: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate).casefold()
        if key not in seen:
            seen.add(key)
            unique.append(candidate)
    return unique


def _choose_active_savegames_dir(save_root: Path) -> Path:
    return choose_active_savegames_dir(save_root)


def _steam_save_root_candidates() -> list[Path]:
    return [documents / "My Games" / "KINGDOM HEARTS III" / "Steam" for documents in _candidate_documents_dirs()]


def _steamapps_dir_from_kh3_root(kh3_root: Path) -> Path | None:
    try:
        kh3_root = kh3_root.expanduser().resolve()
    except OSError:
        kh3_root = kh3_root.expanduser()
    for parent in kh3_root.parents:
        if parent.name.casefold() == "steamapps":
            return parent
    return None


def _steam_save_root_candidates_for_kh3_root(kh3_root: Path) -> list[Path]:
    candidates: list[Path] = []
    steamapps_dir = _steamapps_dir_from_kh3_root(kh3_root)
    if sys.platform.startswith("linux") and steamapps_dir is not None:
        candidates.append(
            steamapps_dir
            / "compatdata"
            / KH3_STEAM_APP_ID
            / "pfx"
            / "drive_c"
            / "users"
            / "steamuser"
            / "My Documents"
            / "My Games"
            / "KINGDOM HEARTS III"
            / "Steam"
        )
    candidates.extend(_steam_save_root_candidates())
    return candidates


def _epic_save_root_candidates() -> list[Path]:
    return [documents / "KINGDOM HEARTS III" / "Epic Games Store" for documents in _candidate_documents_dirs()]


def _first_existing_save_root(candidates: list[Path]) -> Path:
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    return candidates[0]


def _path_is_under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (OSError, ValueError):
        return False


def is_kh3_auto_save_dir(save_dir: Path | str) -> bool:
    """Return whether a SaveGames path is under an auto-discovered KH3 root."""

    path = Path(save_dir).expanduser()
    if path.name.casefold() != "savegames":
        return False
    return any(_path_is_under(path, root) for root in (*_steam_save_root_candidates(), *_epic_save_root_candidates()))


def _path_has_direct_savegames_child(path: Path) -> bool:
    return (path / "SaveGames").is_dir()


def _path_has_savegames_children(path: Path) -> bool:
    if not path.is_dir():
        return False
    try:
        children = list(path.iterdir())
    except OSError:
        return False
    return any(child.is_dir() and (child / "SaveGames").is_dir() for child in children)


def _looks_like_kh3_account_save_root(path: Path) -> bool:
    name = path.name.casefold()
    if STEAM_ACCOUNT_ID_RE.fullmatch(path.name) or name == STEAM_SHARED_SAVE_ACCOUNT_ID:
        return True
    return len(path.name) >= 16 and path.parent.name.casefold() in {"steam", "epic games store"}


def _looks_like_kh3_account_parent_save_root(path: Path) -> bool:
    name = path.name.casefold()
    if name in {"steam", "epic games store"}:
        return any(part.casefold() == "kingdom hearts iii" for part in path.parts)
    return _path_has_savegames_children(path)


def is_kh3_save_dir_dynamic_root(save_dir: Path | str) -> bool:
    """Return whether a supplied root requires account SaveGames selection."""

    path = Path(save_dir).expanduser()
    if path.name.casefold() == "savegames":
        return False
    return (
        _path_has_direct_savegames_child(path)
        or _looks_like_kh3_account_save_root(path)
        or _looks_like_kh3_account_parent_save_root(path)
    )


def resolve_kh3_save_dir_input(save_dir: Path | str) -> Path:
    """Resolve exact, account, or account-parent input to a SaveGames path."""

    path = Path(save_dir).expanduser()
    steam_save_root = steam_save_root_from_path(path)
    if steam_save_root is not None:
        return normalize_save_directory(path)
    if path.name.casefold() == "savegames":
        return path.resolve()
    if _path_has_direct_savegames_child(path) or _looks_like_kh3_account_save_root(path):
        return (path / "SaveGames").resolve()
    if _looks_like_kh3_account_parent_save_root(path):
        return _choose_active_savegames_dir(path)
    return normalize_save_directory(path)


def resolve_kh3_save_dir_for_root(
    kh3_dir: Path,
    settings: KH3ClientSettings | None = None,
) -> Path:
    """Resolve the active Steam or Epic save directory for a KH3 install."""

    settings = settings or load_kh3_client_settings()
    kh3_root = normalize_kh3_root_dir(kh3_dir)
    if is_epic_kh3_root_dir(kh3_root, settings):
        return _choose_active_savegames_dir(_first_existing_save_root(_epic_save_root_candidates()))

    steam_save_root = _first_existing_save_root(_steam_save_root_candidates_for_kh3_root(kh3_root))
    return steam_shared_savegames_dir(steam_save_root)


def resolve_default_kh3_save_dir(
    kh3_dir: Path | None = None,
    settings: KH3ClientSettings | None = None,
) -> Path:
    """Resolve environment, persisted, install-derived, or platform save default."""

    settings = settings or load_kh3_client_settings()
    if settings.kh3_save_dir is not None:
        return resolve_kh3_save_dir_input(settings.kh3_save_dir)

    persisted = Utils.persistent_load().get("kh3_client", {}).get("save_dir")
    if persisted and not is_kh3_auto_save_dir(persisted):
        return resolve_kh3_save_dir_input(persisted)
    if kh3_dir is not None:
        return resolve_kh3_save_dir_for_root(kh3_dir, settings)
    return default_save_directory()


__all__ = [
    "STEAM_ACCOUNT_ID_RE",
    "active_steam_account_id_for_kh3_root",
    "is_kh3_auto_save_dir",
    "is_kh3_save_dir_dynamic_root",
    "resolve_default_kh3_save_dir",
    "resolve_kh3_save_dir_for_root",
    "resolve_kh3_save_dir_input",
]
