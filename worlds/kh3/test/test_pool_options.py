from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

import yaml

from Options import generate_yaml_templates

from ..Minigames import MINIGAME_POOL_OPTION_NAMES, MINIGAME_RANK_OPTIONS
from ..Options import (
    EquipmentAbilitiesPool,
    FullcourseAbilitiesPool,
    Ingredients,
    KeybladeUpgradeRewardsPool,
    KH3LocationPoolMode,
    KH3Options,
    OlympusColiseumPool,
    PhotoMissionsPool,
    SynthesisPool,
)

VANILLA_POOLS = (EquipmentAbilitiesPool, FullcourseAbilitiesPool, SynthesisPool, PhotoMissionsPool, KeybladeUpgradeRewardsPool)


class TestRewardPoolOptions(TestCase):
    def test_new_reward_settings_only_accept_active_modes(self):
        for name, option_type in KH3Options.type_hints.items():
            if not issubclass(option_type, KH3LocationPoolMode):
                continue
            with self.subTest(option=name):
                expected = {2: "randomize", 3: "junk"}
                if option_type is OlympusColiseumPool:
                    expected[1] = "off"
                if option_type in VANILLA_POOLS:
                    expected[1] = "vanilla"
                self.assertEqual(option_type.name_lookup, expected)
                self.assertIn(option_type.default, expected)
                self.assertEqual(option_type.from_any("replace").value, 3)
                if option_type in VANILLA_POOLS:
                    self.assertEqual(option_type.from_any("vanilla").value, 1)
                else:
                    with self.assertRaises(KeyError):
                        option_type.from_any("vanilla")
                if 1 not in expected:
                    with self.assertRaises(KeyError):
                        option_type.from_any(1)

    def test_generated_yaml_has_supported_reward_choices(self):
        with TemporaryDirectory() as folder:
            generate_yaml_templates(folder)
            template_text = (Path(folder) / "Kingdom Hearts III.yaml").read_text(encoding="utf-8-sig")
            settings = yaml.safe_load(
                template_text
            )["Kingdom Hearts III"]
        self.assertEqual(set(settings["ingredients"]), {"off", "ingredientsanity", "ingredientsanity_plus"})
        self.assertEqual(settings["ingredients"]["off"], 50)
        self.assertEqual(settings["ingredients"]["ingredientsanity"], 0)
        self.assertEqual(settings["ingredients"]["ingredientsanity_plus"], 0)
        self.assertNotIn("Experimental: ingredient pickup checks", template_text)
        self.assertEqual(Ingredients.display_name, "Ingredients")
        for name, option_type in KH3Options.type_hints.items():
            if issubclass(option_type, KH3LocationPoolMode):
                with self.subTest(option=name):
                    expected = {"randomize", "junk"}
                    if option_type is OlympusColiseumPool:
                        expected.add("off")
                    if option_type in VANILLA_POOLS:
                        expected.add("vanilla")
                    self.assertEqual(set(settings[name]), expected)
        self.assertNotIn("minigames_pool", settings)
        self.assertEqual({name for name in settings if name.startswith("kh02_")},
                         {"kh02_enabled", "kh02_challenges"})
        for name in ("kh02_enabled", "kh02_challenges"):
            self.assertEqual(settings[name], {"false": 0, "true": 50})
        for removed in ("extra_movement", "moogle_workshop_pool", "junk_photo_missions",
                        "default_critical_abilities", "default_bonus_critical_abilities"):
            self.assertNotIn(removed, settings)
        self.assertTrue(set(MINIGAME_POOL_OPTION_NAMES).issubset(settings))
        for option in MINIGAME_RANK_OPTIONS:
            self.assertEqual(set(settings[option]), {"junk", "d", "c", "b", "a"})

    def test_minigame_rank_choices(self):
        for option in MINIGAME_RANK_OPTIONS:
            with self.subTest(option=option):
                option_type = KH3Options.type_hints[option]
                self.assertEqual(option_type.name_lookup, {0: "junk", 1: "d", 2: "c", 3: "b", 4: "a"})
                self.assertEqual(option_type.default, option_type.option_junk)
                for value, label in enumerate(("Junk", "D", "C", "B", "A")):
                    self.assertEqual(option_type.from_any(label).value, value)
                    self.assertEqual(option_type.get_option_name(value), label)
                with self.assertRaises(KeyError):
                    option_type.from_any("S")
