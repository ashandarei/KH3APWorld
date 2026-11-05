"""Detect and explicitly disable KH3's per-game Steam Cloud setting."""

from __future__ import annotations

import ctypes
import multiprocessing
import os
import re
import threading
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from ..client_support.settings import KH3ClientSettings, load_kh3_client_settings
from .game_discovery import (
    KH3_STEAM_APP_ID,
    _steam_root_for_executable,
    normalize_kh3_root_dir,
    resolve_kh3_executable_path,
)
from .save_discovery import STEAM_ACCOUNT_ID_RE, active_steam_account_id_for_kh3_root

_VDF_TOKEN_RE = re.compile(r'"((?:\\.|[^"\\])*)"|([{}])')
_STEAM_API_LOCK = threading.Lock()
_STEAM_API_HELPER_TIMEOUT_SECONDS = 15.0


class SteamCloudState(Enum):
    """Locally recorded per-game Steam Cloud state."""

    ENABLED = "enabled"
    DISABLED = "disabled"
    UNKNOWN = "unknown"


class SteamCloudDisableStatus(Enum):
    """Outcome of an explicit request to disable KH3 Steam Cloud."""

    DISABLED = "disabled"
    ALREADY_DISABLED = "already_disabled"
    NOT_STEAM = "not_steam"
    UNSUPPORTED_PLATFORM = "unsupported_platform"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class SteamCloudDisableResult:
    """Result returned to Patch Game without exposing Steam API details to the GUI."""

    status: SteamCloudDisableStatus
    detail: str = ""

    @property
    def succeeded(self) -> bool:
        return self.status in {
            SteamCloudDisableStatus.DISABLED,
            SteamCloudDisableStatus.ALREADY_DISABLED,
        }

    @property
    def changed(self) -> bool:
        return self.status is SteamCloudDisableStatus.DISABLED


def _decode_vdf_string(value: str) -> str:
    return re.sub(r'\\(["\\])', r'\1', value)


def _parse_vdf(text: str) -> dict[str, Any]:
    root: dict[str, Any] = {}
    stack = [root]
    pending_key: str | None = None

    for match in _VDF_TOKEN_RE.finditer(text):
        raw_string, brace = match.groups()
        if raw_string is not None:
            value = _decode_vdf_string(raw_string)
            if pending_key is None:
                pending_key = value
            else:
                stack[-1][pending_key] = value
                pending_key = None
            continue

        if brace == "{" and pending_key is not None:
            child: dict[str, Any] = {}
            stack[-1][pending_key] = child
            stack.append(child)
            pending_key = None
        elif brace == "}":
            if len(stack) > 1:
                stack.pop()
            pending_key = None

    return root


def _casefolded_value(mapping: dict[str, Any], key: str) -> Any:
    expected = key.casefold()
    for candidate, value in mapping.items():
        if candidate.casefold() == expected:
            return value
    return None


def _find_steam_app_config(value: Any, app_id: str) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None

    apps = _casefolded_value(value, "apps")
    if isinstance(apps, dict):
        app_config = _casefolded_value(apps, app_id)
        if isinstance(app_config, dict):
            return app_config

    for child in value.values():
        app_config = _find_steam_app_config(child, app_id)
        if app_config is not None:
            return app_config
    return None


def steam_userdata_account_id(steam_id: str) -> str | None:
    """Convert a SteamID64 from loginusers.vdf to its userdata directory id."""

    normalized = str(steam_id).strip()
    if not STEAM_ACCOUNT_ID_RE.fullmatch(normalized):
        return None
    return str(int(normalized) & 0xFFFFFFFF)


def steam_cloud_state_from_sharedconfig(text: str, app_id: str = KH3_STEAM_APP_ID) -> SteamCloudState:
    """Read an explicit per-game Cloud override from Steam's shared config."""

    app_config = _find_steam_app_config(_parse_vdf(text), app_id)
    if app_config is None:
        return SteamCloudState.UNKNOWN
    raw_value = _casefolded_value(app_config, "cloudenabled")
    if not isinstance(raw_value, str):
        return SteamCloudState.UNKNOWN
    normalized = raw_value.strip().casefold()
    if normalized in {"0", "false"}:
        return SteamCloudState.DISABLED
    if normalized in {"1", "true"}:
        return SteamCloudState.ENABLED
    return SteamCloudState.UNKNOWN


