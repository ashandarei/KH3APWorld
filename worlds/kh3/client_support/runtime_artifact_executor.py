"""Execute runtime-artifact plans and invalidate inspection after file changes."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..kh02.host_runtime import provision_host
from ..platform_support.game_discovery import kh3_win64_dir_from_root
from ..platform_support.runtime_install import install_kh3_runtime_files, write_probe_config
from .runtime_artifact_service import (
    ProbeConfigurationInput,
    RuntimeArtifactAction,
    RuntimeArtifactActionKind,
    RuntimeArtifactPlan,
    RuntimeArtifactService,
)
from .runtime_cleanup import uninstall_kh3_ap_files

logger = logging.getLogger("Client")


@dataclass(frozen=True)
class RuntimeArtifactCallbacks:
    root: Callable[[], Path]
    mods: Callable[[], Path]
    bridge: Callable[[], Path | None]
    saves: Callable[[], Path]
    invalidate_runtime: Callable[[], None]
    invalidate_goa: Callable[[], None]
    restore_vanilla: Callable[[], dict[str, Any]]
    disconnect_after_restore: Callable[[], None]
    backup_vanilla: Callable[[], None]
    ensure_goa: Callable[[], None]
    configure_probe: Callable[[], None]


class RuntimeArtifactExecutor:
    def __init__(self, planner: RuntimeArtifactService, callbacks: RuntimeArtifactCallbacks) -> None:
        self._planner = planner
        self._client = callbacks

    def apply_plan(self, plan: RuntimeArtifactPlan) -> None:
        for action in plan.actions:
            self.apply(action)

    def configure_probe(self, config_path: Path | None) -> None:
        from .custom_music import sync_runtime
        from .fast_new_game import sync_bridge

        root = self._client.root()
        try:
            sync_runtime(root)
        except OSError as exc:
            logger.warning("Could not update the custom music folder: %s", exc)
        bridge = self._client.bridge()
        sync_bridge(bridge)
        self.apply_plan(
            self._planner.plan_probe_configuration(
                ProbeConfigurationInput(
                    bridge_dir=bridge,
                    kh3_root=root,
                    win64_exists=kh3_win64_dir_from_root(root).is_dir() if bridge is not None else False,
                    config_path=config_path,
                )
            )
        )

    def apply(self, action: RuntimeArtifactAction) -> None:
        kind = action.kind
        if kind in {RuntimeArtifactActionKind.LOG_INFO, RuntimeArtifactActionKind.LOG_WARNING}:
            log = logger.info if kind is RuntimeArtifactActionKind.LOG_INFO else logger.warning
            log(action.text, *action.log_args)
        elif kind is RuntimeArtifactActionKind.WRITE_PROBE_CONFIG:
            self._write_probe(action)
        elif kind is RuntimeArtifactActionKind.UNINSTALL_RUNTIME_ARTIFACTS:
            self._uninstall()
        elif kind is RuntimeArtifactActionKind.RESTORE_VANILLA_SAVES:
            result = self._client.restore_vanilla()
            self.apply_plan(self._planner.plan_restore_report(self._planner.normalize_restore_result(result)))
        elif kind is RuntimeArtifactActionKind.INSTALL_RUNTIME_FILES:
            self._install()
        else:
            callback = {
                RuntimeArtifactActionKind.DISCONNECT_AFTER_RESTORE: self._client.disconnect_after_restore,
                RuntimeArtifactActionKind.BACKUP_VANILLA_SAVES: self._client.backup_vanilla,
                RuntimeArtifactActionKind.ENSURE_GOA_MOD: self._client.ensure_goa,
            }.get(kind)
            if callback is not None:
                callback()

    def _write_probe(self, action: RuntimeArtifactAction) -> None:
        try:
            write_probe_config(action.path, action.bridge_dir)
            self._client.invalidate_runtime()
            from ..installer_support.paths import probe_config_path, path_specific_probe_config_path
            root = self._client.root()
            if action.path == probe_config_path(root):
                active = path_specific_probe_config_path(root)
                if active is not None and active != action.path:
                    write_probe_config(active, action.bridge_dir)
            logger.info("Updated KH3APProbeCpp config for bridge directory: %s", action.bridge_dir)
        except OSError as exc:
            logger.warning("Could not update KH3APProbeCpp config at %s: %s", action.path, exc)

    def _uninstall(self) -> None:
        try:
            result = uninstall_kh3_ap_files(self._client.root(), self._client.mods())
        finally:
            self._client.invalidate_runtime()
            self._client.invalidate_goa()
        self.apply_plan(self._planner.plan_uninstall_report(self._planner.normalize_uninstall_result(result)))

    def _install(self) -> None:
        try:
            result = install_kh3_runtime_files(self._client.root(), self._client.bridge())
            if result.get("status") not in {"missing_kh3", "error"}:
                provision_host(self._client.root())
                self._install_uobject_config()
        finally:
            self._client.invalidate_runtime()
        if result.get("status") not in {"missing_kh3", "error"}:
            self._client.configure_probe()

    def _install_uobject_config(self) -> None:
        from ..platform_support.uobject_config import install_uobject_config

        try:
            pool_config = install_uobject_config(self._client.saves())
            logger.info(
                "KH3 UObject capacity configured to %s in %s. "
                "Applies on next game start; use Read UObjects in KH3 Debug to verify.",
                f"{pool_config['configured_capacity']:,}",
                pool_config["path"],
            )
            if pool_config.get("backup"):
                logger.info("Previous Engine.ini preserved at %s", pool_config["backup"])
        except (OSError, ValueError) as exc:
            logger.warning("KH3 UObject capacity override was not installed: %s", exc)
