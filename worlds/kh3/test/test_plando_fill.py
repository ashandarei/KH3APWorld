from unittest import TestCase

from Fill import (
    balance_multiworld_progression,
    distribute_items_restrictive,
    distribute_planned_blocks,
    parse_planned_blocks,
    resolve_early_locations_for_planned,
)
from Options import PlandoItems
from test.general import gen_steps, setup_multiworld
from worlds.AutoWorld import call_all
from worlds.kh1 import KH1World

from .. import KH3World


class TestMixedPlandoFill(TestCase):
    def test_extra_plando_items_never_trigger_kh3_pool_deletions(self):
        cases = (
            ([KH3World, KH1World], (True, False)),
            ([KH3World, KH1World], (False, True)),
            ([KH3World, KH3World], (False, False)),
        )
        for worlds, from_pool in cases:
            with self.subTest(worlds=[world.game for world in worlds], from_pool=from_pool):
                mw = setup_multiworld(worlds, steps=gen_steps[:-1], seed=731)
                targets = {}
                for player, use_pool in enumerate(from_pool, 1):
                    targets[player] = next(iter(mw.get_unfilled_locations(player))).name
                    mw.worlds[player].options.plando_items = PlandoItems.from_any([{
                        'item': 'Potion', 'location': targets[player],
                        'from_pool': use_pool, 'force': True,
                    }])
                mw.plando_item_blocks = parse_planned_blocks(mw)
                resolve_early_locations_for_planned(mw)
                distribute_planned_blocks(mw, [block for blocks in mw.plando_item_blocks.values()
                                               for block in blocks])
                pool_before = [id(item) for item in mw.itempool]
                shared_rng = mw.random.getstate()
                call_all(mw, 'pre_fill')
                self.assertEqual([id(item) for item in mw.itempool], pool_before)
                self.assertEqual(mw.random.getstate(), shared_rng)
                with self.assertLogs(level='WARNING') as logs:
                    distribute_items_restrictive(mw)
                self.assertTrue(any('Unplaced items' in message for message in logs.output))
                call_all(mw, 'post_fill')
                balance_multiworld_progression(mw)
                call_all(mw, 'finalize_multiworld')
                for player, name in targets.items():
                    item = mw.get_location(name, player).item
                    self.assertEqual((item.name, item.player), ('Potion', player))
                self.assertFalse(mw.get_unfilled_locations())
                self.assertTrue(mw.can_beat_game())
                self.assertTrue(mw.fulfills_accessibility())
