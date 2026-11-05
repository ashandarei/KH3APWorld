"""KH3 runtime platform config operations."""

from __future__ import annotations

import hashlib
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from ..platform_support.xinput_runtime import LEGACY_XINPUT_SHA256, ensure_windows_xinput, windows_xinput_status
from .file_ops import (
    _backup_existing_file,
    _replace_file_bytes,
    _unique_backup_path,
)
from .paths import (
    kh3_win64_dir,
    proton_user_registry_path_for_kh3_root,
)

KH3_STEAM_APP_ID = "2552450"


KH3_PROTON_APPDEFAULTS_DLL_OVERRIDE_SECTION = r"[Software\\Wine\\AppDefaults\\KINGDOM HEARTS III.exe\\DllOverrides]"


KH3_PROTON_DWMAPI_OVERRIDE_NAME = "dwmapi"


KH3_PROTON_DWMAPI_OVERRIDE_VALUE = "native,builtin"


KH3_PROTON_XINPUT_OVERRIDE_NAME = "xinput1_3"


KH3_PROTON_XINPUT_OVERRIDE_VALUE = "builtin"


def _wine_registry_line_header(line: str) -> str | None:
    stripped = line.strip()
    if not stripped.startswith("["):
        return None
    end = stripped.find("]")
    if end < 0:
        return None
    return stripped[: end + 1]


def _replace_or_add_wine_registry_value(
    text: str,
    section_header: str,
    name: str,
    value: str,
    section_timestamp: str,
) -> tuple[str, bool]:
    lines = text.splitlines(keepends=True)
    output: list[str] = []
    desired_line = f'"{name}"="{value}"\n'
    value_prefix = f'"{name}"='
    section_found = False
    in_section = False
    value_written = False
    changed = False

    for line in lines:
        header = _wine_registry_line_header(line)
        if header is not None:
            if in_section and not value_written:
                output.append(desired_line)
                changed = True
            in_section = header == section_header
            if in_section:
                section_found = True
                value_written = False

        if in_section and line.lstrip().startswith(value_prefix):
            if not value_written:
                output.append(desired_line)
                value_written = True
            if line != desired_line:
                changed = True
            continue

        output.append(line)

    if in_section and not value_written:
        output.append(desired_line)
        changed = True

    if not section_found:
        if output and not output[-1].endswith(("\n", "\r")):
            output.append("\n")
        if output and output[-1].strip():
            output.append("\n")
        output.append(f"{section_header} {section_timestamp}\n")
        output.append(desired_line)
        changed = True

    return "".join(output), changed


def _proton_override_status(kh3_root_dir: Path, name: str, value: str) -> dict[str, Any]:
    if os.name == "nt":
        return {
            "applicable": False,
            "installed": False,
            "path": None,
            "reason": "windows",
        }

    try:
        user_reg = proton_user_registry_path_for_kh3_root(kh3_root_dir)
    except ValueError as exc:
        return {"applicable": True, "installed": False, "path": None, "reason": str(exc)}
    if user_reg is None:
        return {
            "applicable": False,
            "installed": False,
            "path": None,
            "reason": "steamapps_not_found",
        }

    if not user_reg.is_file():
        return {
            "applicable": False,
            "installed": False,
            "path": str(user_reg),
            "reason": "user_reg_not_found",
        }

    try:
        text = user_reg.read_text(encoding="utf-8", errors="surrogateescape")
    except OSError as exc:
        return {
            "applicable": True,
            "installed": False,
            "path": str(user_reg),
            "reason": f"read_error: {exc}",
        }

    expected_line = f'"{name}"="{value}"'
    in_section = False
    installed = False
    for line in text.splitlines():
        header = _wine_registry_line_header(line)
        if header is not None:
            in_section = header == KH3_PROTON_APPDEFAULTS_DLL_OVERRIDE_SECTION
            continue
        if in_section and line.strip() == expected_line:
            installed = True
            break

    return {
        "applicable": True,
        "installed": installed,
        "path": str(user_reg),
        "reason": "ok" if installed else "missing_or_different",
    }


def proton_dwmapi_override_status(kh3_root_dir: Path) -> dict[str, Any]:
    return _proton_override_status(kh3_root_dir, KH3_PROTON_DWMAPI_OVERRIDE_NAME, KH3_PROTON_DWMAPI_OVERRIDE_VALUE)


