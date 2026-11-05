from pathlib import Path
from tempfile import TemporaryDirectory
from typing import ClassVar
from unittest import TestCase
from unittest.mock import patch

from ..client_support.deathlink_settings import initialize_settings, read_settings, write_settings
from ..transport.pipe_transport import register, unregister
from . import KH3TestBase


class TestDeathLinkAmnestySlotData(KH3TestBase):
    options: ClassVar[dict] = {"death_link": 1, "death_link_amnesty": 3}

    def test_exports_amnesty(self):
        slot_data = self.world.build_runtime_slot_data()
        self.assertEqual(slot_data["death_link"], 1)
        self.assertEqual(slot_data["death_link_amnesty"], 3)


class TestDeathLinkPreferences(TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        write_settings(self.root, True, 3)

    def test_seed_defaults_and_saved_override(self):
        fresh = self.root / "fresh"
        fresh.mkdir()
        self.assertEqual(initialize_settings(fresh, {}), (False, 0))
        write_settings(fresh, True, 20)
        self.assertEqual(initialize_settings(fresh, {"death_link_amnesty": 3}), (True, 20))
        (fresh / "deathlink_settings.json").unlink()
        self.assertEqual(initialize_settings(fresh, {
            "death_link": 1, "death_link_amnesty": 3,
        }), (True, 3))

    def test_malformed_settings_fall_back_to_seed_defaults(self):
        path = self.root / "deathlink_settings.json"
        for content in ("null", "[]", "{", '{"schema_version":99}',
                        '{"schema_version":1,"enabled":"false","amnesty":-1}'):
            with self.subTest(content=content):
                path.write_text(content, encoding="utf-8")
                self.assertEqual(read_settings(self.root, {
                    "death_link": 1, "death_link_amnesty": 3,
                }), (True, 3))

    def test_live_cache_observes_native_menu_preferences(self):
        transport = register(self.root)
        try:
            self.assertEqual(read_settings(self.root, {}), (True, 3))
            with patch.object(Path, "read_text", side_effect=AssertionError("disk polling")):
                self.assertEqual(read_settings(self.root, {}), (True, 3))
            replacement = self.root / "menu.tmp"
            replacement.write_text('{"schema_version":1,"enabled":0,"amnesty":5}', encoding="utf-8")
            replacement.replace(self.root / "deathlink_settings.json")
            transport.state._invalidate({"deathlink_settings.json"})
            self.assertEqual(read_settings(self.root, {}), (False, 5))
            self.assertNotIn("deathlink_settings.json", transport.pending)
        finally:
            unregister(transport)