def detect_kh3_steam_cloud_state(
    kh3_root: Path,
    settings: KH3ClientSettings | None = None,
) -> SteamCloudState:
    """Detect KH3's explicit per-game Cloud state for the selected Steam user."""

    settings = settings or load_kh3_client_settings()
    executable_path = resolve_kh3_executable_path(kh3_root)
    steam_root = _steam_root_for_executable(executable_path, settings)
    if steam_root is None:
        return SteamCloudState.UNKNOWN

    steam_id = active_steam_account_id_for_kh3_root(kh3_root, settings)
    userdata_id = steam_userdata_account_id(steam_id or "")
    if userdata_id is None:
        return SteamCloudState.UNKNOWN

    shared_config = steam_root / "userdata" / userdata_id / "7" / "remote" / "sharedconfig.vdf"
    try:
        text = shared_config.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return SteamCloudState.UNKNOWN
    return steam_cloud_state_from_sharedconfig(text)


def _steam_api64_path_for_kh3_root(kh3_root: Path) -> Path | None:
    # Steam's KH3 depot keeps gameplay files in a second `KINGDOM HEARTS III`
    # directory, but the shared Engine files can remain in the outer install
    # directory. Other layouts place both trees under the normalized root.
    install_roots = (kh3_root, kh3_root.parent)
    candidates = [
        path
        for install_root in install_roots
        for path in (
            install_root / "Engine" / "Binaries" / "ThirdParty" / "Steamworks"
        ).glob("Steamv*/Win64/steam_api64.dll")
    ]

    def version(path: Path) -> int:
        match = re.fullmatch(r"Steamv(\d+)", path.parent.parent.name, flags=re.IGNORECASE)
        return int(match.group(1)) if match else -1

    return max(candidates, key=version, default=None)


def _remote_storage_interface(steam_api: Any) -> Any:
    for version in range(32, 0, -1):
        getter = getattr(steam_api, f"SteamAPI_SteamRemoteStorage_v{version:03d}", None)
        if getter is None:
            continue
        getter.argtypes = []
        getter.restype = ctypes.c_void_p
        return getter()
    return None


def _disable_cloud_with_loaded_steam_api(steam_api: Any) -> SteamCloudDisableResult:
    initialized = False
    try:
        initialize = steam_api.SteamAPI_Init
        initialize.argtypes = []
        initialize.restype = ctypes.c_bool
        initialized = bool(initialize())
        if not initialized:
            return SteamCloudDisableResult(
                SteamCloudDisableStatus.FAILED,
                "SteamAPI_Init returned false; make sure the Steam client is running and signed in.",
            )

        remote_storage = _remote_storage_interface(steam_api)
        if not remote_storage:
            return SteamCloudDisableResult(
                SteamCloudDisableStatus.FAILED,
                "Steam did not expose an ISteamRemoteStorage interface.",
            )

        is_enabled = steam_api.SteamAPI_ISteamRemoteStorage_IsCloudEnabledForApp
        is_enabled.argtypes = [ctypes.c_void_p]
        is_enabled.restype = ctypes.c_bool
        set_enabled = steam_api.SteamAPI_ISteamRemoteStorage_SetCloudEnabledForApp
        set_enabled.argtypes = [ctypes.c_void_p, ctypes.c_bool]
        set_enabled.restype = None

        was_enabled = bool(is_enabled(remote_storage))
        set_enabled(remote_storage, False)
        if bool(is_enabled(remote_storage)):
            return SteamCloudDisableResult(
                SteamCloudDisableStatus.FAILED,
                "Steam still reported KH3 Cloud as enabled after the disable request.",
            )
        return SteamCloudDisableResult(
            SteamCloudDisableStatus.DISABLED if was_enabled else SteamCloudDisableStatus.ALREADY_DISABLED
        )
    except (AttributeError, OSError, TypeError, ValueError) as exc:
        return SteamCloudDisableResult(SteamCloudDisableStatus.FAILED, str(exc))
    finally:
        if initialized:
            try:
                shutdown = steam_api.SteamAPI_Shutdown
                shutdown.argtypes = []
                shutdown.restype = None
                shutdown()
            except (AttributeError, OSError, TypeError, ValueError):
                pass


def _load_steam_api64(path: Path) -> Any:
    loader = getattr(ctypes, "WinDLL", None)
    if loader is None:
        raise OSError("This Python runtime cannot load the Windows Steam API library.")
    return loader(str(path))


def _steam_cloud_disable_worker(steam_api_path: str, result_sender: Any) -> None:
    """Load SteamAPI only in this short-lived child process."""

    previous_app_id = os.environ.get("SteamAppId")
    previous_game_id = os.environ.get("SteamGameId")
    os.environ["SteamAppId"] = KH3_STEAM_APP_ID
    os.environ["SteamGameId"] = KH3_STEAM_APP_ID
    try:
        try:
            steam_api = _load_steam_api64(Path(steam_api_path))
        except OSError as exc:
            result = SteamCloudDisableResult(SteamCloudDisableStatus.FAILED, str(exc))
        else:
            result = _disable_cloud_with_loaded_steam_api(steam_api)
    except Exception as exc:
        result = SteamCloudDisableResult(
            SteamCloudDisableStatus.FAILED,
            f"Steam Cloud helper error: {exc}",
        )
    finally:
        if previous_app_id is None:
            os.environ.pop("SteamAppId", None)
        else:
            os.environ["SteamAppId"] = previous_app_id
        if previous_game_id is None:
            os.environ.pop("SteamGameId", None)
        else:
            os.environ["SteamGameId"] = previous_game_id

    try:
        result_sender.send((result.status.value, result.detail))
    finally:
        result_sender.close()


