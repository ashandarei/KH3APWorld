"""MoM polling work stays bounded without weakening result or seed validation."""
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from .. import Client as client
from ..Bridge import build_bridge_slot_data
from ..client_support import mom_challenges as mom
from ..client_support.json_snapshot import JsonArray, copy_json, freeze_slot_location_catalog
from ..client_support.mom_results import SongResultScan, read_song_results
from ..MelodyOfMemory import MOM_LOCATION_ID_BASE, song_location


@pytest.fixture
def challenge():
    from .test_mom_challenges import TestMoMChallenges
    case = TestMoMChallenges()
    case.setUp()
    try:
        yield case
    finally:
        case.doCleanups()


def test_immutable_catalog_skips_traversal_but_tracks_replacement_seed_and_directory(challenge):
    ctx = client.KH3Context.__new__(client.KH3Context)
    ctx.bridge_dir = challenge.root / "cached"
    ctx.slot_data = freeze_slot_location_catalog(challenge.slot)
    service = ctx._get_mom_challenge_service()
    with patch.object(JsonArray, "__iter__", side_effect=AssertionError("unchanged catalog traversal")):
        assert ctx._get_mom_challenge_service() is service
        assert ctx._get_mom_challenge_service() is service
    # Incoming packet ownership is detached; replacement catalogs are revisions.
    challenge.slot["bridge"]["locations"][0]["assigned_item_name"] = "Changed reward"
    assert "assigned_item_name" not in ctx.slot_data["bridge"]["locations"][0]
    ctx.slot_data = freeze_slot_location_catalog(challenge.slot)
    changed = ctx._get_mom_challenge_service()
    assert changed is not service
    assert next(iter(changed.challenges.values()))["assigned_item_name"] == "Changed reward"
    ctx.slot_data["kh3_seed"] = {**ctx.slot_data["kh3_seed"], "seed_name": "other"}
    other = ctx._get_mom_challenge_service()
    assert other is not changed
    ctx.bridge_dir = challenge.root / "other"
    assert ctx._get_mom_challenge_service() is not other
    ctx.slot_data = freeze_slot_location_catalog({"bridge": {"locations": []}})
    assert ctx._get_mom_challenge_service() is None


def test_pending_requests_share_scan_and_consumed_files_are_not_reopened(challenge):
    challenge.slot["bridge"] = build_bridge_slot_data([song_location(0, 0), song_location(1, 0)], 7)
    service = mom.MoMChallengeService(challenge.root / "multi", challenge.slot)
    service.state["requests"] = copy_json(challenge.service.state["requests"])
    second = str(uuid4())
    service.state["requests"][second] = {
        **service.state["requests"][challenge.request], "location_id": MOM_LOCATION_ID_BASE + 1, "seen": []}
    challenge.event(outcome="failed", nativeExit="GameOver", cleared=False, eligibleForChallenge=False)
    challenge.event(requestId=second, songId=1, outcome="failed", nativeExit="GameOver",
                    cleared=False, eligibleForChallenge=False)
    original_glob, original_read = Path.glob, Path.read_text
    scanned, opened = [], []

    def glob(path, pattern, **kwargs):
        scanned.append(path)
        return original_glob(path, pattern, **kwargs)

    def read(path, *args, **kwargs):
        opened.append(path)
        return original_read(path, *args, **kwargs)

    with patch.object(Path, "glob", glob), patch.object(Path, "read_text", read):
        assert service.poll() == set()
        assert scanned == [challenge.events]
        assert len(opened) == 2
        assert all(len(record["seen"]) == 1 for record in service.state["requests"].values())
        scanned.clear()
        opened.clear()
        assert service.poll() == set()
        assert scanned == [challenge.events]
        assert opened == []
    # A new result still arrives through the next scan and retains request scope.
    challenge.event(requestId=second, songId=0)  # Wrong song must not satisfy request 2.
    assert service.poll() == set()
    challenge.event(requestId=second, songId=1)
    assert service.poll() == {MOM_LOCATION_ID_BASE + 1}


def test_shared_scan_does_not_leak_eligibility_between_requested_styles(challenge):
    challenge.event(playMode="Performer")
    directory = challenge.events.parent
    scan = SongResultScan(directory)
    options = dict(request_id=challenge.request, song_id=0, difficulty=0, scan=scan)
    assert not read_song_results(directory, **options)[0]["eligibleForChallenge"]
    assert read_song_results(directory, play_style="Performer", **options)[0]["eligibleForChallenge"]
    with pytest.raises(ValueError, match="another MoM bridge"):
        read_song_results(directory / "other", **options)


def test_seed_switch_during_result_poll_does_not_publish_or_send_to_successor(challenge):
    service = challenge.service
    ctx = client.KH3Context.__new__(client.KH3Context)
    ctx.__dict__.update(_get_mom_challenge_service=lambda: service,
                          _is_ap_connected=lambda: True,
                          slot=1, seed_name="MoMTest", checked_locations=set(), send_msgs=AsyncMock(),
                          bridge_dir=challenge.root / "bridge")

    def poll():
        ctx._get_mom_challenge_service = lambda: None
        return {MOM_LOCATION_ID_BASE}

    with patch.object(service, "poll", side_effect=poll), patch.object(service, "publish_clears") as publish:
        asyncio.run(client.KH3Context.handle_mom_challenge_results(ctx))
    publish.assert_not_called()
    ctx.send_msgs.assert_not_awaited()
