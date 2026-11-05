"""Seed-aware activation for the Olympus Coliseum reward compatibility pak."""

from __future__ import annotations

import hashlib
import json
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from ..Data import (
    OLYMPUS_COLISEUM_ACTIVE_POOL_VALUES,
    RANDOMIZE_LOCATION_POOL_VALUE,
    REPLACE_LOCATION_POOL_VALUE,
    VANILLA_LOCATION_POOL_VALUE,
)

OLYMPUS_COLISEUM_POOL_OPTION = "olympus_coliseum_pool"
OLYMPUS_COLISEUM_VANILLA_MODE = VANILLA_LOCATION_POOL_VALUE
OLYMPUS_COLISEUM_RANDOMIZE_MODE = RANDOMIZE_LOCATION_POOL_VALUE
OLYMPUS_COLISEUM_JUNK_MODE = REPLACE_LOCATION_POOL_VALUE
OLYMPUS_COLISEUM_AP_MODES = OLYMPUS_COLISEUM_ACTIVE_POOL_VALUES

OLYMPUS_COLISEUM_COMPAT_PAK_NAME = "zzzz_APOlympusColiseumCompat_999_P.pak"
OLYMPUS_COLISEUM_COMPAT_DISABLED_SUFFIX = ".disabled-by-kh3ap"
OLYMPUS_COLISEUM_SOURCE_PAK_NAME = "zzzy_ColiseumMod_998_P.pak"
OLYMPUS_COLISEUM_SOURCE_PAK_SHA256 = (
    "9CFB5F5A9A12896690512FC7DAB073938A8E0E2828561A4EC72CE764DAED73E0"
)
OLYMPUS_COLISEUM_SUPPORTED_SOURCE_SHA256S = frozenset({
    OLYMPUS_COLISEUM_SOURCE_PAK_SHA256,
    "A32E44BA76FFB6E085AB93ED972694C0B93889313F2AD23A2313133D55001EC9",
})
OLYMPUS_COLISEUM_MINIMUM_COMPAT_PAK_SIZE_BYTES = 1024

OlympusColiseumCompatBuilder = Callable[..., Path]
OLYMPUS_COLISEUM_COMPAT_VERSION = 2


@dataclass(frozen=True)
class OlympusColiseumCompatResult:
    status: str
    message: str
    option_mode: int | None
    changed: bool = False
    ready: bool = True
    path: Path | None = None


def _normalize_option_mode(slot_data: Mapping[str, Any] | None) -> int | None:
    if not isinstance(slot_data, Mapping):
        return None
    raw_value = slot_data.get(OLYMPUS_COLISEUM_POOL_OPTION)
    if isinstance(raw_value, str):
        named_modes = {
            "vanilla": OLYMPUS_COLISEUM_VANILLA_MODE,
            "randomize": OLYMPUS_COLISEUM_RANDOMIZE_MODE,
            "randomized": OLYMPUS_COLISEUM_RANDOMIZE_MODE,
            "junk": OLYMPUS_COLISEUM_JUNK_MODE,
            "replace": OLYMPUS_COLISEUM_JUNK_MODE,
        }
        normalized_name = raw_value.strip().casefold()
        if normalized_name in named_modes:
            return named_modes[normalized_name]
    try:
        option_mode = int(raw_value)
    except (TypeError, ValueError):
        return None
    if option_mode not in {
        OLYMPUS_COLISEUM_VANILLA_MODE,
        *OLYMPUS_COLISEUM_AP_MODES,
    }:
        return None
    return option_mode


def _disabled_candidates(mods_dir: Path) -> list[Path]:
    prefix = (
        OLYMPUS_COLISEUM_COMPAT_PAK_NAME
        + OLYMPUS_COLISEUM_COMPAT_DISABLED_SUFFIX
    ).casefold()
    if not mods_dir.is_dir():
        return []
    return sorted(
        (
            path
            for path in mods_dir.iterdir()
            if path.is_file() and path.name.casefold().startswith(prefix)
        ),
        key=lambda path: (path.stat().st_mtime_ns, path.name.casefold()),
        reverse=True,
    )


