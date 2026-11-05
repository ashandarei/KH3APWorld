"""Typed environment-backed settings and default KH3 client paths.

All ``KH3_*`` environment variables consumed by the client are parsed
here. Call :func:`load_kh3_client_settings` at the point a setting is needed so
runtime environment overrides retain the legacy behavior.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath

import Utils

DEFAULT_KH3_WIN64_DIR_TEXT = r"C:\Program Files (x86)\Steam\steamapps\common\KINGDOM HEARTS III\KINGDOM HEARTS III\Binaries\Win64"
if os.name == "nt":
    DEFAULT_KH3_WIN64_DIR = Path(DEFAULT_KH3_WIN64_DIR_TEXT)
    DEFAULT_KH3_ROOT_DIR = DEFAULT_KH3_WIN64_DIR.parents[1]
else:
    DEFAULT_KH3_WIN64_DIR = PureWindowsPath(DEFAULT_KH3_WIN64_DIR_TEXT)
    DEFAULT_KH3_ROOT_DIR = Path(str(DEFAULT_KH3_WIN64_DIR.parents[1]))


def _environment_path(environ: Mapping[str, str], name: str) -> Path | None:
    value = environ.get(name)
    return Path(value) if value else None


@dataclass(frozen=True, slots=True)
class KH3ClientSettings:
    """Environment-backed KH3 client settings without discovery side effects.

    Path values intentionally remain unexpanded until their resolver consumes
    them. ``KH3_STEAM_ACCOUNT_ID`` identifies the signed-in Steam user for
    account-scoped Steam operations; it does not select a KH3 save directory.
    """

    kh3_dir: Path | None = None
    kh3_win64_dir: Path | None = None
    kh3_save_dir: Path | None = None
    kh3_mods_dir: Path | None = None
    kh3_ue4ss_log: Path | None = None
    kh3_ap_probe_config: Path | None = None
    kh3_steamapps_dir: Path | None = None
    kh3_epic_manifest_dir: Path | None = None
    kh3_steam_account_id: str | None = None

    @classmethod
    def from_environment(
        cls,
        environ: Mapping[str, str] | None = None,
    ) -> KH3ClientSettings:
        """Parse the KH3 environment variables using legacy truthiness rules."""

        source = os.environ if environ is None else environ
        return cls(
            kh3_dir=_environment_path(source, "KH3_DIR"),
            kh3_win64_dir=_environment_path(source, "KH3_WIN64_DIR"),
            kh3_save_dir=_environment_path(source, "KH3_SAVE_DIR"),
            kh3_mods_dir=_environment_path(source, "KH3_MODS_DIR"),
            kh3_ue4ss_log=_environment_path(source, "KH3_UE4SS_LOG"),
            kh3_ap_probe_config=_environment_path(source, "KH3_AP_PROBE_CONFIG"),
            kh3_steamapps_dir=_environment_path(source, "KH3_STEAMAPPS_DIR"),
            kh3_epic_manifest_dir=_environment_path(source, "KH3_EPIC_MANIFEST_DIR"),
            kh3_steam_account_id=(source.get("KH3_STEAM_ACCOUNT_ID") or None),
        )


def load_kh3_client_settings(
    environ: Mapping[str, str] | None = None,
) -> KH3ClientSettings:
    """Return a fresh settings snapshot so runtime environment patches apply."""

    return KH3ClientSettings.from_environment(environ)


def resolve_default_kh3_root_dir(
    settings: KH3ClientSettings | None = None,
) -> Path:
    """Resolve the configured, persisted, detected, or platform-default root."""

    from ..platform_support.game_discovery import (
        detect_kh3_install_dir,
        normalize_kh3_root_dir,
        resolve_kh3_executable_path,
    )

    settings = settings or load_kh3_client_settings()
    if settings.kh3_dir is not None:
        return normalize_kh3_root_dir(settings.kh3_dir)
    if settings.kh3_win64_dir is not None:
        return normalize_kh3_root_dir(settings.kh3_win64_dir)

    persisted = Utils.persistent_load().get("kh3_client", {}).get("kh3_dir")
    if persisted:
        persisted_root = normalize_kh3_root_dir(Path(persisted))
        if resolve_kh3_executable_path(persisted_root).is_file():
            return persisted_root

    detected = detect_kh3_install_dir(settings=settings)
    if detected is not None:
        return detected
    if os.name != "nt":
        return (Path.home() / "Games" / "KINGDOM HEARTS III").resolve()
    return DEFAULT_KH3_ROOT_DIR.resolve()


def resolve_default_ue4ss_log(
    settings: KH3ClientSettings | None = None,
) -> Path | None:
    """Resolve the configured or discovered UE4SS log when it exists."""

    from ..platform_support.game_discovery import kh3_ue4ss_log_from_root

    settings = settings or load_kh3_client_settings()
    candidates: list[Path] = []
    if settings.kh3_ue4ss_log is not None:
        candidates.append(settings.kh3_ue4ss_log.expanduser())
    candidates.append(kh3_ue4ss_log_from_root(resolve_default_kh3_root_dir(settings)))
    for candidate in candidates:
        if candidate.exists():
            return candidate.resolve()
    return None


def resolve_default_probe_config_path(
    settings: KH3ClientSettings | None = None,
) -> Path | None:
    """Resolve the configured or root-derived probe config when usable."""

    from ..platform_support.game_discovery import kh3_probe_config_from_root

    settings = settings or load_kh3_client_settings()
    candidates: list[Path] = []
    if settings.kh3_ap_probe_config is not None:
        candidates.append(settings.kh3_ap_probe_config.expanduser())
    candidates.append(kh3_probe_config_from_root(resolve_default_kh3_root_dir(settings)))
    for candidate in candidates:
        if candidate.parent.exists():
            return candidate.resolve()
    return None


def resolve_default_kh3_mods_dir(
    settings: KH3ClientSettings | None = None,
) -> Path:
    """Resolve the configured or root-derived KH3 ``~mods`` directory."""

    from ..platform_support.game_discovery import kh3_mods_dir_from_root

    settings = settings or load_kh3_client_settings()
    if settings.kh3_mods_dir is not None:
        return settings.kh3_mods_dir.expanduser().resolve()
    return kh3_mods_dir_from_root(resolve_default_kh3_root_dir(settings)).resolve()


__all__ = [
    "DEFAULT_KH3_ROOT_DIR",
    "DEFAULT_KH3_WIN64_DIR",
    "DEFAULT_KH3_WIN64_DIR_TEXT",
    "KH3ClientSettings",
    "load_kh3_client_settings",
    "resolve_default_kh3_mods_dir",
    "resolve_default_kh3_root_dir",
    "resolve_default_probe_config_path",
    "resolve_default_ue4ss_log",
]
