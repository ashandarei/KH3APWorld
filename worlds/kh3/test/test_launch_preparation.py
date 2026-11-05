"""Required assets gate launch; optional music setup failures do not."""

from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from ..client_support import launch_preparation as preparation


@pytest.mark.parametrize("failure", [None, "garden", "music", "video"])
def test_launch_assets_preserve_order_and_only_required_failures_block_launch(tmp_path, caplog, failure):
    from ..Client import KH3Context

    ctx = KH3Context.__new__(KH3Context)
    ctx.kh3_dir = tmp_path
    events = []
    ctx._ensure_modloader_pak_priority = lambda: events.append("loader") or True
    ctx._reconcile_olympus_coliseum_reward_companion = lambda: events.append("companion")
    ctx._get_client_command_service = Mock()
    ctx._apply_client_facade_plan = lambda plan: events.append("launch")

    def garden(root):
        assert root == tmp_path
        events.append("garden")
        if failure == "garden":
            raise RuntimeError("Garden needs updating")

    def music(root):
        assert root == tmp_path
        events.append("music")
        if failure == "music":
            raise PermissionError(13, "Permission denied", "custom_music_directory.txt.tmp")

    def video(root, *, enabled):
        assert root == tmp_path and enabled
        events.append("video")
        if failure == "video":
            raise OSError("video file")

    with (
        patch("worlds.kh3.client_support.static_chest_tables.validate_static_chest_tables", side_effect=garden),
        patch("worlds.kh3.client_support.custom_music.sync_runtime", side_effect=music),
        patch("worlds.kh3.client_support.fast_new_game.read_settings", return_value={"enabled": True}),
        patch("worlds.kh3.client_support.startup_video.install_short_copyright_video", side_effect=video),
    ):
        ctx._launch_kh3_game_now()
    expected = ["loader", "garden"]
    if failure == "garden":
        assert events == expected
        return
    expected.append("music")
    expected.append("video")
    if failure in {None, "music"}:
        expected.extend(["companion", "launch"])
    if failure == "music":
        assert "continuing KH3 launch" in caplog.text
        assert "Permission denied" in caplog.text
    assert events == expected


def test_launch_file_preparation_does_not_hide_programming_errors(tmp_path):
    with patch("worlds.kh3.client_support.static_chest_tables.validate_static_chest_tables"), \
            patch("worlds.kh3.client_support.custom_music.sync_runtime", side_effect=ValueError("bad config")):
        with pytest.raises(ValueError, match="bad config"):
            preparation.prepare_launch_files(tmp_path)


def test_missing_mod_directory_does_not_probe_process_or_reconcile():
    running = Mock(side_effect=AssertionError("process lookup"))
    with patch.object(preparation, "reconcile_olympus_coliseum_compat") as reconcile:
        assert preparation.reconcile_reward_companion(None, {}, game_running=running) is None
    reconcile.assert_not_called()


@pytest.mark.parametrize(
    "status,changed,log_missing,level",
    [
        ("missing", False, True, "WARNING"),
        ("missing", False, False, None),
        ("outdated", False, True, "WARNING"),
        ("restart_required", False, False, "WARNING"),
        ("ready", True, False, "INFO"),
        ("ready", False, True, None),
    ],
)
def test_companion_messages_retain_the_existing_warning_policy(tmp_path, caplog, status, changed, log_missing, level):
    result = SimpleNamespace(status=status, changed=changed, message="companion status")
    with (
        caplog.at_level("INFO", logger="Client"),
        patch.object(preparation, "reconcile_olympus_coliseum_compat", return_value=result),
    ):
        assert (
            preparation.reconcile_reward_companion(tmp_path, {}, game_running=lambda: False, log_missing=log_missing)
            is result
        )
    records = [record for record in caplog.records if record.message == "companion status"]
    assert [record.levelname for record in records] == ([] if level is None else [level])


def test_companion_builder_announces_work_and_passes_reward_modes(tmp_path):
    source, output = tmp_path / "source.pak", tmp_path / "output.pak"
    status = Mock()
    result = SimpleNamespace(ready=True, changed=False)

    def ensure(mods_dir, slot_data, *, game_running, build_companion):
        assert mods_dir == tmp_path and slot_data == {"seed": "test"} and not game_running
        return result if build_companion(source, output, world_rewards=True) == output else None

    with (
        patch.object(preparation, "ensure_olympus_coliseum_compat", side_effect=ensure),
        patch("worlds.kh3.PakGenerator.build_olympus_coliseum_compat_pak", return_value=output) as build,
    ):
        assert (
            preparation.ensure_reward_companion(
                tmp_path, {"seed": "test"}, game_running=lambda: False, set_status=status
            )
            is result
        )
    status.assert_called_once_with("Building the Olympus Coliseum AP reward companion...")
    build.assert_called_once_with(source, output, world_rewards=True)
