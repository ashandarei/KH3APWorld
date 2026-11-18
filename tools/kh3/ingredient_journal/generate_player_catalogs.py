"""Derive AP checks and client labels from the full journal authoring data."""

import json
import re
from pathlib import Path

DATA = Path(__file__).resolve().parents[3] / "worlds/kh3/data"


def read_catalog(filename, key):
    return json.loads((DATA / filename).read_text(encoding="utf-8"))[key]


def adversary_checks():
    # Boss-only encounters already award checks. Sniper's scripted NPC encounter
    # has not passed a live kill-counter test.
    excluded = {"Dark Inferno", "Metal Troll", "Chaos Carriage", "Frost Serpent", "Sniper"}
    checks = []
    for entry in read_catalog("adversary_journal.json", "enemies"):
        if entry["name"] in excluded:
            continue
        worlds = {source["world"] for source in entry["sources"]}
        regions = sorted({"Re Mind" if world == "Scala ad Caelum" else world for world in worlds})
        checks.append({"id": entry["id"], "name": entry["name"], "worlds": regions})
    return checks


def ingredient_checks():
    actor_drops = {}
    for ingredient in read_catalog("ingredient_journal.json", "ingredients"):
        for source in ingredient["sources"]:
            for actor in source["actors"]:
                actor_drops.setdefault(actor, set()).add(ingredient["name"])
    maps = {key[:4].lower(): entry["map_name"]
            for key, entry in read_catalog("minimap_reference.json", "minimaps").items()
            if entry["map_name"]}
    checks = []
    for spot in read_catalog("ingredient_spots.json", "spots"):
        map_names, drops = set(), set()
        for actor in spot["actors"]:
            match = re.search(r"/Levels/([a-z]{2})/\1_(\d\d)/", actor)
            if match:
                map_names.add(maps.get(match[1] + match[2], f"Map {match[1]}_{match[2]}"))
            drops.update(actor_drops.get(actor, ()))
        pickup = spot["name"].rsplit(" - ", 1)[-1]
        drop_label = "Possible drops: " + ", ".join(sorted(drops)) if drops else "Drop unknown"
        checks.append({
            "id": spot["id"], "name": spot["name"], "world": spot["world"],
            "display_name": " / ".join(sorted(drops)) if drops else "Unknown ingredient",
            "description": f"Ingredients+ - {spot['world']} - {' / '.join(sorted(map_names))} - {pickup} ({drop_label})",
        })
    return checks


if __name__ == "__main__":
    for name, checks in (("adversary_checks", adversary_checks()), ("ingredient_checks", ingredient_checks())):
        (DATA / f"{name}.json").write_text(json.dumps(checks, indent=2) + "\n", encoding="utf-8", newline="\n")
