"""Reversible, hash-pinned EGS MoM installation; no game assemblies or saves are edited."""
from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import time
import urllib.parse
import zipfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from ..bridge_support.files import atomic_write_json
from .mom_release import MANAGED_FILES, download_mod

MOM_EXE = "KINGDOM HEARTS Melody of Memory.exe"
LOADER_VERSION = "0.7.3"
LOADER_URL = f"https://github.com/LavaGang/MelonLoader/releases/download/v{LOADER_VERSION}/MelonLoader.x64.zip"
LOADER_SHA256 = "5b2b2f3d1cd42b59ec886c5bdc2663edae87a0097a4f4a8f58c0965a99dda416"
MOD_PATH = "Mods/MoMController.dll"
STATE_DIR = ".kh3ap-mom"


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def safe_path(root: Path, name: str) -> Path:
    parts = PurePosixPath(name).parts
    if not parts or PurePosixPath(name).is_absolute() or any(p in ("..", ".") or ":" in p or "\\" in p for p in parts):
        raise ValueError(f"Unsafe installation path: {name}")
    target = root.joinpath(*parts)
    for path in (root, *[root.joinpath(*parts[:i]) for i in range(1, len(parts) + 1)]):
        if path.is_symlink() or (path.exists() and getattr(path.stat(), "st_file_attributes", 0) & 0x400):
            raise ValueError(f"Linked installation paths are not supported: {path}")
    return target


def validate_game(path: Path) -> Path:
    root = Path(os.path.abspath(path.expanduser()))
    if not safe_path(root, MOM_EXE).is_file() or not safe_path(root, "GameAssembly.dll").is_file():
        raise ValueError("Select the MoM folder containing KINGDOM HEARTS Melody of Memory.exe and GameAssembly.dll.")
    return root


def discover_installs(manifest_root: Path | None = None) -> list[tuple[Path, str]]:
    if os.name != "nt" and manifest_root is None:
        from .mom_linux_setup import discover
        return [(Path(row["game_dir"]), "kh3ap-linux-mom") for row in discover()]
    root = manifest_root or Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / "Epic/EpicGamesLauncher/Data/Manifests"
    found = {}
    for path in sorted(root.glob("*.item")):
        try:
            item = json.loads(path.read_text(encoding="utf-8-sig"))
            if not isinstance(item, dict):
                continue
            launch = item.get("LaunchExecutable")
            location = item.get("InstallLocation")
            app_name = item.get("AppName")
            if not all(isinstance(value, str) and value.strip() for value in (launch, location, app_name)):
                continue
            executable = Path(launch.replace("\\", "/"))
            install_root = Path(location)
            if executable.name.casefold() != MOM_EXE.casefold() or not install_root.is_absolute():
                continue
            # Epic records a launch path, not necessarily a bare filename. Use
            # its executable's folder so nested installations match the user's
            # MoM Dir and the same folder used by the mod installer.
            game = validate_game((install_root / executable).parent)
            app = urllib.parse.quote(app_name.strip(), safe="")
            found[str(game).casefold()] = (game, f"com.epicgames.launcher://apps/{app}?action=launch&silent=true")
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return list(found.values())


def _registered_install(game: Path, installs: list[tuple[Path, str]]) -> tuple[Path, str]:
    for root, uri in installs:
        if root == game:
            return root, uri
    detected = "; ".join(str(root) for root, _ in installs) or "none"
    raise RuntimeError(
        f"The KH3 client could not match MoM folder '{game}' to an Epic launch record. "
        f"Detected MoM folders: {detected}. Use Detect beside MoM Dir in KH3 Config. "
        "If this folder launches from Epic, share the client log and MoM Epic manifest for diagnosis."
    )


def selected_install() -> tuple[Path, str]:
    import Utils
    selected = Utils.persistent_load().get("kh3_client", {}).get("mom_dir")
    installs = discover_installs()
    if selected:
        game = validate_game(Path(selected))
        return _registered_install(game, installs)
    if len(installs) != 1:
        raise RuntimeError("Select your MoM installation in the KH3 Config tab.")
    return installs[0]


