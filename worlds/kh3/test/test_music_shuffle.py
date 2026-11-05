from typing import ClassVar

from ..Options import IncludeCustomMusic, MusicShuffle
from . import KH3TestBase


class TestMusicShuffleVanillaSlotData(KH3TestBase):
    def test_exports_the_starting_mode_only(self):
        slot_data = self.world.fill_slot_data()
        self.assertEqual(slot_data["music_shuffle"], MusicShuffle.option_vanilla)
        self.assertEqual(slot_data["include_custom_music"], IncludeCustomMusic.default)
        self.assertEqual(IncludeCustomMusic.default, 1)
        # The runtime draws the table from the seed; none travels in slot data.
        self.assertNotIn("bgm_swaps", slot_data)


class TestMusicShuffleWorldsSlotData(KH3TestBase):
    options: ClassVar[dict] = {"music_shuffle": MusicShuffle.option_shuffle_battle_themes,
                             "include_custom_music": 0}

    def test_exports_the_chosen_mode(self):
        self.assertEqual(self.world.fill_slot_data()["music_shuffle"], MusicShuffle.option_shuffle_battle_themes)
        self.assertEqual(self.world.fill_slot_data()["include_custom_music"], 0)
