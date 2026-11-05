"""Heroic launch discovery must target the selected game, including Flatpak."""
import json
from pathlib import Path

import pytest
from unittest.mock import Mock

from ..platform_support.game_discovery import _heroic_launch_uri_for_executable, normalize_kh3_root_dir
from ..platform_support import game_launch


@pytest.mark.parametrize("flatpak", [False, True])
def test_matching_installed_executable(tmp_path, monkeypatch, flatpak):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    config = tmp_path / (".var/app/com.heroicgameslauncher.hgl/config" if flatpak else ".config")
    database = config / "heroic/legendaryConfig/legendary/installed.json"
    database.parent.mkdir(parents=True)
    root = tmp_path / "Games/KH3"
    executable = "Binaries/Win64/KINGDOM HEARTS III.exe"
    database.write_text(json.dumps({"abc123": {"install_path": str(root), "executable": executable}}))
    assert _heroic_launch_uri_for_executable(root / executable) == "heroic://launch?appName=abc123&runner=legendary"
    assert _heroic_launch_uri_for_executable(tmp_path / "another/KINGDOM HEARTS III.exe") is None
    database.write_text(json.dumps({"bad&arg=other": {"install_path": str(root), "executable": executable}}))
    assert _heroic_launch_uri_for_executable(root / executable) is None
    database.write_text("broken json")
    assert _heroic_launch_uri_for_executable(root / executable) is None


@pytest.mark.parametrize("scheme,app_id", [("heroic", "com.heroicgameslauncher.hgl"),
                                         ("steam", "com.valvesoftware.Steam")])
def test_flatpak_launch_does_not_require_registered_uri_handler(monkeypatch, scheme, app_id):
    monkeypatch.delattr(game_launch.os, "startfile", raising=False)
    monkeypatch.setattr(game_launch.shutil, "which", lambda name: "/usr/bin/flatpak" if name == "flatpak" else None)
    check = Mock(return_value=Mock(returncode=0))
    launch = Mock()
    monkeypatch.setattr(game_launch.subprocess, "run", check)
    monkeypatch.setattr(game_launch.subprocess, "Popen", launch)
    uri = f"{scheme}://launch?appName=abc123&runner=legendary"
    assert game_launch._open_launch_uri(uri, description="test")
    assert check.call_args.args[0] == ["/usr/bin/flatpak", "info", app_id]
    assert launch.call_args.args[0][-2:] == [app_id, uri]


@pytest.mark.parametrize("folder", ["KH_3", "KINGDOM HEARTS III"])
def test_store_outer_directory_resolves_nested_runtime(tmp_path, folder):
    outer = tmp_path / folder
    nested = outer / "KINGDOM HEARTS III"
    executable = nested / "Binaries/Win64/KINGDOM HEARTS III.exe"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"test executable")
    assert normalize_kh3_root_dir(outer) == nested.resolve()
