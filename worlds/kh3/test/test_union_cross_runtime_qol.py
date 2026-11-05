from __future__ import annotations

from worlds.kh3.test.runtime_sources import native_project_root

import unittest


class TestUnionCrossRuntimeQol(unittest.TestCase):
    def test_immediate_completion_uses_ue4ss_without_shipping_vanilla_asset(self) -> None:
        runtime_header = (
            native_project_root()
            / "KH3APProbeCpp"
            / "kh3ap"
            / "features"
            / "story"
            / "minigames"
            / "union_cross_completion.h"
        ).read_text(encoding="utf-8")
        runtime_service = (
            native_project_root()
            / "KH3APProbeCpp"
            / "kh3ap"
            / "features"
            / "story"
            / "minigames"
            / "union_cross_command_service.cpp"
        ).read_text(encoding="utf-8")

        self.assertIn('runtime_union_cross_slot_option =\n            "fast_union_cross"', runtime_header)
        self.assertIn("runtime_union_cross_started_property_name", runtime_header)
        self.assertNotIn("CommandUseCountPhase", runtime_header + runtime_service)
        self.assertIn("NotifyEndRide__DelegateSignature", runtime_header)
        self.assertIn("try_read_started", runtime_service)
        self.assertIn("start_tracker.observe_started", runtime_service)
        self.assertIn("actor->ProcessEvent(end_ride, nullptr)", runtime_service)
        self.assertIn("level_script->ProcessEvent", runtime_service)
        self.assertNotIn("RegisterHook", runtime_service)
        self.assertNotIn("CommandCountBeginPhase2", runtime_header + runtime_service)
        self.assertNotIn("CommandLimitPhase2", runtime_header + runtime_service)


if __name__ == "__main__":
    unittest.main()
