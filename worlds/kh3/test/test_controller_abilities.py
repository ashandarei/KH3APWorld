from __future__ import annotations

from worlds.kh3.test.runtime_sources import native_project_root

import unittest
from pathlib import Path

from .runtime_sources import read_vbonus_sources

from ..Data import CONTROLLER_UNLOCK_ITEMS


class TestControllerAbilities(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.repo_root = Path(__file__).resolve().parents[3]
        cls.probe_root = native_project_root() / "KH3APProbeCpp"


    def test_controller_unlock_ap_grants_use_sora_ability_markers(self) -> None:
        helper = (self.probe_root / "kh3ap" / "features" / "grants" / "delivery" / "item_grant_apply_helpers.cpp").read_text(
            encoding="utf-8"
        )
        player_root = self.probe_root / "kh3ap" / "features" / "player"
        controller_sources = sorted((player_root / "controller_abilities").glob("*.cpp"))
        controller_sources.extend(sorted((player_root / "controller_abilities").glob("*.h")))
        service = "\n".join(source.read_text(encoding="utf-8") for source in controller_sources)
        ability_bank = read_vbonus_sources()
        config = (self.probe_root / "kh3ap" / "app" / "config.h").read_text(encoding="utf-8")

        self.assertIn("BP_SetAbilityAthleticFlow", config)
        self.assertIn('request.grant_method == STR("controller_unlock")', helper)
        self.assertIn("controller_abilities.apply_unlock_item", helper)
        for key, ability_id in (
            ("wall_run", 113),
            ("style_change", 114),
            ("magic", 115),
            ("attraction_flow", 116),
        ):
            self.assertIn(f'STR("{key}")', service)
            self.assertRegex(service, rf'uint8_t {key}\s*=\s*{ability_id};')
            self.assertRegex(
                service,
                rf'STR\("{key}"\),\s*STR\("[^\"]+"\),\s*ability_id::{key},',
            )
        for enum_name in (
            "AP_WALL_RUN",
            "AP_STYLE_CHANGE",
            "AP_MAGIC",
            "AP_ATTRACTION_FLOW",
        ):
            self.assertIn(f"ETresAbilityKind::{enum_name}", service)
        for style_id in (
            "00",
            "01",
            "02",
            "03",
            "04",
            "05",
            "06",
            "07",
            "08",
            "09",
            "10",
            "11",
            "12",
            "13",
            "14",
            "17",
            "18",
        ):
            self.assertIn(f"AP_KEYBLADE_STYLE_{style_id}", service)
        self.assertIn("try_apply_ability_enum_augmentation", service)
        self.assertIn('STR("/Script/TresGame.ETresAbilityKind")', service)
        refresh_source = (player_root / "controller_abilities" / "refresh.cpp").read_text(encoding="utf-8")
        sync_body = refresh_source[refresh_source.index("RuntimeControllerAbilityService::Impl::process_pending_refresh(") :]
        self.assertLess(
            sync_body.index("try_apply_ability_enum_augmentation();"),
            sync_body.index("config_.runtime_state.grant_state().pawn_state().object();"),
        )
        self.assertIn("try_grant_unique_equipped_runtime_sora_ability", service)
        self.assertIn("RuntimeControllerAbilityGrantResult", service)
        self.assertIn("try_present_native_vbonus_hud_ability_tokens", helper)
        self.assertIn("maybe_present_controller_unlock_ability", helper)
        self.assertIn("state.owned && state.equipped", service)
        self.assertIn("try_read_unlock_owned", service)
        self.assertIn("if (active_owned || staging_owned)", ability_bank)
        self.assertIn(
            "*active_target |= s_ability_equipped_flag | s_ability_unseen_flag",
            ability_bank,
        )

    def test_controller_unlock_ability_ids(self) -> None:
        self.assertEqual(
            {item_name: metadata["ability_id"] for item_name, metadata in CONTROLLER_UNLOCK_ITEMS.items()},
            {
                "Unlock: Wall Run": 113,
                "Unlock: Style Change": 114,
                "Unlock: Magic": 115,
                "Unlock: Attraction Flow": 116,
                "Unlock: Airstep": None,
                "Unlock: Links": 134,
                "Style Change: Spellweaver": None,
            },
        )




if __name__ == "__main__":
    unittest.main()
