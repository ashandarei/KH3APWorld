"""Location metadata indexing for a KH3 bridge session."""

from __future__ import annotations

from typing import Any


class LocationIndex:
    """Provide the legacy location lookup behavior over validated slot data."""

    def __init__(self, slot_data: dict[str, Any]) -> None:
        self.slot_data = slot_data
        self.by_key: dict[str, dict[str, Any]] = {}
        self.by_name: dict[str, dict[str, Any]] = {}
        self.by_source: dict[tuple[str, str], dict[str, Any]] = {}
        self.by_ap_location_id: dict[int, dict[str, Any]] = {}
        for location in slot_data["bridge"]["locations"]:
            self.by_key[location["bridge_key"]] = location
            self.by_name[location["ap_location_name"].casefold()] = location
            self.by_source[(location["source_table"], location["source_id"])] = location
            try:
                ap_location_id = int(location.get("ap_location_id"))
            except (TypeError, ValueError):
                continue
            self.by_ap_location_id.setdefault(ap_location_id, location)

    def resolve_location(self, token: str) -> dict[str, Any]:
        if token in self.by_key:
            return self.by_key[token]
        normalized = token.casefold()
        if normalized in self.by_name:
            return self.by_name[normalized]
        raise KeyError(f"Unknown KH3 bridge location key or AP location name: {token}")

    def resolve_source_id(self, source_table: str, source_id: str) -> dict[str, Any]:
        try:
            return self.by_source[(source_table, source_id)]
        except KeyError as exc:
            raise KeyError(f"Unknown KH3 bridge source id: {source_table}:{source_id}") from exc

    def resolve_ap_location_id(self, ap_location_id: int) -> dict[str, Any]:
        try:
            return self.by_ap_location_id[ap_location_id]
        except KeyError as exc:
            raise KeyError(f"Unknown KH3 AP location id: {ap_location_id}") from exc

    def list_locations(self, world_name: str | None = None) -> list[dict[str, Any]]:
        locations = self.slot_data["bridge"]["locations"]
        if world_name:
            return [location for location in locations if location["world"].casefold() == world_name.casefold()]
        return list(locations)

    @staticmethod
    def display_location_name(location: dict[str, Any], fallback: str = "") -> str:
        location_description = str(location.get("location_description") or "").strip()
        if location_description:
            return location_description
        return str(location.get("ap_location_name") or fallback)
