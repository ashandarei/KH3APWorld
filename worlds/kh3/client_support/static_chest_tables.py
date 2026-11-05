"""Verify Garden contains the fixed chest suppression tables used by new seeds."""
from __future__ import annotations

import hashlib
import json
import pkgutil
from pathlib import Path

from ..Pak import PakReader


def validate_static_chest_tables(kh3_dir: Path) -> None:
    from .goa_download import KH3_GOA_MOD_FILE_NAME

    raw = pkgutil.get_data(__package__.rsplit(".", 1)[0], "data/static_chest_tables.json")
    if raw is None:
        raise RuntimeError("The KH3 world is missing its static chest table manifest.")
    manifest = json.loads(raw)
    reader = PakReader(kh3_dir / "Content" / "Paks" / "~mods" / KH3_GOA_MOD_FILE_NAME)
    prefix = "../../../KINGDOM HEARTS III/"
    for name, expected in manifest["files"].items():
        full_name = prefix + name
        if not full_name.startswith(reader.mount_point):
            raise RuntimeError("Garden has an unexpected pak mount point.")
        entry = full_name[len(reader.mount_point):]
        if entry not in reader.entries or hashlib.sha256(reader.read(entry)).hexdigest() != expected:
            raise RuntimeError("Garden needs updating: its AP chest suppression tables are missing or outdated.")
