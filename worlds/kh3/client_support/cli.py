"""KH3 launch arguments and Archipelago URL parsing."""

from __future__ import annotations

import argparse

from CommonClient import get_base_parser, handle_url_arg


def build_parser() -> argparse.ArgumentParser:
    parser = get_base_parser(description="Kingdom Hearts III Archipelago bridge client.")
    parser.add_argument("--name", default=None, help="Slot Name to connect as.")
    for option, description in (
        ("--bridge-root", "Override the output directory for KH3 bridge JSON files."),
        ("--kh3-dir", "Override the KH3 game directory used for derived client paths."),
        ("--save-dir", "Override the KH3 save directory used by the integrated bridge."),
        ("--ue4ss-log", "Override the UE4SS.log path included in diagnostic exports."),
        ("--probe-config", "Override KH3APProbeCpp config.lua path to update with the current bridge directory."),
        ("--kh3-mods-dir", "Override the KH3 Content/Paks/~mods directory opened by the KH3 Config tab."),
    ):
        parser.add_argument(option, default=None, help=description)
    parser.add_argument(
        "--no-integrated-bridge",
        action="store_true",
        help="Do not run the integrated KH3 IPC bridge inside this client.",
    )
    parser.add_argument(
        "--bridge-poll-interval", type=float, default=1.0, help="Polling interval for the integrated KH3 bridge."
    )
    parser.add_argument("url", nargs="?", help="Archipelago connection url")
    # Existing shortcuts still pass this flag after the native UI became the default.
    parser.add_argument("--experimental-native-ui", action="store_true", help=argparse.SUPPRESS)
    return parser


def parse_launch_arguments(args: tuple[str, ...]) -> argparse.Namespace:
    parser = build_parser()
    return handle_url_arg(parser.parse_args(args), parser=parser)
