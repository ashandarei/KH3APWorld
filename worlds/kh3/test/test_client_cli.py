"""Launch arguments preserve AP URL handling and existing shortcuts."""

from unittest.mock import AsyncMock, patch

import pytest

from .. import Client as client
from ..client_support.cli import build_parser, parse_launch_arguments


def test_default_launch_remains_disconnected_with_the_integrated_bridge():
    args = parse_launch_arguments(())
    assert args.connect is None
    assert args.name is None
    assert args.password is None
    assert args.save_dir is None
    assert not args.no_integrated_bridge
    assert args.bridge_poll_interval == 1.0


def test_url_credentials_and_explicit_runtime_paths_reach_the_client_entrypoint():
    main = AsyncMock()
    with patch.object(client, "main", main):
        client.launch(
            "archipelago://Sora%20KH3:secret@localhost:38281",
            "--bridge-root",
            "bridge",
            "--kh3-dir",
            "game",
            "--save-dir",
            "saves",
            "--ue4ss-log",
            "UE4SS.log",
            "--probe-config",
            "config.lua",
            "--kh3-mods-dir",
            "mods",
            "--bridge-poll-interval",
            "0.25",
            "--no-integrated-bridge",
            "--experimental-native-ui",
        )
    main.assert_awaited_once_with(
        "Sora%20KH3:secret@localhost:38281",
        "secret",
        "Sora KH3",
        "bridge",
        integrated_bridge=False,
        kh3_dir="game",
        save_dir="saves",
        ue4ss_log="UE4SS.log",
        probe_config="config.lua",
        kh3_mods_dir="mods",
        bridge_poll_interval=0.25,
    )


@pytest.mark.parametrize("args", [("https://invalid.example",), ("--bridge-poll-interval", "invalid")])
def test_invalid_launch_arguments_are_rejected_before_starting_the_client(args):
    with pytest.raises(SystemExit) as raised:
        parse_launch_arguments(args)
    assert raised.value.code == 2


def test_native_shortcut_flag_is_accepted_but_omitted_from_help():
    parser = build_parser()
    assert parser.parse_args(["--experimental-native-ui"]).experimental_native_ui
    assert "experimental-native-ui" not in parser.format_help()
