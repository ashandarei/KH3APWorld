"""XInput prerequisites: Microsoft's installer on Windows, Wine built-ins on Linux."""

from __future__ import annotations

import ctypes
import hashlib
import json
import logging
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from ..RuntimeDownload import _read_url

logger = logging.getLogger("Client")
DIRECTX_URL = (
    "https://download.microsoft.com/download/8/4/a/84a35bf1-dafe-4ae8-82af-ad2ae20b6b14/"
    "directx_Jun2010_redist.exe"
)
DIRECTX_SIZE = 100275120
DIRECTX_SHA256 = "053f76dcbb28802e23341b6a787e3b0791c0fa5c8d4d011b1044172dbf89c73b"
LEGACY_XINPUT_SHA256 = "756cad002e1553cfa1a91ebe8c1b9380ffabe0b4b1916c4a4db802396ddfbef8"
KH3_STEAM_APP_ID = "2552450"


def windows_xinput_path() -> Path:
    # A 32-bit AP client must check the DLL used by the 64-bit game, bypassing
    # Windows' System32 -> SysWOW64 filesystem redirection.
    directory = "Sysnative" if sys.maxsize <= 2**32 and os.environ.get("PROCESSOR_ARCHITEW6432") else "System32"
    return Path(os.environ.get("SystemRoot", r"C:\Windows")) / directory / "xinput1_3.dll"


def _is_x64_dll(path: Path) -> bool:
    try:
        with path.open("rb") as source:
            header = source.read(64)
            if len(header) != 64 or header[:2] != b"MZ":
                return False
            offset = int.from_bytes(header[60:64], "little")
            if not 64 <= offset <= 1024 * 1024:
                return False
            source.seek(offset)
            return source.read(6) == b"PE\0\0\x64\x86"
    except OSError:
        return False


def windows_xinput_status() -> dict[str, Any]:
    """Cheap local inspection; signature verification belongs to Patch Game."""
    path = windows_xinput_path()
    ready = _is_x64_dll(path)
    return {"platform": "windows", "ready": ready, "path": str(path),
            "reason": "ok" if ready else "missing_or_wrong_architecture"}


def _verify_microsoft_signature(path: Path) -> None:
    # Pass the filename as data, never interpolate it into PowerShell code.
    script = (
        "$ErrorActionPreference = 'Stop'; "
        "$s = Get-AuthenticodeSignature -LiteralPath $env:KH3AP_SIGNATURE_FILE; "
        "if ($s.Status -ne 'Valid' -or !$s.SignerCertificate -or "
        "$s.SignerCertificate.Subject -notmatch '(?:^|,\\s*)O=Microsoft Corporation(?:,|$)') "
        "{ throw 'A valid Microsoft Corporation signature is required' }"
    )
    # A client started from PowerShell 7 can inherit its module path, which is
    # incompatible with Windows PowerShell's security module. Let 5.1 set its own.
    environment = {key: value for key, value in os.environ.items() if key.casefold() != "psmodulepath"}
    environment["KH3AP_SIGNATURE_FILE"] = str(path)
    powershell = windows_xinput_path().parent / "WindowsPowerShell/v1.0/powershell.exe"
    result = subprocess.run(
        [str(powershell), "-NoProfile", "-NonInteractive", "-Command", script],
        env=environment,
        capture_output=True, timeout=60, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if result.returncode:
        raise RuntimeError(f"Microsoft signature verification failed for {path.name}")


def directx_cache_path() -> Path:
    import Utils
    return Path(Utils.user_path("kh3", "cache", "directx_Jun2010_redist.exe")).resolve()


def _verified_package(path: Path) -> bool:
    if not path.is_file() or path.stat().st_size != DIRECTX_SIZE:
        return False
    with path.open("rb") as source:
        digest = hashlib.sha256()
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest() == DIRECTX_SHA256


def _directx_package() -> Path:
    package = directx_cache_path()
    if not _verified_package(package):
        logger.info("XInput: downloading Microsoft's DirectX June 2010 runtime (95.6 MB).")
        data = _read_url(DIRECTX_URL, DIRECTX_SIZE)
        if len(data) != DIRECTX_SIZE or hashlib.sha256(data).hexdigest() != DIRECTX_SHA256:
            raise RuntimeError("DirectX download failed size/SHA-256 verification")
        package.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=package.parent, prefix=".directx-", delete=False) as output:
                temporary = Path(output.name)
                output.write(data)
            _verify_microsoft_signature(temporary)
            os.replace(temporary, package)
            temporary = None
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    _verify_microsoft_signature(package)
    return package


