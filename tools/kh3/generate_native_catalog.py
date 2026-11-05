"""Build shared runtime catalog data from the canonical KH3 world modules."""
from __future__ import annotations

import json
from pathlib import Path

from catalog_imports import import_world_module

ROOT = Path(__file__).resolve().parents[2]
WORLD = ROOT / "worlds/kh3"


def catalog_data() -> dict:
    data = import_world_module("Data")
    extracted = data.load_extracted_data()
    ids = data.build_item_name_to_id(extracted)
    tokens = dict(data._build_runtime_token_to_item_name())
    tokens[data.ULTIMA_WEAPON_SHARD_RUNTIME_TOKEN] = data.ULTIMA_WEAPON_SHARD_ITEM_NAME
    payload = {
        "items": {str(item_id): {"name": data.resolve_item_display_name(name),
                   "token": data.resolve_item_runtime_token(name) or ""}
                  for name, item_id in sorted(ids.items(), key=lambda entry: entry[1])},
        "tokens": {token: {"name": name, "flags": data.resolve_runtime_flag_names(token),
                           "munny": data.MUNNY_POUCH_MUNNY_AMOUNT_BY_NAME.get(name, 0)}
                   for token, name in sorted(tokens.items())},
        "vbonus_bundles": [{"tokens": list(bundle), "source_id": source}
                          for bundle, source in sorted(data._build_vbonus_bundle_lookup().items())],
        "event_flags": json.loads((WORLD / "data/event_flags.json").read_text()) ["flags"],
        "vbonus_flags": json.loads((WORLD / "data/vbonus_flags.json").read_text()) ["flags"],
    }
    return payload


def chest_data() -> dict:
    data = import_world_module("Data")
    palettes = json.loads((WORLD / "data/keyblade_chest_palettes.json").read_text(encoding="utf-8"))
    for name, palette in palettes.items():
        palette["token"] = data.resolve_item_runtime_token(name)
    chests = {
        row["source_id"]: {"table": row["source_table"], "location_id": 0x4B483300 + index}
        for index, row in enumerate(data.normalize_locations(data.load_extracted_data()))
        if row["type"] == "treasure"
    }
    payload = {
        "palettes": palettes, "chests": chests,
        "door_keyblades": sorted(data.PORTAL_KEYBLADE_ITEM_NAMES),
        "songs": {str(row["song_id"]): row["name"] for row in json.loads(
            (WORLD / "data/mom_songs.json").read_text(encoding="utf-8"))["songs"]},
        "placements": {row["placement_id"]: row for row in json.loads(
            (WORLD / "data/music_chest_placements.json").read_text(encoding="utf-8"))["placements"]},
    }
    return payload


def location_data() -> dict:
    # Match the catalog and checksum used by server and player seed writers.
    rows, digest = import_world_module("SlotData")._catalog()
    return {"sha256": digest, "locations": {str(key): value for key, value in rows.items()}}


def main() -> None:
    # The APWorld exports data. Native source generation is owned by the DLL project.
    from export_dll_inputs import main as export_main
    export_main()


if __name__ == "__main__":
    main()
