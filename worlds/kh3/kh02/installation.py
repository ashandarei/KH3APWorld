"""Discover and select the local EGS or Steam KH0.2 source installation."""
from __future__ import annotations

import hashlib
import json
import os
import re
import struct
from datetime import datetime
from dataclasses import dataclass
from pathlib import Path

if __name__ == "__main__" and not __package__:
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
    __package__ = "worlds.kh3.kh02"

GAME = "KINGDOM HEARTS 0.2 Birth by Sleep"
EXE = GAME + ".exe"
COLLECTION = "KINGDOM HEARTS HD 2.8 Final Chapter Prologue"
selection_revision = 0
BUILDS = {
    "steam": (0x666D9EB7, 0x4F5F000, 76522024, "a5945af5041047f54db67d7667390f4dc90eee59d1c06459434ddab57d0f339c"),
    "egs": (0x6662E5CD, 0x4EFC000, 76122896, "e4a9e731be7b58012dc8f189f3a033c0828aba4116405dba08122c934a17fa05"),
}


@dataclass(frozen=True)
class Installation:
    root: Path
    platform: str

    @property
    def executable(self) -> Path:
        return self.root / "Binaries/Win64" / EXE

    @property
    def bridge(self) -> Path:
        return self.executable.parent / "kh02_bridge"


def normalize_root(path: Path) -> Path:
    path = path.expanduser().resolve()
    if path.name.casefold() == EXE.casefold():
        path = path.parent
    if path.name.casefold() == "kh02_bridge":
        path = path.parent
    if path.name.casefold() == "win64" and path.parent.name.casefold() == "binaries":
        path = path.parent.parent
    elif path.name.casefold() == "binaries":
        path = path.parent
    if (path / GAME / "Binaries/Win64" / EXE).is_file():
        path /= GAME
    return path


def inspect_install(path: Path, *, verify: bool = False) -> Installation:
    root = normalize_root(path)
    executable = root / "Binaries/Win64" / EXE
    with executable.open("rb") as stream:
        header = stream.read(64)
        if len(header) != 64 or header[:2] != b"MZ":
            raise ValueError("Invalid KH0.2 executable.")
        offset = struct.unpack_from("<I", header, 60)[0]
        if not 64 <= offset <= 0x1000:
            raise ValueError("Invalid KH0.2 PE header.")
        stream.seek(offset)
        pe = stream.read(84)
        if len(pe) != 84 or pe[:4] != b"PE\0\0":
            raise ValueError("Invalid KH0.2 PE header.")
        identity = (struct.unpack_from("<I", pe, 8)[0], struct.unpack_from("<I", pe, 80)[0])
        for platform, (timestamp, image_size, size, digest) in BUILDS.items():
            if identity == (timestamp, image_size):
                if verify:
                    stream.seek(0)
                    if executable.stat().st_size != size or hashlib.file_digest(stream, "sha256").hexdigest() != digest:
                        raise ValueError("KH0.2 executable integrity check failed. Verify the installation in its store.")
                return Installation(root, platform)
    raise ValueError("Unsupported KH0.2 build. Select the supported English EGS or Steam installation.")


def discover_installs(*, source: str = "auto", manifest_root: Path | None = None,
                      steamapps_dirs: list[Path] | None = None) -> list[Installation]:
    if source not in ("auto", "egs", "steam"):
        raise ValueError("Unknown KH0.2 store.")
    candidates = []
    if source in ("auto", "egs"):
        manifests = manifest_root or Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / "Epic/EpicGamesLauncher/Data/Manifests"
        for path in sorted(manifests.glob("*.item")):
            try:
                manifest = json.loads(path.read_text(encoding="utf-8-sig"))
                if manifest.get("DisplayName") == COLLECTION:
                    candidates.append((Path(manifest["InstallLocation"]), "egs"))
            except (OSError, ValueError, KeyError, TypeError):
                continue
    if source in ("auto", "steam"):
        if steamapps_dirs is None:
            from ..platform_support.game_discovery import _steam_library_steamapps_dirs
            steamapps_dirs = _steam_library_steamapps_dirs()
        for steamapps in steamapps_dirs:
            try:
                fields = dict(re.findall(r'"(appid|installdir)"\s+"([^"]*)"',
                                         (steamapps / "appmanifest_2552440.acf").read_text(encoding="utf-8-sig")))
                if fields.get("appid") == "2552440" and fields.get("installdir"):
                    candidates.append((steamapps / "common" / fields["installdir"], "steam"))
            except OSError:
                continue
    found = {}
    for root, platform in candidates:
        try:
            install = inspect_install(root)
            if install.platform == platform:
                found[str(install.root).casefold()] = install
        except (OSError, ValueError):
            continue
    return list(found.values())


