"""Explicit filesystem operations for installing the KH3 client runtime."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from ..installer_support.installation import (
    install_kh3_runtime,
)
from ..installer_support.paths import (
    wine_visible_path_text,
)
from .game_discovery import kh3_win64_dir_from_root

logger = logging.getLogger("Client")


def write_probe_config(config_path: Path, bridge_dir: Path) -> None:
    """Write the probe's runtime-loot output configuration."""

    bridge_loot_path = wine_visible_path_text(bridge_dir / "runtime_loot_event.json")
    content = (
        "return {\n"
        "    event_output_paths = {\n"
        "        [[Mods\\KH3APProbeCpp\\runtime_loot_event.json]],\n"
        f"        [[{bridge_loot_path}]],\n"
        "    },\n"
        "}\n"
    )
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(content, encoding="utf-8")


def install_kh3_runtime_files(kh3_dir: Path, bridge_dir: Path | None) -> dict[str, Any]:
    """Install runtime releases and packaged configuration, preserving the result contract."""

    try:
        result = install_kh3_runtime(kh3_dir, bridge_dir)
    except Exception as exc:
        logger.warning("KH3 runtime install failed: %s", exc, exc_info=True)
        return {
            "status": "error",
            "errors": [str(exc)],
        }
    errors = result.get("errors") if isinstance(result.get("errors"), list) else []
    if result.get("status") == "missing_kh3":
        logger.warning("%s", errors[0] if errors else "KH3 install directory was not found.")
        return result

    if result.get("status") == "error":
        logger.warning("KH3 runtime install failed: %s", "; ".join(str(error) for error in errors))
        return result
    if errors:
        logger.warning("KH3 runtime install completed partially: %s", "; ".join(str(error) for error in errors))
    else:
        logger.info("KH3 runtime installed/updated under: %s", kh3_win64_dir_from_root(kh3_dir))
    if result.get("existing_ue4ss_custom_conflict"):
        logger.warning(
            "Existing UE4SS files differed from KH3 AP's required runtime release. They were backed up before replacement."
        )
    if result.get("replaced_existing_ue4ss"):
        logger.info(
            "Backed up %s existing UE4SS/proxy files before installing the KH3 AP runtime.",
            len(result.get("ue4ss", {}).get("backups", []) if isinstance(result.get("ue4ss"), dict) else []),
        )

    probe = result.get("probe") if isinstance(result.get("probe"), dict) else {}
    legacy_mods_txt = result.get("legacy_mods_txt") if isinstance(result.get("legacy_mods_txt"), dict) else {}
    ue4ss = result.get("ue4ss") if isinstance(result.get("ue4ss"), dict) else {}
    logger.info(
        "KH3 runtime install details: UE4SS copied=%s skipped=%s; probe copied=%s skipped=%s; legacy_probe_mod disabled_changed=%s.",
        len(ue4ss.get("copied", [])) if isinstance(ue4ss.get("copied"), list) else 0,
        len(ue4ss.get("skipped", [])) if isinstance(ue4ss.get("skipped"), list) else 0,
        bool(probe.get("copied")),
        bool(probe.get("skipped")),
        bool(legacy_mods_txt.get("changed")),
    )
    return result


__all__ = ["install_kh3_runtime_files", "write_probe_config"]
