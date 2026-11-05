"""Hosted and inline seeds retain the connected room and save association."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from ..client_support.connected_seed import ConnectedSeedRequest, ConnectedSeedService
from ..client_support.platform_seed_download import KH3PlatformSeedDownloadError
from ..client_support.run_actions import ActiveRunSaves
from ..client_support.run_manager import KH3RunManagerError
from ..client_support.seed_import_service import KH3SeedImportError


@pytest.fixture
def selection():
    importer, downloader = Mock(), Mock()
    downloader.has_platform_room.return_value = True
    downloader.download_player_artifact.return_value = SimpleNamespace(source_path=Path("player.apkh3"))
    request = ConnectedSeedRequest({"kh3_seed": {"format": "kh3_server_seed"}}, "host:38281", 2, "Sora", "seed")
    saves = Mock(return_value=ActiveRunSaves(Path("selected-saves"), "save-seed", "save-run"))
    fallback, progress = Mock(return_value="https://room.example/room/id"), Mock()
    service = ConnectedSeedService(lambda: importer, lambda: downloader)
    return service, importer, downloader, request, saves, fallback, progress


def prepare(selection):
    service, _, _, request, saves, fallback, progress = selection
    return service.prepare(request, resolve_saves=saves, fallback_room=fallback, progress=progress)


def test_hosted_artifact_is_preferred_and_receives_the_active_save_identity(selection):
    _, importer, downloader, request, _, fallback, progress = selection
    assert prepare(selection) is importer.prepare_player_artifact.return_value
    downloader.download_player_artifact.assert_called_once_with(
        request.slot_data, slot_name="Sora", fallback_room_url=fallback.return_value, progress=progress
    )
    importer.prepare_player_artifact.assert_called_once_with(
        Path("player.apkh3"),
        server_address="host:38281",
        expected_slot=2,
        expected_slot_name="Sora",
        expected_seed_name="seed",
        save_dir_override=Path("selected-saves"),
        active_save_seed_id="save-seed",
        active_save_run_id="save-run",
        progress=progress,
    )
    importer.prepare_server_slot_data.assert_not_called()


@pytest.mark.parametrize("advertised", [True, False])
def test_inline_seed_is_used_when_no_artifact_is_available(selection, advertised):
    _, importer, downloader, _, _, fallback, _ = selection
    downloader.has_platform_room.return_value = advertised
    downloader.download_player_artifact.return_value = None
    assert prepare(selection) is importer.prepare_server_slot_data.return_value
    importer.prepare_player_artifact.assert_not_called()
    if not advertised:
        downloader.download_player_artifact.assert_not_called()
        fallback.assert_not_called()


@pytest.mark.parametrize(
    "error", [KH3PlatformSeedDownloadError("download"), KH3SeedImportError("artifact"), KH3RunManagerError("run")]
)
def test_expected_hosted_failures_fall_back_to_valid_inline_data(selection, error):
    _, importer, downloader, request, _, _, progress = selection
    downloader.download_player_artifact.side_effect = error
    assert prepare(selection) is importer.prepare_server_slot_data.return_value
    assert importer.prepare_server_slot_data.call_args.args == (request.slot_data,)
    assert "Hosted player-file download was unavailable" in progress.call_args.args[0]


@pytest.mark.parametrize("inline", [None, {}, {"format": "unknown"}, "invalid"])
def test_hosted_failures_without_valid_inline_data_preserve_the_cause(selection, inline):
    _, importer, downloader, request, _, _, _ = selection
    error = KH3PlatformSeedDownloadError("download")
    downloader.download_player_artifact.side_effect = error
    request.slot_data["kh3_seed"] = inline
    with pytest.raises(KH3SeedImportError, match="download") as raised:
        prepare(selection)
    assert raised.value.__cause__ is error
    importer.prepare_server_slot_data.assert_not_called()


def test_unexpected_hosted_errors_are_not_hidden_by_inline_generation(selection):
    _, importer, downloader, _, _, _, _ = selection
    downloader.download_player_artifact.side_effect = RuntimeError("unexpected")
    with pytest.raises(RuntimeError, match="unexpected"):
        prepare(selection)
    importer.prepare_server_slot_data.assert_not_called()


def test_save_profile_resolution_failure_does_not_fabricate_a_save_owner(selection):
    _, importer, _, _, saves, _, _ = selection
    saves.side_effect = OSError("unavailable")
    prepare(selection)
    options = importer.prepare_player_artifact.call_args.kwargs
    assert not {"save_dir_override", "active_save_seed_id", "active_save_run_id"} & options.keys()
