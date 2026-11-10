"""KH3 runtime installation operations."""

from __future__ import annotations

import json
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from ..RuntimeDownload import DLL_NAMES, RELEASE_CONFIG_NAME, record_installed, stage_runtime
from .file_ops import (
    _copy_file,
    _copy_tree_contents,
)
from .paths import (
    kh3_win64_dir,
    legacy_mods_txt_path,
    mods_txt_path,
    path_specific_mods_txt_path,
    path_specific_probe_config_path,
    path_specific_probe_dll_path,
    probe_config_path,
    probe_dll_path,
)
from .platform_config import (
    ensure_proton_dwmapi_override,
    prepare_xinput_runtime,
)
from .resources import (
    _resource_is_file,
    _runtime_probe_source,
    _runtime_win64_source,
)
from .status import (
    runtime_install_status,
)
from .ue4ss_config import (
    RUNTIME_PROBE_MOD_ENABLED,
    enforce_required_ue4ss_settings,
    ensure_mod_enabled,
    ensure_ue4ss_proxy_override,
    install_path_specific_ue4ss_game_config,
    install_version_specific_signature_overrides,
    remove_legacy_lua_probe,
    remove_stale_proxy_files,
    remove_stale_runtime_files,
    write_default_probe_config,
)


def _install_probe_layouts(
    kh3_root_dir: Path,
    timestamp: str,
    bridge_dir: Path | None,
    errors: list[str],
    probe_source,
) -> dict[str, Any]:
    probe_result = _copy_file(probe_source, probe_dll_path(kh3_root_dir), timestamp)
    if probe_result["missing"]:
        errors.append(f"Missing released KH3APProbeCpp DLL: {probe_result['missing']}")
    if probe_result.get("error"):
        errors.append(f"Could not install released KH3APProbeCpp DLL: {probe_result['error']}")

    path_specific_probe_result: dict[str, Any] | None = None
    path_specific_probe_target = path_specific_probe_dll_path(kh3_root_dir)
    if path_specific_probe_target is not None:
        path_specific_probe_result = _copy_file(probe_source, path_specific_probe_target, timestamp)
        if path_specific_probe_result["missing"]:
            errors.append(f"Missing released path-specific KH3APProbeCpp DLL: {path_specific_probe_result['missing']}")
        if path_specific_probe_result.get("error"):
            errors.append(
                f"Could not install released path-specific KH3APProbeCpp DLL: {path_specific_probe_result['error']}"
            )

    mods_txt_result = ensure_mod_enabled(
        mods_txt_path(kh3_root_dir),
        "KH3APProbeCpp",
        enabled=RUNTIME_PROBE_MOD_ENABLED,
    )
    path_specific_mods_txt_result: dict[str, Any] | None = None
    path_specific_mods_txt = path_specific_mods_txt_path(kh3_root_dir)
    if path_specific_mods_txt is not None:
        path_specific_mods_txt_result = ensure_mod_enabled(
            path_specific_mods_txt,
            "KH3APProbeCpp",
            enabled=RUNTIME_PROBE_MOD_ENABLED,
        )
    legacy_mods_txt_result = ensure_mod_enabled(
        legacy_mods_txt_path(kh3_root_dir),
        "KH3APProbeCpp",
        enabled=False,
    )
    legacy_lua_mods_txt_result = ensure_mod_enabled(
        mods_txt_path(kh3_root_dir),
        "KH3APProbe",
        enabled=False,
        add_if_missing=False,
    )
    legacy_lua_legacy_mods_txt_result = ensure_mod_enabled(
        legacy_mods_txt_path(kh3_root_dir),
        "KH3APProbe",
        enabled=False,
        add_if_missing=False,
    )
    legacy_lua_probe_result = remove_legacy_lua_probe(kh3_root_dir, timestamp)
    if legacy_lua_probe_result.get("error"):
        errors.append(f"Could not remove legacy KH3APProbe Lua mod: {legacy_lua_probe_result['error']}")
    write_default_probe_config(probe_config_path(kh3_root_dir), bridge_dir)
    path_specific_probe_config = path_specific_probe_config_path(kh3_root_dir)
    if path_specific_probe_config is not None:
        write_default_probe_config(path_specific_probe_config, bridge_dir)

    return {
        "probe": probe_result,
        "path_specific_probe": path_specific_probe_result,
        "mods_txt": mods_txt_result,
        "path_specific_mods_txt": path_specific_mods_txt_result,
        "legacy_mods_txt": legacy_mods_txt_result,
        "legacy_lua_mods_txt": legacy_lua_mods_txt_result,
        "legacy_lua_legacy_mods_txt": legacy_lua_legacy_mods_txt_result,
        "legacy_lua_probe": legacy_lua_probe_result,
    }