def require_closed(root: Path) -> None:
    if os.name != "nt":
        from .mom_linux import require_closed as require_owned_closed
        require_owned_closed()
        # One snapshot only when installation is explicitly requested. Wine's
        # process name may be truncated, so inspect exact argv paths as well.
        target = (root / MOM_EXE).resolve()
        for entry in Path("/proc").iterdir():
            if not entry.name.isdecimal():
                continue
            try:
                args = (entry / "cmdline").read_bytes().split(b"\0")
                cwd = (entry / "cwd").resolve(strict=True)
                for raw in args:
                    arg = os.fsdecode(raw)
                    if arg and ((cwd / arg).resolve() == target or arg.replace("\\", "/").rsplit("/", 1)[-1].casefold() == MOM_EXE.casefold()):
                        raise RuntimeError("Close Melody of Memory before changing its installation.")
            except (OSError, ValueError):
                continue
        return
    import psutil
    for process in psutil.process_iter(["name", "exe"]):
        if (process.info.get("name") or "").casefold() == MOM_EXE.casefold():
            exe = process.info.get("exe")
            if not exe or Path(exe).resolve() == (root / MOM_EXE).resolve():
                raise RuntimeError("Close Melody of Memory, then click again.")


def loader_files(progress=lambda message: None, cache: Path | None = None) -> dict[str, bytes]:
    cache = cache or Path(os.environ.get("LOCALAPPDATA", tempfile.gettempdir())) / "KH3AP/cache"
    cache.mkdir(parents=True, exist_ok=True)
    archive = cache / f"MelonLoader-{LOADER_VERSION}-{LOADER_SHA256[:12]}.zip"
    if not archive.is_file() or digest(archive.read_bytes()) != LOADER_SHA256:
        progress("Downloading MelonLoader 0.7.3 (20 MB)…")
        # Reuse system trust and the frozen-Linux host downloader. Creating
        # an SSL context inside an AppImage worker can also crash bundled OpenSSL.
        from ..PakGenerator import _download_file

        with tempfile.TemporaryDirectory(prefix=".mom-download-", dir=cache) as staging:
            downloaded = Path(staging) / "MelonLoader.zip"
            try:
                _download_file(LOADER_URL, downloaded)
            except OSError as exc:
                raise RuntimeError(f"Could not download MelonLoader; nothing was installed. {exc}") from exc
            with downloaded.open("rb") as response:
                data = response.read(40 * 1024 * 1024 + 1)
        if len(data) > 40 * 1024 * 1024 or digest(data) != LOADER_SHA256:
            raise RuntimeError("MelonLoader download failed SHA-256 verification; nothing was installed.")
        atomic_bytes(archive, data)
    progress("Verifying MelonLoader files…")
    result = {}
    with zipfile.ZipFile(archive) as source:
        if sum(info.file_size for info in source.infolist()) > 160 * 1024 * 1024:
            raise ValueError("Unexpected MelonLoader archive size.")
        for info in source.infolist():
            if info.is_dir():
                continue
            name = info.filename
            safe_path(cache, name)
            if name.startswith("MelonLoader/") or name in ("version.dll", "dobby.dll"):
                if name.casefold() in {key.casefold() for key in result}:
                    raise ValueError("Duplicate MelonLoader archive entry.")
                result[name] = source.read(info)
    if not all(name in result for name in ("version.dll", "MelonLoader/net6/MelonLoader.dll")):
        raise ValueError("MelonLoader archive is incomplete.")
    return result


def atomic_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=".mom-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


@contextmanager
def install_lock(root: Path):
    """OS-held lock releases even if a client crashes, unlike a stale lock marker."""
    directory = safe_path(root, STATE_DIR)
    directory.mkdir(exist_ok=True)
    with safe_path(root, f"{STATE_DIR}/lock").open("a+b") as stream:
        stream.seek(0)
        if os.name == "nt":
            import msvcrt
            try:
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise RuntimeError("Another client is changing this MoM installation.") from exc
        else:
            import fcntl
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)


