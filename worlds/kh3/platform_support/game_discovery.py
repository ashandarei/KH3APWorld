"""Read-only Steam and Epic installation discovery for Kingdom Hearts III."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

try:
    import winreg
except ImportError:  # pragma: no cover - only available on Windows
    winreg = None

from ..client_support.settings import (
    DEFAULT_KH3_ROOT_DIR,
    KH3ClientSettings,
    load_kh3_client_settings,
)
from ..installer_support.paths import (
    EGS_GUOBJECT_ARRAY_SIGNATURE,
    kh3_executable_contains_signature,
    ue4ss_path_specific_config_dir,
)

EPIC_MANIFEST_DIR = Path(r"C:\ProgramData\Epic\EpicGamesLauncher\Data\Manifests")
KH3_EXECUTABLE_NAME = "KINGDOM HEARTS III.exe"
KH3_STEAM_APP_ID = "2552450"
KH3_STEAM_INSTALL_DIR_NAME = "KINGDOM HEARTS III"
KH3_EPIC_APP_NAMES = {
    "kh_3",
    "kingdomheartsiii",
    "kingdomhearts3",
}


def normalize_kh3_root_dir(path: Path) -> Path:
    """Normalize Win64, Binaries, and outer Steam folders to the game root."""

    path = path.expanduser()
    if path.name.lower() == "win64" and path.parent.name.lower() == "binaries":
        return path.parent.parent.resolve()
    if path.name.lower() == "binaries":
        return path.parent.resolve()
    if (path.name.lower() == "kingdom hearts iii" or
            not (kh3_win64_dir_from_root(path) / KH3_EXECUTABLE_NAME).is_file()):
        nested_root = path / "KINGDOM HEARTS III"
        if kh3_win64_dir_from_root(nested_root).is_dir():
            return nested_root.resolve()
    return path.resolve()


def kh3_win64_dir_from_root(kh3_root_dir: Path) -> Path:
    return kh3_root_dir / "Binaries" / "Win64"


def kh3_mods_dir_from_root(kh3_root_dir: Path) -> Path:
    return kh3_root_dir / "Content" / "Paks" / "~mods"


def kh3_probe_config_from_root(kh3_root_dir: Path) -> Path:
    return kh3_win64_dir_from_root(kh3_root_dir) / "Mods" / "KH3APProbeCpp" / "config.lua"


def kh3_ue4ss_runtime_dirs_from_root(kh3_root_dir: Path) -> list[Path]:
    win64_dir = kh3_win64_dir_from_root(kh3_root_dir)
    path_specific_dir = ue4ss_path_specific_config_dir(kh3_root_dir)
    candidates = [win64_dir / "ue4ss"]
    if path_specific_dir is not None:
        candidates.append(path_specific_dir)
    candidates.extend((win64_dir / "KINGDOM HEARTS III", win64_dir))

    unique: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = os.path.normcase(str(candidate))
        if key in seen:
            continue
        seen.add(key)
        unique.append(candidate)
    return unique


def kh3_ue4ss_log_from_root(kh3_root_dir: Path) -> Path:
    candidates = [runtime_dir / "UE4SS.log" for runtime_dir in kh3_ue4ss_runtime_dirs_from_root(kh3_root_dir)]
    existing = [candidate for candidate in candidates if candidate.exists()]
    if existing:
        return max(existing, key=lambda path: path.stat().st_mtime)
    return candidates[0]


def resolve_kh3_executable_path(kh3_root_dir: Path) -> Path:
    """Resolve the executable from a normalized, nested, or direct root."""

    kh3_root_dir = normalize_kh3_root_dir(kh3_root_dir)
    candidates = [
        kh3_win64_dir_from_root(kh3_root_dir) / KH3_EXECUTABLE_NAME,
        kh3_win64_dir_from_root(kh3_root_dir / "KINGDOM HEARTS III") / KH3_EXECUTABLE_NAME,
        kh3_root_dir / KH3_EXECUTABLE_NAME,
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    return candidates[0].resolve()


def _paths_equal(left: Path, right: Path) -> bool:
    return os.path.normcase(str(left.resolve())) == os.path.normcase(str(right.resolve()))


def _epic_manifest_dirs(settings: KH3ClientSettings | None = None) -> list[Path]:
    settings = settings or load_kh3_client_settings()
    if settings.kh3_epic_manifest_dir is not None:
        return [settings.kh3_epic_manifest_dir.expanduser()]
    return [EPIC_MANIFEST_DIR]


def resolve_epic_launch_uri_for_kh3_executable(
    executable_path: Path,
    settings: KH3ClientSettings | None = None,
) -> str | None:
    """Return the Epic launch URI whose manifest executable matches KH3."""

    for manifest_dir in _epic_manifest_dirs(settings):
        if not manifest_dir.is_dir():
            continue
        for manifest_path in manifest_dir.glob("*.item"):
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
            except (OSError, json.JSONDecodeError):
                continue
            app_name = manifest.get("AppName") or manifest.get("MainGameAppName")
            install_location = manifest.get("InstallLocation")
            launch_executable = manifest.get("LaunchExecutable")
            if not (
                isinstance(app_name, str)
                and app_name.strip()
                and isinstance(install_location, str)
                and isinstance(launch_executable, str)
            ):
                continue
            manifest_executable = Path(install_location) / Path(launch_executable)
            try:
                if _paths_equal(manifest_executable, executable_path):
                    return f"com.epicgames.launcher://apps/{app_name.strip()}?action=launch&silent=true"
            except OSError:
                continue
    if os.name != "nt":
        return _heroic_launch_uri_for_executable(executable_path)
    return None


def _heroic_launch_uri_for_executable(executable_path: Path) -> str | None:
    """Use Heroic's registered launcher for the exact installed executable."""
    home = Path.home()
    config_home = Path(os.environ.get("XDG_CONFIG_HOME", str(home / ".config")))
    for config_root in (config_home, home / ".var/app/com.heroicgameslauncher.hgl/config"):
        database = config_root / "heroic/legendaryConfig/legendary/installed.json"
        try:
            installed = json.loads(database.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(installed, dict):
            continue
        for app_name, item in installed.items():
            if not isinstance(app_name, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", app_name):
                continue
            if not isinstance(item, dict):
                continue
            root, executable = item.get("install_path"), item.get("executable")
            if not isinstance(root, str) or not Path(root).is_absolute() or not isinstance(executable, str):
                continue
            try:
                if _paths_equal(Path(root) / executable.replace("\\", "/"), executable_path):
                    return f"heroic://launch?appName={app_name}&runner=legendary"
            except OSError:
                continue
    return None


def _steam_install_dir_from_executable(executable_path: Path) -> tuple[Path, str] | None:
    try:
        executable_path = executable_path.resolve()
    except OSError:
        return None
    for steamapps_dir in executable_path.parents:
        if steamapps_dir.name.casefold() != "steamapps":
            continue
        common_dir = steamapps_dir / "common"
        try:
            relative_path = executable_path.relative_to(common_dir)
        except ValueError:
            continue
        if relative_path.parts:
            return steamapps_dir, relative_path.parts[0]
    return None


def resolve_steam_launch_uri_for_kh3_executable(executable_path: Path) -> str | None:
    """Return the Steam app launch URI associated with the executable."""

    steam_install = _steam_install_dir_from_executable(executable_path)
    if steam_install is None:
        return None
    steamapps_dir, install_dir_name = steam_install
    for manifest_path in steamapps_dir.glob("appmanifest_*.acf"):
        try:
            manifest_text = manifest_path.read_text(encoding="utf-8-sig")
        except OSError:
            continue
        fields = dict(re.findall(r'"(appid|installdir)"\s+"([^"]*)"', manifest_text))
        app_id = fields.get("appid")
        install_dir = fields.get("installdir")
        if app_id and install_dir and install_dir.casefold() == install_dir_name.casefold():
            return f"steam://run/{app_id}"
    if install_dir_name.casefold() == "kingdom hearts iii":
        return f"steam://run/{KH3_STEAM_APP_ID}"
    return None


def _steamapps_dir_for_executable(executable_path: Path) -> Path | None:
    steam_install = _steam_install_dir_from_executable(executable_path)
    return steam_install[0] if steam_install is not None else None


def _path_key(path: Path) -> str:
    try:
        resolved = path.expanduser().resolve()
    except OSError:
        resolved = path.expanduser()
    return os.path.normcase(str(resolved))


def _steam_client_root_for_steamapps_dir(
    steamapps_dir: Path,
    settings: KH3ClientSettings | None = None,
) -> Path:
    direct_root = steamapps_dir.parent
    if (direct_root / "steam.exe").is_file() or (direct_root / "config" / "loginusers.vdf").is_file():
        return direct_root

    target_key = _path_key(steamapps_dir)
    for primary_steamapps_dir in _default_steamapps_dirs(settings):
        primary_root = primary_steamapps_dir.parent
        if _path_key(primary_steamapps_dir) == target_key:
            return primary_root
        for library_path in _steam_library_paths_for_steamapps_dir(primary_steamapps_dir):
            library_steamapps = library_path / "steamapps"
            if _path_key(library_steamapps) == target_key:
                return primary_root
    return direct_root


def _steam_root_for_executable(
    executable_path: Path,
    settings: KH3ClientSettings | None = None,
) -> Path | None:
    steamapps_dir = _steamapps_dir_for_executable(executable_path)
    if steamapps_dir is None:
        return None
    return _steam_client_root_for_steamapps_dir(steamapps_dir, settings)


def _unique_existing_dirs(candidates: list[Path]) -> list[Path]:
    unique: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        try:
            resolved = candidate.expanduser().resolve()
        except OSError:
            resolved = candidate.expanduser()
        key = os.path.normcase(str(resolved))
        if key in seen or not resolved.is_dir():
            continue
        seen.add(key)
        unique.append(resolved)
    return unique


def _windows_steam_registry_roots() -> list[Path]:
    """Return existing Steam client roots recorded by Windows registry installation keys."""

    if os.name != "nt" or winreg is None:
        return []

    registry_keys = (
        (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam"),
    )
    registry_views = [0]
    for view_name in ("KEY_WOW64_64KEY", "KEY_WOW64_32KEY"):
        view = getattr(winreg, view_name, 0)
        if view and view not in registry_views:
            registry_views.append(view)

    candidates: list[Path] = []
    for hive, key_path in registry_keys:
        for view in registry_views:
            try:
                with winreg.OpenKey(hive, key_path, 0, winreg.KEY_READ | view) as key:
                    for value_name in ("SteamPath", "InstallPath", "SteamExe"):
                        try:
                            value, _value_type = winreg.QueryValueEx(key, value_name)
                        except OSError:
                            continue
                        if not isinstance(value, str) or not value.strip():
                            continue
                        candidate = Path(value.strip())
                        if value_name == "SteamExe":
                            candidate = candidate.parent
                        candidates.append(candidate)
            except OSError:
                continue

    return _unique_existing_dirs(candidates)


def _steam_library_paths_from_vdf(text: str) -> list[Path]:
    """Extract library roots from both current and legacy Steam VDF layouts."""

    raw_paths = re.findall(r'"path"\s+"([^"]+)"', text)
    if not raw_paths:
        raw_paths = re.findall(r'^\s*"\d+"\s+"([^"]+)"\s*$', text, flags=re.MULTILINE)

    paths: list[Path] = []
    seen: set[str] = set()
    for raw_path in raw_paths:
        library_path = Path(raw_path.replace("\\\\", "\\")).expanduser()
        key = os.path.normcase(str(library_path))
        if key in seen:
            continue
        seen.add(key)
        paths.append(library_path)
    return paths


def _steam_library_paths_for_steamapps_dir(steamapps_dir: Path) -> list[Path]:
    """Read current and fallback Steam libraryfolders.vdf locations for one client root."""

    vdf_paths = (
        steamapps_dir / "libraryfolders.vdf",
        steamapps_dir.parent / "config" / "libraryfolders.vdf",
    )
    library_paths: list[Path] = []
    seen: set[str] = set()
    for vdf_path in vdf_paths:
        try:
            text = vdf_path.read_text(encoding="utf-8-sig")
        except OSError:
            continue
        for library_path in _steam_library_paths_from_vdf(text):
            key = os.path.normcase(str(library_path))
            if key in seen:
                continue
            seen.add(key)
            library_paths.append(library_path)
    return library_paths


def _default_steamapps_dirs(settings: KH3ClientSettings | None = None) -> list[Path]:
    settings = settings or load_kh3_client_settings()
    candidates: list[Path] = []
    if os.name == "nt":
        candidates.extend(root / "steamapps" for root in _windows_steam_registry_roots())
        for env_name in ("PROGRAMFILES(X86)", "PROGRAMFILES"):
            value = os.environ.get(env_name)
            if value:
                candidates.append(Path(value) / "Steam" / "steamapps")
        candidates.extend(
            [
                Path(r"C:\Program Files (x86)\Steam\steamapps"),
                Path(r"C:\Program Files\Steam\steamapps"),
                Path(r"C:\Steam\steamapps"),
            ]
        )
    else:
        try:
            home = Path.home()
        except RuntimeError:
            home = Path(".")
        candidates.extend(
            [
                home / ".steam" / "steam" / "steamapps",
                home / ".local" / "share" / "Steam" / "steamapps",
                home / ".var" / "app" / "com.valvesoftware.Steam" / ".local" / "share" / "Steam" / "steamapps",
            ]
        )
        if os.uname().sysname == "Darwin":
            candidates.append(home / "Library" / "Application Support" / "Steam" / "steamapps")
    if settings.kh3_steamapps_dir is not None:
        candidates.insert(0, settings.kh3_steamapps_dir.expanduser())
    return _unique_existing_dirs(candidates)


def _steam_library_steamapps_dirs(settings: KH3ClientSettings | None = None) -> list[Path]:
    candidates = _default_steamapps_dirs(settings)
    for steamapps_dir in list(candidates):
        for library_path in _steam_library_paths_for_steamapps_dir(steamapps_dir):
            candidates.append(library_path / "steamapps")
    return _unique_existing_dirs(candidates)


def _kh3_root_from_steamapps_dir(steamapps_dir: Path) -> Path | None:
    manifest_path = steamapps_dir / f"appmanifest_{KH3_STEAM_APP_ID}.acf"
    install_dir_name = KH3_STEAM_INSTALL_DIR_NAME
    if manifest_path.is_file():
        try:
            manifest_text = manifest_path.read_text(encoding="utf-8-sig")
        except OSError:
            manifest_text = ""
        fields = dict(re.findall(r'"(appid|installdir)"\s+"([^"]*)"', manifest_text))
        if fields.get("appid") == KH3_STEAM_APP_ID and fields.get("installdir"):
            install_dir_name = fields["installdir"]
    candidate = steamapps_dir / "common" / install_dir_name
    root = normalize_kh3_root_dir(candidate)
    if resolve_kh3_executable_path(root).is_file():
        return root
    return None


def _epic_manifest_kh3_roots(settings: KH3ClientSettings | None = None) -> list[Path]:
    roots: list[Path] = []
    for manifest_dir in _epic_manifest_dirs(settings):
        if not manifest_dir.is_dir():
            continue
        for manifest_path in manifest_dir.glob("*.item"):
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
            except (OSError, json.JSONDecodeError):
                continue
            app_name = manifest.get("AppName") or manifest.get("MainGameAppName")
            catalog_namespace = manifest.get("CatalogNamespace")
            display_name = manifest.get("DisplayName")
            install_location = manifest.get("InstallLocation")
            launch_executable = manifest.get("LaunchExecutable")
            if not isinstance(install_location, str):
                continue
            identifiers = {
                str(value or "").replace(" ", "").replace("_", "").casefold()
                for value in (app_name, catalog_namespace, display_name)
            }
            launch_points_to_kh3 = isinstance(launch_executable, str) and (
                KH3_EXECUTABLE_NAME.casefold() in launch_executable.casefold()
            )
            if not identifiers.intersection(KH3_EPIC_APP_NAMES) and not launch_points_to_kh3:
                continue
            candidate_roots = [normalize_kh3_root_dir(Path(install_location))]
            if isinstance(launch_executable, str):
                manifest_executable = Path(install_location) / Path(launch_executable)
                candidate_roots.insert(0, normalize_kh3_root_dir(manifest_executable.parent))
            for root in candidate_roots:
                if resolve_kh3_executable_path(root).is_file():
                    roots.append(root)
                    break
    return roots


def _default_epic_kh3_roots() -> list[Path]:
    candidates: list[Path] = []
    if os.name == "nt":
        for env_name in ("PROGRAMFILES", "PROGRAMFILES(X86)"):
            value = os.environ.get(env_name)
            if value:
                candidates.extend(
                    [
                        Path(value) / "Epic Games" / "KH_3",
                        Path(value) / "Epic Games" / "KINGDOM HEARTS III",
                    ]
                )
        candidates.extend(
            [
                Path(r"C:\Program Files\Epic Games\KH_3"),
                Path(r"C:\Program Files\Epic Games\KINGDOM HEARTS III"),
            ]
        )
    else:
        try:
            home = Path.home()
        except RuntimeError:
            home = Path(".")
        candidates.extend(
            [
                home / "Games" / "Heroic" / "KH_3",
                home / "Games" / "Heroic" / "KINGDOM HEARTS III",
                home / "Games" / "legendary" / "KH_3",
                home / "Games" / "legendary" / "KINGDOM HEARTS III",
            ]
        )

    roots: list[Path] = []
    for candidate in candidates:
        root = normalize_kh3_root_dir(candidate)
        if resolve_kh3_executable_path(root).is_file():
            roots.append(root)
    return roots


def detect_steam_kh3_install_dir(settings: KH3ClientSettings | None = None) -> Path | None:
    """Detect a Steam KH3 installation without mutating the system."""

    settings = settings or load_kh3_client_settings()
    for steamapps_dir in _steam_library_steamapps_dirs(settings):
        root = _kh3_root_from_steamapps_dir(steamapps_dir)
        if root is not None:
            return root

    if os.name == "nt":
        default_root = normalize_kh3_root_dir(DEFAULT_KH3_ROOT_DIR)
        if resolve_kh3_executable_path(default_root).is_file():
            return default_root
    return None


def detect_egs_kh3_install_dir(settings: KH3ClientSettings | None = None) -> Path | None:
    """Detect an Epic Games Store KH3 installation without mutating the system."""

    settings = settings or load_kh3_client_settings()
    for root in [*_epic_manifest_kh3_roots(settings), *_default_epic_kh3_roots()]:
        if resolve_kh3_executable_path(root).is_file():
            return normalize_kh3_root_dir(root)
    return None


def detect_kh3_install_dir(
    settings: KH3ClientSettings | None = None,
    *,
    source: str = "auto",
) -> Path | None:
    """Detect a KH3 installation, optionally limiting lookup to Steam or EGS."""

    settings = settings or load_kh3_client_settings()
    normalized_source = source.strip().casefold()
    if normalized_source == "auto":
        return detect_steam_kh3_install_dir(settings) or detect_egs_kh3_install_dir(settings)
    if normalized_source == "steam":
        return detect_steam_kh3_install_dir(settings)
    if normalized_source in {"egs", "epic"}:
        return detect_egs_kh3_install_dir(settings)
    raise ValueError(f"Unknown KH3 installation source: {source!r}")


def is_likely_steam_kh3_root_dir(kh3_dir: Path) -> bool:
    kh3_root = normalize_kh3_root_dir(kh3_dir)
    parts = {part.casefold() for part in kh3_root.parts}
    return "steamapps" in parts


def is_epic_kh3_root_dir(
    kh3_dir: Path,
    settings: KH3ClientSettings | None = None,
) -> bool:
    """Identify Epic roots by manifest first, then the executable signature."""

    kh3_root = normalize_kh3_root_dir(kh3_dir)
    if is_likely_steam_kh3_root_dir(kh3_root):
        return False
    executable = resolve_kh3_executable_path(kh3_root)
    if resolve_epic_launch_uri_for_kh3_executable(executable, settings) is not None:
        return True
    return kh3_executable_contains_signature(kh3_root, EGS_GUOBJECT_ARRAY_SIGNATURE)


__all__ = [
    "EPIC_MANIFEST_DIR",
    "KH3_EPIC_APP_NAMES",
    "KH3_EXECUTABLE_NAME",
    "KH3_STEAM_APP_ID",
    "KH3_STEAM_INSTALL_DIR_NAME",
    "detect_kh3_install_dir",
    "detect_egs_kh3_install_dir",
    "detect_steam_kh3_install_dir",
    "is_epic_kh3_root_dir",
    "is_likely_steam_kh3_root_dir",
    "kh3_mods_dir_from_root",
    "kh3_probe_config_from_root",
    "kh3_ue4ss_log_from_root",
    "kh3_ue4ss_runtime_dirs_from_root",
    "kh3_win64_dir_from_root",
    "normalize_kh3_root_dir",
    "resolve_epic_launch_uri_for_kh3_executable",
    "resolve_kh3_executable_path",
    "resolve_steam_launch_uri_for_kh3_executable",
]
