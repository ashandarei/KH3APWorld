"""Optional first-defeat checks; journal visibility is not check eligibility."""
import json

from .PackageResources import read_package_text

ADVERSARY_SOURCE_TABLE = "Adversary"
ADVERSARY_JOURNAL = json.loads(read_package_text(
    "data/adversary_journal.json", module_file=__file__))["enemies"]
# Boss-only encounters already award checks. Sniper's scripted NPC encounter
# has not passed a live kill-counter test, so it remains journal-only.
ADVERSARY_EXCLUSIONS = frozenset({
    "Dark Inferno", "Metal Troll", "Chaos Carriage", "Frost Serpent", "Sniper",
})
ADVERSARIES = tuple(entry for entry in ADVERSARY_JOURNAL
                    if entry["name"] not in ADVERSARY_EXCLUSIONS)
ADVERSARIES_BY_ID = {entry["id"]: entry for entry in ADVERSARIES}
ADVERSARY_WORLDS = {
    entry["id"]: tuple(sorted({
        "Re Mind" if source["world"] == "Scala ad Caelum" else source["world"]
        for source in entry["sources"]
    })) for entry in ADVERSARIES
}


def append_adversary_locations(data: dict) -> None:
    for entry in ADVERSARIES:
        name = f"Adversaries - {entry['name']}"
        data["locations"].append({
            "name": name, "location_description": name,
            "type": "adversary", "world": "Adversaries",
            "source_table": ADVERSARY_SOURCE_TABLE, "source_id": entry["id"],
            "vanilla_item": "ITEM_POTION", "vanilla_item_name": "Potion",
        })