def read_state(root: Path) -> dict | None:
    path = safe_path(root, f"{STATE_DIR}/installation.json")
    if not path.exists():
        return None
    state = json.loads(path.read_text(encoding="utf-8"))
    if state.get("schema") != 1 or state.get("game_dir") != str(root) or not isinstance(state.get("files"), dict):
        raise RuntimeError("Invalid MoM installation record; no files were changed.")
    for name, entry in state["files"].items():
        safe_path(root, name)
        if not (name in MANAGED_FILES or name.startswith("MelonLoader/") or name in ("version.dll", "dobby.dll")):
            raise ValueError("Invalid managed file in MoM installation record.")
        if entry.get("backup"):
            if entry["backup"] != f"{STATE_DIR}/backups/{name}":
                raise ValueError("Invalid MoM backup path.")
            safe_path(root, entry["backup"])
    return state


def install_integration(root: Path, kh3_root: Path, progress=lambda message: None) -> str:
    """Install the mod and configure the host's challenge launch integration."""
    if sys.platform == "linux":
        from .mom_linux_setup import setup
        return setup(root, kh3_root, progress)
    return install(root, progress)


def install(root: Path, progress=lambda message: None, *, payload=None, loader=None) -> str:
    root = validate_game(root)
    require_closed(root)
    mod_files, metadata = payload or download_mod(progress, loader_version=LOADER_VERSION)
    files = loader if loader is not None else loader_files(progress)
    with install_lock(root):
        require_closed(root)
        state = read_state(root)
        if state is None:
            state = {"schema": 1, "game_dir": str(root), "files": {}, "loader_version": LOADER_VERSION}
            core = safe_path(root, "MelonLoader/net6/MelonLoader.dll")
            if core.exists():
                if digest(core.read_bytes()) != digest(files["MelonLoader/net6/MelonLoader.dll"]):
                    raise RuntimeError("An existing, different MelonLoader version is installed. This client requires 0.7.3 and will not replace another loader.")
                # Compatible third-party loader: validate its proxy, then reuse all of it.
                for name in ("version.dll", "dobby.dll"):
                    if name in files and (not safe_path(root, name).is_file() or digest(safe_path(root, name).read_bytes()) != digest(files[name])):
                        raise RuntimeError("The existing MelonLoader proxy differs or is incomplete; repair that loader first.")
                files = {}
            elif any(safe_path(root, name).exists() for name in files):
                raise RuntimeError("Existing loader files conflict with installation. Nothing was overwritten.")
        else:
            # A reused loader remains outside our ownership during Repair.
            files = {name: data for name, data in files.items() if name in state["files"]}
        files = {**files, **mod_files}
        for name, data in files.items():
            target = safe_path(root, name)
            entry = state["files"].get(name)
            # A verified payload may already have been copied by the local build
            # helper. Adopt it into the journal; unknown replacements stay protected.
            if entry and target.exists() and digest(target.read_bytes()) not in [
                digest(data), entry["sha256"], entry.get("original_sha256"), *entry.get("previous_hashes", []),
            ]:
                raise RuntimeError(f"{name} changed outside this installer. It was preserved.")
        # Back up and journal the entire plan before replacing a single file.
        for name, data in files.items():
            if name not in state["files"]:
                target = safe_path(root, name)
                entry = {"backup": None, "original_sha256": None}
                if target.exists():
                    original = target.read_bytes()
                    entry.update(backup=f"{STATE_DIR}/backups/{name}", original_sha256=digest(original))
                    atomic_bytes(safe_path(root, entry["backup"]), original)
                state["files"][name] = entry
            entry = state["files"][name]
            if entry.get("sha256") and entry["sha256"] != digest(data):
                entry["previous_hashes"] = list(set([*entry.get("previous_hashes", []), entry["sha256"]]))
            entry["sha256"] = digest(data)
        state.update(status="installing", mod_version=metadata["version"])
        journal = safe_path(root, f"{STATE_DIR}/installation.json")
        atomic_write_json(journal, state)
        try:
            progress("Installing MoM integration…")
            for name, data in files.items():
                atomic_bytes(safe_path(root, name), data)
            state["status"] = "installed"
            atomic_write_json(journal, state)
        except Exception:
            # The durable journal makes Uninstall recover partial installations.
            progress("Installation interrupted. Click Uninstall to restore the previous files, or Repair to retry.")
            raise
    return "Installed. The first MoM launch prepares MelonLoader automatically; it may take longer than usual."


