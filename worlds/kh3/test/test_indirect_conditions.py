from BaseClasses import CollectionState

from ..Data import PORTAL_KEYBLADE_REGION_LOCKS
from ..Regions import HUB_REGION_NAME, KEYBLADE_GRAVEYARD_REMIND_SHARED_REGION_NAME
from . import KH3TestBase


class TestKH3IndirectConditions(KH3TestBase):
    auto_construct = False
    options = {"olympus_coliseum_pool": "randomize"}

    def _state_checking_entrance_first(self, region_name, excluded_items=()):
        self.world_setup(seed=730142)
        entrance = self.multiworld.get_entrance(f"To {region_name}", self.player)
        hub = self.multiworld.get_region(HUB_REGION_NAME, self.player)
        # Exercise the failing order deterministically: the dependent entrance
        # is visited before the regions its access rule queries.
        hub.exits.remove(entrance)
        hub.exits.insert(0, entrance)
        state = CollectionState(self.multiworld)
        for item in self.multiworld.itempool:
            if item.name not in excluded_items:
                state.collect(item, True)
        for name in excluded_items:
            while state.has(name, self.player):
                state.remove(self.world.create_item(name))
        return state

    def test_shared_chests_rechecked_for_either_world_in_same_traversal(self):
        for available, unavailable in (("Keyblade Graveyard", "Re Mind"), ("Re Mind", "Keyblade Graveyard")):
            with self.subTest(available=available):
                state = self._state_checking_entrance_first(
                    KEYBLADE_GRAVEYARD_REMIND_SHARED_REGION_NAME,
                    (PORTAL_KEYBLADE_REGION_LOCKS[unavailable],),
                )
                state.update_reachable_regions(self.player)
                reachable = state.reachable_regions[self.player]
                self.assertIn(self.multiworld.get_region(available, self.player), reachable)
                self.assertNotIn(self.multiworld.get_region(unavailable, self.player), reachable)
                self.assertIn(self.multiworld.get_region(
                    KEYBLADE_GRAVEYARD_REMIND_SHARED_REGION_NAME, self.player), reachable)

    def test_coliseum_rechecked_after_olympus_in_same_traversal(self):
        state = self._state_checking_entrance_first("Olympus Coliseum")
        state.update_reachable_regions(self.player)
        self.assertIn(self.multiworld.get_region("Olympus", self.player), state.reachable_regions[self.player])
        self.assertIn(self.multiworld.get_region("Olympus Coliseum", self.player),
                      state.reachable_regions[self.player])

    def test_rechecks_preserve_access_requirements(self):
        cases = (
            (KEYBLADE_GRAVEYARD_REMIND_SHARED_REGION_NAME,
             (PORTAL_KEYBLADE_REGION_LOCKS["Keyblade Graveyard"], PORTAL_KEYBLADE_REGION_LOCKS["Re Mind"])),
            ("Olympus Coliseum", (PORTAL_KEYBLADE_REGION_LOCKS["Olympus"],)),
        )
        for region_name, missing in cases:
            with self.subTest(region=region_name, missing=missing):
                state = self._state_checking_entrance_first(region_name, missing)
                state.update_reachable_regions(self.player)
                self.assertNotIn(self.multiworld.get_region(region_name, self.player),
                                 state.reachable_regions[self.player])
