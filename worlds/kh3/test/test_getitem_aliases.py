from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import AsyncMock, patch

from CommonClient import ClientCommandProcessor

from .. import KH3World
from ..CharacterUnlocks import CHARACTER_UNLOCK_ITEM_NAMES
from ..Client import KH3Context


class TestGetitemAliases(IsolatedAsyncioTestCase):
    async def test_character_aliases_send_canonical_commands_to_stock_hosts(self):
        ctx = KH3Context.__new__(KH3Context)
        ctx.send_msgs = AsyncMock()
        processor = ClientCommandProcessor(ctx)
        for item_name in CHARACTER_UNLOCK_ITEM_NAMES:
            character = item_name.removeprefix("Playable Character: ")
            for alias in (f"pc: {character.lower()}", f"PC: {character.upper()}"):
                with self.subTest(alias=alias):
                    with patch("CommonClient.async_start") as start:
                        processor(f"!getitem {alias}")
                    await start.call_args.args[0]
                    ctx.send_msgs.assert_awaited_with([
                        {"cmd": "Say", "text": f"!getitem {item_name}"},
                    ])


class TestGetitemAliasBoundaries(TestCase):
    def test_other_chat_and_commands_pass_through(self):
        ctx = KH3Context.__new__(KH3Context)
        for text in (
            "", "pc: aqua", "!getitem", "!getitem pc: unknown",
            "!getitem pc: aqua extra", "!getitem Playable Character: Aqua",
            "!getitem Magic: Fire", "!hint pc: aqua", "hello pc: aqua",
        ):
            with self.subTest(text=text):
                self.assertEqual(ctx.on_user_say(text), text)

    def test_aliases_preserve_canonical_catalog_and_host_support(self):
        for item_name in CHARACTER_UNLOCK_ITEM_NAMES:
            alias = f"PC: {item_name.removeprefix('Playable Character: ')}"
            self.assertEqual(KH3World.item_name_aliases[alias], item_name)
            self.assertIn(item_name, KH3World.item_name_to_id)
            self.assertNotIn(alias, KH3World.item_name_to_id)
