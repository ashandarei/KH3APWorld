"""Cross-platform KH3 process inspection without bridge orchestration."""

from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Retain identities, not open handles. A PID alone can be reused after KH3 exits.
_windows_process_identities: dict[str, tuple[int, int, dict[str, Any]]] = {}


def _cached_windows_process_status(process_name: str, query_identity) -> dict[str, Any] | None:
    cached = _windows_process_identities.get(process_name)
    if cached is None:
        return None
    pid, created, status = cached
    observed = query_identity(pid)
    if observed is not None and observed[0] == created:
        return dict(status)
    _windows_process_identities.pop(process_name, None)
    return None


KH3_RUNTIME_MODULE_NAMES = (
    "UE4SS.dll",
    "main.dll",
    "dwmapi.dll",
    "xinput1_3.dll",
)


def _subprocess_stdout_text(result: subprocess.CompletedProcess) -> str:
    stdout = result.stdout
    if stdout is None:
        return ""
    if isinstance(stdout, str):
        return stdout
    if not isinstance(stdout, bytes):
        return str(stdout)
    if stdout.startswith((b"\xff\xfe", b"\xfe\xff")):
        return stdout.decode("utf-16", errors="replace")
    try:
        return stdout.decode("utf-8-sig")
    except UnicodeDecodeError:
        if os.name == "nt":
            return stdout.decode("mbcs", errors="replace")
        return stdout.decode(errors="replace")


def get_process_status(process_name: str) -> dict[str, Any]:
    if os.name != "nt":
        return _get_process_status_linux_proc(process_name)
    windows_api_status = _get_process_status_windows_api(process_name)
    if windows_api_status is not None:
        return windows_api_status
    process_stem = process_name.removesuffix(".exe")
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = subprocess.SW_HIDE
    command = (
        "$p = Get-Process -Name "
        + json.dumps(process_stem)
        + " -ErrorAction SilentlyContinue | Select-Object -First 1; "
        + "if ($p) { "
        + "[pscustomobject]@{Running=$true; Id=$p.Id; StartTimeUtc=$p.StartTime.ToUniversalTime().ToString('o')} "
        + "| ConvertTo-Json -Compress "
        + "} else { [pscustomobject]@{Running=$false} | ConvertTo-Json -Compress }"
    )
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", command],
            check=False,
            capture_output=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
            startupinfo=startupinfo,
            timeout=2.0,
        )
    except (OSError, subprocess.SubprocessError):
        return {"running": False}
    output = _subprocess_stdout_text(result)
    try:
        payload = json.loads(output)
    except json.JSONDecodeError:
        return {"running": process_name.casefold() in output.casefold()}
    if not isinstance(payload, dict):
        return {"running": False}
    return {
        "running": bool(payload.get("Running")),
        "pid": payload.get("Id"),
        "started_at_utc": payload.get("StartTimeUtc"),
    }


def _linux_boot_time_seconds(proc_root: Path = Path("/proc")) -> int | None:
    try:
        for line in (proc_root / "stat").read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("btime "):
                return int(line.split()[1])
    except (OSError, ValueError):
        return None
    return None


def _linux_process_started_at_utc(pid: int, proc_root: Path = Path("/proc")) -> str | None:
    boot_time = _linux_boot_time_seconds(proc_root)
    if boot_time is None:
        return None
    try:
        stat_text = (proc_root / str(pid) / "stat").read_text(encoding="utf-8", errors="replace")
        after_comm = stat_text.rsplit(")", 1)[1].strip()
        fields_after_comm = after_comm.split()
        start_ticks = int(fields_after_comm[19])
        ticks_per_second = os.sysconf(os.sysconf_names.get("SC_CLK_TCK", "SC_CLK_TCK"))
    except (IndexError, OSError, TypeError, ValueError):
        return None
    started_timestamp = boot_time + (start_ticks / ticks_per_second)
    return datetime.fromtimestamp(started_timestamp, timezone.utc).isoformat()


def _get_process_status_linux_proc(
    process_name: str,
    proc_root: Path = Path("/proc"),
) -> dict[str, Any]:
    process_name_lower = process_name.casefold()
    try:
        entries = list(proc_root.iterdir())
    except OSError:
        return {"running": False}
    for entry in entries:
        if not entry.name.isdigit():
            continue
        try:
            executable = (entry / "cmdline").read_bytes().split(b"\x00", 1)[0].decode("utf-8", errors="replace")
        except OSError:
            continue
        # Proton/Legendary wrappers also contain the game path in their arguments.
        # Only Wine's actual PE process owns the game's lifetime and PID.
        if executable.replace("\\", "/").rsplit("/", 1)[-1].casefold() != process_name_lower:
            continue
        pid = int(entry.name)
        return {
            "running": True,
            "pid": pid,
            "started_at_utc": _linux_process_started_at_utc(pid, proc_root),
        }
    return {"running": False}


