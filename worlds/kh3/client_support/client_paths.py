"""Resolve launch path overrides without changing client or save state."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..platform_support.game_discovery import (
    kh3_mods_dir_from_root,
    kh3_probe_config_from_root,
    kh3_ue4ss_log_from_root,
    normalize_kh3_root_dir,
)
from ..platform_support.save_discovery import (
    is_kh3_auto_save_dir,
    is_kh3_save_dir_dynamic_root,
    resolve_default_kh3_save_dir,
    resolve_kh3_save_dir_input,
)
from .runtime_artifact_service import ProbeConfigPathInput, RuntimeArtifactService
from .settings import resolve_default_kh3_root_dir


@dataclass(frozen=True)
class ClientPaths:
    kh3_dir: Path
    save_dir: Path
    ue4ss_log: Path | None
    probe_config: Path | None
    mods_dir: Path
    save_dir_override: bool
    dynamic_save_root_override: bool
    ue4ss_log_override: bool
    probe_config_override: bool
    mods_dir_override: bool


@dataclass(frozen=True)
class GameRootPaths:
    """Install paths selected when the user changes the game directory."""

    root: Path
    ue4ss_log: Path | None
    probe_config: Path | None
    mods_dir: Path | None


def resolve_game_root_paths(
    kh3_dir: str | Path,
    *,
    ue4ss_log: Path | None,
    probe_config: Path | None,
    mods_dir: Path | None,
    ue4ss_log_override: bool,
    probe_config_override: bool,
    mods_dir_override: bool,
) -> GameRootPaths:
    root = normalize_kh3_root_dir(Path(kh3_dir))
    return GameRootPaths(
        root=root,
        ue4ss_log=ue4ss_log if ue4ss_log_override else kh3_ue4ss_log_from_root(root),
        probe_config=probe_config
        if probe_config_override
        else RuntimeArtifactService.select_probe_config_path(
            ProbeConfigPathInput(explicit_path=None, discovered_path=kh3_probe_config_from_root(root))
        ),
        mods_dir=mods_dir if mods_dir_override else kh3_mods_dir_from_root(root).resolve(),
    )


def resolve_client_paths(
    *,
    kh3_dir: str | None,
    save_dir: str | None,
    ue4ss_log: str | None,
    probe_config: str | None,
    mods_dir: str | None,
    persisted_save_dir: str | None,
) -> ClientPaths:
    root = normalize_kh3_root_dir(Path(kh3_dir)) if kh3_dir else resolve_default_kh3_root_dir()
    save_input = save_dir if save_dir is not None else persisted_save_dir
    return ClientPaths(
        kh3_dir=root,
        save_dir=resolve_kh3_save_dir_input(save_dir) if save_dir else resolve_default_kh3_save_dir(root),
        ue4ss_log=Path(ue4ss_log).expanduser().resolve() if ue4ss_log else kh3_ue4ss_log_from_root(root),
        probe_config=RuntimeArtifactService.select_probe_config_path(
            ProbeConfigPathInput(explicit_path=probe_config, discovered_path=kh3_probe_config_from_root(root))
        ),
        mods_dir=Path(mods_dir).expanduser().resolve() if mods_dir else kh3_mods_dir_from_root(root).resolve(),
        save_dir_override=save_dir is not None
        or bool(persisted_save_dir and not is_kh3_auto_save_dir(persisted_save_dir)),
        dynamic_save_root_override=bool(save_input and is_kh3_save_dir_dynamic_root(save_input)),
        ue4ss_log_override=ue4ss_log is not None,
        probe_config_override=probe_config is not None,
        mods_dir_override=mods_dir is not None,
    )