def selected_install() -> Installation | None:
    import Utils
    selected = os.environ.get("KH02_DIR") or Utils.persistent_load().get("kh3_client", {}).get("kh02_dir")
    if selected:
        return inspect_install(Path(selected))
    installs = discover_installs()
    return installs[0] if installs else None  # EGS first; explicit selection always wins.


def default_bridge_root() -> Path:
    explicit = os.environ.get("KH02_BRIDGE_ROOT")
    if explicit:
        return Path(explicit)
    install = selected_install()
    return install.bridge if install else Path.home() / "Games" / GAME / "Binaries/Win64/kh02_bridge"


def sync_runtime(kh3_dir: Path, install: Installation | None = None) -> None:
    from ..bridge_support.files import atomic_write_json
    from ..installer_support.paths import wine_visible_path_text
    from ..platform_support.game_discovery import kh3_win64_dir_from_root, normalize_kh3_root_dir
    from .host_runtime import provision_host
    from ..installer_support.platform_config import ensure_proton_kh02_dwmapi_override
    from .linux_source_access import source_executable_for_host
    install = install or selected_install()
    if install is None:
        return
    inspect_install(install.root, verify=True)
    host = normalize_kh3_root_dir(kh3_dir)
    override = ensure_proton_kh02_dwmapi_override(host, datetime.now().strftime("%Y%m%d-%H%M%S"))
    if override.get("errors"):
        raise OSError("Could not configure the KH0.2 companion loader: " + "; ".join(override["errors"]))
    target = kh3_win64_dir_from_root(host) / "kh02_portal_config.json"
    executable = source_executable_for_host(host, install.executable)
    if executable != install.executable:
        # Verify the same supported original build through the actual shared path.
        shared = inspect_install(executable, verify=True)
        if shared.platform != install.platform:
            raise OSError("The shared KH0.2 installation does not match the selected store.")
    payload = {"source_executable": wine_visible_path_text(executable), "source_platform": install.platform}
    if target.is_file():
        existing = json.loads(target.read_text(encoding="utf-8-sig"))
        if all(existing.get(key) == value for key, value in payload.items()):
            payload = None
        else:
            payload = existing | payload
    provision_host(host)
    if payload is not None:
        atomic_write_json(target, payload)


def save_selection(path: Path, kh3_dir: Path) -> Installation:
    global selection_revision
    import Utils
    install = inspect_install(path, verify=True)
    sync_runtime(kh3_dir, install)
    Utils.persistent_store("kh3_client", "kh02_dir", str(install.root))
    selection_revision += 1
    return install


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", choices=("auto", "egs", "steam"))
    parser.add_argument("--path", type=Path)
    parser.add_argument("--field", choices=("root", "executable", "bridge", "platform"))
    args = parser.parse_args()
    if args.path:
        installs = [inspect_install(args.path, verify=True)]
    elif args.source:
        installs = discover_installs(source=args.source)
    else:
        selected = selected_install()
        installs = [selected] if selected else []
    if not installs:
        parser.error("No supported KH0.2 installation found.")
    if args.field:
        inspect_install(installs[0].root, verify=True)
        print(getattr(installs[0], args.field))
    else:
        print(json.dumps([dict(root=str(i.root), executable=str(i.executable), platform=i.platform) for i in installs], indent=2))


if __name__ == "__main__":
    main()
