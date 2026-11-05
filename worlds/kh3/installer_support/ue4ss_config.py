"""KH3 runtime ue4ss config operations."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from .file_ops import (
    _backup_existing_file,
    _copy_tree_contents,
    _replace_file_bytes,
    _unique_backup_path,
)
from .paths import (
    EGS_GUOBJECT_ARRAY_LUA,
    EGS_GUOBJECT_ARRAY_SIGNATURE,
    active_ue4ss_path_specific_config_dir,
    kh3_executable_contains_signature,
    kh3_win64_dir,
    legacy_lua_probe_path,
    ue4ss_path_specific_config_dir,
    wine_visible_path_text,
)
from .resources import (
    _runtime_win64_source,
)

RUNTIME_PROBE_MOD_ENABLED = True


UE4SS_REQUIRED_FILES = (
    "UE4SS.dll",
    "UE4SS-settings.ini",
)


UE4SS_PROXY_FILES = ("dwmapi.dll",)


UE4SS_PROXY_OVERRIDE_FILE = "override.txt"


UE4SS_PROXY_OVERRIDE_VALUE = "."


UE4SS_STALE_PROXY_FILES = ()


UE4SS_STALE_RUNTIME_FILES = (
    Path("UE4SS_Signatures") / "GUObjectArray.lua",
    Path("Mods") / "KH3APProbeCpp" / "runtime_loader_canary.jsonl",
)


UE4SS_MANAGED_FILES = (*UE4SS_REQUIRED_FILES, *UE4SS_PROXY_FILES)


UE4SS_SETTINGS_RELATIVE_PATHS = (
    Path("UE4SS-settings.ini"),
    Path("CustomGameConfigs") / "Kingdom Hearts 3" / "UE4SS-settings.ini",
    Path("KINGDOM HEARTS III") / "UE4SS-settings.ini",
)


UE4SS_REQUIRED_SETTINGS = {
    "HookProcessInternal": "0",
    "HookProcessLocalScriptFunction": "0",
    "HookUObjectProcessEvent": "0",
    "HookGameViewportClientTick": "0",
}


UE4SS_REQUIRED_SETTINGS_SECTION = "Hooks"


UE4SS_REQUIRED_OVERRIDE_SETTINGS_SECTION = "Overrides"


def _required_ue4ss_settings_by_section(win64: Path) -> dict[str, dict[str, str]]:
    return {
        UE4SS_REQUIRED_OVERRIDE_SETTINGS_SECTION: {
            "ModsFolderPath": wine_visible_path_text(win64 / "Mods"),
            "ControllingModsTxt": wine_visible_path_text(win64 / "Mods" / "mods.txt"),
        },
        UE4SS_REQUIRED_SETTINGS_SECTION: dict(UE4SS_REQUIRED_SETTINGS),
    }


def _settings_file_compliant(settings_path: Path, win64: Path) -> bool:
    try:
        lines = settings_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return False

    values_by_section: dict[str, dict[str, str]] = {}
    current_section = ""
    for line in lines:
        stripped = line.strip().lstrip("\ufeff")
        if stripped.startswith("[") and stripped.endswith("]"):
            current_section = stripped[1:-1].strip()
            values_by_section.setdefault(current_section.casefold(), {})
            continue
        if not stripped or stripped.startswith(";") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values_by_section.setdefault(current_section.casefold(), {})[key.strip().casefold()] = value.strip()

    for section, required_settings in _required_ue4ss_settings_by_section(win64).items():
        section_values = values_by_section.get(section.casefold(), {})
        for key, value in required_settings.items():
            if section_values.get(key.casefold()) != value:
                return False
    return True


def ue4ss_settings_relative_paths(win64: Path) -> tuple[Path, ...]:
    relative_paths = list(UE4SS_SETTINGS_RELATIVE_PATHS)
    children = sorted(win64.iterdir(), key=lambda path: path.name) if win64.is_dir() else ()
    for child in children:
        if not child.is_dir():
            continue
        relative_path = Path(child.name) / "UE4SS-settings.ini"
        if (child / "MemberVariableLayout.ini").is_file() and relative_path not in relative_paths:
            relative_paths.append(relative_path)
    return tuple(relative_paths)


def ue4ss_settings_status(win64: Path) -> dict[str, Any]:
    files = {
        relative_path.as_posix(): _settings_file_compliant(win64 / relative_path, win64)
        for relative_path in ue4ss_settings_relative_paths(win64)
    }
    return {
        "files": files,
        "all_compliant": all(files.values()) if files else False,
    }


def ue4ss_proxy_override_status(win64: Path) -> dict[str, Any]:
    target = win64 / UE4SS_PROXY_OVERRIDE_FILE
    if not target.is_file():
        return {
            "path": str(target),
            "exists": False,
            "installed": False,
            "value": None,
            "reason": "missing",
        }

    try:
        text = target.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return {
            "path": str(target),
            "exists": True,
            "installed": False,
            "value": None,
            "reason": f"read_error: {exc}",
        }

    lines = text.splitlines()
    value = lines[0].strip() if lines else ""
    return {
        "path": str(target),
        "exists": True,
        "installed": value == UE4SS_PROXY_OVERRIDE_VALUE,
        "value": value,
        "reason": "ok" if value == UE4SS_PROXY_OVERRIDE_VALUE else "different",
    }


def ensure_ue4ss_proxy_override(win64: Path, timestamp: str) -> dict[str, Any]:
    before = ue4ss_proxy_override_status(win64)
    target = win64 / UE4SS_PROXY_OVERRIDE_FILE
    if before["installed"]:
        return {
            "changed": False,
            "backup": None,
            "path": str(target),
            "value": UE4SS_PROXY_OVERRIDE_VALUE,
            "errors": [],
        }

    backup: str | None = None
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            backup = str(_backup_existing_file(target, timestamp))
        _replace_file_bytes(target, f"{UE4SS_PROXY_OVERRIDE_VALUE}\n".encode("utf-8"))
    except OSError as exc:
        return {
            "changed": False,
            "backup": backup,
            "path": str(target),
            "value": UE4SS_PROXY_OVERRIDE_VALUE,
            "errors": [str(exc)],
        }

    return {
        "changed": True,
        "backup": backup,
        "path": str(target),
        "value": UE4SS_PROXY_OVERRIDE_VALUE,
        "errors": [],
    }


def install_path_specific_ue4ss_game_config(kh3_root_dir: Path, timestamp: str) -> dict[str, Any]:
    copied: list[str] = []
    skipped: list[str] = []
    backups: list[str] = []
    errors: list[str] = []

    target_dir = active_ue4ss_path_specific_config_dir(kh3_root_dir)
    if target_dir is None:
        return {
            "copied": copied,
            "skipped": skipped,
            "backups": backups,
            "errors": errors,
        }

    source_win64 = _runtime_win64_source()
    source_config_dir = source_win64.joinpath("KINGDOM HEARTS III")
    source_signature_dir = source_win64.joinpath("UE4SS_Signatures")
    win64 = kh3_win64_dir(kh3_root_dir)

    config_result = _copy_tree_contents(source_config_dir, target_dir, timestamp)
    copied.extend(str((target_dir.relative_to(win64) / Path(path)).as_posix()) for path in config_result["copied"])
    skipped.extend(str((target_dir.relative_to(win64) / Path(path)).as_posix()) for path in config_result["skipped"])
    backups.extend(config_result["backups"])
    errors.extend(f"{target_dir.relative_to(win64).as_posix()}/{error}" for error in config_result.get("errors", []))

    signature_result = _copy_tree_contents(source_signature_dir, target_dir / "UE4SS_Signatures", timestamp)
    copied.extend(
        str((target_dir.relative_to(win64) / "UE4SS_Signatures" / Path(path)).as_posix())
        for path in signature_result["copied"]
    )
    skipped.extend(
        str((target_dir.relative_to(win64) / "UE4SS_Signatures" / Path(path)).as_posix())
        for path in signature_result["skipped"]
    )
    backups.extend(signature_result["backups"])
    errors.extend(
        f"{target_dir.relative_to(win64).as_posix()}/UE4SS_Signatures/{error}"
        for error in signature_result.get("errors", [])
    )

    return {
        "copied": copied,
        "skipped": skipped,
        "backups": backups,
        "errors": errors,
    }


def install_version_specific_signature_overrides(kh3_root_dir: Path, timestamp: str) -> dict[str, Any]:
    installed: list[str] = []
    skipped: list[str] = []
    backups: list[str] = []
    errors: list[str] = []

    if "steamapps" in {part.casefold() for part in kh3_root_dir.parts}:
        return {
            "installed": installed,
            "skipped": skipped,
            "backups": backups,
            "errors": errors,
        }

    if not kh3_executable_contains_signature(kh3_root_dir, EGS_GUOBJECT_ARRAY_SIGNATURE):
        return {
            "installed": installed,
            "skipped": skipped,
            "backups": backups,
            "errors": errors,
        }

    win64 = kh3_win64_dir(kh3_root_dir)
    signature_dirs = [win64 / "UE4SS_Signatures"]
    path_specific_config_dir = ue4ss_path_specific_config_dir(kh3_root_dir)
    if path_specific_config_dir is not None:
        signature_dirs.append(path_specific_config_dir / "UE4SS_Signatures")

    for signature_dir in signature_dirs:
        target = signature_dir / "GUObjectArray.lua"
        relative_path = target.relative_to(win64).as_posix()
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.is_file() and target.read_text(encoding="utf-8", errors="replace") == EGS_GUOBJECT_ARRAY_LUA:
                skipped.append(relative_path)
                continue
            if target.exists():
                backups.append(str(_backup_existing_file(target, timestamp)))
            target.write_text(EGS_GUOBJECT_ARRAY_LUA, encoding="utf-8")
            installed.append(relative_path)
        except OSError as exc:
            errors.append(f"{relative_path}: {exc}")

    return {
        "installed": installed,
        "skipped": skipped,
        "backups": backups,
        "errors": errors,
    }


def ensure_mod_enabled(
    mods_txt: Path, mod_name: str, enabled: bool = True, add_if_missing: bool = True
) -> dict[str, Any]:
    mods_txt.parent.mkdir(parents=True, exist_ok=True)
    lines = mods_txt.read_text(encoding="utf-8").splitlines() if mods_txt.exists() else []
    updated_lines: list[str] = []
    found = False
    changed = False
    mod_state = "1" if enabled else "0"

    for line in lines:
        stripped = line.strip()
        if stripped.lower().startswith(f"{mod_name.lower()} ") and ":" in stripped:
            updated = f"{mod_name} : {mod_state}"
            updated_lines.append(updated)
            found = True
            changed = changed or line != updated
        else:
            updated_lines.append(line)

    if not found and add_if_missing:
        if updated_lines and updated_lines[-1].strip():
            updated_lines.append("")
        updated_lines.append(f"{mod_name} : {mod_state}")
        changed = True

    if changed:
        mods_txt.write_text("\n".join(updated_lines) + "\n", encoding="utf-8")

    return {"changed": changed, "found_existing": found}


def remove_stale_proxy_files(win64: Path, timestamp: str) -> dict[str, Any]:
    removed: list[str] = []
    backups: list[str] = []
    errors: list[str] = []

    for name in UE4SS_STALE_PROXY_FILES:
        target = win64 / name
        if not target.exists():
            continue
        try:
            backups.append(str(_backup_existing_file(target, timestamp)))
            target.unlink()
            removed.append(name)
        except OSError as exc:
            errors.append(f"{name}: {exc}")

    return {
        "removed": removed,
        "backups": backups,
        "errors": errors,
    }


def remove_stale_runtime_files(win64: Path, timestamp: str) -> dict[str, Any]:
    removed: list[str] = []
    backups: list[str] = []
    errors: list[str] = []

    for relative_path in UE4SS_STALE_RUNTIME_FILES:
        target = win64 / relative_path
        if not target.exists():
            continue
        try:
            backups.append(str(_backup_existing_file(target, timestamp)))
            target.unlink()
            removed.append(relative_path.as_posix())
        except OSError as exc:
            errors.append(f"{relative_path.as_posix()}: {exc}")

    return {
        "removed": removed,
        "backups": backups,
        "errors": errors,
    }


def _section_name_from_line(line: str) -> str | None:
    stripped = line.strip().lstrip("\ufeff")
    if stripped.startswith("[") and stripped.endswith("]"):
        return stripped[1:-1].strip()
    return None


def _upsert_ini_settings_by_section(
    lines: list[str], required_by_section: dict[str, dict[str, str]]
) -> tuple[list[str], bool]:
    updated_lines: list[str] = []
    changed = False
    current_section = ""
    encountered_sections: set[str] = set()
    seen: dict[str, set[str]] = {section.casefold(): set() for section in required_by_section}
    required_normalized = {
        section.casefold(): {key.casefold(): (key, value) for key, value in values.items()}
        for section, values in required_by_section.items()
    }

    def append_missing_for_section(section: str) -> None:
        nonlocal changed
        section_key = section.casefold()
        required = required_normalized.get(section_key)
        if not required:
            return
        missing = [
            (key, value)
            for key_name, (key, value) in required.items()
            if key_name not in seen.setdefault(section_key, set())
        ]
        if not missing:
            return
        if updated_lines and updated_lines[-1].strip():
            updated_lines.append("")
        for key, value in missing:
            updated_lines.append(f"{key} = {value}")
            seen[section_key].add(key.casefold())
            changed = True

    for line in lines:
        section_name = _section_name_from_line(line)
        if section_name is not None:
            append_missing_for_section(current_section)
            current_section = section_name
            encountered_sections.add(current_section.casefold())
            updated_lines.append(line)
            continue

        stripped = line.strip()
        required = required_normalized.get(current_section.casefold())
        if required and stripped and not stripped.startswith(";") and "=" in stripped:
            key, _value = stripped.split("=", 1)
            match = required.get(key.strip().casefold())
            if match is not None:
                canonical_key, required_value = match
                if canonical_key.casefold() in seen.setdefault(current_section.casefold(), set()):
                    changed = True
                    continue
                replacement = f"{canonical_key} = {required_value}"
                updated_lines.append(replacement)
                seen[current_section.casefold()].add(canonical_key.casefold())
                changed = changed or line != replacement
                continue

        updated_lines.append(line)

    append_missing_for_section(current_section)

    for section, required_settings in required_by_section.items():
        if section.casefold() in encountered_sections:
            continue
        if updated_lines and updated_lines[-1].strip():
            updated_lines.append("")
        updated_lines.append(f"[{section}]")
        for key, value in required_settings.items():
            updated_lines.append(f"{key} = {value}")
        changed = True

    return updated_lines, changed


def enforce_required_ue4ss_settings(win64: Path) -> dict[str, Any]:
    changed: list[str] = []
    errors: list[str] = []
    required_by_section = _required_ue4ss_settings_by_section(win64)

    for relative_path in ue4ss_settings_relative_paths(win64):
        target = win64 / relative_path
        try:
            lines = target.read_text(encoding="utf-8", errors="replace").splitlines() if target.exists() else []
            updated_lines, file_changed = _upsert_ini_settings_by_section(lines, required_by_section)

            if file_changed:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("\n".join(updated_lines) + "\n", encoding="utf-8")
                changed.append(relative_path.as_posix())
        except OSError as exc:
            errors.append(f"{relative_path}: {exc}")

    return {
        "changed": changed,
        "errors": errors,
    }


def write_default_probe_config(config_path: Path, bridge_dir: Path | None = None) -> None:
    output_paths = ["        [[Mods\\KH3APProbeCpp\\runtime_loot_event.json]],"]
    if bridge_dir is not None:
        output_paths.append(f"        [[{wine_visible_path_text(bridge_dir / 'runtime_loot_event.json')}]],")
    content = "return {\n    event_output_paths = {\n" + "\n".join(output_paths) + "\n    },\n}\n"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(content, encoding="utf-8")


def remove_legacy_lua_probe(kh3_root_dir: Path, timestamp: str) -> dict[str, Any]:
    legacy_path = legacy_lua_probe_path(kh3_root_dir)
    if not legacy_path.exists():
        return {"changed": False, "path": str(legacy_path)}

    backup_path = _unique_backup_path(legacy_path, timestamp)
    try:
        shutil.move(str(legacy_path), str(backup_path))
    except OSError as exc:
        return {
            "changed": False,
            "path": str(legacy_path),
            "error": str(exc),
        }

    return {
        "changed": True,
        "path": str(legacy_path),
        "backup": str(backup_path),
    }
