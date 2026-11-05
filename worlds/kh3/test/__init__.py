from test.bases import WorldTestBase

from .. import KH3World
from ..Bridge import BRIDGE_PROTOCOL_NAME, BRIDGE_PROTOCOL_VERSION
from ..SlotData import encode_slot_data


def empty_wire_slot_data() -> dict:
    protocol = {"protocol_name": BRIDGE_PROTOCOL_NAME, "protocol_version": BRIDGE_PROTOCOL_VERSION}
    return encode_slot_data({**protocol, "bridge": {**protocol, "locations": []}})


class KH3TestBase(WorldTestBase):
    game = "Kingdom Hearts III"
    world: KH3World

    def reset_reachability(self, state):
        """Recompute AP regions after a test changes the finished logic graph.

        Normal gameplay changes inventory through collect/remove, which AP
        already handles. Directly replacing a rule in a test also needs to
        discard the previous graph's cached reachable regions.
        """
        state.reachable_regions[self.player].clear()
        state.blocked_connections[self.player].clear()
        state.stale[self.player] = True

    @staticmethod
    def get_location_base_region(location):
        region = location.parent_region
        while region.name.startswith("Access: "):
            region = region.entrances[0].parent_region
        return region

    def get_location_by_source(self, source_table: str, source_id: str):
        for location_data in self.world.active_locations:
            if (location_data["source_table"], location_data["source_id"]) == (source_table, source_id):
                return self.multiworld.get_location(location_data["name"], self.player)
        raise AssertionError(f"Missing KH3 location source {source_table}:{source_id}")
