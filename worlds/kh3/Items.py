from BaseClasses import Item, ItemClassification
from .CharacterUnlocks import CHARACTER_UNLOCK_ITEM_NAMES

from .Data import (
    TRAP_ITEM_NAMES,
    CONTROLLER_UNLOCK_ITEM_NAMES,
    FILLER_ITEM_RUNTIME_TOKEN_BY_NAME,
    KEYBLADE_STYLE_ITEM_NAMES,
    LOGIC_REQUIREMENT_ITEM_NAMES,
    PARTY_MEMBER_UNLOCK_ITEM_NAMES,
    PORTAL_KEYBLADE_ITEM_NAMES,
    PROGRESSIVE_KEYBLADE_LEVEL_ITEM_NAMES,
    PROOF_ITEMS,
    STYLE_CHANGE_UNLOCK_ITEM,
    SPELLWEAVER_UNLOCK_ITEM,
    ULTIMA_WEAPON_SHARD_ITEM_NAME,
    build_item_name_to_id,
    is_keyblade_item_name,
    load_extracted_data,
)
from .Space import SPACE_COSMETICS, SPACE_ITEM_IDS, SPACE_PARTS, SPECIAL_WEAPONS, ZONE_UNLOCK_BLUEPRINTS

_EXTRACTED_DATA = load_extracted_data()
_PROGRESSION_ITEM_FLAG = int(ItemClassification.progression)

class KH3Item(Item):
    game = "Kingdom Hearts III"

    def __setattr__(self, attribute, value):
        super().__setattr__(attribute, value)
        if (attribute in {"name", "classification"}
                and hasattr(self, "name") and hasattr(self, "classification")):
            # AP can change classifications after creation (for example item
            # links). Refresh metadata on those writes, rather than every receipt.
            self.logic_item_name = self.name if int(self.classification) & _PROGRESSION_ITEM_FLAG else None


def classify_item(name: str) -> ItemClassification:
    if name in CHARACTER_UNLOCK_ITEM_NAMES:
        return ItemClassification.progression
    if name == "Gate Opener":
        return ItemClassification.useful
    if name in SPACE_PARTS or name in SPACE_COSMETICS:
        return ItemClassification.filler
    if name in ZONE_UNLOCK_BLUEPRINTS or name in SPECIAL_WEAPONS:
        return ItemClassification.progression
    if name in SPACE_ITEM_IDS:
        return ItemClassification.useful
    if name in TRAP_ITEM_NAMES:
        return ItemClassification.trap
    if name in PROGRESSIVE_KEYBLADE_LEVEL_ITEM_NAMES:
        return ItemClassification.useful
    if name == ULTIMA_WEAPON_SHARD_ITEM_NAME:
        return ItemClassification.useful
    if name in PROOF_ITEMS:
        return ItemClassification.progression
    if name in PORTAL_KEYBLADE_ITEM_NAMES:
        return ItemClassification.progression
    if name.startswith("Secret Report "):
        return ItemClassification.useful
    if name.endswith("Heartbinder"):
        return ItemClassification.useful
    if name.startswith("Magic: "):
        return ItemClassification.progression
    if name in KEYBLADE_STYLE_ITEM_NAMES or name in {STYLE_CHANGE_UNLOCK_ITEM, SPELLWEAVER_UNLOCK_ITEM}:
        return ItemClassification.useful
    if name in CONTROLLER_UNLOCK_ITEM_NAMES:
        return ItemClassification.progression
    if name in PARTY_MEMBER_UNLOCK_ITEM_NAMES:
        return ItemClassification.useful
    if name in LOGIC_REQUIREMENT_ITEM_NAMES or name in {
        "Ability: Air Slide",
        "Ability: Dodge Roll",
        "Ability: Block",
        "Ability: High Jump",
        "Ability: Glide",
        "Ability: Pole Spin",
        "Ability: Doubleflight",
        "Ability: Second Chance",
        "Ability: Withstand Combo",
        "Archipelago Item",
    }:
        return ItemClassification.progression
    if name.startswith("Ability: "):
        return ItemClassification.useful
    if is_keyblade_item_name(name) or name.startswith("Map: ") or name.startswith("Bonus: "):
        return ItemClassification.useful
    return ItemClassification.filler


item_name_to_id = build_item_name_to_id(_EXTRACTED_DATA)
ABILITY_ITEM_NAMES = frozenset(
    item_name
    for item_name in item_name_to_id
    if item_name.startswith("Ability: ")
)
# These remain registered rewards, but should not be selected through groups.
ABILITY_GROUP_EXCLUSIONS = frozenset({
    "Ability: Aeroza", "Ability: Blizzaza", "Ability: Curaza",
    "Ability: Firaza", "Ability: Thundaza", "Ability: Waterza",
})
MAGIC_ITEM_NAMES = frozenset(name for name in item_name_to_id if name.startswith("Magic: "))
HEARTBINDER_ITEM_NAMES = frozenset(name for name in item_name_to_id if name.endswith("Heartbinder"))
# Repeatable recovery items, excluding currency pouches and permanent stat boosts.
CONSUMABLE_ITEM_NAMES = frozenset(
    name for name, token in FILLER_ITEM_RUNTIME_TOKEN_BY_NAME.items()
    if token.startswith("ITEM_")
)
