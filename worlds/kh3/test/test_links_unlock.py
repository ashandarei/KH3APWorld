from __future__ import annotations

import unittest

from BaseClasses import ItemClassification

from ..Data import build_item_name_to_id, load_extracted_data, resolve_item_runtime_token
from ..Items import classify_item
from ..Logic import parse_requirement_summary, requirement_clause_item_names
from ..LogicData import LINK_HEARTBINDER_ITEM_NAMES, MONSTROPOLIS_FINAL_COMBAT
from ..Space import SPACE_ITEM_IDS
from . import KH3TestBase


def route_items(summary: str) -> list[set[str]]:
    return [
        {item for clause in route for item in requirement_clause_item_names(clause)}
        for route in parse_requirement_summary(summary)
    ]


class TestLinksRequirements(unittest.TestCase):
    def test_links_do_not_replace_or_imply_spell_magic(self):
        self.assertEqual(route_items("Dream Heartbinder"), [{"Dream Heartbinder", "Unlock: Links"}])
        self.assertEqual(route_items("Fire"), [{"Magic: Fire", "Unlock: Magic"}])
        self.assertEqual(route_items("Unlock: Magic"), [{"Unlock: Magic"}])
        self.assertEqual(route_items("Links"), [{"Unlock: Links"}])

    def test_final_monstropolis_combat_keeps_magic_without_links(self):
        route = route_items(MONSTROPOLIS_FINAL_COMBAT)[0]
        self.assertIn("Unlock: Magic", route)
        self.assertNotIn("Unlock: Links", route)
        self.assertTrue(route.isdisjoint(LINK_HEARTBINDER_ITEM_NAMES))

    def test_heartbinders_are_useful(self):
        for name in LINK_HEARTBINDER_ITEM_NAMES:
            with self.subTest(name=name):
                self.assertEqual(classify_item(name), ItemClassification.useful)

    def test_alternative_routes_keep_separate_unlocks(self):
        spells, links = route_items("Fire OR Dream Heartbinder")
        self.assertIn("Unlock: Magic", spells)
        self.assertNotIn("Unlock: Links", spells)
        self.assertIn("Unlock: Links", links)
        self.assertNotIn("Unlock: Magic", links)

    def test_explicit_links_requirement_is_not_duplicated(self):
        routes = parse_requirement_summary("Unlock: Links + Dream Heartbinder")
        self.assertEqual(sum("Unlock: Links" in requirement_clause_item_names(c) for c in routes[0]), 1)

    def test_links_id_finishes_sora_namespace_and_runtime_token_is_independent(self):
        data = load_extracted_data()
        ids = build_item_name_to_id(data)
        from ..Minigames import MINIGAME_APPENDED_ITEMS
        self.assertEqual(ids["Unlock: Links"], max(ids[name] for name in MINIGAME_APPENDED_ITEMS) + 1)
        self.assertEqual(len(set(ids.values())), len(ids))
        self.assertEqual({name: ids[name] for name in SPACE_ITEM_IDS}, SPACE_ITEM_IDS)
        self.assertNotIn(ids["Unlock: Links"], SPACE_ITEM_IDS.values())
        self.assertEqual(resolve_item_runtime_token("Unlock: Links"), "KH3AP_CONTROLLER_UNLOCK::links")
        self.assertEqual(resolve_item_runtime_token("Unlock: Magic"), "KH3AP_CONTROLLER_UNLOCK::magic")


class TestKH3SeparateLinksPool(KH3TestBase):
    def test_unlocks_generate_independently(self):
        self.world_setup()
        pool = [item.name for item in self.multiworld.itempool]
        self.assertEqual(pool.count("Unlock: Links"), 1)
        self.assertEqual(pool.count("Unlock: Magic"), 1)


class TestKH3StartWithLinks(KH3TestBase):
    options = {"start_with_links": 1}

    def test_links_starting_option_does_not_precollect_magic(self):
        self.world_setup()
        starting = [item.name for item in self.multiworld.precollected_items[self.player]]
        pool = [item.name for item in self.multiworld.itempool]
        self.assertIn("Unlock: Links", starting)
        self.assertNotIn("Unlock: Links", pool)
        self.assertNotIn("Unlock: Magic", starting)
        self.assertEqual(pool.count("Unlock: Magic"), 1)
        self.assertEqual(len(pool), len(self.multiworld.get_unfilled_locations()))
