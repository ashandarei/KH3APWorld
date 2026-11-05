"""Preserve player configuration while installing KH3's UObject pool minimum."""

from __future__ import annotations

import os
import re
import shutil
import tempfile
from datetime import datetime
from pathlib import Path

MIN_UOBJECT_CAPACITY = 2_097_152
SECTION = "/Script/Engine.GarbageCollectionSettings"
KEY = "gc.MaxObjectsInGame"
_SECTION = re.compile(r"^\s*\[([^]]+)\]\s*(?:[;#].*)?$")
_KEY = re.compile(r"^\s*gc\.MaxObjectsInGame\s*=\s*([^;#]*)(?:[;#].*)?$", re.I)


def engine_ini_path(save_dir: Path) -> Path:
    """Use the selected account, including redirected Documents/Proton paths."""
    save_dir = Path(save_dir)
    account = save_dir.parent
    if (save_dir.name.casefold() != "savegames"
            or account.parent.name.casefold() not in {"steam", "epic games store"}
            or not re.fullmatch(r"[0-9a-fA-F]{16,32}", account.name)):
        raise ValueError("Select the KH3 account's SaveGames folder before installing Engine.ini.")
    return account / "Config" / "Engine.ini"


def _decode(data: bytes) -> tuple[str, str]:
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16"), "utf-16"
    if data.startswith(b"\xef\xbb\xbf"):
        return data.decode("utf-8-sig"), "utf-8-sig"
    # Do not silently replace undecodable bytes in unrelated player settings.
    try:
        return data.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        return data.decode("cp1252"), "cp1252"


def _entries(text: str) -> list[tuple[int, int | None]]:
    inside = False
    entries = []
    for index, line in enumerate(text.splitlines()):
        section = _SECTION.fullmatch(line)
        if section:
            inside = section[1].casefold() == SECTION.casefold()
        elif inside and (match := _KEY.fullmatch(line)):
            try:
                value = int(match[1].strip())
            except ValueError:
                value = None
            entries.append((index, value))
    return entries


def inspect_uobject_config(save_dir: Path) -> dict:
    try:
        path = engine_ini_path(save_dir)
        if not path.exists():
            return {"status": "missing", "path": str(path), "configured_capacity": None}
        text, _ = _decode(path.read_bytes())
        entries = _entries(text)
        value = entries[-1][1] if entries else None
        return {"status": "ready" if value is not None and value >= MIN_UOBJECT_CAPACITY else "needs_patch",
                "path": str(path), "configured_capacity": value}
    except (OSError, ValueError, UnicodeError) as exc:
        return {"status": "unavailable", "error": str(exc), "configured_capacity": None}


def install_uobject_config(save_dir: Path) -> dict:
    path = engine_ini_path(save_dir)
    if path.is_symlink():
        raise ValueError(f"Refusing to replace a linked Engine.ini: {path}")
    exists = path.exists()
    original = path.read_bytes() if exists else b""
    text, encoding = _decode(original)
    entries = _entries(text)
    # A later duplicate must not lower a player's deliberately higher limit.
    target = max([MIN_UOBJECT_CAPACITY, *(v for _, v in entries if v is not None)])
    if entries and all(v == target for _, v in entries):
        return {"status": "ready", "path": str(path), "configured_capacity": target,
                "changed": False, "backup": None}
    newline = "\r\n" if "\r\n" in text or not text else "\n"
    lines = text.splitlines(keepends=True)
    if entries:
        indices = {i for i, _ in entries}
        lines = [f"{KEY}={target}{newline}" if i == entries[0][0] else line
                 for i, line in enumerate(lines) if i not in indices or i == entries[0][0]]
        updated = "".join(lines)
    else:
        # Append to the last matching section, or create one if absent.
        section_index = None
        for i, line in enumerate(lines):
            section = _SECTION.fullmatch(line.rstrip("\r\n"))
            if section and section[1].casefold() == SECTION.casefold():
                section_index = i
        if section_index is not None:
            if not lines[section_index].endswith(("\r", "\n")):
                lines[section_index] += newline
            lines.insert(section_index + 1, f"{KEY}={target}{newline}")
            updated = "".join(lines)
        else:
            updated = text + (newline if text and not text.endswith(("\r", "\n")) else "")
            updated += f"[{SECTION}]{newline}{KEY}={target}{newline}"
    path.parent.mkdir(parents=True, exist_ok=True)
    backup = None
    if exists:
        backup = path.with_name(path.name + ".apbak-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f"))
        shutil.copy2(path, backup)
    temp = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix="Engine.ini.", suffix=".tmp", delete=False) as stream:
            temp = Path(stream.name)
            stream.write(updated.encode(encoding))
        os.replace(temp, path)
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)
    return {"status": "ready", "path": str(path), "configured_capacity": target,
            "changed": True, "backup": str(backup) if backup else None}
