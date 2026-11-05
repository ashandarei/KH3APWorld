"""Explicit APWorld-to-DLL data export; not part of a native DLL build."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from generate_native_catalog import catalog_data, chest_data, location_data
from generate_randomization_catalog import catalog_data as randomization_data
from local_environment import local_path


ROOT = Path(__file__).resolve().parents[2]


def runtime_inputs() -> dict:
    return {
        "schema": "kh3ap.runtime_inputs.v1",
        "catalog": catalog_data(),
        "chests": chest_data(),
        "locations": location_data(),
        "randomization": randomization_data(),
        "limits": json.loads((ROOT / "worlds/kh3/data/runtime_json_limits.json").read_text(encoding="utf-8")),
        "ui": ui_inputs(),
    }


def ui_inputs() -> dict:
    def read(name: str) -> dict:
        return json.loads((ROOT / "worlds/kh3/data" / name).read_text(encoding="utf-8"))

    return {
        "spots": read("ingredient_spots.json")["spots"],
        "rates": read("ingredient_spawn_rates.json")["spots"],
        "ingredients": read("ingredient_journal.json")["ingredients"],
        "adversaries": read("adversary_journal.json")["enemies"],
        "minimaps": {key: {field: value[field] for field in ("area_name", "map_name")}
                     for key, value in read("minimap_reference.json")["minimaps"].items()},
        "weapons": json.loads((ROOT / "worlds/kh3/data/alternate_weapons.json").read_text(encoding="utf-8")),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path,
                        default=local_path("KH3_RUNTIME_DIR", ROOT.parent / "KH3Runtime") / "data/runtime-inputs.json")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    expected = runtime_inputs()
    if args.check:
        if not args.output.exists() or json.loads(args.output.read_text(encoding="utf-8")) != expected:
            parser.exit(1, "DLL runtime input export is stale; run tools/kh3/export_dll_inputs.py\n")
        return
    text = json.dumps(expected, ensure_ascii=True, indent=2) + "\n"
    if not args.output.exists() or args.output.read_text(encoding="utf-8") != text:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