def uninstall(root: Path, progress=lambda message: None) -> str:
    root = validate_game(root)
    require_closed(root)
    with install_lock(root):
        state = read_state(root)
        if state is None:
            return "No client-managed installation to remove. Existing mods were left intact."
        other_mods = any(p.is_file() and p.suffix.casefold() == ".dll" and p.relative_to(root).as_posix() != MOD_PATH
                         for folder in ("Mods", "Plugins") for p in safe_path(root, folder).rglob("*"))
        preserve_loader = other_mods or bool(state["files"].get(MOD_PATH, {}).get("backup"))
        # Verify all backups before mutating the installation.
        for entry in state["files"].values():
            if entry.get("backup") and digest(safe_path(root, entry["backup"]).read_bytes()) != entry["original_sha256"]:
                raise RuntimeError("A MoM backup failed verification; nothing was removed.")
        progress("Removing client-managed MoM files…")
        remaining = {}
        for name, entry in state["files"].items():
            if preserve_loader and name not in MANAGED_FILES:
                continue
            target = safe_path(root, name)
            current = digest(target.read_bytes()) if target.exists() else None
            if current not in [None, entry["sha256"], entry.get("original_sha256"), *entry.get("previous_hashes", [])]:
                remaining[name] = entry
                continue
            if entry.get("backup"):
                atomic_bytes(target, safe_path(root, entry["backup"]).read_bytes())
            else:
                target.unlink(missing_ok=True)
        journal = safe_path(root, f"{STATE_DIR}/installation.json")
        if remaining:
            state.update(files=remaining, status="modified_files_preserved")
            atomic_write_json(journal, state)
            return "Integration removed where unchanged. Preserved externally modified files: " + ", ".join(remaining)
        journal.unlink()
        return ("Previous MoM setup restored. Existing loader/mods and saves were preserved." if preserve_loader
                else "MoM integration uninstalled. Saves, results, and other user data were preserved.")


@dataclass(frozen=True)
class InstallationStatus:
    ok: bool
    message: str


def status(root: Path) -> InstallationStatus:
    root = validate_game(root)
    state = read_state(root)
    mod = safe_path(root, MOD_PATH)
    loader = safe_path(root, "MelonLoader/net6/MelonLoader.dll").is_file() and safe_path(root, "version.dll").is_file()
    if not state or not mod.is_file() or not loader:
        return InstallationStatus(False, "MoM integration needs Install / Repair.")
    if state.get("status") != "installed":
        return InstallationStatus(False, "Incomplete or modified installation — Install / Repair or Uninstall.")
    if any(not safe_path(root, name).is_file() or digest(safe_path(root, name).read_bytes()) != entry["sha256"]
           for name, entry in state["files"].items()):
        return InstallationStatus(False, "Managed installation files are missing or changed — Install / Repair.")
    return InstallationStatus(True, f"MoM integration {state['mod_version']} installed. Install / Repair verifies this client's pinned release.")



def test_launch(root: Path, progress=lambda message: None, timeout: float = 180) -> str:
    import psutil
    root = validate_game(root)
    require_closed(root)
    state = read_state(root)
    if not state or not status(root).ok:
        raise RuntimeError("Install / Repair the MoM integration before testing it.")
    version = state["mod_version"]
    _, match = _registered_install(root, discover_installs())
    if not safe_path(root, MOD_PATH).is_file():
        raise RuntimeError("Install the integration first.")
    pending = safe_path(root, "UserData/MoMSongLaunch-request.json")
    if pending.exists():
        raise RuntimeError("A song launch is pending. Finish that launch before testing installation.")
    started = time.time()
    os.startfile(match)
    progress("MoM is starting through Epic. First-launch setup can take a few minutes…")
    ready = safe_path(root, "UserData/MoMInstallReady.json")
    while time.time() - started < timeout:
        try:
            if ready.stat().st_mtime >= started:
                record = json.loads(ready.read_text(encoding="utf-8-sig"))
                process = psutil.Process(record["process_id"])
                if (record.get("schema") == 1 and record.get("version") == version
                        and process.create_time() >= started - 3
                        and Path(process.exe()).resolve() == (root / MOM_EXE).resolve()):
                    return "Test passed: our mod loaded successfully. MoM is open for controller testing."
        except (OSError, ValueError, KeyError, psutil.Error):
            pass
        time.sleep(.5)
    raise RuntimeError("MoM did not report ready within three minutes. Open Logs for details; first-launch setup may still be running.")
