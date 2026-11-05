from __future__ import annotations

from worlds.kh3.test.runtime_sources import native_project_root

import unittest


class TestBeatOfLeadRuntimeQol(unittest.TestCase):
    def test_fast_finish_uses_ue4ss_without_shipping_vanilla_asset(self) -> None:
        runtime_header = (
            native_project_root()
            / "KH3APProbeCpp"
            / "kh3ap"
            / "features"
            / "story"
            / "minigames"
            / "beat_of_lead_completion.h"
        ).read_text(encoding="utf-8")
        runtime_service = (
            native_project_root()
            / "KH3APProbeCpp"
            / "kh3ap"
            / "features"
            / "story"
            / "minigames"
            / "beat_of_lead_service.cpp"
        ).read_text(encoding="utf-8")

        self.assertIn('"fast_beat_of_lead"', runtime_header)
        self.assertIn('L"Minigame"', runtime_header)
        self.assertIn('L"SumScore"', runtime_header)
        self.assertIn('L"ts_03_gameflow_C"', runtime_header)
        self.assertIn('L"OnWipeOut"', runtime_header)
        self.assertIn('L"hyper"', runtime_header)
        self.assertIn("repeatable_minigame", runtime_service)
        self.assertIn("score <= 0", runtime_header)
        self.assertIn(
            "level_script->ProcessEvent(on_wipe_out, params.data())",
            runtime_service,
        )
        self.assertNotIn("Debug", runtime_header + runtime_service)
        self.assertNotIn("RegisterHook", runtime_service)
        for object_scan_api in (
            "GetGlobalObjects",
            "ForEachUObject",
            "GetObjectsOfClass",
            "FindAllOf",
            "IsReal",
            "format_object_debug_name",
        ):
            self.assertNotIn(object_scan_api, runtime_service)


if __name__ == "__main__":
    unittest.main()
