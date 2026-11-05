import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

MODULE_PATH = Path(__file__).resolve().parents[3] / "worlds/kh3/client_support/mom_results.py"
SPEC = importlib.util.spec_from_file_location("mom_results", MODULE_PATH)
reader = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(reader)


class ResultReaderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "results").mkdir()
        self.request = str(uuid4())

    def write_result(self, **changes):
        record = dict(
            schemaVersion=1, eventId=str(uuid4()), sessionId=str(uuid4()), requestId=self.request,
            songId=0, difficulty=0, sceneId=7, outcome="cleared", nativeExit="Clear",
            startedAt="2026-09-11T20:00:00Z", finishedAt="2026-09-11T20:03:00Z",
            cleared=True, eligibleForChallenge=True, playMode="Normal", startSeekTime=0,
            demoPlay=False, autoPlay=False, assisted=False, score=500000,
        )
        record.update(changes)
        (self.root / "results" / (record["eventId"] + ".json")).write_text(json.dumps(record), encoding="utf-8")
        return record

    def read(self, **kwargs):
        return reader.read_song_results(self.root, request_id=self.request, song_id=0, difficulty=0, **kwargs)

    def test_clear_and_retry_survive_offline_read_and_deduplicate_after_ack(self):
        failed = self.write_result(outcome="failed", nativeExit="GameOver", cleared=False, eligibleForChallenge=False)
        cleared = self.write_result()
        results = self.read()
        self.assertEqual(2, len(results))
        self.assertEqual([cleared["eventId"]], [r["eventId"] for r in results if r["eligibleForChallenge"]])
        self.assertEqual([], self.read(seen_event_ids=[failed["eventId"], cleared["eventId"]]))
        self.assertEqual(2, len(self.read()))  # Reads do not prematurely acknowledge.

    def test_wrong_request_song_and_difficulty_are_not_replayed(self):
        self.write_result(requestId=str(uuid4()))
        self.write_result(songId=1)
        self.write_result(difficulty=2)
        self.write_result(requestId=None)
        self.assertEqual([], self.read())

    def test_start_quit_failure_and_inconsistent_clear_never_grant(self):
        self.write_result(outcome="playing", nativeExit=None, finishedAt=None, cleared=False)
        self.write_result(outcome="aborted", nativeExit="Retry", cleared=False)
        self.write_result(outcome="failed", nativeExit="GameOver", cleared=False)
        self.write_result(nativeExit="BackToMenu")
        self.write_result(cleared=False)
        self.assertEqual(2, len(self.read()))
        self.assertFalse(any(r["eligibleForChallenge"] for r in self.read()))

    def test_nonstandard_or_unknown_playback_never_grants(self):
        cases = [dict(demoPlay=True), dict(autoPlay=True), dict(assisted=True), dict(playMode="OneButton"),
                 dict(sceneId=5), dict(startSeekTime=15), dict(autoPlay=None), dict(score=True)]
        for changes in cases:
            self.write_result(**changes)
        self.assertEqual(len(cases), len(self.read()))
        self.assertFalse(any(r["eligibleForChallenge"] for r in self.read()))

    def test_partial_bad_schema_and_invalid_identity_do_not_hide_valid_record(self):
        (self.root / "results" / (str(uuid4()) + ".json")).write_text('{"schemaVersion":', encoding="utf-8")
        (self.root / "results" / (str(uuid4()) + ".json.tmp")).write_text("unfinished", encoding="utf-8")
        self.write_result(schemaVersion=2)
        self.write_result(schemaVersion=True)
        self.write_result(finishedAt="yesterday")
        self.write_result(finishedAt="2026-09-11T19:00:00Z")
        self.write_result(eventId="not-a-uuid")
        good = self.write_result()
        self.assertEqual([good["eventId"]], [r["eventId"] for r in self.read()])

    def test_only_the_requested_style_can_clear(self):
        for style in ("Normal", "OneButton", "Performer"):
            self.write_result(playMode=style)
        for style in ("Normal", "OneButton", "Performer"):
            eligible = [r for r in self.read(play_style=style) if r["eligibleForChallenge"]]
            self.assertEqual([style], [r["playMode"] for r in eligible])
        with self.assertRaises(ValueError):
            self.read(play_style="Unknown")

    def test_alternate_styles_still_reject_assistance_and_failures(self):
        for style in ("OneButton", "Performer"):
            self.write_result(playMode=style, assisted=True)
            self.write_result(playMode=style, autoPlay=True)
            self.write_result(playMode=style, demoPlay=True)
            self.write_result(playMode=style, outcome="failed", nativeExit="GameOver", cleared=False)
            self.assertFalse(any(r["eligibleForChallenge"] for r in self.read(play_style=style)))

    def test_absent_bridge_returns_no_results(self):
        self.assertEqual([], reader.read_song_results(self.root / "missing", request_id=self.request, song_id=0, difficulty=0))


if __name__ == "__main__":
    unittest.main()
