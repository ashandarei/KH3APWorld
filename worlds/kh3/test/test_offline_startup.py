"""Deferred startup rechecks ownership after worker reads and seed enrollment."""

import asyncio
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from ..client_support.offline_bridge_service import OfflineBridgeInitializationInput
from ..client_support.offline_startup import OfflineStartupCallbacks, OfflineStartupService
from ..Bridge import BridgeProtocolError


@pytest.fixture
def startup(tmp_path):
    state = SimpleNamespace(current=True)
    source = OfflineBridgeInitializationInput(None, tmp_path / "bridge", tmp_path / "mods", None, None, None, None)
    result = SimpleNamespace(pak_path=tmp_path / "seed.pak", offline_seed_name="AP_new")
    profile = SimpleNamespace(run_id="new-run")
    metadata, reader = Mock(return_value=result), Mock(return_value=profile)
    callbacks = OfflineStartupCallbacks(
        metadata_reader=Mock(return_value=metadata),
        profile_reader=Mock(return_value=reader),
        still_current=lambda source: state.current,
        installed_paths=Mock(return_value=frozenset({result.pak_path.resolve()})),
        enroll=AsyncMock(),
        apply_bridge=Mock(return_value=True),
        apply_profile=Mock(),
    )
    return OfflineStartupService(callbacks), callbacks, state, source, result, profile, metadata, reader


def initialize(fixture):
    service, _, _, source, *_ = fixture
    return asyncio.run(service.initialize(source))


def test_metadata_and_profile_reads_run_off_thread_but_factories_and_application_stay_on_the_loop(startup):
    _, callbacks, _, _, result, profile, metadata, reader = startup
    loop_thread = threading.get_ident()
    metadata.side_effect = lambda source: (
        result if threading.get_ident() != loop_thread else pytest.fail("main-thread read")
    )
    reader.side_effect = lambda: profile if threading.get_ident() != loop_thread else pytest.fail("main-thread read")
    callbacks.metadata_reader.side_effect = lambda: (
        metadata if threading.get_ident() == loop_thread else pytest.fail("worker factory")
    )
    callbacks.profile_reader.side_effect = lambda: (
        reader if threading.get_ident() == loop_thread else pytest.fail("worker factory")
    )
    assert initialize(startup)
    callbacks.enroll.assert_awaited_once_with(result)
    callbacks.apply_bridge.assert_called_once_with(result)
    callbacks.apply_profile.assert_called_once_with(profile)


@pytest.mark.parametrize("stage", ["read", "enroll"])
def test_connection_change_stops_applying_the_offline_result(startup, stage):
    _, callbacks, state, _, result, _, metadata, _ = startup
    if stage == "read":
        metadata.side_effect = lambda source: setattr(state, "current", False) or result
    else:
        callbacks.enroll.side_effect = lambda result: setattr(state, "current", False)
    assert not initialize(startup)
    assert callbacks.enroll.await_count == int(stage == "enroll")
    callbacks.apply_bridge.assert_not_called()
    callbacks.apply_profile.assert_not_called()


def test_replaced_pak_stops_before_enrollment(startup):
    _, callbacks, *_ = startup
    callbacks.installed_paths.return_value = frozenset({Path("other.pak").resolve()})
    assert not initialize(startup)
    callbacks.enroll.assert_not_awaited()
    callbacks.apply_bridge.assert_not_called()
    callbacks.apply_profile.assert_not_called()


def test_missing_profile_is_reloaded_after_enrollment(startup):
    _, callbacks, _, _, _, profile, _, first_reader = startup
    first_reader.return_value = None
    second_reader = Mock(return_value=profile)
    callbacks.profile_reader.side_effect = [first_reader, second_reader]
    assert initialize(startup)
    assert callbacks.profile_reader.call_count == 2
    second_reader.assert_called_once()
    callbacks.apply_profile.assert_called_once_with(profile)


def test_absent_metadata_still_applies_the_remembered_profile(startup):
    _, callbacks, _, _, _, profile, metadata, _ = startup
    metadata.return_value = None
    assert not initialize(startup)
    callbacks.enroll.assert_not_awaited()
    callbacks.installed_paths.assert_not_called()
    callbacks.apply_bridge.assert_not_called()
    callbacks.apply_profile.assert_called_once_with(profile)


@pytest.mark.parametrize("stage", ["metadata", "profile", "enroll", "apply"])
def test_incompatible_metadata_does_not_close_client(startup, stage, caplog):
    _, callbacks, _, _, _, _, metadata, reader = startup
    target = {"metadata": metadata, "profile": reader, "enroll": callbacks.enroll,
              "apply": callbacks.apply_bridge}[stage]
    target.side_effect = BridgeProtocolError("KH3 location catalog mismatch")
    assert not initialize(startup)
    assert "client remains open" in caplog.text


@pytest.mark.parametrize("error", [OSError("read"), asyncio.CancelledError()])
def test_worker_failure_or_cancellation_propagates_without_applying_results(startup, error):
    _, callbacks, _, _, _, _, metadata, _ = startup
    metadata.side_effect = error
    with pytest.raises(type(error)):
        initialize(startup)
    callbacks.enroll.assert_not_awaited()
    callbacks.apply_bridge.assert_not_called()
    callbacks.apply_profile.assert_not_called()
