"""Cache invalidation and retry coverage for idle client work."""
import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from .. import Client as client
from ..client_support.json_snapshot import freeze_json
from ..client_support.secret_report_hint_service import SecretReportHintService


def test_native_report_snapshot_retries_and_republishes_on_reconnect():
    service = SecretReportHintService()
    snapshot = freeze_json({"schema": "kh3ap.secret_report_hints.v1", "owner": "dll",
                            "reports": {"5": [{"player": 1, "location": 2}]}, "missing_report_numbers": []})
    assert service.plan(snapshot).report_numbers == {5}
    assert service.plan(snapshot).report_numbers == {5}
    service.mark_sent({5})
    assert not service.plan(snapshot).messages
    service.reset_for_connection()
    assert service.plan(snapshot).report_numbers == {5}


def test_poptracker_snapshot_retries_and_reconnects():
    ctx = client.KH3Context.__new__(client.KH3Context)
    ctx.bridge_dir = Path("synthetic")
    ctx.slot, ctx.team, ctx.auth, ctx.seed_name = 3, 2, "Player", "Seed"
    ctx.player_names = {3: "Player"}
    ctx.server = SimpleNamespace(socket=SimpleNamespace(closed=False))
    ctx._load_bridge_sidecar_payload = Mock(return_value=freeze_json({
        "sequence": 2, "captured_global_ids": [13], "latest_capture_global_id": 13,
    }))
    ctx.send_msgs = AsyncMock(side_effect=OSError("disconnected"))
    with pytest.raises(OSError):
        asyncio.run(ctx.handle_poptracker_lucky_emblem_state())
    ctx.send_msgs = AsyncMock()
    from ..client_support import poptracker
    with patch.object(poptracker, "build_emblem_payload",
                      wraps=poptracker.build_emblem_payload) as build:
        asyncio.run(ctx.handle_poptracker_lucky_emblem_state())
        asyncio.run(ctx.handle_poptracker_lucky_emblem_state())
        build.assert_called_once()
        ctx._get_poptracker_sync_service().reset_emblems()  # connection reset
        asyncio.run(ctx.handle_poptracker_lucky_emblem_state())
        assert build.call_count == 2
        ctx.team = 4
        asyncio.run(ctx.handle_poptracker_lucky_emblem_state())
        assert build.call_count == 3
    assert ctx.send_msgs.await_count == 3


def test_mom_service_catalog_detects_in_place_edits(tmp_path):
    from .test_music_chest_bonus import paired_slot_data
    ctx = client.KH3Context.__new__(client.KH3Context)
    ctx.bridge_dir = tmp_path
    ctx.slot_data = paired_slot_data()
    first = ctx._get_mom_challenge_service()
    assert ctx._get_mom_challenge_service() is first
    # Equality compares a detached snapshot, not the same mutable row objects.
    ctx.slot_data["bridge"]["locations"][0]["assigned_item_name"] = "Changed reward"
    assert ctx._get_mom_challenge_service() is not first
    second = ctx._get_mom_challenge_service()
    ctx.bridge_dir = tmp_path / "other"
    assert ctx._get_mom_challenge_service() is not second


def test_mom_clear_cache_retries_and_tracks_verified_results(tmp_path):
    from ..client_support import mom_challenges as mom
    from .test_music_chest_bonus import paired_slot_data
    service = mom.MoMChallengeService(tmp_path, paired_slot_data())
    with patch.object(mom, "atomic_write_json", side_effect=OSError("locked")):
        with pytest.raises(OSError):
            service.publish_clears(tmp_path)
    with patch.object(mom, "atomic_write_json") as write:
        service.publish_clears(tmp_path)
        service.publish_clears(tmp_path)
        write.assert_called_once()
        location = next(iter(service.challenges))
        service.state["cleared"].append(location)
        service.publish_clears(tmp_path)
        assert write.call_count == 2
        assert write.call_args.args[1]["cleared_locations"] == [location]
        assert "chests" not in write.call_args.args[1]
        service.publish_clears(tmp_path / "other")
        assert write.call_count == 3