def _get_process_status_windows_api(process_name: str) -> dict[str, Any] | None:
    try:
        import ctypes
        from ctypes import wintypes
    except ImportError:
        return None

    class FILETIME(ctypes.Structure):
        _fields_ = [
            ("dwLowDateTime", wintypes.DWORD),
            ("dwHighDateTime", wintypes.DWORD),
        ]

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.POINTER(wintypes.ULONG)),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", wintypes.LONG),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * 260),
        ]

    def filetime_to_utc_iso(filetime: FILETIME) -> str | None:
        value = (int(filetime.dwHighDateTime) << 32) + int(filetime.dwLowDateTime)
        if value <= 0:
            return None
        timestamp = (value - 116444736000000000) / 10_000_000
        return datetime.fromtimestamp(timestamp, timezone.utc).isoformat()

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    kernel32.Process32FirstW.restype = wintypes.BOOL
    kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    kernel32.Process32NextW.restype = wintypes.BOOL
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.GetProcessTimes.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(FILETIME),
        ctypes.POINTER(FILETIME),
        ctypes.POINTER(FILETIME),
        ctypes.POINTER(FILETIME),
    ]
    kernel32.GetProcessTimes.restype = wintypes.BOOL
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    def query_identity(pid: int) -> tuple[int, str | None] | None:
        handle = kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return None
        try:
            exit_code = wintypes.DWORD()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)) or exit_code.value != 259:
                return None
            created, exited, kernel, user = FILETIME(), FILETIME(), FILETIME(), FILETIME()
            if not kernel32.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited),
                                           ctypes.byref(kernel), ctypes.byref(user)):
                return None
            if exited.dwHighDateTime or exited.dwLowDateTime:
                return None  # An exited process can itself have exit code 259.
            filetime = (int(created.dwHighDateTime) << 32) + int(created.dwLowDateTime)
            return filetime, filetime_to_utc_iso(created)
        finally:
            kernel32.CloseHandle(handle)

    process_name_lower = process_name.casefold()
    cached_status = _cached_windows_process_status(process_name_lower, query_identity)
    if cached_status is not None:
        return cached_status

    snapshot = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)
    if snapshot == wintypes.HANDLE(-1).value:
        return None
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        found = bool(kernel32.Process32FirstW(snapshot, ctypes.byref(entry)))
        while found:
            if entry.szExeFile.casefold() == process_name_lower:
                pid = int(entry.th32ProcessID)
                identity = query_identity(pid)
                status = {"running": True, "pid": pid, "started_at_utc": identity[1] if identity else None}
                if identity is not None:
                    if len(_windows_process_identities) >= 32:
                        _windows_process_identities.clear()
                    _windows_process_identities[process_name_lower] = (pid, identity[0], status)
                return status
            found = bool(kernel32.Process32NextW(snapshot, ctypes.byref(entry)))
        return {"running": False}
    except OSError:
        return None
    finally:
        kernel32.CloseHandle(snapshot)


def get_process_module_snapshot(
    pid: int,
    module_names: tuple[str, ...] = KH3_RUNTIME_MODULE_NAMES,
) -> dict[str, Any]:
    """Collect only allowlisted runtime modules from a live Windows process."""

    requested = tuple(dict.fromkeys(name for name in module_names if name))
    if os.name != "nt":
        return {
            "status": "unsupported_platform",
            "requested_modules": list(requested),
            "modules": [],
        }

    try:
        import ctypes
        from ctypes import wintypes
    except ImportError:
        return {
            "status": "unavailable",
            "requested_modules": list(requested),
            "modules": [],
        }

    class MODULEENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("th32ModuleID", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("GlblcntUsage", wintypes.DWORD),
            ("ProccntUsage", wintypes.DWORD),
            ("modBaseAddr", ctypes.POINTER(wintypes.BYTE)),
            ("modBaseSize", wintypes.DWORD),
            ("hModule", wintypes.HANDLE),
            ("szModule", wintypes.WCHAR * 256),
            ("szExePath", wintypes.WCHAR * 260),
        ]

    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        kernel32.Module32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MODULEENTRY32W)]
        kernel32.Module32FirstW.restype = wintypes.BOOL
        kernel32.Module32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MODULEENTRY32W)]
        kernel32.Module32NextW.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL

        # Request both the native and WOW64 module lists so a diagnostics run
        # states explicitly whether each runtime DLL reached the game process.
        snapshot = kernel32.CreateToolhelp32Snapshot(0x00000008 | 0x00000010, int(pid))
        if snapshot == wintypes.HANDLE(-1).value:
            return {
                "status": "snapshot_failed",
                "requested_modules": list(requested),
                "modules": [],
                "windows_error": ctypes.get_last_error(),
            }

        try:
            entry = MODULEENTRY32W()
            entry.dwSize = ctypes.sizeof(MODULEENTRY32W)
            found = bool(kernel32.Module32FirstW(snapshot, ctypes.byref(entry)))
            requested_casefold = {name.casefold() for name in requested}
            modules: list[dict[str, Any]] = []
            while found:
                module_name = str(entry.szModule)
                if module_name.casefold() in requested_casefold:
                    modules.append({
                        "name": module_name,
                        "path": str(entry.szExePath),
                        "size": int(entry.modBaseSize),
                    })
                found = bool(kernel32.Module32NextW(snapshot, ctypes.byref(entry)))
        finally:
            kernel32.CloseHandle(snapshot)
    except (OSError, TypeError, ValueError) as exc:
        return {
            "status": "unavailable",
            "requested_modules": list(requested),
            "modules": [],
            "error": str(exc),
        }

    loaded = {module["name"].casefold() for module in modules}
    return {
        "status": "collected",
        "requested_modules": list(requested),
        "modules": modules,
        "missing_modules": [name for name in requested if name.casefold() not in loaded],
    }


def is_process_running(process_name: str) -> bool:
    if os.name != "nt":
        return bool(get_process_status(process_name).get("running"))
    windows_api_status = _get_process_status_windows_api(process_name)
    if windows_api_status is not None:
        return bool(windows_api_status.get("running"))
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = subprocess.SW_HIDE
    try:
        result = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {process_name}", "/FO", "CSV", "/NH"],
            check=False,
            capture_output=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
            startupinfo=startupinfo,
            timeout=2.0,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return process_name.casefold() in _subprocess_stdout_text(result).casefold()
