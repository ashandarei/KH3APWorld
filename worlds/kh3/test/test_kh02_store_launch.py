import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from ..kh02 import store_launch as launch
from ..kh02.installation import Installation, GAME
from ..transport.pipe_transport import PipeTransport


def arguments(app_id="kh28"):
    return ["-AUTH_LOGIN=unused", "-AUTH_PASSWORD=test-only-exchange-code", "-AUTH_TYPE=exchangecode",
            "-epicapp=" + app_id, "-EpicPortal", "-epicusername=A Name"]


@pytest.mark.parametrize("extra", [["-ExecCmds=anything"], ["-AUTH_PASSWORD=duplicate"],
                                  ["-epiclocale=en\nother"], ["-epiclocale=en\0other"]])
def test_unexpected_arguments_rejected(extra):
    with pytest.raises(ValueError):
        launch._validated_arguments({"egl_parameters": arguments() + extra}, "kh28")


def test_wrong_game_or_offline_response_rejected():
    with pytest.raises(ValueError):
        launch._validated_arguments({"egl_parameters": arguments("other")}, "kh28")
    with pytest.raises(ValueError):
        launch._validated_arguments({"egl_parameters": ["-EpicPortal", "-epicapp=kh28"]}, "kh28")


def test_heroic_collection_matches_exact_source_and_captures_output(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    config = tmp_path / ".var/app/com.heroicgameslauncher.hgl/config/heroic/legendaryConfig/legendary"
    config.mkdir(parents=True)
    root = tmp_path / "Games/KH_2.8"
    (config / "installed.json").write_text(json.dumps({"kh28": {"install_path": str(root)}}))
    monkeypatch.setattr(launch.shutil, "which", lambda name: "/usr/bin/flatpak")
    backend = Mock(return_value=SimpleNamespace(returncode=0, stdout=json.dumps({"egl_parameters": arguments()})))
    monkeypatch.setattr(launch.subprocess, "run", backend)
    source = Installation(root / GAME, "egs")
    assert launch.authenticated_arguments(source) == arguments()
    command = backend.call_args.args[0]
    assert command[-5:] == ["launch", "kh28", "--json", "--override-exe",
                            source.executable.relative_to(root).as_posix()]
    assert "--offline" not in command
    assert backend.call_args.kwargs["capture_output"]
    assert backend.call_args.kwargs["timeout"] == 40
    with pytest.raises(RuntimeError, match="not registered"):
        launch.authenticated_arguments(Installation(tmp_path / "other" / GAME, "egs"))
    assert backend.call_count == 1
    backend.side_effect = ValueError("do not disclose exchange code")
    with pytest.raises(RuntimeError) as error:
        launch.authenticated_arguments(source)
    assert "do not disclose" not in str(error.value)


def test_sensitive_response_never_exported_or_replayed(tmp_path):
    transport = PipeTransport(tmp_path, transport="local_tcp")
    with pytest.raises(ValueError, match="live KH3 connection"):
        transport.write(launch.RESPONSE, {"arguments": arguments()})
    transport.connected = True
    transport.write(launch.RESPONSE, {"arguments": arguments()})
    snapshot = json.dumps(transport.diagnostic_snapshot())
    assert "test-only-exchange-code" not in snapshot
    assert launch.RESPONSE not in snapshot
    transport.discard_recovery_commands()
    assert transport.read(launch.RESPONSE) is None
    assert launch.RESPONSE not in transport.pending
    assert not list(tmp_path.rglob("*.json"))


def test_request_deduplication_and_cancel_on_connection_change(tmp_path, monkeypatch):
    async def scenario():
        ctx = SimpleNamespace(bridge_dir=tmp_path)
        transport = PipeTransport(tmp_path, transport="local_tcp")
        transport.connected = True
        transport.connection_generation = 1
        request = {"request_id": "320:1234:1", "source_platform": "egs"}
        transport.values[launch.REQUEST] = json.dumps(request).encode()
        monkeypatch.setattr(launch, "transport_for", lambda path: transport)
        prepare = Mock(return_value=arguments())
        monkeypatch.setattr(launch, "_prepare_arguments", prepare)
        service = launch.StoreLaunchService(ctx)
        await service.observe()
        first = service.task
        await service.observe()
        assert service.task is first
        await first
        assert transport.read(launch.RESPONSE)["status"] == "ready"
        assert prepare.call_count == 1
        transport.connected = False
        await service.observe()
        assert service.task is None
        service.close()
    asyncio.run(scenario())


def test_stale_authentication_result_not_published(tmp_path, monkeypatch):
    async def scenario():
        ctx = SimpleNamespace(bridge_dir=tmp_path)
        transport = PipeTransport(tmp_path, transport="local_tcp")
        transport.connected = True
        transport.connection_generation = 2
        request = {"request_id": "320:1234:1"}
        transport.values[launch.REQUEST] = json.dumps(request).encode()
        monkeypatch.setattr(launch, "_prepare_arguments", lambda *args: arguments())
        service = launch.StoreLaunchService(ctx)
        binding = (transport, 1, request["request_id"], service.selection_revision)
        service.binding = binding
        await service._respond(tmp_path, transport, request, binding)
        assert transport.read(launch.RESPONSE) is None
    asyncio.run(scenario())
