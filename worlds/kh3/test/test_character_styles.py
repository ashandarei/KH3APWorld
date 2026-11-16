from BaseClasses import ItemClassification
from ..Data import CHARACTER_STYLE_ITEM_NAMES, SPELLWEAVER_UNLOCK_ITEM, SEVEN_WISHES_UNLOCK_ITEM
from . import KH3TestBase


class TestCharacterStyles(KH3TestBase):
    def test_styles_follow_stylesanity_and_enabled_allies(self):
        cases = (
            ({"stylesanity": 0}, set()),
            ({"stylesanity": 1}, set(CHARACTER_STYLE_ITEM_NAMES)),
            ({"stylesanity": 1, "ally_characters": 0}, set()),
            ({"stylesanity": 1, "kh02_enabled": 0}, {SEVEN_WISHES_UNLOCK_ITEM}),
        )
        for options, expected in cases:
            with self.subTest(options=options):
                self.options = options
                self.world_setup(seed=359)
                pool = [item.name for item in self.multiworld.itempool]
                starting = [item.name for item in self.multiworld.precollected_items[self.player]]
                for name in CHARACTER_STYLE_ITEM_NAMES:
                    self.assertEqual(pool.count(name), int(name in expected))
                    self.assertNotIn(name, starting)
                    self.assertEqual(self.world.create_item(name).classification, ItemClassification.useful)
                self.assertEqual(len(pool), len(self.multiworld.get_unfilled_locations(self.player)))

    def test_explicit_style_starting_inventory_is_not_duplicated(self):
        from test.general import gen_steps, setup_multiworld
        from worlds.AutoWorld import call_all
        for option in ("start_inventory", "start_inventory_from_pool"):
            for stylesanity in (0, 1):
                with self.subTest(option=option, stylesanity=stylesanity):
                    mw = setup_multiworld(type(self.world), steps=("generate_early",), seed=360,
                                          options={"stylesanity": stylesanity,
                                                   option: {name: 1 for name in CHARACTER_STYLE_ITEM_NAMES}})
                    world = mw.worlds[self.player]
                    # Match Main's precollection before create_items.
                    for name in CHARACTER_STYLE_ITEM_NAMES:
                        mw.push_precollected(world.create_item(name))
                    for step in gen_steps[1:]:
                        call_all(mw, step)
                    starting = [item.name for item in mw.precollected_items[self.player]]
                    pool = [item.name for item in mw.itempool]
                    for name in CHARACTER_STYLE_ITEM_NAMES:
                        self.assertEqual(starting.count(name), 1)
                        self.assertNotIn(name, pool)
                    self.assertEqual(len(pool), len(mw.get_unfilled_locations(self.player)))

    def test_ally_characters_removes_all_rewards_and_round_trips_to_tracker(self):
        from .test_universal_tracker import TestKH3UniversalTracker
        self.options = {"ally_characters": 0}
        self.world_setup(seed=361)
        self.assertFalse(any(row.get("fixed_character_reward") for row in self.world.active_locations))
        pool = {item.name for item in self.multiworld.itempool}
        self.assertTrue(pool.isdisjoint(CHARACTER_STYLE_ITEM_NAMES))
        slot = self.world.build_runtime_slot_data()
        self.assertEqual(slot["ally_characters"], 0)
        tracker = TestKH3UniversalTracker.build_yamlless_tracker_multiworld(self, slot).worlds[self.player]
        self.assertFalse(tracker.options.ally_characters.value)

    def test_disabled_ally_rejects_starting_style(self):
        from test.general import setup_multiworld
        for style in CHARACTER_STYLE_ITEM_NAMES:
            for inventory in ("start_inventory", "start_inventory_from_pool"):
                with self.subTest(style=style, inventory=inventory):
                    with self.assertRaisesRegex(ValueError, "disabled Combat Ally items"):
                        setup_multiworld(type(self.world), steps=("generate_early",), seed=362,
                                         options={"ally_characters": 0, inventory: {style: 1}})
