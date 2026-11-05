"""Install the KH0.2 source DLL with a reversible, narrowly scoped file journal."""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from ..bridge_support.files import atomic_write_json
from ..installer_support.resources import _runtime_resource
from ..platform_support.process_status import is_process_running
from .installation import EXE, inspect_install

MOD = "Mods/KH02APProbeCpp/dlls/main.dll"
MODS = "Mods/mods.txt"
STATE = "kh02-client-install"
SETUP = ("MemberVariableLayout.ini", "VTableLayout.ini", "UE4SS-settings.ini")
# Recognize preceding installer journals so their backups remain reversible.
# New installs never create a catalog; the companion discovers its index in RAM.
RETIRED_CATALOG = "kh02_asset_catalog.json"
MANAGED = {MOD, MODS, *SETUP, RETIRED_CATALOG, "override.txt", "UE4SS.dll", "dwmapi.dll"}


def safe_path(root: Path, name: str) -> Path:
    if name not in MANAGED and name not in {STATE, f"{STATE}/lock", f"{STATE}/installation.json"} and not (
            name.startswith(f"{STATE}/backups/") and name.removeprefix(f"{STATE}/backups/") in MANAGED):
        raise ValueError("Invalid KH0.2 installation path.")
    path = root / name
    path.resolve().relative_to(root.resolve())
    return path


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def atomic_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".kh02-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def require_closed() -> None:
    if is_process_running(EXE):
        raise RuntimeError("Close KH0.2 (and KH3 if it is using KH0.2) before changing the KH0.2 DLL.")


@contextmanager
def locked(root: Path):
    safe_path(root, STATE).mkdir(exist_ok=True)
    with safe_path(root, f"{STATE}/lock").open("a+b") as stream:
        stream.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            require_closed()
            yield
        finally:
            if os.name == "nt":
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)


def read_state(root: Path) -> dict | None:
    journal = safe_path(root, f"{STATE}/installation.json")
    if not journal.exists():
        return None
    state = json.loads(journal.read_text(encoding="utf-8"))
    if state.get("schema") != 1 or state.get("root") != str(root) or not isinstance(state.get("files"), dict):
        raise ValueError("Invalid KH0.2 installation record.")
    for name, entry in state["files"].items():
        if name not in MANAGED:
            raise ValueError("Invalid KH0.2 managed file.")
        safe_path(root, name)
        if entry.get("backup"):
            if entry["backup"] != f"{STATE}/backups/{name}":
                raise ValueError("Invalid KH0.2 backup path.")
            safe_path(root, entry["backup"])
    return state


def mod_list(data: bytes, enabled: bool) -> bytes:
    text = data.decode("utf-8-sig")
    pattern = re.compile(r"[ \t]*KH02APProbeCpp[ \t]*:[ \t]*[01][ \t]*", re.IGNORECASE)
    replacement = f"KH02APProbeCpp : {int(enabled)}"
    newline = "\r\n" if "\r\n" in text else "\n"
    lines = []
    replaced = False
    for line in text.splitlines(keepends=True):
        if pattern.fullmatch(line.rstrip("\r\n")):
            if not replaced:
                lines.append(replacement + newline)
                replaced = True
        else:
            lines.append(line)
    result = "".join(lines)
    if enabled and not replaced:
        result = result.rstrip("\r\n") + (newline if result else "") + replacement + newline
    return result.encode("utf-8")


def payload(root: Path) -> dict[str, bytes]:
    from .runtime_release import companion_bytes
    resources = {name: _runtime_resource("runtime", "KH02", name) for name in SETUP}
    missing = [name for name, resource in resources.items() if not resource.is_file()]
    if missing:
        raise FileNotFoundError("KH0.2 runtime payload is missing: " + ", ".join(missing))
    files = {name: resource.read_bytes() for name, resource in resources.items()}
    files[MOD] = companion_bytes(_runtime_resource)
    # With override.txt selecting the executable directory, UE4SS's default
    # Mods path follows the loaded source even through a Linux document portal.
    files["override.txt"] = b".\n"
    # Verify existing loaders against the same release pins as KH3 before any game writes.
    from ..RuntimeDownload import DLL_NAMES, RELEASE_CONFIG_NAME, stage_runtime
    for name in DLL_NAMES:
        safe_path(root, name)
    config = json.loads(_runtime_resource("runtime", "Win64", RELEASE_CONFIG_NAME).read_bytes())
    with tempfile.TemporaryDirectory(prefix="kh02-loader-") as directory:
        staging = Path(directory)
        release = stage_runtime(config, root, staging)
        files.update({name: (staging / name).read_bytes() for name in release["downloaded"]})
    return files


