"""Seed actions release locks and gate launch and pruning on bridge readiness."""

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from ..client_support.run_manager import KH3RunManagerError
from ..client_support.seed_import_service import KH3SeedImportResult, KH3SeedPreparationResult
from ..client_support.seed_preparation import SeedPreparationCallbacks, SeedPreparationService


@pytest.fixture
def seeds():
    importer, manager = Mock(), Mock()
    run = SimpleNamespace(run_id="run", seed_id="seed", slot_name="slot", player=1)
    importer.has_container_source.return_value = True
    importer.prepare_missing_installed_pak.return_value = KH3SeedPreparationResult("installed", installed_run=run)
    manager.ensure_pak_managed.return_value = SimpleNamespace(run=run, copied=True)
    callbacks = SeedPreparationCallbacks(
        begin=Mock(return_value=True),
        finish=Mock(),
        set_status=Mock(),
        has_installed_pak=Mock(return_value=False),
        preferred_slot=Mock(return_value="Sora"),
        import_source=Mock(return_value=KH3SeedImportResult(Path("source.pak"), (run,), ())),
        apply_prepared=Mock(return_value=True),
        ensure_companion=Mock(),
        cleanup_old_runs=AsyncMock(),
        set_warning=Mock(),
    )
    return SeedPreparationService(lambda: importer, lambda: manager, callbacks), importer, manager, callbacks, run


@pytest.mark.parametrize("installed", [True, False])
def test_missing_pak_checks_do_not_take_an_action_lock_when_no_preparation_is_needed(seeds, installed):
    service, importer, _, callbacks, _ = seeds
    callbacks.has_installed_pak.return_value = installed
    importer.has_container_source.return_value = False
    result = asyncio.run(service.prepare_missing(trigger="launching KH3"))
    assert result.status == ("already_installed" if installed else "no_source")
    callbacks.begin.assert_not_called()
    callbacks.finish.assert_not_called()
    callbacks.apply_prepared.assert_not_called()


@pytest.mark.parametrize("trigger", ["launching KH3", "connecting"])
@pytest.mark.parametrize("bridge_ready", [True, False])
def test_launch_requires_a_ready_bridge_and_pruning_waits_for_readiness(seeds, trigger, bridge_ready):
    service, importer, _, callbacks, _ = seeds
    callbacks.apply_prepared.return_value = bridge_ready
    result = asyncio.run(service.prepare_missing(trigger=trigger))
    expected = (
        None if trigger == "launching KH3" and not bridge_ready else importer.prepare_missing_installed_pak.return_value
    )
    assert result is expected
    callbacks.ensure_companion.assert_called_once()
    if bridge_ready:
        callbacks.cleanup_old_runs.assert_awaited_once_with("run")
    else:
        callbacks.cleanup_old_runs.assert_not_called()
    callbacks.finish.assert_called_once()


def test_missing_pak_preparation_applies_the_bridge_before_companion_and_pruning(seeds):
    service, importer, _, callbacks, _ = seeds
    events = []
    importer.prepare_missing_installed_pak.side_effect = lambda **kwargs: (
        events.append("prepare") or importer.prepare_missing_installed_pak.return_value
    )
    callbacks.apply_prepared.side_effect = lambda result: events.append("apply") or True
    callbacks.ensure_companion.side_effect = lambda: events.append("companion")
    callbacks.cleanup_old_runs.side_effect = lambda run: events.append("prune")
    callbacks.finish.side_effect = lambda: events.append("finish")
    asyncio.run(service.prepare_missing(trigger="launching KH3"))
    assert events == ["prepare", "apply", "companion", "prune", "finish"]


@pytest.mark.parametrize("action", ["missing", "import", "enroll"])
def test_busy_seed_actions_neither_mutate_files_nor_release_another_lock(seeds, action):
    service, importer, manager, callbacks, _ = seeds
    callbacks.begin.return_value = False
    task = {
        "missing": lambda: service.prepare_missing(trigger="launching KH3"),
        "import": lambda: service.import_source("source.pak"),
        "enroll": lambda: service.enroll(Path("source.pak"), "seed", None),
    }[action]()
    asyncio.run(task)
    importer.prepare_missing_installed_pak.assert_not_called()
    callbacks.import_source.assert_not_called()
    manager.ensure_pak_managed.assert_not_called()
    callbacks.finish.assert_not_called()


@pytest.mark.parametrize("action", ["missing", "import", "enroll"])
@pytest.mark.parametrize("error", [OSError("disk"), asyncio.CancelledError()])
def test_seed_action_errors_release_owned_locks_and_cancellation_propagates(seeds, action, error):
    service, importer, manager, callbacks, _ = seeds
    if action == "missing":
        importer.prepare_missing_installed_pak.side_effect = error
        task = service.prepare_missing(trigger="launching KH3")
    elif action == "import":
        callbacks.import_source.side_effect = error
        task = service.import_source("source.pak")
    else:
        manager.ensure_pak_managed.side_effect = error
        task = service.enroll(Path("source.pak"), "seed", None)
    if isinstance(error, asyncio.CancelledError):
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(task)
    else:
        asyncio.run(task)
    callbacks.finish.assert_called_once()
    callbacks.apply_prepared.assert_not_called()


def test_incomplete_import_profile_is_rejected_before_import(seeds):
    service, _, _, callbacks, _ = seeds
    asyncio.run(service.import_source("source.pak", slot_name="Sora"))
    callbacks.import_source.assert_not_called()
    callbacks.finish.assert_called_once()


def test_import_passes_the_complete_connection_profile(seeds, tmp_path):
    service, _, _, callbacks, _ = seeds
    source = tmp_path / "seed.apkh3"
    asyncio.run(service.import_source(source, slot_name="Sora", server="host", port="38281", room_url=""))
    callbacks.import_source.assert_called_once_with(
        source.resolve(),
        progress=callbacks.set_status,
        server_address="host:38281",
        connection_slot_name="Sora",
        room_url="",
    )


@pytest.mark.parametrize("server", ["host:38281", None])
def test_enrollment_only_remembers_a_login_for_a_server(seeds, server):
    service, _, manager, callbacks, run = seeds
    run.server_address = "host:38281"
    asyncio.run(service.enroll(Path("source.pak"), "seed", server, connection_slot_name=" Sora "))
    manager.ensure_pak_managed.assert_called_once_with(
        Path("source.pak"),
        expected_seed_id="seed",
        server_address=server,
        connection_slot_name="Sora" if server else None,
        room_url=None,
    )
    callbacks.finish.assert_called_once()


def test_expected_enrollment_error_is_reported_without_losing_action_ownership(seeds, caplog):
    service, _, manager, callbacks, _ = seeds
    manager.ensure_pak_managed.side_effect = KH3RunManagerError("invalid pak")
    asyncio.run(service.enroll(Path("source.pak"), "seed", None))
    assert "Could not automatically store the KH3 run: invalid pak" in caplog.text
    callbacks.finish.assert_called_once()
