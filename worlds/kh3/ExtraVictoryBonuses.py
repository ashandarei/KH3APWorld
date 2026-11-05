"""Two fixed extra checks for every active victory bonus."""

from __future__ import annotations

from typing import TYPE_CHECKING

from BaseClasses import LocationProgressType
from rule_builder.rules import CanReachLocation

from .Locations import EXTRA_VBONUS_REWARD_NUMBERS, extra_vbonus_location_name, location_name_to_id

if TYPE_CHECKING:
    from . import KH3World


def extra_vbonus_locations(locations: list[dict], slot_data: dict | None = None) -> list[dict]:
    """Always build two extras per VBonus and validate the same layout in UT."""
    extras = []
    for parent in locations:
        if parent["type"] != "vbonus":
            continue
        for number in EXTRA_VBONUS_REWARD_NUMBERS:
            name = extra_vbonus_location_name(parent["name"], number)
            extras.append({
                "name": name,
                "ap_location_id": location_name_to_id[name],
                "type": "vbonus_extra",
                "world": parent["world"],
                "source_table": "VBonusExtra",
                "source_id": f"{parent['source_id']}_{number}",
                # No additional vanilla item is duplicated into the pool.
                # AP fills this unlocked check from the complete shared pool.
                "primary_item_name": "",
                "location_description": name,
                "trigger_bridge_key": f"{parent['source_table']}:{parent['source_id']}",
            })
    if slot_data is not None:
        actual = [row for row in slot_data["bridge"]["locations"] if row.get("type") == "vbonus_extra"]
        expected = {row["ap_location_id"]: row for row in extras}
        if len(actual) != len(expected) or {row.get("ap_location_id") for row in actual} != set(expected):
            raise ValueError("Invalid victory bonus reward selection in tracker slot data.")
        for row in actual:
            wanted = expected[row["ap_location_id"]]
            if (row.get("ap_location_name") != wanted["name"]
                    or any(row.get(key) != wanted[key] for key in (
                        "source_table", "source_id", "world", "trigger_bridge_key"))):
                raise ValueError("Invalid victory bonus reward identity in tracker slot data.")
    return extras


def set_extra_vbonus_rules(world: KH3World) -> None:
    parents = {f"{row['source_table']}:{row['source_id']}": row
               for row in world.active_locations if row["type"] == "vbonus"}
    for row in world.extra_vbonus_locations:
        parent = world.multiworld.get_location(parents[row["trigger_bridge_key"]]["name"], world.player)
        extra = world.multiworld.get_location(row["name"], world.player)
        world.set_rule(extra, CanReachLocation(parent.name))
        extra.item_rule = lambda item, parent=parent: parent.item_rule(item)
        extra.filler_only = parent.filler_only
        # A minigame's own pool can be randomize inside a junk world. Replace
        # the preliminary world-level exclusion with the parent's final policy.
        extra.progress_type = parent.progress_type
        if (parent.name in world.options.exclude_locations.value
                or parent.progress_type == LocationProgressType.EXCLUDED):
            extra.progress_type = LocationProgressType.EXCLUDED
