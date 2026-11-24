"""Warn only for a mismatch from the active KH3 launch."""

import asyncio
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from .. import Client
from ..client_support.runtime_seed_warning import read_seed_startup_mismatch


STARTED = "2026-11-24T15:39:30.218Z"
REJECTED = "[KH3APProbeCpp] Native seed settings unavailable: seed_location_catalog_mismatch"
READY = "[KH3APProbeCpp] Native seed settings/catalog loaded from pak=seed.pak locations=848 loose_files=false"


@pytest.mark.parametrize(("time", "result", "expected"), [
    ("15:39:30", REJECTED, True),
    ("15:39:29", REJECTED, None),
    ("15:39:32", READY, False),
    ("15:39:32", "[KH3APProbeCpp] Native seed settings unavailable: no seed pak", False),
])
def test_reads_only_current_seed_startup_results(tmp_path, time, result, expected):
    log = tmp_path / "UE4SS.log"
    log.write_text(f"[2026-11-24 15:38:31] {REJECTED}\n[2026-11-24 {time}] {result}\n", encoding="utf-8")
    assert read_seed_startup_mismatch(log, datetime.fromisoformat(STARTED)) is expected


def test_watcher_warns_once_across_pipe_reconnections(tmp_path, monkeypatch):
    async def run():
        log = tmp_path / "UE4SS.log"
        log.write_text(f"[2026-11-24 15:39:31] {REJECTED}\n", encoding="utf-8")
        warning_shown = asyncio.Event()
        context = Mock(bridge_dir=tmp_path, kh3_dir=tmp_path, ue4ss_log_override=True, bridge_ue4ss_log=log)
        context.gui_error.side_effect = lambda *_: warning_shown.set()
        pipe = SimpleNamespace(connected=True, connection_generation=1)
        process = Mock(return_value={"running": True, "pid": 37076, "started_at_utc": STARTED})
        monkeypatch.setattr(Client, "get_process_status", process)
        monkeypatch.setattr(Client, "transport_for", lambda _: pipe)

        async def watch(callbacks, **_):
            step = dict(callbacks.steps())["runtime_seed_startup"]
            old_route = callbacks.step_signature("runtime_seed_startup")
            await step()
            await asyncio.wait_for(warning_shown.wait(), timeout=2)
            pipe.connection_generation += 1
            assert callbacks.step_signature("runtime_seed_startup") != old_route
            await step()
            task = next(task for task in asyncio.all_tasks() if task.get_name() == "KH3SeedStartupCheck")
            await task

        monkeypatch.setattr(Client, "watch_bridge", watch)
        await Client.bridge_watcher(context)

        assert process.call_count == 2
        context.gui_error.assert_called_once()
        title, message = context.gui_error.call_args.args
        assert title == "KH3 update required"
        assert message == "KH3 version mismatch. Close KH3, click Patch Game, then relaunch."

    asyncio.run(run())