def install(path: Path) -> str:
    root = inspect_install(path, verify=True).executable.parent
    require_closed()
    files = payload(root)
    with locked(root):
        state = read_state(root) or {"schema": 1, "root": str(root), "files": {}}
        mods = safe_path(root, MODS)
        files[MODS] = mod_list(mods.read_bytes() if mods.exists() else b"", True)
        # Check the complete plan before changing anything; a repair must not overwrite another mod.
        for name, data in files.items():
            target = safe_path(root, name)
            entry = state["files"].get(name)
            if entry and name != MODS and target.exists() and digest(target.read_bytes()) not in (
                    entry["sha256"], entry.get("original_sha256"), digest(data), *entry.get("previous_hashes", [])):
                raise RuntimeError(f"{name} changed outside this installer; it was preserved.")
        for name, data in files.items():
            if name not in state["files"]:
                target = safe_path(root, name)
                entry = {"backup": None, "original_sha256": None}
                if target.exists():
                    original = target.read_bytes()
                    entry.update(backup=f"{STATE}/backups/{name}", original_sha256=digest(original))
                    atomic_bytes(safe_path(root, entry["backup"]), original)
                state["files"][name] = entry
            entry = state["files"][name]
            if entry.get("sha256") and entry["sha256"] != digest(data):
                entry["previous_hashes"] = list(set([*entry.get("previous_hashes", []), entry["sha256"]]))
            entry["sha256"] = digest(data)
        journal = safe_path(root, f"{STATE}/installation.json")
        atomic_write_json(journal, state)
        for name, data in files.items():
            atomic_bytes(safe_path(root, name), data)
        retired = state["files"].get(RETIRED_CATALOG)
        catalog = safe_path(root, RETIRED_CATALOG)
        if retired and catalog.exists() and digest(catalog.read_bytes()) == retired["sha256"]:
            catalog.unlink()
    return "KH0.2 DLL installed. Changes apply on the next game launch."


def uninstall(path: Path) -> str:
    root = inspect_install(path, verify=True).executable.parent
    require_closed()
    with locked(root):
        state = read_state(root)
        if state is None:
            # Also allow removing a DLL installed by the local developer/standalone helper.
            target = safe_path(root, MOD)
            if target.exists():
                backup = target.with_name("main.dll.uninstalled-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f"))
                target.rename(backup)
            mods = safe_path(root, MODS)
            if mods.exists():
                atomic_bytes(mods, mod_list(mods.read_bytes(), False))
            return "KH0.2 DLL uninstalled. Existing loader files and other mods were preserved."
        for entry in state["files"].values():
            if entry.get("backup") and digest(safe_path(root, entry["backup"]).read_bytes()) != entry["original_sha256"]:
                raise RuntimeError("KH0.2 backup verification failed; no files were removed.")
        remaining = {}
        # Other mods may now depend on the shared loader/settings. Keep those files when present.
        mods_dir = safe_path(root, MODS).parent
        other_mods = any(p.is_dir() and p.name != "KH02APProbeCpp" for p in mods_dir.iterdir()) if mods_dir.exists() else False
        for name, entry in state["files"].items():
            target = safe_path(root, name)
            current = target.read_bytes() if target.exists() else None
            if name == MODS and current is not None and digest(current) != entry["sha256"]:
                atomic_bytes(target, mod_list(current, False))
                continue
            if other_mods and name not in (MOD, MODS, RETIRED_CATALOG):
                continue
            if current is not None and digest(current) not in (
                    entry["sha256"], entry.get("original_sha256"), *entry.get("previous_hashes", [])):
                remaining[name] = entry
                continue
            if name == MOD:
                # Keep old DLLs in backups, but do not reactivate one on Uninstall.
                target.unlink(missing_ok=True)
            elif entry.get("backup"):
                original = safe_path(root, entry["backup"]).read_bytes()
                atomic_bytes(target, mod_list(original, False) if name == MODS else original)
            else:
                target.unlink(missing_ok=True)
        journal = safe_path(root, f"{STATE}/installation.json")
        if remaining:
            state["files"] = remaining
            atomic_write_json(journal, state)
            return "KH0.2 uninstall preserved externally changed files: " + ", ".join(remaining)
        journal.unlink()
    return "KH0.2 installation removed; previous files restored."
