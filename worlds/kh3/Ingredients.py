"""Ingredient identities from EGS TresItemData and English kh3_item.locres.

Each location records the first qualifying world pickup, independently of stock
already bought, consumed or restored. The original ingredient is never removed.
"""
import json
import re

from .PackageResources import read_package_text
from .MinimapReference import MINIMAP_REFERENCE

INGREDIENT_NAMES = (
    "Crab", "Scallop", "Lobster", "Sole", "Eel", "Sea Bass", "Mussel", "Cod",
    "Pumpkin", "Zucchini", "Onion", "Tomato", "Eggplant", "Carrot", "Garlic", "Celery",
    "Morel", "Porcini", "Chanterelle", "Portobello", "Black Truffle", "King Oyster Mushroom",
    "Black Trumpet", "Miller Mushroom", "Cloves", "Rosemary", "Thyme", "Bay Leaf", "Basil",
    "Dill", "Parsley", "Saffron", "Apricot", "Gooseberry", "Lemon", "Orange", "Raspberry",
    "Pear", "Blackberry", "Apple", "Cheese", "Chocolate", "Caviar", "Butter", "Olive Oil",
    "Cornichon", "Rice", "Honey", "Sour Cherry", "Strawberry", "Blood Orange", "Banana",
    "Grapes", "Melon", "Watermelon", "Veal", "Beef", "Quail", "Filet Mignon",
)
INGREDIENTS_BY_ID = {f"FST_ITEM{index:02d}": name for index, name in enumerate(INGREDIENT_NAMES, 1)}
INGREDIENT_SOURCE_TABLE = "Ingredient"
INGREDIENT_STATE_FILE = "runtime_ingredient_state.json"
INGREDIENT_STATE_SCHEMA = "kh3ap.runtime_ingredient_state.v1"
INGREDIENT_WORLD_SOURCES = json.loads(read_package_text(
    "data/ingredient_sources.json", module_file=__file__))["ingredients"]
INGREDIENT_SPOTS = json.loads(read_package_text(
    "data/ingredient_spots.json", module_file=__file__))["spots"]
INGREDIENT_SPOTS_BY_ID = {spot["id"]: spot for spot in INGREDIENT_SPOTS}


def _spot_labels() -> tuple[dict[str, str], dict[str, str]]:
    """Join exact actor identities to the reviewed journal and map labels once."""
    journal = json.loads(read_package_text("data/ingredient_journal.json", module_file=__file__))
    actor_drops: dict[str, set[str]] = {}
    for ingredient in journal["ingredients"]:
        for source in ingredient["sources"]:
            for actor in source["actors"]:
                actor_drops.setdefault(actor, set()).add(ingredient["name"])
    maps = {key[:4].lower(): entry["map_name"] for key, entry in MINIMAP_REFERENCE.items()
            if entry["map_name"]}
    display_names, descriptions = {}, {}
    for spot in INGREDIENT_SPOTS:
        map_names, drops = set(), set()
        for actor in spot["actors"]:
            match = re.search(r"/Levels/([a-z]{2})/\1_(\d\d)/", actor)
            if match:
                map_key = match[1] + match[2]
                map_names.add(maps.get(map_key, f"Map {match[1]}_{match[2]}"))
            drops.update(actor_drops.get(actor, ()))
        pickup = spot["name"].rsplit(" - ", 1)[-1]
        display_names[spot["id"]] = " / ".join(sorted(drops)) if drops else "Unknown ingredient"
        drop_label = "Possible drops: " + ", ".join(sorted(drops)) if drops else "Drop unknown"
        descriptions[spot["id"]] = (
            f"Ingredients+ - {spot['world']} - {' / '.join(sorted(map_names))} - {pickup} ({drop_label})")
        if spot["id"] in INGREDIENT_JUNK_SPOTS:
            display_names[spot["id"]] += " (Optional - not required)"
            descriptions[spot["id"]] += " (Optional - not required)"
    return display_names, descriptions


def ingredient_location_description(location: dict) -> str | None:
    """Enrich client messages without changing the generated wire catalog."""
    if location.get("source_table") == "IngredientSpot":
        return INGREDIENT_SPOT_DESCRIPTIONS.get(location.get("source_id"))
    return None


INGREDIENT_SPAWN_RATES = json.loads(read_package_text(
    "data/ingredient_spawn_rates.json", module_file=__file__))["spots"]
# Player review, Ingredientsanity+ F118, F203/F206/F215:F231 and F271.
# Keep observed intermittent actors even when the native table says 100:
# m_GenRate is not the only condition controlling whether an actor appears.
REPORTED_INTERMITTENT_INGREDIENT_SPOTS = frozenset({
    "CA_02j_F_007",
    *(f"RA_01_F_{number:03}" for number in (13, 16, 25, 26, 28, 30, 31, 40, 41)),
    *(f"TS_01_F_{number:03}" for number in (1, 2, 4, 5, 6, 11, 13, 14, 15, 16, 18)),
})
INGREDIENT_FIXED_REWARDS = dict.fromkeys(
    ("RA_01_F_005", "RA_01_F_010", "RA_01_F_011"), "Ether")
INGREDIENT_JUNK_SPOTS = (
    frozenset(spot_id for spot_id, rate in INGREDIENT_SPAWN_RATES.items() if rate < 100)
    | REPORTED_INTERMITTENT_INGREDIENT_SPOTS
    | frozenset(INGREDIENT_FIXED_REWARDS)
)


INGREDIENT_SPOT_DISPLAY_NAMES, INGREDIENT_SPOT_DESCRIPTIONS = _spot_labels()


def ingredient_spot_is_junk(location: dict) -> bool:
    return (location.get("source_table") == "IngredientSpot"
            and location.get("source_id") in INGREDIENT_JUNK_SPOTS)


def append_ingredient_locations(data: dict) -> None:
    for key, name in INGREDIENTS_BY_ID.items():
        data["locations"].append({
            "name": f"Ingredients - {name}", "type": "ingredient", "world": "Ingredients",
            "source_table": INGREDIENT_SOURCE_TABLE, "source_id": key,
            "location_description": f"Ingredients - {name}",
            "display_name": name,
            "vanilla_item": "ITEM_POTION", "vanilla_item_name": "Potion",
            "ap_enabled": key in INGREDIENT_WORLD_SOURCES,
        })
    # Append after the original 59 reserved IDs; never interleave the two modes.
    for spot in INGREDIENT_SPOTS:
        data["locations"].append({
            "name": spot["name"], "location_description": spot["name"],
            "display_name": INGREDIENT_SPOT_DISPLAY_NAMES[spot["id"]],
            "type": "ingredient", "world": "Ingredients",
            "source_table": "IngredientSpot", "source_id": spot["id"],
            "vanilla_item": "ITEM_POTION", "vanilla_item_name": "Potion",
        })
