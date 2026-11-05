from __future__ import annotations

import unittest

from . import KH3TestBase


class TestQualityOfLifeOptions(KH3TestBase):
    def test_seed_options_drive_native_rules(self) -> None:
        self.world_setup()
        enabled = self.world.build_runtime_slot_data()
        self.assertEqual(enabled["dark_baymax_phase_1_skip"], 1)
        self.assertEqual(enabled["max_level_ship"], 1)

        self.world.options.dark_baymax_phase_1_skip.value = 0
        self.world.options.max_level_ship.value = 0
        disabled = self.world.build_runtime_slot_data()
        self.assertEqual(disabled["dark_baymax_phase_1_skip"], 0)
        self.assertEqual(disabled["max_level_ship"], 0)


if __name__ == "__main__":
    unittest.main()
