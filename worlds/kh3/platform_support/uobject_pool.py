"""On-demand, read-only UObject pool snapshots; no debugger or injected code."""

from __future__ import annotations

import os
import struct
from datetime import datetime, timezone
from pathlib import Path

from .game_discovery import KH3_EXECUTABLE_NAME, resolve_kh3_executable_path
from .process_status import get_process_status
from .uobject_config import MIN_UOBJECT_CAPACITY

# Verified fixed-array headers in the supported executables. Values are the
# storage-pointer RVAs, followed by int32 MaxElements and int32 NumElements.
# EGS allocation: 0x341A0CF; Steam slot replacement: 0x2F66676.
PROFILES = {
    (0x60547D11, 0xAEBF000): ("egs", 0x8D4BDF0),
    (0x66743F34, 0xA969000): ("steam", 0x872B020),
}


def read_pool_header(read, base: int) -> dict:
    """Bound all reads and reject unknown images before using layout offsets."""
    dos = read(base, 64)
    if dos[:2] != b"MZ":
        raise ValueError("The running module has no valid DOS header.")
    nt_offset = struct.unpack_from("<I", dos, 60)[0]
    if not 64 <= nt_offset <= 1024 * 1024:
        raise ValueError("Invalid PE header offset.")
    nt = read(base + nt_offset, 84)
    if nt[:4] != b"PE\0\0" or struct.unpack_from("<H", nt, 4)[0] != 0x8664:
        raise ValueError("The running game is not a supported 64-bit image.")
    if struct.unpack_from("<H", nt, 24)[0] != 0x20B:
        raise ValueError("Unsupported PE optional header.")
    identity = (struct.unpack_from("<I", nt, 8)[0], struct.unpack_from("<I", nt, 80)[0])
    if identity not in PROFILES:
        raise ValueError("This KH3 executable version is not supported by the pool reader.")
    storefront, header_rva = PROFILES[identity]
    for _ in range(3):
        header = read(base + header_rva, 16)
        if header == read(base + header_rva, 16):
            break
    else:
        raise ValueError("The object pool is changing; try reading it again.")
    objects, capacity, current = struct.unpack("<Qii", header)
    result = {"storefront": storefront, "current": current, "capacity": capacity}
    if not objects or objects % 8 or not 0 <= current <= capacity <= 0x7FFFFFFF or capacity <= 0:
        return {**result, "status": "invalid_header", "error": "The UObject pool header is invalid or not initialized."}
    return {**result, "status": "ok", "percent": round(current / capacity * 100, 1),
            "minimum_applied": capacity >= MIN_UOBJECT_CAPACITY,
            "note": "Current includes reusable slots; it is not an exact live-object count."}


def _read_windows_pool(pid: int, expected_exe: Path) -> dict:
    import ctypes as c
    from ctypes import wintypes as w

    kernel = c.WinDLL("kernel32", use_last_error=True)
    psapi = c.WinDLL("psapi", use_last_error=True)
    kernel.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
    kernel.OpenProcess.restype = w.HANDLE
    kernel.CloseHandle.argtypes = [w.HANDLE]
    kernel.CloseHandle.restype = w.BOOL
    kernel.QueryFullProcessImageNameW.argtypes = [w.HANDLE, w.DWORD, w.LPWSTR, c.POINTER(w.DWORD)]
    kernel.QueryFullProcessImageNameW.restype = w.BOOL
    kernel.ReadProcessMemory.argtypes = [w.HANDLE, c.c_void_p, c.c_void_p, c.c_size_t, c.POINTER(c.c_size_t)]
    kernel.ReadProcessMemory.restype = w.BOOL
    kernel.GetProcessTimes.argtypes = [w.HANDLE, c.POINTER(w.FILETIME), c.POINTER(w.FILETIME),
                                     c.POINTER(w.FILETIME), c.POINTER(w.FILETIME)]
    kernel.GetProcessTimes.restype = w.BOOL
    psapi.EnumProcessModulesEx.argtypes = [w.HANDLE, c.POINTER(c.c_void_p), w.DWORD, c.POINTER(w.DWORD), w.DWORD]
    psapi.EnumProcessModulesEx.restype = w.BOOL
    handle = kernel.OpenProcess(0x0400 | 0x0010, False, pid)  # query + VM_READ only
    if not handle:
        raise c.WinError(c.get_last_error())
    try:
        path = c.create_unicode_buffer(32768)
        length = w.DWORD(len(path))
        if not kernel.QueryFullProcessImageNameW(handle, 0, path, c.byref(length)):
            raise c.WinError(c.get_last_error())
        if Path(path.value).resolve() != expected_exe.resolve():
            raise ValueError("The running KH3 process is from a different installation. Select its KH3 directory.")
        modules = (c.c_void_p * 1024)()
        needed = w.DWORD()
        if not psapi.EnumProcessModulesEx(handle, modules, c.sizeof(modules), c.byref(needed), 3) or not modules[0]:
            raise c.WinError(c.get_last_error())

        def read(address: int, size: int) -> bytes:
            buffer = c.create_string_buffer(size)
            count = c.c_size_t()
            if not kernel.ReadProcessMemory(handle, address, buffer, size, c.byref(count)) or count.value != size:
                raise c.WinError(c.get_last_error())
            return buffer.raw

        result = read_pool_header(read, modules[0])
        created, exited, cpu_kernel, cpu_user = (w.FILETIME() for _ in range(4))
        if not kernel.GetProcessTimes(handle, c.byref(created), c.byref(exited), c.byref(cpu_kernel), c.byref(cpu_user)):
            raise c.WinError(c.get_last_error())
        if exited.dwHighDateTime or exited.dwLowDateTime:
            raise ValueError("KH3 exited while its pool was being read.")
        return {**result, "pid": pid, "exe": path.value,
                "process_created_filetime": (created.dwHighDateTime << 32) | created.dwLowDateTime}
    finally:
        kernel.CloseHandle(handle)


def snapshot_uobject_pool(kh3_dir: Path) -> dict:
    at = datetime.now(timezone.utc).isoformat()
    try:
        if os.name != "nt":
            return {"status": "unsupported_platform", "at": at, "error": "UObject reading currently requires Windows."}
        process = get_process_status(KH3_EXECUTABLE_NAME)
        if not process.get("running") or not process.get("pid"):
            return {"status": "not_running", "at": at, "error": "Launch KH3, then click Read UObjects."}
        return {**_read_windows_pool(int(process["pid"]), resolve_kh3_executable_path(Path(kh3_dir))), "at": at}
    except (OSError, ValueError, struct.error) as exc:
        return {"status": "unavailable", "at": at, "error": str(exc)}


def format_uobject_snapshot(snapshot: dict | None) -> str:
    if not snapshot:
        return "Click Read UObjects while KH3 is running."
    if snapshot.get("status") != "ok":
        return str(snapshot.get("error") or "UObject count unavailable.")
    current, capacity = snapshot["current"], snapshot["capacity"]
    note = ""
    if capacity < MIN_UOBJECT_CAPACITY:
        note = " | Below patch minimum: run Patch Game with KH3 closed, then restart."
    elif current / capacity >= 0.9:
        note = " | Pool above 90%; export a debug bundle."
    at = str(snapshot["at"]).split(".")[0].replace("T", " ")
    return (f"{current:,} / {capacity:,} ({current / capacity:.1%})"
            f"\nSnapshot {at}{note}")