def _configure_runtime(
    kh3_root_dir: Path,
    win64: Path,
    timestamp: str,
    errors: list[str],
) -> dict[str, Any]:
    proxy_override_result = ensure_ue4ss_proxy_override(win64, timestamp)
    errors.extend(
        f"Could not install UE4SS proxy override {error}" for error in proxy_override_result.get("errors", [])
    )
    stale_proxy_result = remove_stale_proxy_files(win64, timestamp)
    errors.extend(f"Could not remove stale UE4SS proxy {error}" for error in stale_proxy_result.get("errors", []))
    stale_runtime_result = remove_stale_runtime_files(win64, timestamp)
    errors.extend(
        f"Could not remove stale UE4SS runtime file {error}" for error in stale_runtime_result.get("errors", [])
    )
    ue4ss_game_config_alias_result = install_path_specific_ue4ss_game_config(kh3_root_dir, timestamp)
    errors.extend(
        f"Could not install UE4SS game config alias {error}"
        for error in ue4ss_game_config_alias_result.get("errors", [])
    )
    signature_overrides_result = install_version_specific_signature_overrides(kh3_root_dir, timestamp)
    errors.extend(
        f"Could not install UE4SS signature override {error}" for error in signature_overrides_result.get("errors", [])
    )
    ue4ss_settings_result = enforce_required_ue4ss_settings(win64)
    errors.extend(f"Could not update UE4SS setting {error}" for error in ue4ss_settings_result.get("errors", []))
    proton_dwmapi_override_result = ensure_proton_dwmapi_override(kh3_root_dir, timestamp)
    errors.extend(
        f"Could not update Proton dwmapi DLL override {error}"
        for error in proton_dwmapi_override_result.get("errors", [])
    )

    return {
        "ue4ss_proxy_override": proxy_override_result,
        "ue4ss_settings": ue4ss_settings_result,
        "stale_proxies": stale_proxy_result,
        "stale_runtime_files": stale_runtime_result,
        "ue4ss_game_config_alias": ue4ss_game_config_alias_result,
        "signature_overrides": signature_overrides_result,
        "proton_dwmapi_override": proton_dwmapi_override_result,
    }


def install_kh3_runtime(kh3_root_dir: Path, bridge_dir: Path | None = None) -> dict[str, Any]:
    kh3_root_dir = kh3_root_dir.expanduser().resolve()
    win64 = kh3_win64_dir(kh3_root_dir)
    if not win64.is_dir():
        return {
            "status": "missing_kh3",
            "errors": [f"KH3 Binaries\\Win64 directory was not found: {win64}"],
            "status_snapshot": runtime_install_status(kh3_root_dir),
        }

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    errors: list[str] = []
    before_status = runtime_install_status(kh3_root_dir)
    runtime_source = _runtime_win64_source()
    release_config = runtime_source.joinpath(RELEASE_CONFIG_NAME)
    with tempfile.TemporaryDirectory(prefix="kh3-ue4ss-") as temporary_dir:
        staging = Path(temporary_dir)
        try:
            if not _resource_is_file(release_config):
                raise ValueError("Missing UE4SS release configuration. Repair your KH3 client installation.")
            config = json.loads(release_config.read_bytes())
            remote_release = stage_runtime(config, win64, staging)
            probe_source = _runtime_probe_source()
            probe_source.use_installed(probe_dll_path(kh3_root_dir))
            specific = path_specific_probe_dll_path(kh3_root_dir)
            if specific is not None:
                probe_source.use_installed(specific)
            # Verify all probe bytes before prerequisites or any game-file writes.
            probe_source.read_bytes()
        except Exception as exc:
            return {
                "status": "error",
                "errors": [f"Could not fetch/verify the runtime releases; no runtime files changed: {exc}"],
                "status_snapshot": before_status,
            }
        try:
            xinput_result = prepare_xinput_runtime(kh3_root_dir, timestamp)
        except Exception as exc:
            return {
                "status": "error",
                "errors": [f"XInput prerequisite failed; UE4SS/probe files were not changed: {exc}"],
                "status_snapshot": runtime_install_status(kh3_root_dir),
            }
        ue4ss_result = _copy_tree_contents(staging, win64, timestamp)
        ue4ss_result["skipped"].extend(remote_release["skipped"])
        if ue4ss_result["errors"]:
            return {
                "status": "partial",
                "errors": ue4ss_result["errors"],
                "ue4ss": ue4ss_result,
                "ue4ss_release": remote_release,
                "status_snapshot": runtime_install_status(kh3_root_dir),
            }
        record_installed(config, win64, remote_release)
    bundled_result = _copy_tree_contents(runtime_source, win64, timestamp, exclude=DLL_NAMES)
    for key in ("copied", "skipped", "backups", "missing", "errors"):
        ue4ss_result[key].extend(bundled_result[key])
    errors.extend(f"Missing bundled UE4SS path: {path}" for path in ue4ss_result["missing"])
    errors.extend(f"Could not install UE4SS file {error}" for error in ue4ss_result.get("errors", []))
    configuration = _configure_runtime(kh3_root_dir, win64, timestamp, errors)
    probe_results = _install_probe_layouts(kh3_root_dir, timestamp, bridge_dir, errors, probe_source)

    return {
        "status": "ok" if not errors else "partial",
        "errors": errors,
        "ue4ss": ue4ss_result,
        "ue4ss_release": remote_release,
        "xinput_runtime": xinput_result,
        "ue4ss_proxy_override": configuration["ue4ss_proxy_override"],
        "ue4ss_settings": configuration["ue4ss_settings"],
        "probe": probe_results["probe"],
        "path_specific_probe": probe_results["path_specific_probe"],
        "stale_proxies": configuration["stale_proxies"],
        "stale_runtime_files": configuration["stale_runtime_files"],
        "ue4ss_game_config_alias": configuration["ue4ss_game_config_alias"],
        "signature_overrides": configuration["signature_overrides"],
        "proton_dwmapi_override": configuration["proton_dwmapi_override"],
        "mods_txt": probe_results["mods_txt"],
        "path_specific_mods_txt": probe_results["path_specific_mods_txt"],
        "legacy_mods_txt": probe_results["legacy_mods_txt"],
        "legacy_lua_mods_txt": probe_results["legacy_lua_mods_txt"],
        "legacy_lua_legacy_mods_txt": probe_results["legacy_lua_legacy_mods_txt"],
        "legacy_lua_probe": probe_results["legacy_lua_probe"],
        "replaced_existing_ue4ss": bool(ue4ss_result["backups"]),
        "existing_ue4ss_custom_conflict": before_status.get("ue4ss_custom_conflict", False),
        "status_snapshot": runtime_install_status(kh3_root_dir),
    }