def _run_directx_setup(setup: Path) -> None:
    """Show the normal EULA/setup UI, request UAC, and wait for its real result."""
    from ctypes import wintypes

    class ShellExecuteInfo(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD), ("fMask", wintypes.ULONG), ("hwnd", wintypes.HWND),
            ("lpVerb", wintypes.LPCWSTR), ("lpFile", wintypes.LPCWSTR),
            ("lpParameters", wintypes.LPCWSTR), ("lpDirectory", wintypes.LPCWSTR),
            ("nShow", ctypes.c_int), ("hInstApp", wintypes.HINSTANCE),
            ("lpIDList", ctypes.c_void_p), ("lpClass", wintypes.LPCWSTR),
            ("hkeyClass", wintypes.HKEY), ("dwHotKey", wintypes.DWORD),
            ("hIcon", wintypes.HANDLE), ("hProcess", wintypes.HANDLE),
        ]

    shell = ctypes.WinDLL("shell32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    ole = ctypes.WinDLL("ole32", use_last_error=True)
    shell.ShellExecuteExW.argtypes = [ctypes.POINTER(ShellExecuteInfo)]
    shell.ShellExecuteExW.restype = wintypes.BOOL
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel.GetExitCodeProcess.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    ole.CoInitializeEx.argtypes = [ctypes.c_void_p, wintypes.DWORD]
    ole.CoInitializeEx.restype = wintypes.LONG
    ole.CoUninitialize.argtypes = []
    ole.CoUninitialize.restype = None
    info = ShellExecuteInfo()
    info.cbSize = ctypes.sizeof(info)
    info.fMask = 0x40 | 0x100  # SEE_MASK_NOCLOSEPROCESS | SEE_MASK_NOASYNC
    info.lpVerb = "runas"
    info.lpFile = str(setup)
    info.lpDirectory = str(setup.parent)
    info.nShow = 1  # SW_SHOWNORMAL: the player must see and accept Microsoft's EULA.
    # ShellExecuteEx can activate COM shell extensions. The GUI worker normally
    # has no COM apartment; preserve an existing one if already initialized.
    com_result = ole.CoInitializeEx(None, 0x2 | 0x4) & 0xFFFFFFFF
    if com_result not in (0, 1, 0x80010106):  # S_OK, S_FALSE, RPC_E_CHANGED_MODE
        raise RuntimeError(f"Could not initialize Windows setup (HRESULT {com_result:#x})")
    try:
        if not shell.ShellExecuteExW(ctypes.byref(info)):
            error = ctypes.get_last_error()
            if error == 1223:
                raise RuntimeError("DirectX setup was cancelled at the administrator prompt")
            raise ctypes.WinError(error)
        if not info.hProcess:
            raise RuntimeError("Could not track the DirectX setup process")
        # Waiting occurs in the Patch Game worker; keep extraction alive until setup exits.
        if kernel.WaitForSingleObject(info.hProcess, 0xFFFFFFFF) != 0:
            raise ctypes.WinError(ctypes.get_last_error())
        code = wintypes.DWORD()
        if not kernel.GetExitCodeProcess(info.hProcess, ctypes.byref(code)):
            raise ctypes.WinError(ctypes.get_last_error())
        if code.value:
            raise RuntimeError(f"DirectX setup failed or was cancelled (exit code {code.value})")
    finally:
        if info.hProcess:
            kernel.CloseHandle(info.hProcess)
        if com_result in (0, 1):
            ole.CoUninitialize()


def ensure_windows_xinput() -> dict[str, Any]:
    status = windows_xinput_status()
    if status["ready"]:
        _verify_microsoft_signature(windows_xinput_path())
        return {**status, "installed_now": False}
    package = _directx_package()
    with tempfile.TemporaryDirectory(prefix="kh3-directx-") as temporary:
        extracted = Path(temporary)
        result = subprocess.run(
            [str(package), "/Q", f"/T:{extracted}"], cwd=extracted, timeout=120,
            capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if result.returncode:
            raise RuntimeError(f"Could not extract the DirectX runtime (exit code {result.returncode})")
        setup = extracted / "DXSETUP.exe"
        if not setup.is_file():
            raise RuntimeError("The DirectX package did not contain DXSETUP.exe")
        _verify_microsoft_signature(setup)
        logger.info("XInput: accept Microsoft's DirectX license and complete setup to continue Patch Game.")
        _run_directx_setup(setup)
    status = windows_xinput_status()
    if not status["ready"]:
        raise RuntimeError("DirectX setup finished without installing the required 64-bit XInput 1.3 runtime")
    _verify_microsoft_signature(windows_xinput_path())
    return {**status, "installed_now": True}


def _read_config(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _check_heroic_overrides(settings: dict) -> None:
    options = settings.get("enviromentOptions", [])
    if not isinstance(options, list):
        return
    for option in options:
        if not isinstance(option, dict) or option.get("key") != "WINEDLLOVERRIDES":
            continue
        for group in str(option.get("value", "")).split(";"):
            names, separator, mode = group.partition("=")
            if separator and any(name.strip().casefold() in ("xinput1_3", "xinput1_3.dll", "*")
                                 for name in names.split(",")) and mode.strip().casefold() not in ("b", "builtin"):
                raise ValueError("Remove the conflicting XInput WINEDLLOVERRIDES setting in Heroic for KH3, then retry.")


def wine_prefix_for_kh3(root: Path) -> Path | None:
    """Find only a prefix associated with this selected Steam/Heroic installation."""
    path_type = type(root)
    root = root.expanduser().resolve()
    steamapps = next((path for path in (root, *root.parents) if path.name.casefold() == "steamapps"), None)
    if steamapps is not None:
        expected = steamapps / "compatdata" / KH3_STEAM_APP_ID / "pfx"
        if (expected / "user.reg").is_file():
            return expected
        # Steam can keep compatdata in its primary library for an external game library.
        home = path_type.home()
        candidates = {path.resolve() for base in (
            home / ".local/share/Steam", home / ".steam/root", home / ".steam/steam",
            home / ".var/app/com.valvesoftware.Steam/.local/share/Steam",
        ) if ((path := base / "steamapps/compatdata" / KH3_STEAM_APP_ID / "pfx") / "user.reg").is_file()}
        if len(candidates) > 1:
            raise ValueError("Multiple KH3 Proton prefixes were found; select the library containing the active prefix.")
        return next(iter(candidates), expected)

    home = path_type.home()
    config_home = path_type(os.environ.get("XDG_CONFIG_HOME", str(home / ".config")))
    config_roots = (config_home, home / ".var/app/com.heroicgameslauncher.hgl/config")
    candidates = set()
    for config_root in config_roots:
        heroic = config_root / "heroic"
        for database in (heroic / "legendaryConfig/legendary", config_root / "legendary"):
            for app, item in _read_config(database / "installed.json").items():
                if not isinstance(app, str) or not app or "/" in app or "\\" in app or app in (".", ".."):
                    continue
                install = item.get("install_path") if isinstance(item, dict) else None
                if not isinstance(install, str) or not path_type(install).is_absolute():
                    continue
                installed = path_type(install).resolve()
                if root != installed and root != installed / "KINGDOM HEARTS III":
                    continue
                global_config = _read_config(heroic / "config.json")
                defaults = global_config.get("defaultSettings", global_config)
                game_config = _read_config(heroic / "GamesConfig" / f"{app}.json")
                if game_config.get("version", "v0") not in ("v0", "v0.1"):
                    raise ValueError("Unsupported Heroic configuration version for KH3")
                game_settings = game_config.get(app, {})
                settings = {**(defaults if isinstance(defaults, dict) else {}),
                            **(game_settings if isinstance(game_settings, dict) else {})}
                _check_heroic_overrides(settings)
                prefix = settings.get("winePrefix")
                if isinstance(prefix, str) and prefix and path_type(prefix).expanduser().is_absolute():
                    path = path_type(prefix).expanduser().resolve()
                    if (path / "user.reg").is_file():
                        candidates.add(path)
                    elif (path / "pfx/user.reg").is_file():
                        candidates.add(path / "pfx")
    if len(candidates) > 1:
        raise ValueError("Multiple Heroic prefixes match this KH3 installation; remove the stale configuration and retry.")
    return next(iter(candidates), None)
