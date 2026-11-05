"""Build the pickup catalog from inspect-properties dumps of cooked food maps.

Usage: python tools/kh3/generate_ingredient_spots.py --properties DIR --assets DIR
The assets directory contains the extracted Content/Levels tree; properties
contains one KH3UAssetTools inspect-properties .txt per .umap basename.
Outputs are deterministic. Never reorder a published catalog without an ID migration.
"""
import argparse
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]
WORLDS = {"HE": "Olympus", "TT": "Twilight Town", "TS": "Toy Box",
          "RA": "Kingdom of Corona", "MI": "Monstropolis", "FZ": "Arendelle",
          "CA": "The Caribbean", "BX": "San Fransokyo"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--properties", type=Path, required=True)
    parser.add_argument("--assets", type=Path, required=True)
    args = parser.parse_args()
    maps = {p.stem: "/Game/" + p.relative_to(args.assets).with_suffix("").as_posix()
            for p in args.assets.rglob("*.umap")}
    grouped = {}
    for dump in sorted(args.properties.glob("*.txt")):
        if dump.stem not in maps:
            continue
        # The seed can start after Little Chef's one-time collection tutorial.
        if "tutorial" in dump.stem.lower():
            continue
        for block in re.split(r"^EXPORT ", dump.read_text(encoding="utf-8-sig"), flags=re.M)[1:]:
            match = re.search(r"^  m_FoodstuffKeyName .* value=(.+)$", block, re.M)
            if not match:
                continue
            key = match[1].strip()
            actor = block.split()[0]
            package = maps[dump.stem]
            path = f"{package}.{dump.stem}:PersistentLevel.{actor}"
            entry = grouped.setdefault(key, {"id": key, "world": WORLDS[key[:2]], "actors": []})
            entry["actors"].append(path)
    spots = []
    ordinals = {}
    for key, spot in sorted(grouped.items()):
        world = spot["world"]
        ordinals[world] = ordinals.get(world, 0) + 1
        spot["name"] = f"Ingredients+ - {world} - Pickup {ordinals[world]:03d}"
        spot["actors"].sort()
        spots.append(spot)
    destination = ROOT / "worlds/kh3/data/ingredient_spots.json"
    destination.write_text(json.dumps({"schema": 1, "spots": spots}, indent=2) + "\n", encoding="utf-8")
    print(f"{len(spots)} spots, {sum(len(s['actors']) for s in spots)} actor paths")


if __name__ == "__main__":
    main()
