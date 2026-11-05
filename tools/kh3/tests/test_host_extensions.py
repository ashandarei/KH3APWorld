"""Optional conveniences in the development host, not APWorld requirements."""
from unittest import TestCase
from MultiServer import get_intended_item_name
from worlds.kh3 import KH3World


class HostAliasTests(TestCase):
    def test_chat_item_aliases_resolve_to_canonical_item_names(self) -> None:
        for alias, item_name in {
            "wall run": "Unlock: Wall Run",
            "FIRE": "Magic: Fire",
            "style change": "Unlock: Style Change",
        }.items():
            with self.subTest(alias=alias):
                resolved_name, usable, _response = get_intended_item_name(
                    alias,
                    KH3World.item_name_to_id,
                    KH3World.item_name_aliases,
                )
                self.assertTrue(usable)
                self.assertEqual(resolved_name, item_name)