def _next_disabled_path(mods_dir: Path) -> Path:
    base_path = mods_dir / (
        OLYMPUS_COLISEUM_COMPAT_PAK_NAME
        + OLYMPUS_COLISEUM_COMPAT_DISABLED_SUFFIX
    )
    if not base_path.exists():
        return base_path
    suffix_number = 2
    while True:
        candidate = Path(f"{base_path}.{suffix_number}")
        if not candidate.exists():
            return candidate
        suffix_number += 1


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _receipt_path(mods_dir: Path) -> Path:
    return mods_dir / (OLYMPUS_COLISEUM_COMPAT_PAK_NAME + ".json")


def _companion_is_current(path: Path, *, cups: bool, chests: bool) -> bool:
    receipt_path = _receipt_path(path.parent)
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        return (receipt.get("version") == OLYMPUS_COLISEUM_COMPAT_VERSION
                and receipt.get("suppress_cup_rewards") == cups
                and receipt.get("suppress_chest_rewards") == chests
                and receipt.get("pak_sha256") == _file_sha256(path))
    except (OSError, ValueError, AttributeError):
        return False


def reconcile_olympus_coliseum_compat(
    mods_dir: Path,
    slot_data: Mapping[str, Any] | None,
    *,
    game_running: bool,
) -> OlympusColiseumCompatResult:
    """Match the AP reward overlay to the active seed without touching the full mod."""

    mods_dir = Path(mods_dir).expanduser().resolve()
    option_mode = _normalize_option_mode(slot_data)
    # Also runs without the GUI, before source lookup and companion generation.
    from .compatible_mods import COMPATIBLE_MODS, CompatibleModsError, CompatibleModsService

    try:
        CompatibleModsService(mods_dir, game_running=lambda: game_running).normalize_installed(COMPATIBLE_MODS[0])
    except (OSError, CompatibleModsError) as exc:
        return OlympusColiseumCompatResult(
            status="filename_conflict", message=f"Could not prepare Olympus Coliseum filenames: {exc}",
            option_mode=option_mode, ready=False,
        )
    if option_mode is None:
        return OlympusColiseumCompatResult(
            status="unmanaged",
            message="No supported Olympus Coliseum pool mode is available yet; left the reward companion unchanged.",
            option_mode=None,
        )

    active_path = mods_dir / OLYMPUS_COLISEUM_COMPAT_PAK_NAME
    disabled_paths = _disabled_candidates(mods_dir)
    ap_rewards_enabled = option_mode in OLYMPUS_COLISEUM_AP_MODES

    chest_rewards_suppressed = True
    companion_required = ap_rewards_enabled or (
        chest_rewards_suppressed and (mods_dir / OLYMPUS_COLISEUM_SOURCE_PAK_NAME).is_file()
    )

    if companion_required:
        candidate = active_path if active_path.is_file() else next(iter(disabled_paths), None)
        if candidate is not None and not _companion_is_current(
                candidate, cups=ap_rewards_enabled, chests=chest_rewards_suppressed):
            return OlympusColiseumCompatResult(
                status="restart_required" if game_running else "outdated",
                message="The Olympus Coliseum companion needs rebuilding for this seed's chest and cup rewards. Close KH3 before rebuilding.",
                option_mode=option_mode, ready=False, path=candidate,
            )
        if active_path.is_file():
            return OlympusColiseumCompatResult(
                status="active",
                message="Olympus Coliseum AP reward companion is active for this seed.",
                option_mode=option_mode,
                path=active_path,
            )
        if not disabled_paths:
            return OlympusColiseumCompatResult(
                status="missing",
                message=(
                    "This seed needs the Olympus Coliseum compatibility overlay, but "
                    f"{OLYMPUS_COLISEUM_COMPAT_PAK_NAME} is not installed."
                ),
                option_mode=option_mode,
                ready=False,
                path=active_path,
            )
        if game_running:
            return OlympusColiseumCompatResult(
                status="restart_required",
                message=(
                    "KH3 is running, so the Olympus Coliseum AP reward companion could not be enabled. "
                    "Close KH3 and launch it again from the AP client."
                ),
                option_mode=option_mode,
                ready=False,
                path=disabled_paths[0],
            )
        disabled_paths[0].replace(active_path)
        return OlympusColiseumCompatResult(
            status="enabled",
            message="Enabled the Olympus Coliseum AP reward companion for this seed.",
            option_mode=option_mode,
            changed=True,
            path=active_path,
        )

    if not active_path.is_file():
        return OlympusColiseumCompatResult(
            status="inactive",
            message="Olympus Coliseum native reward bundles are active for this vanilla-mode seed.",
            option_mode=option_mode,
            path=disabled_paths[0] if disabled_paths else None,
        )
    if game_running:
        return OlympusColiseumCompatResult(
            status="restart_required",
            message=(
                "KH3 is running, so the Olympus Coliseum AP reward companion could not be disabled. "
                "Close KH3 and launch it again from the AP client to restore native cup rewards."
            ),
            option_mode=option_mode,
            ready=False,
            path=active_path,
        )

    disabled_path = _next_disabled_path(mods_dir)
    active_path.replace(disabled_path)
    return OlympusColiseumCompatResult(
        status="disabled",
        message="Disabled the Olympus Coliseum AP reward companion; native cup rewards are active.",
        option_mode=option_mode,
        changed=True,
        path=disabled_path,
    )