def _disable_cloud_in_helper_process(
    steam_api_path: Path,
    timeout_seconds: float = _STEAM_API_HELPER_TIMEOUT_SECONDS,
) -> SteamCloudDisableResult:
    """Run SteamAPI in an isolated process so Steam never treats the AP client as KH3."""

    context = multiprocessing.get_context("spawn")
    result_receiver, result_sender = context.Pipe(duplex=False)
    process = context.Process(
        target=_steam_cloud_disable_worker,
        name="KH3SteamCloudHelper",
        args=(str(steam_api_path), result_sender),
    )
    try:
        process.start()
    except (OSError, RuntimeError) as exc:
        result_receiver.close()
        result_sender.close()
        return SteamCloudDisableResult(
            SteamCloudDisableStatus.FAILED,
            f"Could not start the Steam Cloud helper: {exc}",
        )

    # Only the child should retain the sending end. Closing the parent's copy
    # also lets poll/recv observe EOF immediately if the helper crashes.
    result_sender.close()
    try:
        if not result_receiver.poll(timeout_seconds):
            return SteamCloudDisableResult(
                SteamCloudDisableStatus.FAILED,
                "The Steam Cloud helper timed out.",
            )
        try:
            payload = result_receiver.recv()
        except EOFError:
            return SteamCloudDisableResult(
                SteamCloudDisableStatus.FAILED,
                "The Steam Cloud helper exited without returning a result.",
            )
        if not isinstance(payload, tuple) or len(payload) != 2:
            return SteamCloudDisableResult(
                SteamCloudDisableStatus.FAILED,
                "The Steam Cloud helper returned an invalid result.",
            )
        raw_status, raw_detail = payload
        try:
            status = SteamCloudDisableStatus(str(raw_status))
        except ValueError:
            return SteamCloudDisableResult(
                SteamCloudDisableStatus.FAILED,
                f"The Steam Cloud helper returned an unknown status: {raw_status!r}.",
            )
        return SteamCloudDisableResult(status, str(raw_detail or ""))
    except (OSError, RuntimeError) as exc:
        return SteamCloudDisableResult(
            SteamCloudDisableStatus.FAILED,
            f"Could not read the Steam Cloud helper result: {exc}",
        )
    finally:
        result_receiver.close()
        process.join(timeout=1.0)
        if process.is_alive():
            process.terminate()
            process.join(timeout=5.0)
        close_process = getattr(process, "close", None)
        if callable(close_process) and not process.is_alive():
            close_process()


def disable_kh3_steam_cloud(
    kh3_root: Path,
    settings: KH3ClientSettings | None = None,
) -> SteamCloudDisableResult:
    """Disable KH3 Cloud through Steamworks after the user invokes Patch Game."""

    settings = settings or load_kh3_client_settings()
    kh3_root = normalize_kh3_root_dir(Path(kh3_root))
    executable_path = resolve_kh3_executable_path(kh3_root)
    if _steam_root_for_executable(executable_path, settings) is None:
        return SteamCloudDisableResult(SteamCloudDisableStatus.NOT_STEAM)

    if detect_kh3_steam_cloud_state(kh3_root, settings) is SteamCloudState.DISABLED:
        return SteamCloudDisableResult(SteamCloudDisableStatus.ALREADY_DISABLED)
    if os.name != "nt":
        return SteamCloudDisableResult(
            SteamCloudDisableStatus.UNSUPPORTED_PLATFORM,
            "Automatic Steam Cloud disabling currently requires the Windows KH3 client.",
        )

    steam_api_path = _steam_api64_path_for_kh3_root(kh3_root)
    if steam_api_path is None:
        return SteamCloudDisableResult(
            SteamCloudDisableStatus.FAILED,
            "Could not find KH3's bundled steam_api64.dll.",
        )

    with _STEAM_API_LOCK:
        return _disable_cloud_in_helper_process(steam_api_path)


__all__ = [
    "SteamCloudDisableResult",
    "SteamCloudDisableStatus",
    "SteamCloudState",
    "disable_kh3_steam_cloud",
    "detect_kh3_steam_cloud_state",
    "steam_cloud_state_from_sharedconfig",
    "steam_userdata_account_id",
]
