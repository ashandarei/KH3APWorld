"""Mixed-store launch must preserve authentication, paths and sandbox scope."""
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from ..platform_support import mixed_store_launch as mixed
from ..platform_support import game_launch
from ..platform_support import process_status
from ..kh02 import installation, store_launch


@pytest.fixture
def installed(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(mixed.shutil, "which", lambda name: "/usr/bin/flatpak")
    steam = tmp_path / ".var/app" / mixed.STEAM_APP / ".local/share/Steam"
    config = tmp_path / ".var/app" / mixed.HEROIC_APP / "config/heroic"
    collection = tmp_path / "Games/Epic KH3"
    executable = collection / "KINGDOM HEARTS III/Binaries/Win64/KINGDOM HEARTS III.exe"
    proton = config / "tools/proton/Test Proton/proton"
    prefix = tmp_path / "Games/Prefixes/KH3"
    runtime = steam / "steamapps/common/SteamLinuxRuntime_4/_v2-entry-point"
    for path in [steam / "steam.sh", executable, proton, runtime]:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    (prefix / "pfx/drive_c").mkdir(parents=True)
    (proton.parent / "toolmanifest.vdf").write_text('"require_tool_appid" "4183110"')
    (steam / "steamapps/appmanifest_4183110.acf").write_text('"installdir" "SteamLinuxRuntime_4"')
    database = config / "legendaryConfig/legendary/installed.json"
    database.parent.mkdir(parents=True)
    database.write_text(json.dumps({"epic123": {"install_path": str(collection),
                                               "executable": executable.relative_to(collection).as_posix()}}))
    settings = config / "GamesConfig/epic123.json"
    settings.parent.mkdir(parents=True)
    settings.write_text(json.dumps({"epic123": {"winePrefix": str(prefix),
        "wineVersion": {"type": "proton", "bin": str(proton)}, "enviromentOptions": [], "wrapperOptions": []}}))
    source = SimpleNamespace(root=steam / "steamapps/common/KH2.8/KH0.2", platform="steam")
    return SimpleNamespace(executable=executable, source=source, settings=settings, steam=steam,
                           proton=proton, prefix=prefix, runtime=runtime, database=database)


def test_existing_heroic_prefix_and_declared_runtime_are_used(installed):
    env = mixed.prepare_environment(installed.executable, installed.source)
    assert env.prefix == installed.prefix.resolve()
    assert env.runtime == installed.runtime.resolve()
    assert env.proton == installed.proton.resolve()
    assert env.app_id == "epic123"


def test_launch_grants_only_selected_directories_without_global_override(installed):
    env = mixed.prepare_environment(installed.executable, installed.source)
    args = mixed.launch_command(env, ["-AUTH_PASSWORD=secret", "-EpicPortal"])
    assert args[0:2] == ["/usr/bin/flatpak", "run"]
    assert "override" not in args and "--filesystem=host" not in args and "--filesystem=home" not in args
    assert "--env=STEAM_COMPAT_DATA_PATH=" + str(env.prefix) in args
    assert "--filesystem=" + str(env.collection) in args
    assert args[-3:] == [str(installed.executable.resolve()), "-AUTH_PASSWORD=secret", "-EpicPortal"]
    assert args.index(mixed.STEAM_APP) < args.index(str(env.proton))
    assert not any("--env=HOME=" in arg for arg in args)


def test_runtime_is_not_guessed_when_the_required_version_is_missing(installed):
    (installed.proton.parent / "toolmanifest.vdf").write_text('"require_tool_appid" "99999"')
    with pytest.raises(RuntimeError, match="required by"):
        mixed.prepare_environment(installed.executable, installed.source)


def test_another_registered_epic_game_cannot_supply_this_launch(installed):
    with pytest.raises(RuntimeError, match="not registered"):
        mixed.prepare_environment(installed.executable.with_name("other.exe"), installed.source)


def test_custom_settings_are_not_silently_discarded(installed):
    data = json.loads(installed.settings.read_text())
    data["epic123"]["wrapperOptions"] = [{"executable": "custom-wrapper"}]
    installed.settings.write_text(json.dumps(data))
    with pytest.raises(RuntimeError, match="Custom Heroic"):
        mixed.prepare_environment(installed.executable, installed.source)


def test_failed_mixed_launch_does_not_fall_back_to_heroic_or_direct_exe(monkeypatch):
    monkeypatch.setattr(mixed, "launch_if_needed", Mock(side_effect=RuntimeError("Steam unavailable")))
    opener = Mock()
    process = Mock()
    monkeypatch.setattr(game_launch, "_open_launch_uri", opener)
    monkeypatch.setattr(game_launch.subprocess, "Popen", process)
    assert not game_launch.launch_epic_game(Path("game.exe"), "heroic://launch?appName=example")
    opener.assert_not_called()
    process.assert_not_called()


def test_ordinary_epic_launch_keeps_the_heroic_route(monkeypatch):
    monkeypatch.setattr(mixed, "launch_if_needed", Mock(return_value=False))
    opener = Mock(return_value=True)
    monkeypatch.setattr(game_launch, "_open_launch_uri", opener)
    uri = "heroic://launch?appName=example"
    assert game_launch.launch_epic_game(Path("game.exe"), uri)
    opener.assert_called_once_with(uri, description="Epic Games Launcher")


@pytest.fixture
def authentication(installed, monkeypatch):
    monkeypatch.setattr(installation, "selected_install", lambda: installed.source)
    monkeypatch.setattr(installation, "inspect_install", Mock())
    monkeypatch.setattr(installation, "sync_runtime", Mock())
    monkeypatch.setattr(installation, "selection_revision", 7)
    monkeypatch.setattr(process_status, "get_process_status", lambda _: {"running": False})
    monkeypatch.setattr(store_launch, "_legendary_command", lambda *args: ["legendary"])
    arguments = ["-AUTH_LOGIN=unused", "-AUTH_PASSWORD=secret-code", "-AUTH_TYPE=exchangecode",
                 "-epicapp=epic123", "-EpicPortal"]
    backend = Mock(return_value=SimpleNamespace(returncode=0, stdout=json.dumps({
        "egl_parameters": arguments, "game_parameters": []})))
    monkeypatch.setattr(mixed.subprocess, "run", backend)
    launch = Mock()
    monkeypatch.setattr(mixed.subprocess, "Popen", launch)
    return backend, launch


def test_authentication_is_transient_and_passed_to_the_selected_game(installed, authentication, caplog):
    backend, launch = authentication
    process = mixed._start_authenticated(installed.executable, installed.source, 7)
    assert process is launch.return_value
    assert backend.call_args.args[0][-3:] == ["launch", "epic123", "--json"]
    args = launch.call_args.args[0]
    assert "-AUTH_PASSWORD=secret-code" in args and "-epicapp=epic123" in args
    assert "--env=SteamAppId=2552440" in args
    assert launch.call_args.kwargs["stdout"] == mixed.subprocess.DEVNULL
    assert launch.call_args.kwargs["stderr"] == mixed.subprocess.DEVNULL
    assert "secret-code" not in caplog.text


def test_backend_errors_cannot_expose_exchange_codes(installed, authentication):
    backend, launch = authentication
    backend.side_effect = RuntimeError("-AUTH_PASSWORD=secret-code")
    with pytest.raises(RuntimeError, match="authentication failed") as result:
        mixed._start_authenticated(installed.executable, installed.source, 7)
    assert "secret-code" not in str(result.value)
    launch.assert_not_called()


def test_changed_source_cancels_pending_authentication(installed, authentication, monkeypatch):
    backend, launch = authentication
    response = backend.return_value
    def changed(*args, **kwargs):
        monkeypatch.setattr(installation, "selection_revision", 8)
        return response
    backend.side_effect = changed
    with pytest.raises(RuntimeError, match="source changed"):
        mixed._start_authenticated(installed.executable, installed.source, 7)
    launch.assert_not_called()


def test_running_game_prevents_prefix_reinitialization(installed, authentication, monkeypatch):
    backend, launch = authentication
    monkeypatch.setattr(process_status, "get_process_status", lambda _: {"running": True})
    with pytest.raises(RuntimeError, match="Close the running"):
        mixed._start_authenticated(installed.executable, installed.source, 7)
    backend.assert_not_called()
    launch.assert_not_called()
    installation.sync_runtime.assert_not_called()


def test_gui_launch_is_dispatched_once_without_waiting_for_authentication(installed, authentication, monkeypatch):
    backend, launch = authentication
    monkeypatch.setattr(mixed, "os", SimpleNamespace(name="posix"))
    monkeypatch.setattr(mixed, "_launching", set())
    thread = Mock()
    monkeypatch.setattr(mixed.threading, "Thread", thread)
    uri = "heroic://launch?appName=epic123&runner=legendary"
    assert mixed.launch_if_needed(installed.executable, uri)
    assert mixed.launch_if_needed(installed.executable, uri)
    thread.assert_called_once()
    thread.return_value.start.assert_called_once()
    backend.assert_not_called()
    launch.assert_not_called()