def _ensure_proton_overrides(kh3_root_dir: Path, timestamp: str, overrides: dict[str, str], *,
                            section: str = KH3_PROTON_APPDEFAULTS_DLL_OVERRIDE_SECTION) -> dict[str, Any]:
    if os.name == "nt":
        return {
            "applicable": False,
            "changed": False,
            "backup": None,
            "path": None,
            "errors": [],
            "reason": "windows",
        }

    try:
        user_reg = proton_user_registry_path_for_kh3_root(kh3_root_dir)
    except ValueError as exc:
        return {
            "applicable": True,
            "changed": False,
            "backup": None,
            "path": None,
            "errors": [str(exc)],
            "reason": "prefix_error",
        }
    if user_reg is None:
        return {
            "applicable": False,
            "changed": False,
            "backup": None,
            "path": None,
            "errors": [],
            "reason": "steamapps_not_found",
        }

    if not user_reg.parent.is_dir():
        return {
            "applicable": False,
            "changed": False,
            "backup": None,
            "path": str(user_reg),
            "errors": [],
            "reason": "proton_prefix_not_found",
        }

    errors: list[str] = []
    try:
        text = user_reg.read_text(encoding="utf-8", errors="surrogateescape") if user_reg.exists() else ""
        updated_text = text
        changed = False
        for name, value in overrides.items():
            updated_text, value_changed = _replace_or_add_wine_registry_value(
                updated_text,
                section,
                name,
                value,
                str(int(datetime.now().timestamp())),
            )
            changed |= value_changed
        backup = str(_backup_existing_file(user_reg, timestamp)) if changed and user_reg.exists() else None
        if changed:
            _replace_file_bytes(user_reg, updated_text.encode("utf-8", errors="surrogateescape"))
    except OSError as exc:
        errors.append(str(exc))
        changed = False
        backup = None

    return {
        "applicable": True,
        "changed": changed,
        "backup": backup,
        "path": str(user_reg),
        "errors": errors,
        "reason": "ok" if not errors else "write_error",
    }


def ensure_proton_dwmapi_override(kh3_root_dir: Path, timestamp: str) -> dict[str, Any]:
    return _ensure_proton_overrides(
        kh3_root_dir,
        timestamp,
        {
            KH3_PROTON_DWMAPI_OVERRIDE_NAME: KH3_PROTON_DWMAPI_OVERRIDE_VALUE,
        },
    )


def ensure_proton_kh02_dwmapi_override(kh3_root_dir: Path, timestamp: str) -> dict[str, Any]:
    """The owned KH0.2 child uses KH3's prefix and its own executable defaults."""
    return _ensure_proton_overrides(
        kh3_root_dir,
        timestamp,
        {KH3_PROTON_DWMAPI_OVERRIDE_NAME: KH3_PROTON_DWMAPI_OVERRIDE_VALUE},
        section=r"[Software\\Wine\\AppDefaults\\KINGDOM HEARTS 0.2 Birth by Sleep.exe\\DllOverrides]",
    )


def xinput_runtime_status(kh3_root_dir: Path) -> dict[str, Any]:
    if os.name == "nt":
        status = windows_xinput_status()
        if (kh3_win64_dir(kh3_root_dir) / "xinput1_3.dll").exists():
            return {**status, "ready": False, "reason": "local_xinput_requires_migration"}
        return status
    status = _proton_override_status(kh3_root_dir, KH3_PROTON_XINPUT_OVERRIDE_NAME, KH3_PROTON_XINPUT_OVERRIDE_VALUE)
    return {"platform": "linux", "ready": status["installed"], **status}


def prepare_xinput_runtime(kh3_root_dir: Path, timestamp: str) -> dict[str, Any]:
    """Complete prerequisites and migrate only the known former AP runtime DLL."""
    legacy = kh3_win64_dir(kh3_root_dir) / "xinput1_3.dll"
    known_legacy = legacy.is_file() and hashlib.sha256(legacy.read_bytes()).hexdigest() == LEGACY_XINPUT_SHA256
    if os.name == "nt" and legacy.exists() and not known_legacy:
        raise ValueError(
            f"An unrecognized local XInput DLL would shadow the system runtime: {legacy}. "
            "Move it aside manually after checking its mod's requirements, then retry Patch Game."
        )
    if os.name == "nt":
        result = ensure_windows_xinput()
    else:
        status = xinput_runtime_status(kh3_root_dir)
        if not status.get("path") or not type(kh3_root_dir)(status["path"]).is_file():
            raise ValueError(
                "The selected KH3 Wine/Proton prefix is unavailable. Launch KH3 once through Steam or Heroic, "
                "close the game, then retry Patch Game. " + str(status.get("reason", ""))
            )
        result = _ensure_proton_overrides(
            kh3_root_dir,
            timestamp,
            {
                KH3_PROTON_XINPUT_OVERRIDE_NAME: KH3_PROTON_XINPUT_OVERRIDE_VALUE,
                KH3_PROTON_DWMAPI_OVERRIDE_NAME: KH3_PROTON_DWMAPI_OVERRIDE_VALUE,
            },
        )
        if result["errors"]:
            raise OSError("; ".join(result["errors"]))
        if not xinput_runtime_status(kh3_root_dir)["ready"]:
            raise ValueError("Could not configure Wine's built-in XInput for KH3")
        result = {**result, "platform": "linux", "ready": True}
    if not result.get("ready"):
        raise ValueError("The XInput prerequisite is not ready")
    backup = None
    if known_legacy:
        backup = _unique_backup_path(legacy, timestamp)
        legacy.replace(backup)
    return {**result, "legacy_backup": str(backup) if backup else None, "custom_local_dll_preserved": legacy.exists()}
