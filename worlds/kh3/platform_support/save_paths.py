"""Resolve KH3 save directories for the client and bridge."""

from __future__ import annotations

import os
import re
from pathlib import Path

CHOICES_MADE_SAVE_NAME = "ChoicesMade.sav"
STEAM_SHARED_SAVE_ACCOUNT_ID = "00000000000000000000000000000000"
STEAM_ACCOUNT_ID_RE = re.compile(r"^\d{17}$")
RANDOMIZER_ACTIVITY_SAVE_NAMES = (
    CHOICES_MADE_SAVE_NAME,
    "RandomizerRetry.sav",
    "PlayerStart.sav",
    "WorldSaveDone.sav",
)
RANDOMIZER_ACTIVITY_SAVE_PATTERNS = (
    "SavePoint*.sav",
    "FlagData*.sav",
)


def _safe_stat(path: Path) -> os.stat_result | None:
    try:
        return path.stat()
    except OSError:
        return None


def _safe_mtime_ns(path: Path) -> int:
    stat = _safe_stat(path)
    return stat.st_mtime_ns if stat is not None else -1


def _savegames_activity_mtime_and_rank(save_dir: Path) -> tuple[int, int]:
    best_mtime_ns = -1
    best_rank = 0

    def add_activity(path: Path, rank: int) -> None:
        nonlocal best_mtime_ns, best_rank
        if path.is_file():
            mtime_ns = _safe_mtime_ns(path)
            if mtime_ns < 0:
                return
            if mtime_ns > best_mtime_ns or (mtime_ns == best_mtime_ns and rank > best_rank):
                best_mtime_ns = mtime_ns
                best_rank = rank

    for name in RANDOMIZER_ACTIVITY_SAVE_NAMES:
        add_activity(save_dir / name, 2)
    for pattern in RANDOMIZER_ACTIVITY_SAVE_PATTERNS:
        try:
            matches = list(save_dir.glob(pattern))
        except OSError:
            continue
        for path in matches:
            add_activity(path, 2)
    return best_mtime_ns, best_rank


def _savegames_activity_key(save_dir: Path) -> tuple[int, int, int, int, str]:
    activity_mtime_ns, activity_rank = _savegames_activity_mtime_and_rank(save_dir)
    return (
        1 if activity_mtime_ns >= 0 else 0,
        activity_mtime_ns,
        activity_rank,
        _safe_mtime_ns(save_dir),
        save_dir.parent.name.casefold(),
    )


def choose_active_savegames_dir(save_root: Path) -> Path:
    candidates: list[Path] = []
    if save_root.is_dir():
        candidates = [
            path / "SaveGames"
            for path in save_root.iterdir()
            if path.is_dir() and (path / "SaveGames").is_dir()
        ]
    if candidates:
        return max(candidates, key=_savegames_activity_key).resolve()
    return (save_root / "SaveGames").resolve()


def steam_shared_savegames_dir(steam_root: Path) -> Path:
    """Return the invariant shared SaveGames directory for the Steam build."""

    return (steam_root / STEAM_SHARED_SAVE_ACCOUNT_ID / "SaveGames").resolve()


def steam_save_root_from_path(save_dir: Path | str) -> Path | None:
    """Return the KH3 Steam save root represented by a recognized save path."""

    path = Path(save_dir).expanduser()
    if path.name.casefold() == "steam" and any(
        part.casefold() == "kingdom hearts iii" for part in path.parts
    ):
        return path
    if path.parent.name.casefold() == "steam" and (
        STEAM_ACCOUNT_ID_RE.fullmatch(path.name) or path.name == STEAM_SHARED_SAVE_ACCOUNT_ID
    ):
        return path.parent
    if path.name.casefold() == "savegames":
        account_root = path.parent
        if account_root.parent.name.casefold() == "steam" and (
            STEAM_ACCOUNT_ID_RE.fullmatch(account_root.name)
            or account_root.name == STEAM_SHARED_SAVE_ACCOUNT_ID
        ):
            return account_root.parent
    return None


def normalize_save_directory(save_dir: Path | str) -> Path:
    """Use the sidecar SaveGames root and normalize recognized Steam accounts."""

    path = Path(save_dir).expanduser()
    if path.name.casefold() == "kh3sv2" and path.parent.name.casefold() == "savegames":
        path = path.parent
    steam_root = steam_save_root_from_path(path)
    target = (
        steam_root / STEAM_SHARED_SAVE_ACCOUNT_ID / "SaveGames"
        if steam_root is not None
        else path
    )
    try:
        return target.resolve()
    except OSError:
        return target.absolute()


def default_save_directory() -> Path:
    configured = os.environ.get("KH3_SAVE_DIR")
    if configured:
        return normalize_save_directory(configured)

    user_profile = Path(os.environ.get("USERPROFILE", str(Path.home())))
    steam_root = user_profile / "Documents" / "My Games" / "KINGDOM HEARTS III" / "Steam"
    return steam_shared_savegames_dir(steam_root)
