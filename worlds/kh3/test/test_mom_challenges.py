from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from ..Bridge import build_bridge_slot_data
from ..Client import KH3Context
from ..client_support import mom_challenges as mom
from ..client_support.mom_settings import read_launch_settings
from ..MelodyOfMemory import MOM_LOCATION_ID_BASE, song_location


class TestMoMChallenges(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.slot = {"kh3_seed": {"seed_name": "MoMTest", "slot": 1, "slot_name": "Player"},
                     "bridge": build_bridge_slot_data([song_location(0, 0)], 7)}
        self.service = mom.MoMChallengeService(self.root / "bridge", self.slot)
        self.game = self.root / "game"
        self.events = self.game / "UserData/MoMBridge/results"
        self.events.mkdir(parents=True)
        self.request = str(uuid4())
        self.service.state["requests"][self.request] = {"location_id": MOM_LOCATION_ID_BASE,
            "game_dir": str(self.game), "seen": [], "difficulty": 0,
            "created_at": 0, "status": "Playing in MoM"}
        self.service.save()

    def event(self, **changes):
        record = dict(schemaVersion=1, eventId=str(uuid4()), sessionId=str(uuid4()), requestId=self.request,
            songId=0, difficulty=0, sceneId=7, outcome="cleared", nativeExit="Clear",
            startedAt="2026-09-11T20:00:00Z", finishedAt="2026-09-11T20:03:00Z",
            cleared=True, eligibleForChallenge=True, playMode="Normal", startSeekTime=0,
            demoPlay=False, autoPlay=False, assisted=False, score=500000)
        record.update(changes)
        (self.events / (record["eventId"] + ".json")).write_text(json.dumps(record), encoding="utf-8")

    def test_offline_failed_then_retry_clear_survives_client_restart(self):
        self.event(outcome="failed", nativeExit="GameOver", cleared=False, eligibleForChallenge=False)
        self.assertEqual(set(), self.service.poll())
        self.assertEqual(1, len(self.service.state["requests"][self.request]["seen"]))
        self.event()
        restarted = mom.MoMChallengeService(self.root / "bridge", self.slot)
        self.assertEqual({MOM_LOCATION_ID_BASE}, restarted.poll())
        self.assertEqual({MOM_LOCATION_ID_BASE}, restarted.poll())
        self.assertEqual(2, len(restarted.state["requests"][self.request]["seen"]))
        self.assertEqual([MOM_LOCATION_ID_BASE], json.loads(restarted.path.read_text())["cleared"])

    def test_repeated_song_requires_a_separate_clear_for_each_chest(self):
        from ..MelodyOfMemory import MUSIC_CHEST_PLACEMENTS, placed_music_chest
        rows = [placed_music_chest(p["placement_id"], 0, 0) for p in MUSIC_CHEST_PLACEMENTS[:2]]
        for row in rows:
            row["music_chest_kind"] = "additional"
        slot = {"kh3_seed": self.slot["kh3_seed"], "bridge": build_bridge_slot_data(rows, 7)}
        service = mom.MoMChallengeService(self.root / "repeats", slot)
        first, second = list(service.challenges)
        second_request = str(uuid4())
        for request, location in ((self.request, first), (second_request, second)):
            service.state["requests"][request] = {"location_id": location,
                "game_dir": str(self.game), "seen": [], "difficulty": 0}
        service.save()
        self.event()
        self.assertEqual({first}, service.poll())
        restarted = mom.MoMChallengeService(self.root / "repeats", slot)
        self.assertEqual({first}, restarted.poll())
        self.event(requestId=second_request)
        self.assertEqual({first, second}, restarted.poll())

    def test_stale_different_song_difficulty_and_seed_never_clear(self):
        self.event(requestId=str(uuid4()))
        self.event(songId=1)
        self.event(difficulty=2)
        self.assertEqual(set(), self.service.poll())
        self.event()
        self.slot["kh3_seed"]["seed_name"] = "OtherSeed"
        other = mom.MoMChallengeService(self.root / "bridge", self.slot)
        self.assertEqual(set(), other.poll())
        self.assertNotEqual(self.service.path, other.path)

    def test_clear_must_persist_before_it_can_be_sent(self):
        self.event()
        with patch.object(mom, "atomic_write_json", side_effect=OSError("locked")):
            with self.assertRaises(OSError):
                self.service.poll()
            with self.assertRaises(OSError):
                self.service.poll()
        self.assertEqual({MOM_LOCATION_ID_BASE}, self.service.poll())
        self.assertEqual([MOM_LOCATION_ID_BASE], json.loads(self.service.path.read_text())["cleared"])

    def test_launcher_persists_binding_before_dispatch_and_uses_epic(self):
        def dispatch(uri):
            request = json.loads((self.game / "UserData/MoMSongLaunch-request.json").read_text())
            saved = json.loads(self.service.path.read_text())
            self.assertIn(request["RequestId"], saved["requests"])
            self.assertEqual((0, 0, True), (request["SongId"], request["Difficulty"], request["FastBoot"]))
            self.assertEqual("Classic", request["Party"])
            self.assertEqual(0, saved["requests"][request["RequestId"]]["difficulty"])
            self.assertEqual("com.epicgames.launcher://apps/test?action=launch", uri)
        with patch.object(mom, "discover_mom_install", return_value=(self.game, "com.epicgames.launcher://apps/test?action=launch")), \
                patch.object(mom, "stop_mom"), patch.object(mom.os, "startfile", side_effect=dispatch, create=True):
            self.service.launch(MOM_LOCATION_ID_BASE)

    def test_launcher_does_not_replace_another_clients_request(self):
        path = self.game / "UserData/MoMSongLaunch-request.json"
        payload = json.dumps({"RequestId": str(uuid4())})
        path.write_text(payload)
        with patch.object(mom, "discover_mom_install", return_value=(self.game, "epic")), patch.object(mom, "stop_mom") as stop:
            with self.assertRaises(RuntimeError):
                self.service.launch(MOM_LOCATION_ID_BASE)
            stop.assert_not_called()
        self.assertEqual(payload, path.read_text())

    def test_menu_preferences_reach_launch_and_survive_settings_change(self):
        settings_path = self.root / "bridge/mom_settings.json"
        settings_path.write_text(json.dumps({"schema_version": 1, "play_style": 2,
                                            "starting_item": 3, "party": 1, "difficulty": 3}))
        with patch.object(mom, "discover_mom_install", return_value=(self.game, "epic")), \
                patch.object(mom, "stop_mom"), patch.object(mom.os, "startfile", create=True):
            self.service.launch(MOM_LOCATION_ID_BASE)
        request = json.loads((self.game / "UserData/MoMSongLaunch-request.json").read_text())
        self.assertEqual(("Performer", "SummoningStar"), (request["PlayStyle"], request["StartingItem"]))
        self.assertEqual(("Days", 2), (request["Party"], request["Difficulty"]))
        saved = json.loads(self.service.path.read_text())["requests"][request["RequestId"]]
        self.assertEqual(("Days", 2), (saved["party"], saved["difficulty"]))
        self.request = request["RequestId"]
        settings_path.write_text(json.dumps({"schema_version": 1, "play_style": 1,
                                            "starting_item": 1, "party": 3, "difficulty": 1}))
        restarted = mom.MoMChallengeService(self.root / "bridge", self.slot)
        self.event(playMode="OneButton", difficulty=2)
        self.assertEqual(set(), restarted.poll())
        self.event(playMode="Performer")
        self.assertEqual(set(), restarted.poll())
        self.event(playMode="Performer", difficulty=2)
        self.assertEqual({MOM_LOCATION_ID_BASE}, restarted.poll())

    def test_yaml_preferences_reach_launch_and_menu_overrides_win(self):
        self.slot.update(melody_of_memory_party=3, melody_of_memory_play_style=2,
                         melody_of_memory_starting_item=3)
        self.service = mom.MoMChallengeService(self.root / "bridge", self.slot)
        with patch.object(mom, "discover_mom_install", return_value=(self.game, "epic")), \
                patch.object(mom, "stop_mom"), patch.object(mom.os, "startfile", create=True):
            self.service.launch(MOM_LOCATION_ID_BASE)
        request = json.loads((self.game / "UserData/MoMSongLaunch-request.json").read_text())
        self.assertEqual(("BBS", "Performer", "SummoningStar"),
                         (request["Party"], request["PlayStyle"], request["StartingItem"]))
        path = self.root / "bridge/mom_settings.json"
        path.write_text(json.dumps({"schema_version": 1, "party": 0, "play_style": 0,
                                   "starting_item": 0}))
        self.assertEqual({"Party": "Classic", "PlayStyle": "Normal", "StartingItem": "None", "Difficulty": 2},
                         read_launch_settings(path.parent, seed_difficulty=2, seed_options=self.slot))
        path.write_text(json.dumps({"schema_version": 1, "party": True, "play_style": 99}))
        self.assertEqual({"Party": "BBS", "PlayStyle": "Performer", "StartingItem": "SummoningStar", "Difficulty": 2},
                         read_launch_settings(path.parent, seed_difficulty=2, seed_options=self.slot))

    def test_settings_defaults_and_invalid_data(self):
        path = self.root / "bridge/mom_settings.json"
        default = {"PlayStyle": "Normal", "StartingItem": "None", "Party": "Classic", "Difficulty": 2}
        self.assertEqual(default, read_launch_settings(path.parent, seed_difficulty=2))
        for data in ([], {"schema_version": 2, "play_style": 1},
                     {"schema_version": True, "play_style": 1},
                     {"schema_version": 1, "play_style": True, "starting_item": -1},
                     {"schema_version": 1, "play_style": 99, "starting_item": "Potion"},
                     {"schema_version": 1, "party": True, "difficulty": True},
                     {"schema_version": 1, "party": -1, "difficulty": -1},
                     {"schema_version": 1, "party": 4, "difficulty": 4},
                     {"schema_version": 1, "party": "Days", "difficulty": "Proud"}):
            path.write_text(json.dumps(data))
            self.assertEqual(default, read_launch_settings(path.parent, seed_difficulty=2))
        path.write_text('{"schema_version":')
        self.assertEqual(default, read_launch_settings(path.parent, seed_difficulty=2))
        for style_index, style in enumerate(("Normal", "OneButton", "Performer")):
            for item_index, item in enumerate(("None", "Potion", "MegaPotion", "SummoningStar")):
                path.write_text(json.dumps({"schema_version": 1,
                    "play_style": style_index, "starting_item": item_index}))
                self.assertEqual({**default, "PlayStyle": style, "StartingItem": item},
                                 read_launch_settings(path.parent, seed_difficulty=2))

    def test_party_and_difficulty_choices_preserve_other_preferences(self):
        path = self.root / "bridge/mom_settings.json"
        for seed_difficulty in range(3):
            for party_index, party in enumerate(("Classic", "Days", "3D", "BBS")):
                for difficulty_index, difficulty in enumerate((seed_difficulty, 0, 1, 2)):
                    with self.subTest(seed=seed_difficulty, party=party, difficulty=difficulty_index):
                        path.write_text(json.dumps({"schema_version": 1, "play_style": 1,
                            "starting_item": 2, "party": party_index, "difficulty": difficulty_index}))
                        self.assertEqual({"PlayStyle": "OneButton", "StartingItem": "MegaPotion",
                                          "Party": party, "Difficulty": difficulty},
                                         read_launch_settings(path.parent, seed_difficulty=seed_difficulty))

    def test_beginner_override_can_clear_a_proud_seed_challenge(self):
        self.slot["bridge"] = build_bridge_slot_data([song_location(0, 2)], 7)
        service = mom.MoMChallengeService(self.root / "bridge", self.slot)
        path = self.root / "bridge/mom_settings.json"
        path.write_text(json.dumps({"schema_version": 1, "difficulty": 1, "party": 2}))
        with patch.object(mom, "discover_mom_install", return_value=(self.game, "epic")), \
                patch.object(mom, "stop_mom"), patch.object(mom.os, "startfile", create=True):
            service.launch(MOM_LOCATION_ID_BASE)
        request = json.loads((self.game / "UserData/MoMSongLaunch-request.json").read_text())
        self.assertEqual((0, "3D"), (request["Difficulty"], request["Party"]))
        self.request = request["RequestId"]
        self.event(difficulty=2)
        self.assertEqual(set(), service.poll())
        self.event(difficulty=0)
        self.assertEqual({MOM_LOCATION_ID_BASE}, service.poll())
        self.assertEqual(2, service.challenges[MOM_LOCATION_ID_BASE]["mom_difficulty"])

    def test_adapter_sends_only_to_matching_connected_seed_and_awaits_ack(self):
        self.event()
        ctx = KH3Context.__new__(KH3Context)
        ctx.__dict__.update(_get_mom_challenge_service=lambda: self.service,
            _is_ap_connected=lambda: True, slot=1, seed_name="MoMTest",
            checked_locations=set(), send_msgs=AsyncMock(), bridge_dir=self.root / "bridge")
        async def run():
            await KH3Context.handle_mom_challenge_results(ctx)
            ctx.send_msgs.assert_awaited_once_with([{"cmd": "LocationChecks", "locations": [MOM_LOCATION_ID_BASE]}])
            ctx.send_msgs.reset_mock()
            ctx.checked_locations.add(MOM_LOCATION_ID_BASE)
            await KH3Context.handle_mom_challenge_results(ctx)
            ctx.send_msgs.assert_not_awaited()
            ctx.checked_locations.clear()
            ctx.seed_name = "WrongSeed"
            await KH3Context.handle_mom_challenge_results(ctx)
            ctx.send_msgs.assert_not_awaited()
            ctx.seed_name = "MoMTest"
            ctx._is_ap_connected = lambda: False
            await KH3Context.handle_mom_challenge_results(ctx)
            ctx.send_msgs.assert_not_awaited()
        asyncio.run(run())
