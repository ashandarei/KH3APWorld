"""Steam Cloud prompts and patch results retain distinct persistence policies."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest

from ..client_support.steam_cloud_client import SteamCloudCallbacks, SteamCloudClientService
from ..platform_support.steam_cloud import SteamCloudDisableResult, SteamCloudDisableStatus, SteamCloudState

STEAM_ROOT = Path("SteamLibrary/steamapps/common/KINGDOM HEARTS III")


@pytest.fixture
def cloud():
    callbacks = SteamCloudCallbacks(
        detect=Mock(return_value=SteamCloudState.ENABLED),
        disable=Mock(),
        load_settings=Mock(return_value={}),
        store_acknowledgement=Mock(),
        print_json=Mock(),
        warn=Mock(),
    )
    return SteamCloudClientService(callbacks), callbacks


@pytest.mark.parametrize("outcome", ["confirm", "dismiss", "failure", "cancel"])
def test_pending_prompt_suppresses_duplicates_and_releases_state(cloud, outcome):
    service, callbacks = cloud

    async def run():
        entered, release = asyncio.Event(), asyncio.Event()

        async def prompt():
            entered.set()
            await release.wait()
            if outcome == "failure":
                raise OSError("modal failed")
            return outcome == "confirm"

        first = asyncio.create_task(service.confirm(STEAM_ROOT, prompt))
        await entered.wait()
        duplicate = AsyncMock(side_effect=AssertionError("duplicate prompt"))
        assert not await service.confirm(STEAM_ROOT, duplicate)
        if outcome == "cancel":
            first.cancel()
            with pytest.raises(asyncio.CancelledError):
                await first
        else:
            release.set()
            assert await first is (outcome == "confirm")
        assert not service.confirmation_pending

    asyncio.run(run())
    assert service.acknowledged is (outcome == "confirm")
    if outcome == "confirm":
        callbacks.store_acknowledgement.assert_called_once_with(True)
    else:
        callbacks.store_acknowledgement.assert_not_called()


def test_prompt_confirmation_must_persist_before_becoming_acknowledged(cloud):
    service, callbacks = cloud
    callbacks.store_acknowledgement.side_effect = OSError("settings locked")
    assert not asyncio.run(service.confirm(STEAM_ROOT, AsyncMock(return_value=True)))
    assert not service.acknowledged and not service.confirmation_pending


def test_successful_patch_remains_acknowledged_when_settings_write_fails(cloud):
    service, callbacks = cloud
    callbacks.disable.return_value = SteamCloudDisableResult(SteamCloudDisableStatus.DISABLED)
    callbacks.store_acknowledgement.side_effect = OSError("settings locked")
    assert service.disable_for_patch(STEAM_ROOT)
    assert service.acknowledged
    callbacks.warn.assert_not_called()


def test_failed_patch_resets_acknowledgement_and_warns_even_if_persistence_fails(cloud):
    service, callbacks = cloud
    service.acknowledged = True
    callbacks.disable.side_effect = RuntimeError("Steam API")
    callbacks.store_acknowledgement.side_effect = OSError("settings locked")
    assert not service.disable_for_patch(STEAM_ROOT)
    assert not service.acknowledged
    callbacks.store_acknowledgement.assert_called_once_with(False)
    callbacks.warn.assert_called_once()


def test_cli_without_prompt_preserves_warning_and_allows_connection(cloud):
    service, callbacks = cloud
    assert asyncio.run(service.confirm(STEAM_ROOT, None))
    assert not service.acknowledged
    callbacks.warn.assert_called_once()
    callbacks.store_acknowledgement.assert_not_called()


@pytest.mark.parametrize(
    "settings,expected",
    [
        (None, False),
        ([], False),
        ({"steam_cloud_disabled_acknowledged": 1}, False),
        ({"steam_cloud_disabled_acknowledged": True}, True),
    ],
)
def test_persisted_acknowledgement_requires_an_explicit_boolean(cloud, settings, expected):
    service, callbacks = cloud
    callbacks.load_settings.return_value = settings
    assert service.has_acknowledged() is expected
