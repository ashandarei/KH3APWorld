"""Prepare launch assets and the optional Olympus reward companion."""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .olympus_coliseum_compat import (
    OlympusColiseumCompatResult,
    ensure_olympus_coliseum_compat,
    reconcile_olympus_coliseum_compat,
)

logger = logging.getLogger("Client")


def ensure_modloader_priority(mods_dir: Path, *, game_running: Callable[[], bool]) -> bool:
    from ..platform_support.modloader import fix_modloader_pak_priority

    try:
        messages = fix_modloader_pak_priority(mods_dir, game_running=game_running())
    except (OSError, RuntimeError) as exc:
        logger.error("KH3 Modloader check failed: %s", exc)
        return False
    for message in messages:
        logger.info(message)
    return True


def prepare_launch_files(kh3_dir: Path) -> bool:
    from .custom_music import sync_runtime
    from .fast_new_game import read_settings
    from .startup_video import install_short_copyright_video
    from .static_chest_tables import validate_static_chest_tables

    try:
        validate_static_chest_tables(kh3_dir)
    except (OSError, ValueError, RuntimeError) as exc:
        logger.error("Could not validate Garden chest tables: %s", exc)
        return False

    try:
        sync_runtime(kh3_dir)
    except OSError as exc:
        logger.warning("Could not configure the custom music folder; continuing KH3 launch: %s", exc)
    try:
        from ..kh02.installation import sync_runtime as sync_kh02_runtime
        sync_kh02_runtime(kh3_dir)
    except (OSError, ValueError) as exc:
        logger.error("Could not configure the KH0.2 source: %s", exc)
        return False
    try:
        install_short_copyright_video(kh3_dir, enabled=read_settings()["enabled"])
    except OSError as exc:
        logger.error("Could not install the short copyright video: %s", exc)
        return False
    return True


def reconcile_reward_companion(
    mods_dir: Path | None,
    slot_data: dict[str, Any] | None,
    *,
    game_running: Callable[[], bool],
    log_missing: bool = True,
) -> OlympusColiseumCompatResult | None:
    if mods_dir is None:
        return None
    try:
        result = reconcile_olympus_coliseum_compat(mods_dir, slot_data, game_running=game_running())
    except OSError as exc:
        logger.warning("Could not update the Olympus Coliseum reward companion in %s: %s", mods_dir, exc)
        return None
    if result.status == "restart_required" or (result.status in {"missing", "outdated"} and log_missing):
        logger.warning(result.message)
    elif result.changed:
        logger.info(result.message)
    return result


def ensure_reward_companion(
    mods_dir: Path,
    slot_data: dict[str, Any],
    *,
    game_running: Callable[[], bool],
    set_status: Callable[[str], None],
) -> OlympusColiseumCompatResult | None:
    def build_companion(source_path: Path, output_path: Path, **reward_modes: bool) -> Path:
        set_status("Building the Olympus Coliseum AP reward companion...")
        from ..PakGenerator import build_olympus_coliseum_compat_pak

        return build_olympus_coliseum_compat_pak(source_path, output_path, **reward_modes)

    try:
        result = ensure_olympus_coliseum_compat(
            mods_dir,
            slot_data,
            game_running=game_running(),
            build_companion=build_companion,
        )
    except OSError as exc:
        logger.warning("Could not prepare the Olympus Coliseum reward companion in %s: %s", mods_dir, exc)
        return None
    if not result.ready:
        logger.warning(result.message)
    elif result.changed:
        logger.info(result.message)
    return result