def ensure_olympus_coliseum_compat(
    mods_dir: Path,
    slot_data: Mapping[str, Any] | None,
    *,
    game_running: bool,
    build_companion: OlympusColiseumCompatBuilder,
) -> OlympusColiseumCompatResult:
    """Activate an existing companion or build it once from the verified full mod."""

    result = reconcile_olympus_coliseum_compat(
        mods_dir,
        slot_data,
        game_running=game_running,
    )
    if result.status not in {"missing", "outdated"}:
        return result

    mods_dir = Path(mods_dir).expanduser().resolve()
    option_mode = result.option_mode
    output_path = mods_dir / OLYMPUS_COLISEUM_COMPAT_PAK_NAME
    if game_running:
        return OlympusColiseumCompatResult(
            status="restart_required",
            message=(
                "KH3 is running, so the missing Olympus Coliseum AP reward companion could not be built. "
                "Close KH3 and reconnect or launch it again from the AP client."
            ),
            option_mode=option_mode,
            ready=False,
            path=output_path,
        )

    source_path = mods_dir / OLYMPUS_COLISEUM_SOURCE_PAK_NAME
    if not source_path.is_file():
        return OlympusColiseumCompatResult(
            status="source_missing",
            message=(
                "This seed uses Archipelago Olympus Coliseum rewards, but the verified full mod was not found at "
                f"{source_path}. Install Olympus Coliseum 1.4.2 or 1.4.2.1 under the supported priority filename first."
            ),
            option_mode=option_mode,
            ready=False,
            path=source_path,
        )

    try:
        source_sha256 = _file_sha256(source_path)
    except OSError as exc:
        return OlympusColiseumCompatResult(
            status="source_unreadable",
            message=f"Could not verify the installed Olympus Coliseum mod at {source_path}: {exc}",
            option_mode=option_mode,
            ready=False,
            path=source_path,
        )
    if source_sha256 not in OLYMPUS_COLISEUM_SUPPORTED_SOURCE_SHA256S:
        return OlympusColiseumCompatResult(
            status="unsupported_source",
            message=(
                "The installed Olympus Coliseum mod does not match a supported 1.4.2/1.4.2.1 build; "
                f"expected SHA-256 {', '.join(sorted(OLYMPUS_COLISEUM_SUPPORTED_SOURCE_SHA256S))}, "
                f"found {source_sha256}. "
                "The AP client left it unchanged."
            ),
            option_mode=option_mode,
            ready=False,
            path=source_path,
        )

    suppress_cup_rewards = option_mode in OLYMPUS_COLISEUM_AP_MODES
    suppress_chest_rewards = True
    try:
        # Build and verify away from the live file, retaining it if generation fails.
        with tempfile.TemporaryDirectory(prefix="kh3ap-coliseum-install-", dir=mods_dir) as work:
            staged_path = Path(work) / OLYMPUS_COLISEUM_COMPAT_PAK_NAME
            built_path = Path(build_companion(
                source_path, staged_path,
                suppress_cup_rewards=suppress_cup_rewards,
            )).expanduser().resolve()
            if built_path != staged_path or staged_path.stat().st_size < OLYMPUS_COLISEUM_MINIMUM_COMPAT_PAK_SIZE_BYTES:
                raise ValueError("The companion build returned an unexpected or incomplete file.")
            receipt = {
                "version": OLYMPUS_COLISEUM_COMPAT_VERSION,
                "suppress_cup_rewards": suppress_cup_rewards,
                "suppress_chest_rewards": suppress_chest_rewards,
                "source_sha256": source_sha256,
                "pak_sha256": _file_sha256(staged_path),
            }
            staged_receipt = Path(work) / "receipt.json"
            staged_receipt.write_text(json.dumps(receipt, indent=2), encoding="utf-8")
            if output_path.is_file():
                import shutil
                stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
                shutil.copy2(output_path, output_path.with_name(output_path.name + f".pre-update-{stamp}.bak"))
            staged_path.replace(output_path)
            staged_receipt.replace(_receipt_path(mods_dir))
            built_path = output_path
    except Exception as exc:
        return OlympusColiseumCompatResult(
            status="build_failed",
            message=f"Could not build the Olympus Coliseum AP reward companion: {exc}",
            option_mode=option_mode,
            ready=False,
            path=output_path,
        )
    try:
        output_size = output_path.stat().st_size
    except OSError as exc:
        return OlympusColiseumCompatResult(
            status="build_failed",
            message=f"The Olympus Coliseum AP reward companion build did not install correctly: {exc}",
            option_mode=option_mode,
            ready=False,
            path=output_path,
        )
    if built_path != output_path or output_size < OLYMPUS_COLISEUM_MINIMUM_COMPAT_PAK_SIZE_BYTES:
        return OlympusColiseumCompatResult(
            status="build_failed",
            message=(
                "The Olympus Coliseum AP reward companion build returned an unexpected or incomplete file. "
                "KH3 was not launched with that companion."
            ),
            option_mode=option_mode,
            ready=False,
            path=output_path,
        )

    return OlympusColiseumCompatResult(
        status="built",
        message=(
            f"Built and installed {OLYMPUS_COLISEUM_COMPAT_PAK_NAME} from the verified "
            "Olympus Coliseum mod."
        ),
        option_mode=option_mode,
        changed=True,
        path=output_path,
    )


__all__ = [
    "OLYMPUS_COLISEUM_AP_MODES",
    "OLYMPUS_COLISEUM_COMPAT_DISABLED_SUFFIX",
    "OLYMPUS_COLISEUM_COMPAT_PAK_NAME",
    "OLYMPUS_COLISEUM_JUNK_MODE",
    "OLYMPUS_COLISEUM_MINIMUM_COMPAT_PAK_SIZE_BYTES",
    "OLYMPUS_COLISEUM_POOL_OPTION",
    "OLYMPUS_COLISEUM_RANDOMIZE_MODE",
    "OLYMPUS_COLISEUM_SOURCE_PAK_NAME",
    "OLYMPUS_COLISEUM_SOURCE_PAK_SHA256",
    "OLYMPUS_COLISEUM_SUPPORTED_SOURCE_SHA256S",
    "OLYMPUS_COLISEUM_VANILLA_MODE",
    "OlympusColiseumCompatBuilder",
    "OlympusColiseumCompatResult",
    "ensure_olympus_coliseum_compat",
    "reconcile_olympus_coliseum_compat",
]
