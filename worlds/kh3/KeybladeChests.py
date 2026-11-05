"""Seed-owned chest requirements and fixed Keyblade palettes."""

from __future__ import annotations

import json
from importlib.resources import files

from .Data import (
    PORTAL_KEYBLADE_ITEM_NAMES,
    PORTAL_KEYBLADE_REGION_LOCKS,
    RANDOMIZED_KEYBLADE_ITEM_NAMES,
    WORLD_LOCATION_POOL_OPTIONS,
    resolve_item_runtime_token,
)

KEYBLADE_CHEST_FIELDS = (
    "keyblade_chest_keyblade", "keyblade_chest_token", "keyblade_chest_native",
)
KEYBLADE_CHEST_PALETTES = json.loads(
    files(__package__).joinpath("data/keyblade_chest_palettes.json").read_text(encoding="utf-8")
)


def native_chest_keyblade(row: dict) -> str | None:
    world = row.get("world")
    if world == "The Final World":
        world = "Keyblade Graveyard"
    return PORTAL_KEYBLADE_REGION_LOCKS.get(world)


def assign_keyblade_chests(rng, locations: list[dict], options, slot_data: dict | None = None) -> None:
    """Melody selection runs first, so converted chests override Chestsanity."""
    enabled = bool(options.chestsanity.value)
    candidates = {}
    for row in locations:
        for field in KEYBLADE_CHEST_FIELDS:
            row.pop(field, None)
        world_option = WORLD_LOCATION_POOL_OPTIONS.get(row.get("world"))
        if (enabled and row.get("type") == "treasure" and "mom_song_id" not in row
                and world_option and int(getattr(options, world_option).value) == 2):
            candidates[(row["source_table"], row["source_id"])] = row
    if set(RANDOMIZED_KEYBLADE_ITEM_NAMES) != set(KEYBLADE_CHEST_PALETTES):
        raise ValueError("Chestsanity palettes must cover every randomized Keyblade.")
    # Keep chest keys separate from the active world-door keys. In character
    # mode those same door Keyblades instead become the chest requirements.
    party_portals = int(options.portal_unlocks.value) == 1
    pool = (RANDOMIZED_KEYBLADE_ITEM_NAMES & PORTAL_KEYBLADE_ITEM_NAMES if party_portals
            else RANDOMIZED_KEYBLADE_ITEM_NAMES - PORTAL_KEYBLADE_ITEM_NAMES)
    blades = sorted(pool - {"Pandora's Power", "Ultima Weapon"})
    saved = None
    if slot_data is not None:
        selected = [r for r in slot_data.get("bridge", {}).get("locations", [])
                    if any(f in r for f in KEYBLADE_CHEST_FIELDS)]
        saved = {(r.get("source_table"), r.get("source_id")): r for r in selected}
        if len(saved) != len(selected) or saved.keys() != candidates.keys():
            raise ValueError("Invalid Chestsanity chest assignments in tracker slot data.")
    for source_key, row in sorted(candidates.items()):
        blade = saved[source_key].get("keyblade_chest_keyblade") if saved is not None else rng.choice(blades)
        if blade not in blades:
            raise ValueError("Invalid Chestsanity Keyblade.")
        expected = dict(zip(KEYBLADE_CHEST_FIELDS, (
            blade, resolve_item_runtime_token(blade),
            not party_portals and blade == native_chest_keyblade(row),
        )))
        if saved is not None and (any(saved[source_key].get(k) != v for k, v in expected.items())
                                 or type(saved[source_key].get("keyblade_chest_native")) is not bool):
            raise ValueError("Invalid Chestsanity Keyblade identity in tracker slot data.")
        row.update(expected)
