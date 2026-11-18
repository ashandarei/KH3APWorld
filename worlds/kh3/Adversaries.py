"""Optional first-defeat checks; journal visibility is not check eligibility."""
import json

from .PackageResources import read_package_text

ADVERSARY_SOURCE_TABLE = "Adversary"
ADVERSARIES = tuple(json.loads(read_package_text("data/adversary_checks.json", module_file=__file__)))
ADVERSARIES_BY_ID = {entry["id"]: entry for entry in ADVERSARIES}
ADVERSARY_WORLDS = {entry["id"]: tuple(entry["worlds"]) for entry in ADVERSARIES}


def append_adversary_locations(data: dict) -> None:
    for entry in ADVERSARIES:
        name = f"Adversaries - {entry['name']}"
        data["locations"].append({
            "name": name, "location_description": name,
            "type": "adversary", "world": "Adversaries",
            "source_table": ADVERSARY_SOURCE_TABLE, "source_id": entry["id"],
            "vanilla_item": "ITEM_POTION", "vanilla_item_name": "Potion",
        })
