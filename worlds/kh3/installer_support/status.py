"""KH3 runtime status operations."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..RuntimeDownload import DLL_NAMES, RELEASE_CONFIG_NAME, cached_file_states
from .paths import (
    active_ue4ss_path_specific_config_dir,
    kh3_win64_dir,
    legacy_lua_probe_path,
    legacy_mods_txt_path,
    legacy_probe_dll_path,
    mods_txt_path,
    probe_config_path,
    probe_dll_path,
)
from .platform_config import (
    proton_dwmapi_override_status,
    xinput_runtime_status,
)
from .resources import (
    _resource_is_dir,
    _resource_is_file,
    _resource_metadata_equal,
    _runtime_probe_source,
    _runtime_win64_source,
    probe_dll_version_info,
)
from .ue4ss_config import (
    UE4SS_MANAGED_FILES,
    UE4SS_PROXY_FILES,
    UE4SS_REQUIRED_FILES,
    ue4ss_proxy_override_status,
    ue4ss_settings_status,
)


def existing_ue4ss_file_state(win64: Path) -> dict[str, str]:
    state: dict[str, str] = {}
    bundled_win64 = _runtime_win64_source()
    remote_states = {}
    if _resource_is_file(bundled_win64.joinpath(RELEASE_CONFIG_NAME)):
        try:
            config = json.loads(bundled_win64.joinpath(RELEASE_CONFIG_NAME).read_bytes())
            remote_states = cached_file_states(config, win64)
        except (OSError, ValueError, TypeError):
            pass
    for name in UE4SS_MANAGED_FILES:
        installed = win64 / name
        bundled = bundled_win64.joinpath(name)
        if not installed.exists():
            state[name] = "missing"
        elif name in DLL_NAMES:
            # Latest-release hashes are checked only during explicit Patch Game.
            # Status polling must not make network requests or compare old loose DLLs.
            state[name] = remote_states.get(name, "installed")
        elif _resource_metadata_equal(bundled, installed):
            state[name] = "bundled"
        elif _resource_is_file(bundled):
            state[name] = "different"
        else:
            state[name] = "installed"
    return state


def _ue4ss_state_has_custom_conflict(
    ue4ss_file_state: dict[str, str],
    settings_status: dict[str, Any],
) -> bool:
    settings_compliant = bool(settings_status.get("files", {}).get("UE4SS-settings.ini"))
    return any(
        value == "different" and not (name == "UE4SS-settings.ini" and settings_compliant)
        for name, value in ue4ss_file_state.items()
    )


@dataclass(frozen=True)
class _ProbeLayoutFiles:
    mods_txt_text: str
    specific_probe: Path | None
    specific_probe_config: Path | None
    specific_mods_txt: Path | None
    specific_mods_txt_text: str
    legacy_mods_txt: Path
    legacy_mods_txt_text: str


def _read_probe_layout_files(kh3_root_dir: Path) -> _ProbeLayoutFiles:
    mods_txt = mods_txt_path(kh3_root_dir)
    mods_txt_text = mods_txt.read_text(encoding="utf-8", errors="replace") if mods_txt.exists() else ""
    path_specific_dir = active_ue4ss_path_specific_config_dir(kh3_root_dir)
    path_specific_probe = (
        path_specific_dir / "Mods" / "KH3APProbeCpp" / "dlls" / "main.dll" if path_specific_dir is not None else None
    )
    path_specific_probe_config = (
        path_specific_dir / "Mods" / "KH3APProbeCpp" / "config.lua" if path_specific_dir is not None else None
    )
    path_specific_mods_txt = path_specific_dir / "Mods" / "mods.txt" if path_specific_dir is not None else None
    path_specific_mods_txt_text = (
        path_specific_mods_txt.read_text(encoding="utf-8", errors="replace")
        if path_specific_mods_txt is not None and path_specific_mods_txt.exists()
        else ""
    )
    legacy_mods_txt = legacy_mods_txt_path(kh3_root_dir)
    legacy_mods_txt_text = (
        legacy_mods_txt.read_text(encoding="utf-8", errors="replace") if legacy_mods_txt.exists() else ""
    )
    return _ProbeLayoutFiles(
        mods_txt_text=mods_txt_text,
        specific_probe=path_specific_probe,
        specific_probe_config=path_specific_probe_config,
        specific_mods_txt=path_specific_mods_txt,
        specific_mods_txt_text=path_specific_mods_txt_text,
        legacy_mods_txt=legacy_mods_txt,
        legacy_mods_txt_text=legacy_mods_txt_text,
    )


def runtime_install_status(kh3_root_dir: Path) -> dict[str, Any]:
    win64 = kh3_win64_dir(kh3_root_dir)
    nested_ue4ss_dll = win64 / "ue4ss" / "UE4SS.dll"
    proxy_override_status = ue4ss_proxy_override_status(win64)
    loader_precedence_conflict = nested_ue4ss_dll.is_file() and not proxy_override_status["installed"]
    layout = _read_probe_layout_files(kh3_root_dir)
    ue4ss_file_state = existing_ue4ss_file_state(win64)
    runtime_probe_source = _runtime_probe_source()
    probe_installed = probe_dll_path(kh3_root_dir).is_file()
    path_specific_probe_installed = layout.specific_probe is not None and layout.specific_probe.is_file()
    legacy_probe_installed = legacy_probe_dll_path(kh3_root_dir).is_file()
    probe_info = probe_dll_version_info(probe_dll_path(kh3_root_dir))
    specific_probe_info = probe_dll_version_info(layout.specific_probe) if layout.specific_probe is not None else {}
    legacy_probe_info = probe_dll_version_info(legacy_probe_dll_path(kh3_root_dir))
    probe_matches_bundle = runtime_probe_source.matches(probe_info)
    path_specific_probe_matches_bundle = layout.specific_probe is not None and runtime_probe_source.matches(specific_probe_info)
    legacy_probe_matches_bundle = runtime_probe_source.matches(legacy_probe_info)
    probe_mod_enabled = "KH3APProbeCpp : 1" in layout.mods_txt_text
    path_specific_probe_mod_enabled = (
        layout.specific_mods_txt is not None and "KH3APProbeCpp : 1" in layout.specific_mods_txt_text
    )
    legacy_probe_mod_enabled = "KH3APProbeCpp : 1" in layout.legacy_mods_txt_text
    settings_status = ue4ss_settings_status(win64)
    path_specific_probe_required = layout.specific_probe is not None
    active_probe_layouts_ready = (
        probe_matches_bundle
        and probe_mod_enabled
        and (not path_specific_probe_required or path_specific_probe_matches_bundle)
        and (not path_specific_probe_required or path_specific_probe_mod_enabled)
        and not legacy_probe_mod_enabled
        and not loader_precedence_conflict
    )
    proton_override_status = proton_dwmapi_override_status(kh3_root_dir)
    xinput_status = xinput_runtime_status(kh3_root_dir)
    return {
        "kh3_root_dir": str(kh3_root_dir),
        "win64_dir": str(win64),
        "win64_exists": win64.is_dir(),
        "bundle_win64_exists": _resource_is_dir(_runtime_win64_source()),
        "bundle_probe_exists": runtime_probe_source.is_file(),
        "ue4ss_files": {name: (win64 / name).is_file() for name in UE4SS_REQUIRED_FILES},
        "ue4ss_proxy_installed": any((win64 / name).is_file() for name in UE4SS_PROXY_FILES),
        "xinput_runtime": xinput_status,
        "xinput_runtime_ready": xinput_status["ready"],
        "ue4ss_proxy_override": proxy_override_status,
        "ue4ss_proxy_override_installed": proxy_override_status["installed"],
        "ue4ss_preferred_nested_runtime_path": str(nested_ue4ss_dll),
        "ue4ss_preferred_nested_runtime_installed": nested_ue4ss_dll.is_file(),
        "ue4ss_loader_precedence_conflict": loader_precedence_conflict,
        "ue4ss_effective_runtime_path": str(
            win64 / "UE4SS.dll"
            if proxy_override_status["installed"] or not nested_ue4ss_dll.is_file()
            else nested_ue4ss_dll
        ),
        "ue4ss_file_state": ue4ss_file_state,
        "ue4ss_settings": settings_status,
        "ue4ss_settings_compliant": settings_status["all_compliant"],
        "ue4ss_existing": any(value in {"bundled", "different", "installed"} for value in ue4ss_file_state.values()),
        "ue4ss_custom_conflict": (
            _ue4ss_state_has_custom_conflict(ue4ss_file_state, settings_status)
            or loader_precedence_conflict
            or (proxy_override_status["exists"] and not proxy_override_status["installed"])
        ),
        "ue4ss_signatures_installed": (win64 / "UE4SS_Signatures").is_dir(),
        "probe_dll_installed": probe_installed,
        "path_specific_probe_required": path_specific_probe_required,
        "path_specific_probe_dll_installed": path_specific_probe_installed,
        "legacy_probe_dll_installed": legacy_probe_installed,
        "probe_dll_version_info": probe_info,
        "path_specific_probe_dll_version_info": specific_probe_info,
        "legacy_probe_dll_version_info": legacy_probe_info,
        "probe_dll_version": probe_info.get("version"),
        "probe_dll_build_stamp": probe_info.get("build_stamp"),
        "probe_dll_short_hash": probe_info.get("short_hash"),
        "probe_dll_matches_bundle": probe_matches_bundle,
        "path_specific_probe_dll_matches_bundle": path_specific_probe_matches_bundle,
        "legacy_probe_dll_matches_bundle": legacy_probe_matches_bundle,
        "probe_dll_layouts_ready": active_probe_layouts_ready,
        "probe_single_layout_ready": active_probe_layouts_ready,
        "probe_config_installed": probe_config_path(kh3_root_dir).is_file(),
        "path_specific_probe_config_installed": (
            layout.specific_probe_config is not None and layout.specific_probe_config.is_file()
        ),
        "legacy_lua_probe_installed": legacy_lua_probe_path(kh3_root_dir).exists(),
        "legacy_lua_probe_enabled": (legacy_lua_probe_path(kh3_root_dir) / "enabled.txt").is_file(),
        "probe_mod_enabled": probe_mod_enabled,
        "path_specific_probe_mod_enabled": path_specific_probe_mod_enabled,
        "legacy_probe_mod_enabled": legacy_probe_mod_enabled,
        "probe_mod_layouts_enabled": (
            probe_mod_enabled
            and (not path_specific_probe_required or path_specific_probe_mod_enabled)
            and not legacy_probe_mod_enabled
        ),
        "probe_legacy_layout_present": legacy_probe_installed or layout.legacy_mods_txt.exists(),
        "proton_dwmapi_override": proton_override_status,
        "proton_dwmapi_override_installed": proton_override_status["installed"],
    }
