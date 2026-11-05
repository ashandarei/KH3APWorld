"""Keep the legacy KH3 Modloader title-map pair above Steam's patch exports."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime
from pathlib import Path

MODLOADER_PAK_NAME = "Modloader_999_P.pak"
_LOADER_NAME = re.compile(r"(?:z+-)?modloader(?:_999_p)?\.pak", re.IGNORECASE)


def fix_modloader_pak_priority(mods_dir: Path, *, game_running: bool = False) -> tuple[str, ...]:
    """Rename known loader paks only; preserve identical duplicates as backups.

    The numeric patch suffix keeps TresGameTitle.umap and .uexp together.
    It does not set the runtime order of mods registered in the loader's menu.
    In particular, C-Mod_ModLoader.pak is a separate compatibility mod.
    """
    candidates = sorted(
        (path for path in mods_dir.rglob("*") if _LOADER_NAME.fullmatch(path.name) and path.is_file()),
        key=lambda path: str(path).casefold(),
    )
    if not candidates:
        return ()
    fixed = [path for path in candidates if path.name.casefold() == MODLOADER_PAK_NAME.casefold()]
    if len(candidates) == 1 and fixed:
        return ()
    if game_running:
        raise RuntimeError("Close KH3 before fixing the Modloader pak filename, then try again.")
    if any(path.is_symlink() for path in candidates):
        raise OSError("The Modloader pak is a symbolic link. Rename it in your mod manager to Modloader_999_P.pak.")
    # Validate every copy before changing any filename. Never choose between versions.
    hashes = {hashlib.sha256(path.read_bytes()).digest() for path in candidates}
    if len(hashes) != 1:
        raise RuntimeError(
            "Different Modloader pak copies are installed: "
            + ", ".join(str(path) for path in candidates)
            + ". Keep the intended version as Modloader_999_P.pak and move the other copies out of ~mods."
        )
    selected = fixed[0] if fixed else candidates[0]
    destination = selected.with_name(MODLOADER_PAK_NAME) if not fixed else selected
    messages = []
    if destination != selected:
        if destination.exists():
            raise FileExistsError(f"Cannot rename Modloader; destination already exists: {destination}")
        selected.rename(destination)
        messages.append(f"Renamed {selected.name} to {destination.name} to prevent the KH3 title-screen crash.")
    for duplicate in candidates:
        if duplicate == selected:
            continue
        backup = duplicate.with_name(duplicate.name + datetime.now().strftime(".%Y%m%d-%H%M%S-%f.bak"))
        if backup.exists():
            raise FileExistsError(f"Cannot preserve duplicate Modloader; backup already exists: {backup}")
        duplicate.rename(backup)
        messages.append(f"Preserved duplicate Modloader pak as {backup}.")
    return tuple(messages)
