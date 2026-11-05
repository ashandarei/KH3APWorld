"""Static English tracker labels; no game inspection or per-message file reads."""

import json

from .PackageResources import read_package_text

_REFERENCE = json.loads(read_package_text("data/minimap_reference.json", module_file=__file__))
WORLD_NAMES = _REFERENCE["world_names"]
MINIMAP_REFERENCE = _REFERENCE["minimaps"]


def minimap_display_name(minimap_id: str, world_code: str) -> str:
    entry = MINIMAP_REFERENCE.get(minimap_id)
    if entry and entry["world_code"] == world_code:
        return entry["name"]
    return f"Unknown minimap ({minimap_id})" if minimap_id else "Unknown minimap"
