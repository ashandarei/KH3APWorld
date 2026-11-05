"""KH3 runtime paths operations."""

from __future__ import annotations

import os
from pathlib import Path

from ..platform_support.xinput_runtime import wine_prefix_for_kh3

EGS_GUOBJECT_ARRAY_SIGNATURE = "48 8D 3D ? ? ? ? 48 89 78 08 48 8B CF FF 15 ? ? ? ? 85 C0 75 0A 48 8B CF FF 15"


EGS_GUOBJECT_ARRAY_LUA = """function Register()
    return "48 8D 3D ? ? ? ? 48 89 78 08 48 8B CF FF 15 ? ? ? ? 85 C0 75 0A 48 8B CF FF 15"
end

function OnMatchFound(MatchAddress)
    local LeaInstr = MatchAddress
    local NextInstr = LeaInstr + 0x7
    local Offset = LeaInstr + 0x3
    local AddressLoaded = NextInstr + DerefToInt32(Offset)
    return AddressLoaded - 0x20
end
"""


_EXECUTABLE_SIGNATURE_CACHE: dict[tuple[Path, str], tuple[tuple[int, int], bool]] = {}


def kh3_win64_dir(kh3_root_dir: Path) -> Path:
    return kh3_root_dir / "Binaries" / "Win64"


def kh3_executable_path(kh3_root_dir: Path) -> Path:
    return kh3_win64_dir(kh3_root_dir) / "KINGDOM HEARTS III.exe"


def ue4ss_game_specific_config_name(kh3_root_dir: Path) -> str | None:
    win64 = kh3_win64_dir(kh3_root_dir)
    try:
        return win64.parent.parent.parent.name
    except IndexError:
        return None


def ue4ss_path_specific_config_dir(kh3_root_dir: Path) -> Path | None:
    name = ue4ss_game_specific_config_name(kh3_root_dir)
    if not name or name == "KINGDOM HEARTS III":
        return None
    return kh3_win64_dir(kh3_root_dir) / name


def probe_config_path(kh3_root_dir: Path) -> Path:
    return kh3_win64_dir(kh3_root_dir) / "Mods" / "KH3APProbeCpp" / "config.lua"


def legacy_lua_probe_path(kh3_root_dir: Path) -> Path:
    return kh3_win64_dir(kh3_root_dir) / "Mods" / "KH3APProbe"


def probe_dll_path(kh3_root_dir: Path) -> Path:
    return kh3_win64_dir(kh3_root_dir) / "Mods" / "KH3APProbeCpp" / "dlls" / "main.dll"


def legacy_probe_dll_path(kh3_root_dir: Path) -> Path:
    return kh3_win64_dir(kh3_root_dir) / "KINGDOM HEARTS III" / "Mods" / "KH3APProbeCpp" / "dlls" / "main.dll"


def mods_txt_path(kh3_root_dir: Path) -> Path:
    return kh3_win64_dir(kh3_root_dir) / "Mods" / "mods.txt"


def legacy_mods_txt_path(kh3_root_dir: Path) -> Path:
    return kh3_win64_dir(kh3_root_dir) / "KINGDOM HEARTS III" / "Mods" / "mods.txt"


def wine_visible_path_text(path: Path) -> str:
    if os.name != "nt" and path.is_absolute():
        return "Z:" + str(path).replace("/", "\\")
    return str(path)


def steamapps_dir_for_kh3_root(kh3_root_dir: Path) -> Path | None:
    for path in (kh3_root_dir, *kh3_root_dir.parents):
        if path.name.casefold() == "steamapps":
            return path
    return None


def proton_prefix_dir_for_kh3_root(kh3_root_dir: Path) -> Path | None:
    return wine_prefix_for_kh3(kh3_root_dir)


def proton_user_registry_path_for_kh3_root(kh3_root_dir: Path) -> Path | None:
    prefix = proton_prefix_dir_for_kh3_root(kh3_root_dir)
    if prefix is None:
        return None
    return prefix / "user.reg"


def _parse_byte_signature(signature: str) -> tuple[bytes, tuple[bool, ...]]:
    values: list[int] = []
    mask: list[bool] = []
    for token in signature.split():
        if token in {"?", "??"}:
            values.append(0)
            mask.append(False)
        else:
            values.append(int(token, 16))
            mask.append(True)
    return bytes(values), tuple(mask)


def _bytes_contain_signature(data: bytes, signature: str) -> bool:
    pattern, mask = _parse_byte_signature(signature)
    pattern_size = len(pattern)
    if pattern_size == 0 or len(data) < pattern_size:
        return False

    # Search the longest literal span in C, then verify only its candidates.
    # Walking every executable byte in Python kept the GIL busy during the
    # native client's background installation inspection.
    anchor_start = anchor_size = run_start = run_size = 0
    for index, fixed in enumerate(mask):
        if fixed:
            if not run_size:
                run_start = index
            run_size += 1
            if run_size > anchor_size:
                anchor_start, anchor_size = run_start, run_size
        else:
            run_size = 0
    if not anchor_size:
        return True
    anchor = pattern[anchor_start : anchor_start + anchor_size]
    fixed_bytes = tuple((index, byte) for index, byte in enumerate(pattern) if mask[index])
    search_from = anchor_start
    last_start = len(data) - pattern_size
    while (found := data.find(anchor, search_from)) != -1:
        offset = found - anchor_start
        if offset > last_start:
            break
        if all(data[offset + index] == byte for index, byte in fixed_bytes):
            return True
        search_from = found + 1  # Keep overlapping candidates.
    return False


def kh3_executable_contains_signature(kh3_root_dir: Path, signature: str) -> bool:
    executable = kh3_executable_path(kh3_root_dir)
    try:
        stat = executable.stat()
    except OSError:
        return False

    cache_key = (executable, signature)
    fingerprint = (stat.st_mtime_ns, stat.st_size)
    cached = _EXECUTABLE_SIGNATURE_CACHE.get(cache_key)
    if cached is not None and cached[0] == fingerprint:
        return cached[1]

    try:
        result = _bytes_contain_signature(executable.read_bytes(), signature)
    except OSError:
        return False
    _EXECUTABLE_SIGNATURE_CACHE[cache_key] = (fingerprint, result)
    return result


def active_ue4ss_path_specific_config_dir(kh3_root_dir: Path) -> Path | None:
    """Return the EGS game-specific directory that UE4SS selects before loading mods."""

    if "steamapps" in {part.casefold() for part in kh3_root_dir.parts}:
        return None
    if not kh3_executable_contains_signature(kh3_root_dir, EGS_GUOBJECT_ARRAY_SIGNATURE):
        return None
    return ue4ss_path_specific_config_dir(kh3_root_dir)


def path_specific_probe_config_path(kh3_root_dir: Path) -> Path | None:
    config_dir = active_ue4ss_path_specific_config_dir(kh3_root_dir)
    if config_dir is None:
        return None
    return config_dir / "Mods" / "KH3APProbeCpp" / "config.lua"


def path_specific_probe_dll_path(kh3_root_dir: Path) -> Path | None:
    config_dir = active_ue4ss_path_specific_config_dir(kh3_root_dir)
    if config_dir is None:
        return None
    return config_dir / "Mods" / "KH3APProbeCpp" / "dlls" / "main.dll"


def path_specific_mods_txt_path(kh3_root_dir: Path) -> Path | None:
    config_dir = active_ue4ss_path_specific_config_dir(kh3_root_dir)
    if config_dir is None:
        return None
    return config_dir / "Mods" / "mods.txt"
